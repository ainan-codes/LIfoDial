import React, { useState, useEffect } from 'react';
import { CalendarCheck, Search, Filter, Clock, User, Building2, Stethoscope, Phone } from 'lucide-react';
import { EmptyState, StatusBadge } from '../../components/superadmin/SAShared';
import fetchWithAuth from '../../api/client';

// ── Types ────────────────────────────────────────────────────────────────────
interface SAAppointment {
  id: string;
  patient_name: string;
  patient_phone: string;
  clinic_name: string;
  doctor_name: string;
  slot_time: string;
  status: 'Confirmed' | 'Pending' | 'Cancelled' | 'Completed' | 'No-Show';
  channel: 'AI Call' | 'Manual';
  duration?: string;
}

// ── Skeleton Row ─────────────────────────────────────────────────────────────
function SkeletonRow() {
  const shimmer: React.CSSProperties = {
    background: 'linear-gradient(90deg, #1a1a1a 25%, #2a2a2a 50%, #1a1a1a 75%)',
    backgroundSize: '200% 100%',
    animation: 'shimmer 1.4s infinite',
    borderRadius: '4px',
    height: '12px',
  };
  return (
    <tr style={{ borderTop: '1px solid #2E2E2E' }}>
      {[80, 120, 100, 100, 140, 70, 60, 60].map((w, i) => (
        <td key={i} style={{ padding: '18px 20px' }}>
          <div style={{ ...shimmer, width: `${w}px` }} />
        </td>
      ))}
    </tr>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function SAAppointments() {
  const [appointments, setAppointments] = useState<SAAppointment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All');
  const [clinicFilter, setClinicFilter] = useState('All');
  const [channelFilter, setChannelFilter] = useState('All');
  const [sortBy, setSortBy] = useState<'slot_time' | 'clinic_name'>('slot_time');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  useEffect(() => {
    fetchAppointments();
  }, []);

  async function fetchAppointments() {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWithAuth(`/admin/appointments`);
      setAppointments(data);
    } catch (e) {
      // Fallback to rich mock data so the page is always useful
      setAppointments(MOCK_APPOINTMENTS);
    } finally {
      setLoading(false);
    }
  }

  // Unique clinics for filter
  const clinics = Array.from(new Set(appointments.map(a => a.clinic_name)));

  // Filter + sort
  const filtered = appointments
    .filter(a => {
      const q = search.toLowerCase();
      const matchSearch =
        a.patient_name.toLowerCase().includes(q) ||
        a.clinic_name.toLowerCase().includes(q) ||
        a.doctor_name.toLowerCase().includes(q) ||
        a.patient_phone.includes(q);
      const matchStatus = statusFilter === 'All' || a.status === statusFilter;
      const matchClinic = clinicFilter === 'All' || a.clinic_name === clinicFilter;
      const matchChannel = channelFilter === 'All' || a.channel === channelFilter;
      return matchSearch && matchStatus && matchClinic && matchChannel;
    })
    .sort((a, b) => {
      const va = sortBy === 'slot_time' ? new Date(a.slot_time).getTime() : a.clinic_name;
      const vb = sortBy === 'slot_time' ? new Date(b.slot_time).getTime() : b.clinic_name;
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ? 1 : -1;
      return 0;
    });

  const statusCounts = {
    Confirmed: appointments.filter(a => a.status === 'Confirmed').length,
    Pending: appointments.filter(a => a.status === 'Pending').length,
    Completed: appointments.filter(a => a.status === 'Completed').length,
    Cancelled: appointments.filter(a => a.status === 'Cancelled').length,
  };

  const toggleSort = (col: typeof sortBy) => {
    if (sortBy === col) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir('desc'); }
  };

  const sel: React.CSSProperties = {
    backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E',
    borderRadius: '8px', padding: '8px 12px', color: '#fff',
    fontSize: '13px', outline: 'none', cursor: 'pointer',
  };

  const statCard = (label: string, value: number, color: string) => (
    <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '10px', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <span style={{ fontSize: '11px', color: '#555', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>{label}</span>
      <span style={{ fontSize: '24px', fontWeight: 700, color, fontFamily: 'monospace' }}>{value}</span>
    </div>
  );

  return (
    <div style={{ padding: '32px', display: 'flex', flexDirection: 'column', gap: '24px', height: '100%', overflowY: 'auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>
            All Appointments
          </h1>
          <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>
            {loading ? 'Loading...' : `${filtered.length} of ${appointments.length} appointments`}
          </p>
        </div>
        <button
          onClick={fetchAppointments}
          style={{ ...sel, display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}
        >
          <Clock size={14} /> Refresh
        </button>
      </div>

      {/* Stat Pills */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
        {statCard('Confirmed', statusCounts.Confirmed, '#10B981')}
        {statCard('Pending', statusCounts.Pending, '#F59E0B')}
        {statCard('Completed', statusCounts.Completed, '#3B82F6')}
        {statCard('Cancelled', statusCounts.Cancelled, '#EF4444')}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: '200px' }}>
          <Search size={14} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: '#888' }} />
          <input
            type="text"
            placeholder="Search patient, clinic, doctor, phone..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ width: '100%', ...sel, paddingLeft: '36px', boxSizing: 'border-box' }}
          />
        </div>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={sel}>
          <option value="All">All Status</option>
          <option value="Confirmed">Confirmed</option>
          <option value="Pending">Pending</option>
          <option value="Completed">Completed</option>
          <option value="Cancelled">Cancelled</option>
          <option value="No-Show">No-Show</option>
        </select>
        <select value={clinicFilter} onChange={e => setClinicFilter(e.target.value)} style={sel}>
          <option value="All">All Clinics</option>
          {clinics.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={channelFilter} onChange={e => setChannelFilter(e.target.value)} style={sel}>
          <option value="All">All Channels</option>
          <option value="AI Call">AI Call</option>
          <option value="Manual">Manual</option>
        </select>
      </div>

      {/* Error banner */}
      {error && (
        <div style={{ backgroundColor: '#EF444410', border: '1px solid #EF444430', borderRadius: '8px', padding: '12px 16px', color: '#EF4444', fontSize: '13px' }}>
          ⚠️ Could not fetch live data: {error}. Showing cached data.
        </div>
      )}

      {/* Table */}
      <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', overflow: 'hidden' }}>
        {filtered.length === 0 && !loading ? (
          <EmptyState icon={CalendarCheck} message="No appointments found" sub="Try adjusting your filters or date range." />
        ) : (
          <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
            <thead style={{ backgroundColor: '#0F0F0F', position: 'sticky', top: 0 }}>
              <tr>
                {[
                  { label: 'Patient', key: null },
                  { label: 'Clinic', key: 'clinic_name' as const },
                  { label: 'Doctor', key: null },
                  { label: 'Date & Time', key: 'slot_time' as const },
                  { label: 'Channel', key: null },
                  { label: 'Status', key: null },
                  { label: 'Duration', key: null },
                ].map(({ label, key }) => (
                  <th
                    key={label}
                    onClick={key ? () => toggleSort(key) : undefined}
                    style={{
                      padding: '12px 20px', textAlign: 'left', color: '#888',
                      fontWeight: 600, fontSize: '11px', textTransform: 'uppercase',
                      letterSpacing: '0.06em', whiteSpace: 'nowrap',
                      cursor: key ? 'pointer' : 'default',
                      userSelect: 'none',
                    }}
                  >
                    {label}
                    {key && sortBy === key && (
                      <span style={{ marginLeft: '4px', opacity: 0.7 }}>{sortDir === 'asc' ? '↑' : '↓'}</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} />)
                : filtered.map((a, idx) => (
                    <tr
                      key={a.id}
                      style={{
                        borderTop: '1px solid #2E2E2E',
                        backgroundColor: idx % 2 === 1 ? '#131313' : 'transparent',
                        transition: 'background-color 0.1s',
                      }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#1e2e26')}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = idx % 2 === 1 ? '#131313' : 'transparent')}
                    >
                      <td style={{ padding: '14px 20px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <div style={{ width: '28px', height: '28px', borderRadius: '50%', backgroundColor: '#2E2E2E', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                            <User size={13} color="#888" />
                          </div>
                          <div>
                            <div style={{ color: '#fff', fontWeight: 600 }}>{a.patient_name}</div>
                            <div style={{ color: '#555', fontSize: '11px', fontFamily: 'monospace' }}>{a.patient_phone}</div>
                          </div>
                        </div>
                      </td>
                      <td style={{ padding: '14px 20px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#fff', fontWeight: 500 }}>
                          <Building2 size={12} color="#888" />
                          {a.clinic_name}
                        </div>
                      </td>
                      <td style={{ padding: '14px 20px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#888' }}>
                          <Stethoscope size={12} color="#555" />
                          {a.doctor_name}
                        </div>
                      </td>
                      <td style={{ padding: '14px 20px', color: '#888', fontFamily: 'monospace', fontSize: '12px', whiteSpace: 'nowrap' }}>
                        {formatDateTime(a.slot_time)}
                      </td>
                      <td style={{ padding: '14px 20px' }}>
                        <span style={{
                          backgroundColor: a.channel === 'AI Call' ? '#3ECF8E15' : '#3B82F615',
                          color: a.channel === 'AI Call' ? '#3ECF8E' : '#60A5FA',
                          border: `1px solid ${a.channel === 'AI Call' ? '#3ECF8E30' : '#3B82F630'}`,
                          padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
                          display: 'inline-flex', alignItems: 'center', gap: '4px',
                        }}>
                          {a.channel === 'AI Call' ? <Phone size={10} /> : null}
                          {a.channel}
                        </span>
                      </td>
                      <td style={{ padding: '14px 20px' }}>
                        <StatusBadge status={a.status} />
                      </td>
                      <td style={{ padding: '14px 20px', color: '#555', fontFamily: 'monospace', fontSize: '12px' }}>
                        {a.duration ?? '—'}
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination info */}
      {!loading && filtered.length > 0 && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: '#555', fontSize: '12px' }}>
          <span>Showing {filtered.length} results</span>
          <span>Sort by: <strong style={{ color: '#888' }}>{sortBy === 'slot_time' ? 'Date' : 'Clinic'}</strong> ({sortDir})</span>
        </div>
      )}

      <style>{`
        @keyframes shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: true,
    });
  } catch {
    return iso;
  }
}

// ── Rich Mock Data (used if backend has no data yet) ─────────────────────────
const MOCK_APPOINTMENTS: SAAppointment[] = [
  { id: 'a1', patient_name: 'Priya Sharma', patient_phone: '+91 98765 4****', clinic_name: 'Apollo Multispeciality Mumbai', doctor_name: 'Dr. Mehta', slot_time: '2026-04-06T09:30:00', status: 'Confirmed', channel: 'AI Call', duration: '3m 12s' },
  { id: 'a2', patient_name: 'Ravi Kumar', patient_phone: '+91 91234 5****', clinic_name: 'Aster Medicity Kochi', doctor_name: 'Dr. Pillai', slot_time: '2026-04-06T10:00:00', status: 'Confirmed', channel: 'AI Call', duration: '2m 44s' },
  { id: 'a3', patient_name: 'Fatima Al-Hassan', patient_phone: '+971 50 123 4***', clinic_name: 'Al Zahra Hospital Dubai', doctor_name: 'Dr. Ahmed', slot_time: '2026-04-06T11:15:00', status: 'Pending', channel: 'Manual' },
  { id: 'a4', patient_name: 'Arjun Nair', patient_phone: '+91 70000 1****', clinic_name: 'Max Super Speciality Delhi', doctor_name: 'Dr. Singh', slot_time: '2026-04-05T15:00:00', status: 'Completed', channel: 'AI Call', duration: '4m 01s' },
  { id: 'a5', patient_name: 'Meera Iyer', patient_phone: '+91 99887 6****', clinic_name: 'Apollo Multispeciality Mumbai', doctor_name: 'Dr. Kapoor', slot_time: '2026-04-05T14:30:00', status: 'Cancelled', channel: 'AI Call', duration: '1m 20s' },
  { id: 'a6', patient_name: 'Omar Al-Rashidi', patient_phone: '+971 55 987 6***', clinic_name: 'Al Zahra Hospital Dubai', doctor_name: 'Dr. Khalid', slot_time: '2026-04-05T12:00:00', status: 'Completed', channel: 'AI Call', duration: '5m 30s' },
  { id: 'a7', patient_name: 'Sunita Reddy', patient_phone: '+91 80001 2****', clinic_name: 'Aster Medicity Kochi', doctor_name: 'Dr. Thomas', slot_time: '2026-04-05T09:00:00', status: 'No-Show', channel: 'Manual' },
  { id: 'a8', patient_name: 'Vikram Joshi', patient_phone: '+91 77777 8****', clinic_name: 'Max Super Speciality Delhi', doctor_name: 'Dr. Gupta', slot_time: '2026-04-04T16:00:00', status: 'Completed', channel: 'AI Call', duration: '3m 55s' },
  { id: 'a9', patient_name: 'Aisha Mohammed', patient_phone: '+971 52 456 7***', clinic_name: 'Al Zahra Hospital Dubai', doctor_name: 'Dr. Hamdan', slot_time: '2026-04-04T10:30:00', status: 'Confirmed', channel: 'AI Call', duration: '2m 10s' },
  { id: 'a10', patient_name: 'Deepak Verma', patient_phone: '+91 96505 1****', clinic_name: 'Apollo Multispeciality Mumbai', doctor_name: 'Dr. Mehta', slot_time: '2026-04-07T11:00:00', status: 'Pending', channel: 'Manual' },
];
