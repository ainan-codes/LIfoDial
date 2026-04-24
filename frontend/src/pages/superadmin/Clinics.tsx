import React, { useState, useMemo, useEffect } from 'react';
import { useSAStore, Clinic, PlanTier } from '../../store/saStore';
import { PlanBadge, StatusBadge, Modal, SpinBtn, EmptyState } from '../../components/superadmin/SAShared';
import { Search, Power, Zap, X, ChevronRight, Building2, Plus, Copy, Check, Mail, Trash2 } from 'lucide-react';
import { API_URL } from '../../api/client';

const PLANS: PlanTier[] = ['Free', 'Pro', 'Enterprise'];

// ── Add Clinic Form ──────────────────────────────────────────────────────────
function AddClinicModal({ onClose }: { onClose: () => void }) {
  const { addClinic } = useSAStore();
  const [form, setForm] = useState({
    clinic_name: '', location: '', language: 'en-IN', admin_name: '', admin_email: '',
  });
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<any>(null);
  const [copied, setCopied] = useState(false);

  const validate = () => {
    const e: Record<string, string> = {};
    if (!form.clinic_name.trim()) e.clinic_name = 'Clinic name is required';
    if (!form.location.trim()) e.location = 'Location is required';
    if (!form.admin_email.includes('@')) e.admin_email = 'Valid email required';
    if (!form.admin_name.trim()) e.admin_name = 'Admin name is required';
    return e;
  };

  const handleSubmit = async () => {
    const e = validate();
    if (Object.keys(e).length) { setErrors(e); return; }
    setLoading(true);
    try {
      const resp = await fetch(`${API_URL}/admin/clinics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form)
      });
      if (!resp.ok) throw new Error('Failed to create clinic');
      const data = await resp.json();
      setResult(data);
      
      // Update local store to show the new clinic in the list
      addClinic({
        id: data.tenant_id,
        name: form.clinic_name,
        location: form.location,
        plan: 'Pro', // Default for now
        joined: 'Just now',
        status: 'Active',
        calls_month: 0,
        bookings: 0,
        res_rate: '0%',
        avg_latency: '-',
        model_id: 'm1',
        admin_email: form.admin_email
      });
    } catch (err: any) {
      alert(err.message);
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = () => {
    const text = `Email: ${result.login_credentials.email}\nPassword: ${result.login_credentials.password}`;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const inp = (field: string, placeholder: string, type = 'text') => (
    <div>
      <label style={{ display: 'block', fontSize: '11px', fontWeight: 700, color: '#888', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
        {placeholder}
      </label>
      <input
        type={type}
        value={(form as any)[field]}
        onChange={e => setForm(f => ({ ...f, [field]: e.target.value }))}
        placeholder={placeholder}
        style={{
          width: '100%', backgroundColor: '#0F0F0F', border: `1px solid ${errors[field] ? '#ef4444' : '#2E2E2E'}`,
          borderRadius: '8px', padding: '10px 12px', color: '#fff', fontSize: '14px',
          outline: 'none', boxSizing: 'border-box',
        }}
      />
      {errors[field] && <p style={{ color: '#ef4444', fontSize: '11px', marginTop: '4px' }}>{errors[field]}</p>}
    </div>
  );

  if (result) {
    return (
      <Modal title="Clinic Created Successfully" onClose={onClose}>
        <div style={{ textAlign: 'center', padding: '10px 0' }}>
          <div style={{ width: '50px', height: '50px', backgroundColor: '#3ECF8E20', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
            <Check color="#3ECF8E" size={30} />
          </div>
          <h3 style={{ color: '#fff', margin: '0 0 8px' }}>{form.clinic_name}</h3>
          <p style={{ color: '#3ECF8E', fontSize: '14px', fontWeight: 600, margin: '0 0 24px' }}>{result.ai_number}</p>
          
          <div style={{ backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '20px', textAlign: 'left', marginBottom: '24px' }}>
            <p style={{ color: '#888', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', marginBottom: '12px' }}>Login Credentials</p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <div>
                <p style={{ color: '#555', fontSize: '11px', margin: '0 0 2px' }}>Email</p>
                <p style={{ color: '#fff', fontSize: '14px', margin: 0, fontWeight: 500 }}>{result.login_credentials.email}</p>
              </div>
              <div>
                <p style={{ color: '#555', fontSize: '11px', margin: '0 0 2px' }}>Password</p>
                <p style={{ color: '#3ECF8E', fontSize: '14px', margin: 0, fontWeight: 700, letterSpacing: '0.05em' }}>{result.login_credentials.password}</p>
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '10px' }}>
            <button 
              onClick={copyToClipboard}
              style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '12px', backgroundColor: '#2E2E2E', border: 'none', borderRadius: '8px', color: '#fff', fontWeight: 600, cursor: 'pointer' }}
            >
              {copied ? <Check size={16} /> : <Copy size={16} />} {copied ? 'Copied!' : 'Copy Info'}
            </button>
            <button style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '12px', backgroundColor: '#3ECF8E', border: 'none', borderRadius: '8px', color: '#000', fontWeight: 700, cursor: 'pointer' }}>
              <Mail size={16} /> Send Email
            </button>
          </div>
          <p style={{ color: '#ef4444', fontSize: '11px', marginTop: '16px', fontWeight: 600 }}>⚠️ Password shown only once. Save it now.</p>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Add New Clinic" onClose={onClose}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {inp('clinic_name', 'Clinic Name')}
        {inp('admin_name', 'Admin Name')}
        {inp('admin_email', 'Admin Email', 'email')}
        {inp('location', 'Location')}
        <div>
          <label style={{ display: 'block', fontSize: '11px', fontWeight: 700, color: '#888', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
            Primary Language
          </label>
          <select
            value={form.language}
            onChange={e => setForm(f => ({ ...f, language: e.target.value }))}
            style={{ width: '100%', backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '10px 12px', color: '#fff', fontSize: '14px', outline: 'none' }}
          >
            <option value="en-IN">English (India)</option>
            <option value="hi-IN">Hindi</option>
            <option value="ar-SA">Arabic</option>
          </select>
        </div>
        <div style={{ display: 'flex', gap: '12px', marginTop: '8px' }}>
          <button onClick={onClose} style={{ flex: 1, padding: '12px', backgroundColor: '#2E2E2E', color: '#888', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}>
            Cancel
          </button>
          <SpinBtn onClick={handleSubmit} loading={loading} style={{ flex: 2 }}>
            Create Clinic & Generate Credentials
          </SpinBtn>
        </div>
      </div>
    </Modal>
  );
}

// ── Clinic Drawer ────────────────────────────────────────────────────────────
function ClinicDrawer({ clinic, onClose, onDeleted }: { clinic: Clinic; onClose: () => void; onDeleted: (id: string) => void }) {
  const { toggleSuspend, models, updateClinicModel } = useSAStore();
  const [suspending, setSuspending] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [selectedModel, setSelectedModel] = useState(clinic.model_id);
  const [reason, setReason] = useState('');
  const [assigning, setAssigning] = useState(false);

  const currentModel = models.find(m => m.id === clinic.model_id);
  
  const handleSuspend = async () => {
    setSuspending(true);
    try {
      const newStatus = clinic.status === 'Suspended' ? true : false;
      await fetch(`${API_URL}/admin/clinics/${clinic.id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: newStatus })
      });
      toggleSuspend(clinic.id);
      onClose();
    } catch (e) {
      alert("Failed to update status");
    } finally {
      setSuspending(false);
    }
  };

  // ... (keeping rest of drawer logic similar but using store)
  const handleAssign = () => {
    if (!reason.trim()) return;
    setAssigning(true);
    setTimeout(() => {
      updateClinicModel(clinic.id, selectedModel, reason);
      setAssigning(false);
      setAssignOpen(false);
    }, 700);
  };

  const eligibleModels = models.filter(m => {
    if (clinic.plan === 'Free') return m.tier === 'basic';
    if (clinic.plan === 'Pro') return m.tier === 'basic' || m.tier === 'pro';
    return true; 
  });

  return (
    <div style={{ width: '360px', backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
      {/* Header */}
      <div style={{ padding: '24px', borderBottom: '1px solid #2E2E2E', position: 'relative' }}>
        <button onClick={onClose} style={{ position: 'absolute', top: '16px', right: '16px', background: 'none', border: 'none', color: '#888', cursor: 'pointer' }}>
          <X size={18} />
        </button>
        <div style={{ width: '44px', height: '44px', backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', borderRadius: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '16px' }}>
          <span style={{ color: '#3ECF8E', fontWeight: 800, fontSize: '18px' }}>{clinic.name[0]}</span>
        </div>
        <h2 style={{ fontSize: '18px', fontWeight: 700, color: '#fff', margin: '0 0 4px' }}>{clinic.name}</h2>
        <p style={{ color: '#888', fontSize: '13px', margin: 0 }}>{clinic.location} · Joined {clinic.joined}</p>
        <div style={{ marginTop: '12px', display: 'flex', gap: '10px', alignItems: 'center' }}>
          <PlanBadge plan={clinic.plan} />
          <StatusBadge status={clinic.status} />
        </div>
      </div>

      <div style={{ padding: '20px 24px', borderBottom: '1px solid #2E2E2E', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        {[
          { label: 'Calls (30d)', value: clinic.calls_month.toLocaleString() },
          { label: 'Bookings', value: clinic.bookings.toLocaleString() },
          { label: 'Resolution', value: clinic.res_rate },
          { label: 'Avg Latency', value: clinic.avg_latency },
        ].map(s => (
          <div key={s.label} style={{ backgroundColor: '#0F0F0F', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '12px 14px' }}>
            <p style={{ color: '#888', fontSize: '11px', marginBottom: '4px', fontWeight: 600, textTransform: 'uppercase' }}>{s.label}</p>
            <p style={{ color: '#fff', fontFamily: 'monospace', fontSize: '16px', fontWeight: 700, margin: 0 }}>{s.value}</p>
          </div>
        ))}
      </div>

      <div style={{ padding: '20px 24px', borderBottom: '1px solid #2E2E2E' }}>
        <p style={{ color: '#888', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', marginBottom: '8px' }}>AI Model</p>
        <div style={{ backgroundColor: '#0F0F0F', border: '1px solid #3ECF8E40', borderRadius: '8px', padding: '12px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: '#3ECF8E', fontFamily: 'monospace', fontSize: '13px' }}>{currentModel?.name ?? 'None'}</span>
          <button onClick={() => setAssignOpen(v => !v)} style={{ fontSize: '11px', backgroundColor: '#3ECF8E20', color: '#3ECF8E', border: 'none', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer', fontWeight: 700 }}>
            Change
          </button>
        </div>
      </div>

      <div style={{ padding: '20px 24px' }}>
        <p style={{ color: '#888', fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', marginBottom: '12px' }}>Quick Actions</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <button 
            onClick={() => window.location.href = `/superadmin/agents/${clinic.id}`} 
            style={{ width: '100%', padding: '10px', backgroundColor: '#3ECF8E', color: '#000', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}
          >
            <Zap size={14} /> Configure AI Receptionist
          </button>
          <SpinBtn onClick={handleSuspend} loading={suspending} variant="danger" style={{ width: '100%', padding: '9px' }}>
            <Power size={14} /> {clinic.status === 'Suspended' ? 'Activate Clinic' : 'Suspend Clinic'}
          </SpinBtn>
          <button
            disabled={deleting}
            onClick={async () => {
              if (!window.confirm(`Permanently delete "${clinic.name}" and all its agents? This cannot be undone.`)) return;
              setDeleting(true);
              try {
                const res = await fetch(`${API_URL}/admin/clinics/${clinic.id}`, { method: 'DELETE' });
                if (res.ok || res.status === 204) {
                  onDeleted(clinic.id);
                  onClose();
                } else {
                  alert('Failed to delete clinic');
                }
              } catch { alert('Network error'); }
              finally { setDeleting(false); }
            }}
            style={{
              width: '100%', padding: '9px', backgroundColor: 'rgba(239,68,68,0.1)',
              color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)',
              borderRadius: '8px', cursor: deleting ? 'not-allowed' : 'pointer',
              fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
              opacity: deleting ? 0.6 : 1, transition: 'all 0.15s',
            }}
          >
            <Trash2 size={14} /> {deleting ? 'Deleting…' : 'Delete Clinic'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SAClinics() {
  const { clinics, setClinics } = useSAStore();
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Clinic | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [loading, setLoading] = useState(true);

  // Fetch initial clinics from backend
  useEffect(() => {
    fetch(`${API_URL}/admin/clinics`)
      .then(r => r.json())
      .then(data => {
        const mapped = data.map((c: any) => ({
          id: c.id,
          name: c.clinic_name,
          location: c.location || '—',
          plan: c.plan || 'Pro',
          joined: new Date(c.created_at).toLocaleDateString(),
          status: c.is_active ? 'Active' : 'Suspended',
          // Real stats: use 0/— until a stats table exists (no fake random data)
          calls_month: c.calls_month || 0,
          bookings: c.bookings || 0,
          res_rate: c.res_rate || '—',
          avg_latency: c.avg_latency || '—',
          admin_email: c.admin_email || '',
          model_id: 'm1',
        }));
        setClinics(mapped);
      })
      .catch(() => setClinics([]))
      .finally(() => setLoading(false));
  }, []);



  const filtered = useMemo(() =>
    clinics.filter(c =>
      (c.name.toLowerCase().includes(search.toLowerCase()) ||
       c.location.toLowerCase().includes(search.toLowerCase()))
    ), [clinics, search]);

  return (
    <div style={{ padding: '32px', display: 'flex', gap: '20px', height: '100%', overflow: 'hidden' }}>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '24px' }}>
          <div>
            <h1 style={{ fontSize: '24px', fontWeight: 800, color: '#fff', margin: 0 }}>All Clinics</h1>
            <p style={{ color: '#888', fontSize: '13px', marginTop: '4px' }}>{clinics.length} clinics registered in system</p>
          </div>
          <button onClick={() => setShowAdd(true)} style={{ display: 'flex', alignItems: 'center', gap: '6px', backgroundColor: '#3ECF8E', color: '#000', border: 'none', borderRadius: '8px', padding: '9px 16px', fontSize: '13px', fontWeight: 700, cursor: 'pointer' }}>
            <Plus size={15} /> Add Clinic
          </button>
        </div>

        <div style={{ position: 'relative', marginBottom: '20px' }}>
          <Search size={14} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: '#888' }} />
          <input
            type="text" placeholder="Search clinics..."
            value={search} onChange={e => setSearch(e.target.value)}
            style={{ width: '100%', backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '8px', padding: '9px 12px 9px 36px', color: '#fff', fontSize: '14px', outline: 'none' }}
          />
        </div>

        <div style={{ backgroundColor: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '12px', overflow: 'hidden', flex: 1, overflowY: 'auto' }}>
          {loading ? (
             <div style={{ padding: '40px', textAlign: 'center', color: '#888' }}>Loading clinics...</div>
          ) : (
            <table style={{ width: '100%', fontSize: '13px', borderCollapse: 'collapse' }}>
              <thead style={{ backgroundColor: '#0F0F0F', position: 'sticky', top: 0 }}>
                <tr>
                  {['Clinic', 'Location', 'Plan', 'Calls', 'Status', ''].map(h => (
                    <th key={h} style={{ padding: '12px 20px', textAlign: 'left', color: '#888', fontSize: '11px', textTransform: 'uppercase' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr><td colSpan={6}><EmptyState icon={Building2} message="No clinics found" /></td></tr>
                ) : (
                  filtered.map(c => (
                    <tr key={c.id} onClick={() => setSelected(c)} style={{ borderTop: '1px solid #2E2E2E', cursor: 'pointer', backgroundColor: selected?.id === c.id ? '#2E2E2E' : 'transparent' }}>
                      <td style={{ padding: '14px 20px', color: '#fff', fontWeight: 600 }}>{c.name}</td>
                      <td style={{ padding: '14px 20px', color: '#888' }}>{c.location}</td>
                      <td style={{ padding: '14px 20px' }}><PlanBadge plan={c.plan} /></td>
                      <td style={{ padding: '14px 20px', color: '#fff', fontFamily: 'monospace' }}>{c.calls_month}</td>
                      <td style={{ padding: '14px 20px' }}><StatusBadge status={c.status} /></td>
                      <td style={{ padding: '14px 20px', textAlign: 'right' }}><ChevronRight size={15} color="#555" /></td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {selected && <ClinicDrawer clinic={selected} onClose={() => setSelected(null)} onDeleted={(id) => setClinics(prev => prev.filter(c => c.id !== id))} />}
      {showAdd && <AddClinicModal onClose={() => setShowAdd(false)} />}
    </div>
  );
}
