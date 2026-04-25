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
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import VoiceLibrary from './VoiceLibrary';
import { useProviders } from '../../hooks/useProviders';

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
  stt_provider: string;
  stt_model: string;
  tts_provider: string;
  tts_model: string;
  tts_voice: string;
  tts_language: string;
  tts_pitch: number;
  tts_pace: number;
  tts_loudness: number;
  llm_model: string;
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
  first_message: 'Namaste! {clinic_name} mein aapka swagat hai. Main {agent_name} hoon. Aaj main aapki kaise madad kar sakti hoon?',
  system_prompt: '',
  stt_provider: 'sarvam', stt_model: 'saaras:v3',
  tts_provider: 'sarvam', tts_model: 'bulbul:v3',
  tts_voice: 'anushka', tts_language: 'hi-IN',
  tts_pitch: 0, tts_pace: 1.0, tts_loudness: 1.0,
  llm_model: 'gemini-2.0-flash', llm_temperature: 0.3, max_tokens: 150,
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

// Fixture clinics replaced with real API data (loaded in CreateAgent component)
const FIXTURE_CLINICS: Array<{ id: string; name: string; email: string; languages: string; has_agent: boolean }> = [];

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

function Step1({ state, onChange, realClinics, clinicsLoading, clinicsError }: {
  state: WizardState;
  onChange: (k: keyof WizardState, v: string) => void;
  realClinics: Array<{ id: string; name: string; email: string; language: string; has_agent: boolean }>;
  clinicsLoading: boolean;
  clinicsError: string;
}) {
  return (
    <div>
      <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#fff', marginBottom: '6px' }}>Which clinic is this agent for?</h2>
      <p style={{ fontSize: '14px', color: '#666', marginBottom: '28px' }}>Each agent serves one clinic.</p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', marginBottom: '28px' }}>
        {[
          { key: 'existing' as const, icon: <Building2 size={24} color="#3ECF8E" />, title: 'Choose Existing Clinic', desc: 'Assign agent to a clinic you\'ve already created.' },
          { key: 'new'      as const, icon: <Plus size={24} color="#A78BFA" />,     title: 'Create New Clinic',    desc: 'Add a new clinic and configure its agent together.' },
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
          <label style={{ fontSize: '12px', fontWeight: 500, color: '#A1A1A1' }}>Select Clinic</label>
          {clinicsLoading && <div style={{ color: '#666', fontSize: '13px', padding: '12px 0' }}>⟳ Loading clinics…</div>}
          {clinicsError && <div style={{ color: '#F87171', fontSize: '13px', padding: '8px 12px', background: 'rgba(248,113,113,0.08)', borderRadius: '8px' }}>⚠️ {clinicsError}</div>}
          {!clinicsLoading && realClinics.length === 0 && !clinicsError && (
            <div style={{ color: '#666', fontSize: '13px', padding: '12px', background: '#111', borderRadius: '8px', border: '1px solid #2E2E2E', textAlign: 'center' }}>
              No clinics found. <a href="/superadmin/clinics" style={{ color: '#3ECF8E' }}>Create a clinic first</a> or choose "Create New Clinic" above.
            </div>
          )}
          {realClinics.map(c => (
            <button
              key={c.id}
              onClick={() => !c.has_agent && onChange('tenant_id', c.id)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '14px 16px', borderRadius: '10px', border: `1px solid ${state.tenant_id === c.id ? '#3ECF8E' : '#2E2E2E'}`,
                background: state.tenant_id === c.id ? 'rgba(62,207,142,0.06)' : '#111',
                cursor: c.has_agent ? 'not-allowed' : 'pointer', transition: 'all 0.15s', opacity: c.has_agent ? 0.5 : 1,
              }}
            >
              <div style={{ textAlign: 'left' }}>
                <div style={{ fontSize: '14px', fontWeight: 500, color: '#fff' }}>{c.name}</div>
                <div style={{ fontSize: '12px', color: '#666', marginTop: '2px' }}>{c.email} · {c.language}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {c.has_agent && <span style={{ fontSize: '11px', color: '#FBBF24', background: 'rgba(251,191,36,0.1)', padding: '2px 8px', borderRadius: '20px' }}>Agent configured ⚠️</span>}
                {!c.has_agent && <span style={{ fontSize: '11px', color: '#3ECF8E', background: 'rgba(62,207,142,0.1)', padding: '2px 8px', borderRadius: '20px' }}>No agent yet ✓</span>}
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

// ── Step 3 — Voice ────────────────────────────────────────────────────────────

function Step3({ state, onChange }: { state: WizardState; onChange: (k: keyof WizardState | 'open_voice_modal', v: any) => void }) {
  const { data: providers, loading, refresh } = useProviders()
  const llmModels = providers?.providers.llm[0]?.models || []
  const ttsProviders = providers?.providers.tts || []
  const sarvamModels = ttsProviders[0]?.models || []
  const selectedModelData = sarvamModels.find(m => m.id === state.tts_model)
  
  // Voices logic
  const maleVoices = selectedModelData?.voices?.male_voices || []
  const femaleVoices = selectedModelData?.voices?.female_voices || []
  
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
      const sampleTexts: Record<string, string> = {
        "hi-IN": "नमस्ते! मैं आपकी रिसेप्शनिस्ट हूँ। आज मैं आपकी कैसे मदद कर सकती हूँ?",
        "en-IN": "Hello! I'm your AI receptionist. How may I help you today?",
        "ta-IN": "வணக்கம்! நான் உங்கள் AI receptionist. நான் உதவலாமா?",
        "ar-SA": "مرحباً! أنا مساعدتك الذكية. كيف يمكنني مساعدتك؟",
      }
      
      const res = await fetch('/models/voices/preview', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          provider: 'sarvam',
          model: state.tts_model,
          voice_id: voiceId,
          language: state.tts_language,
          text: sampleTexts[state.tts_language] || sampleTexts["en-IN"]
        })
      })
      
      const data = await res.json()
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

      <div className="voice-section" style={{ border: '1px solid #2E2E2E', borderRadius: '12px', padding: '24px', background: '#111' }}>
        <h3 style={{ fontSize: '15px', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Voice Selection</h3>
        <div className="voice-model-tabs" style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          {sarvamModels.map(model => (
            <button
              key={model.id}
              className={`tab ${state.tts_model === model.id ? 'active' : ''}`}
              onClick={() => onChange('tts_model', model.id)}
              style={{
                padding: '8px 16px', borderRadius: '6px', cursor: 'pointer',
                background: state.tts_model === model.id ? '#3ECF8E' : '#1A1A1A',
                color: state.tts_model === model.id ? '#000' : '#A1A1A1',
                border: '1px solid #2E2E2E'
              }}
            >
              {model.name}
              {model.recommended && ' ★'}
            </button>
          ))}
        </div>
        
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
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 style={{ fontSize: '13px', fontWeight: 600, color: '#A1A1A1', margin: 0 }}>LLM Model</h3>
            <button onClick={refresh} style={{ fontSize: '11px', color: '#3ECF8E', background: 'none', border: 'none', cursor: 'pointer' }}>↻ Refresh Models</button>
          </div>
          {loading ? (
            <div style={{ height: 48, background: '#1A1A1A', borderRadius: '8px', animation: 'pulse 2s infinite' }} />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {llmModels.map((model: any) => (
                <button
                  key={model.id}
                  onClick={() => onChange('llm_model', model.id)}
                  style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                    padding: '12px', borderRadius: '8px', cursor: 'pointer',
                    background: state.llm_model === model.id ? 'rgba(62,207,142,0.1)' : '#1A1A1A',
                    border: `1px solid ${state.llm_model === model.id ? '#3ECF8E' : '#2E2E2E'}`,
                  }}
                >
                  <div style={{ color: '#fff', fontSize: '13px', fontWeight: 500 }}>{model.name}</div>
                  <div style={{ display: 'flex', gap: '4px', marginTop: '4px', flexWrap: 'wrap' }}>
                    {model.is_recommended && <span style={{ fontSize: '10px', background: '#3ECF8E', color: '#000', padding: '2px 4px', borderRadius: '4px' }}>★ Recommended</span>}
                    {model.tags?.map((tag: string) => (
                      <span key={tag} style={{ fontSize: '10px', background: '#2E2E2E', color: '#A1A1A1', padding: '2px 4px', borderRadius: '4px' }}>{tag}</span>
                    ))}
                  </div>
                </button>
              ))}
            </div>
          )}
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

function Step5({ state }: { state: WizardState }) {
  const clinicLabel = state.clinic_selection === 'new' ? state.new_clinic_name : FIXTURE_CLINICS.find(c => c.id === state.tenant_id)?.name || 'Unknown';

  const sections = [
    { title: 'CLINIC', rows: [{ k: 'Clinic', v: clinicLabel }] },
    { title: 'IDENTITY', rows: [
      { k: 'Agent name',   v: state.agent_name },
      { k: 'Template',     v: TEMPLATES.find(t => t.key === state.template)?.name || state.template },
      { k: 'First message', v: `"${state.first_message.slice(0, 60)}…"` },
    ] },
    { title: 'VOICE PIPELINE', rows: [
      { k: 'STT',  v: `${state.stt_provider} ${state.stt_model}` },
      { k: 'LLM',  v: `${state.llm_model} (temp: ${state.llm_temperature})` },
      { k: 'TTS',  v: `${state.tts_provider} ${state.tts_model} · ${state.tts_voice} · ${state.tts_language}` },
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

function SuccessScreen({ agentId, password, navigate }: { agentId: string; password: string; navigate: (to: string) => void }) {
  const [copied, setCopied] = useState(false);
  const creds = `Email: admin@clinic.lifodial.com\nPassword: ${password}`;

  return (
    <div style={{ textAlign: 'center', padding: '40px 0' }}>
      <div style={{ width: '64px', height: '64px', borderRadius: '50%', background: 'rgba(62,207,142,0.15)', border: '2px solid #3ECF8E', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 20px' }}>
        <CheckCircle size={32} color="#3ECF8E" />
      </div>
      <h2 style={{ fontSize: '22px', fontWeight: 700, color: '#fff', marginBottom: '6px' }}>Agent Created Successfully!</h2>
      <p style={{ fontSize: '14px', color: '#666', marginBottom: '28px' }}>Your AI receptionist is configured and ready to test.</p>

      <div style={{ background: '#111', border: '1px solid rgba(251,191,36,0.3)', borderRadius: '12px', padding: '20px', marginBottom: '24px', textAlign: 'left' }}>
        <div style={{ fontSize: '12px', fontWeight: 700, color: '#FBBF24', marginBottom: '10px', letterSpacing: '0.06em' }}>CLINIC LOGIN CREDENTIALS</div>
        <div style={{ fontFamily: 'monospace', fontSize: '13px', color: '#fff', lineHeight: 1.8 }}>
          <div>Email: <span style={{ color: '#3ECF8E' }}>admin@clinic.lifodial.com</span></div>
          <div>Password: <span style={{ color: '#3ECF8E' }}>{password}</span></div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '10px' }}>
          <AlertTriangle size={12} color="#FBBF24" />
          <span style={{ fontSize: '11px', color: '#FBBF24' }}>Copy now — shown only once</span>
        </div>
        <button
          onClick={() => { navigator.clipboard.writeText(creds); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
          style={{ marginTop: '10px', padding: '7px 14px', borderRadius: '8px', fontSize: '12px', background: '#1A1A1A', border: '1px solid #2E2E2E', color: '#A1A1A1', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
        >
          {copied ? <><Check size={12} color="#3ECF8E" /> Copied!</> : <><Copy size={12} /> Copy Credentials</>}
        </button>
      </div>

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
  const [realClinics, setRealClinics] = useState<Array<{ id: string; name: string; email: string; language: string; has_agent: boolean }>>([]);
  const [clinicsLoading, setClinicsLoading] = useState(false);
  const [clinicsError, setClinicsError] = useState('');
  const tempPw = 'Lf8#mK2p';

  // Load real tenants from the backend on mount
  React.useEffect(() => {
    setClinicsLoading(true);
    fetch('/tenants')
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data)) {
          setRealClinics(data.map((t: any) => ({
            id: t.id,
            name: t.clinic_name,
            email: t.admin_email || '',
            language: t.language || 'en-IN',
            has_agent: !!t.has_agent,
          })));
        }
      })
      .catch(() => setClinicsError('Failed to load clinics. Check backend connection.'))
      .finally(() => setClinicsLoading(false));
  }, []);

  const onChange = (key: keyof WizardState | 'open_voice_modal', value: any) => {
    if (key === 'open_voice_modal') {
      setShowVoiceModal(value);
      return;
    }
    setState(prev => ({ ...prev, [key as keyof WizardState]: value }));
  };

  const handleSelectVoice = (voice: any) => {
    setState(prev => ({
       ...prev,
       tts_provider: voice.provider,
       tts_model: voice.model,
       tts_voice: voice.name,
       tts_language: voice.language
    }));
    setShowVoiceModal(false);
  };

  const canNext = () => {
    if (step === 0) return state.clinic_selection === 'new' ? !!state.new_clinic_name : !!state.tenant_id;
    return true;
  };

  const handleCreate = async () => {
    setLoading(true);
    setCreateError('');
    try {
      const res = await fetch('/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          clinic_selection: state.clinic_selection,
          tenant_id: state.tenant_id || null,
          new_clinic: state.clinic_selection === 'new' ? { clinic_name: state.new_clinic_name, admin_name: state.new_admin_name, admin_email: state.new_admin_email, phone: state.new_phone, location: state.new_location, language: state.new_language } : null,
          agent_name: state.agent_name, template: state.template,
          first_message: state.first_message, system_prompt: state.system_prompt,
          stt_provider: state.stt_provider, stt_model: state.stt_model,
          tts_provider: state.tts_provider, tts_model: state.tts_model,
          tts_voice: state.tts_voice, tts_language: state.tts_language,
          tts_pitch: state.tts_pitch, tts_pace: state.tts_pace, tts_loudness: state.tts_loudness,
          llm_provider: 'gemini', llm_model: state.llm_model,
          llm_temperature: state.llm_temperature, max_response_tokens: state.max_tokens,
          telephony_option: state.telephony_option,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        // Surface the real backend error (409 duplicate, 400 validation, etc.)
        const msg = data?.detail || `Server error ${res.status}`;
        setCreateError(msg);
        setLoading(false);
        return; // Stay on wizard, don't show success screen
      }
      if (!data.agent_id) {
        setCreateError('Agent created but no ID returned. Check backend logs.');
        setLoading(false);
        return;
      }
      setCreatedId(data.agent_id);
      setDone(true);
    } catch (e: any) {
      setCreateError(`Network error: ${e?.message || 'Cannot reach backend'}`);
    }
    setLoading(false);
  };

  const STEP_COMPONENTS = [
    <Step1 state={state} onChange={(k, v) => onChange(k, v)} realClinics={realClinics} clinicsLoading={clinicsLoading} clinicsError={clinicsError} />,
    <Step2 state={state} onChange={(k, v) => onChange(k, v)} />,
    <Step3 state={state} onChange={(k, v) => onChange(k, v)} />,
    <Step4 state={state} onChange={(k, v) => onChange(k, v)} />,
    <Step5 state={state} />,
  ];

  return (
    <div style={{ minHeight: '100vh', padding: '40px 32px', maxWidth: '900px', margin: '0 auto' }}>
      <button onClick={() => navigate('/superadmin/agents')} style={{ background: 'none', border: 'none', color: '#666', cursor: 'pointer', fontSize: '13px', marginBottom: '24px', display: 'flex', alignItems: 'center', gap: '6px' }}>
        <ChevronLeft size={14} /> Back to Agents
      </button>

      {done && createdId ? (
        <SuccessScreen agentId={createdId} password={tempPw} navigate={navigate} />
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
