import {
  AlertTriangle,
  Bot,
  Brain,
  Building2,
  Check,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Copy,
  Hospital,
  Loader,
  PenLine,
  Plus,
  Stethoscope,
  X
} from 'lucide-react';
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import VoiceLibrary from './VoiceLibrary';
import { useProviders } from '../../hooks/useProviders';
import fetchWithAuth from '../../api/client';
import {
  DEFAULT_LANGUAGE, DEFAULT_STT_MODEL, DEFAULT_STT_PROVIDER,
  DEFAULT_TTS_MODEL, DEFAULT_TTS_PROVIDER,
} from '../../api/lockedDefaults';

// ── Types ────────────────────────────────────────────────────────────────────

interface WizardState {
  // Step 1
  clinic_selection: 'existing' | 'new';
  tenant_id: string;
  new_clinic_name: string;
  new_admin_name: string;
  new_admin_email: string;
  new_phone: string;
  new_location: string;
  new_language: string;
  // Step 2
  agent_name: string;
  template: string;
  first_message: string;
  system_prompt: string;
  // Step 3
  // ONE language for the agent (see backend/services/agent_defaults.py) — the
  // wizard collects a single value, not the three that used to disagree.
  //
  // The llm_ provider+model fields are gone for good: the LLM is locked
  // platform-wide, so there is nothing to collect. The stt_/tts_ pairs ARE
  // collected, because switching transcriber or voice vendor is the product's
  // fallback story when one degrades — but only from the backend's whitelist of
  // providers that are genuinely configured and buildable.
  language: string;
  stt_provider: string;
  stt_model: string;
  tts_provider: string;
  tts_model: string;
  tts_voice: string;
  tts_pitch: number;
  tts_pace: number;
  tts_loudness: number;
  llm_temperature: number;
  max_tokens: number;
  // Step 4
  telephony_option: 'assign' | 'existing' | 'skip';
  country_code: string;
  sip_account_sid: string;
  sip_auth_token: string;
  sip_domain: string;
}

const INITIAL_STATE: WizardState = {
  clinic_selection: 'existing',
  tenant_id: '',
  new_clinic_name: '', new_admin_name: '', new_admin_email: '',
  new_phone: '', new_location: '', new_language: 'hi-IN',
  agent_name: 'Receptionist',
  template: 'clinic_receptionist',
  first_message: '',
  system_prompt: '',
  // 'shubh' is Sarvam's default speaker for bulbul:v3 (the locked TTS model).
  // This was 'anushka', which is bulbul:v2-only — every new agent shipped with a
  // voice that 400s on its own model until someone happened to change it.
  tts_voice: 'shubh',
  language: DEFAULT_LANGUAGE,
  // Defaults, not locks. Overwritten from GET /platform/agent/config-options on
  // mount so the wizard can never offer or submit a pair the backend will reject.
  stt_provider: DEFAULT_STT_PROVIDER,
  stt_model: DEFAULT_STT_MODEL,
  tts_provider: DEFAULT_TTS_PROVIDER,
  tts_model: DEFAULT_TTS_MODEL,
  tts_pitch: 0, tts_pace: 1.0, tts_loudness: 1.0,
  llm_temperature: 0.3, max_tokens: 150,
  telephony_option: 'skip',
  country_code: 'IN', sip_account_sid: '', sip_auth_token: '', sip_domain: '',
};

const TEMPLATES = [
  { key: 'clinic_receptionist', name: 'Clinic Receptionist', icon: <Hospital size={20} />, badge: '★ Recommended' },
  { key: 'dental_clinic',       name: 'Dental Clinic',       icon: <Stethoscope size={20} /> },
  { key: 'specialist_hospital', name: 'Specialist Hospital', icon: <Brain size={20} /> },
  { key: 'emergency_care',      name: 'Emergency Care',      icon: <AlertTriangle size={20} /> },
  { key: 'custom',              name: 'Custom (Blank)',       icon: <PenLine size={20} /> },
];

const VOICES_HI = [
  { id: 'anushka',    label: 'anushka',    gender: 'Female' },
  { id: 'pavithra', label: 'pavithra', gender: 'Female' },
  { id: 'maitreyi', label: 'maitreyi', gender: 'Female' },
  { id: 'arvind',   label: 'arvind',   gender: 'Male' },
  { id: 'amol',     label: 'amol',     gender: 'Male' },
  { id: 'amartya',  label: 'amartya',  gender: 'Male' },
];

// ── Shared components ─────────────────────────────────────────────────────────

const Input = ({ label, id, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { label: string; id: string }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    <label htmlFor={id} style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1' }}>{label}</label>
    <input
      id={id}
      {...props}
      style={{
        padding: '10px 12px', borderRadius: '8px', background: '#111', border: '1px solid #2E2E2E',
        color: '#fff', fontSize: '14px', outline: 'none', width: '100%', boxSizing: 'border-box',
        transition: 'border-color 0.15s',
        ...props.style,
      }}
      onFocus={e => { e.target.style.borderColor = '#3ECF8E'; }}
      onBlur={e => { e.target.style.borderColor = '#2E2E2E'; }}
    />
  </div>
);

const Textarea = ({ label, id, rows = 4, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; id: string; rows?: number }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
    <label htmlFor={id} style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1' }}>{label}</label>
    <textarea
      id={id}
      rows={rows}
      {...props}
      style={{
        padding: '10px 12px', borderRadius: '8px', background: '#111', border: '1px solid #2E2E2E',
        color: '#fff', fontSize: '13px', outline: 'none', width: '100%', boxSizing: 'border-box',
        resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.6, transition: 'border-color 0.15s',
        ...props.style,
      }}
      onFocus={e => { e.target.style.borderColor = '#3ECF8E'; }}
      onBlur={e => { e.target.style.borderColor = '#2E2E2E'; }}
    />
  </div>
);

const SliderField = ({ label, value, min, max, step, onChange }: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void;
}) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ fontSize: '12px', color: '#A1A1A1', fontWeight: 500 }}>{label}</span>
      <span style={{ fontSize: '12px', color: '#3ECF8E', fontWeight: 600 }}>{value.toFixed(1)}</span>
    </div>
    <input
      type="range" min={min} max={max} step={step} value={value}
      onChange={e => onChange(parseFloat(e.target.value))}
      style={{ width: '100%', accentColor: '#3ECF8E' }}
    />
  </div>
);

// ── Progress bar ──────────────────────────────────────────────────────────────

const STEPS = ['Clinic', 'Identity', 'Voice', 'Telephony', 'Review'];

function ProgressBar({ current }: { current: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0', marginBottom: '40px' }}>
      {STEPS.map((s, i) => (
        <React.Fragment key={s}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '6px' }}>
            <div style={{
              width: '28px', height: '28px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '12px', fontWeight: 700, flexShrink: 0,
              background: i < current ? '#3ECF8E' : i === current ? '#3ECF8E' : '#1A1A1A',
              border: `2px solid ${i <= current ? '#3ECF8E' : '#2E2E2E'}`,
              color: i <= current ? '#000' : '#555',
              transition: 'all 0.3s',
            }}>
              {i < current ? <Check size={13} /> : i + 1}
            </div>
            <span style={{ fontSize: '11px', fontWeight: 500, color: i === current ? '#3ECF8E' : '#555', whiteSpace: 'nowrap' }}>{s}</span>
          </div>
          {i < STEPS.length - 1 && (
            <div style={{ flex: 1, height: '2px', background: i < current ? '#3ECF8E' : '#2E2E2E', margin: '0 4px', marginBottom: '21px', transition: 'background 0.3s' }} />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

// ── Step 1 — Clinic ───────────────────────────────────────────────────────────

function Step1({ state, onChange, clinicQuery, onClinicQueryChange, clinicResults, clinicsLoading, clinicsError }: {
  state: WizardState;
  onChange: (k: keyof WizardState, v: string) => void;
  clinicQuery: string;
  onClinicQueryChange: (q: string) => void;
  clinicResults: Array<{ id: string; name: string; email: string; language: string; agent_count: number }>;
  clinicsLoading: boolean;
  clinicsError: string;
}) {
  return (
    <div>
      <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', marginBottom: '6px' }}>Which clinic is this agent for?</h2>
      <p style={{ fontSize: '14px', color: '#666', marginBottom: '28px' }}>A clinic can have any number of agents.</p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '28px' }}>
        {[
          { key: 'existing' as const, icon: <Building2 size={24} color="#3ECF8E" />, title: 'Choose Existing Clinic', desc: 'Add another agent to a clinic you\'ve already created.' },
          { key: 'new'      as const, icon: <Plus size={24} color="#A78BFA" />,     title: 'Create New Clinic',    desc: 'Add a new clinic and configure its first agent together.' },
        ].map(opt => (
          <button
            key={opt.key}
            id={`clinic-selection-${opt.key}`}
            onClick={() => onChange('clinic_selection', opt.key)}
            style={{
              padding: '24px', borderRadius: '14px', border: `2px solid ${state.clinic_selection === opt.key ? (opt.key === 'existing' ? '#3ECF8E' : '#A78BFA') : '#2E2E2E'}`,
              background: state.clinic_selection === opt.key ? (opt.key === 'existing' ? 'rgba(62,207,142,0.06)' : 'rgba(167,139,250,0.06)') : '#1A1A1A',
              cursor: 'pointer', textAlign: 'left', transition: 'all 0.2s',
            }}
          >
            <div style={{ marginBottom: '12px' }}>{opt.icon}</div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: '#fff', marginBottom: '6px' }}>{opt.title}</div>
            <div style={{ fontSize: '12px', color: '#666', lineHeight: 1.5 }}>{opt.desc}</div>
          </button>
        ))}
      </div>

      {state.clinic_selection === 'existing' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <label htmlFor="clinic-search" style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1' }}>Search Clinic</label>
          <input
            id="clinic-search"
            value={clinicQuery}
            onChange={e => onClinicQueryChange(e.target.value)}
            placeholder="Type a clinic name…"
            style={{
              padding: '10px 12px', borderRadius: '8px', background: '#111', border: '1px solid #2E2E2E',
              color: '#fff', fontSize: '14px', outline: 'none', width: '100%', boxSizing: 'border-box',
            }}
          />
          {clinicsLoading && <div style={{ color: '#666', fontSize: '13px', padding: '12px 0' }}>⟳ Searching…</div>}
          {clinicsError && <div style={{ color: '#F87171', fontSize: '13px', padding: '8px 12px', background: 'rgba(248,113,113,0.08)', borderRadius: '8px' }}>⚠️ {clinicsError}</div>}
          {!clinicsLoading && clinicResults.length === 0 && !clinicsError && (
            <div style={{ color: '#666', fontSize: '13px', padding: '12px', background: '#111', borderRadius: '8px', border: '1px solid #2E2E2E', textAlign: 'center' }}>
              {clinicQuery.trim()
                ? <>No clinics match "{clinicQuery}". Try a different name or choose "Create New Clinic" above.</>
                : <>No clinics yet. <a href="/superadmin/clinics" style={{ color: '#3ECF8E' }}>Create a clinic first</a> or choose "Create New Clinic" above.</>}
            </div>
          )}
          {clinicResults.map(c => (
            <button
              key={c.id}
              onClick={() => onChange('tenant_id', c.id)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '14px 16px', borderRadius: '10px', border: `1px solid ${state.tenant_id === c.id ? '#3ECF8E' : '#2E2E2E'}`,
                background: state.tenant_id === c.id ? 'rgba(62,207,142,0.06)' : '#111',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
            >
              <div style={{ textAlign: 'left' }}>
                <div style={{ fontSize: '14px', fontWeight: 500, color: '#fff' }}>{c.name}</div>
                <div style={{ fontSize: '12px', color: '#666', marginTop: '2px' }}>{c.email} · {c.language}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '11px', color: c.agent_count > 0 ? '#3ECF8E' : '#666', background: c.agent_count > 0 ? 'rgba(62,207,142,0.1)' : 'rgba(255,255,255,0.05)', padding: '2px 8px', borderRadius: '20px' }}>
                  {c.agent_count === 0 ? 'No agents yet' : `${c.agent_count} agent${c.agent_count === 1 ? '' : 's'}`}
                </span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', padding: '20px', background: '#111', borderRadius: '12px', border: '1px solid #2E2E2E' }}>
          <Input id="new-clinic-name"  label="Clinic Name"       value={state.new_clinic_name}  onChange={e => onChange('new_clinic_name', e.target.value)}  placeholder="Apollo Multispeciality Mumbai" />
          <Input id="new-admin-name"   label="Admin Name"        value={state.new_admin_name}   onChange={e => onChange('new_admin_name', e.target.value)}   placeholder="Dr. Rajesh Kumar" />
          <Input id="new-admin-email"  label="Admin Email"       value={state.new_admin_email}  onChange={e => onChange('new_admin_email', e.target.value)}  placeholder="admin@apolloclinic.com" type="email" />
          <Input id="new-phone"        label="Phone"             value={state.new_phone}        onChange={e => onChange('new_phone', e.target.value)}        placeholder="+91 98765 43210" />
          <Input id="new-location"     label="Location"          value={state.new_location}     onChange={e => onChange('new_location', e.target.value)}     placeholder="Mumbai, Maharashtra" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            <label htmlFor="new-language" style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1' }}>Primary Language</label>
            <select
              id="new-language"
              value={state.new_language}
              onChange={e => onChange('new_language', e.target.value)}
              style={{ padding: '10px 12px', borderRadius: '8px', background: '#111', border: '1px solid #2E2E2E', color: '#fff', fontSize: '14px', outline: 'none', width: '100%', boxSizing: 'border-box' }}
            >
              <option value="en-IN">English</option>
              <option value="hi-IN">Hindi</option>
              <option value="ar-AE">Arabic</option>
              <option value="ml-IN">Malayalam</option>
            </select>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Step 2 — Identity ─────────────────────────────────────────────────────────

function Step2({ state, onChange }: { state: WizardState; onChange: (k: keyof WizardState, v: string) => void }) {
  const VARS = ['{clinic_name}', '{agent_name}', '{working_hours}', '{doctor_count}', '{today_date}'];
  const insertVar = (v: string) => onChange('first_message', state.first_message + v);

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', gap: '24px' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>Configure your agent's identity</h2>
          <p style={{ fontSize: '14px', color: '#666' }}>This is how your AI introduces itself.</p>
        </div>

        <Input id="agent-name" label="Agent Name" value={state.agent_name} onChange={e => onChange('agent_name', e.target.value)} placeholder="Receptionist" />

        <div>
          <label style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1', display: 'block', marginBottom: '10px' }}>Healthcare Template</label>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
            {TEMPLATES.map(t => (
              <button
                key={t.key}
                id={`template-${t.key}`}
                onClick={() => onChange('template', t.key)}
                style={{
                  padding: '14px', borderRadius: '10px', border: `1px solid ${state.template === t.key ? '#3ECF8E' : '#2E2E2E'}`,
                  background: state.template === t.key ? 'rgba(62,207,142,0.08)' : '#111',
                  cursor: 'pointer', textAlign: 'left', transition: 'all 0.15s',
                }}
              >
                <div style={{ color: state.template === t.key ? '#3ECF8E' : '#555', marginBottom: '6px' }}>{t.icon}</div>
                <div style={{ fontSize: '12px', fontWeight: 600, color: state.template === t.key ? '#fff' : '#A1A1A1' }}>{t.name}</div>
                {t.badge && <div style={{ fontSize: '10px', color: '#3ECF8E', marginTop: '4px' }}>{t.badge}</div>}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1', display: 'block', marginBottom: '6px' }}>First Message</label>
          <textarea
            id="first-message"
            rows={3}
            value={state.first_message}
            onChange={e => onChange('first_message', e.target.value)}
            style={{ padding: '10px 12px', borderRadius: '8px', background: '#111', border: '1px solid #2E2E2E', color: '#fff', fontSize: '13px', outline: 'none', width: '100%', boxSizing: 'border-box', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.6 }}
          />
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '8px' }}>
            {VARS.map(v => (
              <button key={v} onClick={() => insertVar(v)} style={{ padding: '3px 8px', borderRadius: '6px', fontSize: '11px', background: 'rgba(62,207,142,0.08)', border: '1px solid rgba(62,207,142,0.2)', color: '#3ECF8E', cursor: 'pointer' }}>{v}</button>
            ))}
          </div>
        </div>

        <Textarea id="system-prompt" label="System Prompt" rows={12} value={state.system_prompt} onChange={e => onChange('system_prompt', e.target.value)} style={{ fontFamily: 'monospace', fontSize: '12px' }} />
      </div>

      {/* Preview panel */}
      <div style={{ background: '#111', border: '1px solid #2E2E2E', borderRadius: '14px', padding: '20px', height: 'fit-content', position: 'sticky', top: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
          <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#3ECF8E' }} />
          <span style={{ fontSize: '13px', fontWeight: 600, color: '#fff' }}>📱 Preview Call</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <ChatBubble from="AI" text={state.first_message.replace('{clinic_name}', 'Apollo Clinic').replace('{agent_name}', state.agent_name || 'Receptionist')} />
          <ChatBubble from="Patient" text="Mujhe doctor se milna hai" />
          <ChatBubble from="AI" text={`Main ${state.agent_name || 'Receptionist'} hoon. Kaunse doctor ya specialization ke liye appointment chahiye?`} muted />
        </div>
        <p style={{ fontSize: '11px', color: '#555', marginTop: '12px' }}>Updates as you edit the prompt above</p>
      </div>
    </div>
  );
}

function ChatBubble({ from, text, muted }: { from: 'AI' | 'Patient'; text: string; muted?: boolean }) {
  const isAI = from === 'AI';
  return (
    <div style={{ display: 'flex', flexDirection: isAI ? 'row' : 'row-reverse', gap: '8px', alignItems: 'flex-start' }}>
      <div style={{ width: '24px', height: '24px', borderRadius: '50%', background: isAI ? 'rgba(62,207,142,0.15)' : '#222', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: '10px' }}>
        {isAI ? '📞' : '👤'}
      </div>
      <div style={{ background: isAI ? '#1A2A1F' : '#1A1A2A', border: `1px solid ${isAI ? 'rgba(62,207,142,0.15)' : '#2E2E2E'}`, borderRadius: '10px', padding: '8px 12px', fontSize: '12px', color: muted ? '#666' : '#ccc', maxWidth: '200px', lineHeight: 1.5, fontStyle: muted ? 'italic' : 'normal' }}>
        {text}
      </div>
    </div>
  );
}

// A labelled <select>, so the four provider/model pickers below do not repeat the
// same 6 lines of inline styling four times.
function WizardSelect({ id, label, value, onChange, options }: {
  id: string; label: string; value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <label htmlFor={id} style={{ fontSize: '12px', color: '#A1A1A1', marginBottom: '8px', display: 'block' }}>{label}</label>
      <select
        id={id}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        style={{ width: '100%', padding: '10px 12px', borderRadius: '8px', background: '#1A1A1A', border: '1px solid #2E2E2E', color: '#fff', fontSize: '14px', outline: 'none' }}
      >
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

// ── Step 3 — Voice ────────────────────────────────────────────────────────────

function Step3({ state, onChange, onChangeMany }: {
  state: WizardState;
  onChange: (k: keyof WizardState | 'open_voice_modal', v: any) => void;
  // Needed because changing a PROVIDER must clear its model in the same render:
  // two sequential onChange calls would fire the config-options fetch against a
  // half-updated pair (new provider, old provider's model), and the response
  // would briefly describe a combination the wizard is not in.
  onChangeMany: (updates: Partial<WizardState>) => void;
}) {
  const { data: providers, loading } = useProviders()
  const ttsProviders = providers?.providers.tts || []
  const sarvamModels = ttsProviders[0]?.models || []
  // Keyed on the wizard's OWN tts_model, not on a constant: the voice roster
  // differs per model (bulbul:v2's speakers are disjoint from v3's), so reading it
  // from anything other than the model actually being submitted is how a new agent
  // gets born with a speaker its own model answers 400 for.
  const selectedModelData = sarvamModels.find(m => m.id === state.tts_model)

  // Voices logic
  const maleVoices = selectedModelData?.voices?.male_voices || []
  const femaleVoices = selectedModelData?.voices?.female_voices || []

  // Provider/model options + the per-language compatibility verdict, from the same
  // endpoint the agent editor uses. Deliberately NOT from useProviders(): that
  // reads the aspirational /platform PROVIDERS catalogue, which lists providers
  // with no key and no pipeline branch — offering those is what let an agent be
  // created on a provider that could not run its first call.
  const [cfg, setCfg] = useState<any>(null)
  useEffect(() => {
    const q = new URLSearchParams({
      stt_provider: state.stt_provider, stt_model: state.stt_model,
      tts_provider: state.tts_provider, tts_model: state.tts_model,
      language: state.language,
    })
    fetchWithAuth(`/platform/agent/config-options?${q}`)
      .then(setCfg)
      .catch(() => setCfg(null))
  }, [state.stt_provider, state.stt_model, state.tts_provider, state.tts_model, state.language])

  // Adopt the backend's normalized model whenever the wizard's value is not one the
  // selected provider serves — which is exactly the state a provider change leaves
  // behind, since it clears the model to ''. Without this the model box would sit
  // blank and the submitted pair would depend on the backend repairing it silently.
  useEffect(() => {
    if (cfg?.stt?.model && !(cfg.stt.models || []).includes(state.stt_model)) {
      onChange('stt_model', cfg.stt.model)
    }
    if (cfg?.tts?.model && !(cfg.tts.models || []).includes(state.tts_model)) {
      onChange('tts_model', cfg.tts.model)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg])

  // Languages the SELECTED voice provider can really speak, each carrying whether
  // the SELECTED transcriber can hear it.
  const modelLanguages: { code: string; name: string; stt_ok?: boolean }[] = cfg?.languages || []

  // Sarvam's bulbul:v2 and bulbul:v3 speaker rosters are disjoint: sending a v2
  // speaker to v3 is a hard 400 ("Speaker 'x' is not compatible with model
  // bulbul:v3"). Switching the model tab therefore has to move the speaker with
  // it, or the wizard silently builds an agent whose voice cannot speak at all.
  // selectModel was removed with the bulbul:v2/v3 model tabs — the TTS model is
  // locked, so there is no longer a model switch that could strand the speaker on
  // an incompatible roster. The initial-render guard below still applies.

  // Same guard for the initial render: the wizard's default voice must be valid
  // for its default model before the admin touches anything.
  useEffect(() => {
    const ids = [...maleVoices, ...femaleVoices]
    if (!ids.length || ids.some((v: any) => v.id === state.tts_voice)) return
    const fallback = ids.find((v: any) => v.default) || ids[0]
    if (fallback) onChange('tts_voice', fallback.id)
  }, [selectedModelData])

  // Likewise the language: a clinic's primary language (which can be ar-AE) or a
  // stale saved value is a 400 from Sarvam, so fall back to one it can speak.
  useEffect(() => {
    if (!modelLanguages.length) return
    if (modelLanguages.some(l => l.code === state.language)) return
    onChange('language', modelLanguages[0].code)
  }, [selectedModelData, state.language])

  const [playingVoice, setPlayingVoice] = useState<string>("")
  const [audioCache, setAudioCache] = useState<Record<string, string>>({})

  const playVoice = async (voiceId: string) => {
    if (playingVoice === voiceId) {
      setPlayingVoice("")
      return
    }
    
    if (audioCache[voiceId]) {
      const audio = new Audio(audioCache[voiceId])
      setPlayingVoice(voiceId)
      audio.onended = () => setPlayingVoice("")
      audio.play()
      return
    }
    
    setPlayingVoice(`loading-${voiceId}`)
    try {
      // No `text`: the backend resolves the sentence from `language`, using the
      // same table the Voice Library and Voice Configuration previews use. This
      // used to be a local four-language map that fell back to English for the
      // other nine — so a Kannada or Malayalam preview spoke English.
      const data = await fetchWithAuth(`/models/voices/preview`, {
        method: 'POST',
        body: JSON.stringify({
          provider: state.tts_provider,
          model: state.tts_model,
          voice_id: voiceId,
          language: state.language,
        })
      })

      const audioUrl = `data:audio/wav;base64,${data.audio_base64}`
      
      setAudioCache(prev => ({...prev, [voiceId]: audioUrl}))
      
      const audio = new Audio(audioUrl)
      setPlayingVoice(voiceId)
      audio.onended = () => setPlayingVoice("")
      audio.play()
      
    } catch {
      setPlayingVoice("")
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '28px' }}>
      <div>
        <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>Configure the agent's voice</h2>
        <p style={{ fontSize: '14px', color: '#666' }}>Powered by dynamic provider configuration.</p>
      </div>

      {/* Transcriber + voice provider. Both dropdowns are populated from the
          backend whitelist, so the wizard cannot create an agent on a provider the
          editor will not show or the pipeline cannot build. The LLM has no such
          section on purpose — it is locked. */}
      <div style={{ border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px', background: '#111', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
        <WizardSelect
          id="stt-provider" label="Transcriber (speech to text)"
          value={state.stt_provider}
          onChange={v => onChangeMany({ stt_provider: v, stt_model: '' })}
          options={(cfg?.stt?.providers || []).map((pr: any) => ({ value: pr.id, label: pr.name }))}
        />
        <WizardSelect
          id="stt-model" label="Transcriber model"
          value={state.stt_model}
          onChange={v => onChange('stt_model', v)}
          options={(cfg?.stt?.models || []).map((m: string) => ({ value: m, label: m }))}
        />
        <WizardSelect
          id="tts-provider" label="Voice (text to speech)"
          value={state.tts_provider}
          onChange={v => onChangeMany({ tts_provider: v, tts_model: '' })}
          options={(cfg?.tts?.providers || []).map((pr: any) => ({ value: pr.id, label: pr.name }))}
        />
        <WizardSelect
          id="tts-model" label="Voice model"
          value={state.tts_model}
          onChange={v => onChange('tts_model', v)}
          options={(cfg?.tts?.models || []).map((m: string) => ({ value: m, label: m }))}
        />
      </div>

      <div className="voice-section" style={{ border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px', background: '#111' }}>
        <h3 style={{ fontSize: '15px', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Voice Selection</h3>

        {modelLanguages.length > 0 && (
          <div style={{ marginBottom: '16px' }}>
            <label htmlFor="agent-language" style={{ fontSize: '12px', color: '#A1A1A1', marginBottom: '8px', display: 'block' }}>Language</label>
            <select
              id="agent-language"
              value={state.language}
              onChange={e => onChange('language', e.target.value)}
              style={{ padding: '10px 12px', borderRadius: '8px', background: '#1A1A1A', border: '1px solid #2E2E2E', color: '#fff', fontSize: '14px', outline: 'none', minWidth: '260px' }}
            >
              {modelLanguages.map(l => (
                <option key={l.code} value={l.code}>
                  {l.name} ({l.code}){l.stt_ok === false ? ' — transcribed by Sarvam AI' : ''}
                </option>
              ))}
            </select>
            <div style={{ fontSize: '11px', color: '#555', marginTop: '6px' }}>
              Sets what the agent hears, speaks and replies in. Every voice below
              can speak any of these {modelLanguages.length} languages.
            </div>
            {/* The chosen transcriber cannot hear every language its voice can
                speak. The call still works — the pipeline substitutes a capable
                transcriber — but that substitution is stated here rather than
                discovered on a live call. */}
            {(cfg?.selected?.errors || []).map((m: string) => (
              <div key={m} style={{ fontSize: '11px', color: '#ff6b6b', marginTop: '6px' }}>⚠ {m}</div>
            ))}
            {(cfg?.selected?.warnings || []).map((m: string) => (
              <div key={m} style={{ fontSize: '11px', color: '#f0b429', marginTop: '6px' }}>⚠ {m}</div>
            ))}
          </div>
        )}

        <div className="voice-gender-group" style={{ marginBottom: '16px' }}>
          <label style={{ fontSize: '12px', color: '#A1A1A1', marginBottom: '8px', display: 'block' }}>Female Voices</label>
          <div className="voice-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px' }}>
            {femaleVoices.map((voice: any) => (
              <div
                key={voice.id}
                style={{
                  background: state.tts_voice === voice.id ? 'rgba(62,207,142,0.1)' : '#1A1A1A',
                  border: `1px solid ${state.tts_voice === voice.id ? '#3ECF8E' : '#2E2E2E'}`,
                  padding: '12px', borderRadius: '8px', display: 'flex', alignItems: 'center', cursor: 'pointer', gap: '10px'
                }}
                onClick={() => onChange('tts_voice', voice.id)}
              >
                <div style={{ background: '#2E2E2E', borderRadius: '50%', width: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>♀</div>
                <div style={{ flex: 1 }}>
                  <div style={{ color: '#fff', fontSize: '14px', fontWeight: 500 }}>{voice.name}</div>
                  <div style={{ color: '#666', fontSize: '11px' }}>{voice.style}</div>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); playVoice(voice.id) }}
                  style={{ background: 'none', border: 'none', color: playingVoice === voice.id ? '#3ECF8E' : '#A1A1A1', cursor: 'pointer' }}
                >
                  {playingVoice === `loading-${voice.id}` ? '⟳' : playingVoice === voice.id ? '⏹' : '▶'}
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="voice-gender-group">
          <label style={{ fontSize: '12px', color: '#A1A1A1', marginBottom: '8px', display: 'block' }}>Male Voices</label>
          <div className="voice-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px' }}>
            {maleVoices.map((voice: any) => (
              <div
                key={voice.id}
                style={{
                  background: state.tts_voice === voice.id ? 'rgba(62,207,142,0.1)' : '#1A1A1A',
                  border: `1px solid ${state.tts_voice === voice.id ? '#3ECF8E' : '#2E2E2E'}`,
                  padding: '12px', borderRadius: '8px', display: 'flex', alignItems: 'center', cursor: 'pointer', gap: '10px'
                }}
                onClick={() => onChange('tts_voice', voice.id)}
              >
                <div style={{ background: '#2E2E2E', borderRadius: '50%', width: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>♂</div>
                <div style={{ flex: 1 }}>
                  <div style={{ color: '#fff', fontSize: '14px', fontWeight: 500 }}>{voice.name}</div>
                  <div style={{ color: '#666', fontSize: '11px' }}>{voice.style}</div>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); playVoice(voice.id) }}
                  style={{ background: 'none', border: 'none', color: playingVoice === voice.id ? '#3ECF8E' : '#A1A1A1', cursor: 'pointer' }}
                >
                  {playingVoice === `loading-${voice.id}` ? '⟳' : playingVoice === voice.id ? '⏹' : '▶'}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', padding: '20px', background: '#111', borderRadius: '12px', border: '1px solid #2E2E2E' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {/* The LLM Model picker was here. Removed — the model is locked
              (see backend/services/agent_defaults.py). It was also a live bug
              source: this wizard sent llm_provider:'gemini' hardcoded alongside
              whatever model was picked from this list, so choosing a Groq model
              here created an agent whose provider and model disagreed. */}
          <SliderField label="Temperature" value={state.llm_temperature} min={0} max={1} step={0.1} onChange={v => onChange('llm_temperature', v)} />
          <SliderField label="Max Tokens" value={state.max_tokens} min={50} max={300} step={10} onChange={v => onChange('max_tokens', v)} />
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <h3 style={{ fontSize: '13px', fontWeight: 600, color: '#A1A1A1', margin: 0 }}>🎚 Voice Tuning</h3>
          <SliderField label="Pitch"     value={state.tts_pitch}    min={-1} max={1} step={0.1} onChange={v => onChange('tts_pitch', v)} />
          <SliderField label="Pace"      value={state.tts_pace}     min={0.5} max={2} step={0.1} onChange={v => onChange('tts_pace', v)} />
          <SliderField label="Loudness"  value={state.tts_loudness} min={0.5} max={2} step={0.1} onChange={v => onChange('tts_loudness', v)} />
        </div>
      </div>
    </div>
  );
}

// ── Step 4 — Telephony ────────────────────────────────────────────────────────

function Step4({ state, onChange }: { state: WizardState; onChange: (k: keyof WizardState, v: string) => void }) {
  const opts: Array<{ key: WizardState['telephony_option']; title: string; icon: string; desc: string }> = [
    { key: 'assign',   title: 'Assign AI Number',         icon: '🔢', desc: 'We assign a virtual number to this clinic.' },
    { key: 'existing', title: "Use Clinic's Existing Number", icon: '📞', desc: 'Set up call forwarding to our AI.' },
    { key: 'skip',     title: 'Browser Testing Only',     icon: '🌐', desc: 'Test in-browser first. Add phone number later.' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <div>
        <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>Connect a phone number</h2>
        <p style={{ fontSize: '14px', color: '#666' }}>How patients will call this agent.</p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {opts.map(o => (
          <button
            key={o.key}
            id={`telephony-${o.key}`}
            onClick={() => onChange('telephony_option', o.key)}
            style={{
              padding: '18px 20px', borderRadius: '12px', border: `1px solid ${state.telephony_option === o.key ? '#3ECF8E' : '#2E2E2E'}`,
              background: state.telephony_option === o.key ? 'rgba(62,207,142,0.06)' : '#111',
              cursor: 'pointer', textAlign: 'left', transition: 'all 0.15s',
              display: 'flex', alignItems: 'center', gap: '16px',
            }}
          >
            <span style={{ fontSize: '24px' }}>{o.icon}</span>
            <div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#fff', marginBottom: '3px' }}>{o.title}</div>
              <div style={{ fontSize: '12px', color: '#666' }}>{o.desc}</div>
            </div>
            {state.telephony_option === o.key && <Check size={16} color="#3ECF8E" style={{ marginLeft: 'auto' }} />}
          </button>
        ))}
      </div>

      {state.telephony_option === 'assign' && (
        <div style={{ padding: '20px', background: '#111', borderRadius: '12px', border: '1px solid #2E2E2E', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
          <Input id="sip-sid"    label="SIP Account SID"  value={state.sip_account_sid} onChange={e => onChange('sip_account_sid', e.target.value)} placeholder="From your Vobiz account" />
          <Input id="sip-token"  label="SIP Auth Token"   value={state.sip_auth_token}  onChange={e => onChange('sip_auth_token', e.target.value)}  placeholder="●●●●●●●●" type="password" />
          <Input id="sip-domain" label="SIP Domain"       value={state.sip_domain}      onChange={e => onChange('sip_domain', e.target.value)}      placeholder="sip.vobiz.com" style={{ gridColumn: '1 / -1' } as React.CSSProperties} />
          <p style={{ gridColumn: '1 / -1', fontSize: '12px', color: '#555', margin: 0 }}>
            ⚠️ Leave blank for now if you want to skip telephony. Agent will still work via browser testing.
          </p>
        </div>
      )}

      {state.telephony_option === 'skip' && (
        <div style={{ padding: '16px', background: 'rgba(62,207,142,0.05)', border: '1px solid rgba(62,207,142,0.15)', borderRadius: '10px' }}>
          <p style={{ fontSize: '13px', color: '#3ECF8E', margin: 0 }}>
            ✅ Your agent will be created in "Configured" status. You can add a phone number anytime from the agent settings.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Step 5 — Review ───────────────────────────────────────────────────────────

function Step5({ state, selectedClinicName }: { state: WizardState; selectedClinicName: string }) {
  const clinicLabel = state.clinic_selection === 'new' ? state.new_clinic_name : (selectedClinicName || 'Unknown');

  const sections = [
    { title: 'CLINIC', rows: [{ k: 'Clinic', v: clinicLabel }] },
    { title: 'IDENTITY', rows: [
      { k: 'Agent name',   v: state.agent_name },
      { k: 'Template',     v: TEMPLATES.find(t => t.key === state.template)?.name || state.template },
      { k: 'First message', v: `"${state.first_message.slice(0, 60)}…"` },
    ] },
    { title: 'VOICE PIPELINE', rows: [
      // Provider/model rows removed — they are locked, so there is nothing here
      // for the admin to review or change. Language and voice are what they chose.
      { k: 'Language', v: state.language },
      { k: 'Voice',    v: state.tts_voice },
      { k: 'Est. latency', v: '~780ms per turn ✅' },
    ] },
    { title: 'TELEPHONY', rows: [
      { k: 'Option', v: state.telephony_option === 'skip' ? 'Browser testing only' : state.telephony_option === 'assign' ? 'Assign AI number' : 'Existing number' },
      ...(state.telephony_option === 'assign' ? [{ k: 'Provider', v: 'Vobiz SIP' }] : []),
    ] },
    { title: 'CAPABILITIES', rows: [
      { k: '✅', v: 'Book appointments' },
      { k: '✅', v: 'Cancel appointments' },
      { k: '✅', v: 'Check availability' },
      { k: '✅', v: 'Emergency transfer' },
      { k: '✅', v: 'Multilingual (auto-detect)' },
    ] },
  ];

  return (
    <div>
      <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>Review your agent</h2>
      <p style={{ fontSize: '14px', color: '#666', marginBottom: '24px' }}>Everything looks good? Let's go live.</p>

      <div style={{ background: '#111', border: '1px solid #2E2E2E', borderRadius: '14px', padding: '24px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', paddingBottom: '16px', borderBottom: '1px solid #1A1A1A' }}>
          <div style={{ width: '40px', height: '40px', borderRadius: '10px', background: 'rgba(62,207,142,0.1)', border: '1px solid rgba(62,207,142,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Bot size={20} color="#3ECF8E" />
          </div>
          <div>
            <div style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>{state.agent_name} ({clinicLabel})</div>
            <div style={{ fontSize: '12px', color: '#666' }}>Ready to create</div>
          </div>
        </div>
        {sections.map(s => (
          <div key={s.title}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: '#555', letterSpacing: '0.08em', marginBottom: '8px' }}>{s.title}</div>
            {s.rows.map(r => (
              <div key={r.k} style={{ display: 'flex', gap: '12px', marginBottom: '4px' }}>
                <span style={{ fontSize: '12px', color: '#555', minWidth: '100px', flexShrink: 0 }}>{r.k}</span>
                <span style={{ fontSize: '12px', color: '#ccc' }}>{r.v}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Success screen ────────────────────────────────────────────────────────────

function SuccessScreen(
  { agentId, credentials, navigate }:
  { agentId: string; credentials: { email?: string; password?: string } | null; navigate: (to: string) => void }
) {
  const [copied, setCopied] = useState(false);
  const email = credentials?.email || '';
  const password = credentials?.password || '';
  // New-clinic path returns real credentials; existing-clinic path returns none.
  const hasCreds = !!(email && password);
  const creds = `Email: ${email}\nPassword: ${password}`;

  return (
    <div style={{ textAlign: 'center', padding: '40px 0' }}>
      <div style={{ width: '64px', height: '64px', borderRadius: '50%', background: 'rgba(62,207,142,0.15)', border: '2px solid #3ECF8E', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
        <CheckCircle size={32} color="#3ECF8E" />
      </div>
      <h2 style={{ fontSize: '22px', fontWeight: 700, color: '#fff', marginBottom: '6px' }}>Agent Created Successfully!</h2>
      <p style={{ fontSize: '14px', color: '#666', marginBottom: '28px' }}>Your AI receptionist is configured and ready to test.</p>

      {hasCreds ? (
        <div style={{ background: '#111', border: '1px solid rgba(251,191,36,0.3)', borderRadius: '12px', padding: '20px', marginBottom: '24px', textAlign: 'left' }}>
          <div style={{ fontSize: '12px', fontWeight: 700, color: '#FBBF24', marginBottom: '10px', letterSpacing: '0.06em' }}>CLINIC LOGIN CREDENTIALS</div>
          <div style={{ fontFamily: 'monospace', fontSize: '13px', color: '#fff', lineHeight: 1.8 }}>
            <div>Email: <span style={{ color: '#3ECF8E' }}>{email}</span></div>
            <div>Password: <span style={{ color: '#3ECF8E' }}>{password}</span></div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '10px' }}>
            <AlertTriangle size={12} color="#FBBF24" />
            <span style={{ fontSize: '11px', color: '#FBBF24' }}>Copy now — the password is shown only once</span>
          </div>
          <button
            onClick={() => { navigator.clipboard.writeText(creds); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
            style={{ marginTop: '10px', padding: '7px 14px', borderRadius: '8px', fontSize: '12px', background: '#1A1A1A', border: '1px solid #2E2E2E', color: '#A1A1A1', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            {copied ? <><Check size={12} color="#3ECF8E" /> Copied!</> : <><Copy size={12} /> Copy Credentials</>}
          </button>
        </div>
      ) : (
        <div style={{ background: '#111', border: '1px solid #2E2E2E', borderRadius: '12px', padding: '16px 20px', marginBottom: '24px', textAlign: 'left' }}>
          <div style={{ fontSize: '13px', color: '#A1A1A1', lineHeight: 1.6 }}>
            This agent was added to an existing clinic. Use that clinic's existing login credentials to sign in.
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <button onClick={() => navigate(`/superadmin/agents/${agentId}?tab=test`)} style={{ padding: '12px', borderRadius: '10px', background: '#3ECF8E', color: '#000', border: 'none', fontWeight: 600, fontSize: '14px', cursor: 'pointer' }}>🎤 Test Agent in Browser</button>
        <button onClick={() => navigate(`/superadmin/agents/${agentId}`)} style={{ padding: '12px', borderRadius: '10px', background: '#1A1A1A', color: '#A1A1A1', border: '1px solid #2E2E2E', fontSize: '14px', cursor: 'pointer' }}>📋 View Agent Settings</button>
        <button onClick={() => navigate('/superadmin/agents')} style={{ padding: '12px', borderRadius: '10px', background: 'none', color: '#666', border: 'none', fontSize: '14px', cursor: 'pointer' }}>← Back to Agents List</button>
      </div>
    </div>
  );
}

// ── Main wizard ───────────────────────────────────────────────────────────────

export default function CreateAgent() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [state, setState] = useState<WizardState>(INITIAL_STATE);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [createdId, setCreatedId] = useState('');
  const [createError, setCreateError] = useState('');
  const [showVoiceModal, setShowVoiceModal] = useState(false);
  const [clinicQuery, setClinicQuery] = useState('');
  const [clinicResults, setClinicResults] = useState<Array<{ id: string; name: string; email: string; language: string; agent_count: number }>>([]);
  const [selectedClinicName, setSelectedClinicName] = useState('');
  const [clinicsLoading, setClinicsLoading] = useState(false);
  const [clinicsError, setClinicsError] = useState('');
  // Real clinic credentials returned by POST /agents for a new clinic (null when
  // attaching to an existing clinic). Replaces the old hardcoded email/password.
  const [credentials, setCredentials] = useState<{ email?: string; password?: string } | null>(null);
  // True once the admin manually edits the System Prompt — stops template
  // auto-fill from overwriting their text (audit P4).
  const [promptEdited, setPromptEdited] = useState(false);

  // Debounced type-ahead search against the backend, re-run on every keystroke
  // (and once on mount with an empty query to show an initial list).
  React.useEffect(() => {
    setClinicsLoading(true);
    setClinicsError('');
    const handle = setTimeout(() => {
      fetchWithAuth(`/tenants/search?q=${encodeURIComponent(clinicQuery.trim())}`)
        .then(data => {
          if (Array.isArray(data)) {
            setClinicResults(data.map((t: any) => ({
              id: t.id,
              name: t.clinic_name,
              email: t.admin_email || '',
              language: t.language || 'en-IN',
              agent_count: t.agent_count ?? 0,
            })));
          }
        })
        .catch(() => setClinicsError('Failed to search clinics. Check backend connection.'))
        .finally(() => setClinicsLoading(false));
    }, 250);
    return () => clearTimeout(handle);
  }, [clinicQuery]);

  // Pre-fill the System Prompt from the selected template, rendered with the
  // clinic's name, when the admin reaches the Agent step. Selecting a template
  // used to leave the field empty, so the created agent had a blank system
  // prompt (audit P4). A manual edit (promptEdited) stops auto-fill; a richer,
  // fully LLM-authored prompt is available via "Generate with LLM" after create.
  React.useEffect(() => {
    if (step !== 1 || promptEdited || state.template === 'custom') return;
    const clinicName = state.clinic_selection === 'new' ? state.new_clinic_name : selectedClinicName;
    const language = state.language;
    let cancelled = false;
    fetchWithAuth('/agents/render-template-prompt', {
      method: 'POST',
      body: JSON.stringify({
        template: state.template,
        language: language || 'en-IN',
        clinic_name: clinicName || 'the clinic',
        agent_name: state.agent_name || 'Receptionist',
      }),
    })
      .then((data: any) => {
        if (cancelled || !data) return;
        setState(prev => ({
          ...prev,
          system_prompt: data.system_prompt || prev.system_prompt,
          first_message: prev.first_message || data.first_message || prev.first_message,
        }));
      })
      .catch(() => { /* non-fatal — leave the field as the admin left it */ });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, state.template]);

  const onChange = (key: keyof WizardState | 'open_voice_modal', value: any) => {
    if (key === 'open_voice_modal') {
      setShowVoiceModal(value);
      return;
    }
    if (key === 'tenant_id') {
      const picked = clinicResults.find(c => c.id === value);
      setSelectedClinicName(picked?.name || '');
      // Default the agent's voice language to the selected clinic's language.
      setState(prev => ({ ...prev, tenant_id: value, ...(picked?.language ? { language: picked.language } : {}) }));
      return;
    }
    if (key === 'new_language') {
      // A new clinic's primary language seeds its first agent's language. Picking
      // a voice no longer overrides this — see handleSelectVoice.
      setState(prev => ({ ...prev, new_language: value, language: value }));
      return;
    }
    if (key === 'template') {
      // Switching template re-enables auto-fill so the prompt follows it.
      setPromptEdited(false);
      setState(prev => ({ ...prev, template: value }));
      return;
    }
    if (key === 'system_prompt') {
      // A manual edit stops template auto-fill from clobbering the admin's text.
      setPromptEdited(true);
      setState(prev => ({ ...prev, system_prompt: value }));
      return;
    }
    setState(prev => ({ ...prev, [key as keyof WizardState]: value }));
  };

  // Several fields in one commit. A blank model means "give me this provider's
  // default"; the backend fills it (agent_defaults.normalize_provider_choice), and
  // the config-options response fills the dropdown, so the wizard never has to
  // hardcode which model belongs to which vendor.
  const onChangeMany = (updates: Partial<WizardState>) => {
    setState(prev => ({ ...prev, ...updates }));
  };

  const handleSelectVoice = (voice: any) => {
    setState(prev => ({
       ...prev,
       // ONLY the voice. This used to also copy the voice's provider, model and
       // catalog language onto the agent, so picking a voice silently changed the
       // agent's language to whatever that voice happened to be tagged with —
       // one of the writers behind the four-way language mismatch.
       //
       // Use the provider's canonical voice id (e.g. Sarvam 'priya'), NOT the
       // display name ('Priya') — the TTS API rejects the display name.
       tts_voice: voice.voice_id || voice.id || voice.name,
    }));
    setShowVoiceModal(false);
  };

  const canNext = () => {
    if (step === 0) {
      if (state.clinic_selection !== 'new') return !!state.tenant_id;
      // New clinic: enforce the fields a functioning clinic actually needs
      // (audit P5). admin_email is login-critical — the backend refuses to
      // create a clinic without it and the clinic login matches on it — so a
      // clinic-name-only "Continue" produced clinics that could never log in.
      const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test((state.new_admin_email || '').trim());
      return !!(
        state.new_clinic_name?.trim() &&
        state.new_admin_name?.trim() &&
        emailOk &&
        state.new_location?.trim()
      );
    }
    return true;
  };

  const handleCreate = async () => {
    setLoading(true);
    setCreateError('');
    try {
      const data = await fetchWithAuth('/agents', {
        method: 'POST',
        body: JSON.stringify({
          clinic_selection: state.clinic_selection,
          tenant_id: state.tenant_id || null,
          new_clinic: state.clinic_selection === 'new' ? { clinic_name: state.new_clinic_name, admin_name: state.new_admin_name, admin_email: state.new_admin_email, phone: state.new_phone, location: state.new_location, language: state.new_language } : null,
          agent_name: state.agent_name, template: state.template,
          first_message: state.first_message, system_prompt: state.system_prompt,
          // ONE language, and no LLM provider/model at all — that pair is locked
          // and the backend applies it itself, so the agent is born consistent with
          // no post-creation correction.
          //
          // The STT/TTS pairs ARE sent and ARE honoured, validated against the same
          // whitelist that populated the dropdowns.
          language: state.language,
          stt_provider: state.stt_provider,
          stt_model: state.stt_model,
          tts_provider: state.tts_provider,
          tts_model: state.tts_model,
          tts_voice: state.tts_voice,
          tts_pitch: state.tts_pitch, tts_pace: state.tts_pace, tts_loudness: state.tts_loudness,
          llm_temperature: state.llm_temperature, max_response_tokens: state.max_tokens,
          telephony_option: state.telephony_option,
        }),
      });
      if (!data.agent_id) {
        setCreateError('Agent created but no ID returned. Check backend logs.');
        setLoading(false);
        return;
      }
      setCreatedId(data.agent_id);
      setCredentials(data.clinic_credentials || null);
      setDone(true);
    } catch (e: any) {
      // fetchWithAuth throws with the backend's `detail` message (409 duplicate
      // clinic name, 404 unknown clinic, etc.) already surfaced as e.message.
      setCreateError(e?.message || 'Cannot reach backend');
    }
    setLoading(false);
  };

  const STEP_COMPONENTS = [
    <Step1
      state={state}
      onChange={(k, v) => onChange(k, v)}
      clinicQuery={clinicQuery}
      onClinicQueryChange={setClinicQuery}
      clinicResults={clinicResults}
      clinicsLoading={clinicsLoading}
      clinicsError={clinicsError}
    />,
    <Step2 state={state} onChange={(k, v) => onChange(k, v)} />,
    <Step3 state={state} onChange={(k, v) => onChange(k, v)} onChangeMany={onChangeMany} />,
    <Step4 state={state} onChange={(k, v) => onChange(k, v)} />,
    <Step5 state={state} selectedClinicName={selectedClinicName} />,
  ];

  return (
    <div style={{ minHeight: '100vh', padding: '40px 32px', maxWidth: '900px', margin: '0 auto' }}>
      <button onClick={() => navigate('/superadmin/agents')} style={{ background: 'none', border: 'none', color: '#666', cursor: 'pointer', fontSize: '13px', marginBottom: '24px', display: 'flex', alignItems: 'center', gap: '6px' }}>
        <ChevronLeft size={14} /> Back to Agents
      </button>

      {done && createdId ? (
        <SuccessScreen agentId={createdId} credentials={credentials} navigate={navigate} />
      ) : (
        <>
          <ProgressBar current={step} />
          {createError && (
            <div style={{ marginBottom: '16px', padding: '12px 16px', background: 'rgba(248,113,113,0.08)', border: '1px solid rgba(248,113,113,0.3)', borderRadius: '10px', color: '#F87171', fontSize: '13px' }}>
              ⚠️ {createError}
            </div>
          )}
          <div style={{ minHeight: '400px' }}>{STEP_COMPONENTS[step]}</div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '36px', paddingTop: '20px', borderTop: '1px solid #1A1A1A' }}>
            <button
              onClick={() => step === 0 ? navigate('/superadmin/agents') : setStep(s => s - 1)}
              style={{ padding: '10px 20px', borderRadius: '9px', background: 'none', border: '1px solid #2E2E2E', color: '#A1A1A1', fontSize: '14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
            >
              <ChevronLeft size={14} /> {step === 0 ? 'Cancel' : 'Back'}
            </button>
            {step < STEPS.length - 1 ? (
              <button
                id="wizard-next-btn"
                disabled={!canNext()}
                onClick={() => setStep(s => s + 1)}
                style={{ padding: '10px 24px', borderRadius: '9px', background: canNext() ? '#3ECF8E' : '#1A1A1A', color: canNext() ? '#000' : '#555', border: 'none', fontSize: '14px', fontWeight: 600, cursor: canNext() ? 'pointer' : 'not-allowed', display: 'flex', alignItems: 'center', gap: '6px', transition: 'all 0.15s' }}
              >
                Continue <ChevronRight size={14} />
              </button>
            ) : (
              <button
                id="wizard-create-btn"
                onClick={handleCreate}
                disabled={loading}
                style={{ padding: '10px 24px', borderRadius: '9px', background: '#3ECF8E', color: '#000', border: 'none', fontSize: '14px', fontWeight: 700, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}
              >
                {loading ? <><Loader size={14} className="animate-spin" /> Creating…</> : '🚀 Create Agent'}
              </button>
            )}
          </div>
        </>
      )}

      {showVoiceModal && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.8)', padding: '24px' }}>
           <div style={{ width: '100%', maxWidth: '1200px', height: '90vh', background: '#0A0A0A', borderRadius: '16px', overflow: 'hidden', display: 'flex', flexDirection: 'column', position: 'relative' }}>
              <button 
                onClick={() => setShowVoiceModal(false)}
                style={{ position: 'absolute', top: '16px', right: '16px', background: 'rgba(255,255,255,0.1)', border: 'none', color: '#fff', width: '32px', height: '32px', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', zIndex: 101 }}
              >
                 <X size={16} />
              </button>
              <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                 <VoiceLibrary isPickerModal onSelectVoice={handleSelectVoice} />
              </div>
           </div>
        </div>
      )}
    </div>
  );
}
