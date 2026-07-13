import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { isAuthenticated as hasValidToken, clearSession } from '../api/auth';

/**
 * Wraps protected routes. Requires a valid (present, unexpired) session token.
 * This is a UX gate only — the backend independently verifies the token on
 * every API call, so tampering with client state grants no data access.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation();

  if (!hasValidToken()) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

export function isAuthenticated(): boolean {
  return hasValidToken();
}

export function signOut(): void {
  clearSession();
}
