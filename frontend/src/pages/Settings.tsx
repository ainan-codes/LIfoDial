import {
    AlertCircle,
    Bell,
    Building2,
    CheckCircle2,
    Copy,
    ExternalLink,
    Moon,
    Phone,
    Send,
    Shield,
    Sun,
    Users,
    Webhook,
    XCircle,
    Zap
} from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import fetchWithAuth from '../api/client';
import { getTenantId, isSuperAdmin } from '../api/auth';
import { useThemeContext } from '../context/ThemeContext';
import AIConfig from './settings/AIConfig';
import Integrations from './settings/Integrations';

// ── Reusable sub-components ───────────────────────────────────────────────────

function SectionHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-6">
      <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{title}</h2>
      {description && <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>{description}</p>}
    </div>
  );
}

function SettingRow({ label, description, children }: {
  label: string; description?: string; children: React.ReactNode;
}) {
  return (
    <div
      className="flex items-center justify-between py-4"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      <div style={{ flex: 1, marginRight: '32px' }}>
        <p style={{ fontSize: '14px', fontWeight: 500, color: 'var(--text-primary)', margin: 0 }}>{label}</p>
        {description && <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>{description}</p>}
      </div>
      {children}
    </div>
  );
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      style={{
        position: 'relative', width: '44px', height: '24px', borderRadius: '9999px',
        border: 'none', cursor: 'pointer',
        backgroundColor: value ? 'var(--accent)' : 'var(--bg-surface-3)',
        transition: 'background-color 0.2s', flexShrink: 0,
      }}
    >
      <span style={{
        position: 'absolute', top: '2px',
        left: value ? '22px' : '2px',
        width: '20px', height: '20px', borderRadius: '50%',
        backgroundColor: '#fff', transition: 'left 0.2s',
      }} />
    </button>
  );
}

function InputField({
  label, value, onChange, placeholder, mono, disabled,
}: {
  label?: string; value: string; onChange?: (v: string) => void;
  placeholder?: string; mono?: boolean; disabled?: boolean;
}) {
  return (
    <div>
      {label && (
        <label style={{
          display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)',
          marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em',
        }}>
          {label}
        </label>
      )}
      <input
        type="text"
        value={value}
        onChange={e => onChange?.(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        style={{
          width: '100%', padding: '10px 12px', fontSize: '14px',
          borderRadius: '8px', outline: 'none',
          backgroundColor: disabled ? 'var(--bg-surface-3)' : 'var(--bg-surface-2)',
          border: '1px solid var(--border)',
          color: disabled ? 'var(--text-muted)' : 'var(--text-primary)',
          fontFamily: mono ? "'JetBrains Mono', monospace" : 'inherit',
          cursor: disabled ? 'default' : 'text',
          transition: 'border-color 0.15s, box-shadow 0.15s',
        }}
        onFocus={e => { if (!disabled) { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.boxShadow = '0 0 0 3px var(--accent-dim)'; } }}
        onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = 'none'; }}
      />
    </div>
  );
}

function SaveButton({ onClick, saved, disabled, label }: {
  onClick: () => void; saved?: boolean; disabled?: boolean; label?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        marginTop: '24px', padding: '10px 24px', borderRadius: '8px',
        fontSize: '14px', fontWeight: 600, color: '#000',
        backgroundColor: saved ? 'var(--accent-hover)' : 'var(--accent)',
        border: 'none', cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.6 : 1, transition: 'background-color 0.15s',
      }}
      onMouseEnter={e => { if (!disabled) (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-hover)'; }}
      onMouseLeave={e => { if (!disabled) (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent)'; }}
    >
      {label ?? (saved ? '✓ Saved' : 'Save Changes')}
    </button>
  );
}

// ── Tab 1 — Clinic Profile ────────────────────────────────────────────────────
// Reads and WRITES the real tenant row. Previously this tab seeded its fields
// from FIXTURE_TENANT ("Apollo Demo Clinic") and its Save button only set a local
// `saved` flag — no API call at all — so it showed "✓ Saved" while discarding
// every edit. Calling hours are stored on the agent (AgentConfig.clinic_info
// .working_hours), which is where the voice pipeline actually reads them from.
interface TenantProfile {
  id: string;
  clinic_name: string;
  language: string;
  ai_number: string | null;
  is_active?: boolean;
}

function ClinicProfileTab() {
  const tenantId = getTenantId();
  const queryClient = useQueryClient();

  const { data: tenant, isLoading } = useQuery<TenantProfile>({
    queryKey: ['tenant', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}`),
    enabled: !!tenantId,
    retry: false,
  });

  // The agent carries working hours. Resolved by CLINIC, from the session token —
  // it used to be looked up by localStorage's email, which is not a reliable
  // pointer to a clinic (see agents.py::get_my_agent and MyAgent.tsx). A
  // superadmin has no clinic of their own, so they must name one.
  const { data: agent } = useQuery<any>({
    queryKey: ['my-agent', tenantId ?? 'none'],
    queryFn: () => fetchWithAuth(
      isSuperAdmin() && tenantId
        ? `/agents/mine?tenant_id=${encodeURIComponent(tenantId)}`
        : '/agents/mine',
    ),
    enabled: !!tenantId,
    retry: false,
  });

  const [name, setName]   = useState('');
  const [lang, setLang]   = useState('');
  const [from, setFrom]   = useState('09:00');
  const [to, setTo]       = useState('19:00');
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [dirty, setDirty] = useState(false);

  // Seed the form from the server once loaded, but never stomp on edits the user
  // has already started making.
  React.useEffect(() => {
    if (!tenant || dirty) return;
    setName(tenant.clinic_name ?? '');
    setLang(tenant.language ?? 'en-IN');
  }, [tenant, dirty]);

  React.useEffect(() => {
    if (!agent || dirty) return;
    // "9:00 AM - 7:00 PM, Mon-Sat" → best-effort 24h values for the time inputs.
    const hours: string = agent?.clinic_info?.working_hours ?? '';
    const m = /(\d{1,2}):(\d{2})\s*(AM|PM)?\s*-\s*(\d{1,2}):(\d{2})\s*(AM|PM)?/i.exec(hours);
    if (m) {
      const to24 = (h: string, mm: string, ap?: string) => {
        let hr = Number(h);
        if (ap?.toUpperCase() === 'PM' && hr < 12) hr += 12;
        if (ap?.toUpperCase() === 'AM' && hr === 12) hr = 0;
        return `${String(hr).padStart(2, '0')}:${mm}`;
      };
      setFrom(to24(m[1], m[2], m[3]));
      setTo(to24(m[4], m[5], m[6]));
    }
  }, [agent, dirty]);

  const edit = <T,>(setter: (v: T) => void) => (v: T) => { setter(v); setDirty(true); };

  const handleSave = async () => {
    setSaving(true);
    setSaveError('');
    try {
      await fetchWithAuth(`/tenants/${tenantId}`, {
        method: 'PUT',
        body: JSON.stringify({ clinic_name: name.trim(), language: lang }),
      });

      // Working hours belong to the agent, not the tenant.
      if (agent?.id) {
        const to12 = (t: string) => {
          const [h, mm] = t.split(':').map(Number);
          const ap = h >= 12 ? 'PM' : 'AM';
          const hr = h % 12 === 0 ? 12 : h % 12;
          return `${hr}:${String(mm).padStart(2, '0')} ${ap}`;
        };
        await fetchWithAuth(`/agents/${agent.id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            clinic_info: {
              ...(agent.clinic_info ?? {}),
              working_hours: `${to12(from)} - ${to12(to)}`,
            },
          }),
        });
      }

      setDirty(false);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      queryClient.invalidateQueries({ queryKey: ['tenant', tenantId] });
      // Must match the query's key above — it is keyed by clinic now, not by email.
      queryClient.invalidateQueries({ queryKey: ['my-agent', tenantId ?? 'none'] });
    } catch (e) {
      setSaveError((e as Error).message || 'Could not save changes');
    } finally {
      setSaving(false);
    }
  };

  const selectStyle: React.CSSProperties = {
    width: '100%', padding: '10px 12px', fontSize: '14px',
    borderRadius: '8px', outline: 'none',
    backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)',
    color: 'var(--text-primary)', cursor: 'pointer',
  };

  return (
    <div className="space-y-5">
      <SectionHeader title="Clinic Profile" description="Basic information about your clinic used by the AI." />

      <InputField
        label="Clinic Name"
        value={isLoading ? 'Loading…' : name}
        onChange={edit(setName)}
        placeholder="Your clinic's name"
        disabled={isLoading}
      />

      <div>
        <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Primary Language
        </label>
        <select value={lang} onChange={e => edit(setLang)(e.target.value)} style={selectStyle} disabled={isLoading}>
          {/* Matches the languages the voice pipeline actually supports
              (backend/agent/resilience.py fallback phrases + Sarvam codes) —
              the old list offered en-US/ar-SA, which Sarvam TTS cannot speak. */}
          <option value="en-IN">🇮🇳 English</option>
          <option value="hi-IN">🇮🇳 Hindi</option>
          <option value="ta-IN">🇮🇳 Tamil</option>
          <option value="te-IN">🇮🇳 Telugu</option>
          <option value="kn-IN">🇮🇳 Kannada</option>
          <option value="ml-IN">🇮🇳 Malayalam</option>
          <option value="mr-IN">🇮🇳 Marathi</option>
          <option value="bn-IN">🇮🇳 Bengali</option>
          <option value="gu-IN">🇮🇳 Gujarati</option>
          <option value="pa-IN">🇮🇳 Punjabi</option>
        </select>
      </div>

      <div>
        <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Clinic Calling Hours
        </label>
        <div className="flex items-center gap-3">
          <input
            type="time" value={from} onChange={e => edit(setFrom)(e.target.value)}
            style={{ ...selectStyle, width: 'auto' }}
          />
          <span style={{ color: 'var(--text-muted)', fontSize: '13px' }}>to</span>
          <input
            type="time" value={to} onChange={e => edit(setTo)(e.target.value)}
            style={{ ...selectStyle, width: 'auto' }}
          />
        </div>
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '6px' }}>
          Outside these hours, AI will indicate that the clinic is closed.
        </p>
      </div>

      {saveError && (
        <p style={{ fontSize: '13px', color: 'var(--destructive)', margin: 0 }}>{saveError}</p>
      )}

      <SaveButton onClick={handleSave} saved={saved} disabled={saving || isLoading} label={saving ? 'Saving…' : undefined} />
    </div>
  );
}

// ── Tab 2 — AI Number ─────────────────────────────────────────────────────────
// Real per-clinic number. This used to render FIXTURE_TENANT.ai_number
// ("+91 90001 23456") to EVERY clinic with `isActive = true` hardcoded, so no
// clinic ever saw its own number and one that had none assigned still looked live.
function AiNumberTab() {
  const [copied, setCopied]   = useState(false);
  const [showModal, setModal] = useState(false);
  const tenantId = getTenantId();

  const { data: tenant } = useQuery<TenantProfile>({
    queryKey: ['tenant', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}`),
    enabled: !!tenantId,
    retry: false,
  });

  const aiNumber = tenant?.ai_number ?? '';
  const isActive = !!tenant?.ai_number && tenant?.is_active !== false;

  const handleCopy = () => {
    navigator.clipboard.writeText(aiNumber).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-6">
      <SectionHeader title="AI Number" description="Your dedicated virtual number that the AI answers." />

      {/* Number display */}
      <div
        className="rounded-xl p-6"
        style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--accent-border)' }}
      >
        <p style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)', marginBottom: '8px' }}>
          Your AI Number
        </p>
        <div className="flex items-center gap-3">
          <p style={{
            fontSize: '28px', fontWeight: 600, color: 'var(--accent)',
            fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.04em', margin: 0,
          }}>
            {aiNumber}
          </p>
          <button
            onClick={handleCopy}
            title="Copy number"
            style={{
              padding: '6px 12px', borderRadius: '8px', fontSize: '12px', fontWeight: 500,
              backgroundColor: copied ? 'var(--accent-dim)' : 'var(--bg-surface)',
              border: '1px solid var(--border)', color: copied ? 'var(--accent)' : 'var(--text-secondary)',
              cursor: 'pointer', transition: 'all 0.15s',
            }}
          >
            {copied ? <><CheckCircle2 size={12} style={{ display: 'inline', marginRight: '4px' }} />Copied!</> : <><Copy size={12} style={{ display: 'inline', marginRight: '4px' }} />Copy</>}
          </button>
        </div>

        {/* Status */}
        <div className="flex items-center gap-2 mt-4">
          {isActive
            ? <><CheckCircle2 size={14} style={{ color: 'var(--accent)' }} /><span style={{ fontSize: '13px', color: 'var(--accent)', fontWeight: 500 }}>Active — Forwarding connected</span></>
            : <><XCircle size={14} style={{ color: 'var(--destructive)' }} /><span style={{ fontSize: '13px', color: 'var(--destructive)', fontWeight: 500 }}>Inactive — Forwarding not detected</span></>
          }
        </div>
      </div>

      {/* Forwarding instructions card */}
      <div
        className="rounded-xl p-5"
        style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
      >
        <div className="flex items-start gap-3 mb-4">
          <AlertCircle size={16} style={{ color: 'var(--warning)', flexShrink: 0, marginTop: '2px' }} />
          <div>
            <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              Call Forwarding Setup
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
              Dial these codes from your clinic phone to route calls to the AI.
            </p>
          </div>
        </div>

        <div className="space-y-3">
          <div
            className="rounded-lg p-4"
            style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)' }}
          >
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>To activate forwarding:</p>
            <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '16px', color: 'var(--accent)', fontWeight: 600 }}>
              *21*{aiNumber.replace(/\s/g, '')}#
            </p>
          </div>
          <div
            className="rounded-lg p-4"
            style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)' }}
          >
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '6px' }}>To deactivate:</p>
            <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: '16px', color: 'var(--text-secondary)', fontWeight: 600 }}>
              ##21#
            </p>
          </div>
        </div>

        <button
          onClick={() => setModal(true)}
          className="flex items-center gap-2"
          style={{
            marginTop: '16px', padding: '8px 16px', borderRadius: '8px',
            fontSize: '13px', fontWeight: 500,
            backgroundColor: 'transparent', border: '1px solid var(--border)',
            color: 'var(--text-secondary)', cursor: 'pointer',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-strong)'; }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--border)'; }}
        >
          <Phone size={13} /> Test Forwarding
        </button>
      </div>

      {/* Test Forwarding modal */}
      {showModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
          onClick={() => setModal(false)}
        >
          <div
            className="rounded-xl p-6"
            style={{
              maxWidth: '420px', width: '100%', margin: '0 16px',
              backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)',
              boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <h3 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px' }}>
              Test Call Forwarding
            </h3>
            <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
              To verify forwarding is active:
            </p>
            <ol style={{ paddingLeft: '20px', color: 'var(--text-secondary)', fontSize: '14px', lineHeight: 1.8 }}>
              <li>Dial the activation code from your clinic landline.</li>
              <li>Call your clinic number from a mobile phone.</li>
              <li>You should hear the AI greeting within 3 rings.</li>
              <li>If successful, the status will update to "Active".</li>
            </ol>
            <button
              onClick={() => setModal(false)}
              style={{
                marginTop: '20px', width: '100%', padding: '10px', borderRadius: '8px',
                fontSize: '14px', fontWeight: 600, color: '#000',
                backgroundColor: 'var(--accent)', border: 'none', cursor: 'pointer',
              }}
            >
              Got it
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Tab 3 — Doctors ───────────────────────────────────────────────────────────
// Real, tenant-scoped doctors. This used to render FIXTURE_DOCTORS — six invented
// names ("Dr. Suresh Menon", "Dr. Priya Nair", …) shown to every clinic, which is
// why a brand-new clinic saw "6 doctors registered" here while its actual
// /doctors page was empty. Same endpoint and react-query key the Doctors page and
// Dashboard already use, so the cache is shared.
interface SettingsDoctor {
  id: string;
  name: string;
  specialization?: string | null;
  is_available?: boolean;
}

function DoctorsTab() {
  const navigate = useNavigate();
  const tenantId = getTenantId();

  const { data, isLoading, error } = useQuery<SettingsDoctor[]>({
    queryKey: ['doctors', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}/doctors`),
    enabled: !!tenantId,
    retry: false,
  });

  const doctors = data ?? [];
  const activeCount = doctors.filter(d => d.is_available).length;

  return (
    <div className="space-y-5">
      <SectionHeader title="Doctors" description="Doctors registered in the AI's knowledge base." />

      <div
        className="rounded-xl p-5"
        style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)' }}
      >
        <div className="flex items-center justify-between mb-4">
          <div>
            <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              {isLoading
                ? 'Loading doctors…'
                : `${doctors.length} ${doctors.length === 1 ? 'doctor' : 'doctors'} registered`}
            </p>
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
              {isLoading ? ' ' : `${activeCount} currently available`}
            </p>
          </div>
          <button
            onClick={() => navigate('/doctors')}
            className="flex items-center gap-2"
            style={{
              padding: '8px 16px', borderRadius: '8px', fontSize: '13px', fontWeight: 500,
              backgroundColor: 'var(--accent)', border: 'none', color: '#000', cursor: 'pointer',
            }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent-hover)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--accent)'; }}
          >
            <ExternalLink size={13} /> Manage Doctors
          </button>
        </div>

        {error && (
          <p style={{ fontSize: '13px', color: 'var(--warning)', margin: 0 }}>
            Couldn't load doctors. Please try again shortly.
          </p>
        )}

        {!isLoading && !error && doctors.length === 0 && (
          <div className="py-6 text-center">
            <p style={{ fontSize: '13px', color: 'var(--text-muted)', margin: 0 }}>
              No doctors yet. Add your first doctor so the AI can book appointments.
            </p>
          </div>
        )}

        <div className="space-y-2">
          {doctors.map(doc => (
            <div
              key={doc.id}
              className="flex items-center justify-between py-2.5 px-3 rounded-lg"
              style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
            >
              <div className="flex items-center gap-3">
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold"
                  style={{ backgroundColor: 'var(--accent-dim)', color: 'var(--accent)' }}
                >
                  {(doc.name || '?').replace(/^Dr\.\s*/, '').split(' ').filter(Boolean).map(w => w[0]).join('').slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <p style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)', margin: 0 }}>{doc.name}</p>
                  <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{doc.specialization || '—'}</p>
                </div>
              </div>
              <div
                className="flex items-center gap-1.5"
                style={{
                  padding: '2px 8px', borderRadius: '9999px', fontSize: '11px', fontWeight: 600,
                  color: doc.is_available ? 'var(--accent)' : 'var(--text-muted)',
                  backgroundColor: doc.is_available ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
                }}
              >
                <div className="w-1 h-1 rounded-full" style={{ backgroundColor: 'currentColor' }} />
                {doc.is_available ? 'Available' : 'On leave'}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Tab 4 — Appearance ────────────────────────────────────────────────────────
function ThemeCard({ label, bg, surface, accent, active, onClick }: {
  label: string; bg: string; surface: string; accent: string; active: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: '160px', borderRadius: '10px', overflow: 'hidden',
        border: active ? '2px solid var(--accent)' : '1px solid var(--border)',
        cursor: 'pointer', backgroundColor: 'transparent', padding: 0,
        transition: 'border-color 0.15s', outline: 'none',
      }}
    >
      <div style={{ backgroundColor: bg, padding: '12px', height: '80px', position: 'relative' }}>
        <div style={{ backgroundColor: surface, borderRadius: '4px', padding: '6px 8px', border: '1px solid rgba(128,128,128,0.2)' }}>
          <div style={{ width: '60%', height: '6px', borderRadius: '3px', backgroundColor: active ? '#fff' : '#999', opacity: 0.8, marginBottom: '5px' }} />
          <div style={{ width: '40%', height: '4px', borderRadius: '2px', backgroundColor: active ? '#fff' : '#999', opacity: 0.3 }} />
        </div>
        <div style={{ position: 'absolute', bottom: '10px', right: '10px', backgroundColor: accent, borderRadius: '4px', padding: '3px 7px' }}>
          <div style={{ width: '24px', height: '5px', borderRadius: '2px', backgroundColor: '#000', opacity: 0.7 }} />
        </div>
      </div>
      <div style={{
        padding: '8px 12px', backgroundColor: 'var(--bg-surface-2)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>{label}</span>
        {active && <span style={{ fontSize: '10px', fontWeight: 600, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Active</span>}
      </div>
    </button>
  );
}

function AppearanceTab() {
  const { theme, toggle } = useThemeContext();
  const isDark = theme === 'dark';

  return (
    <div className="space-y-5">
      <SectionHeader title="Appearance" description="Customize how Lifodial looks for you." />

      <SettingRow label="Theme" description="Choose between dark and light display">
        <div className="flex items-center gap-3">
          <span style={{ fontSize: '13px', color: 'var(--text-secondary)', fontWeight: 500 }}>
            {isDark ? 'Dark' : 'Light'}
          </span>
          <button
            onClick={toggle}
            aria-label="Toggle theme"
            style={{
              position: 'relative', width: '48px', height: '26px', borderRadius: '9999px',
              border: 'none', cursor: 'pointer', padding: 0,
              backgroundColor: isDark ? 'var(--bg-surface-3)' : 'var(--accent)',
              transition: 'background-color 0.2s ease',
            }}
          >
            <span style={{
              position: 'absolute', top: '3px',
              left: isDark ? '3px' : '23px',
              width: '20px', height: '20px', borderRadius: '50%',
              backgroundColor: isDark ? 'var(--text-muted)' : '#000',
              transition: 'left 0.2s ease',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              {isDark ? <Moon size={11} color="var(--bg-page)" /> : <Sun size={11} color="var(--accent)" />}
            </span>
          </button>
        </div>
      </SettingRow>

      <div className="flex gap-4 pt-2">
        <ThemeCard label="Dark"  bg="#0F0F0F" surface="#1A1A1A" accent="#3ECF8E" active={isDark} onClick={() => { if (!isDark) toggle(); }} />
        <ThemeCard label="Light" bg="#F9FAFB" surface="#FFFFFF" accent="#3ECF8E" active={!isDark} onClick={() => { if (isDark) toggle(); }} />
      </div>
    </div>
  );
}

// ── Tab 5 — Notifications ─────────────────────────────────────────────────────
function NotificationsTab() {
  const [telegram, setTelegram]     = useState(false);
  const [channelId, setChannelId]   = useState('');
  const [testSent, setTestSent]     = useState(false);
  const [callAlerts, setCallAlerts] = useState(true);
  const [summary, setSummary]       = useState(false);
  const [failed, setFailed]         = useState(true);

  const sendTest = () => {
    setTestSent(true);
    setTimeout(() => setTestSent(false), 3000);
  };

  return (
    <div className="space-y-5">
      <SectionHeader title="Notifications" description="Get alerted when the AI handles important calls." />

      <SettingRow label="Call alerts" description="Notify when AI handles high-priority or escalated calls">
        <Toggle value={callAlerts} onChange={setCallAlerts} />
      </SettingRow>
      <SettingRow label="Daily summary emails" description="Receive end-of-day call reports">
        <Toggle value={summary} onChange={setSummary} />
      </SettingRow>
      <SettingRow label="Failed call alerts" description="Alert when AI cannot resolve a caller">
        <Toggle value={failed} onChange={setFailed} />
      </SettingRow>

      {/* Telegram section */}
      <div
        className="rounded-xl p-5 space-y-4"
        style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)', marginTop: '8px' }}
      >
        <div className="flex items-center justify-between">
          <div>
            <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
              Telegram Notifications
            </p>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
              Receive instant booking alerts via Telegram bot
            </p>
          </div>
          <Toggle value={telegram} onChange={setTelegram} />
        </div>

        {telegram && (
          <>
            <InputField
              label="Telegram Channel ID"
              value={channelId}
              onChange={setChannelId}
              placeholder="e.g. -1001234567890"
              mono
            />
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              Add @lifodial_bot to your channel, then paste the Channel ID above.
            </p>
            <button
              onClick={sendTest}
              className="flex items-center gap-2"
              style={{
                padding: '8px 16px', borderRadius: '8px', fontSize: '13px', fontWeight: 500,
                backgroundColor: testSent ? 'var(--accent-dim)' : 'transparent',
                border: '1px solid var(--border)',
                color: testSent ? 'var(--accent)' : 'var(--text-secondary)',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
            >
              <Send size={13} />
              {testSent ? 'Test sent!' : 'Test notification'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Tab 6 — System Status ─────────────────────────────────────────────────────
// For a CLINIC ADMIN this answers exactly one question: is my clinic active?
//
// It used to show the platform's internal stack to every clinic — an MVP
// checklist naming LiveKit and "HIS mock", plus a "Backend Services" card listing
// FastAPI :8001 / PostgreSQL :5432 / Redis :6379 / LiveKit :7880 all hardcoded to
// "Offline", and a footer telling the reader to run `make dev`. That is developer
// infrastructure detail, it was permanently wrong (the hardcoded flags never
// reflected reality), and it made a healthy clinic look broken. (The superadmin
// System Health page that used to carry the detailed view was removed too.)
function ClinicStatusTab() {
  const tenantId = getTenantId();

  const { data, isLoading, error } = useQuery<{ clinic_name?: string; is_active?: boolean }>({
    queryKey: ['tenant', tenantId],
    queryFn: () => fetchWithAuth(`/tenants/${tenantId}`),
    enabled: !!tenantId,
    retry: false,
  });

  const isActive = data?.is_active !== false;

  return (
    <div className="space-y-5">
      <SectionHeader
        title="System Status"
        description="Whether your clinic's AI receptionist is currently active."
      />

      <div
        className="rounded-xl p-6"
        style={{
          backgroundColor: 'var(--bg-surface)',
          border: `1px solid ${isLoading || error ? 'var(--border)' : isActive ? 'var(--accent-border)' : 'var(--border)'}`,
        }}
      >
        {isLoading ? (
          <p style={{ fontSize: '14px', color: 'var(--text-muted)', margin: 0 }}>Checking status…</p>
        ) : error ? (
          <div className="flex items-center gap-3">
            <AlertCircle size={18} style={{ color: 'var(--warning)', flexShrink: 0 }} />
            <div>
              <p style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
                Status unavailable
              </p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
                We couldn't reach the service just now. Please try again shortly.
              </p>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            {isActive
              ? <CheckCircle2 size={20} style={{ color: 'var(--accent)', flexShrink: 0 }} />
              : <XCircle size={20} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />}
            <div>
              <p style={{
                fontSize: '15px', fontWeight: 600, margin: 0,
                color: isActive ? 'var(--accent)' : 'var(--text-primary)',
              }}>
                {isActive ? 'Clinic active' : 'Clinic inactive'}
              </p>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
                {isActive
                  ? 'Your AI receptionist is live and able to take calls.'
                  : 'Your AI receptionist is not taking calls. Contact Lifodial support to reactivate.'}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Full platform-internals view — superadmin only (see ClinicStatusTab above).
function SystemStatusTab() {
  const criteria = [
    { label: 'Clinic onboarding configured',         done: true  },
    { label: 'AI number assigned and active',         done: true  },
    { label: 'Voice pipeline connected (LiveKit)',    done: true  },
    { label: 'Booking service connected (HIS mock)', done: true  },
    { label: 'Languages supported: Hindi, English',  done: true  },
    { label: 'Call forwarding verified',             done: false },
    { label: 'First real call received',             done: false },
  ];

  const doneCount = criteria.filter(c => c.done).length;

  return (
    <div className="space-y-5">
      <SectionHeader
        title="System Status"
        description="MVP acceptance criteria for the Lifodial AI Receptionist."
      />

      {/* Progress summary */}
      <div
        className="rounded-xl p-5"
        style={{
          backgroundColor: 'var(--bg-surface-2)',
          border: '1px solid var(--accent-border)',
        }}
      >
        <div className="flex items-center justify-between mb-3">
          <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>
            MVP Acceptance Criteria
          </p>
          <span
            style={{
              padding: '2px 10px', borderRadius: '9999px', fontSize: '12px', fontWeight: 600,
              color: 'var(--accent)', backgroundColor: 'var(--accent-dim)', border: '1px solid var(--accent-border)',
            }}
          >
            {doneCount}/{criteria.length} complete
          </span>
        </div>

        {/* Progress bar */}
        <div style={{ height: '4px', borderRadius: '2px', backgroundColor: 'var(--bg-surface-3)', marginBottom: '16px', overflow: 'hidden' }}>
          <div
            style={{
              height: '100%', borderRadius: '2px',
              backgroundColor: 'var(--accent)',
              width: `${Math.round((doneCount / criteria.length) * 100)}%`,
              transition: 'width 0.5s ease',
            }}
          />
        </div>

        <div className="space-y-3">
          {criteria.map((c, i) => (
            <div key={i} className="flex items-center gap-3">
              {c.done
                ? <CheckCircle2 size={16} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                : <div style={{ width: '16px', height: '16px', borderRadius: '50%', border: '2px solid var(--border)', flexShrink: 0 }} />
              }
              <span
                style={{
                  fontSize: '14px',
                  color: c.done ? 'var(--text-primary)' : 'var(--text-muted)',
                  fontWeight: c.done ? 500 : 400,
                }}
              >
                {c.label}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Backend services quick check */}
      <div
        className="rounded-xl p-5"
        style={{ backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }}
      >
        <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px' }}>
          Backend Services
        </p>
        {[
          { name: 'API Server (FastAPI)',  port: '8001', ok: false },
          { name: 'Database (PostgreSQL)', port: '5432', ok: false },
          { name: 'Cache (Redis)',          port: '6379', ok: false },
          { name: 'Voice Agent (LiveKit)', port: '7880', ok: false },
        ].map(svc => (
          <div key={svc.name} className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid var(--border)' }}>
            <div>
              <span style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>{svc.name}</span>
              <span style={{ fontSize: '12px', color: 'var(--text-muted)', marginLeft: '8px', fontFamily: "'JetBrains Mono', monospace" }}>:{svc.port}</span>
            </div>
            <span style={{
              fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '9999px',
              color: svc.ok ? 'var(--accent)' : 'var(--text-muted)',
              backgroundColor: svc.ok ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
            }}>
              {svc.ok ? 'Online' : 'Offline'}
            </span>
          </div>
        ))}
        <p style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '10px' }}>
          Start the backend with <code style={{ fontFamily: "'JetBrains Mono', monospace", backgroundColor: 'var(--bg-surface-2)', padding: '1px 5px', borderRadius: '4px' }}>make dev</code> to connect all services.
        </p>
      </div>
    </div>
  );
}

// `superadminOnly` tabs are hidden from clinic admins.
//
// "AI Config" is superadmin-only because that panel is the platform's provider
// console: it renders raw API-key entry fields for 11 providers (including
// LiveKit), every provider's env-var name (GEMINI_API_KEY, SARVAM_API_KEY, …),
// links to each provider's billing console, and the live model IDs making up the
// voice pipeline. None of that belongs to a clinic. The behavioural settings a
// clinic legitimately controls (greeting, prompt, languages, voice choice) live
// on the agent itself, not here.
// A clinic admin sees four tabs: Clinic Profile, Doctors, Appearance, System
// Status. The rest are `superadminOnly`:
//
//   * AI Number — provisioning and call-forwarding for a number the platform
//     owns and assigns. A clinic cannot change which number it was given.
//   * Integrations — webhook/Sheets plumbing wired during onboarding; editing it
//     silently breaks the clinic's own booking exports.
//   * Notifications — alerting config for the platform team, not the clinic.
//
// `superadminOnly` is not decoration: it drops the entry from the tab bar AND
// gates the render branch below, so the panel cannot be reached by forcing tab
// state either. There is no URL to guard — tabs are component state, not routes.
const TABS = [
  { id: 'clinic',        label: 'Clinic Profile', icon: Building2  },
  { id: 'ai-number',     label: 'AI Number',      icon: Phone,     superadminOnly: true },
  { id: 'ai-config',     label: 'AI Config',      icon: Zap,       superadminOnly: true },
  { id: 'integrations',  label: 'Integrations',   icon: Webhook,   superadminOnly: true },
  { id: 'doctors',       label: 'Doctors',        icon: Users      },
  { id: 'appearance',   label: 'Appearance',     icon: Sun        },
  { id: 'notifications', label: 'Notifications',  icon: Bell,      superadminOnly: true },
  { id: 'system',        label: 'System Status',  icon: Shield     },
] as const;

type TabId = typeof TABS[number]['id'];

// ── Settings Page ─────────────────────────────────────────────────────────────
export default function Settings() {
  const [activeTab, setActiveTab] = useState<TabId>('clinic');
  const showSuperadminOnly = isSuperAdmin();
  const visibleTabs = TABS.filter(
    t => !('superadminOnly' in t && t.superadminOnly) || showSuperadminOnly
  );
  // Belt and braces: if `activeTab` ever ends up on a tab this role cannot see,
  // fall back to the first visible one rather than rendering a blank panel.
  const currentTab: TabId = visibleTabs.some(t => t.id === activeTab)
    ? activeTab
    : visibleTabs[0].id;

  return (
    <div data-testid="settings-page" className="h-full flex flex-col">
      {/* Top bar */}
      <div
        className="px-8 py-5 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      >
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--text-primary)', letterSpacing: '-0.01em', margin: 0 }}>
          Settings
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '2px' }}>
          Manage your Lifodial AI receptionist configuration
        </p>
      </div>

      {/* Tab bar */}
      <div
        className="flex items-center gap-1 px-8 flex-shrink-0 overflow-x-auto"
        style={{ borderBottom: '1px solid var(--border)', backgroundColor: 'var(--bg-surface)' }}
      >
        {visibleTabs.map(tab => {
          const Icon = tab.icon;
          const isActive = currentTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className="flex items-center gap-2 whitespace-nowrap"
              style={{
                padding: '12px 16px',
                fontSize: '13px',
                fontWeight: isActive ? 600 : 500,
                color: isActive ? 'var(--accent)' : 'var(--text-muted)',
                backgroundColor: 'transparent',
                border: 'none',
                borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                cursor: 'pointer',
                marginBottom: '-1px',
                transition: 'color 0.15s',
              }}
              onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLElement).style.color = 'var(--text-secondary)'; }}
              onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLElement).style.color = 'var(--text-muted)'; }}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto" style={{ backgroundColor: 'var(--bg-page)' }}>
        <div style={{ maxWidth: '680px', margin: '0 auto', padding: '32px 24px' }}>
          {/* Every superadmin-only panel is gated HERE as well as excluded from the
              tab bar above. Hiding the button alone would leave the panel one
              stale `activeTab` away from rendering for a clinic admin. */}
          {currentTab === 'clinic'        && <ClinicProfileTab />}
          {currentTab === 'ai-number'     && showSuperadminOnly && <AiNumberTab />}
          {currentTab === 'ai-config'     && showSuperadminOnly && <AIConfig />}
          {currentTab === 'integrations'  && showSuperadminOnly && <Integrations />}
          {currentTab === 'doctors'       && <DoctorsTab />}
          {currentTab === 'appearance'    && <AppearanceTab />}
          {currentTab === 'notifications' && showSuperadminOnly && <NotificationsTab />}
          {currentTab === 'system'        && (showSuperadminOnly ? <SystemStatusTab /> : <ClinicStatusTab />)}
        </div>
      </div>
    </div>
  );
}
