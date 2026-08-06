/**
 * Centralized client-side session state.
 *
 * The server is the source of truth: every protected API call carries the
 * Bearer token (see client.ts) and the backend validates it. These helpers are
 * a thin UX layer so the SPA can show the right screen without a round-trip.
 *
 * Tokens are short-lived JWTs (12h) issued by /auth/*-login. We store them in
 * localStorage for simplicity; the backend enforces expiry regardless.
 */

const TOKEN_KEY = 'lifodial-token';
const ROLE_KEY = 'lifodial-role';
const TENANT_KEY = 'lifodial-tenant-id';
const EMAIL_KEY = 'lifodial-email';
const CLINIC_KEY = 'lifodial-clinic-name';
// Marks the ACTIVE session as a superadmin impersonation ("view as this clinic").
const IMPERSONATION_KEY = 'lifodial-impersonation';
// The superadmin's own session, parked while impersonating so Exit is one click
// and does not require logging in again.
const SA_BACKUP_KEY = 'lifodial-superadmin-session';

export interface Session {
  token: string;
  role: 'clinic' | 'superadmin';
  tenantId?: string;
  email?: string;
  clinicName?: string;
}

export function setSession(s: Session): void {
  localStorage.setItem(TOKEN_KEY, s.token);
  localStorage.setItem(ROLE_KEY, s.role);
  // Identity fields are REPLACED, not merged. These used to be written only when
  // present, so anything the new session lacked was inherited from the previous
  // one — and starting an impersonation passes no email, so the SUPERADMIN's
  // admin@lifodial.com survived into the clinic session and became the key
  // My Agent looked the agent up by. That is the "No agent configured for
  // admin@lifodial.com" bug: a session half-belonging to two identities.
  const replace = (key: string, value?: string) => {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  };
  replace(TENANT_KEY, s.tenantId);
  replace(EMAIL_KEY, s.email);
  replace(CLINIC_KEY, s.clinicName);
}

export function clearSession(): void {
  [TOKEN_KEY, ROLE_KEY, TENANT_KEY, EMAIL_KEY, CLINIC_KEY,
   // An impersonation marker must never outlive the session it describes — a
   // stale one would put the banner on a genuine clinic login (and vice versa).
   IMPERSONATION_KEY, SA_BACKUP_KEY,
   // legacy flags from the old fake-auth scheme
   'lifodial-authed', 'lifodial-superadmin'].forEach(k => localStorage.removeItem(k));
}

// ── Superadmin impersonation ────────────────────────────────────────────────
//
// The token minted by POST /admin/clinics/{id}/impersonate is an ordinary clinic
// token for exactly one clinic, so it is stored in the normal session slot and
// every existing page works against it unchanged. This record is what makes the
// session VISIBLE: while it exists the clinic shell shows a permanent banner
// (components/ImpersonationBanner.tsx), so an impersonated session can never be
// mistaken for the clinic's own.
//
// The token is never put in a URL — it is handed over in the POST response and
// stored here, so there is no link anyone could copy, bookmark, or share.

export interface Impersonation {
  /** Server-side session id; ending it is what revokes the token. */
  sessionId: string;
  tenantId: string;
  clinicName: string;
  /** Epoch ms. The backend enforces this too — this copy drives the countdown. */
  expiresAt: number;
}

/**
 * Swap the superadmin's session for a clinic-scoped impersonation session,
 * parking their own token so endImpersonation() can put it back.
 */
export function startImpersonation(clinicSession: Session, imp: Impersonation): void {
  const own = getToken();
  if (own && localStorage.getItem(ROLE_KEY) === 'superadmin') {
    localStorage.setItem(SA_BACKUP_KEY, JSON.stringify({
      token: own,
      role: 'superadmin',
      email: localStorage.getItem(EMAIL_KEY) || undefined,
    }));
  }
  setSession(clinicSession);
  localStorage.setItem(IMPERSONATION_KEY, JSON.stringify(imp));
}

/**
 * Read the active impersonation record, if any.
 *
 * Deliberately does NOT hide an expired record. Dropping it on expiry would take
 * the banner away while a clinic-shaped token was still in localStorage — the
 * exact "looks like the clinic's own session" state this feature must not have.
 * The banner surfaces expiry and exits; the token itself is already dead
 * server-side, since auth.py rechecks the session row on every request.
 */
export function getImpersonation(): Impersonation | null {
  const raw = localStorage.getItem(IMPERSONATION_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Impersonation;
    if (!parsed?.sessionId || !parsed?.tenantId) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function isImpersonating(): boolean {
  return getImpersonation() !== null;
}

/**
 * Leave impersonation and restore the superadmin's own session.
 *
 * Returns false when there is nothing valid to restore (the parked token expired
 * during a long support session) — the caller should then send them to the
 * superadmin login rather than pretend they are still signed in.
 *
 * This only tears down the CLIENT side. The server-side session is ended by
 * POST /admin/impersonation/end, which the banner calls first; its TTL is the
 * backstop if that call never lands.
 */
export function endImpersonation(): boolean {
  // Idempotent on purpose. Two paths can call this for one exit: the banner, and
  // the 401 handler in api/client.ts when the POST /impersonation/end itself comes
  // back 401 (session already gone). Without this guard the second call would find
  // no parked backup and clear the superadmin session the FIRST call had just
  // restored — exiting impersonation would silently log the superadmin out.
  if (!getImpersonation()) return isSuperAdmin();

  const backupRaw = localStorage.getItem(SA_BACKUP_KEY);
  clearSession();

  if (!backupRaw) return false;
  try {
    const backup = JSON.parse(backupRaw) as Session;
    if (!backup?.token) return false;
    setSession({ token: backup.token, role: 'superadmin', email: backup.email });
    return isAuthenticated();
  } catch {
    return false;
  }
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** Decode a JWT payload without verifying (server verifies on every call). */
function decodePayload(token: string): { exp?: number; role?: string } | null {
  try {
    const part = token.split('.')[1];
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/** A token is valid for UX purposes if present and not past its exp. */
export function isAuthenticated(): boolean {
  const token = getToken();
  if (!token) return false;
  const p = decodePayload(token);
  if (!p || !p.exp) return false;
  if (Date.now() >= p.exp * 1000) {
    clearSession();
    return false;
  }
  return true;
}

export function isSuperAdmin(): boolean {
  return isAuthenticated() && localStorage.getItem(ROLE_KEY) === 'superadmin';
}

export function getTenantId(): string | null {
  return localStorage.getItem(TENANT_KEY);
}
