import asyncio
import base64
import io
import wave
import logging
from google import genai
from .base import STTProvider, TTSProvider
from backend.config import settings

logger = logging.getLogger(__name__)

# Reuse a single Gemini client across requests
_gemini_client: genai.Client | None = None

def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


class GeminiSTT(STTProvider):
    """
    Gemini-based STT.
    Sends audio inline (base64) instead of uploading to disk — 
    eliminates file I/O and the extra Files API round-trip.
    """
    async def transcribe(
        self, audio: bytes, lang: str = "en-IN"
    ) -> str:
        lang_names = {
            "hi-IN": "Hindi", "ta-IN": "Tamil",
            "te-IN": "Telugu", "kn-IN": "Kannada",
            "ml-IN": "Malayalam", "bn-IN": "Bengali",
            "ar-SA": "Arabic", "en-IN": "English",
            "mr-IN": "Marathi", "pa-IN": "Punjabi",
        }
        lang_name = lang_names.get(lang, "English")

        # Ensure WAV format (wrap raw PCM if needed) — in memory, no disk I/O
        if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
            wav_bytes = audio
        else:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)   # 16-bit
                wf.setframerate(16000)
                wf.writeframes(audio)
            wav_bytes = buf.getvalue()

        # Inline base64 — no file upload API round-trip
        audio_b64 = base64.b64encode(wav_bytes).decode()

        try:
            client = _get_gemini_client()
            # Run synchronous SDK call in thread pool to avoid blocking event loop
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model="gemini-2.0-flash",  # faster than 2.5-flash for STT
                    contents=[
                        {
                            "parts": [
                                {
                                    "inline_data": {
                                        "mime_type": "audio/wav",
                                        "data": audio_b64,
                                    }
                                },
                                {
                                    "text": (
                                        f"Transcribe this audio. The speaker is likely "
                                        f"speaking {lang_name}. "
                                        f"Return ONLY the transcript, nothing else."
                                    )
                                },
                            ]
                        }
                    ],
                )
            )
            return response.text.strip() if response.text else ""
        except Exception as e:
            logger.warning("GeminiSTT error: %s", e)
            return ""


class GeminiTTS(TTSProvider):
    """
    Gemini TTS (preview model).
    Runs in executor to avoid blocking the event loop.
    """
    async def synthesize(
        self, text: str,
        lang: str = "en-IN",
        voice: str = None,
    ) -> bytes:
        voices = {
            "hi-IN": "Charon", "en-IN": "Puck", "ta-IN": "Kore",
            "ar-SA": "Aoede", "te-IN": "Fenrir", "kn-IN": "Charon",
            "ml-IN": "Puck",
        }
        selected_voice = voice or voices.get(lang, "Puck")

        try:
            client = _get_gemini_client()
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model="gemini-2.0-flash-preview-tts",
                    contents=text,
                    config=genai.types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=genai.types.SpeechConfig(
                            voice_config=genai.types.VoiceConfig(
                                prebuilt_voice_config=genai.types.PrebuiltVoiceConfig(
                                    voice_name=selected_voice
                                )
                            )
                        ),
                    ),
                )
            )
            return response.candidates[0].content.parts[0].inline_data.data
        except Exception as e:
            logger.warning("GeminiTTS error: %s", e)
            return b""
