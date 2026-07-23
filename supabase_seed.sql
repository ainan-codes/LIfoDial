-- ─────────────────────────────────────────────────────────────────────────────
-- Lifodial Supabase Seed
-- Project: wcvlmhhayddakfqqafnh  (new account)
-- Apply via Supabase SQL editor or psql session pooler.
-- Idempotent: ON CONFLICT DO NOTHING on every INSERT.
-- NOTE: The real tenant was created via the app UI and already exists in the DB.
--       This file is for reference / re-seeding doctors & agent configs only.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Audit current row counts ──────────────────────────────────────────────
SELECT 'tenants'       AS tbl, COUNT(*) FROM tenants
UNION ALL SELECT 'agent_configs', COUNT(*) FROM agent_configs
UNION ALL SELECT 'doctors',       COUNT(*) FROM doctors;

-- ── 2. Tenants ───────────────────────────────────────────────────────────────
-- Real tenant already exists (created via app UI by mohammedainan3@gmail.com).
-- Uncomment below ONLY if re-seeding from scratch on a fresh database.

-- INSERT INTO tenants (
--   id, clinic_name, admin_email, admin_name, phone, language, plan, status, is_active, created_at
-- ) VALUES
-- ('9d1b0f45-6501-472a-a525-1ef3928f7980', 'Aster Clinic Kochi', 'mohammedainan3@gmail.com',
--  'Admin', '', 'ml-IN', 'Free', 'active', true, NOW())
-- ON CONFLICT (id) DO NOTHING;

-- ── 3. Agent Configs ─────────────────────────────────────────────────────────
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
('72cb61a4-1340-47d3-a9b3-9bd3765ceecf',
 '9d1b0f45-6501-472a-a525-1ef3928f7980',
 'Receptionist', 'clinic_receptionist',
 'നമസ്കാരം! Aster Clinic-ലേക്ക് സ്വാഗതം.',
 'You are AI receptionist for Aster Clinic Kochi. Respond in Malayalam. Under 2 sentences.',
 'sarvam', 'saarika:v2', 'ml-IN',
 'sarvam', 'bulbul:v2', 'kavitha', 'ml-IN',
 'groq', 'llama-3.3-70b-versatile', 0.3, 100,
 'ACTIVE', true, true, true, 'bottom-right', 'dark',
 'Talk to Receptionist', '#3ECF8E', true, NOW())
ON CONFLICT (id) DO NOTHING;

-- ── 4. Doctors ───────────────────────────────────────────────────────────────
-- Doctors are referenced by name in the DB (no fixed UUIDs), so we use ON CONFLICT DO NOTHING
-- to avoid duplicates if re-run.
INSERT INTO doctors (tenant_id, name, specialization, is_available, created_at)
VALUES
('9d1b0f45-6501-472a-a525-1ef3928f7980', 'Dr. Sharma',  'General Physician', true, NOW()),
('9d1b0f45-6501-472a-a525-1ef3928f7980', 'Dr. Reddy',   'Pediatrician',      true, NOW()),
('9d1b0f45-6501-472a-a525-1ef3928f7980', 'Dr. Kapoor',  'Dermatologist',     true, NOW())
ON CONFLICT DO NOTHING;

-- ── 5. Verify ────────────────────────────────────────────────────────────────
SELECT 'tenants'       AS tbl, COUNT(*) FROM tenants
UNION ALL SELECT 'agent_configs', COUNT(*) FROM agent_configs
UNION ALL SELECT 'doctors',       COUNT(*) FROM doctors;
