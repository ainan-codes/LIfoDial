-- ============================================================================
-- Lifodial — Enable Row Level Security (Phase D)
--
-- REVIEW BEFORE APPLYING. This is presented for approval, not yet run.
--
-- WHY THIS IS SAFE FOR THE BACKEND:
--   The backend connects as Postgres role `postgres`, which has
--   rolbypassrls = true AND owns every table. RLS is NOT enforced against
--   BYPASSRLS roles or the table owner (as long as we do NOT use
--   FORCE ROW LEVEL SECURITY). So every existing backend query keeps working
--   unchanged. Verified live: current_user=postgres, rolbypassrls=true,
--   tableowner=postgres.
--
-- WHAT THIS ACTUALLY SECURES:
--   Supabase auto-exposes every public table over PostgREST to the `anon`
--   and `authenticated` API roles. Today RLS is OFF, so anyone with the
--   project's anon key could read/write these tables directly, bypassing the
--   app entirely (that is the "RLS disabled / UNRESTRICTED" banner).
--   Enabling RLS with NO permissive policy = default-deny for those roles,
--   which fully closes that hole.
--
-- WHY NOT tenant_id = auth.jwt() POLICIES:
--   Those only do anything for clients that authenticate to Supabase Auth /
--   PostgREST. This app does neither — it uses its own HS256 JWTs and talks to
--   Postgres only through the bypass role. So auth.jwt()-based policies would
--   never match any real client and would be security theater. If you later
--   expose tables directly via PostgREST under Supabase Auth, add the
--   per-table tenant policies in the OPTIONAL section at the bottom.
-- ============================================================================

-- ── Part 1: enable RLS (default-deny for anon/authenticated) ────────────────
-- NOTE: plain ENABLE, never FORCE — FORCE would subject the owner (postgres,
-- i.e. the backend) to RLS and break every app query.
ALTER TABLE public.agent_configs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_prompt_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alembic_version      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.api_key_configs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.appointments         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bulk_call_campaigns  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.call_logs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.call_records         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clinic_credits       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.credit_transactions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.doctors              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.embed_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.knowledge_bases      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.onboarding_requests  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.phone_numbers        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenants              ENABLE ROW LEVEL SECURITY;

-- Explicitly revoke the blanket PostgREST grants Supabase adds by default, so
-- even a future accidental permissive policy can't re-open these to anon.
-- (Optional but recommended belt-and-suspenders; backend is unaffected.)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
-- 'authenticated' kept revoked too until/unless PostgREST access is designed:
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;


-- ── Part 2 (OPTIONAL, NOT for now): per-tenant PostgREST policies ───────────
-- Only relevant IF you later let clinic browsers hit Supabase/PostgREST
-- directly under Supabase Auth with a tenant_id claim in the JWT. Example for
-- one table; repeat per tenant-scoped table. Left commented — do not apply
-- unless that architecture is actually adopted.
--
-- CREATE POLICY tenant_isolation_select ON public.appointments
--   FOR SELECT TO authenticated
--   USING (tenant_id = (auth.jwt() ->> 'tenant_id'));
-- CREATE POLICY tenant_isolation_write ON public.appointments
--   FOR ALL TO authenticated
--   USING (tenant_id = (auth.jwt() ->> 'tenant_id'))
--   WITH CHECK (tenant_id = (auth.jwt() ->> 'tenant_id'));
