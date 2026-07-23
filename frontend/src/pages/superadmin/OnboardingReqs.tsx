import React, { useState, useEffect, useCallback } from 'react';
import { ClipboardList, CheckCircle, XCircle, RefreshCw, Clock, User, Mail, Phone, MapPin, MessageSquare, Building2 } from 'lucide-react';
import { PlanBadge, StatusBadge, Modal, SpinBtn, EmptyState } from '../../components/superadmin/SAShared';
import fetchWithAuth from '../../api/client';

// ── Types ────────────────────────────────────────────────────────────────────
interface OnboardingRequest {
  id: string;
  clinic_name: string;
  contact_name: string;
  email: string;
  phone: string;
  plan: 'Free' | 'Pro' | 'Enterprise';
  location?: string;
  message?: string;
  status: 'Pending' | 'Approved' | 'Rejected';
  note?: string;
  created_at: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────
function timeAgo(iso: string): string {
  try {
    const diffMs = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diffMs / 60000);
    if (m < 2) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  } catch { return '—'; }
}

// ── Approve Modal ─────────────────────────────────────────────────────────────
function ApproveModal({ req, onClose, onDone }: { req: OnboardingRequest; onClose: () => void; onDone: () => void }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const handleApprove = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWithAuth(`/admin/onboarding-requests/${req.id}/approve`, {
        method: 'PATCH',
      });
      // Show the REAL credentials the backend generated + persisted. NEVER
      // fabricate an admin@<slug>.lifodial.com login here: that fake address was
      // never stored, so it always failed at sign-in — this was the source of the
      // "credentials shown but login invalid" bug (and the double credentials, one
      // real + one fabricated). A failed approval is now an error, not fake success.
      setResult(data);
      onDone();
    } catch (e: any) {
      setError(e?.message || 'Approval failed — the clinic was NOT created. Please retry.');
    } finally {
      setLoading(false);
    }
  };

  if (result) {
    return (
      <Modal title="✅ Clinic Created" onClose={onClose} width={480}>
        <div style={{ backgroundColor: '#0d2e1e', border: '1px solid #3ECF8E40', borderRadius: '10px', padding: '20px', marginBottom: '20px' }}>
          <p style={{ color: '#3ECF8E', fontWeight: 700, marginBottom: '12px', fontSize: '14px' }}>Clinic Onboarded Successfully</p>
          {result.credentials && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '13px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#888' }}>Login Email:</span>
                <code style={{ color: '#fff', backgroundColor: '#1A1A1A', padding: '2px 8px', borderRadius: '4px' }}>{result.credentials.email}</code>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#888' }}>Password:</span>
                <code style={{ color: '#3ECF8E', backgroundColor: '#1A1A1A', padding: '2px 8px', borderRadius: '4px' }}>{result.credentials.password}</code>
              </div>
              {result.credentials.ai_number && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: '#888' }}>AI Number:</span>
                  <code style={{ color: '#60a5fa', backgroundColor: '#1A1A1A', padding: '2px 8px', borderRadius: '4px' }}>{result.credentials.ai_number}</code>
                </div>
              )}
            </div>
          )}
        </div>
        <p style={{ color: '#555', fontSize: '12px' }}>Share these credentials with the clinic admin securely. They can be changed from the clinic settings.</p>
        <button onClick={onClose} style={{ width: '100%', marginTop: '16px', padding: '10px', backgroundColor: '#2E2E2E', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}>
          Close
        </button>
      </Modal>
    );
  }

  return (
    <Modal title="Approve Onboarding Request" onClose={onClose} width={440}>
      <div style={{ marginBottom: '20px', backgroundColor: '#0F0F0F', borderRadius: '10px', padding: '16px', border: '1px solid #2E2E2E' }}>
        <p style={{ color: '#fff', fontWeight: 700, marginBottom: '4px' }}>{req.clinic_name}</p>
        <p style={{ color: '#888', fontSize: '12px', margin: 0 }}>{req.contact_name} · {req.email}</p>
        <div style={{ marginTop: '10px', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <PlanBadge plan={req.plan} />
          {req.location && <span style={{ color: '#888', fontSize: '12px' }}>{req.location}</span>}
        </div>
      </div>
      <p style={{ color: '#888', fontSize: '13px', marginBottom: '20px', lineHeight: 1.6 }}>
        Approving will create an active clinic account and auto-assign the <strong style={{ color: '#3ECF8E' }}>{req.plan}</strong> plan. Access credentials will be generated.
      </p>
      {error && (
        <p style={{ color: '#F87171', fontSize: '13px', marginBottom: '16px', lineHeight: 1.5 }}>{error}</p>
      )}
      <div style={{ display: 'flex', gap: '12px' }}>
        <button onClick={onClose} style={{ flex: 1, padding: '10px', backgroundColor: '#2E2E2E', color: '#888', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}>Cancel</button>
        <SpinBtn onClick={handleApprove} loading={loading} style={{ flex: 1 }}>
          <CheckCircle size={14} /> Approve & Onboard
        </SpinBtn>
      </div>
    </Modal>
  );
}

// ── Reject Modal ──────────────────────────────────────────────────────────────
function RejectModal({ req, onClose, onDone }: { req: OnboardingRequest; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false);

  const handleReject = async () => {
    if (!reason.trim()) return;
    setLoading(true);
    try {
      await fetchWithAuth(`/admin/onboarding-requests/${req.id}/reject`, {
        method: 'PATCH',
        body: JSON.stringify({ reason }),
      });
    } catch { /* optimistic */ }
    onDone();
    setLoading(false);
    onClose();
  };

  return (
    <Modal title="Reject Request" onClose={onClose} width={420}>
      <p style={{ color: '#888', fontSize: '13px', marginBottom: '16px' }}>
        Rejecting <strong style={{ color: '#fff' }}>{req.clinic_name}</strong>'s request.
      </p>
      <textarea
        value={reason}
        onChange={e => setReason(e.target.value)}
        placeholder="e.g. Incomplete details — please resubmit with full clinic documents"
        rows={3}
        style={{ width: '100%', backgroundColor: '#0F0F0F', border: `1px solid ${reason ? '#2E2E2E' : '#ef444430'}`, borderRadius: '8px', padding: '10px 12px', color: '#fff', fontSize: '13px', outline: 'none', resize: 'vertical', marginBottom: '16px', boxSizing: 'border-box' }}
      />
      <div style={{ display: 'flex', gap: '12px' }}>
        <button onClick={onClose} style={{ flex: 1, padding: '10px', backgroundColor: '#2E2E2E', color: '#888', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}>Cancel</button>
        <SpinBtn onClick={handleReject} loading={loading} disabled={!reason.trim()} variant="danger" style={{ flex: 1 }}>
          <XCircle size={14} /> Reject Request
        </SpinBtn>
      </div>
    </Modal>
  );
}

// ── Detail Panel ──────────────────────────────────────────────────────────────
function DetailPanel({ req, onApprove, onReject, onClose }: {
  req: OnboardingRequest;
  onApprove: () => void;
  onReject: () => void;
  onClose: () => void;
}) {
  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: '520px', zIndex: 100,
      backgroundColor: '#0F0F0F', borderLeft: '1px solid #2E2E2E',
      display: 'flex', flexDirection: 'column', overflowY: 'auto',
    }}>
      {/* Header */}
      <div style={{ padding: '24px', borderBottom: '1px solid #2E2E2E', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ color: '#fff', fontSize: '18px', fontWeight: 700, margin: '0 0 4px' }}>{req.clinic_name}</h2>
          <StatusBadge status={req.status} />
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', fontSize: '20px', lineHeight: 1 }}>✕</button>
      </div>

      {/* Content */}
      <div style={{ padding: '24px', flex: 1, display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div style={{ backgroundColor: '#1A1A1A', borderRadius: '10px', padding: '20px', border: '1px solid #2E2E2E' }}>
          <h3 style={{ color: '#888', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '16px' }}>Contact Details</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {[
              { icon: User, label: 'Contact', value: req.contact_name },
              { icon: Mail, label: 'Email', value: req.email },
              { icon: Phone, label: 'Phone', value: req.phone },
              { icon: MapPin, label: 'Location', value: req.location || '—' },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <Icon size={14} color="#555" style={{ flexShrink: 0 }} />
                <span style={{ color: '#555', fontSize: '12px', width: '60px', flexShrink: 0 }}>{label}</span>
                <span style={{ color: '#fff', fontSize: '13px' }}>{value}</span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ backgroundColor: '#1A1A1A', borderRadius: '10px', padding: '20px', border: '1px solid #2E2E2E' }}>
          <h3 style={{ color: '#888', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '12px' }}>Plan Requested</h3>
          <PlanBadge plan={req.plan} />
        </div>

        {req.message && (
          <div style={{ backgroundColor: '#1A1A1A', borderRadius: '10px', padding: '20px', border: '1px solid #2E2E2E' }}>
            <h3 style={{ color: '#888', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <MessageSquare size={12} /> Message
            </h3>
            <p style={{ color: '#ccc', fontSize: '13px', lineHeight: 1.6, margin: 0 }}>{req.message}</p>
          </div>
        )}

        {req.note && (
          <div style={{ backgroundColor: '#1a1a0e', borderRadius: '10px', padding: '16px', border: '1px solid #f59e0b30' }}>
            <p style={{ color: '#f59e0b', fontSize: '12px', fontWeight: 600, marginBottom: '4px' }}>Internal Note</p>
            <p style={{ color: '#ccc', fontSize: '13px', margin: 0 }}>{req.note}</p>
          </div>
        )}

        <p style={{ color: '#555', fontSize: '11px' }}>
          <Clock size={10} style={{ display: 'inline', marginRight: '4px' }} />
          Submitted {timeAgo(req.created_at)}
        </p>
      </div>

      {/* Actions */}
      {req.status === 'Pending' && (
        <div style={{ padding: '20px 24px', borderTop: '1px solid #2E2E2E', display: 'flex', gap: '12px' }}>
          <SpinBtn onClick={onReject} variant="danger" style={{ flex: 1 }}>
            <XCircle size={14} /> Reject
          </SpinBtn>
          <SpinBtn onClick={onApprove} style={{ flex: 1 }}>
            <CheckCircle size={14} /> Approve & Onboard
          </SpinBtn>
        </div>
      )}
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────
function SkeletonCard() {
  const shimmer = { background: 'linear-gradient(90deg, #1a1a1a 25%, #242424 50%, #1a1a1a 75%)', backgroundSize: '200% 100%', animation: 'shimmer 1.4s infinite', borderRadius: '4px', height: '12px' } as React.CSSProperties;
  return (
    <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '20px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <div style={{ ...shimmer, width: '60%' }} />
      <div style={{ ...shimmer, width: '40%' }} />
      <div style={{ ...shimmer, width: '30%' }} />
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function OnboardingReqs() {
  const [requests, setRequests] = useState<OnboardingRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'Pending' | 'Approved' | 'Rejected'>('Pending');
  const [selectedReq, setSelectedReq] = useState<OnboardingRequest | null>(null);
  const [approveTarget, setApproveTarget] = useState<OnboardingRequest | null>(null);
  const [rejectTarget, setRejectTarget] = useState<OnboardingRequest | null>(null);

  const fetchRequests = useCallback(async () => {
    try {
      const data = await fetchWithAuth('/admin/onboarding-requests');
      setRequests(data);
    } catch {
      // Fallback to demo data if backend not ready
      setRequests(MOCK_REQUESTS);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRequests();
    // Poll every 30 seconds for new requests
    const interval = setInterval(fetchRequests, 30000);
    return () => clearInterval(interval);
  }, [fetchRequests]);

  const filtered = requests.filter(r => r.status === tab);
  const pendingCount = requests.filter(r => r.status === 'Pending').length;

  const handleActionDone = () => {
    setApproveTarget(null);
    setRejectTarget(null);
    setSelectedReq(null);
    fetchRequests();
  };

  return (
    <>
      <div style={{ padding: '32px', height: '100%', overflowY: 'auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '28px' }}>
          <div>
            <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0, letterSpacing: '-0.02em' }}>
              Onboarding Requests
            </h1>
            <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>
              Clinics submitted via Contact Sales · auto-refreshes every 30s
            </p>
          </div>
          <button
            onClick={fetchRequests}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '8px 14px', color: '#888', fontSize: '13px', cursor: 'pointer' }}
          >
            <RefreshCw size={13} /> Refresh
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', gap: '4px', marginBottom: '24px', backgroundColor: '#1A1A1A', borderRadius: '10px', padding: '4px', width: 'fit-content', border: '1px solid #2E2E2E' }}>
          {(['Pending', 'Approved', 'Rejected'] as const).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{ padding: '7px 20px', borderRadius: '8px', fontSize: '13px', fontWeight: 600, border: 'none', cursor: 'pointer', transition: 'all 0.15s', backgroundColor: tab === t ? '#2E2E2E' : 'transparent', color: tab === t ? '#fff' : '#888' }}
            >
              {t} {t === 'Pending' && pendingCount > 0 && (
                <span style={{ backgroundColor: '#3ECF8E', color: '#000', borderRadius: '10px', fontSize: '10px', fontWeight: 800, padding: '1px 6px', marginLeft: '4px' }}>
                  {pendingCount}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Content */}
        {loading ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '16px' }}>
            {Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState icon={ClipboardList} message={`No ${tab.toLowerCase()} requests`} sub="Requests from the landing page contact form appear here." />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: '16px' }}>
            {filtered.map(req => (
              <div
                key={req.id}
                onClick={() => setSelectedReq(req)}
                style={{ backgroundColor: '#1A1A1A', border: `1px solid ${selectedReq?.id === req.id ? '#3ECF8E50' : '#2E2E2E'}`, borderRadius: '12px', padding: '20px', cursor: 'pointer', transition: 'all 0.15s' }}
                onMouseEnter={e => (e.currentTarget.style.borderColor = '#3E3E3E')}
                onMouseLeave={e => (e.currentTarget.style.borderColor = selectedReq?.id === req.id ? '#3ECF8E50' : '#2E2E2E')}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <Building2 size={14} color="#888" />
                      <span style={{ color: '#fff', fontWeight: 700, fontSize: '15px' }}>{req.clinic_name}</span>
                    </div>
                    <p style={{ color: '#888', fontSize: '12px', margin: '4px 0 0 22px' }}>{req.contact_name} · {req.email}</p>
                  </div>
                  <PlanBadge plan={req.plan} />
                </div>

                <div style={{ display: 'flex', gap: '16px', fontSize: '12px', color: '#555', marginBottom: '16px', paddingLeft: '22px' }}>
                  {req.location && <span><MapPin size={11} style={{ display: 'inline', marginRight: '4px' }} />{req.location}</span>}
                  <span><Clock size={11} style={{ display: 'inline', marginRight: '4px' }} />{timeAgo(req.created_at)}</span>
                </div>

                {req.status === 'Pending' && (
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <SpinBtn
                      onClick={() => { setApproveTarget(req); }}
                      style={{ flex: 1, padding: '7px', fontSize: '12px' }}
                    >
                      <CheckCircle size={12} /> Approve
                    </SpinBtn>
                    <SpinBtn
                      onClick={() => { setRejectTarget(req); }}
                      variant="danger"
                      style={{ flex: 1, padding: '7px', fontSize: '12px' }}
                    >
                      <XCircle size={12} /> Reject
                    </SpinBtn>
                  </div>
                )}
                {req.status !== 'Pending' && (
                  <StatusBadge status={req.status} />
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Slide-in detail panel */}
      {selectedReq && !approveTarget && !rejectTarget && (
        <>
          <div onClick={() => setSelectedReq(null)} style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.4)', zIndex: 99 }} />
          <DetailPanel
            req={selectedReq}
            onApprove={() => setApproveTarget(selectedReq)}
            onReject={() => setRejectTarget(selectedReq)}
            onClose={() => setSelectedReq(null)}
          />
        </>
      )}

      {approveTarget && <ApproveModal req={approveTarget} onClose={() => setApproveTarget(null)} onDone={handleActionDone} />}
      {rejectTarget && <RejectModal req={rejectTarget} onClose={() => setRejectTarget(null)} onDone={handleActionDone} />}

      <style>{`
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
      `}</style>
    </>
  );
}

// ── Mock Data ─────────────────────────────────────────────────────────────────
const MOCK_REQUESTS: OnboardingRequest[] = [
  { id: 'req1', clinic_name: 'Sunrise Medical Centre', contact_name: 'Rahul Verma', email: 'rahul@sunrisemed.in', phone: '+91 80000 11111', plan: 'Enterprise', location: 'Delhi', message: 'We run 3 speciality clinics and need a multi-location agent.', status: 'Pending', created_at: new Date(Date.now() - 7200000).toISOString() },
  { id: 'req2', clinic_name: 'CityDent Clinic', contact_name: 'Priya Nair', email: 'priya@citydent.com', phone: '+91 94400 55555', plan: 'Pro', location: 'Bangalore', message: 'Dental clinic, Malayalam + English support needed.', status: 'Pending', created_at: new Date(Date.now() - 18000000).toISOString() },
  { id: 'req3', clinic_name: 'Lotus Eye Care', contact_name: 'Dr. Kapoor', email: 'kapoor@lotuseyecare.in', phone: '+91 98765 12345', plan: 'Pro', location: 'Mumbai', status: 'Approved', note: 'Approved. Tenant created.', created_at: new Date(Date.now() - 86400000).toISOString() },
];
