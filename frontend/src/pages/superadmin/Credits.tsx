import { useState, useEffect } from 'react';
import { IndianRupee, Plus, ArrowDownRight, ArrowUpRight, Search, RefreshCw, Settings2, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { API_URL } from '../../api/client';

/**
 * Credits — Super Admin credit management page.
 * View all clinic balances, add credits, change rates.
 */

interface ClinicBalance {
  tenant_id: string;
  clinic_name: string;
  balance: number;
  rate_per_minute: number;
  total_added: number;
  total_deducted: number;
  is_low: boolean;
  updated_at: string;
}

interface Transaction {
  id: string;
  type: string;
  amount: number;
  balance_after: number;
  description: string;
  performed_by: string;
  created_at: string;
}

export default function Credits() {
  const [balances, setBalances] = useState<ClinicBalance[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showTopUp, setShowTopUp] = useState(false);
  const [showRate, setShowRate] = useState(false);
  const [showTxns, setShowTxns] = useState<string | null>(null);
  const [transactions, setTransactions] = useState<Transaction[]>([]);

  // Top-up form
  const [topUpTenant, setTopUpTenant] = useState('');
  const [topUpAmount, setTopUpAmount] = useState('');
  const [topUpDesc, setTopUpDesc] = useState('Admin top-up');
  const [submitting, setSubmitting] = useState(false);

  // Rate form
  const [rateTenant, setRateTenant] = useState('');
  const [rateValue, setRateValue] = useState('');

  useEffect(() => { loadBalances(); }, []);

  const loadBalances = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/credits`);
      if (res.ok) {
        const data = await res.json();
        setBalances(data.credits || []);
      }
    } catch (e) {
      console.error('Failed to load credits:', e);
    }
    setLoading(false);
  };

  const handleTopUp = async () => {
    if (!topUpTenant || !topUpAmount || parseFloat(topUpAmount) <= 0) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/credits/topup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: topUpTenant,
          amount: parseFloat(topUpAmount),
          description: topUpDesc,
        }),
      });
      if (res.ok) {
        setShowTopUp(false);
        setTopUpAmount('');
        loadBalances();
      }
    } catch (e) {
      console.error('Top-up failed:', e);
    }
    setSubmitting(false);
  };

  const handleSetRate = async () => {
    if (!rateTenant || !rateValue || parseFloat(rateValue) < 0) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/credits/set-rate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tenant_id: rateTenant,
          rate_per_minute: parseFloat(rateValue),
        }),
      });
      if (res.ok) {
        setShowRate(false);
        setRateValue('');
        loadBalances();
      }
    } catch (e) {
      console.error('Set rate failed:', e);
    }
    setSubmitting(false);
  };

  const handleInitAll = async () => {
    try {
      const res = await fetch(`${API_URL}/credits/init-all`, { method: 'POST' });
      if (res.ok) loadBalances();
    } catch (e) {
      console.error('Init all failed:', e);
    }
  };

  const loadTransactions = async (tenantId: string) => {
    setShowTxns(tenantId);
    try {
      const res = await fetch(`${API_URL}/credits/${tenantId}/transactions?limit=20`);
      if (res.ok) {
        const data = await res.json();
        setTransactions(data.transactions || []);
      }
    } catch (e) {
      console.error('Failed to load txns:', e);
    }
  };

  const filtered = balances.filter(b =>
    b.clinic_name.toLowerCase().includes(search.toLowerCase())
  );

  const totalBalance = balances.reduce((s, b) => s + b.balance, 0);
  const totalAdded = balances.reduce((s, b) => s + b.total_added, 0);
  const totalDeducted = balances.reduce((s, b) => s + b.total_deducted, 0);
  const lowBalanceCount = balances.filter(b => b.is_low).length;

  return (
    <div style={{ padding: '24px 28px', fontFamily: "'Inter', sans-serif" }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>
            💰 Clinic Credits
          </h1>
          <p style={{ fontSize: 13, color: '#666', margin: '4px 0 0' }}>
            Manage per-clinic ₹ credit balances and billing rates
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={handleInitAll} style={btnSecondary}>
            <RefreshCw size={14} /> Init All Clinics
          </button>
          <button onClick={() => setShowTopUp(true)} style={btnPrimary}>
            <Plus size={14} /> Add Credits
          </button>
          <button onClick={() => setShowRate(true)} style={btnSecondary}>
            <Settings2 size={14} /> Set Rate
          </button>
        </div>
      </div>

      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginBottom: 24 }}>
        <MiniStat label="Total Balance" value={`₹${totalBalance.toFixed(2)}`} icon={<IndianRupee size={16} />} color="#3ECF8E" />
        <MiniStat label="Total Added" value={`₹${totalAdded.toFixed(2)}`} icon={<ArrowUpRight size={16} />} color="#3B82F6" />
        <MiniStat label="Total Deducted" value={`₹${totalDeducted.toFixed(2)}`} icon={<ArrowDownRight size={16} />} color="#ef4444" />
        <MiniStat label="Low Balance" value={String(lowBalanceCount)} icon={<AlertTriangle size={16} />} color={lowBalanceCount > 0 ? '#ef4444' : '#3ECF8E'} />
      </div>

      {/* Search */}
      <div style={{ position: 'relative', marginBottom: 16 }}>
        <Search size={16} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: '#555' }} />
        <input
          placeholder="Search clinics..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={searchInput}
        />
      </div>

      {/* Table */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: '#555' }}>Loading...</div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 60, color: '#555' }}>
          <IndianRupee size={40} color="#333" />
          <p style={{ marginTop: 12 }}>No credit records found</p>
          <p style={{ fontSize: 12, color: '#444' }}>Click "Init All Clinics" to create credit records</p>
        </div>
      ) : (
        <div style={{ background: '#111', borderRadius: 12, border: '1px solid #1A1A1A', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1A1A1A' }}>
                {['Clinic', 'Balance', 'Rate/min', 'Added', 'Deducted', 'Status', 'Actions'].map(h => (
                  <th key={h} style={thStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(b => (
                <tr key={b.tenant_id} style={trStyle}>
                  <td style={tdStyle}>
                    <div style={{ fontWeight: 500, color: '#ddd' }}>{b.clinic_name}</div>
                    <div style={{ fontSize: 11, color: '#555' }}>{b.tenant_id.slice(0, 8)}…</div>
                  </td>
                  <td style={tdStyle}>
                    <span style={{ fontWeight: 600, color: b.is_low ? '#ef4444' : '#3ECF8E', fontSize: 15 }}>
                      ₹{b.balance.toFixed(2)}
                    </span>
                  </td>
                  <td style={tdStyle}>
                    <span style={{ color: '#aaa' }}>₹{b.rate_per_minute.toFixed(2)}</span>
                  </td>
                  <td style={tdStyle}>
                    <span style={{ color: '#3B82F6' }}>₹{b.total_added.toFixed(2)}</span>
                  </td>
                  <td style={tdStyle}>
                    <span style={{ color: '#ef4444' }}>₹{b.total_deducted.toFixed(2)}</span>
                  </td>
                  <td style={tdStyle}>
                    {b.is_low ? (
                      <span style={{ ...statusBadge, color: '#ef4444', background: 'rgba(239,68,68,0.1)', borderColor: 'rgba(239,68,68,0.3)' }}>
                        <AlertTriangle size={11} /> Low
                      </span>
                    ) : (
                      <span style={{ ...statusBadge, color: '#3ECF8E', background: 'rgba(62,207,142,0.1)', borderColor: 'rgba(62,207,142,0.3)' }}>
                        <CheckCircle2 size={11} /> OK
                      </span>
                    )}
                  </td>
                  <td style={tdStyle}>
                    <button
                      onClick={() => {
                        setTopUpTenant(b.tenant_id);
                        setShowTopUp(true);
                      }}
                      style={actionBtn}
                    >
                      + Add
                    </button>
                    <button onClick={() => loadTransactions(b.tenant_id)} style={{ ...actionBtn, marginLeft: 6 }}>
                      History
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Top-Up Modal ── */}
      {showTopUp && (
        <Modal title="Add Credits" onClose={() => setShowTopUp(false)}>
          <div style={formGroup}>
            <label style={formLabel}>Clinic</label>
            <select
              value={topUpTenant}
              onChange={e => setTopUpTenant(e.target.value)}
              style={formSelect}
            >
              <option value="">Select clinic...</option>
              {balances.map(b => (
                <option key={b.tenant_id} value={b.tenant_id}>
                  {b.clinic_name} (₹{b.balance.toFixed(2)})
                </option>
              ))}
            </select>
          </div>
          <div style={formGroup}>
            <label style={formLabel}>Amount (₹)</label>
            <input
              type="number"
              min={1}
              step={0.01}
              value={topUpAmount}
              onChange={e => setTopUpAmount(e.target.value)}
              placeholder="500.00"
              style={formInput}
            />
          </div>
          <div style={formGroup}>
            <label style={formLabel}>Description</label>
            <input
              value={topUpDesc}
              onChange={e => setTopUpDesc(e.target.value)}
              style={formInput}
            />
          </div>
          <button onClick={handleTopUp} disabled={submitting} style={{ ...btnPrimary, width: '100%', marginTop: 8 }}>
            {submitting ? 'Adding...' : `Add ₹${topUpAmount || '0'} Credits`}
          </button>
        </Modal>
      )}

      {/* ── Set Rate Modal ── */}
      {showRate && (
        <Modal title="Set Per-Minute Rate" onClose={() => setShowRate(false)}>
          <div style={formGroup}>
            <label style={formLabel}>Clinic</label>
            <select
              value={rateTenant}
              onChange={e => setRateTenant(e.target.value)}
              style={formSelect}
            >
              <option value="">Select clinic...</option>
              {balances.map(b => (
                <option key={b.tenant_id} value={b.tenant_id}>
                  {b.clinic_name} (current: ₹{b.rate_per_minute.toFixed(2)}/min)
                </option>
              ))}
            </select>
          </div>
          <div style={formGroup}>
            <label style={formLabel}>Rate per minute (₹)</label>
            <input
              type="number"
              min={0}
              step={0.01}
              value={rateValue}
              onChange={e => setRateValue(e.target.value)}
              placeholder="1.50"
              style={formInput}
            />
          </div>
          <button onClick={handleSetRate} disabled={submitting} style={{ ...btnPrimary, width: '100%', marginTop: 8 }}>
            {submitting ? 'Saving...' : 'Update Rate'}
          </button>
        </Modal>
      )}

      {/* ── Transactions Modal ── */}
      {showTxns && (
        <Modal
          title={`Transaction History — ${balances.find(b => b.tenant_id === showTxns)?.clinic_name || showTxns.slice(0, 8)}`}
          onClose={() => setShowTxns(null)}
          wide
        >
          {transactions.length === 0 ? (
            <p style={{ color: '#555', textAlign: 'center', padding: 40 }}>No transactions yet</p>
          ) : (
            <div style={{ maxHeight: 400, overflowY: 'auto' }}>
              {transactions.map(t => (
                <div key={t.id} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '10px 14px', borderBottom: '1px solid #1A1A1A',
                }}>
                  <div>
                    <div style={{ fontSize: 13, color: '#ddd' }}>{t.description || t.type}</div>
                    <div style={{ fontSize: 11, color: '#555' }}>
                      {t.created_at ? new Date(t.created_at).toLocaleString() : '—'} • {t.performed_by || 'system'}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{
                      fontSize: 14, fontWeight: 600,
                      color: t.amount > 0 ? '#3ECF8E' : '#ef4444',
                    }}>
                      {t.amount > 0 ? '+' : ''}₹{t.amount.toFixed(2)}
                    </div>
                    <div style={{ fontSize: 11, color: '#555' }}>
                      Bal: ₹{t.balance_after.toFixed(2)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}


// ── Shared Components ────────────────────────────────────────────────────────

function MiniStat({ label, value, icon, color }: { label: string; value: string; icon: React.ReactNode; color: string }) {
  return (
    <div style={{ background: '#111', borderRadius: 10, padding: '14px 16px', border: '1px solid #1A1A1A' }}>
      <div style={{ color, opacity: 0.8, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: '#fff', letterSpacing: '-0.02em' }}>{value}</div>
      <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>{label}</div>
    </div>
  );
}

function Modal({ title, children, onClose, wide }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }} onClick={onClose}>
      <div
        style={{
          background: '#111', borderRadius: 14, border: '1px solid #1A1A1A',
          padding: '24px 28px', width: wide ? 600 : 420, maxWidth: '90vw',
        }}
        onClick={e => e.stopPropagation()}
      >
        <h3 style={{ fontSize: 16, fontWeight: 600, color: '#fff', margin: '0 0 16px' }}>{title}</h3>
        {children}
      </div>
    </div>
  );
}


// ── Styles ────────────────────────────────────────────────────────────────────

const btnPrimary: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 16px', borderRadius: 8,
  fontSize: 13, fontWeight: 600,
  color: '#000', background: '#3ECF8E', border: 'none',
  cursor: 'pointer', transition: 'opacity 0.15s',
};

const btnSecondary: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 16px', borderRadius: 8,
  fontSize: 13, fontWeight: 500,
  color: '#ccc', background: '#1A1A1A', border: '1px solid #2A2A2A',
  cursor: 'pointer',
};

const searchInput: React.CSSProperties = {
  width: '100%', padding: '10px 12px 10px 36px',
  borderRadius: 8, fontSize: 13,
  background: '#111', border: '1px solid #1A1A1A',
  color: '#ddd', outline: 'none',
};

const thStyle: React.CSSProperties = {
  textAlign: 'left', padding: '10px 14px',
  fontSize: 11, fontWeight: 600, color: '#555',
  textTransform: 'uppercase', letterSpacing: '0.05em',
};

const trStyle: React.CSSProperties = {
  borderBottom: '1px solid #1A1A1A',
};

const tdStyle: React.CSSProperties = {
  padding: '12px 14px', fontSize: 13,
};

const statusBadge: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 4,
  padding: '3px 10px', borderRadius: 6,
  fontSize: 11, fontWeight: 600, border: '1px solid',
};

const actionBtn: React.CSSProperties = {
  padding: '4px 10px', borderRadius: 6,
  fontSize: 11, fontWeight: 500,
  color: '#aaa', background: '#1A1A1A', border: '1px solid #2A2A2A',
  cursor: 'pointer',
};

const formGroup: React.CSSProperties = {
  marginBottom: 14,
};

const formLabel: React.CSSProperties = {
  display: 'block', fontSize: 11, fontWeight: 600,
  color: '#888', textTransform: 'uppercase',
  letterSpacing: '0.05em', marginBottom: 6,
};

const formInput: React.CSSProperties = {
  width: '100%', padding: '9px 12px',
  borderRadius: 8, fontSize: 13,
  background: '#0D0D0D', border: '1px solid #1A1A1A',
  color: '#ddd', outline: 'none',
};

const formSelect: React.CSSProperties = {
  ...formInput,
};
