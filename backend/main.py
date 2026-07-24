import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi.responses import Response, FileResponse
import os as _os

from backend.config import settings
from backend.db import init_db, engine, Base
from backend.auth import require_superadmin, get_current_user

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Filter to suppress noisy requests from other apps (LeadScout etc.)
class _IgnoreNoiseFilter(logging.Filter):
    """Drop log records from unrelated apps hitting this server."""
    _blocked = ("/api/leads", "/api/dashboard", "/api/scrape", "/api/countries",
                "/api/directories", "/api/categories", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
                "connection rejected", "connection closed")
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(b in msg for b in self._blocked)

# Silence noisy 3rd-party loggers
for _noisy in ("httpx", "httpcore", "watchfiles", "hpack", "sqlalchemy.engine"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Apply filter to ALL uvicorn loggers and root logger
_nf = _IgnoreNoiseFilter()
for _uv in ("uvicorn.access", "uvicorn.error", "uvicorn", ""):
    logging.getLogger(_uv).addFilter(_nf)

# ── Error monitoring (Sentry) ──────────────────────────────────────────────────
# Initialized only when SENTRY_DSN is set (audit O1: previously declared in
# render.yaml but never installed/initialized). Guarded import so the backend
# still boots if the SDK isn't present. PII is NOT sent — this app handles
# patient data, so send_default_pii stays False.
if settings.sentry_dsn:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        logger.info("Sentry error monitoring initialized (env=%s)", settings.environment)
    except Exception as _sentry_err:  # pragma: no cover - defensive
        logger.warning("Sentry init failed (non-fatal): %s", _sentry_err)

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Lifodial starting up — environment: %s", settings.environment)
    
    # Initialize DB — runs safe schema migrations automatically
    # Also registers new models for auto-create
    from backend.models import bulk_call  # noqa: ensure BulkCallCampaign is loaded
    await init_db()

    # Resolve the session-store backend once at startup (connects to Redis if
    # REDIS_URL is a real server, else stays in-memory) so logs + the System
    # Health card report the truth from the first request.
    from backend import redis_client as _rc
    await _rc.ping()
    print(f"[OK] Session store ready ({_rc.BACKEND})")

    # Schedule background startup migrations & setup without delaying HTTP readiness
    import asyncio
    asyncio.create_task(_run_startup_migrations())
    asyncio.create_task(_warmup())


    # ── Storage bucket init (idempotent, enforces size + MIME limits) ────────
    # Non-blocking — if Supabase is unreachable the app still boots.
    try:
        from backend.services.storage import ensure_public_bucket, ensure_private_bucket
        asyncio.ensure_future(ensure_public_bucket())
        asyncio.ensure_future(ensure_private_bucket())
    except Exception as _storage_err:
        logger.warning("Storage bucket init failed (non-fatal): %s", _storage_err)

    yield
    logger.info("Lifodial shut down cleanly")


async def _run_startup_migrations() -> None:
    """Run DB syncs and agent model auto-healing migrations non-blockingly."""
    try:
        from backend.routers.platform import sync_keys_from_env
        from backend.db import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            synced = await sync_keys_from_env(db)
            if synced:
                logger.info("[STARTUP] Synced %d API key(s) from .env into AI Platform", synced)

            # 1. Migrate deprecated Gemini model references
            await db.execute(
                text("UPDATE agent_configs SET llm_model = 'gemini-2.5-flash' WHERE llm_model = 'gemini-2.0-flash'")
            )

            # 2. Fix agent names: rename voice-like names to professional clinic names
            AGENT_NAME_FIXES = {
                "agent-001": ("Priya",  "Apollo Receptionist"),
                "agent-002": ("Kavya",  "Aster Receptionist"),
                "agent-003": ("Riya",   "Max Receptionist"),
                "agent-004": ("Shreya", "Manipal Receptionist"),
                "agent-005": ("Layla",  "Al Zahra Receptionist"),
            }
            for agent_id, (old_name, new_name) in AGENT_NAME_FIXES.items():
                await db.execute(
                    text("UPDATE agent_configs SET agent_name = :new_name WHERE id = :id AND agent_name = :old_name"),
                    {"new_name": new_name, "id": agent_id, "old_name": old_name},
                )

            # 3. Valid Sarvam languages & legacy voice remap
            VALID_SARVAM_LANGS = {
                "as-IN", "bn-IN", "brx-IN", "doi-IN", "en-IN", "gu-IN",
                "hi-IN", "kn-IN", "kok-IN", "ks-IN", "mai-IN", "ml-IN",
                "mni-IN", "mr-IN", "ne-IN", "od-IN", "pa-IN", "sa-IN",
                "sat-IN", "sd-IN", "ta-IN", "te-IN", "ur-IN",
            }
            VOICE_REMAP = {
                "meera": "shreya", "pavithra": "kavitha", "maitreyi": "priya",
                "arvind": "rahul", "amol": "aditya", "amartya": "rohan",
                "diya": "ritu", "neel": "amit", "misha": "simran", "vian": "shubh",
            }
            for old_voice, new_voice in VOICE_REMAP.items():
                await db.execute(
                    text("UPDATE agent_configs SET tts_voice = :new WHERE LOWER(tts_voice) = :old AND (tts_model = 'bulbul:v3' OR tts_model IS NULL OR tts_model = '')"),
                    {"new": new_voice, "old": old_voice},
                )

            # 4. Fix unsupported tts_language codes
            rows = await db.execute(text("SELECT DISTINCT tts_language FROM agent_configs WHERE tts_language IS NOT NULL"))
            for (lang,) in rows.fetchall():
                if lang and lang.strip() not in VALID_SARVAM_LANGS:
                    await db.execute(
                        text("UPDATE agent_configs SET tts_language = 'en-IN' WHERE tts_language = :old"),
                        {"old": lang},
                    )

            # 5. Ensure all Sarvam TTS agents use bulbul:v3
            await db.execute(
                text(
                    "UPDATE agent_configs SET tts_model = 'bulbul:v3' "
                    "WHERE (tts_provider = 'sarvam' OR tts_provider IS NULL) "
                    "AND tts_model != 'bulbul:v3' AND tts_model IS NOT NULL"
                )
            )
            await db.commit()
            logger.info("[STARTUP] Background migrations completed successfully.")
    except Exception as exc:
        logger.warning("Startup background migration error (non-fatal): %s", exc)


async def _warmup() -> None:

    """Pre-warm DB connection pool, Sarvam API, and Gemini API.
    Failures are non-fatal — logged and swallowed.
    """
    import httpx
    from backend.db import AsyncSessionLocal
    from backend.config import settings as _s

    # 1. DB pool
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        logger.info("[WARMUP] DB connection pool: OK")
    except Exception as e:
        logger.warning("[WARMUP] DB warmup failed: %s", e)

    # 2. Sarvam API — hit a REAL endpoint (the list-models route). The bare root
    #    "/" returns 404 (no route there), which used to log a misleading
    #    "[WARMUP] Sarvam API reachable: HTTP 404" even though the key was valid.
    #    /v1/models returns 200 for a valid key, so status<400 = genuinely healthy.
    sarvam_key = getattr(_s, "sarvam_api_key", None)
    if sarvam_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    "https://api.sarvam.ai/v1/models",
                    headers={"api-subscription-key": sarvam_key},
                )
            if r.status_code < 400:
                logger.info("[WARMUP] Sarvam API reachable: HTTP %s (OK)", r.status_code)
            else:
                logger.warning(
                    "[WARMUP] Sarvam API rejected the request: HTTP %s — key may be "
                    "invalid/expired. Body: %s", r.status_code, r.text[:120]
                )
        except Exception as e:
            logger.warning("[WARMUP] Sarvam API warmup failed (non-fatal): %s", e)

    # 3. Gemini API — distinguish "reachable+valid" (2xx) from a real key problem
    #    (401/403 = revoked/leaked/geo-blocked key, NOT a malformed check). A 403
    #    with "reported as leaked" means the key must be ROTATED in Google AI
    #    Studio; log it loudly rather than as a bland "reachable: HTTP 403".
    gemini_key = getattr(_s, "gemini_api_key", None)
    if gemini_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
                )
            if r.status_code < 400:
                logger.info("[WARMUP] Gemini API reachable: HTTP %s (OK)", r.status_code)
            elif r.status_code in (401, 403):
                logger.error(
                    "[WARMUP] Gemini API key REJECTED: HTTP %s — key is likely "
                    "revoked/leaked/geo-blocked and must be rotated. Body: %s",
                    r.status_code, r.text[:160]
                )
            else:
                logger.warning("[WARMUP] Gemini API returned HTTP %s: %s", r.status_code, r.text[:120])
        except Exception as e:
            logger.warning("[WARMUP] Gemini API warmup failed (non-fatal): %s", e)


# ── App ────────────────────────────────────────────────────────────────────────
_IS_PROD = settings.environment.lower() == "production"

app = FastAPI(
    title="Lifodial API",
    description="AI Voice Receptionist SaaS for clinics — India & Middle East (Lifodial)",
    version="1.0.4",
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────────
# NOTE: allow_credentials=True is INCOMPATIBLE with allow_origins=["*"] — browsers block it.
# We list explicit dev + prod origins instead.
_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    # Production — Vercel frontend
    "https://lifodial.vercel.app",
    # Production — Render static frontend (if deployed on Render)
    "https://lifodial-frontend.onrender.com",
    # ngrok dev tunnel (frontend exposed for external testing)
    "https://belted-annette-antonomastically.ngrok-free.dev",
]
# Also pull any extra origin from env (for production deployment)
_extra = getattr(settings, "cors_origin", None) or getattr(settings, "frontend_url", None)
if _extra:
    _CORS_ORIGINS.append(_extra)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Embed CORS — allow ANY origin for /embed/ endpoints (public widget) ──────
# The CORSMiddleware above only allows listed origins, but embed widgets load
# from arbitrary clinic websites. This middleware runs BEFORE CORSMiddleware
# (middleware stack is LIFO) and handles preflight + response headers for embed paths.
@app.middleware("http")
async def embed_cors_middleware(request: Request, call_next):
    """Inject permissive CORS for all /embed/ and /widget.js paths."""
    path = request.url.path
    if path.startswith("/embed/") or path == "/widget.js":
        if request.method == "OPTIONS":
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                    "Access-Control-Max-Age": "86400",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response
    return await call_next(request)

# ── Block noise from other projects (LeadScout etc.) ──────────────────────────
_FOREIGN_PATHS = ("/api/leads", "/api/dashboard", "/api/scrape", "/api/countries",
                  "/api/directories", "/api/categories")

@app.middleware("http")
async def block_foreign_requests(request: Request, call_next):
    """Return silent 404 for requests from other projects hitting this port."""
    if any(request.url.path.startswith(p) for p in _FOREIGN_PATHS):
        return Response(status_code=404)
    return await call_next(request)

# ── Core routes ────────────────────────────────────────────────────────────────
@app.api_route("/health", methods=["GET", "HEAD"], tags=["meta"])
async def health() -> dict:
    """Health check — returns database connection status.
    Accepts HEAD (uptime monitors / Render health checks) as well as GET so a
    HEAD probe does not log a 405."""
    from backend.db import AsyncSessionLocal, IS_SQLITE
    db_status = "unknown"
    db_type = "postgresql" if not IS_SQLITE else "sqlite"

    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "database": db_status,
        "database_type": db_type,
        "version": "1.0.4",
        "environment": settings.environment,
    }

@app.api_route("/", methods=["GET", "HEAD"], tags=["meta"])
async def root() -> dict[str, str]:
    # HEAD is handled by the same route so the frequent "HEAD / -> 405" from
    # Render's health monitor / external uptime checkers stops being logged as
    # an error. FastAPI strips the body for HEAD automatically.
    return {"service": "Lifodial API", "docs": "/docs"}


# ── Recent call logs (real data, tenant-scoped) ─────────────────────────────────
def _fmt_call_duration(secs) -> str:
    """Seconds → 'M:SS'. Returns '—' when unknown so the UI never fabricates one."""
    if not secs or secs < 0:
        return "—"
    m, s = divmod(int(secs), 60)
    return f"{m}:{s:02d}"


def _call_record_to_row(rec, clinic_name: str | None = None) -> dict:
    """Shape a CallRecord into the row the dashboard table renders. Every field
    comes from the real row — no fixtures, no fabricated placeholders.

    ``clinic_name`` is filled in for the superadmin network-wide view (which
    spans clinics); the per-tenant dashboard leaves it None. Extra keys
    (clinic_name/phone/date/language) are harmless to callers that ignore them.
    """
    outcome = (rec.outcome or "").lower()
    status_map = {
        "booked": "Booked", "transferred": "Transferred", "resolved": "Resolved",
        "cancelled": "CANCELLED", "unresolved": "Failed",
    }
    if outcome in status_map:
        display_status = status_map[outcome]
    else:
        display_status = {
            "completed": "Resolved", "failed": "Failed", "in_progress": "Pending",
            "transferred": "Transferred", "voicemail": "Pending",
        }.get((rec.status or "").lower(), "Pending")
    caller = rec.patient_number_masked or rec.patient_number or "—"
    when = rec.created_at.strftime("%d %b %Y, %H:%M") if rec.created_at else "—"
    transcript = rec.transcript if isinstance(rec.transcript, list) else []
    return {
        "id": rec.id,
        "clinic_name": clinic_name or "—",
        "caller_number": caller,
        "phone": caller,
        "intent": rec.intent_detected or "—",
        "duration": _fmt_call_duration(rec.duration_seconds),
        "created_at": when,
        "date": when,
        "language": rec.detected_language or "—",
        "status": display_status,
        "transcript": transcript,
    }


@app.get("/api/call_logs", tags=["calls"])
async def recent_call_logs(limit: int = 5, user=Depends(get_current_user)) -> dict:
    """Most-recent calls from the real call_records table.

    Tenant-scoped: a clinic token sees only its own tenant's calls; a superadmin
    token sees calls across all tenants. Returns {"items": []} when there are no
    calls yet — the UI renders a clean empty state, never fixture data.
    """
    from backend.db import AsyncSessionLocal
    from backend.models.call_record import CallRecord
    from sqlalchemy import select, desc

    limit = max(1, min(limit, 50))
    async with AsyncSessionLocal() as db:
        stmt = select(CallRecord).order_by(desc(CallRecord.created_at)).limit(limit)
        if not user.is_superadmin:
            # tenant_id is derived from the verified token, never the request.
            stmt = stmt.where(CallRecord.tenant_id == user.tenant_id)
        rows = (await db.execute(stmt)).scalars().all()

        # Resolve clinic names so the superadmin network view shows which clinic
        # each call belongs to (a single JOIN-equivalent batch lookup).
        name_by_tid: dict = {}
        tids = {r.tenant_id for r in rows if r.tenant_id}
        if tids:
            from backend.models.tenant import Tenant
            tn = (await db.execute(
                select(Tenant.id, Tenant.clinic_name).where(Tenant.id.in_(tids))
            )).all()
            name_by_tid = {tid: cn for tid, cn in tn}

    return {"items": [_call_record_to_row(r, name_by_tid.get(r.tenant_id)) for r in rows]}

@app.post("/admin/reset-db", tags=["superadmin"])
async def reset_db(_user=Depends(require_superadmin)):
    """
    DANGER: Drops and recreates all tables. Superadmin only, and permanently
    disabled in production to prevent catastrophic data loss.
    """
    if _IS_PROD:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": "Disabled in production"})

    # Import all models to ensure Base.metadata is fully populated
    from backend.models import tenant, doctor, appointment, call_log, agent_config, onboarding_request, api_key_config, knowledge_base
    from backend.models import phone_number, call_record, embed_analytics, bulk_call, clinic_credits

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return {"status": "ok", "message": "All tables recreated"}

@app.post("/admin/seed", tags=["superadmin"])
async def seed_db(_user=Depends(require_superadmin)):
    """
    Seeds the database with demo data. Superadmin only; disabled in production.
    """
    if _IS_PROD:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": "Disabled in production"})
    from backend.scripts.seed_demo import seed
    await seed()
    return {"status": "ok", "message": "Database seeded successfully"}


# ── Debug / Audit Endpoint (protected) ───────────────────────────────────────
@app.get("/debug/audit", tags=["debug"])
async def audit_database(_user=Depends(require_superadmin)):
    """Complete database audit — shows row counts only. Superadmin only."""
    from backend.db import AsyncSessionLocal
    from sqlalchemy import text

    results = {}

    async with AsyncSessionLocal() as db:
        tables = [
            "tenants", "agent_configs", "doctors",
            "clinic_credits", "credit_transactions",
            "appointments", "call_logs", "call_records",
        ]

        for table in tables:
            try:
                count = await db.scalar(
                    text(f"SELECT COUNT(*) FROM {table}")
                )
                results[table] = {"count": count}
            except Exception as e:
                results[table] = {"error": str(e)[:100]}

    return results


@app.post("/admin/sync-tenants-from-agents", tags=["superadmin"])
async def sync_tenants_from_agents(_user=Depends(require_superadmin)):
    """
    Finds agents that have tenant_ids with no matching tenant.
    Creates missing tenant records.
    """
    from backend.security import hash_password
    from backend.db import AsyncSessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.models.tenant import Tenant
    from sqlalchemy import select
    from datetime import datetime, timezone

    fixed = []

    async with AsyncSessionLocal() as db:
        # Get all agents
        agents = (await db.execute(select(AgentConfig))).scalars().all()

        for agent in agents:
            # Check if tenant exists
            tenant = (await db.execute(
                select(Tenant).where(Tenant.id == agent.tenant_id)
            )).scalar_one_or_none()

            if not tenant:
                # Create missing tenant
                name = agent.agent_name or "Clinic"
                new_tenant = Tenant(
                    id=str(agent.tenant_id),
                    clinic_name=f"{name} Clinic",
                    admin_email=f"admin@{name.lower().replace(' ', '')}.com",
                    admin_password=hash_password("changeme123"),
                    language=agent.tts_language or "hi-IN",
                    status="active",
                    is_active=True,
                    plan="Free",
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_tenant)
                fixed.append({
                    "tenant_id": str(agent.tenant_id),
                    "created": f"{name} Clinic",
                })

        if fixed:
            await db.commit()

    return {
        "fixed": len(fixed),
        "details": fixed,
        "message": f"Created {len(fixed)} missing tenant records",
    }

# ── Routers ───────────────────────────────────────────────────────────────────
from backend.routers import admin, tenants, doctors, voice, appointments, ws, voice_upload, agents, agent_test, platform, knowledge_base, voices, web_calls, phone_numbers, embed, models, simulation, latency, providers, bulk_calls, credits

app.include_router(admin.router,          prefix="/admin",    tags=["superadmin"])
app.include_router(voice.router,          prefix="/voice",    tags=["voice"])
app.include_router(voices.router,         prefix="/voices",   tags=["voice-library"])
app.include_router(tenants.router,        prefix="/tenants",  tags=["tenants"])
app.include_router(doctors.router,        prefix="",          tags=["doctors"])
app.include_router(appointments.router,   prefix="/tenants",  tags=["appointments"])
app.include_router(voice_upload.router,   prefix="/tenants",  tags=["voice"])
app.include_router(ws.router,             prefix="",          tags=["websocket"])
app.include_router(agents.router,         prefix="",          tags=["agents"])
app.include_router(agent_test.router,     prefix="",          tags=["agent-test"])
app.include_router(platform.router,       prefix="",          tags=["platform"])
app.include_router(knowledge_base.router, prefix="",          tags=["knowledge-base"])
app.include_router(web_calls.router,      prefix="",          tags=["web-calls"])
app.include_router(phone_numbers.router,  prefix="",          tags=["phone-numbers"])
app.include_router(embed.router,          prefix="",          tags=["embed"])
app.include_router(models.router,         prefix="",          tags=["models"])
app.include_router(simulation.router,     prefix="",          tags=["simulation"])
app.include_router(latency.router,        prefix="",          tags=["latency"])
app.include_router(providers.router,      prefix="",          tags=["providers"])
app.include_router(bulk_calls.router,     prefix="",          tags=["bulk-calls"])
app.include_router(credits.router,        prefix="",          tags=["credits"])


# ── Serve widget.js publicly ────────────────────────────────────────────────────
# backend/static/widget.js is the ONE canonical embed script. An older,
# unmaintained copy previously lived at frontend/public/widget.js — it was
# never reachable through this route (this list always checked backend/static
# first) and has been deleted so there's no risk of anyone editing the wrong
# file going forward.
@app.get("/widget.js", tags=["embed"])
async def serve_widget():
    """Public widget script served with CORS + cache headers."""
    widget_paths = [
        _os.path.join("backend", "static", "widget.js"),
        _os.path.join("static", "widget.js"),  # same file, relative to a different CWD
    ]
    for path in widget_paths:
        if _os.path.isfile(path):
            return FileResponse(
                path,
                media_type="application/javascript",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Access-Control-Allow-Origin": "*",
                },
            )
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("// widget.js not found", status_code=404, media_type="application/javascript")


# ── Public widget test page (HTTPS-served so mic permission works on any device) ──
@app.get("/test", tags=["embed"])
@app.get("/test/", tags=["embed"])
async def widget_test_page(agent: str = "agent-001"):
    """Public test page for the embed widget. Reachable from any device:
       https://lifodial.onrender.com/test?agent=agent-001
    Defaults to agent-001 (Apollo Clinic Hindi receptionist).
    """
    from fastapi.responses import HTMLResponse
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Lifodial Widget Test — {agent}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:linear-gradient(135deg,#f0faf6 0%,#e8f4fd 100%);min-height:100vh;color:#1a1a2e}}
  nav{{background:#fff;padding:18px 32px;box-shadow:0 2px 8px rgba(0,0,0,.05);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}}
  nav .brand{{font-weight:800;font-size:20px;color:#0f5fa8}}
  nav .links a{{margin-left:18px;color:#555;text-decoration:none;font-size:14px}}
  .hero{{padding:60px 32px;max-width:900px;margin:0 auto;text-align:center}}
  .hero h1{{font-size:38px;line-height:1.2;margin-bottom:14px}}
  .hero p{{font-size:17px;color:#555;margin-bottom:22px}}
  .badge{{display:inline-block;background:#3ECF8E22;color:#0a8a5d;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:600}}
  .panel{{max-width:560px;margin:24px auto;padding:18px 22px;background:#fff;border-radius:14px;box-shadow:0 4px 20px rgba(0,0,0,.06);font-size:14px}}
  .panel h3{{font-size:15px;margin-bottom:10px;color:#0f5fa8}}
  .panel ol{{margin-left:20px;color:#444;line-height:1.7}}
  .panel code{{background:#f4f4f8;padding:2px 6px;border-radius:4px;font-size:12px}}
  .switcher{{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap}}
  .switcher a{{padding:6px 12px;background:#0f5fa8;color:#fff;text-decoration:none;border-radius:6px;font-size:12px}}
  .switcher a.active{{background:#3ECF8E;color:#0a3d2a}}
</style>
</head><body>
  <nav>
    <div class="brand">🏥 Apollo Multispeciality</div>
    <div class="links"><a href="#">Services</a><a href="#">Doctors</a><a href="#">Book</a></div>
  </nav>
  <div class="hero">
    <h1>World-class healthcare,<br/>at your doorstep</h1>
    <p>Click the floating call button to speak with our AI receptionist.</p>
    <div class="badge">📍 Open 24/7 · Andheri West, Mumbai</div>
  </div>
  <div class="panel">
    <h3>Widget tester</h3>
    <p>Currently loaded agent: <code>{agent}</code></p>
    <div class="switcher">
      <a href="?agent=agent-001" class="{'active' if agent=='agent-001' else ''}">agent-001 (Apollo Hindi)</a>
      <a href="?agent=agent-002" class="{'active' if agent=='agent-002' else ''}">agent-002 (Aster Malayalam)</a>
      <a href="?agent=agent-004" class="{'active' if agent=='agent-004' else ''}">agent-004 (Max English)</a>
      <a href="?agent=agent-005" class="{'active' if agent=='agent-005' else ''}">agent-005 (Aster Mixed)</a>
    </div>
    <h3 style="margin-top:18px">📋 To embed on any clinic site</h3>
    <ol>
      <li>Site must be served over <strong>HTTPS</strong> (browsers block mic on plain HTTP).</li>
      <li>Paste this single line before <code>&lt;/body&gt;</code>:</li>
    </ol>
    <pre style="background:#1a1a1a;color:#3ECF8E;padding:12px;border-radius:6px;overflow:auto;font-size:11px;margin-top:8px">&lt;script src="https://lifodial.onrender.com/widget.js" data-agent-id="{agent}"&gt;&lt;/script&gt;</pre>
  </div>

  <!-- The actual embed -->
  <script
    src="/widget.js"
    data-agent-id="{agent}"
    data-api-url="https://lifodial.onrender.com"
    data-position="bottom-right"
    data-theme="dark"
    data-style="full"
  ></script>
</body></html>"""
    return HTMLResponse(html)


# ── Catch-all WebSocket handlers ───────────────────────────────────────────────
# Silently absorb unknown WebSocket connections (e.g. LeadScout on same port).
#
# Route ordering: agent_test.router (line 150) registers /ws/agent-call/{id} and
# /ws/agent/{id}/tts-stream BEFORE this catch-all (line 198). Starlette matches
# routes in insertion order, so the specific routes always win.
# This handler only handles truly unknown /ws/* paths from foreign processes.
from fastapi import WebSocket as _WS, WebSocketDisconnect as _WSD

@app.websocket("/ws/{path:path}")
async def catch_all_ws(websocket: _WS, path: str):
    """Absorb unknown WebSocket paths. Known API paths are handled by included routers."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive()
    except (_WSD, Exception):
        pass

# Also catch bare /ws without trailing path
@app.websocket("/ws")
async def catch_bare_ws(websocket: _WS):
    await websocket.accept()
    try:
        while True:
            await websocket.receive()
    except (_WSD, Exception):
        pass

# Catch ANY other WebSocket path (e.g. /<jwt-token> without /ws prefix)
@app.websocket("/{path:path}")
async def catch_any_ws(websocket: _WS, path: str):
    # Only accept if it looks like a foreign WebSocket (JWT, etc.)
    if path.startswith("eyJ") or len(path) > 100:
        await websocket.accept()
        try:
            while True:
                await websocket.receive()
        except (_WSD, Exception):
            pass
    else:
        await websocket.close(code=1008)
