"""
backend/models/api_key_config.py
Stores provider API keys for LLM, STT, TTS, Telephony, HIS.

Keys are encrypted at rest with Fernet (AES-128-CBC + HMAC), keyed off the app
SECRET_KEY — see backend/security.py encrypt_secret/decrypt_secret. Reads
transparently fall back to the legacy base64 "obfuscation" for rows written
before this change, so existing keys keep working and get upgraded to real
encryption the next time they're saved.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Boolean, Text
from backend.db import Base
from backend.security import encrypt_secret, decrypt_secret


class ApiKeyConfig(Base):
    __tablename__ = "api_key_configs"

    id: str = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider: str = Column(String(50), nullable=False)       # gemini, openai, sarvam, deepgram, elevenlabs, etc.
    category: str = Column(String(20), nullable=False)       # llm | stt | tts | telephony | his | voice_clone
    display_name: str = Column(String(100), nullable=False)  # "Google Gemini"
    api_key_enc: str = Column(Text, nullable=True)           # Fernet-encrypted key ('fernet:...'); legacy base64 tolerated on read
    is_active: bool = Column(Boolean, default=False)         # is this the currently selected provider
    extra_config: str = Column(Text, nullable=True)          # JSON for base_url, model, etc.
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_key(self, raw_key: str):
        self.api_key_enc = encrypt_secret(raw_key.strip()) if raw_key else None

    def get_key_masked(self) -> str:
        """Masked for display: first 4 + bullets + last 4. Never returns the full key."""
        raw = self.get_key_raw()
        if not raw:
            return ""
        if len(raw) <= 8:
            return "****"
        return raw[:4] + "•" * (len(raw) - 8) + raw[-4:]

    def get_key_raw(self) -> str:
        if not self.api_key_enc:
            return ""
        return decrypt_secret(self.api_key_enc)
