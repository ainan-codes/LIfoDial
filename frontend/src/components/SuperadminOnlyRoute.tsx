/**
 * SuperadminOnlyRoute.tsx
 * Guards a route that lives inside the CLINIC shell (RequireAuth + Layout) but
 * is platform-team tooling, not a clinic feature — currently Voice Clone
 * (/recorder) and Voice Library (/voice-library).
 *
 * Hiding the sidebar entry in Layout.tsx is not enough on its own: the route
 * would still resolve for anyone who typed or bookmarked the URL. This sends a
 * clinic admin back to their dashboard instead.
 *
 * Superadmins fall through and see the page as normal, so they keep full access
 * from both the clinic shell and /superadmin/*.
 */
import { Navigate } from 'react-router-dom';
import { isSuperAdmin } from '../api/auth';

export function SuperadminOnlyRoute({ children }: { children: React.ReactNode }) {
  if (!isSuperAdmin()) {
    return <Navigate to="/dashboard" replace />;
  }
  return <>{children}</>;
}
