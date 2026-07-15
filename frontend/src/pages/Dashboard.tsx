import { useQuery } from '@tanstack/react-query';
import {
    Activity,
    ArrowRight,
    CalendarCheck,
    CheckSquare,
    Clock,
    Headphones,
    PhoneIncoming,
    PhoneMissed,
    Square,
    TrendingUp
} from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import fetchWithAuth from '../api/client';
import {
    FIXTURE_APPOINTMENTS,
    FIXTURE_TENANT
} from '../fixtures/data';

// ── Status badge ──────────────────────────────────────────────────────────────
const STATUS_STYLES: Record<string, { color: string; bg: string; border?: string }> = {
  Booked:      { color: 'var(--accent)',      bg: 'var(--accent-dim)',      border: 'var(--accent-border)' },
  Transferred: { color: 'var(--purple)',      bg: 'var(--purple-dim)' },
  Resolved:    { color: 'var(--text-muted)',  bg: 'var(--bg-surface-2)' },
  Failed:      { color: 'var(--destructive)', bg: 'var(--destructive-dim)' },
  Pending:     { color: 'var(--warning)',     bg: 'var(--warning-dim)' },
  CONFIRMED:   { color: 'var(--accent)',      bg: 'var(--accent-dim)',      border: 'var(--accent-border)' },
  CANCELLED:   { color: 'var(--destructive)', bg: 'var(--destructive-dim)' },
};

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_STYLES[status] ?? { color: 'var(--text-muted)', bg: 'var(--bg-surface-2)' };
  return (
    <span style={{
      display: 'inline-block', padding: '2px 10px', borderRadius: '9999px',
      fontSize: '11px', fontWeight: 600,
      color: s.color, backgroundColor: s.bg,
      border: s.border ? `1px solid ${s.border}` : undefined,
    }}>
      {status}
    </span>
  );
}

// ── Stat skeleton ─────────────────────────────────────────────────────────────
function StatSkeleton() {
  return (
    <div className="rounded-xl p-5" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-start justify-between mb-3">
        <div className="skeleton h-3 w-24" /><div className="skeleton w-6 h-6 rounded" />
      </div>
      <div className="skeleton h-8 w-16 mb-2" /><div className="skeleton h-3 w-20" />
    </div>
  );
}

// ── Empty State ───────────────────────────────────────────────────────────────
function EmptyState({ icon: Icon, title, subtitle }: {
  icon: React.ElementType; title: string; subtitle: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ backgroundColor: 'var(--bg-surface-2)' }}>
        <Icon size={18} style={{ color: 'var(--text-muted)' }} />
      </div>
      <div>
        <p style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-secondary)' }}>{title}</p>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '3px' }}>{subtitle}</p>
      </div>
    </div>
  );
}

// ── KPI definitions ───────────────────────────────────────────────────────────
const KPI_DEFS = [
  { key: 'calls_today',     label: 'Calls Today',     icon: PhoneIncoming },
  { key: 'booked_today',    label: 'Booked',          icon: CalendarCheck },
  { key: 'avg_duration',    label: 'Avg Duration',    icon: Clock },
  { key: 'resolution_rate', label: 'Resolution Rate', icon: TrendingUp },
];

// ── Quick Setup card ──────────────────────────────────────────────────────────
function QuickSetupCard() {
  const tenant = FIXTURE_TENANT;
  const hasDoctors = true; // fixture: has doctors

  const steps = [
    { label: 'Clinic registered',                  done: true  },
    { label: `Phone number assigned: ${tenant.ai_number}`, done: true  },
    { label: 'Call forwarding verified',           done: tenant.forwarding_verified },
    { label: 'Add your first doctor',              done: hasDoctors },
  ];

  const allDone = steps.every(s => s.done);
  if (allDone) return null;

  return (
    <div
      className="rounded-xl p-5"
      style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--accent-border)',
        boxShadow: 'var(--shadow-card)',
      }}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-2.5">
          <Headphones size={18} style={{ color: 'var(--accent)' }} />
          <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            Complete your setup
          </h3>
        </div>
        <span style={{
          fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '9999px',
          color: 'var(--warning)', backgroundColor: 'var(--warning-dim)',
        }}>
          {steps.filter(s => !s.done).length} remaining
        </span>
      </div>

      <div className="space-y-2 mb-4">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center gap-2.5">
            {step.done
              ? <CheckSquare size={15} style={{ color: 'var(--accent)', flexShrink: 0 }} />
              : <Square size={15} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
            <span style={{
              fontSize: '13px',
              color: step.done ? 'var(--text-secondary)' : 'var(--text-primary)',
              fontWeight: step.done ? 400 : 500,
              textDecoration: step.done ? 'line-through' : 'none',
              opacity: step.done ? 0.7 : 1,
            }}>
              {step.label}
            </span>
          </div>
        ))}
      </div>

      <Link
        to="/settings"
        className="flex items-center gap-2"
        style={{
          display: 'inline-flex', padding: '8px 16px', borderRadius: '8px',
          fontSize: '13px', fontWeight: 600, color: '#000',
          backgroundColor: 'var(--accent)', textDecoration: 'none',
          transition: 'background-color 0.15s',
        }}
        onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-hover)'; }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent)'; }}
      >
        Complete Setup <ArrowRight size={14} />
      </Link>
    </div>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const { data: statsData, isLoading: statsLoading } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: () => fetchWithAuth('/api/dashboard/stats'),
    retry: false,
  });

  const { data: callsData, isLoading: callsLoading } = useQuery({
    queryKey: ['recent-calls'],
    queryFn: () => fetchWithAuth('/api/call_logs?limit=5'),
    retry: false,
  });

  // Real data only — /api/call_logs returns {items: [...]}. No fixture fallback:
  // when there are no calls (or the fetch fails) the table renders its empty state.
  const recentCalls = callsData?.items ?? [];
  const liveCount   = statsData?.live_calls ?? 0;
  const isAgentOnline = true; // fixture: always online

  const recentApts = FIXTURE_APPOINTMENTS.slice(0, 3);

  return (
    <div data-testid="dashboard-page" className="h-full flex flex-col">

      {/* Agent status banner */}
      <div
        className="flex items-center gap-2.5 flex-shrink-0"
        style={{
          padding: '10px 16px',
          backgroundColor: isAgentOnline ? 'var(--accent-dim)' : 'var(--destructive-dim)',
          borderBottom: `1px solid ${isAgentOnline ? 'var(--accent-border)' : 'rgba(248,113,113,0.25)'}`,
          flexWrap: 'wrap',
        }}
      >
        <div
          className="w-1.5 h-1.5 rounded-full dot-pulse"
          style={{ backgroundColor: isAgentOnline ? 'var(--accent)' : 'var(--destructive)', flexShrink: 0 }}
        />
        <span style={{
          fontSize: '12px', fontWeight: 500,
          color: isAgentOnline ? 'var(--accent)' : 'var(--destructive)',
        }}>
          {isAgentOnline
            ? `Agent Online — Ready to receive calls on ${FIXTURE_TENANT.ai_number}`
            : 'Agent Offline — Calls will not be answered'}
        </span>
      </div>

      {/* ── Top bar ── */}
      <div
        className="flex items-center justify-between flex-shrink-0"
        style={{ padding: '16px', borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)', flexWrap: 'wrap', gap: '8px' }}
      >
        <div>
          <h1 style={{ fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em', margin: 0 }}>
            Dashboard
          </h1>
          <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
            Real-time receptionist overview
          </p>
        </div>
        <div className="flex items-center gap-2" style={{
          padding: '4px 10px', borderRadius: '9999px',
          backgroundColor: 'var(--accent-dim)', border: '1px solid var(--accent-border)',
        }}>
          <div className="w-1.5 h-1.5 rounded-full dot-pulse" style={{ backgroundColor: 'var(--accent)' }} />
          <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--accent)' }}>Live</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto" style={{ backgroundColor: 'var(--bg-page)', padding: '16px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '1400px', margin: '0 auto' }}>

        {/* ── Quick setup card (hides when complete) ── */}
        <QuickSetupCard />

        {/* ── KPI row ── */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
          {statsLoading
            ? KPI_DEFS.map(k => <StatSkeleton key={k.key} />)
            : KPI_DEFS.map(({ key, label, icon: Icon }) => {
                const val = statsData?.[key];
                return (
                  <div
                    key={key}
                    data-testid={`kpi-${key}`}
                    className="rounded-xl p-5 transition-all"
                    style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; }}
                  >
                    <div className="flex items-start justify-between mb-3">
                      <p style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 500 }}>
                        {label}
                      </p>
                      <Icon size={16} style={{ color: 'var(--text-muted)' }} />
                    </div>
                    <p style={{ fontSize: '32px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.02em', lineHeight: 1 }}>
                      {val ?? '—'}
                    </p>
                    {!val && <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>No data yet</p>}
                  </div>
                );
              })}
        </div>

        {/* ── calls + queue, stacks on mobile ── */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
          {/* Recent calls */}
          <div className="rounded-xl overflow-hidden" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}>
            <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
              <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
                Recent Call Activity
              </h2>
              <Link to="/calls" style={{ fontSize: '12px', color: 'var(--accent)', textDecoration: 'none' }}>
                View all →
              </Link>
            </div>

            {callsLoading ? (
              <div className="p-6 space-y-3">
                {[1, 2, 3].map(i => <div key={i} className="skeleton h-10 w-full" />)}
              </div>
            ) : recentCalls.length === 0 ? (
              <EmptyState icon={PhoneMissed} title="No calls yet" subtitle="Calls appear here once your number receives traffic" />
            ) : (
              <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
              <table className="w-full" style={{ borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {['Caller', 'Intent', 'Duration', 'Time', 'Status'].map(h => (
                      <th key={h} className="px-6 py-3 text-left" style={{
                        fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em',
                        color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontWeight: 500,
                      }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recentCalls.map((call: any, i: number) => (
                    <tr
                      key={call.id ?? i}
                      style={{ borderBottom: i < recentCalls.length - 1 ? '1px solid var(--border)' : 'none' }}
                      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface-2)'; }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                    >
                      <td className="px-6 py-3.5" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: 'var(--text-primary)' }}>
                        {call.caller_number ?? call.phone ?? '—'}
                      </td>
                      <td className="px-6 py-3.5" style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {call.intent ?? '—'}
                      </td>
                      <td className="px-6 py-3.5" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {call.duration ?? '—'}
                      </td>
                      <td className="px-6 py-3.5" style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                        {call.time ?? call.date ?? call.created_at ?? '—'}
                      </td>
                      <td className="px-6 py-3.5">
                        <StatusBadge status={call.status ?? 'Pending'} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>

          {/* Live Call Queue */}
          <div className="rounded-xl p-6 flex flex-col" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}>
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '16px' }}>
              Live Call Queue
            </h2>
            <div className="flex-1 flex flex-col items-center justify-center text-center gap-3">
              <div
                className="w-12 h-12 rounded-full flex items-center justify-center"
                style={{
                  backgroundColor: liveCount > 0 ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
                  border: liveCount > 0 ? '1px solid var(--accent-border)' : '1px solid var(--border)',
                }}
              >
                <Activity size={22} style={{ color: liveCount > 0 ? 'var(--accent)' : 'var(--text-muted)' }} />
              </div>
              <div>
                <p style={{ fontSize: '2.25rem', fontWeight: 600, color: 'var(--text-primary)', lineHeight: 1, letterSpacing: '-0.02em' }}>
                  {liveCount}
                </p>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                  active calls right now
                </p>
              </div>
              <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                {liveCount === 0 ? 'All lines open and ready.' : 'Handling calls.'}
              </p>
            </div>
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: '14px', marginTop: '14px' }}>
              {[
                { label: 'Calls handled today', val: statsData?.calls_today },
                { label: 'Booked today',         val: statsData?.booked_today },
                { label: 'Missed calls',          val: statsData?.missed_calls, red: true },
              ].map(r => (
                <div key={r.label} className="flex justify-between mb-2">
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{r.label}</span>
                  <span style={{ fontSize: '12px', fontWeight: 500, color: (r.red && (r.val ?? 0) > 0) ? 'var(--destructive)' : 'var(--text-primary)' }}>
                    {r.val ?? '—'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Recent Appointments widget ── */}
        <div className="rounded-xl overflow-hidden" style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}>
          <div className="px-6 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
            <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              Recent Appointments
            </h2>
            <Link to="/appointments" style={{ fontSize: '12px', color: 'var(--accent)', textDecoration: 'none' }}>
              View all →
            </Link>
          </div>

          {recentApts.length === 0 ? (
            <EmptyState icon={CalendarCheck} title="No appointments yet" subtitle="Bookings will appear here." />
          ) : (
            <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <table className="w-full" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Patient', 'Doctor', 'Time', 'Status'].map(h => (
                    <th key={h} className="px-6 py-3 text-left" style={{
                      fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em',
                      color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontWeight: 500,
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {recentApts.map((apt, i) => (
                  <tr
                    key={apt.id}
                    style={{ borderBottom: i < recentApts.length - 1 ? '1px solid var(--border)' : 'none' }}
                    onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface-2)'; }}
                    onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                  >
                    <td className="px-6 py-3.5" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: 'var(--text-primary)' }}>
                      {apt.patient_phone}
                    </td>
                    <td className="px-6 py-3.5" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                      {apt.doctor}
                    </td>
                    <td className="px-6 py-3.5" style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                      {apt.slot_time}
                    </td>
                    <td className="px-6 py-3.5">
                      <StatusBadge status={apt.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
