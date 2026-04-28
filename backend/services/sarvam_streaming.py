"""
Sarvam Streaming Speech-to-Text API client
Real-time audio transcription via WebSocket connection
Supports: transcribe, translate, verbatim, translit, codemix modes
"""
import logging
import base64
import json
import asyncio
from typing import AsyncGenerator, Optional, Dict, Any
import websockets
from backend.config import settings

logger = logging.getLogger(__name__)

# Sarvam Streaming API endpoints
SARVAM_STT_WS_URL = "wss://api.sarvam.ai/speech_to_text_streaming"
SARVAM_STTT_WS_URL = "wss://api.sarvam.ai/speech_to_text_translate_streaming"


class SarvamStreamingSTT:
    """
    Streaming speech-to-text client for Sarvam AI
    Handles real-time audio transcription with WebSocket
    """

    def __init__(
        self,
        mode: str = "transcribe",
        language_code: str = "en-IN",
        model: str = "saarika:v2",
        high_vad_sensitivity: bool = True,
        vad_signals: bool = True,
        flush_signal: bool = False,
        sample_rate: int = 16000,
    ):
        """
        Initialize streaming STT connection parameters
        
        Args:
            mode: 'transcribe', 'translate', 'verbatim', 'translit', 'codemix'
            language_code: Language code (e.g., 'en-IN', 'hi-IN', 'kn-IN')
            model: Model version (e.g., 'saarika:v2', 'saarika:v2.5')
            high_vad_sensitivity: Enhanced voice activity detection
            vad_signals: Receive speech_start/speech_end events
            flush_signal: Enable manual buffer flushing
            sample_rate: Audio sample rate in Hz (8000 or 16000)
        """
        self.mode = mode
        self.language_code = language_code
        self.model = model
        self.high_vad_sensitivity = high_vad_sensitivity
        self.vad_signals = vad_signals
        self.flush_signal = flush_signal
        self.sample_rate = sample_rate
        self.ws = None
        self.api_key = settings.sarvam_api_key

    async def connect(self) -> bool:
        """Establish WebSocket connection to Sarvam streaming API"""
        if not self.api_key:
            logger.warning("No Sarvam API key configured — streaming STT will be unavailable")
            return False

        try:
            # Choose endpoint based on mode
            ws_url = SARVAM_STTT_WS_URL if self.mode == "translate" else SARVAM_STT_WS_URL

            # Build connection parameters
            params = {
                "api_subscription_key": self.api_key,
                "model": self.model,
                "language_code": self.language_code,
                "sample_rate": self.sample_rate,
                "high_vad_sensitivity": str(self.high_vad_sensitivity).lower(),
                "vad_signals": str(self.vad_signals).lower(),
                "flush_signal": str(self.flush_signal).lower(),
            }

            # Add mode for saarika models (transcribe, translate, verbatim, translit, codemix)
            if self.model.startswith("saarika") and self.mode != "translate":
                params["mode"] = self.mode

            # Build query string
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            connection_url = f"{ws_url}?{query_string}"

            logger.info(f"Connecting to Sarvam Streaming STT: {self.model} ({self.mode})")
            self.ws = await websockets.connect(connection_url)
            logger.info("✅ Connected to Sarvam Streaming API")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Sarvam Streaming API: {e}")
            self.ws = None
            return False

    async def send_audio(
        self,
        audio_bytes: bytes,
        encoding: str = "audio/wav",
        input_audio_codec: str = "wav",
    ) -> bool:
        """
        Send audio data to Sarvam for transcription
        
        Args:
            audio_bytes: Raw audio bytes or base64-encoded data
            encoding: Audio format (e.g., 'audio/wav', 'audio/pcm')
            input_audio_codec: Codec type ('wav', 'pcm_s16le', 'pcm_l16', 'pcm_raw')
        
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.ws:
            logger.error("WebSocket not connected, call connect() first")
            return False

        try:
            # If bytes are raw PCM, encode as base64
            if isinstance(audio_bytes, bytes):
                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            else:
                audio_b64 = audio_bytes

            # Prepare transcription request
            request = {
                "audio": audio_b64,
                "encoding": encoding,
                "sample_rate": self.sample_rate,
            }

            # Send to Sarvam API
            await self.ws.send(json.dumps(request))
            return True

        except Exception as e:
            logger.error(f"Failed to send audio to Sarvam: {e}")
            return False

    async def flush(self) -> bool:
        """Force immediate processing without waiting for silence detection"""
        if not self.ws or not self.flush_signal:
            return False

        try:
            await self.ws.send(json.dumps({"flush": True}))
            return True
        except Exception as e:
            logger.error(f"Failed to flush Sarvam buffer: {e}")
            return False

    async def receive_results(self) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream transcription results from Sarvam
        
        Yields:
            Message dictionaries with keys: type, text/translation, confidence, language, etc.
        """
        if not self.ws:
            logger.error("WebSocket not connected")
            return

        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)

                    # Handle different message types
                    if data.get("type") == "speech_start":
                        yield {"type": "speech_start", "timestamp": data.get("timestamp")}

                    elif data.get("type") == "speech_end":
                        yield {"type": "speech_end", "timestamp": data.get("timestamp")}

                    elif data.get("type") == "transcript":
                        # STT response
                        yield {
                            "type": "transcript",
                            "text": data.get("transcript", ""),
                            "confidence": data.get("confidence"),
                            "language": self.language_code,
                            "mode": self.mode,
                        }

                    elif data.get("type") == "translation":
                        # STTT response (when mode='translate')
                        yield {
                            "type": "translation",
                            "text": data.get("translation", ""),
                            "confidence": data.get("confidence"),
                            "source_language": self.language_code,
                            "target_language": "en-IN",
                        }

                    else:
                        # Pass through other message types
                        yield data

                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse Sarvam message: {e}")
                    continue

        except websockets.exceptions.ConnectionClosed:
            logger.info("Sarvam streaming connection closed")
        except Exception as e:
            logger.error(f"Error receiving from Sarvam: {e}")

    async def close(self):
        """Close WebSocket connection"""
        if self.ws:
            try:
                await self.ws.close()
                logger.info("Closed Sarvam streaming connection")
            except Exception as e:
                logger.error(f"Error closing Sarvam connection: {e}")
            finally:
                self.ws = None


async def create_streaming_stt(
    language_code: str = "en-IN",
    mode: str = "transcribe",
    sample_rate: int = 16000,
) -> Optional[SarvamStreamingSTT]:
    """
    Factory function to create and connect a streaming STT client
    
    Args:
        language_code: Language for transcription
        mode: transcribe, translate, verbatim, translit, codemix
        sample_rate: Audio sample rate (8000 or 16000 Hz)
    
    Returns:
        Connected SarvamStreamingSTT instance or None if connection fails
    """
    client = SarvamStreamingSTT(
        mode=mode,
        language_code=language_code,
        model="saarika:v2",
        high_vad_sensitivity=True,
        vad_signals=True,
        flush_signal=False,
        sample_rate=sample_rate,
    )

    if await client.connect():
        return client
    else:
        return None


async def transcribe_audio_stream(
    audio_iterator: AsyncGenerator[bytes, None],
    language_code: str = "en-IN",
    mode: str = "transcribe",
    sample_rate: int = 16000,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    High-level convenience function to transcribe a stream of audio
    
    Usage:
        async for result in transcribe_audio_stream(audio_chunks, language_code="hi-IN"):
            if result["type"] == "transcript":
                print(result["text"])
    
    Args:
        audio_iterator: Async generator yielding audio bytes
        language_code: Language code for STT
        mode: Transcription mode
        sample_rate: Audio sample rate
    
    Yields:
        Transcription results as they arrive
    """
    client = await create_streaming_stt(
        language_code=language_code,
        mode=mode,
        sample_rate=sample_rate,
    )

    if not client:
        logger.error("Failed to create streaming STT client")
        return

    try:
        # Send audio in background task
        async def send_audio():
            async for audio_chunk in audio_iterator:
                await client.send_audio(audio_chunk, encoding="audio/wav")
                await asyncio.sleep(0.01)  # Small delay between chunks

        send_task = asyncio.create_task(send_audio())

        # Receive results
        async for result in client.receive_results():
            yield result

        await send_task

    finally:
        await client.close()
