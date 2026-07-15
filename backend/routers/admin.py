import random
import string
import uuid
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text, func, case
from backend.auth import SuperAdmin
from backend.db import AsyncSessionLocal
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment
from backend.models.onboarding_request import OnboardingRequest
from backend.security import hash_password
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

router = APIRouter()

# ── Dependencies ───────────────────────────────────────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# ── Schemas ───────────────────────────────────────────────────────────────────
class ClinicCreate(BaseModel):
    clinic_name: str
    admin_name: str
    admin_email: str
    location: str
    language: str

class ClinicResponse(BaseModel):
    id: str
    clinic_name: str
    ai_number: Optional[str] = None
    is_active: bool
    language: str
    location: Optional[str] = None
    created_at: datetime
    admin_email: Optional[str] = None
    # Stats — not stored in Tenant yet; returned as 0 until a stats table exists
    plan: str = "Free"
    calls_month: int = 0
    bookings: int = 0
    res_rate: str = "—"
    avg_latency: str = "—"
    model_id: str = "m1"

    model_config = ConfigDict(from_attributes=True)

class StatusUpdate(BaseModel):
    is_active: bool

class OnboardingCreate(BaseModel):
    clinic_name: str
    contact_name: str
    email: str
    phone: str
    plan: Optional[str] = "Pro"
    location: Optional[str] = None
    message: Optional[str] = None

class OnboardingResponse(BaseModel):
    id: str
    clinic_name: str
    contact_name: str
    email: str
    phone: str
    plan: str
    location: Optional[str]
    message: Optional[str]
    status: str
    note: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class RejectBody(BaseModel):
    reason: str

# ── Helpers ─────────────────────────────────────────────────────────────────────
def generate_password(length=8):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choice(chars) for _ in range(length))

def generate_ai_number():
    return f"+91 9000{random.randint(100000, 999999)}"

# ── Clinic Routes ────────────────────────────────────────────────────────────────
@router.post("/clinics")
async def create_clinic(data: ClinicCreate, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        slug = data.clinic_name.lower().replace(" ", "")
        gen_pass = generate_password()
        ai_num = generate_ai_number()
        
        new_tenant = Tenant(
            clinic_name=data.clinic_name,
            admin_name=data.admin_name,
            admin_email=data.admin_email,
            location=data.location,
            language=data.language,
            ai_number=ai_num,
            admin_password=hash_password(gen_pass),
            is_active=True
        )
        
        db.add(new_tenant)
        await db.flush()
        
        default_doctors = [
            Doctor(tenant_id=new_tenant.id, name="Dr. Sharma", specialization="General Physician"),
            Doctor(tenant_id=new_tenant.id, name="Dr. Reddy", specialization="Pediatrician"),
            Doctor(tenant_id=new_tenant.id, name="Dr. Kapoor", specialization="Dermatologist")
        ]
        db.add_all(default_doctors)
        await db.commit()
        
        return {
            "tenant_id": new_tenant.id,
            "ai_number": ai_num,
            "login_credentials": {
                "email": f"admin@{slug}.lifodial.com",
                "password": gen_pass
            }
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/clinics")
async def list_clinics(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        from sqlalchemy import select
        from backend.models.tenant import Tenant
        
        result = await db.execute(
            select(Tenant).order_by(Tenant.clinic_name)
        )
        tenants = result.scalars().all()
        
        return {
            "clinics": [
                {
                    "id": str(t.id),
                    "clinic_name": t.clinic_name,
                    "admin_email": getattr(t, 'admin_email', ''),
                    "ai_number": getattr(t, 'ai_number', ''),
                    "language": getattr(t, 'language', 'hi-IN'),
                    "plan": getattr(t, 'plan', 'free'),
                    "status": getattr(t, 'status', 'ACTIVE'),
                    "is_active": getattr(t, 'is_active', True),
                    "created_at": str(t.created_at) if t.created_at else None,
                }
                for t in tenants
            ],
            "total": len(tenants)
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"list_clinics error: {e}")
        # Return empty instead of 500
        return {"clinics": [], "total": 0, "error": str(e)[:100]}

# Platform's operating timezone for calendar-day bucketing (target market is India).
# Tenants don't carry their own timezone field yet, so this is a single global
# bucket rather than per-clinic-local — good enough while the business is India-only.
_PLATFORM_TZ = ZoneInfo("Asia/Kolkata")

# A call stuck in "in_progress" past this age is treated as crashed/orphaned
# (pipeline died without finalizing it), not actually live.
_STALE_CALL_MINUTES = 30


@router.get("/overview")
async def platform_overview(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """
    Real aggregate stats for the superadmin Platform Overview page.
    Every number below comes from a live query — nothing here is mocked.

    Definitions:
    - "Active this month" = tenant has >=1 call_record in the trailing 30 days
      (rolling window from now, not calendar-month-to-date).
    - Per-clinic "calls" / "bookings" use that same trailing-30-day window.
    - Platform-wide "Total Calls" / "Total Bookings" are all-time counts.
    - The 7-day call volume chart buckets by CALENDAR DAY in IST (Asia/Kolkata),
      not naive UTC — otherwise late-night IST calls (UTC+5:30) would land in
      the wrong day's bar.
    - MRR is hardcoded to 0: there is no billing/subscription/pricing table in
      the DB (Tenant.plan is just a label; the `stripe` package in
      requirements.txt is unused, and plan prices only exist as a static mock
      in the frontend store). Wire this up once a real Billing model exists —
      don't fabricate a number from the frontend's mock price table.
    """
    from backend.models.call_record import CallRecord

    now_utc = datetime.now(timezone.utc)
    cutoff_30d = now_utc - timedelta(days=30)
    cutoff_7d = now_utc - timedelta(days=7)
    stale_call_cutoff = now_utc - timedelta(minutes=_STALE_CALL_MINUTES)

    tenants = (await db.execute(select(Tenant))).scalars().all()
    total_clinics = len(tenants)

    # Per-tenant call stats for the trailing 30 days — one grouped pass.
    calls_stmt = (
        select(
            CallRecord.tenant_id,
            func.count(CallRecord.id).label("calls"),
            func.avg(CallRecord.avg_latency_ms).label("avg_latency"),
            func.sum(
                case((CallRecord.outcome.in_(["booked", "resolved"]), 1), else_=0)
            ).label("resolved"),
        )
        .where(CallRecord.created_at >= cutoff_30d)
        .group_by(CallRecord.tenant_id)
    )
    calls_by_tenant = {r.tenant_id: r for r in (await db.execute(calls_stmt)).all()}

    # Per-tenant booking stats for the trailing 30 days (cancelled doesn't count).
    bookings_stmt = (
        select(Appointment.tenant_id, func.count(Appointment.id).label("bookings"))
        .where(Appointment.created_at >= cutoff_30d, Appointment.status != "cancelled")
        .group_by(Appointment.tenant_id)
    )
    bookings_by_tenant = {
        r.tenant_id: r.bookings for r in (await db.execute(bookings_stmt)).all()
    }

    # Platform-wide all-time totals.
    total_calls = (await db.execute(select(func.count(CallRecord.id)))).scalar() or 0
    total_bookings = (
        await db.execute(
            select(func.count(Appointment.id)).where(Appointment.status != "cancelled")
        )
    ).scalar() or 0
    active_calls = (
        await db.execute(
            select(func.count(CallRecord.id)).where(
                CallRecord.status == "in_progress",
                CallRecord.started_at >= stale_call_cutoff,
            )
        )
    ).scalar() or 0

    active_this_month = len(calls_by_tenant)

    # Per-clinic view for the two tables. Tenants with no rows in the grouped
    # queries above (e.g. brand new, or no calls yet) safely default to zero —
    # no join means a tenant deleted mid-request can't break this either.
    clinics_view = []
    for t in tenants:
        stats = calls_by_tenant.get(t.id)
        calls_month = stats.calls if stats else 0
        resolved = int(stats.resolved) if (stats and stats.resolved) else 0
        avg_latency = stats.avg_latency if (stats and stats.avg_latency is not None) else None
        clinics_view.append({
            "id": t.id,
            "clinic_name": t.clinic_name,
            "location": t.location,
            "plan": t.plan,
            "status": "Active" if t.is_active else "Suspended",
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "calls_month": calls_month,
            "bookings": bookings_by_tenant.get(t.id, 0),
            "res_rate": f"{round(resolved / calls_month * 100)}%" if calls_month else "—",
            "avg_latency": f"{round(avg_latency)}ms" if avg_latency is not None else "—",
        })

    recently_onboarded = sorted(
        clinics_view, key=lambda c: c["created_at"] or "", reverse=True
    )[:5]
    top_performing = sorted(
        [c for c in clinics_view if c["status"] == "Active" and c["calls_month"] > 0],
        key=lambda c: c["calls_month"],
        reverse=True,
    )[:5]

    # 7-day call volume, bucketed by real IST calendar day (see docstring).
    call_timestamps = (
        (await db.execute(
            select(CallRecord.created_at).where(CallRecord.created_at >= cutoff_7d)
        )).scalars().all()
    )
    today_ist = now_utc.astimezone(_PLATFORM_TZ).date()
    day_buckets = {today_ist - timedelta(days=i): 0 for i in range(6, -1, -1)}
    for ts in call_timestamps:
        if ts is None:
            continue
        ts_aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        day = ts_aware.astimezone(_PLATFORM_TZ).date()
        if day in day_buckets:
            day_buckets[day] += 1
    call_volume_7d = [
        {"date": d.isoformat(), "day_label": d.strftime("%a"), "count": c}
        for d, c in sorted(day_buckets.items())
    ]

    return {
        "total_clinics": total_clinics,
        "active_this_month": active_this_month,
        "total_calls": total_calls,
        "total_bookings": total_bookings,
        "mrr": 0,
        "active_calls": active_calls,
        "recently_onboarded": recently_onboarded,
        "top_performing": top_performing,
        "call_volume_7d": call_volume_7d,
    }


@router.patch("/clinics/{tenant_id}/status")
async def update_clinic_status(tenant_id: str, data: StatusUpdate, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(is_active=data.is_active)
        )
        await db.commit()
        return {"status": "updated", "is_active": data.is_active}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clinics/{tenant_id}", status_code=204)
async def delete_clinic(tenant_id: str, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """Permanently delete a clinic and all its agents."""
    try:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Clinic not found")

        # Cascade delete agents
        from backend.models.agent_config import AgentConfig
        from sqlalchemy import delete as sa_delete
        await db.execute(sa_delete(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        await db.execute(sa_delete(Doctor).where(Doctor.tenant_id == tenant.id))

        await db.delete(tenant)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── Onboarding Request Routes ────────────────────────────────────────────────
@router.post("/onboarding-requests", response_model=OnboardingResponse)
async def create_onboarding_request(data: OnboardingCreate, db: AsyncSession = Depends(get_db)):
    """Called from the landing page 'Contact Sales' form."""
    try:
        req = OnboardingRequest(
            id=str(uuid.uuid4()),
            clinic_name=data.clinic_name,
            contact_name=data.contact_name,
            email=data.email,
            phone=data.phone,
            plan=data.plan or "Pro",
            location=data.location,
            message=data.message,
            status="Pending",
        )
        db.add(req)
        await db.commit()
        await db.refresh(req)
        return req
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/onboarding-requests", response_model=List[OnboardingResponse])
async def list_onboarding_requests(
    status: Optional[str] = None,
    user: SuperAdmin = None,
    db: AsyncSession = Depends(get_db)
):
    try:
        stmt = select(OnboardingRequest).order_by(OnboardingRequest.created_at.desc())
        if status:
            stmt = stmt.where(OnboardingRequest.status == status)
        result = await db.execute(stmt)
        return result.scalars().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/onboarding-requests/{req_id}", response_model=OnboardingResponse)
async def get_onboarding_request(req_id: str, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(
            select(OnboardingRequest).where(OnboardingRequest.id == req_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        return req
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/onboarding-requests/{req_id}/approve")
async def approve_onboarding_request(req_id: str, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """Approve request and auto-create the clinic tenant."""
    try:
        result = await db.execute(
            select(OnboardingRequest).where(OnboardingRequest.id == req_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        if req.status != "Pending":
            raise HTTPException(status_code=400, detail=f"Request is already {req.status}")

        # Create the clinic tenant
        slug = req.clinic_name.lower().replace(" ", "")
        gen_pass = generate_password()
        ai_num = generate_ai_number()
        
        new_tenant = Tenant(
            clinic_name=req.clinic_name,
            admin_name=req.contact_name,
            admin_email=req.email,
            location=req.location or "",
            language="en",
            ai_number=ai_num,
            admin_password=hash_password(gen_pass),
            is_active=True
        )
        db.add(new_tenant)
        await db.flush()

        # Default doctors
        db.add_all([
            Doctor(tenant_id=new_tenant.id, name="Dr. Sharma", specialization="General Physician"),
            Doctor(tenant_id=new_tenant.id, name="Dr. Reddy", specialization="Pediatrician"),
        ])

        # Mark request approved
        req.status = "Approved"
        req.note = f"Approved. Tenant ID: {new_tenant.id}"
        req.updated_at = datetime.utcnow()

        await db.commit()
        return {
            "status": "approved",
            "tenant_id": new_tenant.id,
            "credentials": {
                "email": f"admin@{slug}.lifodial.com",
                "password": gen_pass,
                "ai_number": ai_num,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/onboarding-requests/{req_id}/reject")
async def reject_onboarding_request(req_id: str, body: RejectBody, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(
            select(OnboardingRequest).where(OnboardingRequest.id == req_id)
        )
        req = result.scalar_one_or_none()
        if not req:
            raise HTTPException(status_code=404, detail="Request not found")
        
        req.status = "Rejected"
        req.note = body.reason
        req.updated_at = datetime.utcnow()
        await db.commit()
        return {"status": "rejected"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── Global Appointments View ─────────────────────────────────────────────────
@router.get("/appointments")
async def list_all_appointments(
    status: Optional[str] = None,
    clinic_id: Optional[str] = None,
    user: SuperAdmin = None,
    db: AsyncSession = Depends(get_db)
):
    """Super admin view of ALL appointments across all clinics."""
    try:
        stmt = select(Appointment, Tenant).join(
            Tenant, Appointment.tenant_id == Tenant.id
        ).order_by(Appointment.slot_time.desc())

        if status:
            stmt = stmt.where(Appointment.status == status)
        if clinic_id:
            stmt = stmt.where(Appointment.tenant_id == clinic_id)

        result = await db.execute(stmt)
        rows = result.all()

        return [
            {
                "id": str(apt.id),
                "patient_name": f"Patient {str(apt.patient_phone)[-4:]}",  # privacy
                "patient_phone": (apt.patient_phone[:-4] + "****") if len(apt.patient_phone or "") > 4 else "****",
                "clinic_name": tenant.clinic_name,
                "doctor_id": str(apt.doctor_id),
                "doctor_name": "—",  # would need join on Doctor
                "slot_time": apt.slot_time.isoformat(),
                "status": apt.status,
                "channel": "AI Call",
            }
            for apt, tenant in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── System Health ────────────────────────────────────────────────────────────
@router.get("/health-status")
async def system_health_status(user: SuperAdmin = None):
    """
    Real health check for all services. Every number here is measured, not
    simulated — DB latency is a warm round-trip, tenant/appointment counts come
    from a dedicated clean session (so a failure is reported, never silently
    rendered as 0), and LiveKit is a real API round-trip, not an env-var check.
    """
    import asyncio
    from backend.config import settings
    from backend.db import IS_SQLITE, DATABASE_URL, AsyncSessionLocal
    results: dict = {}

    def _mask_db_url(url: str) -> str:
        try:
            if "@" not in url:
                return url.split("://", 1)[-1][:60]
            scheme, rest = url.split("://", 1)
            creds, host = rest.split("@", 1)
            u = creds.split(":", 1)[0]
            return f"{scheme}://{u}:***@{host}"
        except Exception:
            return "unparseable"

    masked_db = _mask_db_url(DATABASE_URL) if DATABASE_URL else "<empty>"

    # ── Database: warm latency + reliable counts in one clean session ─────────
    # Use a dedicated session (not the request-scoped one) so a poisoned
    # transaction elsewhere can never wipe the counts. With NullPool the first
    # query pays the TCP+TLS connection cost; we time the SECOND query so the
    # reported latency reflects real query round-trip, not connection setup
    # (this is why the old number read ~1600ms — it was timing a cold connect).
    try:
        async with AsyncSessionLocal() as hdb:
            await hdb.execute(text("SELECT 1"))            # cold connect — not timed
            t0 = time.monotonic()
            await hdb.execute(text("SELECT 1"))            # warm query — timed
            db_latency = round((time.monotonic() - t0) * 1000, 1)
            tenant_count = (await hdb.execute(text("SELECT COUNT(*) FROM tenants"))).scalar()
            appt_count = (await hdb.execute(text("SELECT COUNT(*) FROM appointments"))).scalar()
        results["database"] = {
            "status": "healthy",
            "latency_ms": db_latency,
            "type": "SQLite" if IS_SQLITE else "PostgreSQL",
            "host": masked_db,
            "tenant_count": tenant_count,
            "appointment_count": appt_count,
        }
    except Exception as e:
        # Report the failure explicitly instead of silently showing 0 tenants —
        # that silent-zero was the root cause of "0 tenants vs 8 clinics".
        results["database"] = {
            "status": "error",
            "error": str(e)[:200],
            "host": masked_db,
            "hint": (
                "Supabase pooler requires username 'postgres.<project_ref>'. "
                "Use the Session Pooler connection string."
            ) if "Tenant or user not found" in str(e) else None,
        }

    # ── Session store: honest about what's actually running ───────────────────
    # redis_client.py is an in-process dict, not a Redis connection (the
    # redis_url setting is currently unused). Report that truthfully rather
    # than the old hardcoded "healthy / <1ms" card.
    try:
        from backend import redis_client as _rc
        backend_kind = getattr(_rc, "BACKEND", "in-memory")
    except Exception:
        backend_kind = "in-memory"
    results["session_store"] = {
        "type": backend_kind,
        "connected": True,
        "note": "In-process store (no Redis wired). Session state is per-worker and lost on restart."
                if backend_kind == "in-memory" else "Redis connected.",
    }

    # ── LiveKit: real connectivity check, not just an env-var presence test ───
    livekit_status = "missing_key"
    livekit_detail = "Set LIVEKIT_URL + LIVEKIT_API_KEY + LIVEKIT_API_SECRET"
    if settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret:
        livekit_status = "auth_failed"
        livekit_detail = "Keys present but LiveKit did not respond / rejected them"
        try:
            from livekit import api as _lk
            _client = _lk.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret)
            await asyncio.wait_for(_client.room.list_rooms(_lk.ListRoomsRequest()), timeout=6)
            await _client.aclose()
            livekit_status = "connected"
            livekit_detail = "Live API reachable, credentials valid ✓"
        except Exception as e:
            livekit_detail = f"Keys present but check failed: {str(e)[:80]}"
    results["livekit"] = livekit_status
    results["livekit_detail"] = livekit_detail

    # ── Provider reachability: a REAL round-trip, not a presence check ────────
    # A key that merely exists tells you nothing — Google will revoke a leaked
    # key and it then 403s at call time. So each provider with a key gets one
    # cheap list-models probe (run concurrently). A dead/revoked key surfaces as
    # "auth_failed" instead of a misleading green "connected".
    import httpx

    async def _probe(name: str, key: str, url: str, headers: dict) -> tuple[str, str, str]:
        if not (key and key.strip()):
            return name, "missing_key", "No key set"
        try:
            async with httpx.AsyncClient(timeout=8.0) as c:
                r = await c.get(url, headers=headers)
            if r.status_code < 400:
                return name, "connected", f"Live API reachable ✓ (HTTP {r.status_code})"
            if r.status_code in (401, 403):
                msg = ""
                try:
                    msg = (r.json().get("error", {}) or {}).get("message", "") if isinstance(r.json(), dict) else ""
                except Exception:
                    msg = r.text[:100]
                return name, "auth_failed", f"Key rejected (HTTP {r.status_code}) — likely revoked/leaked. {msg[:120]}"
            return name, "unreachable", f"Unexpected HTTP {r.status_code}"
        except Exception as e:
            return name, "unreachable", f"Check failed: {str(e)[:80]}"

    _probes = await asyncio.gather(
        _probe("gemini", settings.gemini_api_key,
               f"https://generativelanguage.googleapis.com/v1beta/models?key={settings.gemini_api_key}", {}),
        _probe("sarvam", settings.sarvam_api_key,
               "https://api.sarvam.ai/v1/models", {"api-subscription-key": settings.sarvam_api_key}),
        _probe("groq", settings.groq_api_key,
               "https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {settings.groq_api_key}"}),
        _probe("elevenlabs", settings.elevenlabs_api_key,
               "https://api.elevenlabs.io/v1/models", {"xi-api-key": settings.elevenlabs_api_key}),
    )
    for name, status, detail in _probes:
        results[name] = status
        results[f"{name}_detail"] = detail

    # No cheap unauthenticated probe for these — report key presence honestly.
    def _key_status(value: str) -> str:
        return "connected" if value and value.strip() else "missing_key"

    results["vobiz"] = _key_status(settings.vobiz_account_sid)
    results["oxzygen"] = _key_status(settings.oxzygen_api_key)
    # HIS/Oxzygen has no integration code behind it yet — surface that honestly.
    results["his_implemented"] = False

    results["environment"] = settings.environment
    results["timestamp"] = datetime.utcnow().isoformat()
    return results
