"""
backend/config.py — Pydantic settings for Lifodial.
All secrets loaded from .env. Never access os.environ directly;
always import and use `settings` from this module.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./lifodial.db"
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

    # ── CORS (production — set CORS_ORIGIN on Render) ──────────────────────
    cors_origin: str = ""  # e.g. https://lifodial.vercel.app

    # ── Frontend ───────────────────────────────────────────────────────────
    vite_api_url: str = "http://localhost:8001"
    frontend_url: str = "http://localhost:5173"


settings = Settings()
