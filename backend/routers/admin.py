import random
import string
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, case
from backend.auth import SuperAdmin
from backend.db import AsyncSessionLocal
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment
from backend.security import hash_password
from pydantic import BaseModel, ConfigDict
from typing import Optional
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
        gen_pass = generate_password()
        ai_num = generate_ai_number()
        # Store the real admin email (normalised) — the value the admin actually
        # logs in with. Do NOT invent an admin@<slug>.lifodial.com address; that
        # synthetic email never matched the stored login (audit P2).
        admin_email = (data.admin_email or "").strip().lower() or None

        new_tenant = Tenant(
            clinic_name=data.clinic_name,
            admin_name=data.admin_name,
            admin_email=admin_email,
            location=data.location,
            language=data.language,
            ai_number=ai_num,
            admin_password=hash_password(gen_pass),
            is_active=True
        )

        db.add(new_tenant)
        await db.flush()

        # A new clinic starts with ZERO doctors. This used to seed three fake
        # ones ("Dr. Sharma"/"Dr. Reddy"/"Dr. Kapoor"), which were real rows in
        # the doctors table — so a clinic admin logging in for the first time saw
        # staff they never added, and Dashboard's "Add your first doctor" setup
        # step was already silently ticked off. The clinic's own doctors are
        # added through POST /tenants/{id}/doctors.
        await db.commit()

        return {
            "tenant_id": new_tenant.id,
            "ai_number": ai_num,
            "login_credentials": {
                # Echo the email actually stored — the value the admin logs in with.
                "email": new_tenant.admin_email,
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
    """Permanently delete a clinic and every row that references it.

    The ordered cascade lives in one place —
    backend/services/tenant_service.py::delete_tenant_cascade — because this
    endpoint and DELETE /tenants/{id} previously each carried their own
    hand-maintained copy of a 12-statement ordered delete, which is exactly the
    kind of duplication that drifts (and did: neither copy deleted embed_events).
    """
    from backend.services.tenant_service import delete_tenant_cascade

    try:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Clinic not found")

        await delete_tenant_cascade(db, tenant)
        await db.commit()
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
        # Outer-join Doctor so each row shows the real doctor name (was hardcoded
        # "—"). Outer (not inner) so an appointment whose doctor was removed still
        # lists rather than vanishing.
        stmt = select(Appointment, Tenant, Doctor).join(
            Tenant, Appointment.tenant_id == Tenant.id
        ).outerjoin(
            Doctor, Appointment.doctor_id == Doctor.id
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
                # Show the real captured patient name when present; otherwise fall
                # back to a privacy-masked label derived from the phone.
                "patient_name": (
                    apt.patient_name.strip()
                    if apt.patient_name and apt.patient_name.strip()
                    else f"Patient {str(apt.patient_phone)[-4:]}"
                ),
                "patient_phone": (apt.patient_phone[:-4] + "****") if len(apt.patient_phone or "") > 4 else "****",
                "clinic_name": tenant.clinic_name,
                "doctor_id": str(apt.doctor_id),
                "doctor_name": (doctor.name if doctor else "—"),
                "slot_time": apt.slot_time.isoformat(),
                "status": apt.status,
                "channel": "AI Call",
            }
            for apt, tenant, doctor in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

