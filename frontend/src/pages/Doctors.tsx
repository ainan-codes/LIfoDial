import { Clock, Edit2, Plus, Trash2, UserCheck, X } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { SPECIALIZATIONS, type Doctor as BaseDoctor } from '../fixtures/data';
import fetchWithAuth from '../api/client';
import { getTenantId } from '../api/auth';
import DoctorAvailabilityModal from '../components/DoctorAvailabilityModal';

// Widened locally with leave_reason — the shared fixture Doctor type doesn't
// have it (nothing else that imports it needs it).
type Doctor = BaseDoctor & { leave_reason?: string | null };

interface BackendDoctor {
  id: string;
  name: string;
  specialization: string;
  his_doctor_id: string | null;
  is_available: boolean;
  leave_reason: string | null;
}

function fromBackend(d: BackendDoctor): Doctor {
  return {
    id: d.id,
    name: d.name,
    specialization: d.specialization,
    his_doctor_id: d.his_doctor_id ?? '',
    available: d.is_available,
    leave_reason: d.leave_reason,
  };
}

// ── Initials helper ───────────────────────────────────────────────────────────
function initials(name: string) {
  return name
    .replace(/^Dr\.\s*/, '')
    .split(' ')
    .slice(0, 2)
    .map(w => w[0])
    .join('')
    .toUpperCase();
}

// ── Modal ─────────────────────────────────────────────────────────────────────
interface ModalProps {
  doctor?: Doctor | null;
  onSave: (d: Omit<Doctor, 'id'>) => void;
  onClose: () => void;
}

function DoctorModal({ doctor, onSave, onClose }: ModalProps) {
  const [name, setName]           = useState(doctor?.name ?? '');
  const [spec, setSpec]           = useState(doctor?.specialization ?? 'General Physician');
  const [hisId, setHisId]         = useState(doctor?.his_doctor_id ?? '');
  const [available, setAvailable] = useState(doctor?.available ?? true);
  const [leaveReason, setLeaveReason] = useState(doctor?.leave_reason ?? '');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    onSave({
      name: name.trim(), specialization: spec, his_doctor_id: hisId.trim(), available,
      leave_reason: available ? null : (leaveReason.trim() || null),
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full rounded-xl p-6 relative"
        style={{
          maxWidth: '460px',
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
          margin: '0 16px',
        }}
      >
        <div className="flex items-center justify-between mb-5">
          <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            {doctor ? 'Edit Doctor' : 'Add Doctor'}
          </h2>
          <button
            onClick={onClose}
            style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Doctor name */}
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Doctor Name *
            </label>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Dr. Suresh Menon"
              required
              style={{
                width: '100%',
                padding: '10px 12px',
                fontSize: '14px',
                borderRadius: '8px',
                outline: 'none',
                backgroundColor: 'var(--bg-surface-2)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
                transition: 'border-color 0.15s, box-shadow 0.15s',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)'; }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
            />
          </div>

          {/* Specialization */}
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Specialization *
            </label>
            <select
              value={spec}
              onChange={e => setSpec(e.target.value)}
              style={{
                width: '100%',
                padding: '10px 12px',
                fontSize: '14px',
                borderRadius: '8px',
                outline: 'none',
                backgroundColor: 'var(--bg-surface-2)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; }}
            >
              {SPECIALIZATIONS.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {/* HIS Doctor ID */}
          <div>
            <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              HIS Doctor ID <span style={{ color: 'var(--text-muted)', textTransform: 'none', fontWeight: 400 }}>(optional)</span>
            </label>
            <input
              type="text"
              value={hisId}
              onChange={e => setHisId(e.target.value)}
              placeholder="e.g. HIS-D007"
              style={{
                width: '100%',
                padding: '10px 12px',
                fontSize: '14px',
                borderRadius: '8px',
                outline: 'none',
                backgroundColor: 'var(--bg-surface-2)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
                transition: 'border-color 0.15s, box-shadow 0.15s',
                fontFamily: "'JetBrains Mono', monospace",
              }}
              onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)'; }}
              onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
            />
          </div>

          {/* Available toggle */}
          <div className="flex items-center justify-between py-2">
            <div>
              <p style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-primary)', margin: 0 }}>Available by default</p>
              <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>This doctor will be offered to callers</p>
            </div>
            <button
              type="button"
              onClick={() => setAvailable(v => !v)}
              style={{
                position: 'relative',
                width: '44px',
                height: '24px',
                borderRadius: '9999px',
                border: 'none',
                cursor: 'pointer',
                backgroundColor: available ? 'var(--accent)' : 'var(--bg-surface-3)',
                transition: 'background-color 0.2s',
                flexShrink: 0,
              }}
            >
              <span
                style={{
                  position: 'absolute',
                  top: '2px',
                  left: available ? '22px' : '2px',
                  width: '20px',
                  height: '20px',
                  borderRadius: '50%',
                  backgroundColor: '#fff',
                  transition: 'left 0.2s',
                }}
              />
            </button>
          </div>

          {/* Leave reason — only shown while marking the doctor unavailable */}
          {!available && (
            <div>
              <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                Reason <span style={{ color: 'var(--text-muted)', textTransform: 'none', fontWeight: 400 }}>(optional — shown to callers by the AI)</span>
              </label>
              <input
                type="text"
                value={leaveReason}
                onChange={e => setLeaveReason(e.target.value)}
                placeholder="e.g. On leave until Monday"
                style={{
                  width: '100%',
                  padding: '10px 12px',
                  fontSize: '14px',
                  borderRadius: '8px',
                  outline: 'none',
                  backgroundColor: 'var(--bg-surface-2)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                  transition: 'border-color 0.15s, box-shadow 0.15s',
                }}
                onFocus={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)'; }}
                onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
              />
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              style={{
                flex: 1,
                padding: '10px',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: 500,
                backgroundColor: 'transparent',
                border: '1px solid var(--border)',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; }}
            >
              Cancel
            </button>
            <button
              type="submit"
              style={{
                flex: 1,
                padding: '10px',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: 600,
                backgroundColor: 'var(--accent)',
                border: 'none',
                color: '#000',
                cursor: 'pointer',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-hover)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent)'; }}
            >
              {doctor ? 'Save Changes' : 'Add Doctor'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Doctor Card ───────────────────────────────────────────────────────────────
interface DoctorCardProps {
  doctor: Doctor;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
  onSetHours: () => void;
}

function DoctorCard({ doctor, onEdit, onDelete, onToggle, onSetHours }: DoctorCardProps) {
  return (
    <div
      className="rounded-xl p-5 flex flex-col gap-4 transition-all"
      style={{
        backgroundColor: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        boxShadow: 'var(--shadow-card)',
      }}
      onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)'; }}
      onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; }}
    >
      {/* Header row */}
      <div className="flex items-start justify-between">
        {/* Avatar + name */}
        <div className="flex items-center gap-3">
          <div
            className="w-12 h-12 rounded-full flex items-center justify-center font-bold flex-shrink-0"
            style={{
              backgroundColor: 'var(--accent-dim)',
              color: 'var(--accent)',
              fontSize: '15px',
              border: '1px solid var(--accent-border)',
            }}
          >
            {initials(doctor.name)}
          </div>
          <div>
            <p style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              {doctor.name}
            </p>
            {doctor.his_doctor_id && (
              <p style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: "'JetBrains Mono', monospace", marginTop: '2px' }}>
                {doctor.his_doctor_id}
              </p>
            )}
          </div>
        </div>
        {/* Action buttons */}
        <div className="flex items-center gap-1">
          <button
            onClick={onSetHours}
            title="Set weekly hours"
            style={{
              padding: '6px',
              borderRadius: '6px',
              backgroundColor: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-muted)',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface-2)'; (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)'; }}
          >
            <Clock size={14} />
          </button>
          <button
            onClick={onEdit}
            title="Edit"
            style={{
              padding: '6px',
              borderRadius: '6px',
              backgroundColor: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-muted)',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--bg-surface-2)'; (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)'; }}
          >
            <Edit2 size={14} />
          </button>
          <button
            onClick={onDelete}
            title="Delete"
            style={{
              padding: '6px',
              borderRadius: '6px',
              backgroundColor: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-muted)',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--destructive-dim)'; (e.currentTarget as HTMLElement).style.color = 'var(--destructive)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)'; }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {/* Specialization badge */}
      <div>
        <span
          style={{
            display: 'inline-block',
            padding: '3px 10px',
            borderRadius: '9999px',
            fontSize: '12px',
            fontWeight: 500,
            color: 'var(--accent)',
            backgroundColor: 'var(--accent-dim)',
            border: '1px solid var(--accent-border)',
          }}
        >
          {doctor.specialization}
        </span>
      </div>

      {/* Availability toggle */}
      <div
        className="flex items-center justify-between pt-3"
        style={{ borderTop: '1px solid var(--border)' }}
      >
        <div className="flex items-center gap-2">
          <div
            className="w-1.5 h-1.5 rounded-full"
            style={{ backgroundColor: doctor.available ? 'var(--accent)' : 'var(--text-muted)' }}
          />
          <span style={{ fontSize: '13px', fontWeight: 500, color: doctor.available ? 'var(--accent)' : 'var(--text-muted)' }}>
            {doctor.available ? 'Online' : (doctor.leave_reason ? `On leave — ${doctor.leave_reason}` : 'Offline')}
          </span>
        </div>
        <button
          onClick={onToggle}
          style={{
            position: 'relative',
            width: '40px',
            height: '22px',
            borderRadius: '9999px',
            border: 'none',
            cursor: 'pointer',
            backgroundColor: doctor.available ? 'var(--accent)' : 'var(--bg-surface-3)',
            transition: 'background-color 0.2s',
          }}
        >
          <span
            style={{
              position: 'absolute',
              top: '2px',
              left: doctor.available ? '20px' : '2px',
              width: '18px',
              height: '18px',
              borderRadius: '50%',
              backgroundColor: '#fff',
              transition: 'left 0.2s',
            }}
          />
        </button>
      </div>
    </div>
  );
}

// ── Doctors Page ──────────────────────────────────────────────────────────────
export default function Doctors() {
  const tenantId = getTenantId();
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Doctor | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [availabilityTarget, setAvailabilityTarget] = useState<Doctor | null>(null);
  // Set when handleAdd finds a same-name doctor already in the loaded list —
  // shows a confirm dialog instead of silently creating a duplicate (the
  // actual "Dr. Salman x3" bug). Holds the submitted form data so "Add
  // anyway" can resubmit it with allow_duplicate_name: true.
  const [pendingDuplicate, setPendingDuplicate] = useState<Omit<Doctor, 'id'> | null>(null);

  useEffect(() => {
    if (!tenantId) return;
    let cancelled = false;
    setLoading(true);
    fetchWithAuth(`/tenants/${tenantId}/doctors`)
      .then((data: BackendDoctor[]) => {
        if (cancelled) return;
        setDoctors((data || []).map(fromBackend));
        setError(null);
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message || 'Failed to load doctors'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tenantId]);

  const submitAdd = async (data: Omit<Doctor, 'id'>, allowDuplicateName: boolean) => {
    if (!tenantId) return;
    try {
      const created: BackendDoctor = await fetchWithAuth(`/tenants/${tenantId}/doctors`, {
        method: 'POST',
        body: JSON.stringify({
          name: data.name, specialization: data.specialization,
          his_doctor_id: data.his_doctor_id || null, is_available: data.available,
          allow_duplicate_name: allowDuplicateName,
        }),
      });
      setDoctors(prev => [...prev, fromBackend(created)]);
      setModalOpen(false);
      setPendingDuplicate(null);
    } catch (e) {
      setError((e as Error).message || 'Failed to add doctor');
    }
  };

  const handleAdd = async (data: Omit<Doctor, 'id'>) => {
    const nameMatch = doctors.some(d => d.name.trim().toLowerCase() === data.name.trim().toLowerCase());
    if (nameMatch) {
      // Ask before creating a second row for what looks like the same
      // doctor — this is the client-side half of the duplicate-name guard;
      // the backend 409 (checked server-side too) is the backstop for a race.
      // Close the Add modal so only the confirm dialog is on screen.
      setModalOpen(false);
      setPendingDuplicate(data);
      return;
    }
    await submitAdd(data, false);
  };

  const handleEdit = async (data: Omit<Doctor, 'id'>) => {
    if (!tenantId || !editTarget) return;
    try {
      const updated: BackendDoctor = await fetchWithAuth(`/tenants/${tenantId}/doctors/${editTarget.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: data.name, specialization: data.specialization,
          his_doctor_id: data.his_doctor_id || null,
          is_available: data.available, leave_reason: data.leave_reason ?? null,
        }),
      });
      setDoctors(prev => prev.map(d => d.id === editTarget.id ? fromBackend(updated) : d));
      setEditTarget(null);
    } catch (e) {
      setError((e as Error).message || 'Failed to update doctor');
    }
  };

  const handleDelete = async (id: string) => {
    if (!tenantId) return;
    try {
      await fetchWithAuth(`/tenants/${tenantId}/doctors/${id}`, { method: 'DELETE' });
      setDoctors(prev => prev.filter(d => d.id !== id));
    } catch (e) {
      setError((e as Error).message || 'Failed to remove doctor');
    } finally {
      setDeleteConfirm(null);
    }
  };

  const handleToggle = async (id: string) => {
    if (!tenantId) return;
    const target = doctors.find(d => d.id === id);
    if (!target) return;
    const nextAvailable = !target.available;
    // Optimistic update — revert if the request fails.
    setDoctors(prev => prev.map(d => d.id === id ? { ...d, available: nextAvailable } : d));
    try {
      const updated: BackendDoctor = await fetchWithAuth(`/tenants/${tenantId}/doctors/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_available: nextAvailable, leave_reason: target.leave_reason ?? null }),
      });
      setDoctors(prev => prev.map(d => d.id === id ? fromBackend(updated) : d));
    } catch (e) {
      setDoctors(prev => prev.map(d => d.id === id ? { ...d, available: !nextAvailable } : d));
      setError((e as Error).message || 'Failed to update availability');
    }
  };

  const onlineCount = doctors.filter(d => d.available).length;

  return (
    <div data-testid="doctors-page" className="h-full flex flex-col">
      {/* Top bar */}
      <div
        className="px-8 py-5 flex items-center justify-between flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      >
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em', margin: 0 }}>
            Doctors
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
            {doctors.length} registered · {onlineCount} online
          </p>
        </div>
        <button
          onClick={() => { setEditTarget(null); setModalOpen(true); }}
          className="flex items-center gap-2"
          style={{
            padding: '8px 16px',
            borderRadius: '8px',
            fontSize: '14px',
            fontWeight: 600,
            color: '#000',
            backgroundColor: 'var(--accent)',
            border: 'none',
            cursor: 'pointer',
            transition: 'background-color 0.15s',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-hover)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent)'; }}
        >
          <Plus size={16} />
          Add Doctor
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 p-8 overflow-y-auto" style={{ backgroundColor: 'var(--bg-page)' }}>
        {error && (
          <p style={{ fontSize: '13px', color: 'var(--destructive)', marginBottom: '16px' }}>{error}</p>
        )}
        {loading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-24 text-center">
            <p style={{ fontSize: '14px', color: 'var(--text-muted)' }}>Loading doctors…</p>
          </div>
        ) : doctors.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ backgroundColor: 'var(--bg-surface-2)' }}
            >
              <UserCheck size={28} style={{ color: 'var(--text-muted)' }} />
            </div>
            <div>
              <p style={{ fontSize: '16px', fontWeight: 500, color: 'var(--text-secondary)' }}>No doctors yet</p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>
                Add doctors so the system knows who to book appointments with.
              </p>
            </div>
            <button
              onClick={() => { setEditTarget(null); setModalOpen(true); }}
              style={{
                padding: '8px 20px',
                borderRadius: '8px',
                fontSize: '14px',
                fontWeight: 600,
                color: '#000',
                backgroundColor: 'var(--accent)',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Add First Doctor
            </button>
          </div>
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            {doctors.map(doc => (
              <DoctorCard
                key={doc.id}
                doctor={doc}
                onEdit={() => { setEditTarget(doc); setModalOpen(true); }}
                onDelete={() => setDeleteConfirm(doc.id)}
                onToggle={() => handleToggle(doc.id)}
                onSetHours={() => setAvailabilityTarget(doc)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Add/Edit modal */}
      {(modalOpen || editTarget) && (
        <DoctorModal
          doctor={editTarget}
          onSave={editTarget ? handleEdit : handleAdd}
          onClose={() => { setModalOpen(false); setEditTarget(null); }}
        />
      )}

      {/* Weekly availability modal */}
      {availabilityTarget && tenantId && (
        <DoctorAvailabilityModal
          tenantId={tenantId}
          doctorId={availabilityTarget.id}
          doctorName={availabilityTarget.name}
          onClose={() => setAvailabilityTarget(null)}
        />
      )}

      {/* Duplicate-name confirm dialog */}
      {pendingDuplicate && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
        >
          <div
            className="rounded-xl p-6"
            style={{
              width: '380px',
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
              margin: '0 16px',
            }}
          >
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 8px' }}>
              Doctor already exists
            </h3>
            <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
              A doctor named "{pendingDuplicate.name}" is already in your list. Add another one with the
              same name, or did you mean to edit the existing entry instead?
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setPendingDuplicate(null)}
                style={{ flex: 1, padding: '9px', borderRadius: '8px', fontSize: '14px', fontWeight: 500, backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                onClick={() => submitAdd(pendingDuplicate, true)}
                style={{ flex: 1, padding: '9px', borderRadius: '8px', fontSize: '14px', fontWeight: 600, backgroundColor: 'var(--accent)', border: 'none', color: '#000', cursor: 'pointer' }}
              >
                Add anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm dialog */}
      {deleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
        >
          <div
            className="rounded-xl p-6"
            style={{
              width: '360px',
              backgroundColor: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
              margin: '0 16px',
            }}
          >
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 8px' }}>
              Remove doctor?
            </h3>
            <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
              {doctors.find(d => d.id === deleteConfirm)?.name} will be removed from the AI's knowledge.
              This also removes their existing appointments — consider marking them on leave instead if you just want to pause bookings.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setDeleteConfirm(null)}
                style={{ flex: 1, padding: '9px', borderRadius: '8px', fontSize: '14px', fontWeight: 500, backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                onClick={() => handleDelete(deleteConfirm)}
                style={{ flex: 1, padding: '9px', borderRadius: '8px', fontSize: '14px', fontWeight: 600, backgroundColor: 'var(--destructive-dim)', border: '1px solid rgba(248,113,113,0.3)', color: 'var(--destructive)', cursor: 'pointer' }}
              >
                Remove
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
