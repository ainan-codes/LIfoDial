"""
backend/config.py — Pydantic settings for Lifodial.
All secrets loaded from .env. Never access os.environ directly;
always import and use `settings` from this module.
"""

import os
import logging
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Values that must never be used as a real secret in production.
_WEAK_SECRETS = {
    "",
    "change_me",
    "changeme",
    "lifodial_dev_secret_change_in_production",
    "lifodial_prod_change_me_32chars_min_xxxxxxxx",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────
    environment: str = "development"
    secret_key: str = "change_me"

    # ── Error monitoring (Sentry) — optional; set SENTRY_DSN in prod env ─────
    sentry_dsn: str = ""

    # ── Superadmin (platform owner) login — set these in prod env ───────────
    superadmin_email: str = "admin@lifodial.com"
    superadmin_password: str = ""  # if empty in prod, superadmin login is disabled

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = ""
    postgres_user: str = "lifodial"
    postgres_password: str = "change_this_strong_password"

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── LiveKit ────────────────────────────────────────────────────────────
    livekit_url: str = "wss://your-project.livekit.cloud"
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # ── Pipecat agent worker (cold-start pre-warm) ─────────────────────────
    # Public HTTPS base URL of the agent worker service — used by
    # backend/services/agent_worker.py to wake the worker BEFORE dispatching a
    # call into a room and to keep it warm on a timer. Leave blank to disable
    # both (dispatch then behaves exactly as it did before, i.e. cold starts
    # cause agent-less rooms). As of the 2026-07-31 Railway migration this
    # points at the Railway worker (sleepApplication: false, so it never
    # actually cold-starts) — kept set anyway so this still degrades gracefully
    # if the worker is ever moved back to a plan that can sleep.
    agent_worker_url: str = ""

    # Background keep-warm pinger. Set to true on Railway 2026-07-31.
    #
    # This flag is about the HOST sleeping an idle service — nothing else. It starts
    # keep_warm_loop(), which is one outbound GET {AGENT_WORKER_URL}/worker every
    # KEEP_WARM_INTERVAL_SECONDS. It does NOT load or unload STT/TTS/VAD models, and
    # there is no idle-unload cycle anywhere in this codebase for it to disable —
    # model loading is prewarm() in backend/agent/pipeline.py, which is per
    # job-process and has no timer.
    #
    # The default was False for Render's free tier, where holding ONE service awake
    # (~730h/mo) would have exhausted a 750h ACCOUNT-WIDE allowance and suspended
    # every service. That constraint died with the Railway migration: both services
    # report sleepApplication=false, so nothing sleeps and nothing is metered by the
    # hour. What the flag still buys here is skipping the pre-warm probe on the
    # request path (~733ms measured), which is why it is now on.
    agent_worker_keep_warm: bool = False

    # Semantic end-of-turn detection (pipecat's Local Smart Turn v3 ONNX model).
    # DEFAULT OFF while the agent worker is on Render's free plan (0.1 CPU): the
    # per-utterance inference blocks the event loop there, which shows up as
    # "libwebrtc audio_stream queue overflow; dropped N queued frames" on live
    # calls, stuttering TTS and barge-in that reacts late. With it off, end-of-turn
    # uses plain timers (VAD stop_secs + user_speech_timeout ≈ 0.8s).
    # Turn this ON after upgrading the instance — semantic detection is better at
    # not cutting callers off mid-thought.
    agent_smart_turn: bool = False

    # ── Sarvam AI ──────────────────────────────────────────────────────────
    sarvam_api_key: str = ""

    # ── Google Gemini ──────────────────────────────────────────────────────
    gemini_api_key: str = ""

    # ── OpenAI ─────────────────────────────────────────────────────────────
    openai_api_key: str = ""

    # ── Anthropic ──────────────────────────────────────────────────────────
    anthropic_api_key: str = ""

    # ── DeepSeek ───────────────────────────────────────────────────────────
    deepseek_api_key: str = ""

    # ── Groq ───────────────────────────────────────────────────────────────
    groq_api_key: str = ""

    # ── Mistral ────────────────────────────────────────────────────────────
    mistral_api_key: str = ""

    # ── ElevenLabs ─────────────────────────────────────────────────────────
    elevenlabs_api_key: str = ""

    # ── Deepgram ───────────────────────────────────────────────────────────
    deepgram_api_key: str = ""

    # ── AssemblyAI ─────────────────────────────────────────────────────────
    assemblyai_api_key: str = ""

    # ── Newly-added provider catalog keys (STT/TTS/LLM/voice-clone) ────────
    cerebras_api_key: str = ""
    google_speech_api_key: str = ""
    azure_speech_key: str = ""
    cartesia_api_key: str = ""
    playht_api_key: str = ""
    resemble_api_key: str = ""

    # ── Exotel ─────────────────────────────────────────────────────────────
    exotel_api_key: str = ""

    # ── Vobiz ──────────────────────────────────────────────────────────────
    vobiz_account_sid: str = ""
    vobiz_auth_token: str = ""
    vobiz_virtual_number: str = ""
    vobiz_sip_domain: str = ""

    # ── Oxzygen HIS ────────────────────────────────────────────────────────
    oxzygen_base_url: str = ""
    oxzygen_api_key: str = ""

    # ── Telegram ───────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Google Sheets Webhook ──────────────────────────────────────────────
    google_sheets_webhook_url: str = ""

    # ── CORS (production — set CORS_ORIGIN on Render) ──────────────────────
    cors_origin: str = ""  # e.g. https://lifodial.vercel.app

    # ── Frontend ───────────────────────────────────────────────────────────
    vite_api_url: str = "http://localhost:8001"
    frontend_url: str = "http://localhost:5173"

    # ── Supabase Storage (object storage for uploads/branding) ─────────────
    supabase_url: str = ""                     # https://<ref>.supabase.co
    supabase_service_role_key: str = ""        # server-side only; never sent to client
    supabase_storage_bucket: str = "lifodial-uploads"        # private: KB, recordings, consent
    supabase_public_bucket: str = "lifodial-public"          # public: branding/avatars

    # ── Render (production env sync — used ONLY on explicit confirmation) ───
    render_api_key: str = ""
    render_service_id: str = ""


    @model_validator(mode="after")
    def _enforce_prod_secrets(self):
        if self.environment.lower() == "production":
            # SECRET_KEY also derives the Fernet key used to encrypt provider keys
            # at rest (see backend/security.py::_fernet), so this single guard
            # protects both JWT signing AND encryption-at-rest — there is no
            # separate FERNET_KEY to check.
            if self.secret_key.strip() in _WEAK_SECRETS or len(self.secret_key) < 32:
                raise RuntimeError(
                    "SECRET_KEY is missing, weak, or a known default. Set a strong "
                    "(>=32 char) unique SECRET_KEY before running in production."
                )
            # Never boot production against a missing DB or SQLite (db.py enforces
            # the resolved-URL case too; this catches it earlier with a clear msg).
            if not self.database_url.strip() or "sqlite" in self.database_url.lower():
                raise RuntimeError(
                    "DATABASE_URL is missing or points at SQLite while "
                    "ENVIRONMENT=production. Set the Supabase session-pooler "
                    "connection string before running in production."
                )
            if not self.superadmin_password:
                logger.warning(
                    "SUPERADMIN_PASSWORD is not set in production — superadmin login is disabled."
                )
        return self


settings = Settings()
