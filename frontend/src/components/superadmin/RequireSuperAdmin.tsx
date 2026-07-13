import { Navigate, Outlet } from 'react-router-dom';
import { isSuperAdmin } from '../../api/auth';

export function RequireSuperAdmin() {
  // UX gate only — the backend enforces the superadmin role on every /admin
  // API call via a verified JWT, so this cannot be bypassed for data access.
  return isSuperAdmin() ? <Outlet /> : <Navigate to="/superadmin/login" replace />;
}
