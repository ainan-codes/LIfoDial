import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';

/**
 * Wraps protected routes.
 * Checks localStorage for 'lifodial-authed' === 'true'.
 * Redirects to /login with ?from= if not authenticated.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const isAuthed = localStorage.getItem('lifodial-authed') === 'true';

  if (!isAuthed) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

export function isAuthenticated(): boolean {
  return localStorage.getItem('lifodial-authed') === 'true';
}

export function signOut(): void {
  localStorage.removeItem('lifodial-authed');
  localStorage.removeItem('lifodial-superadmin');
  localStorage.removeItem('lifodial-tenant-id');
  localStorage.removeItem('lifodial-email');
  localStorage.removeItem('lifodial-clinic-name');
}
