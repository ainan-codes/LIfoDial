"""
Run this after deleting lifodial.db to populate
test data for development.
python -m backend.scripts.seed_demo
"""
import asyncio
import uuid
from backend.db import AsyncSessionLocal, init_db
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.agent_config import AgentConfig


DEMO_CLINICS = [
    {
        "id": "tenant-001",
        "clinic_name": "Apollo Multispeciality Mumbai",
        "language": "hi-IN",
        "location": "Mumbai, Maharashtra",
        "admin_email": "admin@apollo.lifodial.com",
        "ai_number": "+91 90001 23456",
        "agent": {
            "id": "agent-001",
            "agent_name": "Receptionist",
            "template": "clinic_receptionist",
            "first_message": "Namaste! Apollo Clinic mein aapka swagat hai. Main aapki receptionist hoon. Aaj main aapki kaise madad kar sakti hoon?",
            "system_prompt": "You are the AI receptionist for Apollo Multispeciality Mumbai. Help patients book appointments. Keep responses under 2 sentences. Available doctors: Dr. Suresh Menon (Cardiology), Dr. Ananya Rao (General), Dr. Priya Nair (Dermatology). If patient says emergency words like 'heart attack', 'accident', 'unconscious' - say you are transferring immediately.",
            "stt_provider": "sarvam",
            "stt_model": "saarika:v2",
            "stt_language": "hi-IN",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "meera",
            "tts_language": "hi-IN",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.0-flash",
            "status": "ACTIVE",
        },
        "doctors": [
            {"name": "Dr. Suresh Menon", "specialization": "Cardiology"},
            {"name": "Dr. Ananya Rao", "specialization": "General Physician"},
            {"name": "Dr. Priya Nair", "specialization": "Dermatology"},
        ]
    },
    {
        "id": "tenant-002",
        "clinic_name": "Aster Medicity Kochi",
        "language": "ml-IN",
        "location": "Kochi, Kerala",
        "admin_email": "admin@aster.lifodial.com",
        "ai_number": "+91 90001 34567",
        "agent": {
            "id": "agent-002",
            "agent_name": "Receptionist",
            "template": "clinic_receptionist",
            "first_message": "നമസ്കാരം! Aster Medicity-ലേക്ക് സ്വാഗതം. ഞാൻ എങ്ങനെ സഹായിക്കാം?",
            "system_prompt": "You are the AI receptionist for Aster Medicity Kochi. Respond in Malayalam primarily, switch to English if patient speaks English. Help book appointments. Keep responses under 2 sentences. Available: Dr. Meena Iyer (Gynaecology), Dr. Vikram Shah (Orthopaedic).",
            "stt_provider": "sarvam",
            "stt_model": "saarika:v2",
            "stt_language": "ml-IN",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "pavithra",
            "tts_language": "ml-IN",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.0-flash",
            "status": "ACTIVE",
        },
        "doctors": [
            {"name": "Dr. Meena Iyer", "specialization": "Gynaecology"},
            {"name": "Dr. Vikram Shah", "specialization": "Orthopaedic"},
        ]
    },
    {
        "id": "tenant-005",
        "clinic_name": "Al Zahra Hospital Dubai",
        "language": "ar-SA",
        "location": "Dubai, UAE",
        "admin_email": "admin@alzahra.lifodial.com",
        "ai_number": "+971 50001 12345",
        "agent": {
            "id": "agent-003",
            "agent_name": "Receptionist",
            "template": "clinic_receptionist",
            "first_message": "مرحباً! أهلاً وسهلاً بك في مستشفى الزهراء. كيف يمكنني مساعدتك؟",
            "system_prompt": "You are the AI receptionist for Al Zahra Hospital Dubai. Respond in Arabic primarily, switch to English if needed. Help book appointments. Keep responses under 2 sentences. Available: Dr. Ahmed Al Rashidi (General).",
            "stt_provider": "sarvam",
            "stt_model": "saarika:v2",
            "stt_language": "ar-SA",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "amol",
            "tts_language": "ar-SA",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.0-flash",
            "status": "CONFIGURED",
        },
        "doctors": [
            {"name": "Dr. Ahmed Al Rashidi", "specialization": "General"},
        ]
    },
]


async def seed():
    await init_db()
    
    async with AsyncSessionLocal() as db:
        for clinic_data in DEMO_CLINICS:
            # Check if tenant exists
            from sqlalchemy import select
            existing = await db.execute(
                select(Tenant).where(
                    Tenant.id == clinic_data["id"]
                )
            )
            if existing.scalar_one_or_none():
                print(f"⏭️  {clinic_data['clinic_name']} already exists, skipping")
                continue
            
            # Create Tenant
            tenant = Tenant(
                id=clinic_data["id"],
                clinic_name=clinic_data["clinic_name"],
                language=clinic_data["language"],
                ai_number=clinic_data["ai_number"],
                admin_email=clinic_data.get("admin_email"),
            )
            db.add(tenant)
            
            # Create Doctors
            for doc in clinic_data["doctors"]:
                doctor = Doctor(
                    id=str(uuid.uuid4()),
                    tenant_id=clinic_data["id"],
                    name=doc["name"],
                    specialization=doc["specialization"],
                )
                db.add(doctor)
            
            # Create AgentConfig
            agent_data = clinic_data["agent"]
            agent = AgentConfig(
                id=agent_data["id"],
                tenant_id=clinic_data["id"],
                agent_name=agent_data["agent_name"],
                template=agent_data["template"],
                first_message=agent_data["first_message"],
                system_prompt=agent_data["system_prompt"],
                stt_provider=agent_data["stt_provider"],
                stt_model=agent_data["stt_model"],
                stt_language=agent_data["stt_language"],
                tts_provider=agent_data["tts_provider"],
                tts_model=agent_data["tts_model"],
                tts_voice=agent_data["tts_voice"],
                tts_language=agent_data["tts_language"],
                llm_provider=agent_data["llm_provider"],
                llm_model=agent_data["llm_model"],
                status=agent_data["status"],
            )
            db.add(agent)
            
            print(f"✅ Created: {clinic_data['clinic_name']}")
        
        await db.commit()
        print("\n✅ Seed complete! Database ready.")
        print("\nTest these agents:")
        print("  agent-001 → Apollo Mumbai (Hindi)")
        print("  agent-002 → Aster Kochi (Malayalam)")
        print("  agent-003 → Al Zahra Dubai (Arabic)")


if __name__ == "__main__":
    asyncio.run(seed())
