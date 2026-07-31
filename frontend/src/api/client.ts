/**
 * Centralized API configuration.
 * All frontend files MUST import API_URL / WS_URL from here.
 * For Vercel: set VITE_API_URL in your Vercel env vars (e.g. https://your-ngrok-url.ngrok.io)
 */
import { getToken, clearSession } from './auth';

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

export async function fetchWithAuth(endpoint: string, options: RequestInit = {}) {
  const url = `${API_URL}${endpoint}`;
  const token = getToken();
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'ngrok-skip-browser-warning': 'true',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  // Session expired / rejected — clear and bounce to login.
  if (response.status === 401) {
    clearSession();
    if (!window.location.pathname.includes('/login')) {
      window.location.href = '/login';
    }
    throw new Error('Session expired. Please sign in again.');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'API request failed');
  }
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export default fetchWithAuth;
