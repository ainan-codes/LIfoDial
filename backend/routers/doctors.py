from datetime import date as date_cls, datetime, time as time_cls
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.tenant import Tenant

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────

class DoctorCreate(BaseModel):
    name: str
    specialization: str
    his_doctor_id: str | None = None
    is_available: bool = True
    # Set by the frontend's confirm dialog after a 409 duplicate-name response
    # — two real doctors CAN legitimately share a common name, so this isn't
    # a hard DB constraint, just a "are you sure?" the admin can override.
    allow_duplicate_name: bool = False

# NOTE: ids are str, not uuid.UUID — the DB columns are varchar(36) and
# comparing a Python UUID against them makes Postgres raise
# "operator does not exist: character varying = uuid".
class DoctorResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    specialization: str
    his_doctor_id: str | None
    is_available: bool
    leave_reason: str | None
    created_at: Any

    model_config = ConfigDict(from_attributes=True)

class DoctorUpdate(BaseModel):
    name: str | None = None
    specialization: str | None = None
    his_doctor_id: str | None = None
    is_available: bool | None = None
    leave_reason: str | None = None


class AvailabilityWindow(BaseModel):
    day_of_week: int    # 0=Monday .. 6=Sunday (matches date.weekday())
    start_time: str     # "HH:MM", IST wall-clock, 30-minute granularity
    end_time: str        # "HH:MM"

    @field_validator("day_of_week")
    @classmethod
    def _valid_day(cls, v: int) -> int:
        if not (0 <= v <= 6):
            raise ValueError("day_of_week must be 0 (Monday) through 6 (Sunday)")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        try:
            t = datetime.strptime(v.strip(), "%H:%M").time()
        except ValueError:
            raise ValueError(f"'{v}' is not a valid HH:MM time")
        if t.minute not in (0, 30):
            raise ValueError(f"'{v}' must fall on a 30-minute boundary (:00 or :30)")
        return v


class AvailabilityWindowResponse(AvailabilityWindow):
    id: str

# ── Endpoints ────────────────────────────────────────────────────────
# ALL operations MUST filter by tenant_id (multi-tenant rule)

@router.post("/tenants/{tenant_id}/doctors", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def add_doctor(tenant_id: str, payload: DoctorCreate, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    # Verify tenant exists
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Duplicate-name guard: nothing previously stopped a re-add (after a
    # missed edit, a lost editTarget, or a retry) from silently creating a
    # second row for the same doctor — this is the actual bug behind the
    # "Dr. Salman / HIS 002" x3 duplicate report. Free-text names can't be a
    # hard DB constraint (two real doctors can share a common name), so this
    # is an app-level "are you sure?" the admin explicitly overrides.
    if not payload.allow_duplicate_name:
        existing = await db.scalar(
            select(Doctor).where(
                Doctor.tenant_id == tenant_id,
                func.lower(func.trim(Doctor.name)) == payload.name.strip().lower(),
            )
        )
        if existing:
            # Plain string detail (not a nested object) — the frontend's
            # shared fetchWithAuth helper does `new Error(error.detail)`,
            # which would coerce an object to the useless "[object Object]".
            # The frontend's primary duplicate-name UX is a client-side check
            # against the doctor list it already has loaded (no round trip
            # needed); this 409 is the authoritative backstop for a race
            # (e.g. two admin tabs adding the same name at once) and only
            # ever needs to be readable as a plain error banner in that case.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A doctor named '{existing.name}' already exists.",
            )

    doctor = Doctor(
        tenant_id=tenant_id,
        name=payload.name,
        specialization=payload.specialization,
        his_doctor_id=payload.his_doctor_id,
        is_available=payload.is_available,
    )
    db.add(doctor)
    try:
        await db.commit()
    except IntegrityError:
        # his_doctor_id, when set, is unique per clinic
        # (uq_doctors_tenant_his_id) — this is the DB-level backstop for the
        # same duplicate-doctor bug when a HIS id collides.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A doctor with HIS id '{payload.his_doctor_id}' already exists for this clinic.",
        )
    await db.refresh(doctor)

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)
    return doctor

@router.get("/tenants/{tenant_id}/doctors", response_model=list[DoctorResponse])
async def list_doctors(tenant_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    result = await db.execute(
        select(Doctor).where(Doctor.tenant_id == tenant_id)
    )
    doctors = result.scalars().all()
    return list(doctors)

@router.patch("/tenants/{tenant_id}/doctors/{doctor_id}", response_model=DoctorResponse)
async def update_doctor(
    tenant_id: str, doctor_id: str, payload: DoctorUpdate,
    user: CurrentUser = None, db: AsyncSession = Depends(get_db)
):
    """Update a doctor's profile and/or availability. Clinic admins and
    superadmin can both call this — CurrentUser.require_owns() already lets a
    superadmin token through for any tenant_id."""
    user.require_owns(tenant_id)
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id)
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    if payload.name is not None:
        doctor.name = payload.name
    if payload.specialization is not None:
        doctor.specialization = payload.specialization
    if payload.his_doctor_id is not None:
        doctor.his_doctor_id = payload.his_doctor_id
    if payload.is_available is not None:
        doctor.is_available = payload.is_available
        doctor.leave_reason = payload.leave_reason if not payload.is_available else None
    elif payload.leave_reason is not None:
        doctor.leave_reason = payload.leave_reason if not doctor.is_available else None

    try:
        await db.commit()
    except IntegrityError:
        # his_doctor_id, when set, is unique per clinic (uq_doctors_tenant_his_id).
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A doctor with HIS id '{payload.his_doctor_id}' already exists for this clinic.",
        )
    await db.refresh(doctor)

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)
    return doctor

@router.delete("/tenants/{tenant_id}/doctors/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doctor(tenant_id: str, doctor_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    result = await db.execute(
        select(Doctor).where(
            Doctor.id == doctor_id,
            Doctor.tenant_id == tenant_id
        )
    )
    doctor = result.scalar_one_or_none()

    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Delete this doctor's appointments first — Appointment.doctor_id is
    # NOT NULL, so the DB can't satisfy its own ON DELETE SET NULL rule and
    # would otherwise raise an IntegrityError for any doctor with a booking.
    from backend.models.appointment import Appointment
    from sqlalchemy import delete as sa_delete
    await db.execute(
        sa_delete(Appointment).where(
            Appointment.doctor_id == doctor_id, Appointment.tenant_id == tenant_id,
        )
    )

    await db.delete(doctor)
    await db.commit()

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)
    return None


# ── Weekly availability ──────────────────────────────────────────────

@router.get(
    "/tenants/{tenant_id}/doctors/{doctor_id}/availability",
    response_model=list[AvailabilityWindowResponse],
)
async def get_doctor_availability(
    tenant_id: str, doctor_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db),
):
    user.require_owns(tenant_id)
    doctor = await db.scalar(select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id))
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    rows = (
        await db.execute(
            select(DoctorAvailability)
            .where(DoctorAvailability.doctor_id == doctor_id)
            .order_by(DoctorAvailability.day_of_week, DoctorAvailability.start_time)
        )
    ).scalars().all()
    return [
        AvailabilityWindowResponse(
            id=r.id, day_of_week=r.day_of_week,
            start_time=r.start_time.strftime("%H:%M"), end_time=r.end_time.strftime("%H:%M"),
        )
        for r in rows
    ]


@router.put(
    "/tenants/{tenant_id}/doctors/{doctor_id}/availability",
    response_model=list[AvailabilityWindowResponse],
)
async def set_doctor_availability(
    tenant_id: str, doctor_id: str, payload: list[AvailabilityWindow],
    user: CurrentUser = None, db: AsyncSession = Depends(get_db),
):
    """Full-replace: the whole submitted set is validated BEFORE anything is
    written, so an invalid submission leaves the doctor's previous schedule
    untouched rather than half-overwritten."""
    user.require_owns(tenant_id)
    doctor = await db.scalar(select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id))
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    parsed: list[tuple[int, time_cls, time_cls]] = []
    for w in payload:
        start = datetime.strptime(w.start_time, "%H:%M").time()
        end = datetime.strptime(w.end_time, "%H:%M").time()
        if start >= end:
            raise HTTPException(
                status_code=422,
                detail=f"start_time {w.start_time} must be before end_time {w.end_time} (day {w.day_of_week})",
            )
        parsed.append((w.day_of_week, start, end))

    by_day: dict[int, list[tuple[time_cls, time_cls]]] = {}
    for day, start, end in parsed:
        by_day.setdefault(day, []).append((start, end))
    for day, windows in by_day.items():
        windows.sort()
        for (s1, e1), (s2, e2) in zip(windows, windows[1:]):
            if s2 < e1:
                raise HTTPException(
                    status_code=422,
                    detail=f"Overlapping availability windows on day {day}: {s1}-{e1} and {s2}-{e2}",
                )

    await db.execute(sa_delete(DoctorAvailability).where(DoctorAvailability.doctor_id == doctor_id))
    new_rows = [
        DoctorAvailability(tenant_id=tenant_id, doctor_id=doctor_id, day_of_week=day, start_time=start, end_time=end)
        for day, start, end in parsed
    ]
    db.add_all(new_rows)
    await db.commit()
    for r in new_rows:
        await db.refresh(r)

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)

    return [
        AvailabilityWindowResponse(
            id=r.id, day_of_week=r.day_of_week,
            start_time=r.start_time.strftime("%H:%M"), end_time=r.end_time.strftime("%H:%M"),
        )
        for r in new_rows
    ]


@router.get("/tenants/{tenant_id}/doctors/{doctor_id}/available-slots", response_model=list[str])
async def get_available_slots(
    tenant_id: str, doctor_id: str, date: str,
    user: CurrentUser = None, db: AsyncSession = Depends(get_db),
):
    """date=YYYY-MM-DD, IST calendar date. Thin wrapper over
    availability.compute_available_slots — lets a clinic admin see real
    slots and gives a fast manual-QA path independent of a live voice call."""
    user.require_owns(tenant_id)
    doctor = await db.scalar(select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id))
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    try:
        target_date = date_cls.fromisoformat(date.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")

    from backend.services.availability import compute_available_slots
    from backend.services.timeutil import format_ist_clock, to_ist

    slots_utc = await compute_available_slots(tenant_id, doctor_id, target_date)
    return [format_ist_clock(to_ist(s)) for s in slots_utc]
