import { X } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import fetchWithAuth from '../api/client';

interface AvailabilityWindow {
  id?: string;
  day_of_week: number; // 0=Monday .. 6=Sunday
  start_time: string;  // "HH:MM"
  end_time: string;    // "HH:MM"
}

interface DayRow {
  open: boolean;
  start: string;
  end: string;
}

const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

// 30-minute increments, 00:00–23:30 — matches the backend's fixed slot granularity.
const TIME_OPTIONS: { value: string; label: string }[] = Array.from({ length: 48 }, (_, i) => {
  const hour = Math.floor(i / 2);
  const minute = i % 2 === 0 ? '00' : '30';
  const value = `${String(hour).padStart(2, '0')}:${minute}`;
  const displayHour = hour % 12 || 12;
  const ampm = hour < 12 ? 'AM' : 'PM';
  return { value, label: `${displayHour}:${minute} ${ampm}` };
});

function defaultRows(): DayRow[] {
  return DAY_NAMES.map(() => ({ open: false, start: '09:00', end: '17:00' }));
}

interface Props {
  tenantId: string;
  doctorId: string;
  doctorName: string;
  onClose: () => void;
}

export default function DoctorAvailabilityModal({ tenantId, doctorId, doctorName, onClose }: Props) {
  const [rows, setRows] = useState<DayRow[]>(defaultRows());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchWithAuth(`/tenants/${tenantId}/doctors/${doctorId}/availability`)
      .then((windows: AvailabilityWindow[]) => {
        if (cancelled) return;
        const next = defaultRows();
        // v1 UI supports one window per day — if more exist (e.g. from a
        // future split-shift feature), only the first is shown/editable here.
        for (const w of windows || []) {
          if (!next[w.day_of_week].open) {
            next[w.day_of_week] = { open: true, start: w.start_time, end: w.end_time };
          }
        }
        setRows(next);
        setError(null);
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message || 'Failed to load availability'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [tenantId, doctorId]);

  const updateRow = (day: number, patch: Partial<DayRow>) => {
    setRows(prev => prev.map((r, i) => (i === day ? { ...r, ...patch } : r)));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    const payload = rows
      .map((r, day) => ({ day_of_week: day, start_time: r.start, end_time: r.end, open: r.open }))
      .filter(r => r.open)
      .map(({ day_of_week, start_time, end_time }) => ({ day_of_week, start_time, end_time }));
    try {
      await fetchWithAuth(`/tenants/${tenantId}/doctors/${doctorId}/availability`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      onClose();
    } catch (e) {
      setError((e as Error).message || 'Failed to save availability');
    } finally {
      setSaving(false);
    }
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
          maxWidth: '520px',
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
          margin: '0 16px',
        }}
      >
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              Weekly Hours
            </h2>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>{doctorName}</p>
          </div>
          <button
            onClick={onClose}
            style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
          >
            <X size={18} />
          </button>
        </div>

        {error && (
          <p style={{ fontSize: '13px', color: 'var(--destructive)', marginBottom: '12px' }}>{error}</p>
        )}

        {loading ? (
          <p style={{ fontSize: '14px', color: 'var(--text-muted)', textAlign: 'center', padding: '24px 0' }}>
            Loading…
          </p>
        ) : (
          <div className="space-y-2" style={{ maxHeight: '360px', overflowY: 'auto' }}>
            {DAY_NAMES.map((name, day) => {
              const row = rows[day];
              return (
                <div
                  key={name}
                  className="flex items-center gap-3 py-2"
                  style={{ borderBottom: day < 6 ? '1px solid var(--border)' : 'none' }}
                >
                  <label className="flex items-center gap-2" style={{ width: '110px', cursor: 'pointer', flexShrink: 0 }}>
                    <input
                      type="checkbox"
                      checked={row.open}
                      onChange={e => updateRow(day, { open: e.target.checked })}
                    />
                    <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>{name}</span>
                  </label>
                  {row.open ? (
                    <div className="flex items-center gap-2 flex-1">
                      <select
                        value={row.start}
                        onChange={e => updateRow(day, { start: e.target.value })}
                        style={selectStyle}
                      >
                        {TIME_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                      <span style={{ color: 'var(--text-muted)', fontSize: '13px' }}>to</span>
                      <select
                        value={row.end}
                        onChange={e => updateRow(day, { end: e.target.value })}
                        style={selectStyle}
                      >
                        {TIME_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    </div>
                  ) : (
                    <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>Closed</span>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="flex gap-3 pt-5">
          <button
            type="button"
            onClick={onClose}
            style={{
              flex: 1, padding: '10px', borderRadius: '8px', fontSize: '14px', fontWeight: 500,
              backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={loading || saving}
            style={{
              flex: 1, padding: '10px', borderRadius: '8px', fontSize: '14px', fontWeight: 600,
              backgroundColor: 'var(--accent)', border: 'none', color: '#000',
              cursor: loading || saving ? 'default' : 'pointer', opacity: loading || saving ? 0.6 : 1,
            }}
          >
            {saving ? 'Saving…' : 'Save Hours'}
          </button>
        </div>
      </div>
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  flex: 1,
  padding: '8px 10px',
  fontSize: '13px',
  borderRadius: '8px',
  outline: 'none',
  backgroundColor: 'var(--bg-surface-2)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
  cursor: 'pointer',
};
