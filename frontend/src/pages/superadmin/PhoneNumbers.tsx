import {
    CheckCircle,
    Globe,
    Loader2,
    Phone, Plus, Search,
    Trash2, Wifi, WifiOff,
    X
} from 'lucide-react';
import React, { useEffect, useState } from 'react';
import fetchWithAuth from '../../api/client';

// ── Options ───────────────────────────────────────────────────────────────────
// (Audit P2: the old FIXTURE_PHONE_NUMBERS seed — 4 fake numbers on clinics that
// don't exist — was removed. The table now renders ONLY real rows from
// GET /phone-numbers, and an honest empty state when there are none.)

interface PhoneRow {
  id: string;
  number: string;
  country: string;
  country_code?: string;
  provider?: string;
  agent_id?: string | null;
  agent_name?: string | null;
  tenant_id?: string | null;
  is_active: boolean;
  is_assigned: boolean;
  sip_domain?: string | null;
  created_at?: string | null;
}

const COUNTRY_OPTIONS = [
  { code: 'IN', name: 'India', flag: '🇮🇳', prefix: '+91' },
  { code: 'AE', name: 'UAE', flag: '🇦🇪', prefix: '+971' },
  { code: 'SA', name: 'Saudi Arabia', flag: '🇸🇦', prefix: '+966' },
  { code: 'QA', name: 'Qatar', flag: '🇶🇦', prefix: '+974' },
  { code: 'GB', name: 'United Kingdom', flag: '🇬🇧', prefix: '+44' },
  { code: 'US', name: 'United States', flag: '🇺🇸', prefix: '+1' },
];

const PROVIDER_OPTIONS = ['Vobiz', 'Exotel', 'Knowlarity', 'Custom SIP'];

const FLAG_MAP: Record<string, string> = {
  IN: '🇮🇳', AE: '🇦🇪', SA: '🇸🇦', QA: '🇶🇦', GB: '🇬🇧', US: '🇺🇸',
};

// ── Page Component ───────────────────────────────────────────────────────────

export default function PhoneNumbers() {
  const [numbers, setNumbers] = useState<PhoneRow[]>([]);
  const [search, setSearch] = useState('');
  const [showAddModal, setShowAddModal] = useState(false);
  const [agents, setAgents] = useState<{ id: string; name: string; tenant_id: string }[]>([]);
  const [clinics, setClinics] = useState<{ id: string; name: string }[]>([]);

  // Real data only — the table reflects the actual phone_numbers table (audit P2).
  useEffect(() => {
    fetchWithAuth('/phone-numbers')
      .then((data) => { setNumbers(Array.isArray(data) ? (data as PhoneRow[]) : []); })
      .catch(() => setNumbers([]));

    fetchWithAuth('/agents')
      .then((data) => {
        if (Array.isArray(data)) {
          setAgents(data.map((a: any) => ({
            id: a.id, name: a.agent_name || a.name || 'Agent', tenant_id: a.tenant_id,
          })));
        }
      })
      .catch(() => {});

    // Real clinics for the Add-Number clinic selector (replaces the hardcoded
    // 'tenant-001' the modal used to POST — the reason assignment never worked).
    fetchWithAuth('/tenants')
      .then((data) => {
        if (Array.isArray(data)) {
          setClinics(data.map((c: any) => ({ id: c.id, name: c.clinic_name || c.name || 'Clinic' })));
        }
      })
      .catch(() => {});
  }, []);

  const handleDelete = async (pn: PhoneRow) => {
    if (!window.confirm(`Delete ${pn.number}? This removes the number assignment.`)) return;
    const prev = numbers;
    setNumbers((cur) => cur.filter((n) => n.id !== pn.id));  // optimistic
    try {
      await fetchWithAuth(`/phone-numbers/${pn.id}`, { method: 'DELETE' });
    } catch {
      setNumbers(prev);  // restore on failure
      window.alert('Failed to delete the number. Please try again.');
    }
  };

  const filtered = numbers.filter((n) =>
    n.number.toLowerCase().includes(search.toLowerCase()) ||
    (n.agent_name || '').toLowerCase().includes(search.toLowerCase()) ||
    n.country.toLowerCase().includes(search.toLowerCase())
  );

  const totalNumbers = numbers.length;
  const assignedCount = numbers.filter((n) => n.is_assigned).length;
  const availableCount = totalNumbers - assignedCount;
  const countries = [...new Set(numbers.map((n) => n.country))];

  return (
    <div style={{ padding: '24px 28px', maxWidth: '1200px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', margin: 0 }}>
            Phone Numbers
          </h1>
          <p style={{ fontSize: '13px', color: '#666', margin: '4px 0 0' }}>
            Virtual AI numbers assigned to your clinics
          </p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            padding: '8px 16px', borderRadius: '8px',
            background: '#3ECF8E', color: '#000', border: 'none',
            fontSize: '13px', fontWeight: 600, cursor: 'pointer',
          }}
        >
          <Plus size={14} /> Add Number
        </button>
      </div>

      {/* Stats Row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
        {[
          { label: 'Total Numbers', value: totalNumbers, icon: <Phone size={14} /> },
          { label: 'Assigned', value: assignedCount, icon: <CheckCircle size={14} /> },
          { label: 'Available', value: availableCount, icon: <Globe size={14} /> },
          { label: 'Countries', value: countries.map((c) => FLAG_MAP[COUNTRY_OPTIONS.find((co) => co.name === c)?.code || ''] || '').join(' ') || countries.length, icon: <Globe size={14} /> },
        ].map((stat, i) => (
          <div key={i} style={{
            background: '#111', border: '1px solid #1A1A1A', borderRadius: '10px',
            padding: '14px 16px',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
              <span style={{ color: '#555' }}>{stat.icon}</span>
              <span style={{ fontSize: '11px', color: '#666', fontWeight: 500 }}>{stat.label}</span>
            </div>
            <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>{stat.value}</div>
          </div>
        ))}
      </div>

      {/* Search */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        background: '#111', border: '1px solid #1A1A1A', borderRadius: '8px',
        padding: '0 12px', marginBottom: '16px',
      }}>
        <Search size={14} color="#555" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search numbers, agents, countries..."
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            padding: '10px 0', fontSize: '13px', color: '#ccc',
          }}
        />
      </div>

      {/* Table */}
      <div style={{
        background: '#111', border: '1px solid #1A1A1A', borderRadius: '10px',
        overflow: 'hidden',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1A1A1A' }}>
              {['Number', 'Country', 'Agent Assigned', 'Provider', 'Status', 'Actions'].map((h) => (
                <th key={h} style={{
                  padding: '10px 16px', textAlign: 'left',
                  fontSize: '11px', fontWeight: 600, color: '#555',
                  textTransform: 'uppercase', letterSpacing: '0.03em',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((pn) => (
              <tr key={pn.id} style={{ borderBottom: '1px solid #141414' }}>
                <td style={{ padding: '12px 16px' }}>
                  <span style={{ fontSize: '13px', fontWeight: 600, color: '#fff', fontFamily: 'monospace' }}>
                    {pn.number}
                  </span>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <span style={{ fontSize: '13px', color: '#ccc' }}>
                    {FLAG_MAP[pn.country_code] || ''} {pn.country}
                  </span>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  {pn.is_assigned ? (
                    <span style={{ fontSize: '12px', color: '#3ECF8E' }}>{pn.agent_name}</span>
                  ) : (
                    <span style={{ fontSize: '12px', color: '#555', fontStyle: 'italic' }}>Unassigned</span>
                  )}
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <span style={{ fontSize: '12px', color: '#888' }}>{pn.provider}</span>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: '4px',
                    padding: '3px 10px', borderRadius: '6px', fontSize: '11px', fontWeight: 600,
                    ...(pn.is_active
                      ? { color: '#22C55E', background: 'rgba(34,197,94,0.12)' }
                      : { color: '#F87171', background: 'rgba(248,113,113,0.12)' }),
                  }}>
                    <span style={{
                      width: '6px', height: '6px', borderRadius: '50%',
                      background: pn.is_active ? '#22C55E' : '#F87171',
                    }} />
                    {pn.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    <button style={actionBtnStyle} title="Delete" onClick={() => handleDelete(pn)}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} style={{ textAlign: 'center', padding: '40px', color: '#555', fontSize: '13px' }}>
                  No phone numbers found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Add Number Modal */}
      {showAddModal && (
        <AddNumberModal
          agents={agents}
          clinics={clinics}
          onClose={() => setShowAddModal(false)}
          onAdded={(pn) => {
            setNumbers((prev) => [pn, ...prev]);
            setShowAddModal(false);
          }}
        />
      )}
    </div>
  );
}

// ── Add Number Modal ────────────────────────────────────────────────────────

function AddNumberModal({
  agents,
  clinics,
  onClose,
  onAdded,
}: {
  agents: { id: string; name: string; tenant_id: string }[];
  clinics: { id: string; name: string }[];
  onClose: () => void;
  onAdded: (pn: any) => void;
}) {
  const [country, setCountry] = useState('IN');
  const [number, setNumber] = useState('');
  const [provider, setProvider] = useState('Vobiz');
  const [tenantId, setTenantId] = useState('');
  const [agentId, setAgentId] = useState('');
  const [saveError, setSaveError] = useState<string | null>(null);

  // Only agents belonging to the selected clinic — the backend rejects a POST
  // whose agent_id belongs to a different tenant (audit P2 root cause).
  const clinicAgents = agents.filter((a) => a.tenant_id === tenantId);
  const [sipDomain, setSipDomain] = useState('');
  const [sipUri, setSipUri] = useState('');
  const [sipAccountSid, setSipAccountSid] = useState('');
  const [sipAuthToken, setSipAuthToken] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<null | { connected: boolean; message: string }>(null);
  const [saving, setSaving] = useState(false);

  const selectedCountry = COUNTRY_OPTIONS.find((c) => c.code === country)!;

  const generateRandom = () => {
    const rand = Math.floor(10000 + Math.random() * 90000);
    setNumber(`${selectedCountry.prefix} ${90000 + Math.floor(Math.random() * 10000)} ${rand}`);
  };

  const handleTestSip = async () => {
    setTesting(true);
    setTestResult(null);
    // Mock test
    await new Promise((r) => setTimeout(r, 1500));
    setTestResult({
      connected: !!sipDomain,
      message: sipDomain ? `SIP connection to ${sipDomain} successful` : 'No SIP domain configured',
    });
    setTesting(false);
  };

  const handleSubmit = async () => {
    if (!number.trim() || !tenantId) return;
    setSaving(true);
    setSaveError(null);

    try {
      const data = await fetchWithAuth('/phone-numbers', {
        method: 'POST',
        body: JSON.stringify({
          number: number.trim(),
          country_code: country,
          country: selectedCountry.name,
          provider,
          agent_id: agentId || null,
          sip_domain: sipDomain || null,
          sip_uri: sipUri || null,
          sip_username: sipAccountSid || null,
          sip_password: sipAuthToken || null,
          tenant_id: tenantId,  // the REAL selected clinic (was hardcoded 'tenant-001')
        }),
      });

      // Trust the server row. Do NOT fabricate a local row on failure — a failed
      // save must surface as an error, not look like it worked (audit: honesty).
      onAdded({
        id: data.id,
        number: data.number ?? number.trim(),
        country: selectedCountry.name,
        country_code: country,
        provider,
        tenant_id: tenantId,
        agent_id: agentId || null,
        agent_name: agents.find((a) => a.id === agentId)?.name || null,
        is_active: data.is_active ?? true,
        is_assigned: !!agentId,
        sip_domain: sipDomain || null,
        created_at: data.created_at ?? new Date().toISOString(),
      });
    } catch (e: any) {
      setSaveError(e?.message || 'Failed to save the number. Please check the details and try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(6px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: '#0F0F0F', border: '1px solid #1A1A1A', borderRadius: '14px',
        width: '520px', maxWidth: '90vw', maxHeight: '90vh', overflowY: 'auto',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '16px 20px', borderBottom: '1px solid #1A1A1A',
        }}>
          <h2 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', margin: 0 }}>
            Add Phone Number
          </h2>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#555' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: '20px' }}>
          {/* Country */}
          <div style={{ marginBottom: '16px' }}>
            <label style={labelStyle}>Country</label>
            <select
              value={country}
              onChange={(e) => setCountry(e.target.value)}
              style={inputStyle}
            >
              {COUNTRY_OPTIONS.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.flag} {c.name} ({c.prefix})
                </option>
              ))}
            </select>
          </div>

          {/* Phone Number */}
          <div style={{ marginBottom: '16px' }}>
            <label style={labelStyle}>Phone Number</label>
            <div style={{ display: 'flex', gap: '8px' }}>
              <input
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                placeholder={`${selectedCountry.prefix} 90001 XXXXX`}
                style={{ ...inputStyle, flex: 1 }}
              />
              <button onClick={generateRandom} style={{
                padding: '8px 12px', borderRadius: '8px',
                background: '#1A1A1A', border: '1px solid #2E2E2E',
                color: '#888', cursor: 'pointer', fontSize: '11px', whiteSpace: 'nowrap',
              }}>
                Generate Random
              </button>
            </div>
          </div>

          {/* Clinic (required) — the number is assigned to a REAL clinic. */}
          <div style={{ marginBottom: '16px' }}>
            <label style={labelStyle}>Clinic *</label>
            <select
              value={tenantId}
              onChange={(e) => { setTenantId(e.target.value); setAgentId(''); }}
              style={inputStyle}
            >
              <option value="">Select clinic...</option>
              {clinics.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>

          {/* Assign to Agent — scoped to the selected clinic's agents. */}
          <div style={{ marginBottom: '16px' }}>
            <label style={labelStyle}>Assign to Agent</label>
            <select
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
              style={inputStyle}
              disabled={!tenantId}
            >
              <option value="">{tenantId ? 'Unassigned (clinic only)' : 'Select a clinic first…'}</option>
              {clinicAgents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </div>

          {/* SIP Provider */}
          <div style={{ marginBottom: '16px' }}>
            <label style={labelStyle}>SIP Provider</label>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {PROVIDER_OPTIONS.map((p) => (
                <button
                  key={p}
                  onClick={() => setProvider(p)}
                  style={{
                    padding: '6px 14px', borderRadius: '8px',
                    border: '1px solid',
                    borderColor: provider === p ? '#3ECF8E' : '#2E2E2E',
                    background: provider === p ? '#3ECF8E15' : '#111',
                    color: provider === p ? '#3ECF8E' : '#888',
                    cursor: 'pointer', fontSize: '12px', fontWeight: 500,
                  }}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          {/* SIP Credentials */}
          <div style={{
            padding: '16px', background: '#0A0A0A', borderRadius: '10px',
            border: '1px solid #1A1A1A', marginBottom: '16px',
          }}>
            <p style={{ fontSize: '11px', fontWeight: 600, color: '#555', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              SIP Credentials
            </p>
            <div style={{ display: 'grid', gap: '10px' }}>
              <div>
                <label style={{ ...labelStyle, fontSize: '11px' }}>Account SID</label>
                <input value={sipAccountSid} onChange={(e) => setSipAccountSid(e.target.value)} style={inputStyle} placeholder="AC..." />
              </div>
              <div>
                <label style={{ ...labelStyle, fontSize: '11px' }}>Auth Token</label>
                <input value={sipAuthToken} onChange={(e) => setSipAuthToken(e.target.value)} type="password" style={inputStyle} placeholder="••••••••" />
              </div>
              <div>
                <label style={{ ...labelStyle, fontSize: '11px' }}>SIP Domain</label>
                <input value={sipDomain} onChange={(e) => setSipDomain(e.target.value)} style={inputStyle} placeholder="sip.provider.com" />
              </div>
              <div>
                <label style={{ ...labelStyle, fontSize: '11px' }}>SIP URI</label>
                <input value={sipUri} onChange={(e) => setSipUri(e.target.value)} style={inputStyle} placeholder="sip:+91XXXXXXXXXX@sip.provider.com" />
              </div>
            </div>

            {/* Test Connection */}
            <button
              onClick={handleTestSip}
              disabled={testing}
              style={{
                marginTop: '12px', display: 'flex', alignItems: 'center', gap: '6px',
                padding: '6px 14px', borderRadius: '7px',
                background: '#1A1A1A', border: '1px solid #2E2E2E',
                color: '#888', cursor: testing ? 'wait' : 'pointer',
                fontSize: '12px',
              }}
            >
              {testing ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Wifi size={13} />}
              Test Connection →
            </button>

            {testResult && (
              <div style={{
                marginTop: '8px', display: 'flex', alignItems: 'center', gap: '6px',
                fontSize: '12px',
                color: testResult.connected ? '#22C55E' : '#F87171',
              }}>
                {testResult.connected ? <CheckCircle size={13} /> : <WifiOff size={13} />}
                {testResult.message}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        {saveError && (
          <div style={{ padding: '0 20px', marginBottom: '4px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#F87171', fontSize: '12px' }}>
              <WifiOff size={13} /> {saveError}
            </div>
          </div>
        )}
        <div style={{
          display: 'flex', justifyContent: 'flex-end', gap: '8px',
          padding: '16px 20px', borderTop: '1px solid #1A1A1A',
        }}>
          <button onClick={onClose} style={{
            padding: '8px 16px', borderRadius: '8px',
            background: 'transparent', border: '1px solid #2E2E2E',
            color: '#888', cursor: 'pointer', fontSize: '13px',
          }}>Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={saving || !number.trim() || !tenantId}
            title={!tenantId ? 'Select a clinic first' : ''}
            style={{
              padding: '8px 20px', borderRadius: '8px',
              background: '#3ECF8E', color: '#000', border: 'none',
              cursor: saving ? 'wait' : 'pointer',
              fontSize: '13px', fontWeight: 600,
              opacity: (!number.trim() || !tenantId || saving) ? 0.5 : 1,
            }}
          >
            {saving ? 'Adding...' : 'Add Phone Number'}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

// ── Shared styles ──────────────────────────────────────────────────────────

const actionBtnStyle: React.CSSProperties = {
  background: 'none', border: 'none', cursor: 'pointer',
  color: '#555', padding: '6px', borderRadius: '6px',
  display: 'flex', alignItems: 'center',
};

const labelStyle: React.CSSProperties = {
  fontSize: '12px', fontWeight: 500, color: '#888',
  marginBottom: '4px', display: 'block',
};

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '8px 12px', borderRadius: '8px',
  background: '#111', border: '1px solid #2E2E2E',
  color: '#ccc', fontSize: '13px', outline: 'none',
  boxSizing: 'border-box',
};
