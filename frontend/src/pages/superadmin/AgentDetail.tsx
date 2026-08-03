import {
  Activity,
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
  Settings,
  X
} from 'lucide-react';
import React, { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import fetchWithAuth, { API_URL } from '../../api/client';
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

function getLlmFallbackModels(provider: string): string[] {
  const map: Record<string, string[]> = {
    gemini: ['gemini-2.5-flash', 'gemini-2.5-flash-8b', 'gemini-2.0-flash', 'gemini-1.5-pro'],
    openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'o3-mini'],
    anthropic: ['claude-sonnet-4-5', 'claude-haiku-4-5', 'claude-3-5-sonnet-20241022', 'claude-3-haiku-20240307'],
    groq: ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'gemma2-9b-it', 'deepseek-r1-distill-llama-70b', 'compound-beta-mini'],
    deepseek: ['deepseek-chat', 'deepseek-reasoner'],
    mistral: ['mistral-large-latest', 'mistral-small-latest', 'open-mistral-nemo'],
    cerebras: ['llama-3.3-70b', 'llama3.1-8b'],
  };
  return map[provider] || ['gemini-2.5-flash'];
}

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

  // Dynamic model lists
  const [llmModels, setLlmModels] = useState<string[]>([]);
  const [ttsModels, setTtsModels] = useState<string[]>([]);
  const [ttsVoices, setTtsVoices] = useState<any[]>([]);
  const [ttsLanguages, setTtsLanguages] = useState<{ value: string; label: string }[]>([]);
  const [sttModels, setSttModels] = useState<string[]>([]);

  // Phase B — providers that actually have a key configured. The provider
  // dropdowns show ONLY these (never a hardcoded catalog), and if the agent is
  // currently assigned to a provider that's no longer configured we warn rather
  // than silently keep a dead selection.
  const [configuredProviders, setConfiguredProviders] = useState<Record<string, { id: string; display_name: string }[]>>({});
  useEffect(() => {
    fetchWithAuth('/platform/configured-providers').then(setConfiguredProviders).catch(() => {});
  }, []);
  const configuredIds = (cat: string) => (configuredProviders[cat] || []).map(p => p.id);
  // Options = configured providers, plus the agent's current value if it's set
  // (so the current selection is always visible even when its key was removed).
  const providerOptions = (cat: string, current?: string) => {
    const ids = configuredIds(cat);
    return current && !ids.includes(current) ? [current, ...ids] : (ids.length ? ids : (current ? [current] : []));
  };
  const isDeadProvider = (cat: string, current?: string) =>
    !!current && Object.keys(configuredProviders).length > 0 && !configuredIds(cat).includes(current);

  const loadAgent = useCallback(async () => {
    setLoading(true);
    setLoadError(null);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);

    try {
      const data = await fetchWithAuth(`/agents/${agentId}`, { signal: controller.signal });
      if (!data || typeof data !== 'object') {
        throw new Error('Invalid agent payload');
      }

      setAgent(data);
    } catch (e: any) {
      console.error('Agent detail load failed:', e);
      setAgent(null);
      setLoadError('Unable to load this agent. Please try again.');
    } finally {
      clearTimeout(timeout);
      setLoading(false);
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

  // Fetch models when provider changes.
  // The model catalogue is a SUGGESTION list, not an allow-list: whatever is
  // already configured stays selected and stays selectable, so a model the
  // catalogue happens not to list is never silently replaced (and never
  // auto-saved over). Only an EMPTY model gets filled in. See the matching
  // comment on the STT effect below for the incident this prevents.
  const mergeCurrent = (current: string | undefined, catalogue: string[]) => {
    const c = (current || '').trim();
    return c && !catalogue.includes(c) ? [c, ...catalogue] : catalogue;
  };

  useEffect(() => {
    if (!agent?.llm_provider) return;
    const apply = (catalogue: string[]) => {
      setLlmModels(mergeCurrent(agent.llm_model, catalogue));
      if (!(agent.llm_model || '').trim() && catalogue.length) {
        updateField('llm_model', catalogue[0]);
      }
    };
    fetchWithAuth(`/platform/models/${agent.llm_provider}`)
      .then(d => apply(d.models?.length ? d.models : getLlmFallbackModels(agent.llm_provider)))
      .catch(() => apply(getLlmFallbackModels(agent.llm_provider)));
  }, [agent?.llm_provider]);

  useEffect(() => {
    if (!agent?.tts_provider) return;
    fetchWithAuth(`/platform/models/${agent.tts_provider}?category=tts`)
      .then(d => {
        const catalogue: string[] = d.models?.length ? d.models : [];
        setTtsModels(mergeCurrent(agent.tts_model, catalogue));
        if (!(agent.tts_model || '').trim() && catalogue.length) {
          updateField('tts_model', catalogue[0]);
        }
      })
      .catch(() => setTtsModels(agent.tts_model ? [agent.tts_model] : []));
  }, [agent?.tts_provider]);

  useEffect(() => {
    if (!agent?.tts_provider) return;
    // Pass model as a filter param so the dropdown only shows voices for the selected model
    const modelParam = agent.tts_model ? `?model=${encodeURIComponent(agent.tts_model)}` : '';
    fetchWithAuth(`/platform/tts/voices/${agent.tts_provider}${modelParam}`)
      .then(d => {
        if (d.voices && Array.isArray(d.voices)) {
          const mapped = d.voices.map((v: any) => ({
            value: v.voice_id || v.id || v.name,
            label: `${v.name} (${v.language || v.gender || 'Unknown'})`
          }));
          setTtsVoices(mapped);
        } else {
          setTtsVoices([]);
        }
        // Same payload, same list the Voice Library builds its filter from, so
        // a language can never be pickable in one place and missing in the other.
        const langs = Array.isArray(d.languages) ? d.languages : [];
        setTtsLanguages(langs.map((l: any) => ({
          value: String(l.code), label: `${l.name || l.code} (${l.code})`
        })));
      })
      .catch(() => { setTtsVoices([]); setTtsLanguages([]); });
  // Re-fetch any time provider OR model changes
  }, [agent?.tts_provider, agent?.tts_model]);

  useEffect(() => {
    if (!agent?.stt_provider) return;
    fetchWithAuth(`/platform/models/${agent.stt_provider}?category=stt`)
      .then(d => {
        const catalogue: string[] = d.models?.length ? d.models : [];
        const current = (agent.stt_model || '').trim();
        // The catalogue is a SUGGESTION list, not an allow-list. Keep whatever is
        // already configured as a selectable option so a model we don't happen to
        // list — a new Deepgram tier, a private Sarvam build — is never silently
        // replaced. This used to do
        //     if (!models.includes(agent.stt_model)) updateField('stt_model', models[0])
        // and auto-save, which is how simply selecting Deepgram overwrote a
        // working nova-3 config with nova-2 (nova-3 wasn't in the catalogue at
        // the time) and left the agent unable to hear Indian callers.
        setSttModels(mergeCurrent(current, catalogue));
        // Only fill in a model when there is genuinely none — never replace one.
        if (!current && catalogue.length) {
          updateField('stt_model', catalogue[0]);
        }
      })
      .catch(() => setSttModels(agent.stt_model ? [agent.stt_model] : []));
  }, [agent?.stt_provider]);

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
  const toggleSamplePlayback = () => {
    if (samplePlayer === 'playing') {
      stopAudio();
    } else {
      playTTSPreview();
    }
  };

  const playTTSPreview = async (overrideParams?: { provider?: string; voice_id?: string; model?: string; language?: string; text?: string }) => {
    stopAudio();

    const prov = overrideParams?.provider || agent?.tts_provider || 'sarvam';
    const voice = overrideParams?.voice_id || agent?.tts_voice || 'meera';
    const mdl = overrideParams?.model || agent?.tts_model || '';
    const lang = overrideParams?.language || agent?.tts_language || 'hi-IN';
    // Only an explicit override supplies text (the STT-provider announcements and
    // chat playback below). Otherwise the backend picks a sentence written in
    // `lang`, so Play Sample follows the Language dropdown beside it. It used to
    // fall back to the agent's own greeting, which ignored that dropdown
    // entirely — switching to Malayalam changed the language code and left the
    // words in Hindi/English.
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
            onClick={loadAgent}
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
            <CollapsibleSection icon={Brain} title="Model" summary={`${agent.llm_provider} · ${agent.llm_model}`}>
              {isDeadProvider('llm', agent.llm_provider) && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '12px 14px', marginBottom: '16px', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: '10px' }}>
                  <AlertTriangle size={16} color="#f59e0b" style={{ flexShrink: 0 }} />
                  <span style={{ fontSize: '13px', color: '#f59e0b' }}>
                    This agent uses <strong>{agent.llm_provider}</strong>, which is no longer configured in AI Platform. Add its key or pick a configured provider — calls will fall back until then.
                  </span>
                </div>
              )}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div>
                    <Label>Provider</Label>
                    <Select value={agent.llm_provider} onChange={(v:any) => updateField('llm_provider', v)} options={providerOptions('llm', agent.llm_provider)} />
                  </div>
                  <div>
                    <Label>Model</Label>
                    <Select value={agent.llm_model} onChange={(v:any) => updateField('llm_model', v)} options={llmModels.length ? llmModels : [agent.llm_model || 'gemini-2.5-flash']} />
                    <Helper>Models are auto-fetched from your API key. <span style={{color: ACCENT, cursor:'pointer', fontSize:'11px'}} onClick={() => { fetchWithAuth(`/platform/providers/${agent.llm_provider}/fetch-models`, {method:'POST'}).then(d=>{if(d.models?.length) setLlmModels(d.models)}); }}>⟳ Refresh Models</span></Helper>
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
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
            <CollapsibleSection icon={Mic} title="Voice Configuration" summary={`${agent.tts_provider} · ${agent.tts_voice} · ${agent.tts_language}`}>
               <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(255,255,255,0.02)', padding: '16px', borderRadius: '12px', border: `1px solid ${BORDER}` }}>
                 <div>
                    <div style={{ fontSize: '11px', fontWeight: 700, color: ACCENT, letterSpacing: '0.05em', marginBottom: '4px' }}>SELECTED VOICE</div>
                    <div style={{ fontSize: '16px', fontWeight: 600, color: '#fff', display: 'flex', alignItems: 'center', gap: '8px' }}>
                       {agent.tts_voice} · {agent.tts_language}
                    </div>
                    <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.45)', marginTop: '4px' }}>{agent.tts_provider} · {agent.tts_model}</div>
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

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginTop: '8px' }}>
                <div>
                  <Label>Provider</Label>
                  <Select value={agent.tts_provider} onChange={(v:any) => {
                    updateField('tts_provider', v);
                  }} options={[
                    { value: 'sarvam', label: 'Sarvam AI' },
                    { value: 'elevenlabs', label: 'ElevenLabs' },
                    { value: 'openai_tts', label: 'OpenAI TTS' }
                  ]} />
                </div>
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
                </div>
                <div>
                  <Label>Voice Model</Label>
                  <Select value={agent.tts_model} onChange={(v:any) => {
                    updateField('tts_model', v);
                  }} options={ttsModels.length ? ttsModels : [agent.tts_model || 'bulbul:v3']} />
                </div>
                <div>
                  <Label>Language</Label>
                  {ttsLanguages.length > 0 ? (
                    <Select
                      value={agent.tts_language}
                      onChange={(v:any) => updateField('tts_language', v)}
                      options={ttsLanguages}
                    />
                  ) : (
                    // Providers that publish no language catalogue (ElevenLabs,
                    // OpenAI TTS) keep the free-text field they had.
                    <Input value={agent.tts_language} onChange={(v:any) => updateField('tts_language', v)} />
                  )}
                </div>
              </div>

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
              </div>

            </CollapsibleSection>

            {/* 3. TRANSCRIBER (STT) — still in Assistant tab */}
            {/* 3. TRANSCRIBER (STT) */}
            <CollapsibleSection icon={Activity} title="Transcriber" summary={`${agent.stt_provider} · ${agent.stt_language}`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '24px' }}>
                <div><Label>Provider</Label><Select value={agent.stt_provider} onChange={(v:any) => {
                  updateField('stt_provider', v);
                }} options={[
                  { value: 'sarvam', label: 'Sarvam AI' },
                  { value: 'elevenlabs', label: 'ElevenLabs' },
                  { value: 'deepgram', label: 'Deepgram' },
                  { value: 'whisper', label: 'OpenAI Whisper' }
                ]} /></div>
                <div><Label>Model</Label><Select value={agent.stt_model} onChange={(v:any) => {
                  updateField('stt_model', v);
                }} options={sttModels.length ? sttModels : [agent.stt_model || 'saarika:v2']} /></div>
                <div><Label>Language</Label><Select value={agent.stt_language} onChange={(v:any) => updateField('stt_language', v)} options={['en-IN', 'hi-IN', 'ta-IN', 'te-IN', 'ar-SA', 'en-US', 'Multilingual (English/Hindi/Regional)', 'auto-detect']} /></div>
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
                   onSelectVoice={(voice) => {
                     const newVoiceId = voice.voice_id || voice.id || voice.name;
                     updateFields({
                       tts_provider: voice.provider,
                       tts_model: voice.model,
                       tts_voice: newVoiceId,
                       tts_language: voice.language
                     });
                     setShowVoiceModal(false);
                     setTimeout(() => {
                       playTTSPreview({
                         provider: voice.provider,
                         voice_id: newVoiceId,
                         model: voice.model,
                         language: voice.language
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
