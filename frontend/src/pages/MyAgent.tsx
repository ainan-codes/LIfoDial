import { AlertCircle, Headphones } from 'lucide-react';
import { Suspense, lazy, useEffect, useState } from 'react';
import fetchWithAuth from '../api/client';
import { AgentCard, PhoneCallModal } from '../components/AgentCard';
import { FixtureAgent } from '../fixtures/data';

// Lazy: pulls in the LiveKit/WebRTC client stack (~526kB, the largest chunk in
// the app) — only needed once the test modal actually opens.
const TestAgentModal = lazy(() => import('./../components/TestAgentModal'));

/**
 * My Agent — the clinic's read-only view of its own receptionist.
 *
 * WHAT THIS PAGE IS
 *   The same agent card Superadmin → Agents shows (components/AgentCard.tsx),
 *   scoped to the one agent belonging to the current session's clinic, with the
 *   editing removed and the two test buttons kept. A clinic admin sees exactly
 *   what the platform team sees for their agent — status, greeting, language,
 *   voice/model, Calls today / Bookings / Avg latency / Resolution — and cannot
 *   change any of it.
 *
 * WHAT IT USED TO BE, AND WHY THAT WAS WRONG
 *   A five-tab editable dashboard (Assistant/Logs/Tools/Analysis/Advanced) with a
 *   Credit Balance card, a Recent Calls table and an editable Voice Configuration
 *   section. Two problems:
 *
 *     * Shape. It let a clinic edit live voice/model config — the settings that
 *       decide whether calls work at all — and duplicated call history that
 *       already has its own pages (Call Logs, Dashboard).
 *     * Speed. loadData() awaited FIVE requests in sequence: GET /agents
 *       (superadmin-only, so it 403'd for every clinic session and was swallowed),
 *       /agents/mine, /credits/my-balance, /agents/{id}/call-logs and
 *       /api/call_logs?limit=50. Serial round trips to Railway+Supabase, each one
 *       also paying the impersonation session check under a "view as" session.
 *       That was the long "Loading your agent..." spinner — not slow rendering.
 *
 *   Removing the heavy dashboard removed four of those five calls. What is left is
 *   /agents/mine, then /agents/{id}/health for the stats (which needs the id, so
 *   it cannot be parallelised) — and the card renders as soon as the first
 *   resolves, with stats filling in after, exactly as Superadmin → Agents does.
 *
 * READ-ONLY IS ENFORCED ON THE SERVER TOO
 *   `readOnly` here only removes affordances. PATCH /agents/{id} refuses every
 *   config field for a clinic-role token (`_authorize_agent_patch`), and the
 *   prompt/greeting/avatar writes refuse it outright (`_require_agent_write_access`)
 *   — see backend/tests/test_agent_config_readonly_for_clinics.py. The one field a
 *   clinic may still write is `clinic_info` (working hours), which Settings →
 *   Clinic Profile saves; nothing on THIS page writes anything.
 */

/** Map a GET /agents/mine payload onto the card's shape (same mapping SAAgents uses). */
function toCardAgent(a: any): FixtureAgent {
  return {
    ...a,
    name: a.agent_name || a.name || 'AI Receptionist',
    languages: a.language ? [a.language.split('-')[0].toUpperCase()] : ['EN'],
    // Filled in by the /health call below; '—' until then.
    calls_today: 0,
    bookings_today: 0,
    avg_latency_ms: 0,
    resolution_rate: 0,
  } as FixtureAgent;
}

export default function MyAgent() {
  const [agent, setAgent] = useState<FixtureAgent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [testTarget, setTestTarget] = useState<FixtureAgent | null>(null);
  const [phoneTarget, setPhoneTarget] = useState<FixtureAgent | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        // One request, resolved from the session's clinic_id server-side. No
        // email, no tenant id, no superadmin-only endpoint in the way.
        const data = await fetchWithAuth('/agents/mine');
        if (cancelled) return;
        const mapped = toCardAgent(data);
        setAgent(mapped);
        setLoading(false);

        // Stats after the card is already on screen — a slow or failing health
        // check must not hold up the page or blank it.
        try {
          const h = await fetchWithAuth(`/agents/${mapped.id}/health`);
          if (cancelled) return;
          setAgent(prev => prev ? {
            ...prev,
            calls_today: h.last_24h?.total_calls ?? 0,
            avg_latency_ms: h.latency?.avg_ms ?? 0,
          } : prev);
        } catch { /* stats stay '—' */ }
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message || 'Could not load your agent.');
        setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div style={{ padding: '32px', maxWidth: '560px', margin: '0 auto' }}>
        <div style={{
          background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '14px',
          padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px',
        }}>
          {/* Skeleton in the card's own shape, so the layout does not jump when
              the real card replaces it. */}
          <div className="skeleton" style={{ height: 18, width: 90, borderRadius: 20 }} />
          <div className="skeleton" style={{ height: 44, width: '70%', borderRadius: 8 }} />
          <div className="skeleton" style={{ height: 52, width: '100%', borderRadius: 8 }} />
          <div className="skeleton" style={{ height: 60, width: '100%', borderRadius: 8 }} />
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div style={{ padding: '32px', maxWidth: '560px', margin: '0 auto' }}>
        <div style={{
          background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '14px',
          padding: '40px 32px', textAlign: 'center',
        }}>
          <AlertCircle size={36} color="#ef4444" />
          <h2 style={{ color: '#fff', fontSize: 18, margin: '14px 0 6px' }}>No agent found</h2>
          <p style={{ color: '#888', fontSize: 13, margin: 0 }}>
            {error || 'No agent is configured for this clinic yet.'}
          </p>
          <p style={{ color: '#666', fontSize: 12, marginTop: 10 }}>
            Please contact the Lifodial team to set up your AI receptionist.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: '32px', maxWidth: '560px', margin: '0 auto' }}>
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ fontSize: '22px', fontWeight: 700, color: '#fff', margin: 0, letterSpacing: '-0.02em', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Headphones size={20} color="#3ECF8E" /> My Agent
        </h1>
        <p style={{ fontSize: '13px', color: '#666', margin: '4px 0 0' }}>
          Your AI receptionist, as configured by the Lifodial team. Try it with a
          web or phone call.
        </p>
      </div>

      <AgentCard
        agent={agent}
        readOnly
        onTest={() => setTestTarget(agent)}
        onWebCall={() => setTestTarget(agent)}
        onPhoneCall={() => setPhoneTarget(agent)}
      />

      {testTarget && (
        <Suspense fallback={null}>
          <TestAgentModal agent={testTarget} onClose={() => setTestTarget(null)} />
        </Suspense>
      )}

      {phoneTarget && (
        <PhoneCallModal agent={phoneTarget} onClose={() => setPhoneTarget(null)} />
      )}
    </div>
  );
}
