import { useState, useEffect } from 'react';
import { Phone, Clock, IndianRupee, Activity, Mic, Volume2, Brain, ChevronRight, AlertCircle, CheckCircle2 } from 'lucide-react';
import { API_URL } from '../api/client';

/**
 * MyAgent — Read-only clinic admin dashboard.
 * Shows: agent status, credit balance, recent calls, and voice config.
 * NO editing — all settings are managed by Super Admin.
 */

interface AgentInfo {
  id: string;
  agent_name: string;
  clinic_name: string;
  status: string;
  tts_voice: string;
  tts_language: string;
  tts_model: string;
  stt_model: string;
  llm_model: string;
  first_message: string;
}

interface CreditInfo {
  balance: number;
  rate_per_minute: number;
  total_added: number;
  total_deducted: number;
  is_low: boolean;
  recent_transactions: Array<{
    id: string;
    type: string;
    amount: number;
    balance_after: number;
    description: string;
    created_at: string;
  }>;
}

interface CallRecord {
  id: string;
  call_type: string;
  started_at: string;
  duration_seconds: number;
  status: string;
  outcome: string;
  sentiment: string;
}

const LANG_MAP: Record<string, string> = {
  'hi-IN': '🇮🇳 Hindi', 'en-IN': '🇮🇳 English', 'ta-IN': '🇮🇳 Tamil',
  'ml-IN': '🇮🇳 Malayalam', 'te-IN': '🇮🇳 Telugu', 'kn-IN': '🇮🇳 Kannada',
  'bn-IN': '🇮🇳 Bengali', 'ar-SA': '🇦🇪 Arabic', 'mr-IN': '🇮🇳 Marathi',
};

export default function MyAgent() {
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [credits, setCredits] = useState<CreditInfo | null>(null);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const email = localStorage.getItem('lifodial-email') || '';
      const tenantId = localStorage.getItem('lifodial-tenant-id') || '';

      let myAgent: AgentInfo | null = null;

      // Priority 1: look up by tenant_id if we have it from login
      if (tenantId) {
        const res = await fetch(`${API_URL}/agents`);
        if (res.ok) {
          const agents = await res.json();
          myAgent = agents.find((a: any) => a.tenant_id === tenantId) || null;
        }
      }

      // Priority 2: look up by email (ties to clinic's admin_email)
      if (!myAgent && email) {
        try {
          const res = await fetch(`${API_URL}/agents/mine?email=${encodeURIComponent(email)}`);
          if (res.ok) {
            myAgent = await res.json();
          }
        } catch {
          // email lookup failed — continue to fallback
        }
      }

      // Priority 3: show first agent (dev / demo mode)
      if (!myAgent) {
        const res = await fetch(`${API_URL}/agents`);
        if (res.ok) {
          const agents = await res.json();
          if (agents.length > 0) {
            myAgent = agents[0];
          }
        }
      }

      if (myAgent) {
        setAgent(myAgent);
        // Load credits
        try {
          const tid = (myAgent as any).tenant_id || tenantId;
          if (tid) {
            const creditsRes = await fetch(`${API_URL}/credits/my-balance?tenant_id=${tid}`);
            if (creditsRes.ok) setCredits(await creditsRes.json());
          }
        } catch {}
        // Load recent calls
        try {
          const callsRes = await fetch(`${API_URL}/agents/${myAgent.id}/call-logs?limit=10`);
          if (callsRes.ok) setCalls(await callsRes.json());
        } catch {}
      } else {
        setError(email ? `No agent configured for ${email}` : 'No agent found for your clinic.');
      }
    } catch (e: any) {
      setError(e.message);
    }
    setLoading(false);
  };


  if (loading) {
    return (
      <div style={styles.page}>
        <div style={styles.loadingContainer}>
          <div style={styles.spinner} />
          <p style={{ color: '#888', marginTop: 16 }}>Loading your agent...</p>
        </div>
      </div>
    );
  }

  if (error || !agent) {
    return (
      <div style={styles.page}>
        <div style={styles.errorCard}>
          <AlertCircle size={40} color="#ef4444" />
          <h2 style={{ color: '#fff', marginTop: 16 }}>No Agent Found</h2>
          <p style={{ color: '#888' }}>{error || 'No agent is configured for your clinic yet.'}</p>
          <p style={{ color: '#666', fontSize: 13 }}>
            Please contact the Lifodial team to set up your AI receptionist.
          </p>
        </div>
      </div>
    );
  }

  const totalCalls = calls.length;
  const completedCalls = calls.filter(c => c.status === 'completed').length;
  const totalMinutes = Math.ceil(calls.reduce((s, c) => s + (c.duration_seconds || 0), 0) / 60);

  return (
    <div style={styles.page}>
      {/* Header */}
      <div style={styles.header}>
        <div>
          <h1 style={styles.title}>My AI Receptionist</h1>
          <p style={styles.subtitle}>{agent.clinic_name}</p>
        </div>
        <div style={{
          ...styles.statusBadge,
          backgroundColor: agent.status === 'ACTIVE' ? 'rgba(62,207,142,0.1)' : 'rgba(245,158,11,0.1)',
          color: agent.status === 'ACTIVE' ? '#3ECF8E' : '#F59E0B',
          borderColor: agent.status === 'ACTIVE' ? 'rgba(62,207,142,0.3)' : 'rgba(245,158,11,0.3)',
        }}>
          {agent.status === 'ACTIVE' ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
          {agent.status}
        </div>
      </div>

      {/* Stats Row */}
      <div style={styles.statsRow}>
        <StatCard
          icon={<IndianRupee size={20} />}
          label="Credit Balance"
          value={`₹${(credits?.balance ?? 0).toFixed(2)}`}
          detail={`Rate: ₹${(credits?.rate_per_minute ?? 1.5).toFixed(2)}/min`}
          accent={credits?.is_low ? '#ef4444' : '#3ECF8E'}
          warning={credits?.is_low ? 'Low balance!' : undefined}
        />
        <StatCard
          icon={<Phone size={20} />}
          label="Recent Calls"
          value={String(totalCalls)}
          detail={`${completedCalls} completed`}
          accent="#3B82F6"
        />
        <StatCard
          icon={<Clock size={20} />}
          label="Total Minutes"
          value={String(totalMinutes)}
          detail={`₹${(credits?.total_deducted ?? 0).toFixed(2)} spent`}
          accent="#8B5CF6"
        />
        <StatCard
          icon={<Activity size={20} />}
          label="Agent"
          value={agent.agent_name}
          detail={LANG_MAP[agent.tts_language] || agent.tts_language}
          accent="#F59E0B"
        />
      </div>

      {/* Two Columns */}
      <div style={styles.columns}>
        {/* Left: Voice Config */}
        <div style={styles.card}>
          <h3 style={styles.cardTitle}>Voice Configuration</h3>
          <p style={styles.cardSubtitle}>Read-only — managed by Lifodial team</p>

          <div style={styles.configGrid}>
            <ConfigRow icon={<Mic size={16} />} label="STT Model" value={agent.stt_model || 'saaras:v3'} />
            <ConfigRow icon={<Volume2 size={16} />} label="TTS Voice" value={agent.tts_voice || 'meera'} />
            <ConfigRow icon={<Volume2 size={16} />} label="TTS Model" value={agent.tts_model || 'bulbul:v3'} />
            <ConfigRow icon={<Brain size={16} />} label="LLM" value={agent.llm_model || 'gemini-2.0-flash'} />
          </div>

          {agent.first_message && (
            <div style={styles.firstMsgBox}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#888', textTransform: 'uppercase' as const, letterSpacing: '0.05em', marginBottom: 6 }}>
                Greeting Message
              </div>
              <p style={{ color: '#ccc', fontSize: 13, lineHeight: 1.5, margin: 0 }}>
                "{agent.first_message}"
              </p>
            </div>
          )}
        </div>

        {/* Right: Recent Calls */}
        <div style={styles.card}>
          <h3 style={styles.cardTitle}>Recent Calls</h3>
          <p style={styles.cardSubtitle}>Last 10 voice interactions</p>

          {calls.length === 0 ? (
            <div style={{ textAlign: 'center' as const, padding: '40px 0', color: '#555' }}>
              <Phone size={32} color="#333" />
              <p style={{ marginTop: 12 }}>No calls yet</p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 6, marginTop: 12 }}>
              {calls.slice(0, 10).map(call => (
                <div key={call.id} style={styles.callRow}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{
                      width: 8, height: 8, borderRadius: '50%',
                      backgroundColor: call.status === 'completed' ? '#3ECF8E' : call.status === 'failed' ? '#ef4444' : '#F59E0B',
                    }} />
                    <div>
                      <div style={{ fontSize: 13, color: '#ddd', fontWeight: 500 }}>
                        {call.call_type === 'web' ? '🌐 Web Call' : '📞 Phone Call'}
                      </div>
                      <div style={{ fontSize: 11, color: '#666' }}>
                        {call.started_at ? new Date(call.started_at).toLocaleString() : '—'}
                      </div>
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' as const }}>
                    <div style={{ fontSize: 13, color: '#aaa', fontWeight: 500 }}>
                      {call.duration_seconds ? `${Math.floor(call.duration_seconds / 60)}m ${call.duration_seconds % 60}s` : '—'}
                    </div>
                    <div style={{ fontSize: 11, color: '#555', textTransform: 'capitalize' as const }}>
                      {call.status}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Credit Transactions */}
      {credits && credits.recent_transactions.length > 0 && (
        <div style={{ ...styles.card, marginTop: 20 }}>
          <h3 style={styles.cardTitle}>Credit History</h3>
          <p style={styles.cardSubtitle}>Recent balance changes</p>
          <div style={{ display: 'flex', flexDirection: 'column' as const, gap: 4, marginTop: 12 }}>
            {credits.recent_transactions.map(txn => (
              <div key={txn.id} style={styles.txnRow}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{
                    width: 28, height: 28, borderRadius: 6,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    backgroundColor: txn.amount > 0 ? 'rgba(62,207,142,0.1)' : 'rgba(239,68,68,0.1)',
                    color: txn.amount > 0 ? '#3ECF8E' : '#ef4444',
                    fontSize: 14,
                  }}>
                    {txn.amount > 0 ? '+' : '−'}
                  </div>
                  <div>
                    <div style={{ fontSize: 13, color: '#ddd' }}>{txn.description || txn.type}</div>
                    <div style={{ fontSize: 11, color: '#555' }}>
                      {txn.created_at ? new Date(txn.created_at).toLocaleString() : '—'}
                    </div>
                  </div>
                </div>
                <div style={{
                  fontSize: 14, fontWeight: 600,
                  color: txn.amount > 0 ? '#3ECF8E' : '#ef4444',
                }}>
                  {txn.amount > 0 ? '+' : ''}₹{Math.abs(txn.amount).toFixed(2)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}


// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({ icon, label, value, detail, accent, warning }: {
  icon: React.ReactNode; label: string; value: string; detail: string; accent: string; warning?: string;
}) {
  return (
    <div style={{
      ...styles.statCard,
      borderColor: warning ? 'rgba(239,68,68,0.3)' : '#1A1A1A',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ color: accent, opacity: 0.8 }}>{icon}</div>
        {warning && (
          <span style={{ fontSize: 10, color: '#ef4444', fontWeight: 600, background: 'rgba(239,68,68,0.1)', padding: '2px 8px', borderRadius: 4 }}>
            {warning}
          </span>
        )}
      </div>
      <div style={{ fontSize: 24, fontWeight: 700, color: '#fff', letterSpacing: '-0.02em' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>{label}</div>
      <div style={{ fontSize: 11, color: '#555', marginTop: 2 }}>{detail}</div>
    </div>
  );
}

function ConfigRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={styles.configRow}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#888' }}>
        {icon}
        <span style={{ fontSize: 13 }}>{label}</span>
      </div>
      <span style={{ fontSize: 13, color: '#ddd', fontWeight: 500 }}>{value}</span>
    </div>
  );
}


// ── Styles ────────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  page: {
    padding: '28px 32px',
    minHeight: '100vh',
    background: '#0A0A0A',
    fontFamily: "'Inter', 'Segoe UI', sans-serif",
    maxWidth: 1200,
    margin: '0 auto',
  },
  loadingContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '50vh',
  },
  spinner: {
    width: 40, height: 40,
    border: '3px solid #1A1A1A',
    borderTop: '3px solid #3ECF8E',
    borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
  errorCard: {
    textAlign: 'center',
    padding: 60,
    background: '#111',
    borderRadius: 16,
    border: '1px solid #1A1A1A',
    maxWidth: 500,
    margin: '80px auto',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 28,
  },
  title: {
    fontSize: 24,
    fontWeight: 700,
    color: '#fff',
    letterSpacing: '-0.02em',
    margin: 0,
  },
  subtitle: {
    fontSize: 14,
    color: '#888',
    margin: '4px 0 0',
  },
  statusBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '6px 14px',
    borderRadius: 8,
    fontSize: 12,
    fontWeight: 600,
    border: '1px solid',
    letterSpacing: '0.03em',
  },
  statsRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: 16,
    marginBottom: 24,
  },
  statCard: {
    background: '#111',
    borderRadius: 12,
    padding: '18px 20px',
    border: '1px solid #1A1A1A',
  },
  columns: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 20,
  },
  card: {
    background: '#111',
    borderRadius: 14,
    padding: '22px 24px',
    border: '1px solid #1A1A1A',
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: 600,
    color: '#fff',
    margin: 0,
  },
  cardSubtitle: {
    fontSize: 12,
    color: '#555',
    margin: '4px 0 0',
  },
  configGrid: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    marginTop: 16,
  },
  configRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 14px',
    background: '#0D0D0D',
    borderRadius: 8,
    border: '1px solid #1A1A1A',
  },
  firstMsgBox: {
    marginTop: 16,
    padding: '14px 16px',
    background: '#0D0D0D',
    borderRadius: 8,
    border: '1px solid #1A1A1A',
  },
  callRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 14px',
    background: '#0D0D0D',
    borderRadius: 8,
    border: '1px solid #1A1A1A',
  },
  txnRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 14px',
    background: '#0D0D0D',
    borderRadius: 8,
    border: '1px solid #1A1A1A',
  },
};
