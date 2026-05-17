import asyncio
import base64
import logging
import re
from backend.config import settings
from backend.agent.pipeline import _get_http_client

logger = logging.getLogger(__name__)


class SarvamTTS:
    """
    Sarvam TTS provider.
    - Uses shared persistent HTTP client (no per-request TCP overhead)
    - enable_preprocessing=False for lower latency
    - Parallel chunk synthesis for long texts
    """
    def __init__(
        self,
        model="bulbul:v3",
        voice="meera",
        language="hi-IN",
        pitch=0.0,
        pace=1.05,
        loudness=1.0,
    ):
        self.model = model
        self.voice = voice
        self.language = language
        self.pitch = pitch
        self.pace = pace
        self.loudness = loudness

    async def synthesize(
        self, text: str, lang: str = None, voice: str = None
    ) -> bytes:
        use_lang = lang or self.language
        use_voice = voice or self.voice

        if not text or not text.strip():
            return b""

        # Sarvam 500 char limit per request (v3 is ~2500 but we keep 450 safe margin)
        if len(text) > 450:
            return await self._synthesize_chunked(text, use_lang, use_voice)

        return await self._call_tts(text, use_lang, use_voice)

    async def _call_tts(self, text: str, lang: str, voice: str) -> bytes:
        client = _get_http_client()
        response = await client.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={
                "api-subscription-key": settings.sarvam_api_key,
                "Content-Type": "application/json",
            },
            json={
                "inputs": [text],
                "target_language_code": lang,
                "speaker": voice,
                "model": self.model,
                "pitch": self.pitch,
                "pace": self.pace,
                "loudness": self.loudness,
                "speech_sample_rate": 16000,
                "enable_preprocessing": False,  # ← faster: skip server-side NLP
            },
        )

        if response.status_code != 200:
            logger.warning(
                "Sarvam TTS error: %d — %s",
                response.status_code,
                response.text[:200],
            )
            return b""

        data = response.json()
        if "audios" not in data or not data["audios"]:
            logger.warning("Sarvam TTS: no audio in response")
            return b""

        return base64.b64decode(data["audios"][0])

    async def _synthesize_chunked(
        self, text: str, lang: str, voice: str
    ) -> bytes:
        sentences = re.split(r'(?<=[।.!?])\s+', text)
        chunks, current = [], ""
        for s in sentences:
            if len(current) + len(s) < 450:
                current += s + " "
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = s + " "
        if current.strip():
            chunks.append(current.strip())

        # Synthesize all chunks IN PARALLEL instead of sequentially
        tasks = [self._call_tts(chunk, lang, voice) for chunk in chunks]
        results = await asyncio.gather(*tasks)

        audio_parts = [r for r in results if r]
        return b"".join(audio_parts)
