"""
backend/routers/embed.py
Public-facing embed endpoints — called from clinic websites.
No auth required (public). CORS: allow all origins.
"""
import json
import logging
import time
import uuid
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from backend.db import async_session
from backend.models.agent_config import AgentConfig
from backend.models.embed_analytics import EmbedEvent

logger = logging.getLogger(__name__)
router = APIRouter()

# ── In-memory rate limits ──────────────────────────────────────────────────────
# {session_id: [timestamps]}
_session_messages: dict[str, list[float]] = defaultdict(list)
# {agent_id: [timestamps]}
_agent_sessions: dict[str, list[float]] = defaultdict(list)

MAX_MESSAGES_PER_MINUTE = 10
MAX_SESSIONS_PER_AGENT_PER_HOUR = 50


def _check_rate_limit(agent_id: str, session_id: str) -> None:
    now = time.time()

    # Per-session: max 10 messages per 60s
    msgs = _session_messages[session_id]
    _session_messages[session_id] = [t for t in msgs if now - t < 60]
    if len(_session_messages[session_id]) >= MAX_MESSAGES_PER_MINUTE:
        raise HTTPException(429, "Rate limit exceeded. Please wait before sending more messages.")
    _session_messages[session_id].append(now)

    # Per-agent: max 50 new sessions per hour
    sessions = _agent_sessions[agent_id]
    _agent_sessions[agent_id] = [t for t in sessions if now - t < 3600]
    # Only count if this session hasn't been seen in last hour
    known = session_id in {s for s in _session_messages}
    if not known and len(_agent_sessions[agent_id]) >= MAX_SESSIONS_PER_AGENT_PER_HOUR:
        raise HTTPException(429, "This agent has reached its hourly session limit.")
    if not known:
        _agent_sessions[agent_id].append(now)


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


# ── OPTIONS preflight ──────────────────────────────────────────────────────────
@router.options("/embed/{agent_id}/{rest:path}")
async def embed_preflight(agent_id: str, rest: str) -> JSONResponse:
    return JSONResponse({}, headers=_cors_headers())


# ── GET /embed/{agent_id}/config ───────────────────────────────────────────────
@router.get("/embed/{agent_id}/config")
async def embed_config(agent_id: str, request: Request) -> JSONResponse:
    """Public — returns safe widget config. No secrets exposed."""
    origin = request.headers.get("origin", "")

    async with async_session() as db:
        result = await db.execute(
            select(AgentConfig).where(AgentConfig.id == agent_id)
        )
        agent: AgentConfig | None = result.scalars().first()

    if not agent:
        raise HTTPException(404, "Agent not found")

    # Domain whitelist check
    allowed_domains = agent.embed_allowed_domains or []
    if isinstance(allowed_domains, str):
        try:
            allowed_domains = json.loads(allowed_domains)
        except Exception:
            allowed_domains = []

    if allowed_domains and origin:
        # strip protocol from origin for comparison
        clean_origin = origin.replace("https://", "").replace("http://", "").rstrip("/")
        if not any(clean_origin == d or clean_origin.endswith("." + d) for d in allowed_domains):
            return JSONResponse(
                {
                    "error": "This agent is not authorized for this domain. "
                             "Contact your Lifodial admin."
                },
                status_code=403,
                headers=_cors_headers(),
            )

    if not getattr(agent, "embed_enabled", True):
        raise HTTPException(403, "Embed is disabled for this agent.")

    payload = {
        "agent_id": agent.id,
        "agent_name": agent.agent_name,
        "clinic_name": getattr(agent, "clinic_name", "") or agent.agent_name,
        "greeting": agent.first_message or "Hello! How can I help you today?",
        "first_message": agent.first_message or "Hello! How can I help you today?",
        "first_message_mode": getattr(agent, "first_message_mode", "assistant-speaks-first") or "assistant-speaks-first",
        "language": agent.stt_language or "en-IN",
        "theme": {
            "primary_color": getattr(agent, "embed_primary_color", "#3ECF8E"),
            "position": getattr(agent, "embed_position", "bottom-right"),
            "button_text": getattr(agent, "embed_button_text", "Talk to Receptionist"),
            "button_icon": "phone",
        },
        "modes": ["chat", "voice"],
        "allowed_domains": allowed_domains,
        # An agent is callable if it's not explicitly disabled. CONFIGURED, ACTIVE,
        # and (None) all count as "ready". Only "INACTIVE" / "DISABLED" exclude it.
        "is_active": (agent.status or "").upper() not in ("INACTIVE", "DISABLED", "ARCHIVED"),
        # Public embed settings
        "embed_primary_color": getattr(agent, "embed_primary_color", "#3ECF8E"),
        "embed_position": getattr(agent, "embed_position", "bottom-right"),
        "embed_theme": getattr(agent, "embed_theme", "dark"),
        "embed_button_text": getattr(agent, "embed_button_text", "Talk to Receptionist"),
        "embed_show_branding": getattr(agent, "embed_show_branding", True),
    }

    return JSONResponse(payload, headers=_cors_headers())


# ── POST /embed/{agent_id}/chat ────────────────────────────────────────────────
class EmbedChatRequest(BaseModel):
    message: str
    session_id: str = ""
    visitor_language: str = "en-IN"
    history: list = []


@router.post("/embed/{agent_id}/chat")
async def embed_chat(agent_id: str, body: EmbedChatRequest, request: Request) -> JSONResponse:
    """Public — chat with the embedded agent. Rate limited."""
    session_id = body.session_id or f"visitor-{uuid.uuid4().hex[:12]}"
    _check_rate_limit(agent_id, session_id)

    async with async_session() as db:
        result = await db.execute(
            select(AgentConfig).where(AgentConfig.id == agent_id)
        )
        agent: AgentConfig | None = result.scalars().first()

        if not agent:
            raise HTTPException(404, "Agent not found")

        # Re-use existing LLM logic
        from backend.routers.agent_test import generate_llm_response
        response_text = await generate_llm_response(
            agent=agent,
            user_message=body.message,
            db=db,
            session_id=f"embed-{session_id}",
        )

        # Track event
        try:
            event = EmbedEvent(
                tenant_id=agent.tenant_id,
                agent_id=agent_id,
                event_type="chat_started",
                session_id=session_id,
                domain=request.headers.get("origin", "")[:200],
                language=body.visitor_language,
            )
            db.add(event)
            await db.commit()
        except Exception as e:
            logger.warning("Failed to save embed event: %s", e)

    return JSONResponse(
        {
            "response": response_text,
            "session_id": session_id,
            "intent": "general",
        },
        headers=_cors_headers(),
    )


# ── POST /embed/{agent_id}/track ───────────────────────────────────────────────
class EmbedTrackRequest(BaseModel):
    event_type: str
    session_id: str = ""
    domain: str = ""
    language: str = ""


@router.post("/embed/{agent_id}/track")
async def embed_track(agent_id: str, body: EmbedTrackRequest) -> JSONResponse:
    """Fire-and-forget analytics tracking."""
    async with async_session() as db:
        result = await db.execute(
            select(AgentConfig).where(AgentConfig.id == agent_id)
        )
        agent = result.scalars().first()
        if not agent:
            return JSONResponse({"ok": True}, headers=_cors_headers())

        try:
            event = EmbedEvent(
                tenant_id=agent.tenant_id,
                agent_id=agent_id,
                event_type=body.event_type[:30],
                session_id=body.session_id or "anonymous",
                domain=body.domain[:200],
                language=body.language[:10],
            )
            db.add(event)
            await db.commit()
        except Exception as e:
            logger.warning("Embed track error: %s", e)

    return JSONResponse({"ok": True}, headers=_cors_headers())


# ── GET /embed/{agent_id}/analytics ───────────────────────────────────────────
@router.get("/embed/{agent_id}/analytics")
async def embed_analytics(agent_id: str) -> JSONResponse:
    """Returns this month's analytics stats for the embed settings page."""
    from sqlalchemy import func, extract
    from datetime import datetime

    async with async_session() as db:
        now = datetime.utcnow()
        result = await db.execute(
            select(
                EmbedEvent.event_type,
                func.count(EmbedEvent.id).label("count"),
            )
            .where(
                EmbedEvent.agent_id == agent_id,
                extract("month", EmbedEvent.created_at) == now.month,
                extract("year", EmbedEvent.created_at) == now.year,
            )
            .group_by(EmbedEvent.event_type)
        )
        rows = result.all()

    counts = {r.event_type: r.count for r in rows}
    views = counts.get("widget_view", 0)
    opens = counts.get("widget_open", 0)
    chats = counts.get("chat_started", 0)
    bookings = counts.get("booking_completed", 0)

    return JSONResponse(
        {
            "views": views,
            "opens": opens,
            "conversations": chats,
            "bookings": bookings,
            "open_rate": round(opens / views * 100, 1) if views else 0,
            "chat_rate": round(chats / opens * 100, 1) if opens else 0,
            "booking_rate": round(bookings / chats * 100, 1) if chats else 0,
        },
        headers=_cors_headers(),
    )


# ── GET /embed/{agent_id}/preview ─────────────────────────────────────────────
@router.get("/embed/{agent_id}/preview", response_class=HTMLResponse)
async def embed_preview(agent_id: str) -> HTMLResponse:
    """Returns a minimal HTML page with the widget loaded — for iframe preview."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Widget Preview</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh;
    }}
    .notice {{
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.1);
      backdrop-filter: blur(12px);
      padding: 32px 40px;
      border-radius: 20px;
      text-align: center;
      max-width: 380px;
    }}
    .notice h3 {{ color: #fff; font-size: 20px; margin-bottom: 8px; }}
    .notice p {{ color: rgba(255,255,255,0.55); font-size: 14px; line-height: 1.6; }}
    .notice .accent {{ color: #3ECF8E; margin-top: 12px; font-weight: 600; }}
    .notice .arrow {{ font-size: 24px; margin-top: 16px; animation: bounce 1.5s infinite; display: block; }}
    @keyframes bounce {{
      0%,100% {{ transform: translateY(0); }}
      50% {{ transform: translateY(6px); }}
    }}
  </style>
</head>
<body>
  <div class="notice">
    <h3>Widget Preview</h3>
    <p>Your AI receptionist widget appears in the corner of this page.</p>
    <p class="accent">👇 Click the button to test it!</p>
    <span class="arrow">↘</span>
  </div>
  <script
    src="/widget.js"
    data-agent-id="{agent_id}"
    data-api-url=""
  ></script>
</body>
</html>"""
    return HTMLResponse(html)
