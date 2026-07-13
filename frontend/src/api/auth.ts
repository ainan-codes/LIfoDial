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
  if (s.tenantId) localStorage.setItem(TENANT_KEY, s.tenantId);
  if (s.email) localStorage.setItem(EMAIL_KEY, s.email);
  if (s.clinicName) localStorage.setItem(CLINIC_KEY, s.clinicName);
}

export function clearSession(): void {
  [TOKEN_KEY, ROLE_KEY, TENANT_KEY, EMAIL_KEY, CLINIC_KEY,
   // legacy flags from the old fake-auth scheme
   'lifodial-authed', 'lifodial-superadmin'].forEach(k => localStorage.removeItem(k));
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
