// ─── Shared SA Components ─────────────────────────────────────────────────────
import React, { ReactNode, useEffect } from 'react';
import { useSAStore, PlanTier } from '../../store/saStore';
import { X, CheckCircle, XCircle, Info } from 'lucide-react';

// ── PlanBadge ─────────────────────────────────────────────────────────────────
const PLAN_COLORS: Record<PlanTier, { bg: string; text: string; border: string }> = {
  Free:       { bg: '#1f1f1f', text: '#888',    border: '#2E2E2E' },
  Pro:        { bg: '#1a2540', text: '#60a5fa', border: '#2563eb40' },
  Enterprise: { bg: '#0d2e1e', text: '#3ECF8E', border: '#3ECF8E40' },
};

export function PlanBadge({ plan }: { plan: PlanTier }) {
  const c = PLAN_COLORS[plan];
  return (
    <span style={{
      backgroundColor: c.bg, color: c.text, border: `1px solid ${c.border}`,
      padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 700,
      letterSpacing: '0.04em', display: 'inline-block',
    }}>
      {plan.toUpperCase()}
    </span>
  );
}

// ── StatusBadge ───────────────────────────────────────────────────────────────
export function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, { dot: string; text: string }> = {
    Active:    { dot: '#3ECF8E', text: '#3ECF8E' },
    Pending:   { dot: '#f59e0b', text: '#f59e0b' },
    Suspended: { dot: '#ef4444', text: '#ef4444' },
    Approved:  { dot: '#3ECF8E', text: '#3ECF8E' },
    Rejected:  { dot: '#ef4444', text: '#ef4444' },
    Paid:      { dot: '#3ECF8E', text: '#3ECF8E' },
    Overdue:   { dot: '#ef4444', text: '#ef4444' },
    // Appointment statuses — previously absent, so every appointment row on
    // the superadmin table rendered this badge in the fallback grey.
    Confirmed:   { dot: '#3ECF8E', text: '#3ECF8E' },
    Cancelled:   { dot: '#ef4444', text: '#ef4444' },
    Rescheduled: { dot: '#60A5FA', text: '#60A5FA' },
    Completed:   { dot: '#3ECF8E', text: '#3ECF8E' },
    'No-Show':   { dot: '#ef4444', text: '#ef4444' },
  };
  const c = colors[status] ?? { dot: '#888', text: '#888' };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
      <div style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: c.dot, flexShrink: 0 }} />
      <span style={{ fontSize: '12px', color: c.text, fontWeight: 600 }}>{status}</span>
    </div>
  );
}

// ── StatCard ──────────────────────────────────────────────────────────────────
export function StatCard({
  label, value, icon: Icon, delta, prefix = '',
}: {
  label: string; value: string | number; icon: React.ElementType; delta?: string; prefix?: string;
}) {
  return (
    <div style={{
      backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E',
      borderRadius: '12px', padding: '20px 24px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <span style={{ color: '#888', fontSize: '12px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
        <Icon size={16} color="#3ECF8E" />
      </div>
      <div style={{ fontSize: '28px', fontWeight: 800, color: '#fff', fontFamily: "'JetBrains Mono', monospace", letterSpacing: '-0.02em' }}>
        {prefix}{typeof value === 'number' ? value.toLocaleString('en-IN') : value}
      </div>
      {delta && <p style={{ marginTop: '6px', fontSize: '11px', color: '#3ECF8E' }}>{delta}</p>}
    </div>
  );
}

// ── Modal Portal ──────────────────────────────────────────────────────────────
export function Modal({ title, children, onClose, width = 480 }: {
  title: string; children: ReactNode; onClose: () => void; width?: number;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  return (
    <div style={{
      position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      backdropFilter: 'blur(4px)',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E',
        borderRadius: '16px', width: '100%', maxWidth: `${width}px`,
        maxHeight: '90vh', overflowY: 'auto', padding: '28px',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px' }}>
          <h2 style={{ fontSize: '18px', fontWeight: 700, color: '#fff', margin: 0 }}>{title}</h2>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', color: '#888', cursor: 'pointer',
            padding: '4px', borderRadius: '6px', display: 'flex', alignItems: 'center',
          }}>
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ── Toast Container ───────────────────────────────────────────────────────────
export function ToastContainer() {
  const { toasts, removeToast } = useSAStore();

  const icons = { success: CheckCircle, error: XCircle, info: Info };
  const colors = { success: '#3ECF8E', error: '#ef4444', info: '#60a5fa' };

  if (toasts.length === 0) return null;
  return (
    <div style={{
      position: 'fixed', bottom: '24px', right: '24px', zIndex: 2000,
      display: 'flex', flexDirection: 'column', gap: '10px',
    }}>
      {toasts.map(t => {
        const Icon = icons[t.type];
        return (
          <div key={t.id} style={{
            display: 'flex', alignItems: 'center', gap: '10px',
            backgroundColor: '#1A1A1A', border: `1px solid ${colors[t.type]}40`,
            borderRadius: '10px', padding: '12px 16px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            minWidth: '280px', animation: 'slideInRight 0.2s ease',
          }}>
            <Icon size={16} color={colors[t.type]} style={{ flexShrink: 0 }} />
            <span style={{ color: '#fff', fontSize: '13px', fontWeight: 500, flex: 1 }}>{t.message}</span>
            <button onClick={() => removeToast(t.id)} style={{
              background: 'none', border: 'none', color: '#888', cursor: 'pointer', padding: '2px',
            }}>
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ── EmptyState ────────────────────────────────────────────────────────────────
export function EmptyState({ icon: Icon, message, sub }: {
  icon: React.ElementType; message: string; sub?: string;
}) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', padding: '64px 32px', color: '#888',
    }}>
      <Icon size={40} color="#2E2E2E" style={{ marginBottom: '16px' }} />
      <p style={{ fontSize: '15px', fontWeight: 600, color: '#555', margin: '0 0 6px' }}>{message}</p>
      {sub && <p style={{ fontSize: '13px', color: '#444' }}>{sub}</p>}
    </div>
  );
}

// ── Spinner Button ────────────────────────────────────────────────────────────
export function SpinBtn({
  onClick, loading, children, variant = 'primary', disabled = false,
  style: extraStyle = {},
}: {
  onClick: () => void; loading?: boolean; children: ReactNode;
  variant?: 'primary' | 'danger' | 'ghost'; disabled?: boolean;
  style?: React.CSSProperties;
}) {
  const baseStyle: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
    padding: '8px 16px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
    cursor: loading || disabled ? 'not-allowed' : 'pointer',
    opacity: loading || disabled ? 0.6 : 1, border: 'none', transition: 'all 0.15s',
    ...extraStyle,
  };
  const variants: Record<string, React.CSSProperties> = {
    primary: { backgroundColor: '#3ECF8E', color: '#000' },
    danger:  { backgroundColor: 'transparent', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' },
    ghost:   { backgroundColor: '#2E2E2E', color: '#fff' },
  };

  return (
    <button
      onClick={onClick}
      disabled={loading || disabled}
      style={{ ...baseStyle, ...variants[variant] }}
    >
      {loading
        ? <><div style={{ width: 12, height: 12, border: '2px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 0.6s linear infinite' }} />Processing...</>
        : children
      }
    </button>
  );
}
