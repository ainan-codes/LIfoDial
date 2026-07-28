import { useQuery } from '@tanstack/react-query';
import { BarChart2, CalendarCheck, PhoneIncoming, TrendingUp } from 'lucide-react';
import React from 'react';
import { getTenantId } from '../api/auth';
import fetchWithAuth from '../api/client';

/**
 * Analytics — real, tenant-scoped call performance.
 *
 * This page used to import FIXTURE_APPOINTMENTS / FIXTURE_CALL_LOGS and make ZERO
 * API calls: every KPI, both breakdowns and the 7-day chart were computed from
 * five invented calls, the average handle time was the literal string '2:18', the
 * chart was a hardcoded Mon–Sun array, and the subtitle said "Apollo Demo Clinic"
 * no matter who logged in. A brand-new clinic therefore opened Analytics and saw
 * a full week of traffic it had never received.
 */

/** A row from GET /api/call_logs (backend/main.py::_call_record_to_row). */
interface CallLogRow {
  id: string;
  date: string;      // "%d %b %Y, %H:%M"
  duration: string;  // "M:SS", or "—" when unknown
  intent: string;
  status: string;
  language: string;
}

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

function tally(logs: CallLogRow[], key: 'intent' | 'language'): Record<string, number> {
  return logs.reduce((acc, l) => {
    let v = String(l[key] ?? '').trim();
    if (key === 'language') v = LANGUAGE_LABELS[v] ?? v;
    if (v && v !== '—') acc[v] = (acc[v] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>);
}

/** Trailing 7 real days, oldest → newest, bucketed from each call's timestamp. */
function last7Days(logs: CallLogRow[]): { day: string; value: number }[] {
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

function StatCard({ label, value, icon: Icon, color }: {
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

// Simple bar chart using divs
function BarGroup({ day, value, max }: { day: string; value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
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

export default function Analytics() {
  const tenantId = getTenantId();

  const { data: logsData, isLoading } = useQuery<{ items: CallLogRow[] }>({
    queryKey: ['analytics-call-logs'],
    queryFn: () => fetchWithAuth('/api/call_logs?limit=200'),
    retry: false,
  });

  const { data: tenantData } = useQuery<{ clinic_name?: string }>({
    queryKey: ['tenant', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}`),
    enabled: !!tenantId,
    retry: false,
  });

  const { data: aptsData } = useQuery<{ status?: string }[]>({
    queryKey: ['analytics-appointments', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}/appointments`),
    enabled: !!tenantId,
    retry: false,
  });

  const logs = logsData?.items ?? [];
  const totalCalls = logs.length;

  const booked = (aptsData ?? []).filter(
    a => String(a.status ?? '').toUpperCase() === 'CONFIRMED'
  ).length;

  const resolved = logs.filter(l => l.status === 'Booked' || l.status === 'Resolved').length;
  const resolutionPct = totalCalls ? `${Math.round((resolved / totalCalls) * 100)}%` : '—';

  const durations = logs
    .map(l => parseDurationSeconds(l.duration))
    .filter((n): n is number => n !== null);
  const avgDuration = durations.length
    ? formatDurationSeconds(durations.reduce((a, b) => a + b, 0) / durations.length)
    : '—';

  const intentCounts = tally(logs, 'intent');
  const langCounts   = tally(logs, 'language');
  const days         = last7Days(logs);
  // Floor of 1 so an all-zero week renders flat bars instead of dividing by zero.
  const maxDay       = Math.max(1, ...days.map(d => d.value));

  const clinicName = tenantData?.clinic_name;
  const hasNoData  = !isLoading && totalCalls === 0;

  const cardStyle: React.CSSProperties = {
    backgroundColor: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    boxShadow: 'var(--shadow-card)',
    borderRadius: '12px',
  };

  return (
    <div data-testid="analytics-page" className="h-full flex flex-col">
      {/* Top bar */}
      <div
        className="px-8 py-5 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      >
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em', margin: 0 }}>
          Analytics
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
          {clinicName
            ? `Call performance and outcome trends for ${clinicName}`
            : 'Call performance and outcome trends'}
        </p>
      </div>

      <div className="flex-1 p-8 space-y-5 overflow-y-auto" style={{ backgroundColor: 'var(--bg-page)' }}>
        {hasNoData && (
          <div
            className="rounded-xl p-5"
            style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          >
            <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              No calls yet
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>
              Once your AI receptionist starts taking calls, performance trends will appear here.
            </p>
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-4 gap-4">
          <StatCard label="Total Calls"       value={totalCalls}    icon={PhoneIncoming} />
          <StatCard label="Apts Booked"        value={booked}        icon={CalendarCheck} color="var(--accent)" />
          <StatCard label="Resolution Rate"   value={resolutionPct} icon={TrendingUp} />
          <StatCard label="Avg Handle Time"   value={avgDuration}   icon={BarChart2} />
        </div>

        {/* Bottom: 2-column grid */}
        <div className="grid gap-5" style={{ gridTemplateColumns: '1fr 1fr' }}>
          {/* Call volume chart */}
          <div style={cardStyle}>
            <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
              <h2 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Call Volume — Last 7 Days
              </h2>
            </div>
            <div className="px-6 py-5">
              <div className="flex items-end gap-2" style={{ height: '120px', alignItems: 'flex-end' }}>
                {days.map(d => <BarGroup key={d.day} {...d} max={maxDay} />)}
              </div>
            </div>
          </div>

          {/* Intent breakdown */}
          <div style={cardStyle}>
            <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
              <h2 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Intent Breakdown
              </h2>
            </div>
            <div className="px-6 py-5 space-y-3">
              {Object.entries(intentCounts).map(([intent, count]) => {
                const pct = Math.round((count / totalCalls) * 100);
                const colors: Record<string, string> = {
                  Appointment:   'var(--accent)',
                  Emergency:     'var(--destructive)',
                  'General Query': 'var(--purple)',
                  Cancellation:  'var(--warning)',
                };
                const clr = colors[intent] ?? 'var(--text-muted)';
                return (
                  <div key={intent}>
                    <div className="flex justify-between mb-1">
                      <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 500 }}>{intent}</span>
                      <span style={{ fontSize: '13px', color: 'var(--text-muted)', fontFamily: "'JetBrains Mono', monospace" }}>{count} · {pct}%</span>
                    </div>
                    <div style={{ height: '6px', borderRadius: '3px', backgroundColor: 'var(--bg-surface-2)', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, borderRadius: '3px', backgroundColor: clr, transition: 'width 0.5s ease' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Language distribution */}
          <div style={cardStyle}>
            <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
              <h2 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)' }}>
                Language Distribution
              </h2>
            </div>
            <div className="px-6 py-5 space-y-3">
              {Object.entries(langCounts).map(([lang, count]) => {
                const pct = Math.round((count / totalCalls) * 100);
                const flags: Record<string, string> = { Hindi: '🇮🇳', English: '🇬🇧', Tamil: '🇮🇳' };
                return (
                  <div key={lang}>
                    <div className="flex justify-between mb-1">
                      <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                        {flags[lang] ?? ''} {lang}
                      </span>
                      <span style={{ fontSize: '13px', color: 'var(--text-muted)', fontFamily: "'JetBrains Mono', monospace" }}>{count} · {pct}%</span>
                    </div>
                    <div style={{ height: '6px', borderRadius: '3px', backgroundColor: 'var(--bg-surface-2)', overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, borderRadius: '3px', backgroundColor: 'var(--accent)', transition: 'width 0.5s ease' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* AI summary card */}
          <div style={{ ...cardStyle, backgroundColor: 'var(--accent-dim)', border: '1px solid var(--accent-border)' }}>
            <div className="px-6 py-4" style={{ borderBottom: '1px solid var(--accent-border)' }}>
              <h2 style={{ margin: 0, fontSize: '14px', fontWeight: 600, color: 'var(--accent)' }}>
                Receptionist Impact
              </h2>
            </div>
            <div className="px-6 py-5 space-y-4">
              {[
                { label: 'Calls fully resolved by AI',    value: `${resolutionPct}` },
                { label: 'Languages handled',              value: `${Object.keys(langCounts).length}` },
                { label: 'Appointments booked (no staff)', value: `${booked}` },
                // "Avg response time: < 3 sec" was a hardcoded marketing claim.
                // Average call length is a number we can actually measure.
                { label: 'Avg call length',                value: avgDuration },
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
  );
}
