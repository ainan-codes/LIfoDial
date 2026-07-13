/**
 * AIConfig.tsx — Production-Level AI Configuration
 *
 * Model lists are fetched LIVE from each provider's API via the backend.
 * No hardcoded model arrays anywhere. Users can also type any custom model ID.
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  AlertCircle, CheckCircle2, XCircle, Lock, ChevronDown,
  Activity, ChevronRight, Save, Mic, RefreshCw, Loader,
  Edit3, Check, ExternalLink,
} from 'lucide-react';
import fetchWithAuth from '../../api/client';
import VoiceBrowserModal from '../../components/VoiceBrowserModal';

// ── Types ─────────────────────────────────────────────────────────────────────

type TestStatus = 'idle' | 'testing' | 'ok' | 'fail';

interface ProviderMeta {
  id: string;
  name: string;
  icon: string;
  envKey: string;
  keyUrl: string;
  category: 'stt' | 'llm' | 'tts';
  defaultModel: string;
}

// ── Provider registry (meta only — no model lists, those are fetched live) ────

const PROVIDER_META: Record<string, ProviderMeta> = {
  // LLM
  gemini:    { id: 'gemini',    name: 'Google Gemini',    icon: 'G',  envKey: 'GEMINI_API_KEY',    keyUrl: 'https://aistudio.google.com/app/apikey',      category: 'llm', defaultModel: 'gemini-2.5-flash' },
  openai:    { id: 'openai',    name: 'OpenAI',           icon: 'O',  envKey: 'OPENAI_API_KEY',    keyUrl: 'https://platform.openai.com/api-keys',        category: 'llm', defaultModel: 'gpt-4o-mini' },
  anthropic: { id: 'anthropic', name: 'Anthropic Claude', icon: 'A',  envKey: 'ANTHROPIC_API_KEY', keyUrl: 'https://console.anthropic.com/settings/keys', category: 'llm', defaultModel: 'claude-3-5-haiku-20241022' },
  groq:      { id: 'groq',      name: 'Groq',             icon: 'Gq', envKey: 'GROQ_API_KEY',      keyUrl: 'https://console.groq.com/keys',               category: 'llm', defaultModel: 'llama-3.3-70b-versatile' },
  deepseek:  { id: 'deepseek',  name: 'DeepSeek',         icon: 'DS', envKey: 'DEEPSEEK_API_KEY',  keyUrl: 'https://platform.deepseek.com',               category: 'llm', defaultModel: 'deepseek-chat' },
  mistral:   { id: 'mistral',   name: 'Mistral AI',       icon: 'M',  envKey: 'MISTRAL_API_KEY',   keyUrl: 'https://console.mistral.ai',                  category: 'llm', defaultModel: 'mistral-large-latest' },
  // STT
  sarvam:    { id: 'sarvam',    name: 'Sarvam AI',        icon: 'S',  envKey: 'SARVAM_API_KEY',    keyUrl: 'https://dashboard.sarvam.ai',                 category: 'stt', defaultModel: 'saarika:v2.5' },
  deepgram:  { id: 'deepgram',  name: 'Deepgram',         icon: 'D',  envKey: 'DEEPGRAM_API_KEY',  keyUrl: 'https://console.deepgram.com',                category: 'stt', defaultModel: 'nova-2' },
  assemblyai:{ id: 'assemblyai',name: 'AssemblyAI',       icon: 'As', envKey: 'ASSEMBLYAI_API_KEY',keyUrl: 'https://www.assemblyai.com',                  category: 'stt', defaultModel: 'best' },
  elevenlabs:{ id: 'elevenlabs',name: 'ElevenLabs',       icon: 'El', envKey: 'ELEVENLABS_API_KEY',keyUrl: 'https://elevenlabs.io',                       category: 'tts', defaultModel: 'eleven_flash_v2_5' },
  openai_tts:{ id: 'openai_tts',name: 'OpenAI TTS',       icon: 'O',  envKey: 'OPENAI_API_KEY',    keyUrl: 'https://platform.openai.com/api-keys',        category: 'tts', defaultModel: 'tts-1' },
};

const STT_PROVIDERS = ['sarvam', 'deepgram', 'elevenlabs', 'assemblyai', 'openai'];
const LLM_PROVIDERS = ['gemini', 'openai', 'anthropic', 'groq', 'deepseek', 'mistral'];
const TTS_PROVIDERS = ['sarvam', 'elevenlabs', 'openai_tts'];

const LOCAL_KEY = 'lifodial_ai_config_v2';

// ── Helper: fetch models from backend ─────────────────────────────────────────

async function fetchModels(provider: string, category: string): Promise<string[]> {
  try {
    const data = await fetchWithAuth(`/platform/models/${provider}?category=${category}`);
    return data.models || [];
  } catch {
    return [];
  }
}

async function triggerFetchModels(provider: string): Promise<string[]> {
  try {
    const data = await fetchWithAuth(`/platform/providers/${provider}/fetch-models`, { method: 'POST' });
    return data.models || [];
  } catch {
    return [];
  }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{title}</h2>
      {description && <p style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>{description}</p>}
    </div>
  );
}

// ── Dynamic Model Selector ────────────────────────────────────────────────────

interface ModelSelectorProps {
  provider: string;
  category: string;
  value: string;
  onChange: (m: string) => void;
  disabled?: boolean;
}

function ModelSelector({ provider, category, value, onChange, disabled }: ModelSelectorProps) {
  const [models, setModels]       = useState<string[]>([]);
  const [loading, setLoading]     = useState(false);
  const [custom, setCustom]       = useState(false);
  const [inputVal, setInputVal]   = useState('');
  const hasFetched = useRef(false);

  const load = useCallback(async (force = false) => {
    if (!provider) return;
    setLoading(true);
    hasFetched.current = true;

    // Try cached first
    let result = await fetchModels(provider, category);

    // If empty or force, trigger a live fetch from provider API
    if (result.length === 0 || force) {
      result = await triggerFetchModels(provider);
      if (result.length === 0) {
        result = await fetchModels(provider, category); // re-read after fetch
      }
    }

    setModels(result);
    setLoading(false);

    // If current value is not in list and list is non-empty, auto-select first
    if (result.length > 0 && !result.includes(value)) {
      onChange(result[0]);
    }
  }, [provider, category]);

  useEffect(() => {
    hasFetched.current = false;
    setModels([]);
    setCustom(false);
    setInputVal('');
  }, [provider]);

  useEffect(() => {
    if (provider && !hasFetched.current) load();
  }, [provider, load]);

  // Check if value is not in fetched list → show as custom
  useEffect(() => {
    if (value && models.length > 0 && !models.includes(value)) {
      setCustom(true);
      setInputVal(value);
    }
  }, [value, models]);

  if (disabled) return null;

  const selectStyle: React.CSSProperties = {
    width: '100%', padding: '8px 10px', fontSize: 13, borderRadius: 8,
    backgroundColor: 'var(--bg-surface)', border: '1px solid var(--accent-border)',
    color: 'var(--text-primary)', outline: 'none', cursor: 'pointer',
    appearance: 'none', backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`,
    backgroundRepeat: 'no-repeat', backgroundPosition: 'right 10px center', paddingRight: 28,
  };

  return (
    <div style={{ marginTop: 10 }}>
      {/* Loading shimmer */}
      {loading && (
        <div style={{
          height: 36, borderRadius: 8, backgroundColor: 'var(--bg-surface-3)',
          animation: 'pulse 1.2s ease-in-out infinite', display: 'flex',
          alignItems: 'center', paddingLeft: 12, gap: 8,
        }}>
          <Loader size={12} style={{ animation: 'spin 1s linear infinite', color: 'var(--text-muted)' }} />
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Fetching models from API…</span>
        </div>
      )}

      {/* Dropdown or custom input */}
      {!loading && !custom && (
        <select value={value} onChange={e => onChange(e.target.value)} onClick={e => e.stopPropagation()} style={selectStyle}>
          {models.length === 0 && (
            <option value={value}>{value || PROVIDER_META[provider]?.defaultModel || 'Select model'}</option>
          )}
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      )}

      {!loading && custom && (
        <div style={{ position: 'relative' }}>
          <input
            type="text"
            value={inputVal}
            onChange={e => setInputVal(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { onChange(inputVal.trim()); setCustom(false); } }}
            placeholder="Type exact model ID, then press Enter"
            onClick={e => e.stopPropagation()}
            style={{
              width: '100%', padding: '8px 72px 8px 10px', fontSize: 13, borderRadius: 8,
              backgroundColor: 'var(--bg-surface)', border: '1px solid var(--accent)',
              color: 'var(--text-primary)', outline: 'none', boxSizing: 'border-box',
            }}
            autoFocus
          />
          <button
            onClick={e => { e.stopPropagation(); onChange(inputVal.trim()); setCustom(false); }}
            style={{
              position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)',
              padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
              backgroundColor: 'var(--accent)', border: 'none', color: '#000', cursor: 'pointer',
            }}
          >Set</button>
        </div>
      )}

      {/* Actions row */}
      {!loading && (
        <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
          {!custom && (
            <button
              onClick={e => { e.stopPropagation(); setCustom(true); setInputVal(value); }}
              style={{
                padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 500,
                backgroundColor: 'transparent', border: '1px solid var(--border)',
                color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
              }}
            >
              <Edit3 size={10} /> Custom model
            </button>
          )}
          {custom && models.length > 0 && (
            <button
              onClick={e => { e.stopPropagation(); setCustom(false); onChange(models[0]); }}
              style={{
                padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 500,
                backgroundColor: 'transparent', border: '1px solid var(--border)',
                color: 'var(--text-muted)', cursor: 'pointer',
              }}
            >
              ← Back to list
            </button>
          )}
          <button
            onClick={e => { e.stopPropagation(); load(true); }}
            style={{
              padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 500,
              backgroundColor: 'transparent', border: '1px solid var(--border)',
              color: 'var(--text-muted)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
            }}
          >
            <RefreshCw size={10} /> Refresh
          </button>
          {value && (
            <span style={{ padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
              backgroundColor: 'rgba(62,207,142,0.08)', border: '1px solid rgba(62,207,142,0.2)',
              color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <Check size={10} /> {value}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ── Provider Card ─────────────────────────────────────────────────────────────

interface ProviderCardProps {
  providerId: string;
  category: 'stt' | 'llm' | 'tts';
  isActive: boolean;
  hasKey: boolean;
  model: string;
  onActivate: () => void;
  onModelChange: (m: string) => void;
  // ElevenLabs voice browser
  selectedELVoice?: { voice_id: string; name: string } | null;
  onOpenVoiceBrowser?: () => void;
  onClearVoice?: () => void;
}

function ProviderCard({
  providerId, category, isActive, hasKey, model,
  onActivate, onModelChange,
  selectedELVoice, onOpenVoiceBrowser, onClearVoice,
}: ProviderCardProps) {
  const meta = PROVIDER_META[providerId];
  const name = meta?.name ?? providerId;
  const keyUrl = meta?.keyUrl ?? '';

  return (
    <div
      onClick={() => hasKey && onActivate()}
      style={{
        padding: '14px 16px', borderRadius: 12, cursor: hasKey ? 'pointer' : 'not-allowed',
        backgroundColor: isActive ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
        border: `${isActive ? 2 : 1}px solid ${isActive ? 'var(--accent)' : 'var(--border)'}`,
        opacity: hasKey ? 1 : 0.55,
        transition: 'all 0.15s',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 6, display: 'flex', alignItems: 'center',
            justifyContent: 'center', fontSize: 10, fontWeight: 800,
            backgroundColor: isActive ? 'var(--accent)' : 'var(--bg-surface-3)',
            color: isActive ? '#000' : 'var(--text-muted)',
          }}>
            {meta?.icon ?? name.slice(0, 2).toUpperCase()}
          </div>
          <span style={{ fontSize: 14, fontWeight: 600, color: isActive ? 'var(--accent)' : 'var(--text-primary)' }}>
            {name}
          </span>
        </div>
        {!hasKey && (
          <a
            href={keyUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={e => e.stopPropagation()}
            style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: 'var(--accent)', textDecoration: 'none' }}
          >
            <Lock size={11} /> Get key <ExternalLink size={10} />
          </a>
        )}
      </div>

      {isActive && (
        <div onClick={e => e.stopPropagation()} style={{ marginTop: 10 }}>
          <ModelSelector
            provider={providerId}
            category={category}
            value={model}
            onChange={onModelChange}
          />

          {/* ElevenLabs voice browser */}
          {providerId === 'elevenlabs' && category === 'tts' && (
            <div style={{ marginTop: 8 }}>
              {selectedELVoice && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '6px 10px', borderRadius: 6, marginBottom: 6,
                  backgroundColor: 'rgba(62,207,142,0.08)',
                  border: '1px solid rgba(62,207,142,0.25)',
                }}>
                  <Mic size={12} color="var(--accent)" />
                  <span style={{ fontSize: 12, color: 'var(--accent)', fontWeight: 600 }}>
                    {selectedELVoice.name}
                  </span>
                  <button
                    onClick={onClearVoice}
                    style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 12 }}
                  >✕</button>
                </div>
              )}
              <button
                onClick={onOpenVoiceBrowser}
                style={{
                  width: '100%', padding: '8px 12px', borderRadius: 8,
                  fontSize: 12, fontWeight: 600,
                  backgroundColor: 'rgba(62,207,142,0.1)',
                  border: '1px solid rgba(62,207,142,0.3)',
                  color: 'var(--accent)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                }}
              >
                <Mic size={13} />
                {selectedELVoice ? 'Change Voice' : 'Browse Voices'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── API Key Row ───────────────────────────────────────────────────────────────

interface KeyRowProps {
  id: string;
  name: string;
  keyUrl: string;
  value: string;
  onChange: (v: string) => void;
  status: TestStatus;
  onTest: () => void;
}

function KeyRow({ id, name, keyUrl, value, onChange, status, onTest }: KeyRowProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div style={{
      padding: '14px 16px', borderRadius: 12,
      backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{name}</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {status === 'ok'      && <span style={{ color: 'var(--accent)',       fontSize: 12, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 3 }}><CheckCircle2 size={13}/> Connected</span>}
          {status === 'fail'    && <span style={{ color: 'var(--destructive)',  fontSize: 12, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 3 }}><XCircle size={13}/> Invalid</span>}
          {status === 'testing' && <span style={{ color: 'var(--text-muted)',   fontSize: 12, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 3 }}><Loader size={13} style={{ animation: 'spin 1s linear infinite' }}/> Testing…</span>}
          {!value && status === 'idle' && (
            <a href={keyUrl} target="_blank" rel="noopener noreferrer" style={{ fontSize: 11, color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: 3, textDecoration: 'none' }}>
              Get key <ExternalLink size={10}/>
            </a>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <input
            type={visible ? 'text' : 'password'}
            placeholder="Paste API key here…"
            value={value}
            onChange={e => onChange(e.target.value)}
            style={{
              width: '100%', padding: '9px 38px 9px 12px', fontSize: 13, borderRadius: 8,
              backgroundColor: 'var(--bg-surface-3)', border: '1px solid var(--border)',
              color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace",
              outline: 'none', boxSizing: 'border-box',
            }}
          />
          {value && (
            <button
              onClick={() => setVisible(v => !v)}
              style={{
                position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 11,
              }}
            >
              {visible ? '🙈' : '👁'}
            </button>
          )}
        </div>
        <button
          onClick={onTest}
          disabled={!value || status === 'testing'}
          style={{
            padding: '9px 16px', borderRadius: 8, fontSize: 12, fontWeight: 500,
            backgroundColor: status === 'ok' ? 'var(--accent-dim)' : 'var(--bg-surface)',
            border: '1px solid var(--border)',
            color: status === 'ok' ? 'var(--accent)' : 'var(--text-secondary)',
            cursor: value ? 'pointer' : 'not-allowed', whiteSpace: 'nowrap',
          }}
        >
          {status === 'testing' ? 'Testing…' : 'Test'}
        </button>
        {value && (
          <button
            onClick={() => onChange('')}
            style={{ padding: '9px 12px', borderRadius: 8, fontSize: 12, backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)', cursor: 'pointer' }}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

// ── Main AIConfig ─────────────────────────────────────────────────────────────

export default function AIConfig() {
  // API keys
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [keyStatus, setKeyStatus] = useState<Record<string, TestStatus>>({});

  // Provider selections
  const [sttProvider, setSttProvider] = useState('sarvam');
  const [sttModel,    setSttModel]    = useState('saarika:v2.5');
  const [llmProvider, setLlmProvider] = useState('gemini');
  const [llmModel,    setLlmModel]    = useState('gemini-2.5-flash');
  const [ttsProvider, setTtsProvider] = useState('sarvam');
  const [ttsModel,    setTtsModel]    = useState('bulbul:v3');

  // ElevenLabs voice
  const [voiceBrowserOpen, setVoiceBrowserOpen] = useState(false);
  const [selectedELVoice, setSelectedELVoice]   = useState<{ voice_id: string; name: string } | null>(null);

  // Advanced
  const [expanded, setExpanded]           = useState(false);
  const [advVad, setAdvVad]               = useState('300');
  const [advMinSpeech, setAdvMinSpeech]   = useState('100');
  const [advBackchannel, setAdvBackchannel] = useState(true);
  const [advInterrupt, setAdvInterrupt]   = useState(true);
  const [advTemp, setAdvTemp]             = useState('0.3');
  const [advTokens, setAdvTokens]         = useState('150');

  const [saved, setSaved]   = useState(false);
  const [saving, setSaving] = useState(false);

  // ── Load from localStorage ─────────────────────────────────────────────────
  useEffect(() => {
    try {
      const s = localStorage.getItem(LOCAL_KEY);
      if (!s) return;
      const p = JSON.parse(s);
      if (p.keys)        setKeys(p.keys);
      if (p.sttProvider) setSttProvider(p.sttProvider);
      if (p.sttModel)    setSttModel(p.sttModel);
      if (p.llmProvider) setLlmProvider(p.llmProvider);
      if (p.llmModel)    setLlmModel(p.llmModel);
      if (p.ttsProvider) setTtsProvider(p.ttsProvider);
      if (p.ttsModel)    setTtsModel(p.ttsModel);
      if (p.elVoice)     setSelectedELVoice(p.elVoice);
      if (p.advVad)      setAdvVad(p.advVad);
      if (p.advMinSpeech)setAdvMinSpeech(p.advMinSpeech);
      if (p.advTemp)     setAdvTemp(p.advTemp);
      if (p.advTokens)   setAdvTokens(p.advTokens);
      if (p.advBackchannel !== undefined) setAdvBackchannel(p.advBackchannel);
      if (p.advInterrupt  !== undefined)  setAdvInterrupt(p.advInterrupt);
    } catch { /* ignore */ }
  }, []);

  // ── Test a key ─────────────────────────────────────────────────────────────
  const handleTest = async (providerId: string) => {
    const key = keys[providerId];
    if (!key) return;
    setKeyStatus(p => ({ ...p, [providerId]: 'testing' }));
    try {
      await fetchWithAuth(`/platform/providers/${providerId}/fetch-models`, {
        method: 'POST',
        body: JSON.stringify({ api_key: key }),
      });
      setKeyStatus(p => ({ ...p, [providerId]: 'ok' }));
    } catch {
      setKeyStatus(p => ({ ...p, [providerId]: 'fail' }));
    }
  };

  // ── Save ──────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    setSaving(true);
    localStorage.setItem(LOCAL_KEY, JSON.stringify({
      keys, sttProvider, sttModel, llmProvider, llmModel, ttsProvider, ttsModel,
      elVoice: selectedELVoice,
      advVad, advMinSpeech, advBackchannel, advInterrupt, advTemp, advTokens,
    }));

    // Push keys to backend
    const keyPushes = Object.entries(keys).filter(([, v]) => v.trim()).map(async ([pid, val]) => {
      const meta = PROVIDER_META[pid];
      if (!meta) return;
      await fetchWithAuth('/platform/keys', {
        method: 'POST',
        body: JSON.stringify({ provider: pid, category: meta.category, api_key: val, is_active: true }),
      }).catch(() => {});
    });
    await Promise.allSettled(keyPushes);

    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  // ── hasKey helper ─────────────────────────────────────────────────────────
  const hasKey = (pid: string) => {
    // sarvam and gemini default to configured env keys on the backend
    if (pid === 'sarvam' || pid === 'gemini') return true;
    return (keys[pid] ?? '').length > 8;
  };

  // ── Key rows to render ────────────────────────────────────────────────────
  const KEY_ROWS = [
    { id: 'google',     name: 'Google Gemini API Key',    keyUrl: PROVIDER_META.gemini.keyUrl },
    { id: 'openai',     name: 'OpenAI API Key',           keyUrl: PROVIDER_META.openai.keyUrl },
    { id: 'anthropic',  name: 'Anthropic Claude API Key', keyUrl: PROVIDER_META.anthropic.keyUrl },
    { id: 'groq',       name: 'Groq API Key',             keyUrl: PROVIDER_META.groq.keyUrl },
    { id: 'deepseek',   name: 'DeepSeek API Key',         keyUrl: PROVIDER_META.deepseek.keyUrl },
    { id: 'mistral',    name: 'Mistral AI Key',           keyUrl: PROVIDER_META.mistral.keyUrl },
    { id: 'sarvam',     name: 'Sarvam AI API Key',        keyUrl: PROVIDER_META.sarvam.keyUrl },
    { id: 'elevenlabs', name: 'ElevenLabs API Key',       keyUrl: PROVIDER_META.elevenlabs.keyUrl },
    { id: 'deepgram',   name: 'Deepgram API Key',         keyUrl: PROVIDER_META.deepgram.keyUrl },
    { id: 'assemblyai', name: 'AssemblyAI API Key',       keyUrl: PROVIDER_META.assemblyai.keyUrl },
    { id: 'livekit',    name: 'LiveKit API Key',          keyUrl: 'https://cloud.livekit.io' },
  ];

  const sttActive  = PROVIDER_META[sttProvider];
  const llmActive  = PROVIDER_META[llmProvider];
  const ttsActive  = PROVIDER_META[ttsProvider];

  return (
    <div style={{ paddingBottom: 40 }}>

      {/* ── SECTION 1: API KEYS ─────────────────────────────────────────── */}
      <section style={{ marginBottom: 40 }}>
        <SectionHeader title="API Keys" description="Keys are stored in the browser and synced to the backend on Save. Never shared with third parties." />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {KEY_ROWS.map(row => (
            <KeyRow
              key={row.id}
              id={row.id}
              name={row.name}
              keyUrl={row.keyUrl}
              value={keys[row.id] ?? ''}
              onChange={v => { setKeys(p => ({ ...p, [row.id]: v })); setKeyStatus(p => ({ ...p, [row.id]: 'idle' })); }}
              status={keyStatus[row.id] ?? 'idle'}
              onTest={() => handleTest(row.id)}
            />
          ))}
        </div>
      </section>

      {/* ── SECTION 2: PROVIDER + MODEL SELECTION ──────────────────────── */}
      <section style={{ marginBottom: 40 }}>
        <SectionHeader title="Voice Pipeline Configuration" description="Select a provider for each stage. Models are fetched live from the provider API — you can also type any custom model ID." />

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 20 }}>

          {/* STT */}
          <div>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>
              1 · Speech to Text
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {STT_PROVIDERS.map(pid => (
                <ProviderCard
                  key={pid}
                  providerId={pid}
                  category="stt"
                  isActive={sttProvider === pid}
                  hasKey={hasKey(pid)}
                  model={sttModel}
                  onActivate={() => { setSttProvider(pid); setSttModel(PROVIDER_META[pid]?.defaultModel ?? ''); }}
                  onModelChange={setSttModel}
                />
              ))}
            </div>
          </div>

          {/* LLM */}
          <div>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>
              2 · Intelligence (LLM)
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {LLM_PROVIDERS.map(pid => (
                <ProviderCard
                  key={pid}
                  providerId={pid}
                  category="llm"
                  isActive={llmProvider === pid}
                  hasKey={hasKey(pid)}
                  model={llmModel}
                  onActivate={() => { setLlmProvider(pid); setLlmModel(PROVIDER_META[pid]?.defaultModel ?? ''); }}
                  onModelChange={setLlmModel}
                />
              ))}
            </div>
          </div>

          {/* TTS */}
          <div>
            <h3 style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 12 }}>
              3 · Text to Speech
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {TTS_PROVIDERS.map(pid => (
                <ProviderCard
                  key={pid}
                  providerId={pid}
                  category="tts"
                  isActive={ttsProvider === pid}
                  hasKey={hasKey(pid)}
                  model={ttsModel}
                  onActivate={() => { setTtsProvider(pid); setTtsModel(PROVIDER_META[pid]?.defaultModel ?? ''); }}
                  onModelChange={setTtsModel}
                  selectedELVoice={ttsProvider === 'elevenlabs' ? selectedELVoice : null}
                  onOpenVoiceBrowser={() => setVoiceBrowserOpen(true)}
                  onClearVoice={() => setSelectedELVoice(null)}
                />
              ))}
            </div>
          </div>

        </div>
      </section>

      {/* ── SECTION 3: LIVE PIPELINE DIAGRAM ───────────────────────────── */}
      <section style={{ marginBottom: 40 }}>
        <SectionHeader title="Active Pipeline" description="Current streaming architecture — updates as you change providers." />
        <div style={{ borderRadius: 12, padding: '24px 20px', backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)', overflowX: 'auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', minWidth: 600 }}>
            {[
              { label: 'Caller', sub: 'Audio In', accent: false },
              null,
              { label: sttActive?.name ?? 'STT', sub: sttModel || '—', accent: true },
              null,
              { label: llmActive?.name ?? 'LLM', sub: llmModel || '—', accent: true },
              null,
              { label: ttsActive?.name ?? 'TTS', sub: ttsModel || '—', accent: true },
            ].map((item, i) => item === null ? (
              <ChevronRight key={i} size={20} color="var(--text-muted)" style={{ flexShrink: 0 }} />
            ) : (
              <div key={i} style={{
                padding: '14px 16px', borderRadius: 10, textAlign: 'center',
                minWidth: 130, flexShrink: 0, position: 'relative',
                backgroundColor: item.accent ? 'var(--accent-dim)' : 'var(--bg-surface)',
                border: `2px solid ${item.accent ? 'var(--accent)' : 'var(--border)'}`,
              }}>
                {item.accent && <div style={{ position: 'absolute', top: -5, right: -5, width: 10, height: 10, borderRadius: '50%', backgroundColor: 'var(--accent)' }} />}
                <p style={{ fontSize: 12, color: 'var(--text-muted)', margin: '0 0 4px' }}>{item.label}</p>
                <p style={{ fontSize: 13, fontWeight: 700, color: item.accent ? 'var(--accent)' : 'var(--text-primary)', margin: 0, wordBreak: 'break-all' }}>{item.sub}</p>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', marginTop: 16 }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 18px', borderRadius: 999, backgroundColor: 'var(--accent-dim)', border: '1px solid var(--accent)' }}>
              <Activity size={13} color="var(--accent)" />
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>Target latency: ~800ms</span>
            </div>
          </div>
        </div>
      </section>

      {/* ── SECTION 4: ADVANCED ────────────────────────────────────────── */}
      <section style={{ marginBottom: 40 }}>
        <button
          onClick={() => setExpanded(e => !e)}
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '8px 0', display: 'flex', alignItems: 'center', gap: 8 }}
        >
          <ChevronDown size={16} color="var(--text-secondary)" style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
          <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-primary)' }}>Advanced Voice Settings</span>
        </button>

        {expanded && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px 32px', marginTop: 20, padding: '20px', borderRadius: 12, backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)' }}>
            {[
              { label: `VAD Silence Threshold (${advVad}ms)`, min: 100, max: 1000, step: 50, value: advVad, onChange: setAdvVad },
              { label: `LLM Temperature (${advTemp})`,         min: 0,   max: 1,    step: 0.05, value: advTemp, onChange: setAdvTemp },
              { label: `Min Speech Duration (${advMinSpeech}ms)`, min: 50, max: 500, step: 50, value: advMinSpeech, onChange: setAdvMinSpeech },
              { label: `Max Response Tokens (${advTokens})`,   min: 50,  max: 500,  step: 10, value: advTokens, onChange: setAdvTokens },
            ].map(({ label, min, max, step, value, onChange }) => (
              <div key={label}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>{label}</label>
                <input type="range" min={min} max={max} step={step} value={value}
                  onChange={e => onChange(e.target.value)}
                  style={{ width: '100%', accentColor: 'var(--accent)' }} />
              </div>
            ))}
            {[
              { label: 'Enable Backchannels (hmm, sure)', value: advBackchannel, onChange: setAdvBackchannel },
              { label: 'Allow Patient Interruption (Barge-in)', value: advInterrupt, onChange: setAdvInterrupt },
            ].map(({ label, value, onChange }) => (
              <div key={label} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gridColumn: 'span 1' }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{label}</span>
                <input type="checkbox" checked={value} onChange={e => onChange(e.target.checked)} style={{ width: 18, height: 18, accentColor: 'var(--accent)' }} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── SAVE ─────────────────────────────────────────────────────────── */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 24 }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{
            width: '100%', padding: 16, borderRadius: 12, fontSize: 15, fontWeight: 700,
            color: '#000', backgroundColor: saved ? 'var(--accent-hover)' : 'var(--accent)',
            border: 'none', cursor: saving ? 'wait' : 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10,
            transition: 'all 0.2s',
          }}
        >
          {saving ? <><Loader size={18} style={{ animation: 'spin 1s linear infinite' }} /> Saving…</>
          : saved  ? <><CheckCircle2 size={18} /> Configuration Saved</>
          :           <><Save size={18} /> Save AI Configuration</>}
        </button>
        {saved && (
          <p style={{ textAlign: 'center', color: 'var(--accent)', fontSize: 13, marginTop: 10, fontWeight: 500 }}>
            ✅ Configuration saved. Takes effect on next call.
          </p>
        )}
      </div>

      {/* Voice Browser Modal */}
      <VoiceBrowserModal
        open={voiceBrowserOpen}
        onClose={() => setVoiceBrowserOpen(false)}
        onSelect={v => setSelectedELVoice(v)}
        selectedVoiceId={selectedELVoice?.voice_id}
      />

      {/* Keyframe CSS */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%,100% { opacity:.5; } 50% { opacity:.9; } }
      `}</style>
    </div>
  );
}
