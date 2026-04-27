import asyncio
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from backend.db import AsyncSessionLocal
from backend.models.tenant import Tenant
from backend.models.agent_config import AgentConfig
from backend.models.doctor import Doctor

async def main():
    async with AsyncSessionLocal() as db:
        # 1. Create Demo Tenant
        tenant_id = "demo-tenant-id"
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        
        if not tenant:
            tenant = Tenant(
                id=tenant_id,
                clinic_name="Lifodial Demo Clinic",
                admin_name="Demo Admin",
                admin_email="admin@lifodial.com",
                admin_password="password123", # Plain text for demo as per previous patterns
                phone="+919876543210",
                location="Bangalore, India",
                status="active",
                plan="Pro",
                is_active=True
            )
            db.add(tenant)
            print(f"Created tenant: {tenant.clinic_name}")

        # 2. Create Demo Doctor
        doctor_id = "demo-doctor-id"
        result = await db.execute(select(Doctor).where(Doctor.id == doctor_id))
        doctor = result.scalar_one_or_none()
        
        if not doctor:
            doctor = Doctor(
                id=doctor_id,
                tenant_id=tenant_id,
                name="Dr. Smith",
                specialization="General Physician"
            )
            db.add(doctor)
            print(f"Created doctor: {doctor.name}")

        # 3. Create Demo Agent Config
        agent_id = "demo-agent-id"
        result = await db.execute(select(AgentConfig).where(AgentConfig.tenant_id == tenant_id))
        agent = result.scalar_one_or_none()
        
        if not agent:
            agent = AgentConfig(
                id=agent_id,
                tenant_id=tenant_id,
                agent_name="Receptionist Maya",
                greeting="Hello, this is Lifodial Demo Clinic. How can I help you today?",
                prompt="You are a professional medical receptionist. You help patients book appointments with Dr. Smith.",
                provider="gemini",
                model="gemini-2.0-flash",
                voice_provider="sarvam",
                voice_id="meera",
                language="hi-IN",
                # Capabilities
                can_book_appointments=True,
                can_cancel_appointments=True,
                can_check_availability=True,
                auto_detect_language=True,
                record_calls=True
            )
            db.add(agent)
            print(f"Created agent: {agent.agent_name}")

        await db.commit()
        print("Seed complete.")

if __name__ == "__main__":
    asyncio.run(main())
