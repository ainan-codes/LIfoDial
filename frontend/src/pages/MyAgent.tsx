import React, { useState, useEffect } from 'react';

import {
  Phone, Clock, IndianRupee, Activity, Mic, Volume2, Brain,
  AlertCircle, CheckCircle2, FileText, Wrench, BarChart2, Settings,
  ChevronDown, ChevronUp, Download, Zap, Shield, RefreshCw,
  PhoneMissed, PlayCircle, Globe, Lock, Bell,
} from 'lucide-react';
import fetchWithAuth from '../api/client';
import { FIXTURE_CALL_LOGS, FIXTURE_APPOINTMENTS } from '../fixtures/data';

/**
 * MyAgent — Vapi-style tabbed agent dashboard.
 * Tabs: Assistant | Logs | Tools | Analysis | Advanced
 * ALL existing content preserved, zero removals.
 */

// ── Types ─────────────────────────────────────────────────────────────────────

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
  system_prompt?: string;
  llm_temperature?: number;
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

type Tab = 'assistant' | 'logs' | 'tools' | 'analysis' | 'advanced';

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'assistant', label: 'Assistant',  icon: Mic },
  { id: 'logs',      label: 'Logs',       icon: FileText },
  { id: 'tools',     label: 'Tools',      icon: Wrench },
  { id: 'analysis',  label: 'Analysis',   icon: BarChart2 },
  { id: 'advanced',  label: 'Advanced',   icon: Settings },
];

// ── Main Component ─────────────────────────────────────────────────────────────

export default function MyAgent() {
  const [activeTab, setActiveTab] = useState<Tab>('assistant');
  const [agent, setAgent] = useState<AgentInfo | null>(null);
  const [credits, setCredits] = useState<CreditInfo | null>(null);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    setLoading(true);
    setError('');
    try {
      const email = localStorage.getItem('lifodial-email') || '';
      const tenantId = localStorage.getItem('lifodial-tenant-id') || '';
      let myAgent: AgentInfo | null = null;

      if (tenantId) {
        try {
          const agents = await fetchWithAuth('/agents');
          myAgent = agents.find((a: any) => a.tenant_id === tenantId) || null;
        } catch {}
      }
      if (!myAgent && email) {
        try {
          myAgent = await fetchWithAuth(`/agents/mine?email=${encodeURIComponent(email)}`);
        } catch {}
      }
      if (!myAgent) {
        try {
          const agents = await fetchWithAuth('/agents');
          if (agents.length > 0) myAgent = agents[0];
        } catch {}
      }

      if (myAgent) {
        setAgent(myAgent);
        try {
          const tid = (myAgent as any).tenant_id || tenantId;
          if (tid) {
            setCredits(await fetchWithAuth(`/credits/my-balance?tenant_id=${tid}`));
          }
        } catch {}
        try {
          setCalls(await fetchWithAuth(`/agents/${myAgent.id}/call-logs?limit=10`));
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
          <p style={{ color: '#666', fontSize: 13 }}>Please contact the Lifodial team to set up your AI receptionist.</p>
        </div>
      </div>
    );
  }

  const totalCalls     = calls.length;
  const completedCalls = calls.filter(c => c.status === 'completed').length;
  const totalMinutes   = Math.ceil(calls.reduce((s, c) => s + (c.duration_seconds || 0), 0) / 60);

  return (
    <div style={{ minHeight: '100vh', background: '#0A0A0A', fontFamily: "'Inter', 'Segoe UI', sans-serif" }}>

      {/* ── Vapi-style Header ─────────────────────────────────────────── */}
      <div style={{
        borderBottom: '1px solid #1A1A1A',
        background: '#0D0D0D',
        padding: '0 32px',
      }}>
        {/* Agent identity row */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          paddingTop: 20,
          paddingBottom: 14,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Agent avatar */}
            <div style={{
              width: 40, height: 40, borderRadius: 10,
              background: 'linear-gradient(135deg, #3ECF8E22, #3ECF8E44)',
              border: '1px solid #3ECF8E44',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Mic size={18} color="#3ECF8E" />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <h1 style={{ fontSize: 18, fontWeight: 700, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>
                  {agent.agent_name}
                </h1>
                {/* Status badge */}
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  padding: '2px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                  backgroundColor: agent.status === 'ACTIVE' ? 'rgba(62,207,142,0.1)' : 'rgba(245,158,11,0.1)',
                  color: agent.status === 'ACTIVE' ? '#3ECF8E' : '#F59E0B',
                  border: `1px solid ${agent.status === 'ACTIVE' ? 'rgba(62,207,142,0.3)' : 'rgba(245,158,11,0.3)'}`,
                }}>
                  {agent.status === 'ACTIVE' ? <CheckCircle2 size={10} /> : <AlertCircle size={10} />}
                  {agent.status === 'ACTIVE' ? 'Published' : agent.status}
                </span>
              </div>
              <p style={{ fontSize: 12, color: '#555', margin: '2px 0 0' }}>{agent.clinic_name}</p>
            </div>
          </div>

          {/* Right: Quick stats */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
            <QuickStat label="Balance" value={`₹${(credits?.balance ?? 0).toFixed(2)}`} accent={credits?.is_low ? '#ef4444' : '#3ECF8E'} />
            <QuickStat label="Recent Calls" value={String(totalCalls)} accent="#3B82F6" />
            <QuickStat label="Minutes Used" value={String(totalMinutes)} accent="#8B5CF6" />
            <div style={{ width: 1, height: 32, background: '#1A1A1A' }} />
            <button
              onClick={loadData}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '7px 14px', borderRadius: 8,
                background: 'transparent', border: '1px solid #222',
                color: '#888', fontSize: 12, fontWeight: 500, cursor: 'pointer',
              }}
            >
              <RefreshCw size={13} /> Refresh
            </button>
          </div>
        </div>

        {/* ── Tab bar ─────────────────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 4 }}>
          {TABS.map(tab => {
            const isActive = activeTab === tab.id;
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 7,
                  padding: '9px 16px',
                  borderRadius: '8px 8px 0 0',
                  background: isActive ? '#111' : 'transparent',
                  border: isActive ? '1px solid #1A1A1A' : '1px solid transparent',
                  borderBottom: isActive ? '1px solid #111' : '1px solid transparent',
                  marginBottom: isActive ? -1 : 0,
                  color: isActive ? '#3ECF8E' : '#555',
                  fontSize: 13, fontWeight: isActive ? 600 : 500,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
                onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLElement).style.color = '#aaa'; }}
                onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLElement).style.color = '#555'; }}
              >
                <Icon size={14} />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Tab Content ───────────────────────────────────────────────── */}
      <div style={{ padding: '28px 32px', maxWidth: 1200, margin: '0 auto' }}>

        {/* ══ ASSISTANT TAB ═══════════════════════════════════════════════ */}
        {activeTab === 'assistant' && (
          <div>
            {/* Stats Row */}
            <div style={styles.statsRow}>
              <StatCard icon={<IndianRupee size={20} />} label="Credit Balance"
                value={`₹${(credits?.balance ?? 0).toFixed(2)}`}
                detail={`Rate: ₹${(credits?.rate_per_minute ?? 1.5).toFixed(2)}/min`}
                accent={credits?.is_low ? '#ef4444' : '#3ECF8E'} warning={credits?.is_low ? 'Low balance!' : undefined} />
              <StatCard icon={<Phone size={20} />} label="Recent Calls"
                value={String(totalCalls)} detail={`${completedCalls} completed`} accent="#3B82F6" />
              <StatCard icon={<Clock size={20} />} label="Total Minutes"
                value={String(totalMinutes)} detail={`₹${(credits?.total_deducted ?? 0).toFixed(2)} spent`} accent="#8B5CF6" />
              <StatCard icon={<Activity size={20} />} label="Agent"
                value={agent.agent_name} detail={LANG_MAP[agent.tts_language] || agent.tts_language} accent="#F59E0B" />
            </div>

            {/* Two Columns: Voice Config + Recent Calls */}
            <div style={styles.columns}>
              {/* Voice Config */}
              <div style={styles.card}>
                <h3 style={styles.cardTitle}>Voice Configuration</h3>
                <p style={styles.cardSubtitle}>Read-only — managed by Lifodial team</p>
                <div style={styles.configGrid}>
                  <ConfigRow icon={<Mic size={16} />} label="STT Model" value={agent.stt_model || 'saaras:v3'} />
                  <ConfigRow icon={<Volume2 size={16} />} label="TTS Voice" value={agent.tts_voice || 'meera'} />
                  <ConfigRow icon={<Volume2 size={16} />} label="TTS Model" value={agent.tts_model || 'bulbul:v3'} />
                  <ConfigRow icon={<Brain size={16} />} label="LLM" value={agent.llm_model || 'gemini-2.5-flash'} />
                </div>
                {agent.first_message && (
                  <div style={styles.firstMsgBox}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#888', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
                      Greeting Message
                    </div>
                    <p style={{ color: '#ccc', fontSize: 13, lineHeight: 1.5, margin: 0 }}>
                      "{agent.first_message}"
                    </p>
                  </div>
                )}
              </div>

              {/* Recent Calls */}
              <div style={styles.card}>
                <h3 style={styles.cardTitle}>Recent Calls</h3>
                <p style={styles.cardSubtitle}>Last 10 voice interactions</p>
                {calls.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: '#555' }}>
                    <Phone size={32} color="#333" />
                    <p style={{ marginTop: 12 }}>No calls yet</p>
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 12 }}>
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
                        <div style={{ textAlign: 'right' }}>
                          <div style={{ fontSize: 13, color: '#aaa', fontWeight: 500 }}>
                            {call.duration_seconds ? `${Math.floor(call.duration_seconds / 60)}m ${call.duration_seconds % 60}s` : '—'}
                          </div>
                          <div style={{ fontSize: 11, color: '#555', textTransform: 'capitalize' }}>{call.status}</div>
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
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 12 }}>
                  {credits.recent_transactions.map(txn => (
                    <div key={txn.id} style={styles.txnRow}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <div style={{
                          width: 28, height: 28, borderRadius: 6,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          backgroundColor: txn.amount > 0 ? 'rgba(62,207,142,0.1)' : 'rgba(239,68,68,0.1)',
                          color: txn.amount > 0 ? '#3ECF8E' : '#ef4444', fontSize: 14,
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
                      <div style={{ fontSize: 14, fontWeight: 600, color: txn.amount > 0 ? '#3ECF8E' : '#ef4444' }}>
                        {txn.amount > 0 ? '+' : ''}₹{Math.abs(txn.amount).toFixed(2)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ══ LOGS TAB ════════════════════════════════════════════════════ */}
        {activeTab === 'logs' && <LogsTab calls={calls} />}

        {/* ══ TOOLS TAB ═══════════════════════════════════════════════════ */}
        {activeTab === 'tools' && <ToolsTab agent={agent} />}

        {/* ══ ANALYSIS TAB ════════════════════════════════════════════════ */}
        {activeTab === 'analysis' && <AnalysisTab calls={calls} />}

        {/* ══ ADVANCED TAB ════════════════════════════════════════════════ */}
        {activeTab === 'advanced' && <AdvancedTab agent={agent} credits={credits} />}

      </div>
    </div>
  );
}


// ── LOGS TAB ──────────────────────────────────────────────────────────────────

function LogsTab({ calls }: { calls: CallRecord[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState('ALL');

  const logs = FIXTURE_CALL_LOGS;
  const filtered = logs.filter(l => filterStatus === 'ALL' || l.status === filterStatus);
  const toggle = (id: string) => setExpandedId(prev => prev === id ? null : id);

  const handleExport = () => {
    const headers = ['ID', 'Phone', 'Date', 'Duration', 'Intent', 'Language', 'Status'];
    const rows = logs.map(l => [l.id, l.phone, l.date, l.duration, l.intent, l.language, l.status].join(','));
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'call_logs.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  const selectStyle: React.CSSProperties = {
    padding: '7px 11px', borderRadius: 8, fontSize: 13,
    backgroundColor: '#111', border: '1px solid #1A1A1A',
    color: '#aaa', outline: 'none', cursor: 'pointer',
  };

  const STATUS_COLORS: Record<string, { color: string; bg: string }> = {
    Booked:      { color: '#3ECF8E', bg: 'rgba(62,207,142,0.1)' },
    Transferred: { color: '#8B5CF6', bg: 'rgba(139,92,246,0.1)' },
    Resolved:    { color: '#888',    bg: '#1A1A1A' },
    Failed:      { color: '#ef4444', bg: 'rgba(239,68,68,0.1)' },
    Pending:     { color: '#F59E0B', bg: 'rgba(245,158,11,0.1)' },
  };

  return (
    <div>
      {/* Filter bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
        <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} style={selectStyle}>
          <option value="ALL">All Statuses</option>
          {['Booked', 'Resolved', 'Transferred', 'Failed', 'Pending'].map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {filterStatus !== 'ALL' && (
          <button onClick={() => setFilterStatus('ALL')} style={{ fontSize: 12, color: '#3ECF8E', background: 'none', border: 'none', cursor: 'pointer' }}>
            Clear
          </button>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 12, color: '#555' }}>{filtered.length} call{filtered.length !== 1 ? 's' : ''}</span>
        <button onClick={handleExport} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', borderRadius: 8, fontSize: 12, fontWeight: 500, color: '#888', backgroundColor: 'transparent', border: '1px solid #1A1A1A', cursor: 'pointer' }}>
          <Download size={13} /> Export CSV
        </button>
      </div>

      <div style={{ background: '#111', borderRadius: 14, border: '1px solid #1A1A1A', overflow: 'hidden' }}>
        {filtered.length === 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '60px 0', color: '#333' }}>
            <PhoneMissed size={32} />
            <p style={{ marginTop: 12, color: '#555' }}>No calls match filters</p>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ backgroundColor: '#0D0D0D' }}>
                {['Caller', 'Date & Time', 'Duration', 'Intent', 'Language', 'Status', 'Transcript'].map(h => (
                  <th key={h} style={{ padding: '10px 16px', textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#444', borderBottom: '1px solid #1A1A1A', fontWeight: 500 }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((call, i) => {
                const isOpen = expandedId === call.id;
                const sc = STATUS_COLORS[call.status] ?? { color: '#888', bg: '#1A1A1A' };
                return (
                  <React.Fragment key={call.id}>
                    <tr
                      onClick={() => toggle(call.id)}
                      style={{ borderBottom: !isOpen && i < filtered.length - 1 ? '1px solid #161616' : 'none', cursor: 'pointer', backgroundColor: isOpen ? '#141414' : 'transparent' }}
                      onMouseEnter={e => { if (!isOpen) (e.currentTarget as HTMLElement).style.backgroundColor = '#131313'; }}
                      onMouseLeave={e => { if (!isOpen) (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                    >
                      <td style={{ padding: '12px 16px', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: '#ddd' }}>{call.phone}</td>
                      <td style={{ padding: '12px 16px', fontSize: 12, color: '#888' }}>{call.date}</td>
                      <td style={{ padding: '12px 16px', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: '#888' }}>{call.duration}</td>
                      <td style={{ padding: '12px 16px' }}>
                        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 9999, color: '#3ECF8E', backgroundColor: 'rgba(62,207,142,0.1)' }}>{call.intent}</span>
                      </td>
                      <td style={{ padding: '12px 16px', fontSize: 12, color: '#888' }}>{call.flag} {call.language}</td>
                      <td style={{ padding: '12px 16px' }}>
                        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 9999, color: sc.color, backgroundColor: sc.bg }}>{call.status}</span>
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <button
                          onClick={e => { e.stopPropagation(); toggle(call.id); }}
                          style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px', borderRadius: 6, fontSize: 12, fontWeight: 500, backgroundColor: isOpen ? 'rgba(62,207,142,0.1)' : 'transparent', border: `1px solid ${isOpen ? 'rgba(62,207,142,0.3)' : '#1A1A1A'}`, color: isOpen ? '#3ECF8E' : '#555', cursor: 'pointer' }}
                        >
                          {isOpen ? <><ChevronUp size={11} />Hide</> : <><ChevronDown size={11} />View</>}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={7} style={{ padding: 0 }}>
                          <div style={{ padding: '16px 24px', backgroundColor: '#0D0D0D', borderBottom: '1px solid #1A1A1A' }}>
                            <p style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#444', marginBottom: 12 }}>Call Transcript</p>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 640 }}>
                              {call.transcript.map((msg, idx) => (
                                <div key={idx} style={{ display: 'flex', justifyContent: msg.role === 'ai' ? 'flex-end' : 'flex-start' }}>
                                  <div style={{ maxWidth: '70%', padding: '8px 12px', borderRadius: msg.role === 'ai' ? '12px 12px 2px 12px' : '12px 12px 12px 2px', backgroundColor: msg.role === 'ai' ? 'rgba(62,207,142,0.08)' : '#131313', border: `1px solid ${msg.role === 'ai' ? 'rgba(62,207,142,0.2)' : '#1A1A1A'}` }}>
                                    <p style={{ fontSize: 13, margin: 0, color: msg.role === 'ai' ? '#3ECF8E' : '#ccc' }}>{msg.text}</p>
                                    <p style={{ fontSize: 10, color: '#444', marginTop: 4, textAlign: msg.role === 'ai' ? 'right' : 'left' }}>
                                      {msg.role === 'ai' ? 'Agent' : 'Patient'} · {msg.time}
                                    </p>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


// ── TOOLS TAB ─────────────────────────────────────────────────────────────────

function ToolsTab({ agent }: { agent: AgentInfo }) {
  const tools = [
    {
      id: 'appointment_booking',
      name: 'Book Appointment',
      icon: '📅',
      status: 'active',
      description: 'Automatically saves confirmed appointments to PostgreSQL database and fires Google Sheets sync.',
      trigger: 'Patient confirms booking slot',
      latency: '0ms (background task)',
    },
    {
      id: 'google_sheets',
      name: 'Google Sheets Sync',
      icon: '📊',
      status: 'active',
      description: 'Sends appointment data to clinic\'s Google Sheet via Apps Script webhook after booking.',
      trigger: 'After appointment confirmed',
      latency: 'Async — no call latency',
    },
    {
      id: 'doctor_lookup',
      name: 'Doctor Lookup',
      icon: '🩺',
      status: 'active',
      description: 'Matches patient\'s specialization request to available doctors from clinic database.',
      trigger: 'Patient mentions specialty/doctor',
      latency: 'In-memory cache — ~0ms',
    },
    {
      id: 'emergency_transfer',
      name: 'Emergency Transfer',
      icon: '🚨',
      status: 'active',
      description: 'Detects emergency keywords and immediately routes call for urgent handling.',
      trigger: '"chest pain", "emergency", "accident", "unconscious"',
      latency: 'Immediate',
    },
    {
      id: 'language_detection',
      name: 'Auto Language Detection',
      icon: '🌐',
      status: 'active',
      description: 'Automatically switches response language to match patient\'s spoken language.',
      trigger: 'Every patient turn',
      latency: 'STT-level — ~0ms overhead',
    },
    {
      id: 'credit_deduction',
      name: 'Call Billing',
      icon: '₹',
      status: 'active',
      description: 'Deducts call credits from clinic balance based on call duration after disconnect.',
      trigger: 'Room disconnect event',
      latency: 'Post-call — no impact',
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h2 style={{ fontSize: 16, fontWeight: 600, color: '#fff', margin: 0 }}>Active Tools & Functions</h2>
        <p style={{ fontSize: 13, color: '#555', marginTop: 4 }}>All capabilities wired into the {agent.agent_name} voice pipeline</p>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {tools.map(tool => (
          <div key={tool.id} style={{ background: '#111', borderRadius: 12, padding: '18px 20px', border: `1px solid ${tool.status === 'active' ? 'rgba(62,207,142,0.15)' : '#1A1A1A'}` }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{ width: 36, height: 36, borderRadius: 8, background: '#0D0D0D', border: '1px solid #1A1A1A', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>
                  {tool.icon}
                </div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: '#fff' }}>{tool.name}</div>
                  <span style={{ fontSize: 10, fontWeight: 600, padding: '1px 7px', borderRadius: 9999, backgroundColor: 'rgba(62,207,142,0.1)', color: '#3ECF8E' }}>
                    {tool.status}
                  </span>
                </div>
              </div>
              <Zap size={14} color="#3ECF8E" />
            </div>
            <p style={{ fontSize: 12, color: '#666', lineHeight: 1.5, margin: '0 0 12px' }}>{tool.description}</p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 11, color: '#444' }}>Trigger</span>
                <span style={{ fontSize: 11, color: '#888', fontStyle: 'italic' }}>{tool.trigger}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 11, color: '#444' }}>Latency impact</span>
                <span style={{ fontSize: 11, color: '#3ECF8E', fontWeight: 600 }}>{tool.latency}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}


// ── ANALYSIS TAB ──────────────────────────────────────────────────────────────

function AnalysisTab({ calls }: { calls: CallRecord[] }) {
  const totalCalls    = FIXTURE_CALL_LOGS.length;
  const booked        = FIXTURE_APPOINTMENTS.filter(a => a.status === 'CONFIRMED').length;
  const resolved      = FIXTURE_CALL_LOGS.filter(l => l.status === 'Booked' || l.status === 'Resolved').length;
  const resolutionPct = `${Math.round((resolved / totalCalls) * 100)}%`;

  const intentCounts = FIXTURE_CALL_LOGS.reduce((acc, l) => {
    acc[l.intent] = (acc[l.intent] ?? 0) + 1; return acc;
  }, {} as Record<string, number>);

  const langCounts = FIXTURE_CALL_LOGS.reduce((acc, l) => {
    acc[l.language] = (acc[l.language] ?? 0) + 1; return acc;
  }, {} as Record<string, number>);

  const days = [
    { day: 'Mon', value: 2 }, { day: 'Tue', value: 4 }, { day: 'Wed', value: 3 },
    { day: 'Thu', value: 6 }, { day: 'Fri', value: 5 }, { day: 'Sat', value: totalCalls }, { day: 'Sun', value: 0 },
  ];
  const maxDay = Math.max(...days.map(d => d.value));

  const kpiCards = [
    { label: 'Total Calls',      value: totalCalls,    icon: Phone,       accent: '#3B82F6' },
    { label: 'Apts Booked',      value: booked,        icon: CheckCircle2, accent: '#3ECF8E' },
    { label: 'Resolution Rate',  value: resolutionPct, icon: Activity,    accent: '#8B5CF6' },
    { label: 'Avg Handle Time',  value: '2:18',        icon: Clock,       accent: '#F59E0B' },
  ];

  const INTENT_COLORS: Record<string, string> = {
    Appointment: '#3ECF8E', Emergency: '#ef4444', 'General Query': '#8B5CF6', Cancellation: '#F59E0B',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* KPI row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
        {kpiCards.map(k => (
          <div key={k.label} style={{ background: '#111', borderRadius: 12, padding: '18px 20px', border: '1px solid #1A1A1A' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#555', fontWeight: 500 }}>{k.label}</span>
              <k.icon size={16} color={k.accent} />
            </div>
            <div style={{ fontSize: 30, fontWeight: 700, color: k.accent, letterSpacing: '-0.02em' }}>{k.value}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* Call Volume Chart */}
        <div style={{ background: '#111', borderRadius: 12, border: '1px solid #1A1A1A' }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid #1A1A1A' }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#fff' }}>Call Volume — Last 7 Days</h3>
          </div>
          <div style={{ padding: '20px', display: 'flex', alignItems: 'flex-end', gap: 8, height: 140 }}>
            {days.map(d => {
              const pct = maxDay > 0 ? Math.round((d.value / maxDay) * 100) : 0;
              return (
                <div key={d.day} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 11, color: '#555', fontWeight: 500 }}>{d.value || ''}</span>
                  <div style={{ width: '100%', height: 80, position: 'relative', borderRadius: 4, backgroundColor: '#1A1A1A' }}>
                    <div style={{ position: 'absolute', bottom: 0, left: 0, right: 0, height: `${pct}%`, borderRadius: 4, backgroundColor: '#3ECF8E', minHeight: d.value > 0 ? 4 : 0, transition: 'height 0.6s ease' }} />
                  </div>
                  <span style={{ fontSize: 11, color: '#444' }}>{d.day}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Intent Breakdown */}
        <div style={{ background: '#111', borderRadius: 12, border: '1px solid #1A1A1A' }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid #1A1A1A' }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#fff' }}>Intent Breakdown</h3>
          </div>
          <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {Object.entries(intentCounts).map(([intent, count]) => {
              const pct = Math.round((count / totalCalls) * 100);
              const clr = INTENT_COLORS[intent] ?? '#888';
              return (
                <div key={intent}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 13, color: '#aaa', fontWeight: 500 }}>{intent}</span>
                    <span style={{ fontSize: 12, color: '#555', fontFamily: "'JetBrains Mono', monospace" }}>{count} · {pct}%</span>
                  </div>
                  <div style={{ height: 6, borderRadius: 3, backgroundColor: '#1A1A1A', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${pct}%`, borderRadius: 3, backgroundColor: clr, transition: 'width 0.5s ease' }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Language distribution */}
        <div style={{ background: '#111', borderRadius: 12, border: '1px solid #1A1A1A' }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid #1A1A1A' }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#fff' }}>Language Distribution</h3>
          </div>
          <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {Object.entries(langCounts).map(([lang, count]) => {
              const pct = Math.round((count / totalCalls) * 100);
              const flags: Record<string, string> = { Hindi: '🇮🇳', English: '🇬🇧', Tamil: '🇮🇳' };
              return (
                <div key={lang}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 13, color: '#aaa', fontWeight: 500 }}>{flags[lang] ?? ''} {lang}</span>
                    <span style={{ fontSize: 12, color: '#555', fontFamily: "'JetBrains Mono', monospace" }}>{count} · {pct}%</span>
                  </div>
                  <div style={{ height: 6, borderRadius: 3, backgroundColor: '#1A1A1A', overflow: 'hidden' }}>
                    <div style={{ height: '100%', width: `${pct}%`, borderRadius: 3, backgroundColor: '#3ECF8E', transition: 'width 0.5s ease' }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* AI Impact Card */}
        <div style={{ background: 'rgba(62,207,142,0.04)', borderRadius: 12, border: '1px solid rgba(62,207,142,0.15)' }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid rgba(62,207,142,0.15)' }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#3ECF8E' }}>Receptionist Impact</h3>
          </div>
          <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {[
              { label: 'Calls fully resolved by AI', value: resolutionPct },
              { label: 'Languages handled',           value: `${Object.keys(langCounts).length}` },
              { label: 'Appointments booked (no staff)', value: `${booked}` },
              { label: 'Avg response time',           value: '< 3 sec' },
            ].map(item => (
              <div key={item.label} style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 13, color: 'rgba(62,207,142,0.7)' }}>{item.label}</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: '#3ECF8E', fontFamily: "'JetBrains Mono', monospace" }}>{item.value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}


// ── ADVANCED TAB ──────────────────────────────────────────────────────────────

function AdvancedTab({ agent, credits }: { agent: AgentInfo; credits: CreditInfo | null }) {
  const sections = [
    {
      title: 'Model Configuration',
      icon: Brain,
      rows: [
        { label: 'LLM Model',         value: agent.llm_model || 'gemini-2.0-flash' },
        { label: 'Temperature',       value: String(agent.llm_temperature ?? 0.3) },
        { label: 'Max Output Tokens', value: '120 (voice-optimised)' },
        { label: 'STT Model',         value: agent.stt_model || 'saaras:v2' },
        { label: 'TTS Model',         value: agent.tts_model || 'bulbul:v3' },
        { label: 'TTS Voice',         value: agent.tts_voice || 'priya' },
      ],
    },
    {
      title: 'Latency & Performance',
      icon: Activity,
      rows: [
        { label: 'HTTP Client',       value: 'Persistent HTTP/2 (shared)' },
        { label: 'VAD Silence',       value: '250ms (optimised)' },
        { label: 'Prefix Padding',    value: '100ms' },
        { label: 'TTS Preprocessing', value: 'Disabled (speed)' },
        { label: 'TTS Pace',          value: '1.05× (faster speech)' },
        { label: 'Sheets Sync',       value: 'asyncio.create_task (0ms block)' },
      ],
    },
    {
      title: 'Security & Access',
      icon: Shield,
      rows: [
        { label: 'Authentication',  value: 'JWT Bearer Token' },
        { label: 'Data Isolation',  value: 'Per-tenant DB rows' },
        { label: 'Phone Masking',   value: 'Last 4 digits visible' },
        { label: 'Call Encryption', value: 'LiveKit DTLS/SRTP' },
      ],
    },
    {
      title: 'Billing & Credits',
      icon: IndianRupee,
      rows: [
        { label: 'Rate',          value: `₹${(credits?.rate_per_minute ?? 1.5).toFixed(2)}/min` },
        { label: 'Total Added',   value: `₹${(credits?.total_added ?? 0).toFixed(2)}` },
        { label: 'Total Spent',   value: `₹${(credits?.total_deducted ?? 0).toFixed(2)}` },
        { label: 'Current Balance', value: `₹${(credits?.balance ?? 0).toFixed(2)}` },
        { label: 'Status',        value: credits?.is_low ? '⚠️ Low balance' : '✅ Sufficient' },
      ],
    },
    {
      title: 'Notification Settings',
      icon: Bell,
      rows: [
        { label: 'Low Balance Alert', value: 'Enabled' },
        { label: 'Booking Alert',     value: 'Google Sheets Webhook' },
        { label: 'Emergency Alert',   value: 'Transfer to forwarding number' },
      ],
    },
    {
      title: 'Supported Languages',
      icon: Globe,
      rows: [
        { label: 'Primary',     value: LANG_MAP[agent.tts_language] || agent.tts_language },
        { label: 'Auto-detect', value: 'Hindi, English, Tamil, Malayalam, Telugu, Kannada, Bengali, Arabic, Marathi' },
        { label: 'Barge-in',    value: 'Enabled (patient can interrupt AI)' },
      ],
    },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
      {sections.map(sec => (
        <div key={sec.title} style={{ background: '#111', borderRadius: 12, border: '1px solid #1A1A1A', overflow: 'hidden' }}>
          <div style={{ padding: '14px 20px', borderBottom: '1px solid #1A1A1A', display: 'flex', alignItems: 'center', gap: 8 }}>
            <sec.icon size={15} color="#555" />
            <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: '#fff' }}>{sec.title}</h3>
            <Lock size={11} color="#333" style={{ marginLeft: 'auto' }} />
          </div>
          <div style={{ padding: '12px 0' }}>
            {sec.rows.map(row => (
              <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 20px' }}>
                <span style={{ fontSize: 12, color: '#555' }}>{row.label}</span>
                <span style={{ fontSize: 12, color: '#aaa', fontWeight: 500, textAlign: 'right', maxWidth: '55%' }}>{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}


// ── Shared Sub-components ─────────────────────────────────────────────────────

function QuickStat({ label, value, accent }: { label: string; value: string; accent: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 16, fontWeight: 700, color: accent, letterSpacing: '-0.02em' }}>{value}</div>
      <div style={{ fontSize: 10, color: '#444', marginTop: 1 }}>{label}</div>
    </div>
  );
}

function StatCard({ icon, label, value, detail, accent, warning }: {
  icon: React.ReactNode; label: string; value: string; detail: string; accent: string; warning?: string;
}) {
  return (
    <div style={{ ...styles.statCard, borderColor: warning ? 'rgba(239,68,68,0.3)' : '#1A1A1A' }}>
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
    padding: '28px 32px', minHeight: '100vh', background: '#0A0A0A',
    fontFamily: "'Inter', 'Segoe UI', sans-serif", maxWidth: 1200, margin: '0 auto',
  },
  loadingContainer: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '50vh' },
  spinner: { width: 40, height: 40, border: '3px solid #1A1A1A', borderTop: '3px solid #3ECF8E', borderRadius: '50%', animation: 'spin 0.8s linear infinite' },
  errorCard: { textAlign: 'center', padding: 60, background: '#111', borderRadius: 16, border: '1px solid #1A1A1A', maxWidth: 500, margin: '80px auto' },
  statsRow: { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 },
  statCard: { background: '#111', borderRadius: 12, padding: '18px 20px', border: '1px solid #1A1A1A' },
  columns: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 },
  card: { background: '#111', borderRadius: 14, padding: '22px 24px', border: '1px solid #1A1A1A' },
  cardTitle: { fontSize: 16, fontWeight: 600, color: '#fff', margin: 0 },
  cardSubtitle: { fontSize: 12, color: '#555', margin: '4px 0 0' },
  configGrid: { display: 'flex', flexDirection: 'column', gap: 8, marginTop: 16 },
  configRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', background: '#0D0D0D', borderRadius: 8, border: '1px solid #1A1A1A' },
  firstMsgBox: { marginTop: 16, padding: '14px 16px', background: '#0D0D0D', borderRadius: 8, border: '1px solid #1A1A1A' },
  callRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', background: '#0D0D0D', borderRadius: 8, border: '1px solid #1A1A1A' },
  txnRow: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', background: '#0D0D0D', borderRadius: 8, border: '1px solid #1A1A1A' },
};
