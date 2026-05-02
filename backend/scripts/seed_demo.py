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
        "admin_name": "Dr. Rajesh Sharma",
        "admin_email": "admin@apollo.lifodial.com",
        "ai_number": "+91 90001 23456",
        "credits": 500.0,
        "agent": {
            "id": "agent-001",
            "agent_name": "Priya",
            "template": "clinic_receptionist",
            "first_message": "Namaste! Apollo Clinic mein aapka swagat hai. Main Priya hoon, aapki AI receptionist. Aaj main aapki kaise madad kar sakti hoon?",
            "system_prompt": "You are Priya, the AI receptionist for Apollo Multispeciality Mumbai. Help patients book appointments. Keep responses under 2 sentences. Available doctors: Dr. Suresh Menon (Cardiology), Dr. Ananya Rao (General), Dr. Priya Nair (Dermatology). If patient says emergency words like 'heart attack', 'accident', 'unconscious' - say you are transferring immediately.",
            "stt_provider": "sarvam",
            "stt_model": "saaras:v3",
            "stt_language": "hi-IN",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "meera",
            "tts_language": "hi-IN",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
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
        "admin_name": "Dr. Meena Thomas",
        "admin_email": "admin@aster.lifodial.com",
        "ai_number": "+91 90001 34567",
        "credits": 750.0,
        "agent": {
            "id": "agent-002",
            "agent_name": "Kavya",
            "template": "clinic_receptionist",
            "first_message": "നമസ്കാരം! Aster Medicity-ലേക്ക് സ്വാഗതം. ഞാൻ Kavya ആണ്. എങ്ങനെ സഹായിക്കാം?",
            "system_prompt": "You are Kavya, the AI receptionist for Aster Medicity Kochi. Respond in Malayalam primarily, switch to English if patient speaks English. Help book appointments. Keep responses under 2 sentences. Available: Dr. Meena Iyer (Gynaecology), Dr. Vikram Shah (Orthopaedic).",
            "stt_provider": "sarvam",
            "stt_model": "saaras:v3",
            "stt_language": "ml-IN",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "pavithra",
            "tts_language": "ml-IN",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "status": "ACTIVE",
        },
        "doctors": [
            {"name": "Dr. Meena Iyer", "specialization": "Gynaecology"},
            {"name": "Dr. Vikram Shah", "specialization": "Orthopaedic"},
        ]
    },
    {
        "id": "tenant-003",
        "clinic_name": "Max Healthcare Delhi",
        "language": "hi-IN",
        "location": "New Delhi",
        "admin_name": "Dr. Amit Gupta",
        "admin_email": "admin@max.lifodial.com",
        "ai_number": "+91 90001 45678",
        "credits": 1000.0,
        "agent": {
            "id": "agent-003",
            "agent_name": "Riya",
            "template": "clinic_receptionist",
            "first_message": "Hello! Welcome to Max Healthcare Delhi. I am Riya, your AI receptionist. How can I assist you today?",
            "system_prompt": "You are Riya, the AI receptionist for Max Healthcare Delhi. Help patients in Hindi or English. Book appointments. Keep responses under 2 sentences. Available: Dr. Anil Kapoor (Neurology), Dr. Sunita Verma (Paediatrics), Dr. Rahul Singh (Orthopaedic).",
            "stt_provider": "sarvam",
            "stt_model": "saaras:v3",
            "stt_language": "en-IN",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "priya",
            "tts_language": "hi-IN",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "status": "ACTIVE",
        },
        "doctors": [
            {"name": "Dr. Anil Kapoor", "specialization": "Neurology"},
            {"name": "Dr. Sunita Verma", "specialization": "Paediatrics"},
            {"name": "Dr. Rahul Singh", "specialization": "Orthopaedic"},
        ]
    },
    {
        "id": "tenant-004",
        "clinic_name": "Manipal Hospitals Bangalore",
        "language": "kn-IN",
        "location": "Bangalore, Karnataka",
        "admin_name": "Dr. Kiran Rao",
        "admin_email": "admin@manipal.lifodial.com",
        "ai_number": "+91 90001 56789",
        "credits": 250.0,
        "agent": {
            "id": "agent-004",
            "agent_name": "Shreya",
            "template": "clinic_receptionist",
            "first_message": "Namaskara! Manipal Hospitals ge swagata. Naanu Shreya. Nimage hege sahaya maadali?",
            "system_prompt": "You are Shreya, the AI receptionist for Manipal Hospitals Bangalore. Respond in Kannada primarily, switch to English if needed. Help book appointments. Keep responses under 2 sentences. Available: Dr. Lakshmi Prasad (Cardiology), Dr. Rohan D'Souza (General).",
            "stt_provider": "sarvam",
            "stt_model": "saaras:v3",
            "stt_language": "kn-IN",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "meera",
            "tts_language": "kn-IN",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "status": "CONFIGURED",
        },
        "doctors": [
            {"name": "Dr. Lakshmi Prasad", "specialization": "Cardiology"},
            {"name": "Dr. Rohan D'Souza", "specialization": "General"},
        ]
    },
    {
        "id": "tenant-005",
        "clinic_name": "Al Zahra Hospital Dubai",
        "language": "ar-SA",
        "location": "Dubai, UAE",
        "admin_name": "Dr. Ahmed Al Rashidi",
        "admin_email": "admin@alzahra.lifodial.com",
        "ai_number": "+971 50001 12345",
        "credits": 2000.0,
        "agent": {
            "id": "agent-005",
            "agent_name": "Layla",
            "template": "clinic_receptionist",
            "first_message": "مرحباً! أهلاً وسهلاً بك في مستشفى الزهراء. أنا ليلى، موظفة الاستقبال. كيف يمكنني مساعدتك؟",
            "system_prompt": "You are Layla, the AI receptionist for Al Zahra Hospital Dubai. Respond in Arabic primarily, switch to English if needed. Help book appointments. Keep responses under 2 sentences. Available: Dr. Ahmed Al Rashidi (General), Dr. Sara Khalil (Gynaecology).",
            "stt_provider": "sarvam",
            "stt_model": "saaras:v3",
            "stt_language": "ar-SA",
            "tts_provider": "sarvam",
            "tts_model": "bulbul:v3",
            "tts_voice": "amol",
            "tts_language": "ar-SA",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "status": "ACTIVE",
        },
        "doctors": [
            {"name": "Dr. Ahmed Al Rashidi", "specialization": "General"},
            {"name": "Dr. Sara Khalil", "specialization": "Gynaecology"},
        ]
    },
]


async def seed():
    await init_db()

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        from backend.services.credit_service import CreditService

        for clinic_data in DEMO_CLINICS:
            # Check if tenant exists
            existing = await db.execute(
                select(Tenant).where(Tenant.id == clinic_data["id"])
            )
            tenant_obj = existing.scalar_one_or_none()

            if tenant_obj:
                print(f"⏭️  {clinic_data['clinic_name']} already exists, skipping")
            else:
                # Create Tenant
                tenant_obj = Tenant(
                    id=clinic_data["id"],
                    clinic_name=clinic_data["clinic_name"],
                    language=clinic_data["language"],
                    location=clinic_data.get("location"),
                    admin_name=clinic_data.get("admin_name"),
                    admin_email=clinic_data.get("admin_email"),
                    ai_number=clinic_data["ai_number"],
                    status="active",
                )
                db.add(tenant_obj)

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

                # Check for existing agent
                existing_agent = await db.execute(
                    select(AgentConfig).where(AgentConfig.id == agent_data["id"])
                )
                if not existing_agent.scalar_one_or_none():
                    from backend.config import settings
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
                        livekit_url=settings.livekit_url,
                        livekit_api_key=settings.livekit_api_key,
                        livekit_api_secret=settings.livekit_api_secret,
                    )
                    db.add(agent)

                await db.flush()  # flush so tenant id is visible for credits
                print(f"✅ Created: {clinic_data['clinic_name']}")

            # Seed/top-up credits (always ensure a balance exists)
            try:
                credits = await CreditService.get_or_create_balance(db, clinic_data["id"])
                if credits.balance == 0.0 and credits.total_added == 0.0:
                    await CreditService.add_credits(
                        db,
                        tenant_id=clinic_data["id"],
                        amount=clinic_data.get("credits", 500.0),
                        description="Initial demo credits",
                        performed_by="system_seed",
                    )
                    print(f"   💰 Credited ₹{clinic_data.get('credits', 500.0)} to {clinic_data['clinic_name']}")
            except Exception as e:
                print(f"   ⚠️  Credits error for {clinic_data['clinic_name']}: {e}")

        await db.commit()
        print("\n✅ Seed complete! Database ready.")
        print("\nDemo clinic logins:")
        for c in DEMO_CLINICS:
            print(f"  {c['admin_email']} → {c['clinic_name']}")


if __name__ == "__main__":
    asyncio.run(seed())
