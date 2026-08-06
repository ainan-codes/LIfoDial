import { useQuery } from '@tanstack/react-query';
import {
    Activity,
    ArrowRight,
    BarChart2,
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
import { getTenantId } from '../api/auth';

/**
 * Dashboard — the clinic's single overview page.
 *
 * This absorbed the former /analytics page (which now redirects here). They had
 * overlapping purpose and, worse, three metrics with the SAME NAME computed over
 * DIFFERENT windows: Dashboard read Resolution Rate / Avg Duration / Booked from
 * /api/clinic/stats (today), while Analytics computed them from the last 200 call
 * logs. Shown side by side those would simply disagree.
 *
 * So the merge keeps both, under two explicitly labelled sections:
 *
 *   "Today"          — /api/clinic/stats: calls, booked, missed, avg duration,
 *                      resolution rate, live-now.
 *   "Recent history" — derived from the last 200 call logs: totals, 7-day volume,
 *                      intent mix, language mix, and the Receptionist Impact card.
 *
 * Nothing that was only on Analytics was dropped. The 7-day chart, intent
 * breakdown, language distribution and impact card are all below; its four KPI
 * tiles live in the "Recent history" row, which is where their window is stated.
 *
 * One call-log request now serves both the "Recent Call Activity" table (first 5)
 * and every trend below — the two pages together used to fetch limit=5 and
 * limit=200 separately.
 */

const APPOINTMENT_STATUS_FROM_BACKEND: Record<string, string> = {
  confirmed: 'CONFIRMED', cancelled: 'CANCELLED', pending: 'PENDING',
};

/** How many recent calls the trend section is computed over. */
const TREND_WINDOW = 200;

// ── Trend helpers (moved verbatim from the old Analytics page) ────────────────

const LANGUAGE_LABELS: Record<string, string> = {
  'hi-IN': 'Hindi', 'en-IN': 'English', 'en-US': 'English (US)', 'en-GB': 'English (UK)',
  'ta-IN': 'Tamil', 'te-IN': 'Telugu', 'kn-IN': 'Kannada', 'ml-IN': 'Malayalam',
  'mr-IN': 'Marathi', 'bn-IN': 'Bengali', 'pa-IN': 'Punjabi', 'gu-IN': 'Gujarati',
  'ar-AE': 'Arabic', 'ar-SA': 'Arabic',
};

function parseDurationSeconds(d: string): number | null {
  const m = /^(\d+):(\d{2})$/.exec((d || '').trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

function formatDurationSeconds(secs: number): string {
  return `${Math.floor(secs / 60)}:${String(Math.round(secs % 60)).padStart(2, '0')}`;
}

function tally(logs: any[], key: 'intent' | 'language'): Record<string, number> {
  return logs.reduce((acc, l) => {
    let v = String(l[key] ?? '').trim();
    if (key === 'language') v = LANGUAGE_LABELS[v] ?? v;
    if (v && v !== '—') acc[v] = (acc[v] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>);
}

/** Trailing 7 real days, oldest → newest, bucketed from each call's timestamp. */
function last7Days(logs: any[]): { day: string; value: number }[] {
  const labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const buckets: { day: string; value: number }[] = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    buckets.push({ day: labels[d.getDay()], value: 0 });
  }
  for (const l of logs) {
    const parsed = new Date((l.date || '').replace(',', ''));
    if (Number.isNaN(parsed.getTime())) continue;
    parsed.setHours(0, 0, 0, 0);
    const ago = Math.round((today.getTime() - parsed.getTime()) / 86_400_000);
    if (ago >= 0 && ago <= 6) buckets[6 - ago].value += 1;
  }
  return buckets;
}

function BarGroup({ day, value, max }: { day: string; value: number; max: number }) {
  const pct = Math.round((value / max) * 100);
  return (
    <div className="flex flex-col items-center gap-2" style={{ flex: 1, minWidth: 0 }}>
      <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 500 }}>{value}</span>
      <div
        style={{
          width: '100%', maxWidth: '40px', height: '80px',
          position: 'relative', borderRadius: '4px',
          backgroundColor: 'var(--bg-surface-2)',
        }}
      >
        <div
          style={{
            position: 'absolute', bottom: 0, left: 0, right: 0,
            height: `${pct}%`, borderRadius: '4px',
            backgroundColor: 'var(--accent)', minHeight: value > 0 ? '4px' : 0,
            transition: 'height 0.6s ease',
          }}
        />
      </div>
      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{day}</span>
    </div>
  );
}

/** A KPI tile — same look as the "Today" row, so the two sections read as one page. */
function StatTile({ label, value, icon: Icon, color }: {
  label: string; value: string | number; icon: React.ElementType; color?: string;
}) {
  return (
    <div
      className="rounded-xl p-5 transition-all"
      style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}
      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)'; }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; }}
    >
      <div className="flex items-start justify-between mb-3">
        <p style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', fontWeight: 500 }}>
          {label}
        </p>
        <Icon size={16} style={{ color: color ?? 'var(--text-muted)' }} />
      </div>
      <p style={{ fontSize: '32px', fontWeight: 600, color: color ?? 'var(--text-primary)', letterSpacing: '-0.02em', lineHeight: 1 }}>
        {value}
      </p>
    </div>
  );
}

/** Section heading that STATES THE WINDOW — the whole point of the merge. */
function SectionHeading({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div style={{ marginTop: '8px' }}>
      <h2 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{title}</h2>
      <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '2px 0 0' }}>{subtitle}</p>
    </div>
  );
}

/** Shared card chrome for the three trend panels. */
const trendCardStyle: React.CSSProperties = {
  backgroundColor: 'var(--bg-surface)',
  border: '1px solid var(--border)',
  boxShadow: 'var(--shadow-card)',
  borderRadius: '12px',
};

function TrendPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={trendCardStyle}>
      <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
        <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>{title}</h3>
      </div>
      {children}
    </div>
  );
}

/** A labelled proportion bar, used by both breakdowns. */
function ShareRow({ label, count, total, color }: {
  label: string; count: number; total: number; color: string;
}) {
  const pct = total ? Math.round((count / total) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 500 }}>{label}</span>
        <span style={{ fontSize: '13px', color: 'var(--text-muted)', fontFamily: "'JetBrains Mono', monospace" }}>{count} · {pct}%</span>
      </div>
      <div style={{ height: '6px', borderRadius: '3px', backgroundColor: 'var(--bg-surface-2)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, borderRadius: '3px', backgroundColor: color, transition: 'width 0.5s ease' }} />
      </div>
    </div>
  );
}

function formatSlotTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

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
// TODAY only — every one of these comes from /api/clinic/stats. `missed_calls`
// moved up here from the Live Call Queue card, which used to repeat calls_today
// and booked_today as a list beneath the very tiles that already showed them.
const KPI_DEFS = [
  { key: 'calls_today',     label: 'Calls Today',     icon: PhoneIncoming },
  { key: 'booked_today',    label: 'Booked',          icon: CalendarCheck },
  { key: 'missed_calls',    label: 'Missed',          icon: PhoneMissed },
  { key: 'avg_duration',    label: 'Avg Duration',    icon: Clock },
  { key: 'resolution_rate', label: 'Resolution Rate', icon: TrendingUp },
];

// ── Quick Setup card ──────────────────────────────────────────────────────────
function QuickSetupCard({ aiNumber, forwardingSet, hasDoctors }: {
  aiNumber: string | null; forwardingSet: boolean; hasDoctors: boolean;
}) {
  const steps = [
    { label: 'Clinic registered',                  done: true  },
    { label: `Phone number assigned: ${aiNumber ?? 'pending'}`, done: !!aiNumber },
    { label: 'Call forwarding number set',          done: forwardingSet },
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
  const tenantId = getTenantId();

  // /api/clinic/stats, NOT /api/dashboard/stats — the latter never existed AND
  // the whole /api/dashboard prefix is hard-404'd by block_foreign_requests() in
  // backend/main.py as noise from another project, so every KPI tile below
  // rendered "—" forever with no visible error.
  const { data: statsData, isLoading: statsLoading } = useQuery({
    queryKey: ['clinic-stats'],
    queryFn: () => fetchWithAuth('/api/clinic/stats'),
    retry: false,
  });

  // ONE call-log fetch serves the recent-activity table (first 5) and every trend
  // panel below. The two pages used to issue limit=5 and limit=200 separately.
  const { data: callsData, isLoading: callsLoading } = useQuery({
    queryKey: ['clinic-call-logs', TREND_WINDOW],
    queryFn: () => fetchWithAuth(`/api/call_logs?limit=${TREND_WINDOW}`),
    retry: false,
  });

  // Real clinic identity (name, AI number, forwarding number) — replaces the
  // fixture tenant that used to be shown regardless of which clinic logged in.
  const { data: tenantData } = useQuery({
    queryKey: ['tenant', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}`),
    enabled: !!tenantId,
    retry: false,
  });

  const { data: doctorsData } = useQuery({
    queryKey: ['doctors', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}/doctors`),
    enabled: !!tenantId,
    retry: false,
  });

  // Real appointments (already ordered most-recent-first server-side) for the
  // "Recent Appointments" preview — same endpoint the full Appointments page uses.
  const { data: aptsData } = useQuery({
    queryKey: ['recent-appointments', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}/appointments`),
    enabled: !!tenantId,
    retry: false,
  });

  // Real data only — /api/call_logs returns {items: [...]}. No fixture fallback:
  // when there are no calls (or the fetch fails) the table renders its empty state.
  const allLogs     = callsData?.items ?? [];
  const recentCalls = allLogs.slice(0, 5);
  const liveCount   = statsData?.live_calls ?? 0;
  const isAgentOnline = true; // TODO: no single "is this clinic's agent live" signal exists yet

  // ── Recent-history trends (was the Analytics page) ────────────────────────
  const trendTotalCalls = allLogs.length;
  const trendBooked = (aptsData ?? []).filter(
    (a: any) => String(a.status ?? '').toUpperCase() === 'CONFIRMED'
  ).length;
  const trendResolved = allLogs.filter((l: any) => l.status === 'Booked' || l.status === 'Resolved').length;
  const trendResolutionPct = trendTotalCalls ? `${Math.round((trendResolved / trendTotalCalls) * 100)}%` : '—';
  const trendDurations = allLogs
    .map((l: any) => parseDurationSeconds(l.duration))
    .filter((n: number | null): n is number => n !== null);
  const trendAvgDuration = trendDurations.length
    ? formatDurationSeconds(trendDurations.reduce((a: number, b: number) => a + b, 0) / trendDurations.length)
    : '—';
  const intentCounts = tally(allLogs, 'intent');
  const langCounts   = tally(allLogs, 'language');
  const volumeDays   = last7Days(allLogs);
  // Floor of 1 so an all-zero week renders flat bars instead of dividing by zero.
  const maxDay       = Math.max(1, ...volumeDays.map(d => d.value));
  const INTENT_COLORS: Record<string, string> = {
    Appointment:     'var(--accent)',
    Emergency:       'var(--destructive)',
    'General Query': 'var(--purple)',
    Cancellation:    'var(--warning)',
  };

  const recentApts = (aptsData ?? []).slice(0, 3).map((a: any) => ({
    id: a.id,
    patient_phone: a.patient_phone,
    doctor: a.doctor_name,
    slot_time: formatSlotTime(a.slot_time),
    status: APPOINTMENT_STATUS_FROM_BACKEND[a.status] ?? 'PENDING',
  }));

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
            ? `Agent Online — Ready to receive calls on ${tenantData?.ai_number ?? '—'}`
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
            Receptionist overview and call analytics
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
        <QuickSetupCard
          aiNumber={tenantData?.ai_number ?? null}
          forwardingSet={!!tenantData?.forwarding_number}
          hasDoctors={(doctorsData?.length ?? 0) > 0}
        />

        {/* ── Today ── */}
        <SectionHeading title="Today" subtitle="Live figures for the current day." />

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
            {/* The three-row summary that used to sit here (calls handled today /
                booked today / missed calls) is gone: all three are KPI tiles at the
                top of this same section, and repeating them under the tiles was the
                duplicate-metric problem in miniature. */}
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

        {/* ══ Recent history — the former /analytics page ══════════════════════
            Everything below is computed from the last {TREND_WINDOW} calls, which is
            why the heading says so: three of these figures share a NAME with a
            "Today" tile above but a different window, and that was the one real
            hazard in merging the two pages. */}
        <SectionHeading
          title="Recent history"
          subtitle={
            trendTotalCalls > 0
              ? `Trends across the last ${trendTotalCalls} call${trendTotalCalls === 1 ? '' : 's'} — a longer window than the figures above.`
              : `Trends appear here once your receptionist starts taking calls (last ${TREND_WINDOW} calls).`
          }
        />

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px' }}>
          <StatTile label="Total Calls"      value={trendTotalCalls}    icon={PhoneIncoming} />
          <StatTile label="Apts Booked"      value={trendBooked}        icon={CalendarCheck} color="var(--accent)" />
          <StatTile label="Resolution Rate"  value={trendResolutionPct} icon={TrendingUp} />
          <StatTile label="Avg Handle Time"  value={trendAvgDuration}   icon={BarChart2} />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
          <TrendPanel title="Call Volume — Last 7 Days">
            <div className="px-6 py-5">
              <div className="flex items-end gap-2" style={{ height: '120px', alignItems: 'flex-end' }}>
                {volumeDays.map((d, i) => <BarGroup key={`${d.day}-${i}`} {...d} max={maxDay} />)}
              </div>
            </div>
          </TrendPanel>

          <TrendPanel title="Intent Breakdown">
            <div className="px-6 py-5 space-y-3">
              {Object.keys(intentCounts).length === 0 ? (
                <EmptyState icon={PhoneMissed} title="No intents recorded yet" subtitle="Each answered call is classified automatically." />
              ) : (
                Object.entries(intentCounts).map(([intent, count]) => (
                  <ShareRow
                    key={intent}
                    label={intent}
                    count={count}
                    total={trendTotalCalls}
                    color={INTENT_COLORS[intent] ?? 'var(--text-muted)'}
                  />
                ))
              )}
            </div>
          </TrendPanel>

          <TrendPanel title="Language Distribution">
            <div className="px-6 py-5 space-y-3">
              {Object.keys(langCounts).length === 0 ? (
                <EmptyState icon={PhoneMissed} title="No languages recorded yet" subtitle="The language of each call is logged as it happens." />
              ) : (
                Object.entries(langCounts).map(([lang, count]) => (
                  <ShareRow key={lang} label={lang} count={count} total={trendTotalCalls} color="var(--accent)" />
                ))
              )}
            </div>
          </TrendPanel>

          {/* Receptionist Impact — kept from Analytics verbatim. */}
          <div style={{ ...trendCardStyle, backgroundColor: 'var(--accent-dim)', border: '1px solid var(--accent-border)' }}>
            <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--accent-border)' }}>
              <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--accent)' }}>
                Receptionist Impact
              </h3>
            </div>
            <div className="px-6 py-5 space-y-4">
              {[
                { label: 'Calls fully resolved by AI',    value: trendResolutionPct },
                { label: 'Languages handled',              value: `${Object.keys(langCounts).length}` },
                { label: 'Appointments booked (no staff)', value: `${trendBooked}` },
                { label: 'Avg call length',                value: trendAvgDuration },
              ].map(item => (
                <div key={item.label} className="flex justify-between">
                  <span style={{ fontSize: '13px', color: 'var(--accent)', opacity: 0.8 }}>{item.label}</span>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>{item.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        </div>
      </div>
    </div>
  );
}
