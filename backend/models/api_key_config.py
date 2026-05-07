"""
backend/models/api_key_config.py
Stores provider API keys for LLM, STT, TTS, Telephony, HIS.
Keys are stored encrypted-at-rest (base64 obfuscation in dev; use KMS in prod).
"""
import uuid
import base64
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Text
from backend.db import Base


def _obfuscate(value: str) -> str:
    """Simple reversible obfuscation for dev. Use proper encryption in production."""
    return base64.b64encode(value.encode()).decode()


def _deobfuscate(value: str) -> str:
    try:
        return base64.b64decode(value.encode()).decode().strip()
    except Exception:
        return str(value).strip() if value else ""


class ApiKeyConfig(Base):
    __tablename__ = "api_key_configs"

    id: str = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: str = Column(String(50), nullable=False)       # gemini, openai, sarvam, deepgram, elevenlabs, etc.
    category: str = Column(String(20), nullable=False)       # llm | stt | tts | telephony | his
    display_name: str = Column(String(100), nullable=False)  # "Google Gemini"
    api_key_enc: str = Column(Text, nullable=True)           # obfuscated key
    is_active: bool = Column(Boolean, default=False)         # is this the currently selected provider
    extra_config: str = Column(Text, nullable=True)          # JSON for base_url, model, etc.
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_key(self, raw_key: str):
        self.api_key_enc = _obfuscate(raw_key.strip()) if raw_key else None

    def get_key_masked(self) -> str:
        """Returns masked key for display: sk-...xxxx"""
        if not self.api_key_enc:
            return ""
        raw = _deobfuscate(self.api_key_enc)
        if len(raw) <= 8:
            return "****"
        return raw[:4] + "•" * (len(raw) - 8) + raw[-4:]

    def get_key_raw(self) -> str:
        if not self.api_key_enc:
            return ""
        return _deobfuscate(self.api_key_enc)
