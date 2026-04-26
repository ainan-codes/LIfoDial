import random
import string
import uuid
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, text
from backend.db import AsyncSessionLocal
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment
from backend.models.onboarding_request import OnboardingRequest
from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime

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
async def create_clinic(data: ClinicCreate, db: AsyncSession = Depends(get_db)):
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
            admin_password=gen_pass,
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
async def list_clinics(db: AsyncSession = Depends(get_db)):
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

@router.patch("/clinics/{tenant_id}/status")
async def update_clinic_status(tenant_id: str, data: StatusUpdate, db: AsyncSession = Depends(get_db)):
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
async def delete_clinic(tenant_id: str, db: AsyncSession = Depends(get_db)):
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
async def get_onboarding_request(req_id: str, db: AsyncSession = Depends(get_db)):
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
async def approve_onboarding_request(req_id: str, db: AsyncSession = Depends(get_db)):
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
            admin_password=gen_pass,
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
async def reject_onboarding_request(req_id: str, body: RejectBody, db: AsyncSession = Depends(get_db)):
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
async def system_health_status(db: AsyncSession = Depends(get_db)):
    """Real health check for all services — reads actual env vars."""
    from backend.config import settings
    from backend.db import IS_SQLITE
    results: dict = {}

    # ── Database ping ────────────────────────────────────────────────────────
    try:
        t0 = time.monotonic()
        await db.execute(text("SELECT 1"))
        db_latency = round((time.monotonic() - t0) * 1000, 1)
        db_type = "SQLite" if IS_SQLITE else "PostgreSQL"
        results["database"] = {
            "status": "healthy",
            "latency_ms": db_latency,
            "type": db_type,
        }
    except Exception as e:
        results["database"] = {"status": "error", "error": str(e)[:120]}

    # ── Count records ────────────────────────────────────────────────────────
    try:
        tenant_count = (await db.execute(text("SELECT COUNT(*) FROM tenants"))).scalar()
        appt_count   = (await db.execute(text("SELECT COUNT(*) FROM appointments"))).scalar()
        results["database"]["tenant_count"]      = tenant_count
        results["database"]["appointment_count"] = appt_count
    except Exception:
        pass

    # ── API key presence checks ──────────────────────────────────────────────
    def _key_status(value: str) -> str:
        return "connected" if value and value.strip() else "missing_key"

    results["gemini"]    = _key_status(settings.gemini_api_key)
    results["sarvam"]    = _key_status(settings.sarvam_api_key)
    results["livekit"]   = _key_status(settings.livekit_api_key) if (settings.livekit_url and settings.livekit_api_key) else "missing_key"
    results["vobiz"]     = _key_status(settings.vobiz_account_sid)
    results["oxzygen"]   = _key_status(settings.oxzygen_api_key)
    results["groq"]      = _key_status(settings.groq_api_key)
    results["elevenlabs"] = _key_status(settings.elevenlabs_api_key)

    results["environment"] = settings.environment
    results["timestamp"]   = datetime.utcnow().isoformat()
    return results
