import {
    Bot,
    ChevronDown,
    Plus, Search
} from 'lucide-react';
import React, { Suspense, lazy, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
// Lazy: pulls in the LiveKit/WebRTC client stack (~526kB alone, the single
// largest chunk in the app) — only needed when the in-browser test modal opens.
const TestAgentModal = lazy(() => import('../../components/TestAgentModal'));
import { FixtureAgent } from '../../fixtures/data';
import fetchWithAuth from '../../api/client';
// The card, the stat/pill primitives and the dialer now live in one place so the
// clinic's own My Agent page renders exactly what this page does — see
// components/AgentCard.tsx.
import { AgentCard, PhoneCallModal } from '../../components/AgentCard';

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

  // A clinic can have multiple agents now — when two share the same name,
  // show a distinguishing id/date on both so they aren't visually identical.
  const duplicateNameKeys = new Set<string>();
  {
    const seen = new Set<string>();
    for (const a of agents) {
      const key = `${a.clinic_name}::${a.name}`;
      if (seen.has(key)) duplicateNameKeys.add(key);
      seen.add(key);
    }
  }

  useEffect(() => {
    fetchWithAuth('/agents')
      .then(async data => {
        // Map backend agent dict to frontend expected format
        const mapped = data.map((a: any) => ({
          ...a,
          name: a.agent_name || a.name || 'AI Receptionist',
          languages: a.language ? [a.language.split('-')[0].toUpperCase()] : ['EN'],
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
            const h = await fetchWithAuth(`/agents/${a.id}/health`);
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

  const handleDelete = async (id: string) => {
    if (!window.confirm('Permanently delete this agent?')) return;
    try {
      await fetchWithAuth(`/agents/${id}`, { method: 'DELETE' });
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
              showDisambiguator={duplicateNameKeys.has(`${a.clinic_name}::${a.name}`)}
            />
          ))}
        </div>
      )}

      {/* In-browser Test Modal */}
      {testTarget && (
        <Suspense fallback={null}>
          <TestAgentModal
            agent={testTarget}
            onClose={() => setTestTarget(null)}
          />
        </Suspense>
      )}

      {/* Outbound dialer — the same component the clinic's My Agent page uses,
          so "identical to Superadmin's flow" is structural rather than a claim. */}
      {phoneCallTarget && (
        <PhoneCallModal agent={phoneCallTarget} onClose={() => setPhoneCallTarget(null)} />
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
