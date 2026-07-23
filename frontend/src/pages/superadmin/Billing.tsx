import React, { useState, useEffect } from 'react';
import { useSAStore, BillingPlan, PlanTier } from '../../store/saStore';
import { PlanBadge, StatCard } from '../../components/superadmin/SAShared';
import { IndianRupee, TrendingUp, Receipt, ToggleLeft, ToggleRight, Edit2, Check, Building2, Wallet, AlertCircle } from 'lucide-react';
import fetchWithAuth from '../../api/client';

// Shape returned by GET /admin/billing — every field is a real query result.
interface BillingData {
  has_paid_billing: boolean;
  billing_note: string;
  total_clinics: number;
  active_clinics: number;
  clinics_by_plan: Record<string, number>;
  paid_plan_clinics: number;
  mrr: number;
  total_collected: number;
  credits: { total_added: number; total_used: number; outstanding_balance: number };
  recent_transactions: Array<{
    id: string; clinic_name: string; type: string; amount: number;
    balance_after: number | null; description: string | null; created_at: string | null;
  }>;
}

const inr = (n: number) => `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—';

function PlanCard({ plan }: { plan: BillingPlan }) {
  const { updatePlanPrice, togglePlanAvailability, addToast } = useSAStore();
  const [editing, setEditing] = useState(false);
  const [draftPrice, setDraftPrice] = useState(plan.price.toString());

  const tierColors: Record<PlanTier, string> = {
    Free: '#888', Pro: '#60a5fa', Enterprise: '#3ECF8E',
  };
  const color = tierColors[plan.tier];

  const savePrice = () => {
    const n = parseInt(draftPrice, 10);
    if (isNaN(n) || n < 0) { addToast('Invalid price value', 'error'); return; }
    updatePlanPrice(plan.id, n);
    setEditing(false);
  };

  return (
    <div style={{
      backgroundColor: '#1A1A1A', border: `1px solid ${color}30`,
      borderRadius: '14px', padding: '28px', display: 'flex', flexDirection: 'column', gap: '20px',
      opacity: plan.available ? 1 : 0.5,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <PlanBadge plan={plan.tier} />
          <div style={{ marginTop: '16px', display: 'flex', alignItems: 'baseline', gap: '4px' }}>
            {editing ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ color, fontSize: '22px', fontWeight: 800 }}>₹</span>
                <input
                  type="number" value={draftPrice} onChange={e => setDraftPrice(e.target.value)}
                  style={{ backgroundColor: '#0F0F0F', border: '1px solid #3ECF8E', borderRadius: '6px', padding: '4px 8px', color: '#fff', fontSize: '24px', fontWeight: 800, width: '120px', outline: 'none' }}
                  autoFocus
                />
                <button onClick={savePrice} style={{ backgroundColor: '#3ECF8E', border: 'none', borderRadius: '6px', padding: '6px 10px', cursor: 'pointer', color: '#000' }}>
                  <Check size={14} />
                </button>
              </div>
            ) : (
              <>
                <span style={{ color, fontSize: '32px', fontWeight: 800, fontFamily: 'monospace' }}>
                  ₹{plan.price.toLocaleString('en-IN')}
                </span>
                <span style={{ color: '#888', fontSize: '13px' }}>/mo</span>
                <button onClick={() => setEditing(true)} style={{ background: 'none', border: 'none', color: '#555', cursor: 'pointer', padding: '4px', marginLeft: '4px' }}>
                  <Edit2 size={12} />
                </button>
              </>
            )}
          </div>
        </div>
        <button
          onClick={() => togglePlanAvailability(plan.id)}
          style={{ background: 'none', border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', color: plan.available ? '#3ECF8E' : '#555', fontSize: '12px', fontWeight: 600 }}
        >
          {plan.available ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
          {plan.available ? 'Available' : 'Disabled'}
        </button>
      </div>

      <div style={{ display: 'grid', gap: '8px' }}>
        {[
          { label: 'Call Minutes', value: plan.call_minutes.toLocaleString() },
          { label: 'Max Concurrent', value: `${plan.max_concurrent} sessions` },
          { label: 'Model Tier', value: plan.model_tier.charAt(0).toUpperCase() + plan.model_tier.slice(1) },
          { label: 'Overage Rate', value: `₹${plan.overage_rate}/min` },
        ].map(row => (
          <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid #2E2E2E' }}>
            <span style={{ color: '#888', fontSize: '12px' }}>{row.label}</span>
            <span style={{ color: '#fff', fontSize: '12px', fontWeight: 600, fontFamily: 'monospace' }}>{row.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Billing() {
  const { billingPlans } = useSAStore();
  const [data, setData] = useState<BillingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetchWithAuth('/admin/billing');
        if (alive) setData(res as BillingData);
      } catch (e: any) {
        if (alive) setError(e?.message || 'Failed to load billing data');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  const planEntries = data ? Object.entries(data.clinics_by_plan) : [];
  const maxPlanCount = Math.max(1, ...planEntries.map(([, n]) => n));

  return (
    <div style={{ padding: '32px' }}>
      <div style={{ marginBottom: '28px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>Billing</h1>
        <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>Manage plans, pricing, and revenue</p>
      </div>

      {loading && <p style={{ color: '#888', fontSize: '13px' }}>Loading billing data…</p>}
      {error && (
        <div style={{ backgroundColor: '#2A1A1A', border: '1px solid #7f1d1d', borderRadius: '10px', padding: '14px 18px', color: '#fca5a5', fontSize: '13px', marginBottom: '20px' }}>
          Could not load billing data: {error}
        </div>
      )}

      {data && (
        <>
          {/* Honest state banner — no subscription/invoice billing exists yet. */}
          {!data.has_paid_billing && (
            <div style={{ backgroundColor: '#1A2230', border: '1px solid #2c4a6e', borderRadius: '10px', padding: '14px 18px', marginBottom: '24px', display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
              <AlertCircle size={16} style={{ color: '#60a5fa', flexShrink: 0, marginTop: '1px' }} />
              <span style={{ color: '#9db8d6', fontSize: '12.5px', lineHeight: 1.5 }}>{data.billing_note}</span>
            </div>
          )}

          {/* Stats — every value is a real query result. */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px', marginBottom: '28px' }}>
            <StatCard label="Active Clinics" value={`${data.active_clinics} / ${data.total_clinics}`} icon={Building2} />
            <StatCard label="Credits Collected" value={inr(data.credits.total_added)} icon={TrendingUp} />
            <StatCard label="Credits Used" value={inr(data.credits.total_used)} icon={Receipt} />
            <StatCard label="Outstanding Balance" value={inr(data.credits.outstanding_balance)} icon={Wallet} />
          </div>

          {/* Plan Configuration — plan definitions (pricing not yet billed). */}
          <div style={{ marginBottom: '28px' }}>
            <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#888', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
              Plan Configuration
            </h2>
            <p style={{ color: '#666', fontSize: '12px', marginBottom: '16px' }}>
              Plan definitions only — prices are not yet tied to a billing/payment system, so they do not generate charges.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
              {billingPlans.map(p => <PlanCard key={p.id} plan={p} />)}
            </div>
          </div>

          {/* Clinics by Plan — real counts (replaces the fabricated revenue chart). */}
          <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px', marginBottom: '24px' }}>
            <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '20px' }}>
              Clinics by Plan
            </h2>
            {data.paid_plan_clinics === 0 ? (
              <p style={{ color: '#888', fontSize: '13px', margin: 0 }}>
                All {data.total_clinics} clinic{data.total_clinics === 1 ? '' : 's'} are on the Free plan — no subscription revenue yet.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {planEntries.map(([plan, count]) => (
                  <div key={plan} style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{ width: '90px', color: '#888', fontSize: '12px' }}>{plan}</span>
                    <div style={{ flex: 1, height: '18px', backgroundColor: '#0F0F0F', borderRadius: '4px', overflow: 'hidden' }}>
                      <div style={{ width: `${(count / maxPlanCount) * 100}%`, height: '100%', backgroundColor: plan.toLowerCase() === 'free' ? '#555' : '#3ECF8E' }} />
                    </div>
                    <span style={{ width: '40px', textAlign: 'right', color: '#fff', fontSize: '12px', fontFamily: 'monospace' }}>{count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Credit ledger — real transactions (replaces the fabricated invoices). */}
          <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', overflow: 'hidden' }}>
            <div style={{ padding: '20px 24px', borderBottom: '1px solid #2E2E2E', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ fontSize: '13px', fontWeight: 700, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em', margin: 0 }}>Credit Transactions</h2>
            </div>
            {data.recent_transactions.length === 0 ? (
              <p style={{ padding: '20px 24px', color: '#888', fontSize: '13px', margin: 0 }}>No credit transactions yet.</p>
            ) : (
              <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
                <thead style={{ backgroundColor: '#0F0F0F' }}>
                  <tr>
                    {['Clinic', 'Type', 'Amount', 'Balance After', 'Date'].map(h => (
                      <th key={h} style={{ padding: '12px 24px', textAlign: 'left', color: '#888', fontWeight: 600, fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.recent_transactions.map(txn => (
                    <tr key={txn.id} style={{ borderTop: '1px solid #2E2E2E' }}>
                      <td style={{ padding: '14px 24px', color: '#fff', fontWeight: 600 }}>{txn.clinic_name}</td>
                      <td style={{ padding: '14px 24px', color: '#888' }}>{txn.type}</td>
                      <td style={{ padding: '14px 24px', color: txn.amount < 0 ? '#fca5a5' : '#3ECF8E', fontFamily: 'monospace' }}>{inr(txn.amount)}</td>
                      <td style={{ padding: '14px 24px', color: '#fff', fontFamily: 'monospace' }}>{txn.balance_after !== null ? inr(txn.balance_after) : '—'}</td>
                      <td style={{ padding: '14px 24px', color: '#888' }}>{fmtDate(txn.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
