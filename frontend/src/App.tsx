import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ThemeProvider } from './context/ThemeContext';

// Public pages
import Docs from './pages/Docs';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Onboarding from './pages/Onboarding';

// Protected pages
import Analytics from './pages/Analytics';
import Appointments from './pages/Appointments';
import CallLogs from './pages/CallLogs';
import Dashboard from './pages/Dashboard';
import Doctors from './pages/Doctors';
import MyAgent from './pages/MyAgent';
import Settings from './pages/Settings';
import VoiceRecorder from './pages/VoiceRecorder';

import { AgentRouteGuard } from './components/AgentRouteGuard';
import Layout from './components/Layout';
import { RequireAuth } from './components/RequireAuth';

// Super Admin Imports
import { RequireSuperAdmin } from './components/superadmin/RequireSuperAdmin';
import SuperAdminLayout from './components/superadmin/SuperAdminLayout';
import AgentDetail from './pages/superadmin/AgentDetail';
import SAAgents from './pages/superadmin/Agents';
import AIPlatform from './pages/superadmin/AIPlatform';
import SAAppointments from './pages/superadmin/Appointments';
import SABilling from './pages/superadmin/Billing';
import SACalls from './pages/superadmin/Calls';
import SAClinics from './pages/superadmin/Clinics';
import CreateAgent from './pages/superadmin/CreateAgent';
import SADashboard from './pages/superadmin/Dashboard';
import KnowledgeBase from './pages/superadmin/KnowledgeBase';
import SAOnboardingReqs from './pages/superadmin/OnboardingReqs';
import PhoneNumbers from './pages/superadmin/PhoneNumbers';
import SuperAdminLogin from './pages/superadmin/SuperAdminLogin';
import SASystemHealth from './pages/superadmin/SystemHealth';
import VoiceLibrary from './pages/superadmin/VoiceLibrary';
import SACredits from './pages/superadmin/Credits';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
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

            <Route
              path="/recorder"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<VoiceRecorder />} />
            </Route>

            <Route
              path="/settings"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<Settings />} />
            </Route>
            
            <Route
              path="/voice-library"
              element={<RequireAuth><Layout /></RequireAuth>}
            >
              <Route index element={<VoiceLibrary readOnly={true} />} />
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
                <Route path="/superadmin/system" element={<SASystemHealth />} />
                <Route path="/superadmin/ai-platform" element={<AIPlatform />} />
                <Route path="/superadmin/requests" element={<SAOnboardingReqs />} />
                <Route path="/superadmin/billing" element={<SABilling />} />
                <Route path="/superadmin/voice-library" element={<VoiceLibrary />} />
                <Route path="/superadmin/phone-numbers" element={<PhoneNumbers />} />
                <Route path="/superadmin/credits" element={<SACredits />} />
              </Route>
            </Route>

            {/* Catch-all — redirect to landing */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}

export default App;
