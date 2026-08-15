/**
 * Centralized API configuration.
 * All frontend files MUST import API_URL / WS_URL from here.
 * For Vercel: set VITE_API_URL in your Vercel env vars (e.g. https://your-ngrok-url.ngrok.io)
 */
import { getToken, clearSession, isImpersonating, endImpersonation } from './auth';

const getDynamicApiUrl = () => {
  if (typeof window !== 'undefined') {
    const origin = window.location.origin;
    // Tunneled access (phone, remote testing): route through the same origin's
    // /api prefix so the Vite dev-server proxy forwards to the local backend —
    // a raw VITE_API_URL=http://localhost:8001 would resolve to the PHONE's
    // own localhost, not the laptop running the backend.
    if (origin.includes('ngrok') || origin.includes('trycloudflare.com')) return `${origin}/api`;
    if (origin.includes('localhost') || origin.includes('127.0.0.1')) {
      return import.meta.env.VITE_API_URL || 'http://localhost:8001';
    }
  }
  if (import.meta.env.VITE_API_URL) return import.meta.env.VITE_API_URL;
  return 'https://lifodial-backend-production.up.railway.app';
};

export const API_URL = getDynamicApiUrl();

// Derive WebSocket URL from API URL
const _wsBase = API_URL.replace(/^http/, 'ws');
export const WS_URL = _wsBase;

/** Append the Bearer token (if any) to a WS URL as ?token= for authenticated sockets. */
export function wsUrlWithAuth(path: string): string {
  const token = getToken();
  const sep = path.includes('?') ? '&' : '?';
  return `${WS_URL}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ''}`;
}

/**
 * How long any single API request may take before we give up on it.
 *
 * The backend runs on NullPool, so every request pays a fresh ~2.3s Supabase
 * connect before its query even starts (see backend/db.py). A slow endpoint on
 * top of that used to hang the page indefinitely — there was no timeout at all —
 * and the user's only signal was a spinner that never resolved.
 */
const REQUEST_TIMEOUT_MS = 20000;

/**
 * Retry once on a transient failure.
 *
 * Only for GETs, and only for the failures that are actually transient: a
 * network blip, a gateway 502/503/504, or our own timeout. A 4xx is a real
 * answer and must not be retried; a POST/PATCH/DELETE must never be replayed
 * because it may already have taken effect.
 *
 * This is why the appointments page showed "Could not load appointments: API
 * request failed" and then worked on a manual refresh (observed 2026-08-15,
 * backend logs show a 500 followed six seconds later by a 200). The retry the
 * user was performing by hand is the one the client should have done itself.
 */
const RETRYABLE_STATUS = new Set([502, 503, 504]);

function isRetryable(method: string, err: unknown, status?: number): boolean {
  if (method !== 'GET') return false;
  if (status !== undefined) return RETRYABLE_STATUS.has(status);
  // No status => the request never completed: abort (our timeout) or a network error.
  return true;
}

export async function fetchWithAuth(endpoint: string, options: RequestInit = {}) {
  const url = `${API_URL}${endpoint}`;
  const token = getToken();
  const method = (options.method || 'GET').toUpperCase();

  const attempt = async (): Promise<Response> => {
    // AbortSignal.timeout is not available in every browser we support, so the
    // controller is driven manually and the timer is always cleared.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      return await fetch(url, {
        ...options,
        signal: options.signal ?? controller.signal,
        headers: {
          'Content-Type': 'application/json',
          'ngrok-skip-browser-warning': 'true',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...options.headers,
        },
      });
    } finally {
      clearTimeout(timer);
    }
  };

  let response: Response;
  try {
    response = await attempt();
    if (!response.ok && isRetryable(method, undefined, response.status)) {
      response = await attempt();
    }
  } catch (err) {
    if (!isRetryable(method, err)) throw err;
    response = await attempt();
  }

  // Session expired / rejected — clear and bounce to login.
  if (response.status === 401) {
    // An impersonation session that has been exited or has run past its TTL 401s
    // here (the backend rechecks the session row on every request). That is a
    // superadmin sitting in someone else's dashboard, not a clinic whose login
    // lapsed: restore their own session and return them to the panel instead of
    // dumping them on the clinic login page with their superadmin session wiped.
    if (isImpersonating()) {
      const restored = endImpersonation();
      window.location.href = restored ? '/superadmin/clinics' : '/superadmin/login';
      throw new Error('Impersonation session has ended.');
    }
    clearSession();
    if (!window.location.pathname.includes('/login')) {
      window.location.href = '/login';
    }
    throw new Error('Session expired. Please sign in again.');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    // A non-JSON error body means the response did not come from FastAPI — a
    // proxy/gateway page, usually a timeout. The bare "API request failed" that
    // used to surface here told the user nothing about which of those it was,
    // and every 5xx looked identical in the UI.
    throw new Error(
      error.detail ||
        (response.status >= 500
          ? `The server did not respond in time (${response.status}). Please try again.`
          : `Request failed (${response.status}).`),
    );
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export default fetchWithAuth;
