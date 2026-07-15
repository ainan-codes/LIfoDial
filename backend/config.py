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
