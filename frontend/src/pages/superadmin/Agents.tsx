import {
    AlertCircle,
    Brain,
    CalendarCheck,
    CheckCircle,
    ChevronDown,
    Circle,
    Clock,
    Copy,
    FlaskConical,
    Globe,
    Headphones,
    Mic,
    MoreVertical,
    Pause,
    Phone,
    Plus, Search,
    Trash2,
    TrendingUp,
    Zap
} from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import TestAgentModal from '../../components/TestAgentModal';
import { AgentStatus, FIXTURE_AGENTS, FixtureAgent } from '../../fixtures/data';
import { API_URL } from '../../api/client';

// ── Status config ────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<AgentStatus, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  ACTIVE:     { color: '#22C55E', bg: 'rgba(34,197,94,0.12)',   label: 'Active',      icon: <CheckCircle size={11} /> },
  CONFIGURED: { color: '#FBBF24', bg: 'rgba(251,191,36,0.12)',  label: 'Configured',  icon: <Clock size={11} /> },
  ERROR:      { color: '#F87171', bg: 'rgba(248,113,113,0.12)', label: 'Error',       icon: <AlertCircle size={11} /> },
  INACTIVE:   { color: '#6B7280', bg: 'rgba(107,114,128,0.12)', label: 'Inactive',    icon: <Circle size={11} /> },
};

// ── Dropdown menu ────────────────────────────────────────────────────────────

function AgentDropdown({ agent, onDelete }: { agent: FixtureAgent; onDelete: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: '#666', padding: '4px', borderRadius: '6px',
          display: 'flex', alignItems: 'center',
          transition: 'background 0.15s',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = '#2A2A2A'; e.currentTarget.style.color = '#fff'; }}
        onMouseLeave={e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.color = '#666'; }}
      >
        <MoreVertical size={15} />
      </button>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: '100%', zIndex: 99,
          background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '10px',
          padding: '4px', minWidth: '148px', boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}>
          {[
            { icon: <Copy size={13} />, label: 'Duplicate', action: () => {} },
            { icon: <Pause size={13} />, label: 'Pause Agent', action: () => {} },
            { icon: <Trash2 size={13} />, label: 'Delete', action: () => { onDelete(agent.id); setOpen(false); }, danger: true },
          ].map(({ icon, label, action, danger }) => (
            <button
              key={label}
              onClick={action}
              style={{
                width: '100%', display: 'flex', alignItems: 'center', gap: '8px',
                padding: '8px 12px', borderRadius: '7px', border: 'none',
                background: 'none', cursor: 'pointer', fontSize: '13px',
                color: danger ? '#F87171' : '#A1A1A1', textAlign: 'left',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = '#2A2A2A'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'none'; }}
            >
              {icon} {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Agent Card ───────────────────────────────────────────────────────────────

function AgentCard({ agent, onEdit, onTest, onDelete, onWebCall, onPhoneCall }: {
  agent: FixtureAgent;
  onEdit: (id: string) => void;
  onTest: () => void;
  onDelete: (id: string) => void;
  onWebCall: () => void;
  onPhoneCall: () => void;
}) {
  const st = STATUS_CONFIG[agent.status];

  return (
    <div
      onClick={() => onEdit(agent.id)}
      style={{
        background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '14px',
        padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px',
        transition: 'border-color 0.2s, box-shadow 0.2s', cursor: 'pointer',
      }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLDivElement).style.borderColor = '#3ECF8E44';
        (e.currentTarget as HTMLDivElement).style.boxShadow = '0 0 0 1px #3ECF8E22';
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLDivElement).style.borderColor = '#2E2E2E';
        (e.currentTarget as HTMLDivElement).style.boxShadow = 'none';
      }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            background: st.bg, borderRadius: '20px', padding: '3px 10px 3px 6px',
            color: st.color, fontSize: '11px', fontWeight: 600,
          }}
        >
          <span style={{ color: st.color }}>{st.icon}</span>
          {st.label}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button
            id={`edit-agent-${agent.id}`}
            onClick={(e) => { e.stopPropagation(); onEdit(agent.id); }}
            style={{
              padding: '5px 12px', borderRadius: '7px', fontSize: '12px', fontWeight: 500,
              border: '1px solid #2E2E2E', background: 'none', color: '#A1A1A1',
              cursor: 'pointer', transition: 'all 0.15s',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = '#3ECF8E'; e.currentTarget.style.color = '#3ECF8E'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = '#2E2E2E'; e.currentTarget.style.color = '#A1A1A1'; }}
          >
            Edit
          </button>
          <button
            id={`test-agent-${agent.id}`}
            onClick={(e) => { e.stopPropagation(); onTest(); }}
            style={{
              padding: '5px 12px', borderRadius: '7px', fontSize: '12px', fontWeight: 600,
              border: '1px solid #3ECF8E44', background: 'rgba(62,207,142,0.08)', color: '#3ECF8E',
              cursor: 'pointer', transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: '4px',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(62,207,142,0.16)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(62,207,142,0.08)'; }}
          >
            <FlaskConical size={11} /> Test
          </button>
          <div onClick={e => e.stopPropagation()}>
            <AgentDropdown agent={agent} onDelete={onDelete} />
          </div>
        </div>
      </div>

      {/* Agent name + clinic */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
          <div
            style={{
              width: '32px', height: '32px', borderRadius: '8px',
              background: 'rgba(62,207,142,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: '1px solid rgba(62,207,142,0.2)', flexShrink: 0,
            }}
          >
            <Headphones size={16} color="#3ECF8E" />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: '#fff', lineHeight: 1.3 }}>{agent.name}</div>
            <div style={{ fontSize: '12px', color: '#666' }}>{agent.clinic_name}</div>
          </div>
        </div>

        {/* First message preview */}
        <div
          style={{
            background: '#111', border: '1px solid #222', borderRadius: '8px',
            padding: '10px 12px', marginTop: '10px',
            fontSize: '12px', color: '#888', lineHeight: 1.6,
            fontStyle: 'italic', maxHeight: '52px', overflow: 'hidden',
          }}
        >
          "{agent.first_message.slice(0, 100)}{agent.first_message.length > 100 ? '…"' : '"'}"
        </div>
      </div>

      {/* Tech info pills */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <InfoPill icon={<Phone size={11} />} text={agent.ai_number} />
          <InfoPill icon={<Globe size={11} />} text={agent.languages.join(' + ')} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <InfoPill icon={<Mic size={11} />} text={`${agent.tts_provider} · ${agent.tts_model} · ${agent.tts_voice}`} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* LLM model — show exact provider + model from DB */}
          <InfoPill icon={<Brain size={11} />} text={`${agent.llm_provider || 'gemini'} · ${agent.llm_model || agent.model || 'gemini-2.0-flash'}`} />
        </div>
      </div>

      {/* Stats row */}
      <div
        style={{
          borderTop: '1px solid #222', paddingTop: '14px',
          display: 'grid', gridTemplateColumns: '1fr 1px 1fr', gap: '0',
        }}
      >
        <StatItem icon={<Phone size={12} color="#3ECF8E" />} label="Calls today" value={agent.calls_today > 0 ? agent.calls_today.toString() : '—'} />
        <div style={{ background: '#2E2E2E' }} />
        <StatItem icon={<CalendarCheck size={12} color="#A78BFA" />} label="Bookings" value={agent.bookings_today > 0 ? agent.bookings_today.toString() : '—'} right />
      </div>
      <div
        style={{
          display: 'grid', gridTemplateColumns: '1fr 1px 1fr', gap: '0',
        }}
      >
        <StatItem icon={<Zap size={12} color="#FBBF24" />} label="Avg latency" value={agent.avg_latency_ms > 0 ? `${agent.avg_latency_ms}ms` : '—'} />
        <div style={{ background: '#2E2E2E' }} />
        <StatItem icon={<TrendingUp size={12} color="#22C55E" />} label="Resolution" value={agent.resolution_rate > 0 ? `${agent.resolution_rate}%` : '—'} right />
      </div>

      {/* Web Call + Phone Call buttons */}
      <div style={{
        display: 'flex', gap: '8px', borderTop: '1px solid #222', paddingTop: '14px',
      }}>
        <button
          onClick={(e) => { e.stopPropagation(); onWebCall(); }}
          title="Test via browser (no phone needed)"
          style={{
            flex: 1, padding: '7px 0', borderRadius: '8px', fontSize: '12px', fontWeight: 600,
            border: '1px solid #3B82F644', background: 'rgba(59,130,246,0.08)', color: '#3B82F6',
            cursor: 'pointer', transition: 'all 0.15s', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '5px',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(59,130,246,0.16)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; }}
        >
          🌐 Web Call
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onPhoneCall(); }}
          title="Make outbound phone call"
          disabled={!agent.sip_provider}
          style={{
            flex: 1, padding: '7px 0', borderRadius: '8px', fontSize: '12px', fontWeight: 600,
            border: '1px solid #A78BFA44', background: 'rgba(167,139,250,0.08)', color: '#A78BFA',
            cursor: agent.sip_provider ? 'pointer' : 'not-allowed', transition: 'all 0.15s',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '5px',
            opacity: agent.sip_provider ? 1 : 0.4,
          }}
          onMouseEnter={e => { if (agent.sip_provider) e.currentTarget.style.background = 'rgba(167,139,250,0.16)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(167,139,250,0.08)'; }}
        >
          📞 Phone Call
        </button>
      </div>
    </div>
  );
}

function InfoPill({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: '#666', fontSize: '12px' }}>
      <span style={{ color: '#555' }}>{icon}</span>
      <span>{text}</span>
    </div>
  );
}

function StatItem({ icon, label, value, right }: { icon: React.ReactNode; label: string; value: string; right?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', padding: right ? '0 0 0 16px' : '0 16px 0 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
        {icon}
        <span style={{ fontSize: '11px', color: '#555' }}>{label}</span>
      </div>
      <span style={{ fontSize: '15px', fontWeight: 600, color: value === '—' ? '#444' : '#fff' }}>{value}</span>
    </div>
  );
}

// ── Empty state ──────────────────────────────────────────────────────────────

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '50vh' }}>
      <div style={{
        background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '20px',
        padding: '48px 56px', textAlign: 'center', maxWidth: '420px',
      }}>
        <div style={{
          width: '64px', height: '64px', borderRadius: '16px',
          background: '#222', display: 'flex', alignItems: 'center', justifyContent: 'center',
          margin: '0 auto 20px', border: '1px solid #2E2E2E',
        }}>
          <Bot size={28} color="#444" />
        </div>
        <div style={{ fontSize: '18px', fontWeight: 600, color: '#fff', marginBottom: '8px' }}>
          No agents configured yet
        </div>
        <div style={{ fontSize: '14px', color: '#666', marginBottom: '24px', lineHeight: 1.6 }}>
          Create your first AI voice receptionist to start handling clinic calls automatically.
        </div>
        <button
          id="create-first-agent-btn"
          onClick={onCreate}
          style={{
            padding: '12px 24px', borderRadius: '10px', fontSize: '14px', fontWeight: 600,
            background: '#3ECF8E', color: '#000', border: 'none', cursor: 'pointer',
            display: 'inline-flex', alignItems: 'center', gap: '8px', transition: 'background 0.15s',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = '#2EBF7E'; }}
          onMouseLeave={e => { e.currentTarget.style.background = '#3ECF8E'; }}
        >
          <Plus size={16} />
          Create Your First Agent
        </button>
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function SAAgents() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<any[]>([]);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [search, setSearch] = useState('');
  const [filterStatus, setFilterStatus] = useState('All');
  const [filterLang, setFilterLang] = useState('All');

  const filtered = agents.filter(a => {
    const q = search.toLowerCase();
    const matchSearch = !q || a.name.toLowerCase().includes(q) || a.clinic_name.toLowerCase().includes(q);
    const matchStatus = filterStatus === 'All' || a.status === filterStatus;
    const matchLang = filterLang === 'All' || (a.languages && a.languages.some((l: string) => l.toLowerCase().includes(filterLang.toLowerCase())));
    return matchSearch && matchStatus && matchLang;
  });

  useEffect(() => {
    fetch(`${API_URL}/agents`)
      .then(res => res.json())
      .then(async data => {
        // Map backend agent dict to frontend expected format
        const mapped = data.map((a: any) => ({
          ...a,
          name: a.agent_name || a.name || 'AI Receptionist',
          languages: a.tts_language ? [a.tts_language.split('-')[0].toUpperCase()] : ['EN'],
          // Stats: default 0, will be loaded individually via health API
          calls_today: 0,
          bookings_today: 0,
          avg_latency_ms: 0,
          resolution_rate: 0,
        }));
        setAgents(mapped);
        setLoadingAgents(false);

        // Fetch real stats for each agent in the background
        mapped.forEach(async (a: any) => {
          try {
            const h = await fetch(`${API_URL}/agents/${a.id}/health`).then(r => r.json());
            setAgents(prev => prev.map(ag => ag.id === a.id ? {
              ...ag,
              calls_today: h.last_24h?.total_calls ?? 0,
              avg_latency_ms: h.latency?.avg_ms ?? 0,
            } : ag));
          } catch {}
        });
      })
      .catch(err => {
        console.error('Failed to fetch agents:', err);
        setLoadingAgents(false);
      });
  }, []);

  const [testTarget, setTestTarget] = useState<FixtureAgent | null>(null);
  const [phoneCallTarget, setPhoneCallTarget] = useState<FixtureAgent | null>(null);
  const [phoneNumber, setPhoneNumber] = useState('');

  const handleDelete = async (id: string) => {
    if (!window.confirm('Permanently delete this agent?')) return;
    try {
      await fetch(`${API_URL}/agents/${id}`, { method: 'DELETE' });
    } catch {}
    setAgents(prev => prev.filter(a => a.id !== id));
  };
  const handleEdit = (id: string) => navigate(`/superadmin/agents/${id}`);
  const handleTest = (agent: FixtureAgent) => setTestTarget(agent);
  const handleCreate = () => navigate('/superadmin/agents/new');
  const handleWebCall = (agent: FixtureAgent) => setTestTarget(agent);
  const handlePhoneCall = (agent: FixtureAgent) => setPhoneCallTarget(agent);

  const activeCount = agents.filter(a => a.status === 'ACTIVE').length;

  return (
    <div style={{ padding: '32px', maxWidth: '1400px', margin: '0 auto' }}>

      {/* ── Top bar ── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '28px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 700, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>
            Agents
          </h1>
          <p style={{ fontSize: '13px', color: '#666', margin: '4px 0 0' }}>
            {agents.length} agents configured · {activeCount} active
          </p>
        </div>
        <button
          id="create-agent-btn"
          onClick={handleCreate}
          style={{
            padding: '10px 20px', borderRadius: '10px', fontSize: '14px', fontWeight: 600,
            background: '#3ECF8E', color: '#000', border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: '8px', transition: 'background 0.15s',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = '#2EBF7E'; }}
          onMouseLeave={e => { e.currentTarget.style.background = '#3ECF8E'; }}
        >
          <Plus size={16} />
          Create Agent
        </button>
      </div>

      {/* ── Filters ── */}
      <div style={{ display: 'flex', gap: '12px', marginBottom: '24px', flexWrap: 'wrap' }}>
        {/* Search */}
        <div style={{ position: 'relative', flex: 1, minWidth: '200px', maxWidth: '340px' }}>
          <Search size={14} color="#555" style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
          <input
            id="agent-search"
            placeholder="Search agents..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              width: '100%', padding: '9px 12px 9px 36px', borderRadius: '9px',
              background: '#1A1A1A', border: '1px solid #2E2E2E', color: '#fff',
              fontSize: '13px', outline: 'none', boxSizing: 'border-box',
            }}
          />
        </div>
        <SelectFilter
          value={filterStatus}
          onChange={setFilterStatus}
          options={['All', 'ACTIVE', 'CONFIGURED', 'ERROR', 'INACTIVE']}
          labels={{ All: 'All Status', ACTIVE: '● Active', CONFIGURED: '● Configured', ERROR: '● Error', INACTIVE: '● Inactive' }}
        />
        <SelectFilter
          value={filterLang}
          onChange={setFilterLang}
          options={['All', 'Hindi', 'English', 'Malayalam', 'Arabic', 'Tamil', 'Telugu']}
          labels={{ All: 'All Languages' }}
        />
      </div>

      {/* ── Content ── */}
      {loadingAgents ? (
        <div style={{ textAlign: 'center', padding: '80px 0', color: '#666' }}>Loading agents...</div>
      ) : agents.length === 0 ? (
        <EmptyState onCreate={handleCreate} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '20px' }}>
          {filtered.map(a => (
            <AgentCard
              key={a.id}
              agent={a}
              onEdit={handleEdit}
              onTest={() => handleTest(a)}
              onDelete={handleDelete}
              onWebCall={() => handleWebCall(a)}
              onPhoneCall={() => handlePhoneCall(a)}
            />
          ))}
        </div>
      )}

      {/* In-browser Test Modal */}
      {testTarget && (
        <TestAgentModal
          agent={testTarget}
          onClose={() => setTestTarget(null)}
        />
      )}



      {/* Phone Call Modal */}
      {phoneCallTarget && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 9998,
          background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(6px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            background: '#0F0F0F', border: '1px solid #1A1A1A', borderRadius: '14px',
            width: '420px', maxWidth: '90vw', padding: '24px',
          }}>
            <h3 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', margin: '0 0 4px' }}>
              📞 Make Outbound Call
            </h3>
            <p style={{ fontSize: '12px', color: '#666', margin: '0 0 20px' }}>
              Call from: {phoneCallTarget.name}
            </p>
            <p style={{ fontSize: '12px', color: '#888', margin: '0 0 4px' }}>
              AI Number: <span style={{ color: '#3ECF8E', fontFamily: 'monospace' }}>{phoneCallTarget.ai_number}</span>
            </p>

            <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#888', margin: '16px 0 6px' }}>
              Dial number
            </label>
            <div style={{ display: 'flex', gap: '8px' }}>
              <span style={{
                padding: '8px 12px', borderRadius: '8px',
                background: '#111', border: '1px solid #2E2E2E',
                color: '#888', fontSize: '13px',
              }}>
                🇮🇳 +91
              </span>
              <input
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                placeholder="98765 43210"
                style={{
                  flex: 1, padding: '8px 12px', borderRadius: '8px',
                  background: '#111', border: '1px solid #2E2E2E',
                  color: '#ccc', fontSize: '13px', outline: 'none',
                }}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '24px' }}>
              <button
                onClick={() => { setPhoneCallTarget(null); setPhoneNumber(''); }}
                style={{
                  padding: '8px 16px', borderRadius: '8px',
                  background: 'transparent', border: '1px solid #2E2E2E',
                  color: '#888', cursor: 'pointer', fontSize: '13px',
                }}
              >Cancel</button>
              <button
                onClick={() => {
                  const num = phoneNumber.trim();
                  if (!num) return;
                  const fullNumber = num.startsWith('+') ? num : `+91${num.replace(/\s/g, '')}`;
                  fetch(`/agents/${phoneCallTarget.id}/outbound-call`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phone_number: fullNumber }),
                  }).then(r => r.json()).then(data => {
                    alert(data.message || 'Call initiated');
                    setPhoneCallTarget(null);
                    setPhoneNumber('');
                  }).catch(() => {
                    alert('SIP trunk not configured. Configure telephony first.');
                    setPhoneCallTarget(null);
                    setPhoneNumber('');
                  });
                }}
                disabled={!phoneNumber.trim()}
                style={{
                  padding: '8px 20px', borderRadius: '8px',
                  background: '#3ECF8E', color: '#000', border: 'none',
                  cursor: phoneNumber.trim() ? 'pointer' : 'not-allowed',
                  fontSize: '13px', fontWeight: 600,
                  opacity: phoneNumber.trim() ? 1 : 0.5,
                }}
              >📞 Call Now</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SelectFilter({
  value, onChange, options, labels = {},
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  labels?: Record<string, string>;
}) {
  return (
    <div style={{ position: 'relative' }}>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          padding: '9px 32px 9px 12px', borderRadius: '9px', appearance: 'none',
          background: '#1A1A1A', border: '1px solid #2E2E2E', color: '#A1A1A1',
          fontSize: '13px', cursor: 'pointer', outline: 'none',
        }}
      >
        {options.map(o => (
          <option key={o} value={o}>{labels[o] || o}</option>
        ))}
      </select>
      <ChevronDown size={13} color="#555" style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
    </div>
  );
}
