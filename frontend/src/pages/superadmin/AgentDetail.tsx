import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Headphones,
  History,
  Loader2,
  Mic,
  Pause,
  Phone,
  Play,
  RefreshCw,
  Settings,
  X
} from 'lucide-react';
import React, { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import fetchWithAuth, { API_URL } from '../../api/client';
import {
  DEFAULT_LANGUAGE, DEFAULT_LLM_MODEL, DEFAULT_STT_MODEL, DEFAULT_STT_PROVIDER,
  DEFAULT_TTS_MODEL, DEFAULT_TTS_PROVIDER,
} from '../../api/lockedDefaults';
import { getToken } from '../../api/auth';
// Lazy: pulls in the LiveKit/WebRTC client stack (~526kB alone, the single
// largest chunk in the app) — only needed when Test Agent is opened.
const TestAgentModal = lazy(() => import('../../components/TestAgentModal'));
import VoiceLibrary from './VoiceLibrary';
import { useSAStore } from '../../store/saStore';

const ACCENT = '#00D4AA';
const BG = '#0a0a0a';
const CARD_BG = '#0f0f0f';
const BORDER = 'rgba(255,255,255,0.06)';

// Human-friendly provider label for preview error messages (never Sarvam-only).
const _PROVIDER_LABELS: Record<string, string> = {
  sarvam: 'Sarvam AI', elevenlabs: 'ElevenLabs', openai_tts: 'OpenAI TTS',
  cartesia: 'Cartesia', playht: 'PlayHT', azure_tts: 'Azure Neural',
  deepgram_aura: 'Deepgram Aura',
};
const prettyProvider = (p?: string) => _PROVIDER_LABELS[p || ''] || (p ? p : 'Provider');

// mm:ss formatter for the sample player timer.
const fmtTime = (secs: number) => {
  if (!isFinite(secs) || secs < 0) secs = 0;
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
};

// Quick-inject prompt blocks. Each appends a real, production-quality
// instruction block to the system prompt (previously these just appended their
// own button label — a no-op bug). Kept provider-neutral so they work with any LLM.
const PROMPT_SNIPPETS: { label: string; block: string }[] = [
  { label: '+ Appointment booking', block:
`## Appointment Booking
When a caller wants to book, reschedule, or cancel an appointment:
1. Ask for the patient's full name and phone number (confirm the number by reading it back).
2. Ask which doctor or department, and their preferred day and time.
3. Offer the nearest available slots; never invent availability you haven't been given.
4. Read the final appointment details back to the caller and get an explicit "yes" before confirming.
5. If no slot fits, offer to take a callback request rather than leaving the caller without a next step.` },
  { label: '+ Clinic hours', block:
`## Clinic Hours & Location
- State opening hours clearly when asked, including which days the clinic is closed.
- If the caller asks about a time outside working hours, tell them the next time the clinic is open.
- Give the address and a nearby landmark if asked, and offer to send directions by SMS if that capability is enabled.` },
  { label: '+ Doctor list', block:
`## Doctors & Specialities
- When asked "which doctors are available", list doctors by speciality, not all at once — ask what kind of problem the caller has first, then suggest the right speciality.
- Do not give medical advice or diagnoses; route clinical questions to booking an appointment with the appropriate doctor.` },
  { label: '+ Emergency redirect', block:
`## Emergency Handling
- If the caller describes a medical emergency (chest pain, difficulty breathing, severe bleeding, unconsciousness, stroke symptoms), STOP the normal flow immediately.
- Tell them clearly to call emergency services / go to the nearest emergency room now, and offer to connect them to the clinic's emergency line if one is configured.
- Never attempt to book a routine appointment for an emergency.` },
  { label: '+ Language detection', block:
`## Language
- Detect the language the caller is speaking and respond in that same language for the rest of the call.
- If the caller switches languages mid-call, switch with them.
- Keep responses natural and conversational in the chosen language — do not mix languages within a single sentence unless the caller does.` },
];

// ── UI Components ────────────────────────────────────────────────────────────

const Label = ({ children }: { children: React.ReactNode }) => (
  <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.45)', marginBottom: '6px', fontWeight: 600 }}>
    {children}
  </div>
);

const Helper = ({ children }: { children: React.ReactNode }) => (
  <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.35)', marginTop: '4px' }}>
    {children}
  </div>
);

const Input = ({ value, onChange, placeholder, type = 'text', style, min, max }: any) => (
  <input
    type={type}
    value={value ?? ''}
    onChange={e => onChange(e.target.value)}
    placeholder={placeholder}
    min={min}
    max={max}
    style={{
      width: '100%', padding: '10px 14px', borderRadius: '8px', background: '#1a1a1a',
      border: '1px solid rgba(255,255,255,0.1)', color: '#fff', fontSize: '13px', outline: 'none',
      boxSizing: 'border-box', transition: 'border 0.2s', ...style
    }}
    onFocus={e => (e.currentTarget.style.borderColor = ACCENT)}
    onBlur={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)')}
  />
);

const Select = ({ value, onChange, options, style }: any) => (
  <div style={{ position: 'relative', width: '100%' }}>
    <select
      value={value ?? ''}
      onChange={e => onChange(e.target.value)}
      style={{
        width: '100%', padding: '10px 14px', borderRadius: '8px', background: '#1a1a1a',
        border: '1px solid rgba(255,255,255,0.1)', color: '#fff', fontSize: '13px', outline: 'none',
        appearance: 'none', cursor: 'pointer', ...style
      }}
      onFocus={e => (e.currentTarget.style.borderColor = ACCENT)}
      onBlur={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)')}
    >
      {options.map((o: any) => (
        <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
      ))}
    </select>
    <ChevronDown size={14} color="rgba(255,255,255,0.5)" style={{ position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
  </div>
);

const Textarea = ({ value, onChange, placeholder, rows = 3, mono }: any) => (
  <textarea
    value={value ?? ''}
    onChange={e => onChange(e.target.value)}
    placeholder={placeholder}
    rows={rows}
    style={{
      width: '100%', padding: '10px 14px', borderRadius: '8px', background: '#1a1a1a',
      border: '1px solid rgba(255,255,255,0.1)', color: '#fff', fontSize: mono ? '12px' : '13px',
      fontFamily: mono ? 'monospace' : 'inherit', outline: 'none', boxSizing: 'border-box',
      resize: 'vertical', lineHeight: 1.5,
    }}
    onFocus={e => (e.currentTarget.style.borderColor = ACCENT)}
    onBlur={e => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)')}
  />
);

const Toggle = ({ checked, onChange, label, helper }: any) => (
  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', cursor: 'pointer' }} onClick={() => onChange(!checked)}>
    <div style={{ marginTop: '2px', width: '36px', height: '20px', borderRadius: '10px', background: checked ? ACCENT : '#333', position: 'relative', transition: 'background 0.2s', flexShrink: 0 }}>
      <div style={{ position: 'absolute', top: '2px', left: checked ? '18px' : '2px', width: '16px', height: '16px', borderRadius: '50%', background: '#fff', transition: 'left 0.2s' }} />
    </div>
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <span style={{ fontSize: '13px', color: '#fff' }}>{label}</span>
      {helper && <Helper>{helper}</Helper>}
    </div>
  </div>
);

const Slider = ({ value, onChange, min = 0, max = 1, step = 0.1, leftLabel, rightLabel }: any) => {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', width: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div style={{ position: 'relative', flex: 1, height: '4px', background: 'rgba(255,255,255,0.1)', borderRadius: '2px' }}>
          <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', background: ACCENT, borderRadius: '2px', width: `${pct}%` }} />
          <input
            type="range" min={min} max={max} step={step} value={value}
            onChange={e => onChange(parseFloat(e.target.value))}
            style={{ position: 'absolute', width: '100%', height: '100%', opacity: 0, cursor: 'pointer', top: 0, left: 0 }}
          />
          <div style={{ position: 'absolute', top: '50%', left: `${pct}%`, width: '14px', height: '14px', background: '#fff', borderRadius: '50%', transform: 'translate(-50%, -50%)', pointerEvents: 'none' }} />
        </div>
        <span style={{ fontSize: '12px', color: ACCENT, minWidth: '30px', textAlign: 'right' }}>{value}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'rgba(255,255,255,0.35)' }}>
        <span>{leftLabel}</span>
        <span>{rightLabel}</span>
      </div>
    </div>
  );
};

const TagInput = ({ tags, onChange, placeholder }: any) => {
  const [val, setVal] = useState('');
  return (
    <div style={{ background: '#1a1a1a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '6px 10px', display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
      {(tags||[]).map((t: string, i: number) => (
        <div key={i} style={{ background: 'rgba(255,255,255,0.1)', padding: '2px 8px', borderRadius: '4px', fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', color: '#fff' }}>
          {t} <span style={{ cursor: 'pointer', opacity: 0.5 }} onClick={() => onChange(tags.filter((_:any, j:number) => j !== i))}>×</span>
        </div>
      ))}
      <input
        value={val}
        onChange={e => setVal(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && val.trim()) {
            onChange([...(tags||[]), val.trim()]);
            setVal('');
          }
        }}
        placeholder={tags?.length ? '' : placeholder}
        style={{ background: 'none', border: 'none', color: '#fff', fontSize: '13px', outline: 'none', flex: 1, minWidth: '100px' }}
      />
    </div>
  );
};

// ── Prompt version history (system_prompt / first_message) ──────────────────
// Last 5 versions with one-click revert — see backend/routers/agents.py
// GET/POST /agents/{id}/prompt-history[/{history_id}/revert].
function PromptHistoryButton({ agentId, field, onReverted }: {
  agentId: string; field: 'system_prompt' | 'first_message'; onReverted: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reverting, setReverting] = useState<string | null>(null);
  const [history, setHistory] = useState<{ id: string; value: string; created_at: string }[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onOutside);
    return () => document.removeEventListener('mousedown', onOutside);
  }, [open]);

  const handleOpen = async () => {
    setOpen(true);
    setLoading(true);
    try {
      const data = await fetchWithAuth(`/agents/${agentId}/prompt-history?field=${field}`);
      setHistory(Array.isArray(data) ? data : []);
    } catch {
      setHistory([]);
    } finally {
      setLoading(false);
    }
  };

  const handleRevert = async (historyId: string) => {
    setReverting(historyId);
    try {
      const data = await fetchWithAuth(`/agents/${agentId}/prompt-history/${historyId}/revert`, { method: 'POST' });
      onReverted(data.value);
      setOpen(false);
    } catch {
      alert('Failed to revert.');
    } finally {
      setReverting(null);
    }
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={handleOpen}
        style={{ background: 'none', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '4px 8px', fontSize: '11px', color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}
      >
        <History size={11} /> History
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: '28px', right: 0, zIndex: 60, width: '320px',
          background: '#161616', border: `1px solid ${BORDER}`, borderRadius: '10px',
          padding: '12px', boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
        }}>
          <div style={{ fontSize: '11px', fontWeight: 700, color: 'rgba(255,255,255,0.45)', textTransform: 'uppercase', marginBottom: '8px' }}>
            Last {history.length || 5} version{history.length === 1 ? '' : 's'}
          </div>
          {loading ? (
            <div style={{ fontSize: '12px', color: '#666', padding: '6px 0' }}>Loading…</div>
          ) : history.length === 0 ? (
            <div style={{ fontSize: '12px', color: '#666', padding: '6px 0' }}>No earlier versions yet — edit and save to start building history.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '280px', overflowY: 'auto' }}>
              {history.map(h => (
                <div key={h.id} style={{ border: `1px solid ${BORDER}`, borderRadius: '8px', padding: '8px' }}>
                  <div style={{ fontSize: '10px', color: '#666', marginBottom: '4px' }}>{new Date(h.created_at).toLocaleString()}</div>
                  <div style={{ fontSize: '11px', color: '#ccc', marginBottom: '8px', whiteSpace: 'pre-wrap', maxHeight: '54px', overflow: 'hidden' }}>
                    {h.value.slice(0, 160)}{h.value.length > 160 ? '…' : ''}
                  </div>
                  <button
                    onClick={() => handleRevert(h.id)}
                    disabled={reverting === h.id}
                    style={{ fontSize: '11px', fontWeight: 600, background: 'rgba(0,212,170,0.1)', color: ACCENT, border: 'none', borderRadius: '6px', padding: '4px 10px', cursor: reverting === h.id ? 'not-allowed' : 'pointer', opacity: reverting === h.id ? 0.6 : 1 }}
                  >
                    {reverting === h.id ? 'Reverting…' : 'Revert to this'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Section Card Component ───────────────────────────────────────────────────

const CollapsibleSection = ({ icon: Icon, title, summary, children }: any) => {
  const [expanded, setExpanded] = useState(true);
  return (
    <div style={{ background: CARD_BG, border: `1px solid ${BORDER}`, borderRadius: '12px', overflow: 'hidden', marginBottom: '12px' }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ padding: '20px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', background: expanded ? 'transparent' : 'rgba(255,255,255,0.01)', transition: 'background 0.2s' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <Icon size={18} color={ACCENT} />
          <span style={{ fontSize: '15px', fontWeight: 500, color: '#fff' }}>{title}</span>
          {!expanded && <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.45)', marginLeft: '12px' }}>{summary}</span>}
        </div>
        <div style={{ transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}>
          <ChevronRight size={16} color="rgba(255,255,255,0.45)" />
        </div>
      </div>
      {expanded && (
        <div style={{ padding: '0 24px 24px 24px', borderTop: `1px solid rgba(255,255,255,0.03)` }}>
          <div style={{ paddingTop: '20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>
            {children}
          </div>
        </div>
      )}
    </div>
  );
};

// ── Main Page ────────────────────────────────────────────────────────────────

// There is deliberately NO model catalogue in this file. The Model dropdown is
// populated from GET /platform/llm/models, which asks Groq itself on every cache
// miss.
//
// The function that used to live here, getLlmFallbackModels, is why: it listed
// gemini-2.5-flash-8b among Gemini's models, which is also the exact string one live
// agent had stored against provider 'groq' — Groq answers 404 for it, so that
// agent's LLM was dead. A per-provider model catalogue on the client is precisely
// the affordance that let that pair be assembled. The provider half is gone for good
// (locked to Groq server-side); the model half is a real choice again, but only ever
// from Groq's live answer.

/** Human-readable context window, e.g. 131072 → "131K context". */
const contextLabel = (tokens?: number) =>
  !tokens ? '' : tokens >= 1000 ? `${Math.round(tokens / 1000)}K context` : `${tokens} context`;

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const [agent, setAgent] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<null | 'saving' | 'saved' | 'error'>(null);
  const [showTest, setShowTest] = useState(false);
  const timerRef = useRef<any>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Monotonic id for agent GETs, so an out-of-order response cannot repaint the
  // editor with superseded data. See loadAgent.
  const loadSeqRef = useRef(0);

  // Publish this agent's name so the breadcrumb shows it instead of the raw
  // UUID taken from the URL (audit P5).
  const setEntityLabel = useSAStore(s => s.setEntityLabel);
  useEffect(() => {
    if (agentId && agent?.agent_name) setEntityLabel(agentId, agent.agent_name);
  }, [agentId, agent?.agent_name, setEntityLabel]);

  // Play Sample player state machine (idle | loading | playing | error)
  const [samplePlayer, setSamplePlayer] = useState<'idle' | 'loading' | 'playing' | 'error'>('idle');
  const [sampleProgress, setSampleProgress] = useState(0); // 0..1 of duration
  const [sampleDuration, setSampleDuration] = useState(0); // seconds
  const [samplePosition, setSamplePosition] = useState(0); // seconds
  const [sampleError, setSampleError] = useState<string | null>(null);
  // Brief in-memory cache of the last synthesized sample, keyed by voice/settings,
  // so replaying the same voice doesn't re-hit the provider. Invalidated on any
  // change to voice/model/language/pitch/pace/etc via the cache key.
  const sampleCacheRef = useRef<{ key: string; url: string } | null>(null);

  // System prompt "Generate with LLM" state
  const [generatingPrompt, setGeneratingPrompt] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [preGeneratePrompt, setPreGeneratePrompt] = useState<string | null>(null);
  const [generateProviderUsed, setGenerateProviderUsed] = useState<string | null>(null);
  const generateAbortRef = useRef<AbortController | null>(null);

  // First message "Compose with AI" state
  const [composingFirst, setComposingFirst] = useState(false);
  const [composeError, setComposeError] = useState<string | null>(null);
  const [preComposeFirst, setPreComposeFirst] = useState<string | null>(null);
  const [composeProviderUsed, setComposeProviderUsed] = useState<string | null>(null);
  const composeAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (sampleCacheRef.current) {
        URL.revokeObjectURL(sampleCacheRef.current.url);
        sampleCacheRef.current = null;
      }
    };
  }, []);
  
  // Tab navigation. Assistant is the only tab in this MVP phase — the Logs /
  // Tools / Analysis / Advanced tabs and their sections were removed, so there
  // is nothing left to scroll-spy between.
  type AgentTab = 'assistant';
  const [activeTab, setActiveTab] = useState<AgentTab>('assistant');
  const AGENT_TABS: { id: AgentTab; label: string; icon: any }[] = [
    { id: 'assistant', label: 'Assistant',  icon: Mic },
  ];

  // Refs for the scroll container + the Assistant section anchor
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Record<AgentTab, HTMLDivElement | null>>({
    assistant: null,
  });

  // Scroll to section when tab is clicked
  const handleTabClick = (tabId: AgentTab) => {
    setActiveTab(tabId);
    const el = sectionRefs.current[tabId];
    if (el && scrollContainerRef.current) {
      const containerTop = scrollContainerRef.current.getBoundingClientRect().top;
      const elTop = el.getBoundingClientRect().top;
      const offset = elTop - containerTop + scrollContainerRef.current.scrollTop - 16;
      scrollContainerRef.current.scrollTo({ top: offset, behavior: 'smooth' });
    }
  };

  // Test lab state
  const [testTab, setTestTab] = useState<'voice'|'chat'>('voice');
  const [chatLog, setChatLog] = useState<{from: 'agent'|'user', text: string}[]>([]);
  const [chatIn, setChatIn] = useState('');
  
  // Voice picker modal
  const [showVoiceModal, setShowVoiceModal] = useState(false);

  // Label for THE one language, e.g. "Malayalam (ml-IN)". Falls back to the bare
  // code until the catalogue arrives, so this never renders blank.
  //
  // Reads the raw catalogue rather than the dropdown's option labels: those carry
  // an inline "— transcribed by Sarvam AI" caveat, which belongs in the picker but
  // not in a section header.
  const languageLabel = (code?: string) => {
    if (!code) return '—';
    const hit = (cfgOptions?.languages || []).find((l: any) => l.code === code);
    return hit ? `${hit.name} (${hit.code})` : code;
  };

  const [ttsVoices, setTtsVoices] = useState<any[]>([]);
  const [ttsLanguages, setTtsLanguages] = useState<{ value: string; label: string }[]>([]);

  // Everything the Transcriber and Voice sections offer, from ONE backend call:
  // the selectable providers, each provider's real models, the language list, and
  // whether the currently-configured combination genuinely works.
  //
  // The LLM PROVIDER dropdown is not here and is not coming back — the provider is
  // locked platform-wide (backend/services/agent_defaults.py). The LLM MODEL has its
  // own state below, fetched from Groq's live catalogue rather than from this
  // endpoint. STT and TTS provider/model ARE here: switching provider is the
  // product's fallback story when a vendor degrades, so the choice stays. What
  // changed is that the option list is a whitelist of providers that are genuinely
  // configured and buildable, instead of the aspirational /platform PROVIDERS
  // catalogue that used to offer ElevenLabs, Whisper, PlayHT and Azure alongside the
  // two that work.
  const [cfgOptions, setCfgOptions] = useState<any>(null);

  // Groq's live model list for the Model dropdown, and the reason it is missing when
  // it is missing.
  //
  // `llmModelsError` is rendered rather than swallowed on purpose: the backend
  // answers 503 with Groq's own explanation instead of serving a stale list (see
  // GET /platform/llm/models). An empty dropdown next to a visible error is
  // recoverable; a plausible-looking list of models that 404 mid-call is not. The
  // agent's CURRENT model is always offered regardless, so a fetch failure can never
  // make the field look unset or silently move an agent off its model.
  const [llmModels, setLlmModels] = useState<any[] | null>(null);
  const [llmModelsError, setLlmModelsError] = useState<string | null>(null);
  const [refreshingModels, setRefreshingModels] = useState(false);

  const loadLlmModels = useCallback(async (opts?: { refresh?: boolean }) => {
    const refresh = opts?.refresh === true;
    if (refresh) setRefreshingModels(true);
    try {
      const d = await fetchWithAuth(`/platform/llm/models${refresh ? '?refresh=true' : ''}`);
      setLlmModels(Array.isArray(d?.models) ? d.models : []);
      setLlmModelsError(null);
    } catch (e: any) {
      // fetchWithAuth surfaces the backend's `detail`, which is Groq's own reason
      // (bad key, unreachable, nothing usable) — far more actionable than "failed".
      setLlmModels(null);
      setLlmModelsError(e?.message || 'Could not load the model list from Groq.');
    } finally {
      if (refresh) setRefreshingModels(false);
    }
  }, []);

  useEffect(() => { loadLlmModels(); }, [loadLlmModels]);

  // The agent's CURRENT model is ALWAYS an option, even when Groq's list could not be
  // fetched or no longer contains it.
  //
  // Two reasons, and the second is the one that matters: a <select> whose value
  // matches no <option> renders blank — so the field would read as "this agent has no
  // model" during any Groq hiccup — and the browser reports that blank state as the
  // first option's value, meaning one stray change event could save a model nobody
  // chose. Pinning the current value keeps the control honest in both directions.
  //
  // When the list DID load and the current model is absent from it, that is labelled
  // rather than hidden: the agent is sitting on a model Groq no longer serves, which
  // is a real problem (it is what left one live agent answering 404 on every call).
  // The backend repairs such a row on its next save — see apply_locked_defaults'
  // llm_model_ok tri-state.
  const currentLlmModel = agent?.llm_model || DEFAULT_LLM_MODEL;
  const selectedLlmModel = (llmModels || []).find((m: any) => m.id === currentLlmModel) || null;
  const llmModelOptions = React.useMemo(() => {
    const opts = (llmModels || []).map((m: any) => ({
      value: m.id,
      label: [
        m.id,
        contextLabel(m.context_window),
        // The number that actually decides how many calls a day this agent can
        // serve, and one no Groq response header reports.
        m.daily_token_budget ? `${Math.round(m.daily_token_budget / 1000)}K tokens/day` : '',
        m.reasoning ? 'reasoning' : '',
        m.booking_verified ? '' : 'booking not verified',
      ].filter(Boolean).join(' · '),
    }));
    if (!opts.some((o: any) => o.value === currentLlmModel)) {
      opts.unshift({
        value: currentLlmModel,
        // Nothing is claimed about the model when the list is simply unavailable —
        // "we could not ask Groq" and "Groq says this does not exist" are different
        // facts, and the backend keeps them distinct too (groq_catalog.check_model).
        label: llmModels === null
          ? currentLlmModel
          : `${currentLlmModel} · no longer served by Groq`,
      });
    }
    return opts;
  }, [llmModels, currentLlmModel]);

  // `quiet` re-reads the agent WITHOUT touching the page-level `loading` flag.
  //
  // That flag gates the whole page on a full-screen "Loading agent..." (see the
  // early return below), so calling this the normal way after a provider change
  // tore the entire editor down and rebuilt it — the "full page reload / blank
  // flash" on every Voice Provider switch. There was never a location.reload()
  // or a key={provider}; it was this refetch flipping the page gate.
  //
  // A quiet load also must not clear `agent` on failure: the PATCH that
  // triggered it already succeeded, so blanking the editor to "Agent not found"
  // over a transient GET would throw away a working screen and the operator's
  // scroll position. It keeps what is on screen and leaves the save status to
  // report the problem.
  const loadAgent = useCallback(async (opts?: { quiet?: boolean }) => {
    const quiet = opts?.quiet === true;
    if (!quiet) setLoading(true);
    setLoadError(null);

    // Only the newest load may write to state. Switching provider twice quickly
    // fires two PATCH+refetch pairs, and without this the SLOWER response could
    // land last and repaint the editor with the superseded provider.
    const seq = ++loadSeqRef.current;

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);

    try {
      const data = await fetchWithAuth(`/agents/${agentId}`, { signal: controller.signal });
      if (!data || typeof data !== 'object') {
        throw new Error('Invalid agent payload');
      }

      if (seq === loadSeqRef.current) setAgent(data);
    } catch (e: any) {
      console.error('Agent detail load failed:', e);
      if (!quiet && seq === loadSeqRef.current) {
        setAgent(null);
        setLoadError('Unable to load this agent. Please try again.');
      }
    } finally {
      clearTimeout(timeout);
      // NOT seq-gated: a loud load must always clear the flag it set, even when a
      // newer quiet refetch has superseded it. Gating this on seq leaves `loading`
      // stuck true forever (the quiet load never clears it) and the page frozen on
      // "Loading agent...".
      if (!quiet) setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    loadAgent();
  }, [loadAgent]);

  // ── Centralized landing behavior for EVERY navigation into this page ────────
  // React Router reuses this component instance when only :agentId changes
  // (same route, different param), so the scroll container's DOM node — and
  // whatever tab the IntersectionObserver last saw — persists from the
  // previous agent unless explicitly reset here. This is the single place
  // that controls it; don't patch individual callers instead.
  //
  // Runs in a layout effect (before paint) so there's no visible flash of the
  // old scroll position for a split second on navigation.
  useLayoutEffect(() => {
    setActiveTab('assistant');
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = 0;
    }
  }, [agentId]);

  // Voice list, from the agent's SELECTED TTS provider/model.
  //
  // Falls back to the platform default while the agent row is still loading, so
  // the picker is never populated from a vendor the agent is not on — reading a
  // provider off a stale row is how a voice list could describe one vendor while
  // the call ran on another.
  const ttsProvider = agent?.tts_provider || DEFAULT_TTS_PROVIDER;
  const ttsModel = agent?.tts_model || DEFAULT_TTS_MODEL;

  useEffect(() => {
    fetchWithAuth(`/platform/tts/voices/${ttsProvider}?model=${encodeURIComponent(ttsModel)}`)
      .then(d => {
        const voices = Array.isArray(d.voices) ? d.voices : [];
        // NO language suffix in the label. Sarvam's speakers are
        // language-agnostic — every bulbul voice renders every one of its
        // languages — so the old `(${v.language})` tag was a second, independent
        // language display AND factually wrong: it labelled shruti "hi-IN" while
        // shruti speaks Malayalam correctly. That tag is what made the stakeholder's
        // Voice field disagree with the Language field beside it.
        setTtsVoices(voices.map((v: any) => ({
          value: v.voice_id || v.id || v.name,
          label: v.gender ? `${v.name} · ${v.gender}` : v.name,
        })));
      })
      .catch(() => { setTtsVoices([]); });
  }, [ttsProvider, ttsModel]);

  // Provider/model/language options + the compatibility verdict. Re-fetched
  // whenever any input to that verdict changes, so the warning under the Language
  // field can never describe a provider other than the selected one.
  //
  // The LANGUAGE list comes from here rather than from the voices payload because
  // it now carries per-language STT support flags, which depend on the transcriber
  // — a fact the TTS endpoint has no way to know.
  const sttProvider = agent?.stt_provider || DEFAULT_STT_PROVIDER;
  const sttModel = agent?.stt_model || DEFAULT_STT_MODEL;

  useEffect(() => {
    const q = new URLSearchParams({
      stt_provider: sttProvider, stt_model: sttModel,
      tts_provider: ttsProvider, tts_model: ttsModel,
      language: agent?.language || DEFAULT_LANGUAGE,
    });
    fetchWithAuth(`/platform/agent/config-options?${q}`)
      .then(d => {
        setCfgOptions(d);
        const langs = Array.isArray(d?.languages) ? d.languages : [];
        // The label carries the honest caveat inline, so an operator scanning the
        // dropdown sees which languages the selected transcriber cannot actually
        // hear before choosing one — not afterwards, on a live call.
        setTtsLanguages(langs.map((l: any) => ({
          value: String(l.code),
          label: `${l.name || l.code} (${l.code})${l.stt_ok ? '' : ' — transcribed by Sarvam AI'}`,
        })));
      })
      .catch(() => { setCfgOptions(null); setTtsLanguages([]); });
  }, [sttProvider, sttModel, ttsProvider, ttsModel, agent?.language]);

  const updateField = useCallback((key: string, val: any) => {
    setAgent(prev => {
      const next = { ...prev, [key]: val };
      
      // Auto-save debounce
      if (timerRef.current) clearTimeout(timerRef.current);
      setSaveStatus('saving');
      timerRef.current = setTimeout(async () => {
        try {
          const payloadVal = (Array.isArray(val) || typeof val === 'object') ? JSON.stringify(val) : val;
          await fetchWithAuth(`/agents/${agentId}`, {
            method: 'PATCH',
            body: JSON.stringify({ [key]: payloadVal })
          });
          setSaveStatus('saved');
          setTimeout(() => setSaveStatus(null), 3000);
        } catch {
          setSaveStatus('error');
        }
      }, 1500);

      return next;
    });
  }, [agentId]);

  const updateFields = useCallback((updates: Record<string, any>) => {
    setAgent(prev => {
      const next = { ...prev, ...updates };
      
      if (timerRef.current) clearTimeout(timerRef.current);
      setSaveStatus('saving');
      timerRef.current = setTimeout(async () => {
        try {
          const payload = { ...updates };
          Object.keys(payload).forEach(k => {
            if (Array.isArray(payload[k]) || typeof payload[k] === 'object') {
              payload[k] = JSON.stringify(payload[k]);
            }
          });
          await fetchWithAuth(`/agents/${agentId}`, {
            method: 'PATCH',
            body: JSON.stringify(payload)
          });
          setSaveStatus('saved');
          setTimeout(() => setSaveStatus(null), 3000);
        } catch {
          setSaveStatus('error');
        }
      }, 1500);
      return next;
    });
  }, [agentId]);

  // Provider/model changes save IMMEDIATELY and then re-read the row, unlike every
  // other field's 1.5s debounce-and-assume.
  //
  // Two reasons, both about not showing the operator something untrue:
  //   * the backend may legitimately change MORE than what was sent — switching
  //     transcriber to Sarvam AI makes 'nova-3' meaningless, and switching voice
  //     model to bulbul:v2 invalidates a v3-only speaker. Optimistic local state
  //     would leave a model in the box that the row does not hold.
  //   * these are the fields whose disagreement with the row was the original bug.
  //     Reading back what was actually stored is the cheap way to guarantee the UI
  //     and the DB agree.
  const changeProviderOrModel = useCallback(async (updates: Record<string, any>) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setSaveStatus('saving');
    setAgent((prev: any) => ({ ...prev, ...updates }));
    try {
      await fetchWithAuth(`/agents/${agentId}`, {
        method: 'PATCH',
        body: JSON.stringify(updates),
      });
      // quiet: the optimistic setAgent above already put the new provider on
      // screen. This refetch exists to pick up what the SERVER derived (a reset
      // tts_model, a repaired voice, the language mirrors) and must not blank the
      // page to do it.
      await loadAgent({ quiet: true });
      setSaveStatus('saved');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch {
      setSaveStatus('error');
      // Re-read so a rejected change (e.g. a language the new provider cannot
      // speak) does not linger in the UI as if it had been accepted. Also quiet —
      // the error is already reported through saveStatus.
      loadAgent({ quiet: true });
    }
  }, [agentId, loadAgent]);

  const handleGeneratePrompt = useCallback(async () => {
    if (generatingPrompt) return; // debounce: ignore clicks while one is in flight
    const originalPrompt = agent?.system_prompt ?? '';
    setGenerateError(null);
    setGenerateProviderUsed(null);
    setPreGeneratePrompt(originalPrompt);
    setGeneratingPrompt(true);

    const controller = new AbortController();
    generateAbortRef.current = controller;
    let receivedAny = false;

    try {
      const token = getToken();
      const response = await fetch(`${API_URL}/agents/${agentId}/generate-system-prompt`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Request failed (${response.status})`);
      }

      // Response is newline-delimited JSON events, streamed as they're generated.
      updateField('system_prompt', '');
      let buffer = '';
      let liveText = '';
      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.trim()) continue;
          let evt: any;
          try {
            evt = JSON.parse(line);
          } catch {
            continue;
          }

          if (evt.type === 'meta') {
            setGenerateProviderUsed(evt.fallback_used ? `${evt.provider} (fallback)` : evt.provider);
          } else if (evt.type === 'chunk') {
            receivedAny = true;
            liveText += evt.text;
            updateField('system_prompt', liveText);
          } else if (evt.type === 'error') {
            throw new Error(evt.message || 'Generation failed');
          }
          // 'done' needs no action — loop just ends naturally.
        }
      }

      if (!receivedAny || !liveText.trim()) {
        // Never wipe the existing prompt on an empty/whitespace result.
        updateField('system_prompt', originalPrompt);
        throw new Error('The model returned an empty response. Please try again.');
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        setGenerateError(err?.message || 'Generation failed. Please try again.');
        // Also restore on any failure (e.g. network drop mid-stream) so a
        // partial/garbled generation never overwrites the admin's prior text
        // unless at least a full, non-empty response streamed in.
        if (!receivedAny) updateField('system_prompt', originalPrompt);
      }
    } finally {
      setGeneratingPrompt(false);
      generateAbortRef.current = null;
    }
  }, [agentId, agent?.system_prompt, generatingPrompt, updateField]);

  const handleRestoreOriginalPrompt = useCallback(() => {
    if (preGeneratePrompt === null) return;
    updateField('system_prompt', preGeneratePrompt);
    setPreGeneratePrompt(null);
    setGenerateProviderUsed(null);
  }, [preGeneratePrompt, updateField]);

  // "Compose with AI" — streams a clinic-specific first greeting into the
  // First Message textarea using the agent's OWN selected LLM. Mirrors the
  // system-prompt generator: undo-able, never wipes on empty/error.
  const handleComposeFirstMessage = useCallback(async () => {
    if (composingFirst) return;
    const original = agent?.first_message ?? '';
    setComposeError(null);
    setComposeProviderUsed(null);
    setPreComposeFirst(original);
    setComposingFirst(true);

    const controller = new AbortController();
    composeAbortRef.current = controller;
    let receivedAny = false;

    try {
      const token = getToken();
      const response = await fetch(`${API_URL}/agents/${agentId}/generate-first-message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Request failed (${response.status})`);
      }

      updateField('first_message', '');
      let buffer = '';
      let liveText = '';
      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (!line.trim()) continue;
          let evt: any;
          try { evt = JSON.parse(line); } catch { continue; }
          if (evt.type === 'meta') {
            setComposeProviderUsed(evt.fallback_used ? `${evt.provider} (fallback)` : evt.provider);
          } else if (evt.type === 'chunk') {
            receivedAny = true;
            liveText += evt.text;
            updateField('first_message', liveText.replace(/^["']|["']$/g, ''));
          } else if (evt.type === 'error') {
            throw new Error(evt.message || 'Generation failed');
          }
        }
      }

      if (!receivedAny || !liveText.trim()) {
        updateField('first_message', original);
        throw new Error('The model returned an empty response. Please try again.');
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        setComposeError(err?.message || 'Compose failed. Please try again.');
        if (!receivedAny) updateField('first_message', original);
      }
    } finally {
      setComposingFirst(false);
      composeAbortRef.current = null;
    }
  }, [agentId, agent?.first_message, composingFirst, updateField]);

  const handleRestoreFirstMessage = useCallback(() => {
    if (preComposeFirst === null) return;
    updateField('first_message', preComposeFirst);
    setPreComposeFirst(null);
    setComposeProviderUsed(null);
  }, [preComposeFirst, updateField]);

  const saveAllManual = async () => {
    setSaveStatus('saving');
    try {
      // Convert arrays back to strings for the backend
      const payload = { ...agent };
      if (Array.isArray(payload.end_call_phrases)) {
        payload.end_call_phrases = JSON.stringify(payload.end_call_phrases);
      }
      if (typeof payload.clinic_info === 'object') {
        payload.clinic_info = JSON.stringify(payload.clinic_info);
      }
      if (Array.isArray(payload.transcriber_keywords)) {
         payload.transcriber_keywords = JSON.stringify(payload.transcriber_keywords);
      }
      if (Array.isArray(payload.tools_enabled)) {
         payload.tools_enabled = JSON.stringify(payload.tools_enabled);
      }
      
      await fetchWithAuth(`/agents/${agentId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload)
      });
      setSaveStatus('saved');
      // also fetch refreshed value
      fetchWithAuth(`/agents/${agentId}`).then(setAgent);
    } catch {
      setSaveStatus('error');
    }
  };

  // Stop playback and return the Play Sample control to a true idle state.
  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.onended = null;
      audioRef.current.ontimeupdate = null;
      audioRef.current.onloadedmetadata = null;
      audioRef.current.onerror = null;
      audioRef.current = null;
    }
    setSamplePlayer('idle');
    setSampleProgress(0);
    setSamplePosition(0);
  };

  // Button handler: toggle between playing and idle.
  //
  // The PRIMARY preview is the agent's real first message, so what an admin hears
  // is literally what a caller hears when the agent picks up — not a generic
  // demo sentence that happens to be in the right language. The generic sentence
  // is still reachable (second button) because it is the right tool for auditioning
  // a voice, and it is the only option when no greeting has been written yet.
  const toggleSamplePlayback = () => {
    if (samplePlayer === 'playing') {
      stopAudio();
    } else {
      playTTSPreview({ text: (agent?.first_message || '').trim() || undefined });
    }
  };

  const previewFirstMessage = Boolean((agent?.first_message || '').trim());

  // Does the first message appear to be WRITTEN in the configured language?
  //
  // Detected from the Unicode block the text actually uses, which is decisive for
  // Indian languages: each has its own block, so this is a fact about the string,
  // not a guess. Latin text is deliberately not flagged — an Indian clinic greeting
  // routinely mixes in Latin ("KMCT", "aster clinic kochi"), and English is a
  // legitimate greeting for any agent.
  const greetingScriptWarning = (() => {
    const text = (agent?.first_message || '').trim();
    const lang = agent?.language || '';
    if (!text || !lang) return null;

    const SCRIPTS: Record<string, { re: RegExp; langs: string[] }> = {
      Devanagari: { re: /[ऀ-ॿ]/, langs: ['hi-IN', 'mr-IN'] },
      Bengali:    { re: /[ঀ-৿]/, langs: ['bn-IN', 'as-IN'] },
      Gurmukhi:   { re: /[਀-੿]/, langs: ['pa-IN'] },
      Gujarati:   { re: /[઀-૿]/, langs: ['gu-IN'] },
      Odia:       { re: /[଀-୿]/, langs: ['od-IN', 'or-IN'] },
      Tamil:      { re: /[஀-௿]/, langs: ['ta-IN'] },
      Telugu:     { re: /[ఀ-౿]/, langs: ['te-IN'] },
      Kannada:    { re: /[ಀ-೿]/, langs: ['kn-IN'] },
      Malayalam:  { re: /[ഀ-ൿ]/, langs: ['ml-IN'] },
      Arabic:     { re: /[؀-ۿ]/, langs: ['ar-SA', 'ur-IN'] },
    };

    const found = Object.entries(SCRIPTS).filter(([, v]) => v.re.test(text));
    if (!found.length) return null;                       // Latin only — fine.
    if (found.some(([, v]) => v.langs.includes(lang))) return null;  // Agrees.

    const scriptNames = found.map(([name]) => name).join(' and ');
    return (
      `The first message is written in ${scriptNames} script, but this agent's ` +
      `language is ${languageLabel(lang)}. The voice will read it with ` +
      `${languageLabel(lang)} pronunciation, which will sound wrong. Press ` +
      `Play First Message to hear it, then either rewrite the greeting or change ` +
      `the language.`
    );
  })();

  const playTTSPreview = async (overrideParams?: { provider?: string; voice_id?: string; model?: string; language?: string; text?: string }) => {
    stopAudio();

    const prov = overrideParams?.provider || agent?.tts_provider || 'sarvam';
    const voice = overrideParams?.voice_id || agent?.tts_voice || 'meera';
    const mdl = overrideParams?.model || agent?.tts_model || '';
    const lang = overrideParams?.language || agent?.language || 'en-IN';
    // With no `text`, the backend picks a neutral sentence written in `lang`, so the
    // preview follows the Language dropdown. Callers that want to hear the agent's
    // OWN words pass the first message explicitly (see toggleSamplePlayback) —
    // deliberately as an override rather than as a silent default here, because the
    // chat-playback and voice-picker callers below want the neutral sentence.
    const txt = overrideParams?.text;

    setSampleError(null);
    setSamplePlayer('loading');

    try {
      const params = new URLSearchParams({
        provider: prov,
        voice_id: voice,
        language: lang,
        ...(txt ? { text: txt } : {}),
        pitch: String(agent?.tts_pitch ?? 0),
        pace: String(agent?.tts_pace ?? 1),
        loudness: String(agent?.tts_loudness ?? 1),
        input_preprocessing: String(agent?.tts_input_preprocessing !== 0 && agent?.tts_input_preprocessing !== false),
      });
      if (mdl) {
        params.append('model', mdl);
      }
      if (prov !== 'sarvam') {
        if (agent?.tts_stability != null) params.append('stability', String(agent.tts_stability));
        if (agent?.tts_clarity != null) params.append('similarity_boost', String(agent.tts_clarity));
        if (agent?.tts_style != null) params.append('style', String(agent.tts_style));
        if (agent?.tts_use_speaker_boost != null) params.append('use_speaker_boost', String(agent.tts_use_speaker_boost === 1 || agent.tts_use_speaker_boost === true));
        if (agent?.tts_speed != null) params.append('speed', String(agent.tts_speed));
      }

      // Any change to voice/model/language/pitch/pace/etc changes this key,
      // invalidating the cached sample as required.
      const cacheKey = params.toString();
      let audioUrl: string;
      if (sampleCacheRef.current && sampleCacheRef.current.key === cacheKey) {
        audioUrl = sampleCacheRef.current.url;
      } else {
        // Returns raw audio bytes (not JSON), so fetchWithAuth (which always parses
        // the response as JSON) can't be used here — attach the bearer token manually.
        const token = getToken();
        const res = await fetch(`${API_URL}/platform/tts/preview?${cacheKey}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) {
          let detail = `Preview failed (HTTP ${res.status})`;
          try {
            const body = await res.json();
            if (body?.detail) detail = String(body.detail);
          } catch { /* non-JSON error body */ }
          setSampleError(`${prettyProvider(prov)}: ${detail}`);
          setSamplePlayer('error');
          return;
        }
        const audioBlob = await res.blob();
        if (!audioBlob.size) {
          setSampleError(`${prettyProvider(prov)}: empty audio returned`);
          setSamplePlayer('error');
          return;
        }
        audioUrl = URL.createObjectURL(audioBlob);
        // Replace any prior cached sample (revoke its object URL to avoid a leak).
        if (sampleCacheRef.current) URL.revokeObjectURL(sampleCacheRef.current.url);
        sampleCacheRef.current = { key: cacheKey, url: audioUrl };
      }

      const audio = new Audio(audioUrl);
      audioRef.current = audio;
      audio.onloadedmetadata = () => {
        if (isFinite(audio.duration)) setSampleDuration(audio.duration);
      };
      audio.ontimeupdate = () => {
        if (audioRef.current !== audio) return;
        setSamplePosition(audio.currentTime);
        if (audio.duration > 0) setSampleProgress(audio.currentTime / audio.duration);
      };
      audio.onended = () => {
        if (audioRef.current === audio) audioRef.current = null;
        setSamplePlayer('idle');
        setSampleProgress(0);
        setSamplePosition(0);
      };
      audio.onerror = () => {
        if (audioRef.current === audio) audioRef.current = null;
        setSampleError(`${prettyProvider(prov)}: audio playback failed`);
        setSamplePlayer('error');
      };
      await audio.play();
      setSamplePlayer('playing');
    } catch (e: any) {
      console.error('Play preview failed', e);
      setSampleError(`${prettyProvider(prov)}: ${e?.message || 'preview failed'}`);
      setSamplePlayer('error');
    }
  };

  const playSTTPreview = async (provider: string, model: string) => {
    let ttsProvider = 'elevenlabs';
    let voiceId = '21m00Tcm4TlvDq8ikWAM'; // Rachel
    let text = `Speech-to-text model configured to ElevenLabs Scribe.`;
    
    if (provider === 'sarvam') {
      ttsProvider = 'sarvam';
      voiceId = 'meera';
      text = `Speech to text model configured to Sarvam Saaras.`;
    } else if (provider === 'deepgram') {
      ttsProvider = 'openai_tts';
      voiceId = 'alloy';
      text = `Speech to text model configured to Deepgram Nova.`;
    } else if (provider === 'whisper') {
      ttsProvider = 'openai_tts';
      voiceId = 'alloy';
      text = `Speech to text model configured to OpenAI Whisper.`;
    }
    
    await playTTSPreview({
      provider: ttsProvider,
      voice_id: voiceId,
      text: text,
      language: ttsProvider === 'sarvam' ? 'hi-IN' : 'en-US',
      model: ttsProvider === 'elevenlabs' ? 'eleven_flash_v2_5' : 'bulbul:v3'
    });
  };

  const sendTestChat = async () => {
    if(!chatIn.trim()) return;
    setChatLog(p => [...p, {from:'user', text: chatIn}]);
    const inputMsg = chatIn;
    setChatIn('');
    try {
      const data = await fetchWithAuth(`/agents/${agentId}/test`, {
        method: 'POST',
        body: JSON.stringify({ message: inputMsg })
      });
      setChatLog(p => [...p, {from:'agent', text: data.ai_response || 'Response received'}]);
    } catch(e) {
      setChatLog(p => [...p, {from:'agent', text: 'Error connecting to agent.'}]);
    }
  };

  if (loading) {
    return <div style={{ height: '100vh', background: BG, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading agent...</div>;
  }

  if (!agent) {
    return (
      <div style={{ height: '100vh', background: BG, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: '12px' }}>
        <div style={{ fontSize: '14px', color: 'rgba(255,255,255,0.75)' }}>{loadError || 'Agent not found'}</div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={() => navigate(-1)}
            style={{ padding: '8px 14px', borderRadius: '8px', background: 'rgba(255,255,255,0.08)', border: `1px solid ${BORDER}`, color: '#fff', cursor: 'pointer' }}
          >
            Back
          </button>
          <button
            // Wrapped, not passed directly: React would hand loadAgent the click
            // event as its `opts` argument.
            onClick={() => loadAgent()}
            style={{ padding: '8px 14px', borderRadius: '8px', background: ACCENT, border: 'none', color: '#000', fontWeight: 600, cursor: 'pointer' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ 
      display: 'flex', flexDirection: 'column', height: '100vh', background: BG, 
      backgroundImage: `radial-gradient(circle, rgba(255,255,255,0.035) 1px, transparent 1px)`,
      backgroundSize: '28px 28px'
    }}>
      {/* ── TOP BAR ───────────────────────────────────────────────────────────── */}
      <header style={{ 
        height: '64px', borderBottom: `1px solid ${BORDER}`, background: '#080808',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 24px',
        position: 'sticky', top: 0, zIndex: 10
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div onClick={() => navigate(-1)} style={{ cursor: 'pointer', padding: '8px', margin: '-8px', display: 'flex', alignItems: 'center' }}>
            <ChevronLeft size={20} color="#888" />
          </div>
          <div style={{ width: '40px', height: '40px', borderRadius: '50%', background: 'rgba(0,212,170,0.1)', border: `1px solid rgba(0,212,170,0.3)`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Headphones size={20} color={ACCENT} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <input 
                value={agent.agent_name} onChange={e => updateField('agent_name', e.target.value)}
                style={{ fontSize: '16px', fontWeight: 600, color: '#fff', background: 'transparent', border: 'none', outline: 'none', padding: 0, width: '200px' }}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'rgba(255,255,255,0.1)', padding: '2px 8px', borderRadius: '12px', fontSize: '11px', color: '#fff', fontWeight: 500 }}>
                <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: agent.status === 'ACTIVE' ? '#22C55E' : '#FBBF24' }} />
                {agent.status}
              </div>
            </div>
            <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)', marginTop: '2px' }}>
              {agent.clinic_name}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {saveStatus && (
            <div style={{ fontSize: '12px', color: saveStatus === 'error' ? '#F87171' : 'rgba(255,255,255,0.45)', display: 'flex', alignItems: 'center', gap: '6px' }}>
              {saveStatus === 'saving' && 'Saving...'}
              {saveStatus === 'saved' && <><CheckCircle2 size={12} /> Saved ✓</>}
              {saveStatus === 'error' && 'Save failed'}
            </div>
          )}
          <button 
            onClick={() => setShowTest(!showTest)}
            style={{ padding: '8px 16px', borderRadius: '8px', background: 'rgba(255,255,255,0.1)', color: '#fff', border: 'none', fontSize: '13px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            <Phone size={14} /> Test Agent
          </button>
          <button
            onClick={async () => {
              // Unpublishing takes a live receptionist offline for real patients —
              // confirm first (audit P5). Publishing needs no confirmation.
              if (agent.status === 'ACTIVE') {
                const ok = window.confirm(
                  `This will stop ${agent.agent_name || 'this agent'} from taking calls. Continue?`
                );
                if (!ok) return;
              }
              await saveAllManual();
              const newStatus = agent.status === 'ACTIVE' ? 'CONFIGURED' : 'ACTIVE';
              updateField('status', newStatus);
            }}
            style={{ padding: '8px 16px', borderRadius: '8px', background: ACCENT, color: '#000', border: 'none', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}
          >
            {agent.status === 'ACTIVE' ? 'Unpublish' : 'Publish'}
          </button>
        </div>
      </header>

      {/* ── VAPI-STYLE TAB BAR (scroll-spy) ─────────────────────────────────── */}
      <div style={{ borderBottom: `1px solid ${BORDER}`, background: '#080808', padding: '0 24px', display: 'flex', gap: '4px' }}>
        {AGENT_TABS.map(tab => {
          const isActive = activeTab === tab.id;
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => handleTabClick(tab.id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '10px 18px',
                borderRadius: '8px 8px 0 0',
                background: isActive ? '#0f0f0f' : 'transparent',
                border: isActive ? `1px solid ${BORDER}` : '1px solid transparent',
                borderBottom: isActive ? `1px solid #0f0f0f` : '1px solid transparent',
                marginBottom: isActive ? -1 : 0,
                color: isActive ? ACCENT : 'rgba(255,255,255,0.4)',
                fontSize: 13, fontWeight: isActive ? 600 : 500,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLElement).style.color = 'rgba(255,255,255,0.75)'; }}
              onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLElement).style.color = 'rgba(255,255,255,0.4)'; }}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* ── CONTENT BODY ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        
        {/* Scrollable Form — all sections rendered, scroll-spy updates tab */}
        <div ref={scrollContainerRef} style={{ flex: 1, overflowY: 'auto', padding: '32px 40px', display: 'flex', justifyContent: 'center' }}>
          <div style={{ width: '100%', maxWidth: '840px', paddingBottom: '120px' }}>
            
            {/* ══ ASSISTANT SECTION ════════════════════════════════════════════ */}
            <div ref={el => { sectionRefs.current.assistant = el; }} data-section="assistant">
            {/* 1. MODEL */}
            {/* The LLM PROVIDER dropdown stays removed — it is locked to Groq
                server-side (backend/services/agent_defaults.py) and was a real source
                of breakage: one live agent had llm_provider='groq' with
                llm_model='gemini-2.5-flash-8b', which Groq answers 404 for.

                The MODEL dropdown is back, because that pair could only be assembled
                when BOTH halves were free-form against client-side catalogues. The
                options here come from Groq's live API, and the backend re-checks the
                submitted model against Groq before storing it. */}
            <CollapsibleSection icon={Brain} title="Model" summary={agent.llm_model || DEFAULT_LLM_MODEL}>
              {/* Was a two-column grid: Provider/Model on the left, First Message
                  on the right. Kept as a single column — the Model dropdown reads
                  better full-width, since the option labels carry the context window
                  and the reasoning caveat. */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '24px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Label>LLM Model</Label>
                      <button
                        onClick={() => loadLlmModels({ refresh: true })}
                        disabled={refreshingModels}
                        title="Re-ask Groq for the current model list"
                        style={{
                          background: 'none', border: `1px solid ${BORDER}`, borderRadius: '12px',
                          padding: '4px 8px', fontSize: '11px', color: '#fff',
                          cursor: refreshingModels ? 'not-allowed' : 'pointer',
                          opacity: refreshingModels ? 0.6 : 1,
                          display: 'flex', alignItems: 'center', gap: '5px',
                        }}
                      >
                        <RefreshCw size={11} style={refreshingModels ? { animation: 'lifodial-spin 0.8s linear infinite' } : undefined} />
                        {refreshingModels ? 'Refreshing…' : 'Refresh'}
                      </button>
                    </div>
                    <Select
                      value={agent.llm_model || DEFAULT_LLM_MODEL}
                      // Saves immediately and re-reads the row, like the STT/TTS
                      // pickers: the backend may reject a model Groq no longer
                      // serves, and optimistic state would leave a value on screen
                      // that the row does not hold.
                      onChange={(v: any) => changeProviderOrModel({ llm_model: v })}
                      options={llmModelOptions}
                    />
                    {/* A reasoning model's visible chain-of-thought is spoken to the
                        caller — nothing in the pipeline strips it — so the caveat
                        belongs next to the choice, not buried in a doc. */}
                    {selectedLlmModel?.reasoning && (
                      <div style={{ marginTop: '6px', fontSize: '12px', color: '#e0a94a', display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} />
                        This is a reasoning model. Its thinking is not stripped from the
                        reply, so callers may hear it read aloud.
                      </div>
                    )}
                    {/* "Not measured" rather than "broken" — but worth saying, because
                        the one model that WAS measured badly failed by telling the
                        patient their appointment was booked without emitting the tag
                        that writes it. A silent booking failure is the worst outcome
                        this product has. */}
                    {selectedLlmModel && !selectedLlmModel.booking_verified && (
                      <div style={{ marginTop: '6px', fontSize: '12px', color: '#e0a94a', display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} />
                        This model has not been verified on appointment booking. Test a
                        booking end to end before relying on it — an unverified model can
                        confirm an appointment it never actually saved.
                      </div>
                    )}
                    {llmModelsError ? (
                      <div style={{ marginTop: '6px', fontSize: '12px', color: '#ff6b6b', display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                        <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} />
                        {llmModelsError} Only this agent's current model is shown until
                        the list can be fetched — it has not been changed.
                      </div>
                    ) : (
                      <Helper>
                        Groq is the provider for every agent on the platform. The model is
                        per-agent, and each one has its own daily token budget — moving an
                        agent to a different model gives it a separate allowance.
                      </Helper>
                    )}
                  </div>
                  <div>
                    <Label>First Message Mode</Label>
                    <div style={{ display: 'flex', gap: '12px', marginTop: '6px' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#fff', cursor: 'pointer' }}>
                        <input type="radio" checked={agent.first_message_mode === 'assistant-speaks-first'} onChange={() => updateField('first_message_mode', 'assistant-speaks-first')} style={{ accentColor: ACCENT }} />
                        Assistant speaks first
                      </label>
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#fff', cursor: 'pointer' }}>
                        <input type="radio" checked={agent.first_message_mode === 'wait'} onChange={() => updateField('first_message_mode', 'wait')} style={{ accentColor: ACCENT }} />
                        Wait for patient
                      </label>
                    </div>
                  </div>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Label>First Message</Label>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        {preComposeFirst !== null && !composingFirst && (
                          <span
                            onClick={handleRestoreFirstMessage}
                            style={{ fontSize: '11px', color: 'var(--text-muted, #888)', cursor: 'pointer', textDecoration: 'underline' }}
                            title="Restore the message from before AI compose"
                          >
                            ↺ Undo
                          </span>
                        )}
                        <PromptHistoryButton agentId={agentId!} field="first_message" onReverted={(v) => updateField('first_message', v)} />
                        <button
                          onClick={handleComposeFirstMessage}
                          disabled={composingFirst}
                          style={{ background: 'none', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '4px 8px', fontSize: '11px', color: '#fff', cursor: composingFirst ? 'not-allowed' : 'pointer', opacity: composingFirst ? 0.6 : 1, display: 'flex', alignItems: 'center', gap: '5px' }}
                        >
                          {composingFirst && <Loader2 size={11} style={{ animation: 'spin 0.8s linear infinite' }} />}
                          {composingFirst ? 'Composing…' : '✨ Compose with AI'}
                        </button>
                      </div>
                    </div>
                    <Textarea value={agent.first_message} onChange={(v:any) => updateField('first_message', v)} />
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Helper>{agent.first_message?.length || 0} characters</Helper>
                      {composeProviderUsed && !composingFirst && !composeError && (
                        <span style={{ fontSize: '11px', color: 'var(--text-muted, #888)' }}>Generated using {composeProviderUsed}</span>
                      )}
                    </div>
                    {composeError && (
                      <div style={{ fontSize: '12px', color: '#ff6b6b', marginTop: '4px' }}>{composeError}</div>
                    )}
                  </div>
                </div>
              </div>
              
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <Label>System Prompt</Label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {preGeneratePrompt !== null && !generatingPrompt && (
                      <span
                        onClick={handleRestoreOriginalPrompt}
                        style={{ fontSize: '11px', color: ACCENT, cursor: 'pointer', textDecoration: 'underline', marginRight: '2px' }}
                      >
                        Restore original
                      </span>
                    )}
                    <PromptHistoryButton agentId={agentId!} field="system_prompt" onReverted={(v) => updateField('system_prompt', v)} />
                    <button
                      onClick={handleGeneratePrompt}
                      disabled={generatingPrompt}
                      style={{
                        background: 'none', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '4px 8px',
                        fontSize: '11px', color: '#fff', cursor: generatingPrompt ? 'not-allowed' : 'pointer',
                        opacity: generatingPrompt ? 0.6 : 1, display: 'flex', alignItems: 'center', gap: '5px',
                      }}
                    >
                      {generatingPrompt && (
                        <span
                          style={{
                            width: '10px', height: '10px', borderRadius: '50%',
                            border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff',
                            display: 'inline-block', animation: 'lifodial-spin 0.7s linear infinite',
                          }}
                        />
                      )}
                      {generatingPrompt ? 'Generating…' : 'Generate with LLM'}
                    </button>
                  </div>
                </div>
                <style>{`@keyframes lifodial-spin { to { transform: rotate(360deg); } }`}</style>
                <Textarea value={agent.system_prompt} onChange={(v:any) => updateField('system_prompt', v)} rows={12} mono />
                {generateError && (
                  <div style={{ marginTop: '6px', fontSize: '12px', color: '#ff6b6b', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <AlertTriangle size={13} /> {generateError}
                  </div>
                )}
                {generateProviderUsed && !generatingPrompt && !generateError && (
                  <Helper>Generated using {generateProviderUsed}.</Helper>
                )}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '12px' }}>
                  {PROMPT_SNIPPETS.map(s => (
                    <div key={s.label} style={{ padding: '6px 12px', background: 'rgba(255,255,255,0.05)', border: `1px solid ${BORDER}`, borderRadius: '16px', fontSize: '12px', color: '#ccc', cursor: 'pointer' }}
                      title={s.block.trim().slice(0, 90) + '…'}
                      onClick={() => updateField('system_prompt', (agent.system_prompt || '').trimEnd() + '\n\n' + s.block)}>{s.label}</div>
                  ))}
                </div>
              </div>
              
              <div style={{ width: '50%' }}>
                <Label>Max Tokens</Label>
                <Input
                  type="number"
                  min={50}
                  max={2000}
                  value={agent.max_response_tokens}
                  onChange={(v: any) => {
                    const n = parseInt(v);
                    if (Number.isNaN(n)) return;
                    updateField('max_response_tokens', Math.min(2000, Math.max(50, n)));
                  }}
                />
                <Helper>Maximum response length per turn (50–2000)</Helper>
                {agent.max_response_tokens < 200 && (
                  <div style={{ marginTop: '6px', fontSize: '12px', color: '#ffb020', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <AlertTriangle size={13} /> Low token limit may cause truncated responses mid-sentence.
                  </div>
                )}
              </div>
            </CollapsibleSection>

            {/* 2. VOICE CONFIGURATION */}
            <CollapsibleSection icon={Mic} title="Voice & Language" summary={`${agent.tts_voice} · ${languageLabel(agent.language)}`}>
               <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.02)', padding: '16px', borderRadius: '12px', border: `1px solid ${BORDER}` }}>
                 <div>
                    <div style={{ fontSize: '11px', fontWeight: 700, color: ACCENT, letterSpacing: '0.05em', marginBottom: '4px' }}>SELECTED VOICE</div>
                    {/* Reads `agent.language` — THE one field. This header used to
                        render tts_language while the Voice field below rendered the
                        catalog's per-voice tag and the test widget rendered
                        stt_language, which is how one agent displayed Malayalam,
                        Hindi and Tamil simultaneously. */}
                    <div style={{ fontSize: '16px', fontWeight: 600, color: '#fff', display: 'flex', alignItems: 'center', gap: '8px' }}>
                       {agent.tts_voice} · {languageLabel(agent.language)}
                    </div>
                 </div>
                 <button
                   onClick={() => setShowVoiceModal(true)}
                   style={{
                      padding: '8px 16px', borderRadius: '8px', background: 'rgba(255,255,255,0.05)', color: '#fff', border: `1px solid ${BORDER}`,
                      fontSize: '13px', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px', transition: 'all 0.15s'
                   }}
                 >
                    🎙 Change Voice / Open Library
                 </button>
               </div>

              {/* Voice provider + model. Options come from the backend whitelist
                  (agent_defaults.SELECTABLE_TTS_PROVIDERS), not from the
                  aspirational /platform PROVIDERS catalogue these used to read —
                  that is what offered ElevenLabs and PlayHT, neither of which could
                  run a call. Today the whitelist has exactly one entry, so this
                  renders one option; that is the honest state of the platform, not
                  a placeholder. */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginTop: '8px' }}>
                <div>
                  <Label>Voice Provider</Label>
                  <Select
                    value={ttsProvider}
                    onChange={(v:any) => changeProviderOrModel({ tts_provider: v, tts_model: '' })}
                    options={(cfgOptions?.tts?.providers || []).map((p:any) => ({ value: p.id, label: p.name }))}
                  />
                  <Helper>Only providers with a working key and a live pipeline branch are listed.</Helper>
                </div>
                <div>
                  <Label>Voice Model</Label>
                  <Select
                    value={ttsModel}
                    onChange={(v:any) => changeProviderOrModel({ tts_model: v })}
                    options={cfgOptions?.tts?.models || [ttsModel]}
                  />
                  {/* bulbul:v2's speakers are a different roster from v3's, and an
                      unmatched (speaker, model) pair is a Sarvam 400 — an agent that
                      answers with silence. The backend repairs the voice on save
                      rather than letting that ship. */}
                  <Helper>Changing this may reset the voice, since each model has its own speakers.</Helper>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginTop: '8px' }}>
                <div>
                  <Label>Voice</Label>
                  {ttsVoices.length > 0 ? (
                    <Select
                       value={agent.tts_voice}
                       onChange={(v:any) => {
                         updateField('tts_voice', v);
                       }}
                       options={ttsVoices}
                    />
                  ) : (
                    <Input value={agent.tts_voice} onChange={(v:any) => {
                      updateField('tts_voice', v);
                    }} />
                  )}
                  {/* No per-voice language tag here any more — see the ttsVoices
                      effect. Sarvam's speakers are language-agnostic, so the old
                      "(hi-IN)" suffix was both a second language display AND
                      factually wrong: shruti speaks Malayalam perfectly. */}
                  <Helper>Every voice speaks the language selected below.</Helper>
                </div>
                <div>
                  <Label>Language</Label>
                  {/* THE one language field. Writes `language`; the backend derives
                      the STT and TTS values from it. */}
                  {ttsLanguages.length > 0 ? (
                    <Select
                      value={agent.language}
                      onChange={(v:any) => updateField('language', v)}
                      options={ttsLanguages}
                    />
                  ) : (
                    <Select
                      value={agent.language}
                      onChange={(v:any) => updateField('language', v)}
                      options={[agent.language || 'en-IN']}
                    />
                  )}
                  <Helper>Sets what the agent hears, speaks and replies in.</Helper>
                </div>
              </div>

              {/* The honesty requirement, rendered. A language whose transcriber
                  cannot hear it still WORKS — the pipeline swaps in Sarvam — but the
                  swap must not be silent, because "silently ran on a provider that
                  could not handle the configured language" is the failure this
                  whole change exists to end. Errors are red because they mean the
                  agent genuinely cannot function; warnings are amber because the
                  call works with a real trade-off. */}
              {(cfgOptions?.selected?.errors?.length || cfgOptions?.selected?.warnings?.length) ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {(cfgOptions.selected.errors || []).map((m: string) => (
                    <div key={m} style={{ fontSize: '12px', color: '#ff6b6b', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                      <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} /> <span>{m}</span>
                    </div>
                  ))}
                  {(cfgOptions.selected.warnings || []).map((m: string) => (
                    <div key={m} style={{ fontSize: '12px', color: '#f0b429', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                      <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} /> <span>{m}</span>
                    </div>
                  ))}
                </div>
              ) : null}

              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px 16px', background: 'rgba(255,255,255,0.03)', borderRadius: '12px', border: `1px solid ${BORDER}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                  <button
                    onClick={toggleSamplePlayback}
                    disabled={samplePlayer === 'loading'}
                    style={{ padding: '8px 16px', borderRadius: '8px', background: ACCENT, color: '#000', border: 'none', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, cursor: samplePlayer === 'loading' ? 'default' : 'pointer', opacity: samplePlayer === 'loading' ? 0.7 : 1, minWidth: '150px', justifyContent: 'center' }}
                  >
                    {samplePlayer === 'loading' ? (
                      <><Loader2 size={14} style={{ animation: 'spin 0.8s linear infinite' }} /> Synthesizing…</>
                    ) : samplePlayer === 'playing' ? (
                      <><Pause size={14} fill="#000" /> Stop</>
                    ) : previewFirstMessage ? (
                      <><Play size={14} fill="#000" /> Play First Message</>
                    ) : (
                      <><Play size={14} fill="#000" /> Play Sample</>
                    )}
                  </button>
                  <div style={{ flex: 1, height: '4px', background: 'rgba(255,255,255,0.1)', borderRadius: '2px', position: 'relative' }}>
                    <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', width: `${Math.round(sampleProgress * 100)}%`, background: ACCENT, borderRadius: '2px', transition: 'width 0.1s linear' }} />
                  </div>
                  <span style={{ fontSize: '12px', color: '#888', fontVariantNumeric: 'tabular-nums', minWidth: '72px', textAlign: 'right' }}>
                    {fmtTime(samplePosition)} / {fmtTime(sampleDuration)}
                  </span>
                </div>
                {samplePlayer === 'error' && sampleError && (
                  <span style={{ fontSize: '12px', color: '#ff6b6b' }}>{sampleError}</span>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                  {previewFirstMessage ? (
                    <>
                      <Helper>
                        {agent.first_message_mode === 'wait'
                          // Worth saying: this agent is set to stay silent until the
                          // caller speaks, so the greeting below is real but is NOT
                          // what a caller hears first. Playing it without that caveat
                          // would be a preview of something that never happens.
                          ? 'This is the agent’s first message — but the agent is set to wait for the caller to speak first, so a caller will not hear it as a greeting.'
                          : 'Exactly what a caller hears when the agent picks up.'}
                      </Helper>
                      <button
                        onClick={() => playTTSPreview()}
                        style={{ padding: '4px 10px', borderRadius: '6px', background: 'transparent', color: '#888', border: `1px solid ${BORDER}`, fontSize: '11px', cursor: 'pointer' }}
                      >
                        Voice sample instead
                      </button>
                    </>
                  ) : (
                    <Helper>
                      No first message written yet, so this plays a sample sentence in
                      the selected language. Write a first message to hear the real
                      greeting.
                    </Helper>
                  )}
                </div>

                {/* The greeting is free text and the language is a dropdown, so
                    nothing stops them disagreeing — and on the live database they
                    already did: the 'aster clnic kochi' agent was set to Kannada with
                    a Hindi greeting. Sarvam then renders Devanagari text with Kannada
                    settings, which is exactly the kind of "configured one thing, got
                    another" the single language field exists to end. Detected from the
                    text's own script rather than guessed, and only ever a warning —
                    an admin may have a real reason to greet in another language. */}
                {greetingScriptWarning && (
                  <div style={{ fontSize: '12px', color: '#f0b429', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                    <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: '1px' }} /> <span>{greetingScriptWarning}</span>
                  </div>
                )}
              </div>

            </CollapsibleSection>

            {/* 3. TRANSCRIBER (STT)
                Provider and Model are back — switching transcriber is the fallback
                story when one vendor degrades, and Sarvam AI is the correct choice
                for Malayalam/Punjabi/Odia, which Deepgram serves on no tier.

                What is NOT back, and must not come back, is this section's old
                LANGUAGE dropdown. That was the SECOND language field, and it is the
                one that made the kmct agent transcribe Tamil while speaking
                Malayalam. The transcriber language is derived from the single
                Language field in Voice & Language above; what remains here is only
                whether the transcriber PINS to it or lets the provider detect. */}
            <CollapsibleSection
              icon={Headphones}
              title="Transcriber"
              summary={`${(cfgOptions?.stt?.providers || []).find((p:any) => p.id === sttProvider)?.name || sttProvider} · ${sttModel}`}
            >
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div>
                  <Label>Provider</Label>
                  <Select
                    value={sttProvider}
                    onChange={(v:any) => changeProviderOrModel({ stt_provider: v, stt_model: '' })}
                    options={(cfgOptions?.stt?.providers || []).map((p:any) => ({ value: p.id, label: p.name }))}
                  />
                  <Helper>Only providers with a working key and a live pipeline branch are listed.</Helper>
                </div>
                <div>
                  <Label>Model</Label>
                  <Select
                    value={sttModel}
                    onChange={(v:any) => changeProviderOrModel({ stt_model: v })}
                    options={cfgOptions?.stt?.models || [sttModel]}
                  />
                  <Helper>
                    {sttProvider === 'sarvam'
                      ? 'saaras:v3 serves 23 languages; saarika:v2.5 serves 11.'
                      : 'nova-3 is the only Deepgram tier with Indic support.'}
                  </Helper>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div>
                  <Label>Language Detection</Label>
                  {/* The one language-adjacent knob that is NOT a second language
                      field: it decides whether the transcriber is pinned to the
                      agent's language or lets the provider detect. It predates this
                      change — no new knob was added. */}
                  <Select
                    value={agent.auto_detect_language ? 'auto' : 'pinned'}
                    onChange={(v:any) => updateField('auto_detect_language', v === 'auto')}
                    options={[
                      { value: 'pinned', label: `Pin to ${languageLabel(agent.language)}` },
                      { value: 'auto', label: 'Let the provider detect' },
                    ]}
                  />
                  <Helper>
                    Pinning is more accurate for a single-language clinic. Detection
                    helps when callers switch language mid-sentence.
                  </Helper>
                </div>
                <div>
                  <Label>Transcription Language</Label>
                  {/* Read-only on purpose. It is the derived mirror of the Language
                      field above, shown so an operator can SEE that the two agree
                      rather than having to trust it — the original complaint was
                      four values disagreeing, so the fix has to be visible. */}
                  <div style={{ padding: '10px 12px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: `1px solid ${BORDER}`, fontSize: '13px', color: '#888' }}>
                    {agent.auto_detect_language ? 'Auto-detect' : languageLabel(agent.language)}
                  </div>
                  <Helper>Derived from the Language field above — set it there.</Helper>
                </div>
              </div>
            </CollapsibleSection>

            {/* 4. CALL BEHAVIOR — still Assistant tab */}
            <CollapsibleSection icon={Settings} title="Call Behavior" summary={`Max ${agent.max_duration_seconds}s · ${agent.silence_timeout_seconds}s timeout`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div>
                  <Label>Silence Timeout (Seconds)</Label>
                  <Input type="number" value={agent.silence_timeout_seconds} onChange={(v:any) => updateField('silence_timeout_seconds', parseInt(v))} />
                  <Helper>Hang up if patient is silent for this long</Helper>
                </div>
                <div>
                  <Label>Maximum Call Duration (Seconds)</Label>
                  <Input type="number" value={agent.max_duration_seconds} onChange={(v:any) => updateField('max_duration_seconds', parseInt(v))} />
                  <Helper>Maximum length of any single call</Helper>
                </div>
                <div>
                  <Label>End Call Phrases</Label>
                  <TagInput tags={Array.isArray(agent.end_call_phrases) ? agent.end_call_phrases : (agent.end_call_phrases ? (typeof agent.end_call_phrases === 'string' ? JSON.parse(agent.end_call_phrases) : ['goodbye']) : ['goodbye', 'thank you, bye'])} onChange={(t:any) => updateField('end_call_phrases', JSON.stringify(t))} />
                  <Helper>If patient says these, end call.</Helper>
                </div>
                <div>
                  <Label>End Call Message</Label>
                  <Textarea value={agent.end_call_message ?? 'Thank you for calling. Goodbye!'} onChange={(v:any) => updateField('end_call_message', v)} rows={2} />
                </div>
              </div>
            </CollapsibleSection>

            </div>{/* end assistant section */}

          </div>
        </div>

      </div>

      {/* ── FOOTER MANUAL SAVE ────────────────────────────────────────────────── */}
      <div style={{ borderTop: `1px solid ${BORDER}`, background: '#0a0a0a', padding: '16px 24px', display: 'flex', justifyContent: 'flex-end', position: 'sticky', bottom: 0, zIndex: 10 }}>
        <button onClick={saveAllManual} style={{ padding: '10px 24px', borderRadius: '8px', background: ACCENT, color: '#000', border: 'none', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
          Save Changes
        </button>
      </div>

      {/* ── VOICE PICKER MODAL ────────────────────────────────────────────────── */}
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
                 <VoiceLibrary
                   isPickerModal
                   // Open on the AGENT's language, not unfiltered. Every Sarvam
                   // voice speaks every language, but each card shows one display
                   // tag — mostly hi-IN — so an unfiltered picker made a Malayalam
                   // agent look as though it had no Malayalam voices to choose
                   // from. Pre-filtering re-tags the cards to ml-IN and makes the
                   // previews speak Malayalam.
                   initialLanguage={agent?.language || agent?.tts_language || ''}
                   onSelectVoice={(voice) => {
                     const newVoiceId = voice.voice_id || voice.id || voice.name;
                     // ONLY the voice changes. This used to also write
                     // tts_provider, tts_model and — critically — tts_language
                     // from the voice's catalog tag, so picking a voice silently
                     // changed the agent's language to whatever that voice was
                     // labelled with. That is one of the writers that produced the
                     // four-way mismatch. Provider/model are locked; language is
                     // owned by the Language field alone.
                     updateFields({ tts_voice: newVoiceId });
                     setShowVoiceModal(false);
                     // Preview in the agent's OWN language, not the voice's tag —
                     // every Sarvam voice can speak it.
                     setTimeout(() => {
                       playTTSPreview({
                         voice_id: newVoiceId,
                         language: agent?.language,
                       });
                     }, 300);
                   }} 
                 />
              </div>
           </div>
        </div>
      )}

        {/* Local Suspense boundary: TestAgentModal is a lazy chunk (the LiveKit
            stack). The removed Simulation Testing section used to mount it on
            page load, so the chunk was always warm by the time this button was
            clicked. Without a boundary here, the first click would suspend up to
            the route-level fallback and blank the whole page while it loads. */}
        {showTest && (
          <Suspense fallback={null}>
            <TestAgentModal
              agent={{ ...agent, name: agent?.agent_name || agent?.name }}
              agentId={agentId}
              onClose={() => setShowTest(false)}
            />
          </Suspense>
        )}

    </div>
  );
}
