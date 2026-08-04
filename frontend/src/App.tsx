import { lazy, Suspense } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ThemeProvider } from './context/ThemeContext';

// Public pages
const Docs = lazy(() => import('./pages/Docs'));
const Landing = lazy(() => import('./pages/Landing'));
const Login = lazy(() => import('./pages/Login'));
const Onboarding = lazy(() => import('./pages/Onboarding'));

// Protected pages
const Analytics = lazy(() => import('./pages/Analytics'));
const Appointments = lazy(() => import('./pages/Appointments'));
const CallLogs = lazy(() => import('./pages/CallLogs'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Doctors = lazy(() => import('./pages/Doctors'));
const MyAgent = lazy(() => import('./pages/MyAgent'));
const Settings = lazy(() => import('./pages/Settings'));
const VoiceRecorder = lazy(() => import('./pages/VoiceRecorder'));

import { AgentRouteGuard } from './components/AgentRouteGuard';
import Layout from './components/Layout';
import { RequireAuth } from './components/RequireAuth';
import { SuperadminOnlyRoute } from './components/SuperadminOnlyRoute';

// Super Admin Imports
import { RequireSuperAdmin } from './components/superadmin/RequireSuperAdmin';
const SuperAdminLayout = lazy(() => import('./components/superadmin/SuperAdminLayout'));
const AgentDetail = lazy(() => import('./pages/superadmin/AgentDetail'));
const SAAgents = lazy(() => import('./pages/superadmin/Agents'));
const SAAppointments = lazy(() => import('./pages/superadmin/Appointments'));
const SACalls = lazy(() => import('./pages/superadmin/Calls'));
const SAClinics = lazy(() => import('./pages/superadmin/Clinics'));
const CreateAgent = lazy(() => import('./pages/superadmin/CreateAgent'));
const SADashboard = lazy(() => import('./pages/superadmin/Dashboard'));
const KnowledgeBase = lazy(() => import('./pages/superadmin/KnowledgeBase'));
const SuperAdminLogin = lazy(() => import('./pages/superadmin/SuperAdminLogin'));
const VoiceLibrary = lazy(() => import('./pages/superadmin/VoiceLibrary'));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

// Route-level Suspense fallback. Each top-level page is its own chunk (see the
// lazy() imports above), so this only ever shows for the ~100-300ms a chunk
// takes to fetch on navigation — not on every render.
function PageLoader() {
  return (
    <div className="flex h-screen w-full items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600" />
    </div>
  );
}

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Suspense fallback={<PageLoader />}>
          <Routes>
            {/* ── Public routes ── */}
            <Route path="/"       element={<Landing />} />
            <Route path="/docs"   element={<Docs />} />
            <Route path="/login"  element={<Login />} />
            <Route path="/onboarding" element={<Onboarding />} />

            {/* ── Protected routes (wrapped in RequireAuth + Layout) ── */}
            <Route
              path="/dashboard"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<Dashboard />} />
            </Route>

            <Route
              path="/calls"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<CallLogs />} />
            </Route>

            <Route
              path="/appointments"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<Appointments />} />
            </Route>

            <Route
              path="/doctors"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<Doctors />} />
            </Route>

            <Route
              path="/analytics"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<Analytics />} />
            </Route>

            {/* Voice Clone — platform tooling, superadmin only (clinic admins
                must not be able to retrain the clinic's TTS voice). */}
            <Route
              path="/recorder"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<SuperadminOnlyRoute><VoiceRecorder /></SuperadminOnlyRoute>} />
            </Route>

            <Route
              path="/settings"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<Settings />} />
            </Route>

            {/* Voice Library — superadmin only. It exposes which TTS providers
                have API keys configured ("Connected" / "Add API key to unlock"),
                which is platform information, not clinic information. */}
            <Route
              path="/voice-library"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<SuperadminOnlyRoute><VoiceLibrary readOnly={true} /></SuperadminOnlyRoute>} />
            </Route>

            {/* Clinic admin — Agents view (hidden, redirects to dashboard) */}
            <Route
              path="/agents"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              {/* Agent setup pending — will be enabled later */}
              <Route index element={<AgentRouteGuard />} />
            </Route>

            {/* ── Clinic Admin: My Agent (read-only dashboard) ── */}
            <Route
              path="/my-agent"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<MyAgent />} />
            </Route>

            {/* Super Admin Routes */}
            <Route path="/superadmin/login" element={<SuperAdminLogin />} />

            <Route element={<RequireSuperAdmin />}>
              <Route element={<SuperAdminLayout />}>
                <Route path="/superadmin/dashboard" element={<SADashboard />} />
                <Route path="/superadmin/agents" element={<SAAgents />} />
                <Route path="/superadmin/agents/new" element={<CreateAgent />} />
                <Route path="/superadmin/agents/:agentId" element={<AgentDetail />} />
                <Route path="/superadmin/clinics" element={<SAClinics />} />
                <Route path="/superadmin/calls" element={<SACalls />} />
                <Route path="/superadmin/appointments" element={<SAAppointments />} />
                <Route path="/superadmin/knowledge" element={<KnowledgeBase />} />
                <Route path="/superadmin/voice-library" element={<VoiceLibrary />} />
              </Route>
            </Route>

            {/* Catch-all — redirect to landing */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;
