import {
  Activity,
  BookOpen,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Globe,
  Headphones,
  LineChart,
  Mic,
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
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { API_URL } from '../../api/client';
import TestAgentModal from '../../components/TestAgentModal';
import { FIXTURE_AGENTS } from '../../fixtures/data';
import VoiceLibrary from './VoiceLibrary';
import SimulationTab from './agent_detail/SimulationTab';
import AgentHealthTab from './agent_detail/AgentHealthTab';

const ACCENT = '#00D4AA';
const BG = '#0a0a0a';
const CARD_BG = '#0f0f0f';
const BORDER = 'rgba(255,255,255,0.06)';

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

const Input = ({ value, onChange, placeholder, type = 'text', style }: any) => (
  <input
    type={type}
    value={value ?? ''}
    onChange={e => onChange(e.target.value)}
    placeholder={placeholder}
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
    fetch(`${apiBase}/embed/${agentId}/analytics`)
      .then(r => r.json())
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
    wordpress: {
      title: 'WordPress',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          <b style={{ color: '#fff' }}>Option A — Theme Editor:</b><br />
          1. Go to <em>Appearance → Theme File Editor</em><br />
          2. Choose <code style={{ color: color }}>footer.php</code><br />
          3. Paste the code just before <code style={{ color: color }}>&lt;/body&gt;</code><br /><br />
          <b style={{ color: '#fff' }}>Option B — Plugin (easier):</b><br />
          1. Install "WP Headers and Footers" plugin<br />
          2. Go to <em>Settings → WP Headers and Footers</em><br />
          3. Paste code in the Footer Scripts box
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
    shopify: {
      title: 'Shopify',
      steps: (
        <div style={{ fontSize: '13px', lineHeight: 1.8, color: 'rgba(255,255,255,0.7)' }}>
          1. <em>Online Store → Themes → Edit Code</em><br />
          2. Open <code style={{ color: color }}>theme.liquid</code><br />
          3. Paste the code just before <code style={{ color: color }}>&lt;/body&gt;</code><br />
          4. Click <b style={{ color: '#fff' }}>Save file</b>
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
                <Label>Show Lifodial Branding</Label>
                <Toggle checked={agent.embed_show_branding !== false && agent.embed_show_branding !== 0} onChange={(v: any) => updateField('embed_show_branding', v ? 1 : 0)} label="Powered by Lifodial" />
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

          {/* Live Preview */}
          <div>
            <div style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'rgba(255,255,255,0.35)', marginBottom: '12px', fontWeight: 600 }}>Live Preview</div>
            <iframe
              src={`${apiBase}/embed/${agentId}/preview`}
              style={{ width: '100%', height: '280px', border: `1px solid rgba(255,255,255,0.08)`, borderRadius: '12px', background: '#0a0a0a' }}
              title="Widget Preview"
            />
          </div>

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
    gemini: ['gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-1.5-pro'],
    openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
    anthropic: ['claude-3-5-sonnet-20241022', 'claude-3-haiku-20240307'],
    groq: ['llama-3.3-70b-versatile', 'mixtral-8x7b-32768'],
    deepseek: ['deepseek-chat', 'deepseek-reasoner'],
    mistral: ['mistral-large-latest', 'mistral-small-latest'],
  };
  return map[provider] || ['gemini-2.0-flash'];
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

  const toFallbackAgent = useCallback((id?: string) => {
    const found = FIXTURE_AGENTS.find(a => a.id === id) || FIXTURE_AGENTS[0];
    if (!found) return null;

    return {
      id: found.id,
      agent_name: found.name,
      clinic_name: found.clinic_name,
      status: found.status,
      llm_provider: found.llm_provider || 'gemini',
      llm_model: found.llm_model || 'gemini-2.0-flash',
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
      const res = await fetch(`${API_URL}/agents/${agentId}`, { signal: controller.signal });
      if (!res.ok) {
        throw new Error(`Failed to fetch agent (${res.status})`);
      }

      const data = await res.json();
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

  // Fetch models when provider changes.
  // FIX: Only auto-reset model when the current model does NOT belong to the
  // newly selected provider — preserves the user's explicit model choice.
  useEffect(() => {
    if (!agent?.llm_provider) return;
    fetch(`${API_URL}/platform/models/${agent.llm_provider}`)
      .then(r => r.json())
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
    fetch(`${API_URL}/platform/models/${agent.tts_provider}?category=tts`)
      .then(r => r.json())
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
    fetch(`${API_URL}/platform/tts/voices/${agent.tts_provider}${modelParam}`)
      .then(r => r.json())
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
    fetch(`${API_URL}/platform/models/${agent.stt_provider}?category=stt`)
      .then(r => r.json())
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
          const res = await fetch(`${API_URL}/agents/${agentId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ [key]: payloadVal })
          });
          if (res.ok) setSaveStatus('saved');
          else setSaveStatus('error');
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
          const res = await fetch(`${API_URL}/agents/${agentId}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
          });
          if (res.ok) setSaveStatus('saved');
          else setSaveStatus('error');
          setTimeout(() => setSaveStatus(null), 3000);
        } catch {
          setSaveStatus('error');
        }
      }, 1500);
      return next;
    });
  }, [agentId]);

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
      
      const res = await fetch(`${API_URL}/agents/${agentId}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        setSaveStatus('saved');
        // also fetch refreshed value
        fetch(`${API_URL}/agents/${agentId}`).then(r => r.json()).then(setAgent);
      } else {
        setSaveStatus('error');
      }
    } catch {
      setSaveStatus('error');
    }
  };

  const playTTSPreview = async () => {
    try {
      const params = new URLSearchParams({
        provider: agent.tts_provider || 'sarvam',
        voice_id: agent.tts_voice || 'meera',
        text: agent.first_message || 'Hello! I am your clinic receptionist. How can I help you today?',
        pitch: String(agent.tts_pitch ?? 0),
        pace: String(agent.tts_pace ?? 1),
        loudness: String(agent.tts_loudness ?? 1),
      });
      const res = await fetch(`${API_URL}/platform/tts/preview?${params}`);
      if (res.ok) {
        const audioBlob = await res.blob();
        const audio = new Audio(URL.createObjectURL(audioBlob));
        audio.play();
      }
    } catch(e) { console.error('Play failed', e); }
  };

  const sendTestChat = async () => {
    if(!chatIn.trim()) return;
    setChatLog(p => [...p, {from:'user', text: chatIn}]);
    const inputMsg = chatIn;
    setChatIn('');
    try {
      const res = await fetch(`${API_URL}/agents/${agentId}/test`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ message: inputMsg })
      });
      const data = await res.json();
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

      {/* ── CONTENT BODY ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        
        {/* Scrollable Form */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '32px 40px', display: 'flex', justifyContent: 'center' }}>
          <div style={{ width: '100%', maxWidth: '840px', paddingBottom: '60px' }}>
            
            {/* 1. MODEL */}
            <CollapsibleSection icon={Brain} title="Model" summary={`${agent.llm_provider} · ${agent.llm_model}`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div>
                    <Label>Provider</Label>
                    <Select value={agent.llm_provider} onChange={(v:any) => updateField('llm_provider', v)} options={['gemini', 'openai', 'anthropic', 'groq', 'deepseek', 'mistral']} />
                  </div>
                  <div>
                    <Label>Model</Label>
                    <Select value={agent.llm_model} onChange={(v:any) => updateField('llm_model', v)} options={llmModels.length ? llmModels : [agent.llm_model || 'gemini-2.0-flash']} />
                    <Helper>Models are auto-fetched from your API key. <span style={{color: ACCENT, cursor:'pointer', fontSize:'11px'}} onClick={() => { fetch(`${API_URL}/platform/providers/${agent.llm_provider}/fetch-models`, {method:'POST'}).then(r=>r.json()).then(d=>{if(d.models?.length) setLlmModels(d.models)}); }}>⟳ Refresh Models</span></Helper>
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
                      <button style={{ background: 'none', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '4px 8px', fontSize: '11px', color: '#fff', cursor: 'pointer' }}>✨ Compose with AI</button>
                    </div>
                    <Textarea value={agent.first_message} onChange={(v:any) => updateField('first_message', v)} />
                    <Helper>{agent.first_message?.length || 0} characters</Helper>
                  </div>
                </div>
              </div>
              
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <Label>System Prompt</Label>
                  <button style={{ background: 'none', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '4px 8px', fontSize: '11px', color: '#fff', cursor: 'pointer' }}>Generate with LLM</button>
                </div>
                <Textarea value={agent.system_prompt} onChange={(v:any) => updateField('system_prompt', v)} rows={12} mono />
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '12px' }}>
                  {['+ Appointment booking', '+ Clinic hours', '+ Doctor list', '+ Emergency redirect', '+ Language detection'].map(chip => (
                    <div key={chip} style={{ padding: '6px 12px', background: 'rgba(255,255,255,0.05)', border: `1px solid ${BORDER}`, borderRadius: '16px', fontSize: '12px', color: '#ccc', cursor: 'pointer' }} onClick={() => updateField('system_prompt', agent.system_prompt + '\n\n' + chip)}>{chip}</div>
                  ))}
                </div>
              </div>
              
              <div style={{ width: '50%' }}>
                <Label>Max Tokens</Label>
                <Input type="number" value={agent.max_response_tokens} onChange={(v:any) => updateField('max_response_tokens', parseInt(v))} />
                <Helper>Maximum response length per turn</Helper>
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
                  <Select value={agent.tts_provider} onChange={(v:any) => updateField('tts_provider', v)} options={['sarvam', 'elevenlabs', 'openai', 'azure', 'gemini']} />
                </div>
                <div>
                  <Label>Voice</Label>
                  {ttsVoices.length > 0 ? (
                    <Select 
                       value={agent.tts_voice} 
                       onChange={(v:any) => updateField('tts_voice', v)} 
                       options={ttsVoices} 
                    />
                  ) : (
                    <Input value={agent.tts_voice} onChange={(v:any) => updateField('tts_voice', v)} />
                  )}
                </div>
                <div>
                  <Label>Voice Model</Label>
                  <Select value={agent.tts_model} onChange={(v:any) => updateField('tts_model', v)} options={ttsModels.length ? ttsModels : [agent.tts_model || 'bulbul:v3']} />
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '16px', padding: '12px 16px', background: 'rgba(255,255,255,0.03)', borderRadius: '12px', border: `1px solid ${BORDER}` }}>
                <button onClick={playTTSPreview} style={{ padding: '8px 16px', borderRadius: '8px', background: ACCENT, color: '#000', border: 'none', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}><Play size={14} fill="#000" /> Play Sample</button>
                <div style={{ flex: 1, height: '4px', background: 'rgba(255,255,255,0.1)', borderRadius: '2px', position: 'relative' }}>
                  <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', width: '15%', background: ACCENT, borderRadius: '2px' }} />
                </div>
                <span style={{ fontSize: '12px', color: '#888' }}>0:03</span>
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
                <Slider value={agent.tts_speed} min={0.5} max={2.0} onChange={(v:any) => updateField('tts_speed', v)} leftLabel="0.5x Speed" rightLabel="2.0x Speed" />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginTop: '8px' }}>
                <div>
                  <Label>Optimize for Streaming Latency</Label>
                  <Select value={agent.tts_optimize_streaming_latency} onChange={(v:any) => updateField('tts_optimize_streaming_latency', parseInt(v))} options={[0,1,2,3,4]} />
                  <Helper>Higher = faster response, lower quality</Helper>
                </div>
                <div style={{ paddingTop: '16px' }}>
                  <Toggle checked={agent.tts_filler_injection === 1} onChange={(v:any) => updateField('tts_filler_injection', v?1:0)} label="Filler Injection" helper="Adds 'mm-hmm', 'I see' during pauses" />
                </div>
              </div>
            </CollapsibleSection>

            {/* 3. TRANSCRIBER (STT) */}
            <CollapsibleSection icon={Activity} title="Transcriber" summary={`${agent.stt_provider} · ${agent.stt_language}`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '24px' }}>
                <div><Label>Provider</Label><Select value={agent.stt_provider} onChange={(v:any) => updateField('stt_provider', v)} options={['sarvam', 'deepgram', 'whisper']} /></div>
                <div><Label>Model</Label><Select value={agent.stt_model} onChange={(v:any) => updateField('stt_model', v)} options={sttModels.length ? sttModels : [agent.stt_model || 'saarika:v2']} /></div>
                <div><Label>Language</Label><Select value={agent.stt_language} onChange={(v:any) => updateField('stt_language', v)} options={['en-IN', 'hi-IN', 'ta-IN', 'te-IN', 'ar-SA', 'en-US', 'Multilingual (English/Hindi/Regional)', 'auto-detect']} /></div>
              </div>
              <div>
                <Label>Keywords / Boost Terms</Label>
                <TagInput tags={Array.isArray(agent.transcriber_keywords) ? agent.transcriber_keywords : (agent.transcriber_keywords ? (typeof agent.transcriber_keywords === 'string' ? JSON.parse(agent.transcriber_keywords) : []) : [])} onChange={(t:any) => updateField('transcriber_keywords', JSON.stringify(t))} placeholder="Type a word and press enter..." />
                <Helper>Add clinic-specific terms to improve accuracy for names/medications.</Helper>
              </div>
              <div>
                <Label>Fallback Transcribers</Label>
                <button style={{ padding: '8px 12px', background: 'none', border: `1px solid ${BORDER}`, color: '#fff', borderRadius: '8px', fontSize: '13px', cursor: 'pointer' }}>+ Add Fallback</button>
                <Helper>If primary transcriber fails, try these.</Helper>
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

            {/* 6. TOOLS */}
            <CollapsibleSection icon={Wrench} title="Tools" summary={`${Array.isArray(agent.tools_enabled) ? agent.tools_enabled.length : (agent.tools_enabled && typeof agent.tools_enabled === 'string' ? JSON.parse(agent.tools_enabled).length : 0)} tools enabled`}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                {[
                  { id: 'appt_booking', name: '🗓️ Appointment Booking', desc: 'Connect to HIS to check availability & book.' },
                  { id: 'transfer_call', name: '📞 Transfer Call', desc: 'Transfer to human agent.' },
                  { id: 'sms', name: '📱 Send SMS', desc: 'Send appointment confirmation.' },
                  { id: 'status', name: '🔍 Check Appt Status', desc: 'Patients can check existing.' },
                  { id: 'cancel', name: '❌ Cancel Appointment', desc: 'Patients can cancel.' },
                  { id: 'doctors', name: '🩺 Doctor Information', desc: 'Answer questions about doctors.' },
                  { id: 'hours', name: '⏰ Clinic Hours', desc: 'Tell patients about hours.' },
                  { id: 'emergency', name: '🚨 Emergency Redirect', desc: 'Detect emergencies & redirect.' }
                ].map(t => {
                  const enabledTools = Array.isArray(agent.tools_enabled) ? agent.tools_enabled : (agent.tools_enabled && typeof agent.tools_enabled === 'string' ? JSON.parse(agent.tools_enabled) : []);
                  const isEnabled = enabledTools.includes(t.id);
                  return (
                    <div key={t.id} style={{ background: 'rgba(255,255,255,0.02)', border: `1px solid ${BORDER}`, borderRadius: '12px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontSize: '14px', fontWeight: 600, color: '#fff' }}>{t.name}</span>
                        <Toggle checked={isEnabled} onChange={(on:any) => {
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
            </CollapsibleSection>

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

            {/* 8. VOICEMAIL DETECTION */}
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

            {/* 10. ADVANCED */}
            <CollapsibleSection icon={Sliders} title="Advanced" summary="Recording Consent, Privacy, Keypad">
               <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
                 <div><Label>Recording Consent Plan</Label><Select value={agent.recording_consent_plan} onChange={(v:any) => updateField('recording_consent_plan', v)} options={['none', 'inform', 'require']} /></div>
                 <div><Label>Keypad Input</Label><Toggle checked={agent.keypad_input_enabled === 1} onChange={(v:any) => updateField('keypad_input_enabled', v?1:0)} label="Allow DTMF keypad input" /></div>
                 <div><Label>HIPAA Mode</Label><Toggle checked={agent.hipaa_enabled === 1} onChange={(v:any) => updateField('hipaa_enabled', v?1:0)} label="Redact PII from logs limits" /></div>
                 <div><Label>PII Redaction</Label><Toggle checked={agent.pii_redaction_enabled === 1} onChange={(v:any) => updateField('pii_redaction_enabled', v?1:0)} label="Redact names, phone numbers" /></div>
               </div>
            </CollapsibleSection>

            {/* 11. EMBED / WEBSITE WIDGET */}
            <EmbedSection agent={agent} agentId={agentId} updateField={updateField} />

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
            
          </div>
        </div>

        {/* ── TEST PANEL: rendered outside the scroll container as a fixed overlay ── */}
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
                     updateFields({
                       tts_provider: voice.provider,
                       tts_model: voice.model,
                         tts_voice: voice.voice_id || voice.id || voice.name,
                       tts_language: voice.language
                     });
                     setShowVoiceModal(false);
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
