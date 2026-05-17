import asyncio
import logging
import time
import uuid
import base64
import io
import wave
import re
from datetime import datetime
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import silero
import google.generativeai as genai
import httpx

logger = logging.getLogger(__name__)

# ── Persistent HTTP client (shared across all plugin calls) ───────────────────
# Creating a new client per request wastes ~50-150ms on TCP handshake.
# This client is reused for the lifetime of the process.
_http_client: httpx.AsyncClient | None = None

def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=3.0, read=12.0, write=5.0, pool=2.0),
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=30,
            ),
            http2=True,  # HTTP/2 multiplexing reduces per-request overhead
        )
    return _http_client


class LifodialAgent(Agent):
    """
    Streaming voice pipeline for Lifodial.
    Uses Sarvam for STT + TTS, Gemini Flash for LLM.
    Target: < 800ms voice-to-voice latency.
    """
    
    def __init__(self, agent_config: dict, tenant: dict):
        self.agent_config = agent_config
        self.tenant = tenant
        self.session_history = []
        self.detected_lang = agent_config.get(
            "tts_language", "hi-IN"
        )
        self.turn_count = 0
        self.call_start = time.time()
        self.interruption_count = 0
        self.latency_log: list[dict] = []
        
        # Build system prompt with clinic info
        self.system_prompt = self._build_system_prompt()
        
        super().__init__(instructions=self.system_prompt)
    
    def _build_system_prompt(self) -> str:
        doctors = self.tenant.get("doctors", [])
        doctors_text = "\n".join([
            f"- {d['name']} ({d['specialization']})"
            for d in doctors
        ]) or "- General Physician available"
        
        return f"""You are {self.agent_config.get('agent_name', 'Receptionist')}, \
the AI voice receptionist for {self.tenant.get('clinic_name', 'the clinic')}.

CRITICAL RULES FOR VOICE:
- Maximum 2 short sentences per response
- This is a PHONE CALL — be concise and natural
- Never say you are an AI unless directly asked
- Ask only ONE question at a time
- Speak numbers as words (eleven AM not 11 AM)
- Auto-detect patient language and respond in it

AVAILABLE DOCTORS:
{doctors_text}

CLINIC HOURS: {self.tenant.get('working_hours', '9 AM - 7 PM, Mon-Sat')}

BOOKING FLOW:
1. Get patient name
2. Get required specialty
3. Offer available slot
4. Confirm booking
5. Give appointment ID

EMERGENCY: On keywords (heart attack, accident, emergency, \
unconscious, bleeding) → transfer immediately.

FALLBACK: "Kya aap dobara bol sakte hain?" (or in patient's language)"""
    
    async def on_agent_speech_interrupted(self, context):
        """Called when patient speaks while AI is talking (barge-in)."""
        self.interruption_count += 1
        logger.info(
            f"Barge-in #{self.interruption_count} — patient interrupted AI. "
            f"LiveKit stops TTS automatically."
        )

    async def on_user_turn_completed(self, turn_ctx, new_message):
        """Called when patient finishes speaking — logs turn + timing."""
        text = new_message.text_content
        if not text:
            return
        
        self.turn_count += 1
        
        # Add to history
        self.session_history.append({
            "role": "user", "text": text
        })
        
        logger.info(
            f"Turn {self.turn_count} | Patient: {text[:60]} "
            f"| Lang: {self.detected_lang}"
        )

    def record_latency(self, llm_ms: int, tts_ms: int):
        """Record per-turn latency for health dashboard."""
        total = llm_ms + tts_ms
        self.latency_log.append({"llm_ms": llm_ms, "tts_ms": tts_ms, "total_ms": total})
        logger.info(
            f"Turn {self.turn_count}: LLM={llm_ms}ms TTS={tts_ms}ms Total={total}ms"
        )
        if total > 1500:
            logger.warning(f"HIGH LATENCY: {total}ms on turn {self.turn_count}")
    
    def avg_latency_ms(self) -> float | None:
        if not self.latency_log:
            return None
        return sum(t["total_ms"] for t in self.latency_log) / len(self.latency_log)


async def entrypoint(ctx):
    """
    LiveKit agent entrypoint.
    Sets up streaming pipeline with Sarvam + Gemini.
    """
    import json
    from backend.config import settings
    from livekit import api as livekit_api
    
    # Parse room metadata
    metadata = {}
    try:
        metadata = json.loads(ctx.room.metadata or '{}')
    except:
        pass
    
    tenant_id = metadata.get("tenant_id")
    agent_id = metadata.get("agent_id")
    
    logger.info(f"Agent starting for tenant={tenant_id} agent={agent_id}")
    
    # Load config from DB or use metadata defaults
    agent_config = metadata
    tenant = {
        "clinic_name": metadata.get("clinic_name", "Clinic"),
        "working_hours": "9 AM - 7 PM, Mon-Sat",
        "doctors": []
    }
    
    # Try to load from DB
    try:
        from backend.db import AsyncSessionLocal
        from backend.models.agent_config import AgentConfig
        from backend.models.tenant import Tenant
        from backend.models.doctor import Doctor
        from sqlalchemy import select
        
        async with AsyncSessionLocal() as db:
            if agent_id:
                result = await db.execute(
                    select(AgentConfig).where(
                        AgentConfig.id == agent_id
                    )
                )
                config = result.scalar_one_or_none()
                if config:
                    agent_config = {
                        "agent_name": config.agent_name,
                        "first_message": config.first_message,
                        "system_prompt": config.system_prompt,
                        "tts_voice": config.tts_voice,
                        "tts_language": config.tts_language,
                        "tts_model": config.tts_model,
                        "stt_model": config.stt_model,
                        "llm_model": config.llm_model,
                        "llm_temperature": config.llm_temperature,
                    }
            
            if tenant_id:
                t_result = await db.execute(
                    select(Tenant).where(Tenant.id == tenant_id)
                )
                t = t_result.scalar_one_or_none()
                if t:
                    tenant["clinic_name"] = t.clinic_name
                
                d_result = await db.execute(
                    select(Doctor).where(
                        Doctor.tenant_id == tenant_id
                    )
                )
                doctors = d_result.scalars().all()
                tenant["doctors"] = [
                    {"name": d.name, "specialization": d.specialization}
                    for d in doctors
                ]
    except Exception as e:
        logger.warning(f"Could not load from DB: {e}. Using metadata.")
    
    # Configure Gemini API
    genai.configure(api_key=settings.gemini_api_key)
    
    # Create agent session with LiveKit
    await ctx.connect()
    
    session = AgentSession(
        # Sarvam STT — best for Indian languages
        stt=SarvamSTTPlugin(
            api_key=settings.sarvam_api_key,
            model=agent_config.get("stt_model", "saaras:v2"),  # v2 is faster for short utterances
            language=agent_config.get("tts_language", "hi-IN"),
        ),
        # Gemini LLM — streaming
        llm=GeminiLLMPlugin(
            api_key=settings.gemini_api_key,
            model=agent_config.get("llm_model", "gemini-2.0-flash"),  # 2.0-flash is faster than 2.5-flash
            temperature=float(
                agent_config.get("llm_temperature", 0.3) if agent_config.get("llm_temperature") is not None else 0.3
            ),
        ),
        # Sarvam TTS — streaming
        tts=SarvamTTSPlugin(
            api_key=settings.sarvam_api_key,
            model=agent_config.get("tts_model", "bulbul:v3"),
            voice=agent_config.get("tts_voice", "ritu"),
            language=agent_config.get("tts_language", "hi-IN"),
        ),
        # Silero VAD — detects when patient stops speaking
        vad=silero.VAD.load(
            min_silence_duration=0.25,       # reduced: 250ms instead of 300ms
            prefix_padding_duration=0.1,     # reduced: 100ms instead of 200ms
            activation_threshold=0.5,
        ),
        turn_detection="vad",
    )
    
    agent = LifodialAgent(agent_config, tenant)
    
    # Start session
    session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )
    
    # Speak first message
    first_msg = agent_config.get(
        "first_message",
        f"Namaste! {tenant['clinic_name']} mein aapka swagat hai. "
        f"Main kaise madad kar sakti hoon?"
    )
    
    await session.say(first_msg, allow_interruptions=True)

    # ── Post-call credit deduction ─────────────────────────────────
    async def _on_call_end():
        """Deduct credits after call ends."""
        try:
            duration = int(time.time() - agent.call_start)
            if duration <= 0 or not tenant_id:
                return

            from backend.db import AsyncSessionLocal
            from backend.services.credit_service import CreditService

            call_id = metadata.get("call_record_id")

            async with AsyncSessionLocal() as db:
                result = await CreditService.deduct_call_credits(
                    db,
                    tenant_id=tenant_id,
                    duration_seconds=duration,
                    call_id=call_id,
                )
                await db.commit()

            logger.info(
                "Call billing: tenant=%s duration=%ds deducted=₹%.2f balance=₹%.2f",
                tenant_id,
                duration,
                result["deducted"],
                result["balance_after"],
            )
        except Exception as exc:
            logger.error("Credit deduction failed: %s", exc, exc_info=True)

    # Wait for room disconnect, then deduct
    @ctx.room.on("disconnected")
    def _handle_disconnect():
        asyncio.ensure_future(_on_call_end())


# ── Sarvam STT Plugin ──────────────────────────────────────────
class SarvamSTTPlugin:
    """
    Sarvam STT wrapper for LiveKit agents.
    Uses persistent HTTP client for low latency.
    """
    def __init__(self, api_key: str, model: str, language: str):
        self.api_key = api_key
        self.model = model
        self.language = language
    
    async def transcribe(self, audio: bytes) -> str:
        """
        Transcribe audio bytes via Sarvam STT.
        
        LiveKit sends raw PCM frames (16-bit, 16kHz, mono).
        Sarvam API requires a valid WAV file, so we wrap PCM
        in a WAV header if needed.
        """
        if not audio or len(audio) < 44:
            return ""

        # Detect if already WAV (starts with RIFF header)
        is_wav = audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"

        if is_wav:
            wav_bytes = audio
        else:
            # Raw PCM → WAV conversion (16-bit, 16kHz, mono) — in-memory, no disk I/O
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(16000)
                wf.writeframes(audio)
            wav_bytes = wav_buffer.getvalue()

        client = _get_http_client()
        response = await client.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": self.api_key},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={
                "language_code": self.language,
                "model": self.model,
                "with_timestamps": "false",
                "with_disfluencies": "false",
            },
        )
        if response.status_code != 200:
            logger.warning(
                "Sarvam STT error %d: %s",
                response.status_code,
                response.text[:200],
            )
            return ""
        return response.json().get("transcript", "")


# ── Sarvam TTS Plugin ──────────────────────────────────────────
class SarvamTTSPlugin:
    """
    Sarvam TTS wrapper for LiveKit agents.
    - Persistent HTTP client (no per-request TCP overhead)
    - enable_preprocessing=False for speed
    - Parallel chunk synthesis for long texts
    """
    def __init__(self, api_key: str, model: str, 
                 voice: str, language: str):
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.language = language
    
    async def synthesize(self, text: str) -> bytes:
        if not text.strip():
            return b""
        
        # v3 supports 2500 chars, v2 supports 500
        max_chars = 2500 if "v3" in self.model else 500
        
        if len(text) <= max_chars:
            return await self._call_tts(text)
        
        # Chunk and synthesize IN PARALLEL for minimal latency
        chunks = self._chunk_text(text, max_chars - 50)
        tasks = [self._call_tts(chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)
        return b"".join(r for r in results if r)
    
    async def _call_tts(self, text: str) -> bytes:
        # ── Validate model, speaker, and language ───────────────────
        normalized_model = self.model if self.model and self.model.startswith("bulbul:") else "bulbul:v3"

        _V3_SPEAKERS = {
            "aditya", "ritu", "ashutosh", "priya", "neha", "rahul",
            "pooja", "rohan", "simran", "kavya", "amit", "dev",
            "ishita", "shreya", "ratan", "varun", "manan", "sumit",
            "roopa", "kabir", "aayan", "shubh", "advait", "anand",
            "tanya", "tarun", "sunny", "mani", "gokul", "vijay",
            "shruti", "suhani", "mohit", "kavitha", "rehan", "soham",
            "rupali", "niharika",
        }
        _VOICE_REMAP = {
            "meera": "shreya", "pavithra": "kavitha", "maitreyi": "priya",
            "arvind": "rahul", "amol": "aditya", "amartya": "rohan",
            "diya": "ritu", "neel": "amit", "misha": "simran", "vian": "shubh",
        }
        req_speaker = (self.voice or "priya").lower().strip()
        if " " in req_speaker:
            req_speaker = req_speaker.split(" ", 1)[-1].strip()
        req_speaker = _VOICE_REMAP.get(req_speaker, req_speaker)
        normalized_voice = req_speaker if req_speaker in _V3_SPEAKERS else "priya"
        if normalized_voice != req_speaker:
            logger.info("pipeline TTS: remapped speaker '%s' → '%s'", self.voice, normalized_voice)

        _VALID_LANGS = {
            "as-IN", "bn-IN", "brx-IN", "doi-IN", "en-IN", "gu-IN",
            "hi-IN", "kn-IN", "kok-IN", "ks-IN", "mai-IN", "ml-IN",
            "mni-IN", "mr-IN", "ne-IN", "od-IN", "pa-IN", "sa-IN",
            "sat-IN", "sd-IN", "ta-IN", "te-IN", "ur-IN",
        }
        lang = (self.language or "en-IN").strip()
        if lang not in _VALID_LANGS:
            prefix = lang.split("-")[0].lower()
            lang = next((v for v in _VALID_LANGS if v.startswith(prefix + "-")), "en-IN")
        normalized_language = lang

        payload = {
            "text": text,
            "target_language_code": normalized_language,
            "speaker": normalized_voice,
            "model": normalized_model,
            "speech_sample_rate": 16000,
            "enable_preprocessing": False,   # ← SPEED: skip server-side preprocessing
            "pace": 1.05,                    # ← Slightly faster pace to reduce audio length
        }

        client = _get_http_client()
        response = await client.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={
                "api-subscription-key": self.api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if response.status_code != 200:
            logger.error(
                f"Sarvam TTS error {response.status_code}: "
                f"{response.text[:200]}"
            )
            return b""

        data = response.json()
        audios = data.get("audios", [])
        if not audios:
            return b""

        return base64.b64decode(audios[0])
    
    def _chunk_text(self, text: str, max_len: int) -> list:
        sentences = re.split(r'(?<=[।.!?])\s+', text)
        chunks, current = [], ""
        for s in sentences:
            if len(current) + len(s) < max_len:
                current += s + " "
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = s + " "
        if current.strip():
            chunks.append(current.strip())
        return chunks


# ── Gemini LLM Plugin ──────────────────────────────────────────
class GeminiLLMPlugin:
    """
    Gemini LLM wrapper with streaming support.
    Switches model dynamically based on agent config.
    """
    def __init__(self, api_key: str, model: str, temperature: float):
        genai.configure(api_key=api_key)
        self.model_id = model
        self.temperature = temperature
        self._model = genai.GenerativeModel(
            model,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": 120,   # ← Reduced: voice responses are short
                "candidate_count": 1,
            }
        )
    
    async def generate(self, prompt: str, history: list) -> str:
        chat = self._model.start_chat(history=[
            {
                "role": msg["role"],
                "parts": [msg["text"]]
            }
            for msg in history[-6:]  # last 6 turns
        ])
        
        response = await chat.send_message_async(prompt)
        return response.text.strip()
    
    async def generate_streaming(self, prompt: str, history: list):
        """Yields text chunks as they are generated."""
        chat = self._model.start_chat(history=[
            {"role": m["role"], "parts": [m["text"]]}
            for m in history[-6:]
        ])
        
        async for chunk in await chat.send_message_async(
            prompt, stream=True
        ):
            if chunk.text:
                yield chunk.text


if __name__ == "__main__":
    from livekit.agents import cli, WorkerOptions
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
