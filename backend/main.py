import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi.responses import Response, FileResponse
import os as _os

from backend.config import settings
from backend.db import init_db, engine, Base

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

# ── Lifespan (startup / shutdown) ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Lifodial starting up — environment: %s", settings.environment)
    
    # Initialize DB — runs safe schema migrations automatically
    # Also registers new models for auto-create
    from backend.models import bulk_call  # noqa: ensure BulkCallCampaign is loaded
    await init_db()
    print("[OK] Session store ready (in-memory)")

    # Sync .env API keys into the database so they show in AI Platform
    try:
        from backend.routers.platform import sync_keys_from_env
        from backend.db import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            synced = await sync_keys_from_env(db)
            if synced:
                print(f"[OK] Synced {synced} API key(s) from .env into AI Platform")
    except Exception as e:
        logger.warning("Env key sync failed (non-fatal): %s", e)

    # ── API Warmup — eliminate cold-start latency ───────────────────────────
    # Run in background (non-blocking) so startup doesn't stall
    import asyncio
    asyncio.ensure_future(_warmup())

    yield
    logger.info("Lifodial shut down cleanly")


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

    # 2. Sarvam API (cheap OPTIONS call or real transcribe with silent audio)
    sarvam_key = getattr(_s, "sarvam_api_key", None)
    if sarvam_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    "https://api.sarvam.ai/",
                    headers={"api-subscription-key": sarvam_key},
                )
            logger.info("[WARMUP] Sarvam API reachable: HTTP %s", r.status_code)
        except Exception as e:
            logger.warning("[WARMUP] Sarvam API warmup failed (non-fatal): %s", e)

    # 3. Gemini API
    gemini_key = getattr(_s, "gemini_api_key", None)
    if gemini_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
                )
            logger.info("[WARMUP] Gemini API reachable: HTTP %s", r.status_code)
        except Exception as e:
            logger.warning("[WARMUP] Gemini API warmup failed (non-fatal): %s", e)


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Lifodial API",
    description="AI Voice Receptionist SaaS for clinics — India & Middle East (Lifodial)",
    version="1.0.2",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
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
@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Health check — returns database connection status."""
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
        "version": "1.0.2",
        "environment": settings.environment,
    }

@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {"service": "Lifodial API", "docs": "/docs"}

@app.post("/admin/reset-db", tags=["superadmin"])
async def reset_db():
    """
    ONE TIME USE: Drops and recreates all tables.
    Delete this endpoint after use.
    """
    # Import all models to ensure Base.metadata is fully populated
    from backend.models import tenant, doctor, appointment, call_log, agent_config, onboarding_request, api_key_config, knowledge_base
    from backend.models import phone_number, call_record, embed_analytics, bulk_call
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return {"status": "ok", "message": "All tables recreated"}

@app.post("/admin/seed", tags=["superadmin"])
async def seed_db():
    """
    ONE TIME USE: Seeds the database with demo data.
    """
    from backend.scripts.seed_demo import seed
    await seed()
    return {"status": "ok", "message": "Database seeded successfully"}

# ── Routers ───────────────────────────────────────────────────────────────────
from backend.routers import admin, tenants, doctors, voice, appointments, ws, voice_upload, agents, agent_test, platform, knowledge_base, voices, web_calls, phone_numbers, embed, models, simulation, latency, providers, bulk_calls

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


# ── Serve widget.js publicly ────────────────────────────────────────────────────
@app.get("/widget.js", tags=["embed"])
async def serve_widget():
    """Public widget script served with CORS + cache headers."""
    widget_paths = [
        _os.path.join("backend", "static", "widget.js"),
        _os.path.join("static", "widget.js"),
        _os.path.join("frontend", "public", "widget.js"),
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
