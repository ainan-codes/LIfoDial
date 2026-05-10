-- ─────────────────────────────────────────────────────────────────────────────
-- Lifodial Supabase Seed
-- Project: citniicxkmazxuosauxy
-- Apply via antigravity Supabase MCP (or psql session pooler).
-- Idempotent: ON CONFLICT DO NOTHING on every INSERT.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Audit current row counts ──────────────────────────────────────────────
SELECT 'tenants'        AS tbl, COUNT(*) FROM tenants
UNION ALL SELECT 'agent_configs',  COUNT(*) FROM agent_configs
UNION ALL SELECT 'doctors',        COUNT(*) FROM doctors
UNION ALL SELECT 'clinic_credits', COUNT(*) FROM clinic_credits;

-- ── 2. Tenants ───────────────────────────────────────────────────────────────
INSERT INTO tenants (
  id, clinic_name, admin_email, admin_password,
  admin_name, phone, language, plan, status, is_active,
  ai_number, created_at
) VALUES
('tenant-001','Apollo Multispeciality Mumbai','admin@apollomumbai.com',
 'Apollo@2024','Dr. Rajesh Kumar','+91 90001 23456','hi-IN','pro',
 'ACTIVE',true,'+91 90001 23456',NOW()),
('tenant-002','Aster Medicity Kochi','admin@astermedicity.com',
 'Aster@2024','Dr. Meena Iyer','+91 90001 34567','ml-IN','pro',
 'ACTIVE',true,'+91 90001 34567',NOW()),
('tenant-003','Al Zahra Hospital Dubai','admin@alzahradubai.com',
 'AlZahra@2024','Dr. Ahmed Al Rashidi','+971 50001 12345','ar-SA',
 'free','ACTIVE',true,'+971 50001 12345',NOW()),
('tenant-004','Max Healthcare Delhi','admin@maxdelhi.com',
 'Max@2024','Dr. Priya Sharma','+91 90001 45678','hi-IN','pro',
 'ACTIVE',true,'+91 90001 45678',NOW()),
('tenant-005','Aster Clinic Kochi','admin@asterkochi.com',
 'AsterKochi@2024','Dr. Vishnu Nair','+91 90001 56789','ml-IN',
 'free','ACTIVE',true,'+91 90001 56789',NOW())
ON CONFLICT (id) DO NOTHING;

-- ── 3. Agent configs ─────────────────────────────────────────────────────────
INSERT INTO agent_configs (
  id, tenant_id, agent_name, template,
  first_message, system_prompt,
  stt_provider, stt_model, stt_language,
  tts_provider, tts_model, tts_voice, tts_language,
  llm_provider, llm_model, llm_temperature, max_response_tokens,
  status, can_book_appointments, auto_detect_language,
  embed_enabled, embed_position, embed_theme,
  embed_button_text, embed_primary_color, embed_show_branding,
  created_at
) VALUES
('agent-001','tenant-001','Receptionist','clinic_receptionist',
 'Namaste! Apollo Clinic mein aapka swagat hai. Main aapki kaise madad kar sakti hoon?',
 'You are AI receptionist for Apollo Multispeciality Mumbai. CRITICAL: Keep every response under 2 sentences. This is a voice call. Help book appointments. Doctors: Dr. Suresh Menon (Cardiology), Dr. Ananya Rao (General), Dr. Priya Nair (Dermatology). Emergency words: transfer immediately.',
 'sarvam','saarika:v2','hi-IN',
 'sarvam','bulbul:v2','meera','hi-IN',
 'gemini','gemini-2.0-flash',0.3,100,
 'ACTIVE',true,true,true,'bottom-right','dark',
 'Talk to Receptionist','#3ECF8E',true,NOW()),
('agent-002','tenant-002','Receptionist','clinic_receptionist',
 'നമസ്കാരം! Aster Medicity-ലേക്ക് സ്വാഗതം. ഞാൻ നിങ്ങളുടെ AI റിസപ്ഷനിസ്റ്റ് ആണ്.',
 'You are AI receptionist for Aster Medicity Kochi. Respond in Malayalam. Under 2 sentences. Doctors: Dr. Meena Iyer (Gynaecology), Dr. Vikram Shah (Orthopaedic).',
 'sarvam','saarika:v2','ml-IN',
 'sarvam','bulbul:v2','pavithra','ml-IN',
 'groq','llama-3.3-70b-versatile',0.3,100,
 'ACTIVE',true,true,true,'bottom-right','dark',
 'Talk to Receptionist','#3ECF8E',true,NOW()),
('agent-003','tenant-003','Receptionist','clinic_receptionist',
 'مرحباً! أهلاً وسهلاً بك في مستشفى الزهراء. كيف يمكنني مساعدتك؟',
 'You are AI receptionist for Al Zahra Hospital Dubai. Respond in Arabic. Under 2 sentences. Doctor: Dr. Ahmed Al Rashidi (General Medicine).',
 'sarvam','saarika:v2','ar-SA',
 'groq','playai-tts-arabic','Nadia-PlayAI','ar-SA',
 'gemini','gemini-2.0-flash',0.3,100,
 'ACTIVE',true,true,true,'bottom-right','dark',
 'Talk to Receptionist','#3ECF8E',true,NOW()),
('agent-004','tenant-004','Receptionist','clinic_receptionist',
 'Hello! Welcome to Max Healthcare Delhi. How can I help you today?',
 'You are AI receptionist for Max Healthcare Delhi. Under 2 sentences. Doctors: Dr. Priya Sharma (General), Dr. Ravi Kumar (Cardiology).',
 'sarvam','saarika:v2','hi-IN',
 'sarvam','bulbul:v2','priya','hi-IN',
 'groq','llama-3.3-70b-versatile',0.3,100,
 'ACTIVE',true,true,true,'bottom-right','dark',
 'Talk to Receptionist','#3ECF8E',true,NOW()),
('agent-005','tenant-005','Receptionist','clinic_receptionist',
 'നമസ്കാരം! Aster Clinic-ലേക്ക് സ്വാഗതം.',
 'You are AI receptionist for Aster Clinic Kochi. Respond in Malayalam. Under 2 sentences.',
 'sarvam','saarika:v2','ml-IN',
 'sarvam','bulbul:v2','kavitha','ml-IN',
 'groq','llama-3.3-70b-versatile',0.3,100,
 'ACTIVE',true,true,true,'bottom-right','dark',
 'Talk to Receptionist','#3ECF8E',true,NOW())
ON CONFLICT (id) DO NOTHING;

-- ── 4. Doctors ───────────────────────────────────────────────────────────────
INSERT INTO doctors (id, tenant_id, name, specialization, is_available, created_at)
VALUES
(gen_random_uuid()::text,'tenant-001','Dr. Suresh Menon','Cardiology',true,NOW()),
(gen_random_uuid()::text,'tenant-001','Dr. Ananya Rao','General Physician',true,NOW()),
(gen_random_uuid()::text,'tenant-001','Dr. Priya Nair','Dermatology',true,NOW()),
(gen_random_uuid()::text,'tenant-002','Dr. Meena Iyer','Gynaecology',true,NOW()),
(gen_random_uuid()::text,'tenant-002','Dr. Vikram Shah','Orthopaedic',true,NOW()),
(gen_random_uuid()::text,'tenant-003','Dr. Ahmed Al Rashidi','General Medicine',true,NOW()),
(gen_random_uuid()::text,'tenant-004','Dr. Priya Sharma','General Physician',true,NOW()),
(gen_random_uuid()::text,'tenant-004','Dr. Ravi Kumar','Cardiology',true,NOW()),
(gen_random_uuid()::text,'tenant-005','Dr. Vishnu Nair','General Physician',true,NOW())
ON CONFLICT DO NOTHING;

-- ── 5. Clinic credits ────────────────────────────────────────────────────────
INSERT INTO clinic_credits (
  id, tenant_id, balance_paise, monthly_allocation_paise,
  used_this_month_paise, plan, is_active, created_at
) VALUES
(gen_random_uuid()::text,'tenant-001',500000,500000,0,'pro',true,NOW()),
(gen_random_uuid()::text,'tenant-002',500000,500000,0,'pro',true,NOW()),
(gen_random_uuid()::text,'tenant-003',100000,100000,0,'free',true,NOW()),
(gen_random_uuid()::text,'tenant-004',500000,500000,0,'pro',true,NOW()),
(gen_random_uuid()::text,'tenant-005',100000,100000,0,'free',true,NOW())
ON CONFLICT DO NOTHING;

-- ── 6. Verify ────────────────────────────────────────────────────────────────
SELECT 'tenants'        AS tbl, COUNT(*) FROM tenants
UNION ALL SELECT 'agent_configs',  COUNT(*) FROM agent_configs
UNION ALL SELECT 'doctors',        COUNT(*) FROM doctors
UNION ALL SELECT 'clinic_credits', COUNT(*) FROM clinic_credits;
