"""
backend/routers/credits.py — Credit management + debug STT endpoints.

Super admin endpoints:
  • GET  /credits           — all clinic balances
  • POST /credits/topup     — add credits to a clinic
  • POST /credits/set-rate  — change per-minute rate
  • GET  /credits/{tid}/transactions — transaction history

Clinic admin endpoints:
  • GET  /credits/my-balance?tenant_id=xxx — own balance + recent txns

Debug endpoints:
  • POST /debug/test-stt    — test Sarvam STT connectivity
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db import async_session
from backend.services.credit_service import CreditService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class TopUpPayload(BaseModel):
    tenant_id: str
    amount: float
    description: str = "Admin top-up"


class SetRatePayload(BaseModel):
    tenant_id: str
    rate_per_minute: float


# ── Super Admin: All Balances ─────────────────────────────────────────────────

@router.get("/credits")
async def list_all_credits() -> dict:
    """Get all clinic credit balances (super admin)."""
    try:
        async with async_session() as db:
            balances = await CreditService.get_all_balances(db)
            return {"credits": balances, "total": len(balances)}
    except Exception as e:
        logger.exception("Error listing credits: %s", e)
        raise HTTPException(500, str(e))


# ── Super Admin: Top Up ──────────────────────────────────────────────────────

@router.post("/credits/topup")
async def topup_credits(payload: TopUpPayload) -> dict:
    """Add credits to a clinic's balance."""
    try:
        async with async_session() as db:
            result = await CreditService.add_credits(
                db,
                tenant_id=payload.tenant_id,
                amount=payload.amount,
                description=payload.description,
                performed_by="super_admin",
            )
            await db.commit()
            return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Error topping up credits: %s", e)
        raise HTTPException(500, str(e))


# ── Super Admin: Set Rate ────────────────────────────────────────────────────

@router.post("/credits/set-rate")
async def set_credit_rate(payload: SetRatePayload) -> dict:
    """Update per-minute billing rate for a clinic."""
    try:
        async with async_session() as db:
            result = await CreditService.set_rate(
                db,
                tenant_id=payload.tenant_id,
                rate_per_minute=payload.rate_per_minute,
            )
            await db.commit()
            return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("Error setting rate: %s", e)
        raise HTTPException(500, str(e))


# ── Super Admin: Transaction History ─────────────────────────────────────────

@router.get("/credits/{tenant_id}/transactions")
async def get_transactions(tenant_id: str, limit: int = 50) -> dict:
    """Get transaction history for a specific clinic."""
    try:
        async with async_session() as db:
            txns = await CreditService.get_transactions(db, tenant_id, limit)
            return {"transactions": txns, "total": len(txns)}
    except Exception as e:
        logger.exception("Error fetching transactions: %s", e)
        raise HTTPException(500, str(e))


# ── Clinic Admin: My Balance ─────────────────────────────────────────────────

@router.get("/credits/my-balance")
async def my_balance(tenant_id: str) -> dict:
    """Get credit balance for a specific clinic (clinic admin view)."""
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


# ── Super Admin: Initialize Credits for All Clinics ──────────────────────────

@router.post("/credits/init-all")
async def init_all_credits() -> dict:
    """Create credit records for all clinics that don't have one."""
    try:
        from backend.models.tenant import Tenant

        async with async_session() as db:
            result = await db.execute(select(Tenant))
            tenants = result.scalars().all()

            created = 0
            for tenant in tenants:
                credits = await CreditService.get_or_create_balance(
                    db, str(tenant.id)
                )
                if credits.balance == 0.0 and credits.total_added == 0.0:
                    created += 1

            await db.commit()
            return {
                "total_clinics": len(tenants),
                "records_created": created,
            }
    except Exception as e:
        logger.exception("Error initializing credits: %s", e)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# DEBUG ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/debug/test-stt")
async def test_stt() -> dict:
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
async def test_tts() -> dict:
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
                    "speaker": "meera",
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
