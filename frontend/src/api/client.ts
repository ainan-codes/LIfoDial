/**
 * Centralized API configuration.
 * All frontend files MUST import API_URL / WS_URL from here.
 * For Vercel: set VITE_API_URL in your Vercel env vars (e.g. https://your-ngrok-url.ngrok.io)
 */

export const API_URL = import.meta.env.VITE_API_URL || 'https://lifodial.onrender.com';

// Derive WebSocket URL from API URL
const _wsBase = API_URL.replace(/^http/, 'ws');
export const WS_URL = _wsBase;

export async function fetchWithAuth(endpoint: string, options: RequestInit = {}) {
  const url = `${API_URL}${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || 'API request failed');
  }
  return response.json();
}

export default fetchWithAuth;
