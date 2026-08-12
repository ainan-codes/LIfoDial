import {
  CalendarCheck, Clock, Download, Filter, Globe, HelpCircle, MessageSquare, Mic,
  Phone, UserRound, XCircle,
} from 'lucide-react';
import React, { useEffect, useState } from 'react';
import type { Appointment } from '../fixtures/data';
import fetchWithAuth from '../api/client';
import { getTenantId } from '../api/auth';

// ── Backend <-> UI shape mapping ───────────────────────────────────────────
// GET /tenants/{tenant_id}/appointments returns lowercase status
// (pending/confirmed/cancelled) and doctor_name/specialization already
// joined server-side (backend/routers/appointments.py).
interface BackendAppointment {
  id: string;
  doctor_id: string;
  doctor_name: string;
  specialization: string;
  slot_time: string;
  patient_phone: string;
  status: 'pending' | 'confirmed' | 'cancelled';
  patient_name?: string | null;
  source?: string | null;
}

// ── Booking channel ────────────────────────────────────────────────────────
// Mirrors backend/models/appointment.py's vocabulary. This column used to be a
// hardcoded "AI Voice" badge on every row, on the assumption that voice was the
// only way to book — which was wrong about every appointment in the database:
// on 2026-08-12 all of them had come from the chat/embed channel and no voice
// call had ever produced one. `null` is a row written before the channel was
// recorded, so it reads "Unknown" rather than being attributed to a guess.
type SourceKey = 'voice' | 'web_voice' | 'chat' | 'embed' | 'dashboard' | 'unknown';

const SOURCE_META: Record<SourceKey, { label: string; icon: React.ElementType; tone: 'accent' | 'info' | 'muted' }> = {
  voice:     { label: 'Phone Call',    icon: Phone,          tone: 'accent' },
  web_voice: { label: 'Web Call',      icon: Mic,            tone: 'accent' },
  chat:      { label: 'Chat',          icon: MessageSquare,  tone: 'info'   },
  embed:     { label: 'Website Chat',  icon: Globe,          tone: 'info'   },
  dashboard: { label: 'Added by Staff',icon: UserRound,      tone: 'muted'  },
  unknown:   { label: 'Unknown',       icon: HelpCircle,     tone: 'muted'  },
};

function sourceKey(source?: string | null): SourceKey {
  const s = (source || '').toLowerCase();
  return (s in SOURCE_META ? s : 'unknown') as SourceKey;
}

const STATUS_FROM_BACKEND: Record<BackendAppointment['status'], Appointment['status']> = {
  confirmed: 'CONFIRMED',
  cancelled: 'CANCELLED',
  pending: 'PENDING',
};

function formatSlotTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

interface Row extends Appointment {
  source: SourceKey;
  patient_name: string;
}

function fromBackend(a: BackendAppointment): Row {
  const key = sourceKey(a.source);
  return {
    id: a.id,
    patient_phone: a.patient_phone,
    doctor: a.doctor_name,
    specialization: a.specialization,
    slot_time: formatSlotTime(a.slot_time),
    booked_via: SOURCE_META[key].label,
    call_id: '',
    status: STATUS_FROM_BACKEND[a.status] ?? 'PENDING',
    source: key,
    patient_name: (a.patient_name || '').trim(),
  };
}

// ── Status badge ──────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: Appointment['status'] }) {
  const map: Record<string, { color: string; bg: string; border?: string }> = {
    CONFIRMED: { color: 'var(--accent)',      bg: 'var(--accent-dim)',      border: 'var(--accent-border)' },
    CANCELLED: { color: 'var(--destructive)', bg: 'var(--destructive-dim)' },
    PENDING:   { color: 'var(--warning)',     bg: 'var(--warning-dim)' },
  };
  const s = map[status] ?? map.PENDING;
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 10px',
      borderRadius: '9999px',
      fontSize: '11px',
      fontWeight: 600,
      color: s.color,
      backgroundColor: s.bg,
      border: s.border ? `1px solid ${s.border}` : undefined,
    }}>
      {status}
    </span>
  );
}

// ── Booked-via badge ─────────────────────────────────────────────────────────
function SourceBadge({ source }: { source: SourceKey }) {
  const meta = SOURCE_META[source];
  const Icon = meta.icon;
  const tone = {
    accent: { color: 'var(--accent)', bg: 'var(--accent-dim)', border: 'var(--accent-border)' },
    info:   { color: 'var(--text-primary)', bg: 'var(--bg-surface-2)', border: 'var(--border-strong)' },
    muted:  { color: 'var(--text-muted)', bg: 'var(--bg-surface-2)', border: 'var(--border)' },
  }[meta.tone];
  return (
    <span
      title={`Booked via ${meta.label}`}
      className="flex items-center gap-1.5"
      style={{
        display: 'inline-flex',
        padding: '2px 8px',
        borderRadius: '9999px',
        fontSize: '11px',
        fontWeight: 600,
        color: tone.color,
        backgroundColor: tone.bg,
        border: `1px solid ${tone.border}`,
        whiteSpace: 'nowrap',
      }}
    >
      <Icon size={11} />
      {meta.label}
    </span>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────
function StatCard({ label, value, icon: Icon, color }: {
  label: string; value: number; icon: React.ElementType; color?: string;
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

// ── Appointments Page ─────────────────────────────────────────────────────────
export default function Appointments() {
  const [appointments, setAppointments] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<string>('ALL');
  const [filterSpec, setFilterSpec]     = useState<string>('ALL');
  const [filterSource, setFilterSource] = useState<string>('ALL');

  useEffect(() => {
    const tenantId = getTenantId();
    if (!tenantId) return;
    let cancelled = false;
    setLoading(true);
    fetchWithAuth(`/tenants/${tenantId}/appointments`)
      .then((data: BackendAppointment[]) => {
        if (cancelled) return;
        setAppointments((data || []).map(fromBackend));
        setError(null);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message || 'Failed to load appointments');
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const specs = Array.from(new Set(appointments.map(a => a.specialization)));

  const sources = Array.from(new Set(appointments.map(a => a.source)));

  const filtered = appointments.filter(a =>
    (filterStatus === 'ALL' || a.status === filterStatus) &&
    (filterSpec   === 'ALL' || a.specialization === filterSpec) &&
    (filterSource === 'ALL' || a.source === filterSource)
  );

  const stats = {
    total:    appointments.length,
    confirmed: appointments.filter(a => a.status === 'CONFIRMED').length,
    cancelled: appointments.filter(a => a.status === 'CANCELLED').length,
    pending:   appointments.filter(a => a.status === 'PENDING').length,
  };

  // CSV export (client-side, no API needed)
  const handleExport = () => {
    const headers = ['ID', 'Patient Name', 'Patient Phone', 'Doctor', 'Specialization', 'Slot Time', 'Booked Via', 'Status'];
    const rows = appointments.map(a =>
      [a.id, a.patient_name, a.patient_phone, a.doctor, a.specialization, a.slot_time, a.booked_via, a.status].join(',')
    );
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'appointments.csv';
    link.click();
    URL.revokeObjectURL(url);
  };

  // Cancel an appointment
  const handleCancel = async (id: string) => {
    const tenantId = getTenantId();
    if (!tenantId) return;
    // Optimistic update — revert if the request fails.
    const prevAppointments = appointments;
    setAppointments(prev =>
      prev.map(a => a.id === id ? { ...a, status: 'CANCELLED' as const } : a)
    );
    try {
      await fetchWithAuth(`/tenants/${tenantId}/appointments/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'cancelled' }),
      });
    } catch (e) {
      setAppointments(prevAppointments);
      setError((e as Error).message || 'Failed to cancel appointment');
    }
  };

  const selectStyle: React.CSSProperties = {
    padding: '7px 12px',
    borderRadius: '8px',
    fontSize: '13px',
    backgroundColor: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    color: 'var(--text-secondary)',
    outline: 'none',
    cursor: 'pointer',
  };

  return (
    <div data-testid="appointments-page" className="h-full flex flex-col">
      {/* Top bar */}
      <div
        className="px-8 py-5 flex items-center justify-between flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      >
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em', margin: 0 }}>
            Appointments
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
            Bookings made by the receptionist
          </p>
        </div>
        <button
          onClick={handleExport}
          className="flex items-center gap-2"
          style={{
            padding: '8px 16px',
            borderRadius: '8px',
            fontSize: '13px',
            fontWeight: 500,
            color: 'var(--text-secondary)',
            backgroundColor: 'transparent',
            border: '1px solid var(--border)',
            cursor: 'pointer',
            transition: 'border-color 0.15s',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; }}
        >
          <Download size={14} />
          Export CSV
        </button>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden" style={{ backgroundColor: 'var(--bg-page)' }}>
        {/* Stats row */}
        <div className="px-8 pt-6 pb-4 grid grid-cols-4 gap-4 flex-shrink-0">
          <StatCard label="Total Today"  value={stats.total}     icon={CalendarCheck} />
          <StatCard label="Confirmed"    value={stats.confirmed} icon={CalendarCheck} color="var(--accent)" />
          <StatCard label="Cancelled"    value={stats.cancelled} icon={XCircle}       color="var(--destructive)" />
          <StatCard label="Pending"      value={stats.pending}   icon={Clock}         color="var(--warning)" />
        </div>

        {/* Filter row */}
        <div
          className="px-8 py-3 flex items-center gap-3 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
        >
          <Filter size={14} style={{ color: 'var(--text-muted)' }} />
          <span style={{ fontSize: '13px', color: 'var(--text-muted)', fontWeight: 500 }}>Filter:</span>

          <select value={filterSpec} onChange={e => setFilterSpec(e.target.value)} style={selectStyle}>
            <option value="ALL">All Specializations</option>
            {specs.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} style={selectStyle}>
            <option value="ALL">All Statuses</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="CANCELLED">Cancelled</option>
            <option value="PENDING">Pending</option>
          </select>

          {/* Only channels this clinic has actually booked through are listed —
              a dropdown of five options where four can never match is noise. */}
          <select value={filterSource} onChange={e => setFilterSource(e.target.value)} style={selectStyle}>
            <option value="ALL">All Channels</option>
            {sources.map(s => <option key={s} value={s}>{SOURCE_META[s].label}</option>)}
          </select>

          {(filterStatus !== 'ALL' || filterSpec !== 'ALL' || filterSource !== 'ALL') && (
            <button
              onClick={() => { setFilterStatus('ALL'); setFilterSpec('ALL'); setFilterSource('ALL'); }}
              style={{ fontSize: '12px', color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Clear filters
            </button>
          )}

          <span style={{ marginLeft: 'auto', fontSize: '12px', color: 'var(--text-muted)' }}>
            {filtered.length} result{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto px-8 py-4">
          <div
            className="rounded-xl overflow-hidden"
            style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)', boxShadow: 'var(--shadow-card)' }}
          >
            {loading ? (
              <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
                <p style={{ fontSize: '14px', color: 'var(--text-muted)' }}>Loading appointments…</p>
              </div>
            ) : error ? (
              <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
                <XCircle size={22} style={{ color: 'var(--destructive)' }} />
                <p style={{ fontSize: '14px', fontWeight: 500, color: 'var(--destructive)' }}>{error}</p>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
                <div
                  className="w-12 h-12 rounded-full flex items-center justify-center"
                  style={{ backgroundColor: 'var(--bg-surface-2)' }}
                >
                  <CalendarCheck size={22} style={{ color: 'var(--text-muted)' }} />
                </div>
                <p style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-secondary)' }}>
                  {appointments.length === 0 ? 'No appointments yet' : 'No appointments match your filters'}
                </p>
                <p style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                  {appointments.length === 0 ? 'Bookings made by the receptionist — on a call or in chat — will show up here.' : 'Try clearing the filters above.'}
                </p>
              </div>
            ) : (
              <table className="w-full" style={{ borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ backgroundColor: 'var(--bg-surface-2)' }}>
                    {['Time', 'Patient', 'Doctor', 'Specialization', 'Booked Via', 'Status', 'Actions'].map(h => (
                      <th
                        key={h}
                        className="px-5 py-3 text-left"
                        style={{
                          fontSize: '11px',
                          textTransform: 'uppercase',
                          letterSpacing: '0.06em',
                          color: 'var(--text-muted)',
                          borderBottom: '1px solid var(--border)',
                          fontWeight: 500,
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((apt, i) => (
                    <tr
                      key={apt.id}
                      data-testid={`apt-row-${apt.id}`}
                      style={{ borderBottom: i < filtered.length - 1 ? '1px solid var(--border)' : 'none' }}
                      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface-2)'; }}
                      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                    >
                      <td className="px-5 py-3.5" style={{ fontSize: '13px', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                        {apt.slot_time}
                      </td>
                      <td className="px-5 py-3.5">
                        {apt.patient_name && (
                          <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                            {apt.patient_name}
                          </div>
                        )}
                        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '12px', color: apt.patient_name ? 'var(--text-muted)' : 'var(--text-primary)' }}>
                          {apt.patient_phone}
                        </div>
                      </td>
                      <td className="px-5 py-3.5" style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>
                        {apt.doctor}
                      </td>
                      <td className="px-5 py-3.5" style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                        {apt.specialization}
                      </td>
                      <td className="px-5 py-3.5">
                        <SourceBadge source={apt.source} />
                      </td>
                      <td className="px-5 py-3.5">
                        <StatusBadge status={apt.status} />
                      </td>
                      <td className="px-5 py-3.5">
                        {apt.status === 'CONFIRMED' && (
                          <button
                            onClick={() => handleCancel(apt.id)}
                            style={{
                              fontSize: '12px',
                              fontWeight: 500,
                              color: 'var(--destructive)',
                              background: 'none',
                              border: 'none',
                              cursor: 'pointer',
                              padding: '4px 8px',
                              borderRadius: '6px',
                            }}
                            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--destructive-dim)'; }}
                            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                          >
                            Cancel
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
