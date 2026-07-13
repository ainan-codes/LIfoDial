import {
  Activity,
  AlertTriangle,
  BookOpen,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Globe,
  Headphones,
  History,
  LineChart,
  Loader2,
  Mic,
  Pause,
  Phone,
  Play,
  Send,
  Settings,
  Sliders,
  Upload,
  Voicemail,
  Wrench,
  X
} from 'lucide-react';
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import fetchWithAuth, { API_URL } from '../../api/client';
import { getToken } from '../../api/auth';
import TestAgentModal from '../../components/TestAgentModal';
import { FIXTURE_AGENTS } from '../../fixtures/data';
import VoiceLibrary from './VoiceLibrary';
import SimulationTab from './agent_detail/SimulationTab';
import AgentHealthTab from './agent_detail/AgentHealthTab';

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

// ── Embed Section Component ──────────────────────────────────────────────────

function EmbedSection({ agent, agentId, updateField }: { agent: any; agentId: string; updateField: (k: string, v: any) => void }) {
  const [stats, setStats] = React.useState<any>(null);
  const [copied, setCopied] = React.useState(false);
  const [showGuide, setShowGuide] = React.useState(false);
  const [guidePlatform, setGuidePlatform] = React.useState('wordpress');
  const [avatarState, setAvatarState] = React.useState<'idle' | 'uploading' | 'error'>('idle');
  const [avatarError, setAvatarError] = React.useState('');
  const avatarInputRef = React.useRef<HTMLInputElement>(null);

  const handleAvatarPick = async (fileList: FileList | null) => {
    const f = fileList?.[0];
    if (!f) return;
    setAvatarError('');
    // Client-side guard mirrors the server-side validation (PNG/JPG/WebP, ≤8MB).
    if (!['image/png', 'image/jpeg', 'image/jpg', 'image/webp'].includes(f.type)) {
      setAvatarState('error'); setAvatarError('Use a PNG, JPG, or WebP image.'); return;
    }
    if (f.size > 8 * 1024 * 1024) {
      setAvatarState('error'); setAvatarError('Image must be 8MB or smaller.'); return;
    }
    setAvatarState('uploading');
    try {
      const fd = new FormData();
      fd.append('file', f);
      // Bare fetch (not fetchWithAuth) so we don't force a JSON Content-Type on multipart.
      const token = localStorage.getItem('lifodial-token');
      const res = await fetch(`${API_URL}/agents/${agentId}/avatar`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `Upload failed (${res.status})`); }
      const data = await res.json();
      updateField('avatar_url', data.avatar_url); // sync local state + persist
      setAvatarState('idle');
    } catch (e: any) {
      setAvatarState('error'); setAvatarError(e?.message || 'Upload failed');
    }
  };

  const handleAvatarRemove = async () => {
    setAvatarState('uploading');
    try {
      const token = localStorage.getItem('lifodial-token');
      await fetch(`${API_URL}/agents/${agentId}/avatar`, { method: 'DELETE', headers: token ? { Authorization: `Bearer ${token}` } : {} });
      updateField('avatar_url', null);
      setAvatarState('idle');
    } catch {
      setAvatarState('idle');
    }
  };

  // Derive API base from current browser origin for dynamic embed code generation.
  // In production the admin dashboard is served from the same domain as the API.
  // When developing locally (vite devserver on 5173 → backend on 8001), fall back.
  const apiBase = API_URL;

  const position   = agent.embed_position    || 'bottom-right';
  const theme      = agent.embed_theme       || 'dark';
  const color      = agent.embed_primary_color || '#3ECF8E';
  const buttonText = agent.embed_button_text  || 'Talk to Receptionist';

  const embedCode = `<script\n  src="${apiBase}/widget.js"\n  data-agent-id="${agentId}"\n  data-position="${position}"\n  data-theme="${theme}"\n></script>`;

  React.useEffect(() => {
    fetchWithAuth(`/embed/${agentId}/analytics`)
      .then(setStats)
      .catch(() => {});
  }, [agentId]);

  const copyCode = () => {
    navigator.clipboard.writeText(embedCode).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2200);
    });
  };

  const platforms: Record<string, { title: string; steps: React.ReactNode }> = {
    react: {
      title: 'React',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          <b style={{ color: '#fff' }}>Create React App or Vite — same fix either way:</b><br />
          1. Open <code style={{ color: color }}>public/index.html</code> (CRA) or <code style={{ color: color }}>index.html</code> (Vite) — the static HTML shell, <em>not</em> a <code style={{ color: color }}>.jsx</code>/<code style={{ color: color }}>.tsx</code> file.<br />
          2. Paste the code just before <code style={{ color: color }}>&lt;/body&gt;</code>, as a sibling of <code style={{ color: color }}>&lt;div id="root"&gt;</code> (or <code style={{ color: color }}>#app</code> for Vite).<br /><br />
          <b style={{ color: '#fff' }}>Why here and not inside a component:</b> React Router only re-renders what's inside your root div — it never touches the static shell around it, so a script tag placed here survives every client-side route change automatically. Put it inside a component instead (e.g. a shared <code style={{ color: color }}>Layout.tsx</code>) and it can re-execute every time that component remounts, which is exactly the double-load bug this widget guards against — but placing it in the HTML shell avoids the question entirely.
        </div>
      ),
    },
    nextjs: {
      title: 'Next.js',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          <b style={{ color: '#fff' }}>App Router — <code style={{ color: color }}>app/layout.tsx</code>:</b><br />
          1. Import <code style={{ color: color }}>Script</code> from <code style={{ color: color }}>next/script</code> at the top of your root layout.<br />
          2. Render it inside <code style={{ color: color }}>&lt;body&gt;</code>, alongside <code style={{ color: color }}>{'{children}'}</code>:
          <pre style={{ background: '#0a0a0a', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '8px', padding: '12px', margin: '8px 0', fontSize: '12px', color: '#ccc', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
{`import Script from 'next/script'

<Script
  src="${apiBase}/widget.js"
  data-agent-id="${agentId}"
  strategy="afterInteractive"
/>`}
          </pre>
          <b style={{ color: '#fff' }}>Pages Router — <code style={{ color: color }}>pages/_app.tsx</code>:</b><br />
          Same <code style={{ color: color }}>next/script</code> import, rendered once around <code style={{ color: color }}>&lt;Component {'{...pageProps}'} /&gt;</code>.<br /><br />
          Either way, put it in the <em>root</em> layout/app file, not an individual page — that's what makes it persist across navigation. <code style={{ color: color }}>next/script</code> also dedupes by <code style={{ color: color }}>src</code> on its own, on top of the widget's own duplicate-load guard.
        </div>
      ),
    },
    wordpress: {
      title: 'WordPress',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          Most themes strip raw <code style={{ color: color }}>&lt;script&gt;</code> tags pasted directly into post/page content — use one of these instead:<br /><br />
          <b style={{ color: '#fff' }}>Option A — Theme Editor:</b><br />
          1. Go to <em>Appearance → Theme File Editor</em><br />
          2. Choose <code style={{ color: color }}>footer.php</code><br />
          3. Paste the code just before <code style={{ color: color }}>&lt;/body&gt;</code><br /><br />
          <b style={{ color: '#fff' }}>Option B — Plugin (easier, survives theme updates):</b><br />
          1. Install "WP Headers and Footers" (or "Insert Headers and Footers")<br />
          2. Go to <em>Settings → WP Headers and Footers</em><br />
          3. Paste the code in the Footer Scripts box
        </div>
      ),
    },
    shopify: {
      title: 'Shopify',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          Shopify blocks script injection through the storefront editor's content areas — <code style={{ color: color }}>theme.liquid</code> (or an app embed block) is the only reliable path:<br /><br />
          1. <em>Online Store → Themes → Edit Code</em><br />
          2. Open <code style={{ color: color }}>layout/theme.liquid</code><br />
          3. Paste the code just before <code style={{ color: color }}>&lt;/body&gt;</code><br />
          4. Click <b style={{ color: '#fff' }}>Save</b><br /><br />
          If your theme is managed by an agency and direct code edits get overwritten on theme updates, ask them to add it as an <b style={{ color: '#fff' }}>app embed block</b> instead — same script tag, just delivered through the Theme App Extension so it survives theme changes.
        </div>
      ),
    },
    wix: {
      title: 'Wix',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          1. Open <b style={{ color: '#fff' }}>Wix Editor</b><br />
          2. Click <em>Settings → Custom Code</em><br />
          3. Click <b style={{ color: '#fff' }}>+ Add Custom Code</b><br />
          4. Paste the Lifodial code<br />
          5. Set "Place Code in" to <code style={{ color: color }}>Body - end</code><br />
          6. Click <b style={{ color: '#fff' }}>Apply</b>
        </div>
      ),
    },
    squarespace: {
      title: 'Squarespace',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          1. Go to <em>Pages → Website Tools → Code Injection</em><br />
          2. Paste the code in the <b style={{ color: '#fff' }}>Footer</b> section<br />
          3. Click <b style={{ color: '#fff' }}>Save</b>
        </div>
      ),
    },
    html: {
      title: 'Custom HTML',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          Paste the code just before the closing <code style={{ color: color }}>&lt;/body&gt;</code> tag of your HTML file:
          <pre style={{ background: '#0a0a0a', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '8px', padding: '12px', marginTop: '12px', fontSize: '12px', color: '#ccc', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
{`<!DOCTYPE html>
<html>
  <head>...</head>
  <body>
    <!-- your content -->

    ← Paste here ↓
    ${embedCode}
  </body>
</html>`}
          </pre>
        </div>
      ),
    },
  };

  return (
    <>
      <div style={{ borderRadius: '16px', border: `1px solid rgba(255,255,255,0.06)`, overflow: 'hidden', marginBottom: '12px' }}>
        {/* Header */}
        <div style={{ padding: '20px 24px', background: 'rgba(255,255,255,0.02)', display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{ width: '36px', height: '36px', borderRadius: '8px', background: 'rgba(0,212,170,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Globe size={18} color={ACCENT} />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: '#fff' }}>Website Embed</div>
            <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.4)', marginTop: '2px' }}>Add your AI receptionist to any clinic website in one line</div>
          </div>
        </div>

        <div style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '28px' }}>

          {/* Enable toggle */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', background: 'rgba(255,255,255,0.02)', borderRadius: '12px', border: `1px solid rgba(255,255,255,0.06)` }}>
            <div>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>Widget Active</div>
              <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.4)', marginTop: '3px' }}>Allow this agent to appear on external websites</div>
            </div>
            <Toggle checked={agent.embed_enabled !== false && agent.embed_enabled !== 0} onChange={(v: any) => updateField('embed_enabled', v ? 1 : 0)} label="" />
          </div>

          {/* Avatar — per-agent widget image */}
          <div>
            <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '16px', fontWeight: 600 }}>Widget Avatar</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <div style={{ width: '56px', height: '56px', borderRadius: '50%', overflow: 'hidden', background: 'rgba(255,255,255,0.05)', border: `1px solid rgba(255,255,255,0.1)`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                {agent.avatar_url
                  ? <img src={agent.avatar_url} alt="Agent avatar" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                  : <Headphones size={22} color={ACCENT} />}
              </div>
              <div style={{ flex: 1 }}>
                <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp" style={{ display: 'none' }} onChange={e => handleAvatarPick(e.target.files)} />
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button onClick={() => avatarInputRef.current?.click()} disabled={avatarState === 'uploading'}
                    style={{ padding: '8px 14px', borderRadius: '8px', border: `1px solid rgba(255,255,255,0.15)`, background: 'rgba(255,255,255,0.06)', color: '#fff', fontSize: '13px', fontWeight: 500, cursor: 'pointer' }}>
                    {avatarState === 'uploading' ? 'Uploading…' : agent.avatar_url ? 'Replace image' : 'Upload image'}
                  </button>
                  {agent.avatar_url && (
                    <button onClick={handleAvatarRemove} disabled={avatarState === 'uploading'}
                      style={{ padding: '8px 14px', borderRadius: '8px', border: `1px solid rgba(255,255,255,0.12)`, background: 'transparent', color: 'rgba(255,255,255,0.6)', fontSize: '13px', cursor: 'pointer' }}>
                      Remove
                    </button>
                  )}
                </div>
                <div style={{ fontSize: '11px', color: avatarState === 'error' ? '#EF4444' : 'rgba(255,255,255,0.4)', marginTop: '8px' }}>
                  {avatarState === 'error' ? avatarError : 'PNG, JPG, or WebP · max 8MB · automatically optimized to a fast 256×256 image · shown in the widget launcher & header. Falls back to the default icon if unset.'}
                </div>
              </div>
            </div>
          </div>

          {/* Appearance */}
          <div>
            <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '16px', fontWeight: 600 }}>Appearance</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
              <div>
                <Label>Button Text</Label>
                <Input value={agent.embed_button_text || 'Talk to Receptionist'} onChange={(v: any) => updateField('embed_button_text', v)} placeholder="Talk to Receptionist" />
              </div>
              <div>
                <Label>Primary Color</Label>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                  <input type="color" value={agent.embed_primary_color || '#3ECF8E'} onChange={e => updateField('embed_primary_color', e.target.value)}
                    style={{ width: '48px', height: '42px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)', cursor: 'pointer', background: 'none', padding: '2px' }} />
                  <Input value={agent.embed_primary_color || '#3ECF8E'} onChange={(v: any) => updateField('embed_primary_color', v)} style={{ fontFamily: 'monospace' }} />
                </div>
              </div>
              <div>
                <Label>Button Position</Label>
                <div style={{ display: 'flex', gap: '10px', marginTop: '6px' }}>
                  {['bottom-right', 'bottom-left'].map(pos => (
                    <label key={pos} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#fff', cursor: 'pointer' }}>
                      <input type="radio" checked={(agent.embed_position || 'bottom-right') === pos}
                        onChange={() => updateField('embed_position', pos)} style={{ accentColor: ACCENT }} />
                      {pos === 'bottom-right' ? 'Bottom Right' : 'Bottom Left'}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <Label>Theme</Label>
                <div style={{ display: 'flex', gap: '10px', marginTop: '6px' }}>
                  {['dark', 'light'].map(t => (
                    <label key={t} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#fff', cursor: 'pointer' }}>
                      <input type="radio" checked={(agent.embed_theme || 'dark') === t}
                        onChange={() => updateField('embed_theme', t)} style={{ accentColor: ACCENT }} />
                      {t.charAt(0).toUpperCase() + t.slice(1)}
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <Label>Display Mode</Label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '6px' }}>
                  {[
                    { id: 'button', name: 'Button with label', desc: 'Icon + button text (default).' },
                    { id: 'icon', name: 'Icon only', desc: 'Just the launcher icon — minimal footprint, no text.' },
                    { id: 'auto-invite', name: 'Auto-invite', desc: 'Panel auto-opens after a delay to invite the visitor. Does NOT start audio or ask for the mic — the visitor still taps to talk.' },
                  ].map(m => (
                    <label key={m.id} style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '13px', color: '#fff', cursor: 'pointer' }}>
                      <input type="radio" checked={(agent.embed_display_mode || 'button') === m.id}
                        onChange={() => updateField('embed_display_mode', m.id)} style={{ accentColor: ACCENT, marginTop: '2px' }} />
                      <span>
                        <span style={{ fontWeight: 600 }}>{m.name}</span>
                        <span style={{ display: 'block', fontSize: '11px', color: 'rgba(255,255,255,0.45)' }}>{m.desc}</span>
                      </span>
                    </label>
                  ))}
                </div>
                {(agent.embed_display_mode === 'auto-invite') && (
                  <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Label>Auto-invite delay (seconds)</Label>
                    <input type="number" min={1} max={60}
                      value={agent.embed_auto_invite_delay ?? 3}
                      onChange={e => updateField('embed_auto_invite_delay', Math.max(1, Math.min(60, parseInt(e.target.value) || 3)))}
                      style={{ width: '64px', background: 'rgba(255,255,255,0.04)', border: `1px solid ${BORDER}`, borderRadius: '8px', padding: '6px 8px', color: '#fff', fontSize: '13px' }} />
                  </div>
                )}
              </div>
              <div>
                <Label>Show Lifosys Branding</Label>
                <Toggle checked={agent.embed_show_branding !== false && agent.embed_show_branding !== 0} onChange={(v: any) => updateField('embed_show_branding', v ? 1 : 0)} label="Powered by Lifosys" />
              </div>
            </div>
          </div>

          {/* Security */}
          <div>
            <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '12px', fontWeight: 600 }}>Security — Allowed Domains</div>
            <Textarea
              value={Array.isArray(agent.embed_allowed_domains) ? agent.embed_allowed_domains.join('\n') : (agent.embed_allowed_domains || '')}
              onChange={(v: any) => {
                const domains = v.split('\n').map((d: string) => d.trim()).filter(Boolean);
                updateField('embed_allowed_domains', domains);
              }}
              placeholder={'apolloclinic.com\napollo.in\nwww.apollohospital.com'}
              rows={3}
            />
            <Helper>One domain per line. Leave empty to allow all domains (not recommended in production).</Helper>
          </div>

          {/* Live Preview — reflects UNSAVED appearance + display mode in real time.
              The form values are forwarded to the preview page as query params,
              which it maps to the widget's data-* attributes. `key` forces the
              iframe to reload whenever any of these change. */}
          {(() => {
            const previewParams = new URLSearchParams({
              style: agent.embed_display_mode || 'button',
              theme: agent.embed_theme || 'dark',
              position: agent.embed_position || 'bottom-right',
              color: agent.embed_primary_color || '#3ECF8E',
              label: agent.embed_button_text || 'Talk to Receptionist',
              delay: String(agent.embed_auto_invite_delay ?? 3),
            }).toString();
            const previewSrc = `${apiBase}/embed/${agentId}/preview?${previewParams}`;
            return (
              <div>
                <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '12px', fontWeight: 600 }}>Live Preview</div>
                <iframe
                  key={previewSrc}
                  src={previewSrc}
                  style={{ width: '100%', height: '280px', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '12px', background: '#0a0a0a' }}
                  title="Widget Preview"
                />
              </div>
            );
          })()}

          {/* Embed Code */}
          <div>
            <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '12px', fontWeight: 600 }}>Embed Code</div>
            <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.5)', marginBottom: '12px' }}>
              Copy this single line and give it to your web developer:
            </div>
            <div style={{ position: 'relative' }}>
              <pre style={{
                background: '#080808', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '12px',
                padding: '16px 20px', fontSize: '13px', color: '#a5f3c0', fontFamily: 'monospace',
                overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0,
                paddingRight: '100px',
              }}>{embedCode}</pre>
              <button
                onClick={copyCode}
                style={{
                  position: 'absolute', top: '12px', right: '12px',
                  padding: '6px 14px', borderRadius: '8px', border: `1px solid rgba(255,255,255,0.15)`,
                  background: copied ? ACCENT : 'rgba(255,255,255,0.08)',
                  color: copied ? '#000' : '#fff', fontSize: '12px', fontWeight: 600,
                  cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', transition: 'all 0.2s',
                }}
              >
                <Code2 size={12} />{copied ? '✓ Copied!' : 'Copy'}
              </button>
            </div>
          </div>

          {/* View Install Guide */}
          <div>
            <button
              onClick={() => setShowGuide(true)}
              style={{ padding: '10px 20px', borderRadius: '8px', border: `1px solid rgba(255,255,255,0.12)`, background: 'transparent', color: '#fff', fontSize: '13px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px' }}
            >
              📄 View Full Installation Instructions
            </button>
          </div>

          {/* Analytics */}
          {stats && (
            <div>
              <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '16px', fontWeight: 600 }}>This Month's Analytics</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px' }}>
                {[
                  { label: 'Widget Views', val: stats.views },
                  { label: 'Opens', val: `${stats.opens} (${stats.open_rate}%)` },
                  { label: 'Conversations', val: `${stats.conversations} (${stats.chat_rate}%)` },
                  { label: 'Bookings via Web', val: `${stats.bookings} (${stats.booking_rate}%)` },
                ].map(s => (
                  <div key={s.label} style={{ padding: '16px', background: 'rgba(255,255,255,0.02)', border: `1px solid rgba(255,255,255,0.06)`, borderRadius: '12px' }}>
                    <div style={{ fontSize: '22px', fontWeight: 700, color: '#fff' }}>{s.val}</div>
                    <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.4)', marginTop: '4px' }}>{s.label}</div>
                  </div>
                ))}
              </div>
              {/* Funnel */}
              <div style={{ marginTop: '16px', padding: '14px 18px', background: 'rgba(255,255,255,0.02)', border: `1px solid rgba(255,255,255,0.06)`, borderRadius: '12px', fontSize: '12px', color: 'rgba(255,255,255,0.5)' }}>
                Conversion funnel: &nbsp;
                <span style={{ color: '#fff' }}>Views {stats.views}</span> →&nbsp;
                <span style={{ color: '#fff' }}>Opens {stats.opens}</span> →&nbsp;
                <span style={{ color: '#fff' }}>Conversations {stats.conversations}</span> →&nbsp;
                <span style={{ color: ACCENT }}>Bookings {stats.bookings}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Installation Guide Modal ── */}
      {showGuide && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 200, background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}
          onClick={e => { if (e.target === e.currentTarget) setShowGuide(false); }}>
          <div style={{ width: '100%', maxWidth: '680px', background: '#0f0f0f', border: `1px solid rgba(255,255,255,0.1)`, borderRadius: '20px', overflow: 'hidden', maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}>
            {/* Modal Header */}
            <div style={{ padding: '20px 24px', borderBottom: `1px solid rgba(255,255,255,0.06)`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
              <div>
                <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>Add AI Receptionist to Your Website</div>
                <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.4)', marginTop: '3px' }}>Step-by-step integration guide</div>
              </div>
              <button onClick={() => setShowGuide(false)} style={{ background: 'rgba(255,255,255,0.08)', border: 'none', color: '#fff', width: '32px', height: '32px', borderRadius: '50%', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <X size={16} />
              </button>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '24px', display: 'flex', flexDirection: 'column', gap: '28px' }}>
              {/* Step 1 */}
              <div>
                <div style={{ fontSize: '12px', color: ACCENT, fontWeight: 700, marginBottom: '8px' }}>STEP 1 — Copy the embed code</div>
                <div style={{ position: 'relative' }}>
                  <pre style={{ background: '#080808', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '10px', padding: '14px 16px', fontSize: '12px', color: '#a5f3c0', fontFamily: 'monospace', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0, paddingRight: '80px' }}>{embedCode}</pre>
                  <button onClick={copyCode} style={{ position: 'absolute', top: '10px', right: '10px', padding: '5px 12px', borderRadius: '6px', border: `1px solid rgba(255,255,255,0.15)`, background: copied ? ACCENT : 'rgba(255,255,255,0.08)', color: copied ? '#000' : '#fff', fontSize: '11px', fontWeight: 600, cursor: 'pointer' }}>
                    {copied ? '✓' : 'Copy'}
                  </button>
                </div>
              </div>

              {/* Step 2 — diagram */}
              <div>
                <div style={{ fontSize: '12px', color: ACCENT, fontWeight: 700, marginBottom: '8px' }}>STEP 2 — Where to paste it</div>
                <p style={{ fontSize: '13px', color: 'rgba(255,255,255,0.6)', marginBottom: '12px' }}>
                  Paste this code just before the closing <code style={{ color: ACCENT }}>&lt;/body&gt;</code> tag of your website's HTML.
                </p>
                <pre style={{ background: '#080808', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '10px', padding: '16px', fontSize: '12px', color: '#ccc', fontFamily: 'monospace', whiteSpace: 'pre', overflowX: 'auto', margin: 0 }}>
{`<!DOCTYPE html>
<html>
  <head>...</head>
  <body>
    <!-- Your website content -->

    <!-- ↓ Paste Lifodial script here -->
    <script
      src="${apiBase}/widget.js"
      data-agent-id="${agentId}"
    ></script>
  </body>  ← just before this
</html>`}
                </pre>
              </div>

              {/* Step 3 — platforms */}
              <div>
                <div style={{ fontSize: '12px', color: ACCENT, fontWeight: 700, marginBottom: '12px' }}>STEP 3 — Platform-specific guide</div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
                  {Object.entries(platforms).map(([key, p]) => (
                    <button key={key} onClick={() => setGuidePlatform(key)}
                      style={{ padding: '6px 14px', borderRadius: '20px', border: `1px solid ${guidePlatform === key ? ACCENT : 'rgba(255,255,255,0.12)'}`, background: guidePlatform === key ? 'rgba(0,212,170,0.1)' : 'transparent', color: guidePlatform === key ? ACCENT : '#888', fontSize: '12px', fontWeight: 500, cursor: 'pointer' }}>
                      {p.title}
                    </button>
                  ))}
                </div>
                <div style={{ padding: '16px', background: 'rgba(255,255,255,0.02)', border: `1px solid rgba(255,255,255,0.06)`, borderRadius: '10px' }}>
                  {platforms[guidePlatform].steps}
                </div>
              </div>

              {/* Step 4 */}
              <div>
                <div style={{ fontSize: '12px', color: ACCENT, fontWeight: 700, marginBottom: '8px' }}>STEP 4 — Test it</div>
                <p style={{ fontSize: '13px', color: 'rgba(255,255,255,0.6)' }}>
                  Visit your website after adding the code. You should see the <strong style={{ color: '#fff' }}>"Talk to Receptionist"</strong> button in the corner. Click it to start chatting with your AI receptionist!
                </p>
              </div>

              {/* Step 5 — customization */}
              <div>
                <div style={{ fontSize: '12px', color: ACCENT, fontWeight: 700, marginBottom: '8px' }}>STEP 5 — Optional customization</div>
                <pre style={{ background: '#080808', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '10px', padding: '14px 16px', fontSize: '12px', color: '#ccc', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
{`data-position="bottom-left"  → Move widget to bottom-left
data-theme="light"            → Use light background
data-language="ta-IN"         → Force Tamil language`}
                </pre>
              </div>

              {/* Step 6 — CSP */}
              <div>
                <div style={{ fontSize: '12px', color: ACCENT, fontWeight: 700, marginBottom: '8px' }}>STEP 6 — Nothing showing up? Check your CSP</div>
                <p style={{ fontSize: '13px', color: 'rgba(255,255,255,0.6)', marginBottom: '12px' }}>
                  Many WordPress security plugins and enterprise sites ship a strict <code style={{ color: ACCENT }}>Content-Security-Policy</code> that blocks third-party widgets with <em>no visible error</em> on the page — only a CSP violation in the browser console. If the button never appears, hand this to whoever manages the site's security headers:
                </p>
                <pre style={{ background: '#080808', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '10px', padding: '14px 16px', fontSize: '12px', color: '#ccc', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
{(() => {
  const origin = (() => { try { return new URL(apiBase).origin; } catch { return apiBase; } })();
  const wsOrigin = origin.replace(/^http/, 'ws');
  return `script-src ${origin};
connect-src ${origin} ${wsOrigin};
style-src 'unsafe-inline';`;
})()}
                </pre>
                <p style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)', marginTop: '10px', lineHeight: 1.7 }}>
                  <b style={{ color: 'rgba(255,255,255,0.7)' }}>script-src</b> — loads widget.js itself.<br />
                  <b style={{ color: 'rgba(255,255,255,0.7)' }}>connect-src</b> — the widget's chat/config requests (<code style={{ color: ACCENT }}>https</code>) and its live voice call (<code style={{ color: ACCENT }}>wss</code>) both count as "connect," not "frame" — the widget never uses an iframe, so <code style={{ color: ACCENT }}>frame-src</code> isn't needed.<br />
                  <b style={{ color: 'rgba(255,255,255,0.7)' }}>style-src 'unsafe-inline'</b> — the widget injects its own <code style={{ color: ACCENT }}>&lt;style&gt;</code> tag at runtime to theme itself; without this the button and panel will render completely unstyled.<br /><br />
                  Voice calls also need microphone access — if the site sets a <code style={{ color: ACCENT }}>Permissions-Policy</code> header that blocks <code style={{ color: ACCENT }}>microphone</code> site-wide, voice will fail even with CSP correctly configured. Chat is unaffected either way.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────

function getLlmFallbackModels(provider: string): string[] {
  const map: Record<string, string[]> = {
    gemini: ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-1.5-pro'],
    openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
    anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-haiku-20240307'],
    groq: ['llama-3.3-70b-versatile', 'mixtral-8x7b-32768'],
    deepseek: ['deepseek-chat', 'deepseek-reasoner'],
    mistral: ['mistral-large-latest', 'mistral-small-latest'],
  };
  return map[provider] || ['gemini-2.5-flash'];
}

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [agent, setAgent] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<null | 'saving' | 'saved' | 'error'>(null);
  const [showTest, setShowTest] = useState(false);
  const timerRef = useRef<any>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

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
  
  // Scroll-spy tab navigation
  type AgentTab = 'assistant' | 'logs' | 'tools' | 'analysis' | 'advanced';
  const [activeTab, setActiveTab] = useState<AgentTab>('assistant');
  const AGENT_TABS: { id: AgentTab; label: string; icon: any }[] = [
    { id: 'assistant', label: 'Assistant',  icon: Mic },
    { id: 'logs',      label: 'Logs',       icon: LineChart },
    { id: 'tools',     label: 'Tools',      icon: Wrench },
    { id: 'analysis',  label: 'Analysis',   icon: Activity },
    { id: 'advanced',  label: 'Advanced',   icon: Sliders },
  ];

  // Refs for scroll-spy section anchors
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const sectionRefs = useRef<Record<AgentTab, HTMLDivElement | null>>({
    assistant: null, logs: null, tools: null, analysis: null, advanced: null,
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

  // IntersectionObserver for scroll-spy
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const observer = new IntersectionObserver(
      (entries) => {
        // Find the topmost section that is intersecting
        const visible = entries
          .filter(e => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          const id = visible[0].target.getAttribute('data-section') as AgentTab;
          if (id) setActiveTab(id);
        }
      },
      { root: container, rootMargin: '-20% 0px -60% 0px', threshold: 0 }
    );
    // Observe all section refs
    Object.entries(sectionRefs.current).forEach(([, el]) => {
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [agent]); // re-run when agent loads
  
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

  const toFallbackAgent = useCallback((id?: string) => {
    const found = FIXTURE_AGENTS.find(a => a.id === id) || FIXTURE_AGENTS[0];
    if (!found) return null;

    return {
      id: found.id,
      agent_name: found.name,
      clinic_name: found.clinic_name,
      status: found.status,
      llm_provider: found.llm_provider || 'gemini',
      llm_model: found.llm_model || 'gemini-2.5-flash',
      first_message_mode: 'assistant-speaks-first',
      first_message: found.first_message || 'Hello, how can I help you today?',
      system_prompt: 'You are a helpful AI clinic receptionist.',
      max_response_tokens: 300,
      tts_provider: found.tts_provider || 'sarvam',
      tts_voice: found.tts_voice || 'meera',
      tts_language: found.tts_language || 'en-IN',
      tts_model: found.tts_model || 'bulbul:v3',
      tts_pitch: 0,
      tts_pace: 1,
      tts_loudness: 1,
      tts_input_preprocessing: 1,
      tts_stability: 0.5,
      tts_clarity: 0.75,
      tts_style: 0,
      tts_use_speaker_boost: 1,
      tts_speed: 1,
      tts_optimize_streaming_latency: 1,
      tts_filler_injection: 0,
      stt_provider: found.stt_provider || 'sarvam',
      stt_model: found.stt_model || 'saaras:v3',
      transcriber_keywords: [],
      end_call_phrases: ['thank you', 'goodbye'],
      tools_enabled: [],
      clinic_info: {},
    };
  }, []);

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
      const fallback = toFallbackAgent(agentId);
      if (fallback) {
        setAgent(fallback);
        setLoadError('Live agent API unavailable. Showing local fallback data.');
      } else {
        setAgent(null);
        setLoadError('Unable to load this agent. Please try again.');
      }
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  }, [agentId, toFallbackAgent]);

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

  // Deep-link support: a caller can request a specific tab via ?tab=<id>
  // (e.g. a future "View Logs" link) to opt out of the assistant/top default
  // above. Runs after the agent has loaded so the target section actually
  // exists in the DOM to scroll to.
  useEffect(() => {
    if (loading || !agent) return;
    const requested = new URLSearchParams(location.search).get('tab');
    const validTabs: AgentTab[] = ['assistant', 'logs', 'tools', 'analysis', 'advanced'];
    if (requested && (validTabs as string[]).includes(requested) && requested !== 'assistant') {
      handleTabClick(requested as AgentTab);
    }
  }, [agentId, location.search, loading, agent]);

  // Fetch models when provider changes.
  // FIX: Only auto-reset model when the current model does NOT belong to the
  // newly selected provider — preserves the user's explicit model choice.
  useEffect(() => {
    if (!agent?.llm_provider) return;
    fetchWithAuth(`/platform/models/${agent.llm_provider}`)
      .then(d => {
        const models = d.models?.length ? d.models : getLlmFallbackModels(agent.llm_provider);
        setLlmModels(models);
        // Only reset if current model is unknown for this provider
        if (!agent.llm_model || !models.includes(agent.llm_model)) {
          updateField('llm_model', models[0]);
        }
      })
      .catch(() => {
        const fallback = getLlmFallbackModels(agent.llm_provider);
        setLlmModels(fallback);
        if (!agent.llm_model || !fallback.includes(agent.llm_model)) {
          updateField('llm_model', fallback[0]);
        }
      });
  }, [agent?.llm_provider]);

  useEffect(() => {
    if (!agent?.tts_provider) return;
    fetchWithAuth(`/platform/models/${agent.tts_provider}?category=tts`)
      .then(d => {
        const models = d.models?.length ? d.models : ['bulbul:v3'];
        setTtsModels(models);
        // Only reset if current model is unknown for this TTS provider
        if (!agent.tts_model || !models.includes(agent.tts_model)) {
          updateField('tts_model', models[0]);
        }
      })
      .catch(() => setTtsModels(['bulbul:v3']));
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
      })
      .catch(() => setTtsVoices([]));
  // Re-fetch any time provider OR model changes
  }, [agent?.tts_provider, agent?.tts_model]);

  useEffect(() => {
    if (!agent?.stt_provider) return;
    fetchWithAuth(`/platform/models/${agent.stt_provider}?category=stt`)
      .then(d => {
        const models = d.models?.length ? d.models : ['saarika:v2'];
        setSttModels(models);
        // Only reset if current model is unknown for this STT provider
        if (!agent.stt_model || !models.includes(agent.stt_model)) {
          updateField('stt_model', models[0]);
        }
      })
      .catch(() => setSttModels(['saarika:v2']));
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
    const txt = overrideParams?.text || agent?.first_message || 'Hello! I am your AI receptionist. How can I help you today?';

    setSampleError(null);
    setSamplePlayer('loading');

    try {
      const params = new URLSearchParams({
        provider: prov,
        voice_id: voice,
        language: lang,
        text: txt,
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

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '32px 24px', marginTop: '8px' }}>
                {agent.tts_provider === 'sarvam' ? (
                  <>
                    <Slider value={agent.tts_pitch} min={-1} max={1} onChange={(v:any) => updateField('tts_pitch', v)} leftLabel="Low Pitch" rightLabel="High Pitch" />
                    <Slider value={agent.tts_pace} min={0.5} max={2.0} onChange={(v:any) => updateField('tts_pace', v)} leftLabel="Slow" rightLabel="Fast" />
                    <Slider value={agent.tts_loudness} min={0.5} max={2.0} onChange={(v:any) => updateField('tts_loudness', v)} leftLabel="Quiet" rightLabel="Loud" />
                    <Toggle checked={agent.tts_input_preprocessing === 1} onChange={(v:any) => updateField('tts_input_preprocessing', v ? 1 : 0)} label="Input Preprocessing" />
                  </>
                ) : (
                  <>
                    <Slider value={agent.tts_stability} min={0} max={1} onChange={(v:any) => updateField('tts_stability', v)} leftLabel="More variable" rightLabel="More stable" />
                    <Slider value={agent.tts_clarity} min={0} max={1} onChange={(v:any) => updateField('tts_clarity', v)} leftLabel="Low" rightLabel="High" />
                    <Slider value={agent.tts_style} min={0} max={1} onChange={(v:any) => updateField('tts_style', v)} leftLabel="None" rightLabel="Exaggerated" />
                    <Toggle checked={agent.tts_use_speaker_boost === 1} onChange={(v:any) => updateField('tts_use_speaker_boost', v ? 1 : 0)} label="Use Speaker Boost" />
                  </>
                )}
                {/* Redundant with the Sarvam-only Pace slider above (both control
                    playback speed for that provider) — only shown for providers
                    where tts_speed is the sole speed control. */}
                {agent.tts_provider !== 'sarvam' && (
                  <Slider value={agent.tts_speed} min={0.5} max={2.0} onChange={(v:any) => updateField('tts_speed', v)} leftLabel="0.5x Speed" rightLabel="2.0x Speed" />
                )}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginTop: '8px' }}>
                <div style={{ opacity: 0.5 }}>
                  <Label>Optimize for Streaming Latency</Label>
                  <Select value={agent.tts_optimize_streaming_latency} onChange={() => {}} options={[0,1,2,3,4]} style={{ pointerEvents: 'none' }} />
                  <Helper>Not supported by the currently-used Sarvam/ElevenLabs streaming integration — has no effect on calls.</Helper>
                </div>
                <div style={{ paddingTop: '16px', opacity: 0.5 }}>
                  <Toggle checked={false} onChange={() => {}} label="Filler Injection" helper="Not yet implemented — has no effect on calls." />
                </div>
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
              <div>
                <Label>Keywords / Boost Terms</Label>
                <TagInput tags={Array.isArray(agent.transcriber_keywords) ? agent.transcriber_keywords : (agent.transcriber_keywords ? (typeof agent.transcriber_keywords === 'string' ? JSON.parse(agent.transcriber_keywords) : []) : [])} onChange={(t:any) => updateField('transcriber_keywords', JSON.stringify(t))} placeholder="Type a word and press enter..." />
                <Helper>Add clinic-specific terms to improve accuracy for names/medications.</Helper>
              </div>
              <div>
                <Label>Fallback Transcribers</Label>
                <TagInput
                  tags={Array.isArray(agent.fallback_transcribers) ? agent.fallback_transcribers : (agent.fallback_transcribers ? (typeof agent.fallback_transcribers === 'string' ? JSON.parse(agent.fallback_transcribers) : []) : [])}
                  onChange={(t: any) => updateField('fallback_transcribers', JSON.stringify(t))}
                  placeholder="Type a provider name (e.g. deepgram) and press enter..."
                />
                <Helper>
                  Persisted, but not yet consulted by the live pipeline — the currently-running STT service isn't
                  retried against a fallback provider on failure. Configuring this list documents intent for now.
                </Helper>
              </div>
            </CollapsibleSection>

            {/* 4. TELEPHONY */}
            <CollapsibleSection icon={Phone} title="Telephony" summary={`${agent.telephony_option} · ${agent.ai_number||'None'}`}>
              <div style={{ display: 'flex', gap: '12px' }}>
                {['exotel', 'twilio', 'sip', 'livekit'].map(opt => (
                  <div key={opt} onClick={() => updateField('telephony_option', opt)} style={{ flex: 1, padding: '16px', background: agent.telephony_option === opt ? 'rgba(0,212,170,0.1)' : 'rgba(255,255,255,0.03)', border: `1px solid ${agent.telephony_option === opt ? ACCENT : BORDER}`, borderRadius: '12px', cursor: 'pointer', textAlign: 'center', transition: 'all 0.2s' }}>
                    <div style={{ fontSize: '14px', fontWeight: 600, color: agent.telephony_option === opt ? ACCENT : '#fff', textTransform: 'capitalize' }}>{opt}</div>
                  </div>
                ))}
              </div>
              <Helper>
                {agent.telephony_option === 'livekit'
                  ? 'LiveKit is what actually carries every call today (web + browser test), regardless of this setting.'
                  : `Exotel/Twilio/SIP selection is saved, but no telephony integration is wired to it yet — calls still route through LiveKit. Configure keys under AI Platform → Telephony before relying on ${agent.telephony_option || 'this provider'}.`}
              </Helper>

              <div style={{ marginTop: '8px' }}>
                {agent.telephony_option === 'exotel' && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                    <div><Label>API Key</Label><Input type="password" value="********" onChange={()=>{}} /></div>
                    <div><Label>API Token</Label><Input type="password" value="********" onChange={()=>{}} /></div>
                    <div><Label>Account SID</Label><Input value={agent.sip_account_sid} onChange={(v:any) => updateField('sip_account_sid', v)} /></div>
                    <div><Label>Virtual Number</Label><Input value={agent.ai_number} onChange={(v:any) => updateField('ai_number', v)} /></div>
                    <div style={{ gridColumn: '1 / -1' }}><Label>Webhook URL</Label><Input value={`https://api.lifodial.com/voice/incoming/${agent.id}`} locked /></div>
                  </div>
                )}
                {agent.telephony_option === 'livekit' && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                    <div style={{ gridColumn: '1 / -1' }}><Label>LiveKit URL</Label><Input value={agent.livekit_url} onChange={(v:any) => updateField('livekit_url', v)} /></div>
                    <div><Label>API Key</Label><Input type="password" value={agent.livekit_api_key} onChange={(v:any) => updateField('livekit_api_key', v)} /></div>
                    <div><Label>API Secret</Label><Input type="password" value={agent.livekit_api_secret} onChange={(v:any) => updateField('livekit_api_secret', v)} /></div>
                  </div>
                )}
                {agent.telephony_option === 'sip' && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                    <div><Label>SIP Domain</Label><Input value={agent.sip_domain} onChange={(v:any) => updateField('sip_domain', v)} /></div>
                    <div><Label>SIP Provider Name</Label><Input value={agent.sip_provider} onChange={(v:any) => updateField('sip_provider', v)} /></div>
                  </div>
                )}
              </div>

              <div style={{ marginTop: '16px', paddingTop: '16px', borderTop: `1px solid ${BORDER}` }}>
                <Label>Existing Clinic Number</Label>
                <div style={{ display: 'flex', gap: '12px' }}>
                  <Select value={agent.country_code} onChange={(v:any) => updateField('country_code', v)} options={['+91', '+1', '+44', '+971']} style={{ width: '80px' }} />
                  <Input value={agent.existing_clinic_number} onChange={(v:any) => updateField('existing_clinic_number', v)} placeholder="e.g. 9876543210" style={{ flex: 1 }} />
                </div>
                <Helper>The clinic's current phone number. Set up call forwarding from this to the AI number above.</Helper>
              </div>
            </CollapsibleSection>

            {/* 5. CALL BEHAVIOR — still Assistant tab */}
            {/* 5. CALL BEHAVIOR */}
            <CollapsibleSection icon={Settings} title="Call Behavior" summary={`Max ${agent.max_duration_seconds}s · ${agent.silence_timeout_seconds}s timeout`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
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
                    <Label>Background Sound</Label>
                    <Select value={agent.background_sound} onChange={(v:any) => updateField('background_sound', v)} options={['none', 'office_ambience', 'soft_music']} />
                  </div>
                  <Toggle checked={agent.background_denoising === 1} onChange={(v:any) => updateField('background_denoising', v?1:0)} label="Background Denoising" helper="Filter out clinic noise" />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  <Toggle checked={agent.record_calls === 1} onChange={(v:any) => updateField('record_calls', v?1:0)} label="Record Calls" helper="Store call recordings for quality review" />
                  <Toggle checked={agent.model_output_in_realtime === 1} onChange={(v:any) => updateField('model_output_in_realtime', v?1:0)} label="Real-time Model Output" helper="Stream AI responses word by word" />
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
              </div>
            </CollapsibleSection>

            </div>{/* end assistant section */}

            {/* ══ TOOLS SECTION ═════════════════════════════════════════════════ */}
            <div ref={el => { sectionRefs.current.tools = el; }} data-section="tools">
            {/* 6. TOOLS */}
            <CollapsibleSection icon={Wrench} title="Tools" summary={`${Array.isArray(agent.tools_enabled) ? agent.tools_enabled.length : (agent.tools_enabled && typeof agent.tools_enabled === 'string' ? JSON.parse(agent.tools_enabled).length : 0)} tools enabled`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                {[
                  { id: 'appt_booking', name: '🗓️ Appointment Booking', desc: 'Connect to HIS to check availability & book.', dbField: 'can_book_appointments' },
                  { id: 'transfer_call', name: '📞 Transfer Call', desc: 'Transfer to human agent. (Not yet available — no telephony transfer integration configured.)' },
                  { id: 'sms', name: '📱 Send SMS', desc: 'Send appointment confirmation. (Not yet available — no SMS provider configured.)' },
                  { id: 'status', name: '🔍 Check Appt Status', desc: 'Patients can check existing.', dbField: 'can_check_availability' },
                  { id: 'cancel', name: '❌ Cancel Appointment', desc: 'Patients can cancel.', dbField: 'can_cancel_appointments' },
                  { id: 'doctors', name: '🩺 Doctor Information', desc: 'Answer questions about doctors.' },
                  { id: 'hours', name: '⏰ Clinic Hours', desc: 'Tell patients about hours.' },
                  { id: 'emergency', name: '🚨 Emergency Redirect', desc: 'Detect emergencies & speak the emergency number. (Announcement only — no live call transfer yet.)', dbField: 'can_transfer_emergency' }
                ].map(t => {
                  const enabledTools = Array.isArray(agent.tools_enabled) ? agent.tools_enabled : (agent.tools_enabled && typeof agent.tools_enabled === 'string' ? JSON.parse(agent.tools_enabled) : []);
                  // Tools backed by a real AgentConfig column (dbField) are gated by
                  // that column at call time — read/write it directly instead of the
                  // flat tools_enabled list, which nothing in the pipeline reads.
                  const isEnabled = t.dbField ? (agent[t.dbField] ?? true) : enabledTools.includes(t.id);
                  return (
                    <div key={t.id} style={{ background: 'rgba(255,255,255,0.02)', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{t.name}</span>
                        <Toggle checked={isEnabled} onChange={(on:any) => {
                          if (t.dbField) {
                            updateField(t.dbField, on);
                            return;
                          }
                          const newer = on ? [...enabledTools, t.id] : enabledTools.filter((x:any) => x !== t.id);
                          updateField('tools_enabled', JSON.stringify(newer));
                        }} label="" />
                      </div>
                      <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.45)' }}>{t.desc}</div>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: '20px', paddingTop: '20px', borderTop: `1px solid ${BORDER}` }}>
                <Label>Custom Functions</Label>
                <Helper>Define custom functions to extend agent capabilities via your own webhooks.</Helper>
                <button style={{ marginTop: '12px', padding: '8px 16px', background: 'none', border: `1px solid ${BORDER}`, color: '#fff', borderRadius: '8px', fontSize: '13px', cursor: 'pointer' }}>+ Add Function</button>
              </div>

              <div style={{ marginTop: '20px', paddingTop: '20px', borderTop: `1px solid ${BORDER}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                  <Globe size={14} color={ACCENT} />
                  <span style={{ fontSize: '12px', fontWeight: 600, color: '#fff', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Google Sheets Integration</span>
                </div>
                <Label>Google Sheets Webapp URL</Label>
                <Input 
                  value={agent.google_sheets_webhook_url} 
                  onChange={(v:any) => updateField('google_sheets_webhook_url', v)} 
                  placeholder="e.g. https://script.google.com/macros/s/.../exec"
                />
                <Helper>
                  Paste the deployed Google Apps Script Web App URL here. Every time a patient books, reschedules, or cancels an appointment, the details will automatically sync with this Google Sheet.
                </Helper>
              </div>
            </CollapsibleSection>

            {/* 7. KNOWLEDGE BASE — still in Tools tab */}
            {/* 7. KNOWLEDGE BASE */}
            <CollapsibleSection icon={BookOpen} title="Knowledge Base" summary="0 documents · 0MB indexed">
              <div style={{ padding: '40px', border: `1px dashed rgba(255,255,255,0.2)`, borderRadius: '12px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '12px', background: 'rgba(255,255,255,0.01)', cursor: 'pointer' }}>
                <Upload size={24} color="#888" />
                <div style={{ color: '#fff', fontSize: '14px', fontWeight: 500 }}>Drop files here or click to browse</div>
                <div style={{ color: 'rgba(255,255,255,0.45)', fontSize: '12px' }}>PDF · TXT · DOCX · CSV · MD (Max 50MB)</div>
              </div>
              <div style={{ marginTop: '20px' }}>
                <Label>Search Test</Label>
                <Input placeholder="Type a question to test retrieval..." onChange={()=>{}} />
              </div>
            </CollapsibleSection>

            {/* 11. EMBED / WEBSITE WIDGET */}
            <EmbedSection agent={agent} agentId={agentId} updateField={updateField} />

            </div>{/* end tools section */}

            {/* ══ ANALYSIS SECTION ══════════════════════════════════════════════ */}
            <div ref={el => { sectionRefs.current.analysis = el; }} data-section="analysis">
            {/* 9. ANALYSIS & OUTCOMES */}
            <CollapsibleSection icon={LineChart} title="Analysis & Outcomes" summary={`Summary · Evaluation · Structured output ${agent.structured_output_enabled ? 'on' : 'off'}`}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                <Toggle checked={agent.summary_enabled === 1} onChange={(v:any) => updateField('summary_enabled', v?1:0)} label="Call Summary" helper="Generate a summary of each call automatically" />
                <div>
                  <Toggle checked={agent.success_evaluation_enabled === 1} onChange={(v:any) => updateField('success_evaluation_enabled', v?1:0)} label="Success Evaluation" helper="Evaluate if the call achieved its goal" />
                  {agent.success_evaluation_enabled === 1 && (
                     <div style={{ marginTop: '12px', paddingLeft: '48px' }}>
                       <Label>Success Criteria</Label>
                       <Textarea value="The call was successful if the patient booked an appointment OR was given the information they needed." onChange={()=>{}} />
                     </div>
                  )}
                </div>
                <div>
                  <Toggle checked={agent.structured_output_enabled === 1} onChange={(v:any) => updateField('structured_output_enabled', v?1:0)} label="Structured Output" helper="Extract JSON data from each call (appointment details, intent, etc)" />
                </div>
              </div>
            </CollapsibleSection>

            {/* Voicemail Detection — also in Analysis tab */}
            <CollapsibleSection icon={Voicemail} title="Voicemail Detection" summary={agent.voicemail_detection_enabled ? "Enabled" : "Disabled"}>
              <Toggle checked={agent.voicemail_detection_enabled === 1} onChange={(v:any) => updateField('voicemail_detection_enabled', v?1:0)} label="Enable Voicemail Detection" />
              {agent.voicemail_detection_enabled === 1 && (
                <div style={{ marginTop: '16px' }}>
                  <Label>Voicemail Message</Label>
                  <Textarea value={agent.voicemail_message ?? ''} onChange={(v:any) => updateField('voicemail_message', v)} placeholder="Hello! Please call back later..." />
                  <Helper>If voicemail detected, leave this message.</Helper>
                </div>
              )}
            </CollapsibleSection>

            </div>{/* end analysis section */}

            {/* ══ ADVANCED SECTION ══════════════════════════════════════════════ */}
            <div ref={el => { sectionRefs.current.advanced = el; }} data-section="advanced">
            {/* 10. ADVANCED */}
            <CollapsibleSection icon={Sliders} title="Advanced" summary="Recording Consent, Privacy, Keypad">
               <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                 <div><Label>Recording Consent Plan</Label><Select value={agent.recording_consent_plan} onChange={(v:any) => updateField('recording_consent_plan', v)} options={['none', 'inform', 'require']} /></div>
                 <div><Label>Keypad Input</Label><Toggle checked={agent.keypad_input_enabled === 1} onChange={(v:any) => updateField('keypad_input_enabled', v?1:0)} label="Allow DTMF keypad input" /></div>
                 <div><Label>HIPAA Mode</Label><Toggle checked={agent.hipaa_enabled === 1} onChange={(v:any) => updateField('hipaa_enabled', v?1:0)} label="Redact PII from logs limits" /></div>
                 <div><Label>PII Redaction</Label><Toggle checked={agent.pii_redaction_enabled === 1} onChange={(v:any) => updateField('pii_redaction_enabled', v?1:0)} label="Redact names, phone numbers" /></div>
               </div>
            </CollapsibleSection>

            </div>{/* end advanced section */}

            {/* ══ LOGS SECTION (Simulation + Health) ═════════════════════════════ */}
            <div ref={el => { sectionRefs.current.logs = el; }} data-section="logs">
            {/* 12. SIMULATION TESTING / TEST PANEL */}
            <CollapsibleSection icon={Activity} title="Simulation Testing" summary="Run real-time voice and text patient scenarios">
              <div style={{ height: '600px', display: 'flex', flexDirection: 'column' }}>
                <TestAgentModal 
                  agent={{ ...agent, name: agent?.agent_name || agent?.name }}
                  agentId={agentId}
                  inline={true}
                  onClose={() => {}}
                />
              </div>
            </CollapsibleSection>

            {/* 13. AGENT HEALTH DASHBOARD */}
            <CollapsibleSection icon={LineChart} title="Agent Health" summary="Latency · Call stats · Eval scores">
              <AgentHealthTab agentId={agentId!} />
            </CollapsibleSection>

            </div>{/* end logs section */}

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

        {showTest && (
          <TestAgentModal
            agent={{ ...agent, name: agent?.agent_name || agent?.name }}
            agentId={agentId}
            onClose={() => setShowTest(false)}
          />
        )}

    </div>
  );
}
