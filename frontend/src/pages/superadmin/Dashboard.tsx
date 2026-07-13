import { Activity, Building2, CalendarCheck, IndianRupee, Phone } from 'lucide-react';
import { useEffect, useState } from 'react';
import fetchWithAuth from '../../api/client';
import { PlanBadge, StatCard, StatusBadge } from '../../components/superadmin/SAShared';
import { useRealtimeDashboard } from '../../hooks/useRealtimeDashboard';
import { useSAStore } from '../../store/saStore';

// ── Shape returned by GET /admin/overview — see backend/routers/admin.py ───────
interface OverviewClinic {
  id: string;
  clinic_name: string;
  location: string | null;
  plan: string;
  status: 'Active' | 'Suspended';
  created_at: string | null;
  calls_month: number;
  bookings: number;
  res_rate: string;
  avg_latency: string;
}

interface Overview {
  total_clinics: number;
  active_this_month: number;
  total_calls: number;
  total_bookings: number;
  mrr: number;
  active_calls: number;
  recently_onboarded: OverviewClinic[];
  top_performing: OverviewClinic[];
  call_volume_7d: { date: string; day_label: string; count: number }[];
}

const asPlan = (plan: string) => (['Free', 'Pro', 'Enterprise'].includes(plan) ? plan : 'Free') as any;
const formatJoined = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

// ─── Skeleton ─────────────────────────────────────────────────────────────────
function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} style={{ padding: '12px 24px' }}>
          <div style={{
            height: '12px',
            borderRadius: '4px',
            width: i === 0 ? '60%' : '40%',
            background: 'linear-gradient(90deg, #1a1a1a 25%, #252525 50%, #1a1a1a 75%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 1.4s infinite',
          }} />
        </td>
      ))}
    </tr>
  );
}

// ── Map API clinic to store Clinic shape ──────────────────────────────────────
function mapApiClinic(c: any) {
  return {
    id: c.id,
    name: c.clinic_name,
    location: c.location ?? '—',
    plan: (['Free', 'Pro', 'Enterprise'].includes(c.plan) ? c.plan : 'Free') as any,
    joined: c.created_at ? new Date(c.created_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—',
    status: c.is_active ? 'Active' : ('Suspended' as any),
    calls_month: c.calls_month ?? 0,
    bookings: c.bookings ?? 0,
    res_rate: c.res_rate ?? '—',
    avg_latency: c.avg_latency ?? '—',
    model_id: c.model_id ?? 'm1',
    admin_email: c.admin_email ?? '',
  };
}

export default function SADashboard() {
  const { setClinics } = useSAStore();
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState<Overview | null>(null);
  const { liveCallCount, lastBooking, isConnected } = useRealtimeDashboard('platform');

  useEffect(() => {
    let cancelled = false;

    // Populates the shared clinics store (other superadmin pages, e.g. Billing,
    // read from it) — kept separate from the /admin/overview fetch below,
    // which drives everything actually rendered on this page.
    async function fetchClinicsForStore() {
      try {
        const data = await fetchWithAuth('/admin/clinics');
        if (!cancelled) setClinics((data.clinics ?? []).map(mapApiClinic));
      } catch {
        // Store keeps whatever it already had if the backend is unreachable.
      }
    }

    async function fetchOverview() {
      try {
        const data: Overview = await fetchWithAuth('/admin/overview');
        if (!cancelled) setOverview(data);
      } catch {
        // Leave overview null — cards/tables render their empty states below.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchClinicsForStore();
    fetchOverview();
    return () => { cancelled = true; };
  }, [setClinics]);

  const callVol = overview?.call_volume_7d ?? [];
  const chartMax = Math.max(...callVol.map(d => d.count), 1);

  const recentlyOnboarded = (overview?.recently_onboarded ?? []).map(c => ({
    id: c.id,
    name: c.clinic_name,
    location: c.location ?? '—',
    plan: asPlan(c.plan),
    joined: formatJoined(c.created_at),
  }));

  const topPerforming = (overview?.top_performing ?? []).map(c => ({
    id: c.id,
    name: c.clinic_name,
    calls_month: c.calls_month,
    bookings: c.bookings,
    res_rate: c.res_rate,
    avg_latency: c.avg_latency,
    plan: asPlan(c.plan),
    status: c.status,
  }));

  const totalClinics = overview?.total_clinics ?? 0;
  const activeThisMonth = overview?.active_this_month ?? 0;
  const totalCalls = overview?.total_calls ?? 0;
  const totalBookings = overview?.total_bookings ?? 0;
  const mrr = overview?.mrr ?? 0;

  const latestBookingLabel = lastBooking?.patient_name || lastBooking?.phone || lastBooking?.patient_phone || null;

  return (
    <div style={{ padding: '32px', overflowY: 'auto', height: '100%' }}>
      <div style={{ marginBottom: '28px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>
          Platform Overview
        </h1>
        <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>
          Live metrics across the Lifodial AI network
        </p>
        <div style={{ display: 'flex', gap: '10px', marginTop: '14px', flexWrap: 'wrap' }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            borderRadius: '999px',
            background: isConnected ? 'rgba(62, 207, 142, 0.08)' : 'rgba(245, 158, 11, 0.08)',
            border: `1px solid ${isConnected ? 'rgba(62, 207, 142, 0.18)' : 'rgba(245, 158, 11, 0.18)'}`,
          }}>
            <span style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: isConnected ? '#3ECF8E' : '#f59e0b',
            }} />
            <span style={{ color: isConnected ? '#3ECF8E' : '#f59e0b', fontSize: '12px', fontWeight: 600 }}>
              {isConnected ? 'Realtime connected' : 'Realtime reconnecting'}
            </span>
          </div>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            borderRadius: '999px',
            background: '#141414',
            border: '1px solid #2E2E2E',
          }}>
            <span style={{ color: '#fff', fontSize: '12px', fontWeight: 700 }}>{liveCallCount}</span>
            <span style={{ color: '#888', fontSize: '12px' }}>active calls</span>
          </div>
          {latestBookingLabel && (
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 12px',
              borderRadius: '999px',
              background: '#141414',
              border: '1px solid #2E2E2E',
              maxWidth: '320px',
            }}>
              <span style={{ color: '#3ECF8E', fontSize: '12px', fontWeight: 600 }}>Latest booking</span>
              <span style={{ color: '#bbb', fontSize: '12px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {latestBookingLabel}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Stat Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '16px', marginBottom: '28px' }}>
        <StatCard label="Total Clinics"     value={totalClinics}    icon={Building2} />
        <StatCard label="Active This Month" value={activeThisMonth} icon={Activity}  />
        <StatCard label="Total Calls"       value={totalCalls}      icon={Phone}     />
        <StatCard label="Total Bookings"    value={totalBookings}   icon={CalendarCheck} />
        <StatCard label="MRR" value={mrr > 0 ? `₹${(mrr / 1000).toFixed(0)}k` : '₹0'} icon={IndianRupee} />
      </div>

      {/* Tables Row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '20px' }}>
        {/* Recently Onboarded */}
        <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px' }}>
          <div style={{ padding: '20px 24px', borderBottom: '1px solid #2E2E2E', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: '12px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>
              Recently Onboarded
            </h3>
            <span style={{ color: '#555', fontSize: '11px' }}>{totalClinics} total</span>
          </div>
          <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: '#888' }}>
                {['Clinic', 'Location', 'Plan', 'Joined'].map(h => (
                  <th key={h} style={{ padding: '12px 24px', textAlign: 'left', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 3 }).map((_, i) => <SkeletonRow key={i} cols={4} />)
                : recentlyOnboarded.length === 0
                  ? (
                    <tr>
                      <td colSpan={4} style={{ padding: '32px 24px', textAlign: 'center', color: '#555', fontSize: '13px' }}>
                        No clinics yet — approve an onboarding request to add one
                      </td>
                    </tr>
                  )
                  : recentlyOnboarded.map(c => (
                    <tr key={c.id} style={{ borderTop: '1px solid #2E2E2E' }}>
                      <td style={{ padding: '12px 24px', color: '#fff', fontWeight: 600 }}>{c.name}</td>
                      <td style={{ padding: '12px 24px', color: '#888' }}>{c.location}</td>
                      <td style={{ padding: '12px 24px' }}><PlanBadge plan={c.plan} /></td>
                      <td style={{ padding: '12px 24px', color: '#888' }}>{c.joined}</td>
                    </tr>
                  ))
              }
            </tbody>
          </table>
        </div>

        {/* Call Volume Chart */}
        <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px' }}>
          <h3 style={{ fontSize: '12px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
            Call Volume (7 Days)
          </h3>
          <p style={{ color: '#555', fontSize: '11px', marginBottom: '24px', marginTop: 0 }}>
            Real call volume by day, last 7 days (IST)
          </p>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: '10px', height: '140px', paddingBottom: '4px' }}>
            {callVol.map((d, i) => (
              <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px', height: '100%', justifyContent: 'flex-end' }}>
                <div style={{
                  width: '100%',
                  height: `${Math.round((d.count / chartMax) * 100)}%`,
                  background: 'linear-gradient(180deg, #3ECF8E 0%, #2EAF74 100%)',
                  borderRadius: '4px 4px 0 0',
                  opacity: 0.85,
                  transition: 'height 0.4s ease',
                  minHeight: '4px',
                }} />
                <span style={{ fontSize: '10px', color: '#555' }}>{d.day_label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Top Performing */}
      <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px' }}>
        <div style={{ padding: '20px 24px', borderBottom: '1px solid #2E2E2E', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ fontSize: '12px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>
            Top Performing Clinics
          </h3>
          <span style={{ color: '#555', fontSize: '11px' }}>by calls this month</span>
        </div>
        <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ color: '#888' }}>
              {['Clinic', 'Calls/Month', 'Bookings', 'Resolution', 'Latency', 'Plan', 'Status'].map(h => (
                <th key={h} style={{ padding: '12px 24px', textAlign: 'left', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 3 }).map((_, i) => <SkeletonRow key={i} cols={7} />)
              : topPerforming.length === 0
                ? (
                  <tr>
                    <td colSpan={7} style={{ padding: '40px 24px', textAlign: 'center', color: '#555', fontSize: '13px' }}>
                      No active clinics with calls yet
                    </td>
                  </tr>
                )
                : topPerforming.map(c => (
                  <tr key={c.id} style={{ borderTop: '1px solid #2E2E2E' }}>
                    <td style={{ padding: '14px 24px', color: '#fff', fontWeight: 600 }}>{c.name}</td>
                    <td style={{ padding: '14px 24px', color: '#fff', fontFamily: 'monospace' }}>{c.calls_month.toLocaleString()}</td>
                    <td style={{ padding: '14px 24px', color: '#fff', fontFamily: 'monospace' }}>{c.bookings.toLocaleString()}</td>
                    <td style={{ padding: '14px 24px', color: '#3ECF8E', fontWeight: 600 }}>{c.res_rate}</td>
                    <td style={{ padding: '14px 24px', color: '#888', fontFamily: 'monospace' }}>{c.avg_latency}</td>
                    <td style={{ padding: '14px 24px' }}><PlanBadge plan={c.plan} /></td>
                    <td style={{ padding: '14px 24px' }}><StatusBadge status={c.status} /></td>
                  </tr>
                ))
            }
          </tbody>
        </table>
      </div>

      <style>{`
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
      `}</style>
    </div>
  );
}
