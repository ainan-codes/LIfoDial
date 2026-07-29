"""
backend/routers/credits.py — Credit balance read-out + debug STT/TTS endpoints.

Credits are NOT enforced anywhere in this MVP phase: no call is ever gated,
refused, or ended because of a balance (see backend/services/credit_service.py).
The ledger tables remain in place, so this read-only endpoint stays for the
clinic dashboard's balance card.

Clinic admin endpoints:
  • GET  /credits/my-balance?tenant_id=xxx — own balance + recent txns

Debug endpoints:
  • POST /debug/test-stt    — test Sarvam STT connectivity
  • POST /debug/test-tts    — test Sarvam TTS connectivity
"""
import logging
from fastapi import APIRouter, HTTPException

from backend.auth import CurrentUser, SuperAdmin
from backend.db import async_session
from backend.services.credit_service import CreditService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Clinic Admin: My Balance ─────────────────────────────────────────────────

@router.get("/credits/my-balance")
async def my_balance(tenant_id: str, user: CurrentUser = None) -> dict:
    """Get credit balance for a specific clinic (clinic admin view)."""
    user.require_owns(tenant_id)
    try:
        async with async_session() as db:
            credits = await CreditService.get_or_create_balance(db, tenant_id)
            txns = await CreditService.get_transactions(db, tenant_id, limit=10)
            await db.commit()

            return {
                "tenant_id": tenant_id,
                "balance": credits.balance,
                "rate_per_minute": credits.rate_per_minute,
                "total_added": credits.total_added,
                "total_deducted": credits.total_deducted,
                "is_low": credits.balance < credits.low_balance_threshold,
                "low_balance_threshold": credits.low_balance_threshold,
                "recent_transactions": txns,
            }
    except Exception as e:
        logger.exception("Error fetching my balance: %s", e)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# DEBUG ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/debug/test-stt")
async def test_stt(user: SuperAdmin = None) -> dict:
    """
    Test Sarvam STT API connectivity.
    Sends 1 second of silence (16-bit PCM WAV) and checks the response.
    """
    import struct
    import io
    import wave
    import httpx
    from backend.config import settings

    api_key = settings.sarvam_api_key
    if not api_key:
        return {"status": "error", "message": "SARVAM_API_KEY not set"}

    # Generate 1 second of silence as WAV
    sample_rate = 16000
    num_samples = sample_rate  # 1 second
    silence = b"\x00\x00" * num_samples  # 16-bit silence

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(silence)

    wav_bytes = wav_buffer.getvalue()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.sarvam.ai/speech-to-text",
                headers={"api-subscription-key": api_key},
                files={"file": ("test.wav", wav_bytes, "audio/wav")},
                data={
                    "language_code": "hi-IN",
                    "model": "saaras:v3",
                    "with_timestamps": "false",
                    "with_disfluencies": "false",
                },
            )

            return {
                "status": "ok" if response.status_code == 200 else "error",
                "http_status": response.status_code,
                "response": response.json() if response.status_code == 200 else response.text[:200],
                "wav_size_bytes": len(wav_bytes),
                "message": "Sarvam STT API is reachable" if response.status_code == 200 else "STT call failed",
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Connection failed: {str(e)[:200]}",
        }


@router.post("/debug/test-tts")
async def test_tts(user: SuperAdmin = None) -> dict:
    """Test Sarvam TTS API connectivity."""
    import httpx
    from backend.config import settings

    api_key = settings.sarvam_api_key
    if not api_key:
        return {"status": "error", "message": "SARVAM_API_KEY not set"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "inputs": ["Namaste, test successful"],
                    "target_language_code": "hi-IN",
                    "speaker": "shreya",
                    "model": "bulbul:v3",
                    "speech_sample_rate": 16000,
                    "enable_preprocessing": True,
                },
            )

            data = response.json() if response.status_code == 200 else {}
            audio_count = len(data.get("audios", []))

            return {
                "status": "ok" if response.status_code == 200 and audio_count > 0 else "error",
                "http_status": response.status_code,
                "audio_chunks": audio_count,
                "message": "Sarvam TTS API is working" if audio_count > 0 else "TTS call failed",
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Connection failed: {str(e)[:200]}",
        }
