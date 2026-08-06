import {
    AlertCircle,
    Brain,
    CalendarCheck,
    CheckCircle,
    Circle,
    Clock,
    Copy,
    FlaskConical,
    Globe,
    Headphones,
    Mic,
    MoreVertical,
    Pause,
    Phone,
    Trash2,
    TrendingUp,
    Zap
} from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import fetchWithAuth from '../api/client';
import { AgentStatus, FixtureAgent } from '../fixtures/data';

/**
 * The agent card, shared by Superadmin → Agents and the clinic's own My Agent.
 *
 * It lives here rather than in either page because the two MUST show the same
 * thing: a clinic admin looking at their receptionist should see exactly what the
 * platform team sees for that agent — same status badge, greeting preview,
 * language, voice/model lines and stats — with the editing removed.
 *
 * `readOnly` is what makes it safe for a clinic. It drops the Edit button, the
 * dropdown (duplicate/pause/delete) and the whole-card click-through to the
 * editor, leaving only the two test buttons. It is a UI affordance, not the
 * security boundary: the agent-config write endpoints reject clinic-role tokens
 * server-side regardless of what this renders — see backend/routers/agents.py
 * (`_authorize_agent_patch` for PATCH, `_require_agent_write_access` for the
 * prompt/greeting/avatar writes).
 */

// ── Status config ────────────────────────────────────────────────────────────

export const STATUS_CONFIG: Record<AgentStatus, { color: string; bg: string; label: string; icon: React.ReactNode }> = {
  ACTIVE:     { color: '#22C55E', bg: 'rgba(34,197,94,0.12)',   label: 'Active',      icon: <CheckCircle size={11} /> },
  CONFIGURED: { color: '#FBBF24', bg: 'rgba(251,191,36,0.12)',  label: 'Configured',  icon: <Clock size={11} /> },
  ERROR:      { color: '#F87171', bg: 'rgba(248,113,113,0.12)', label: 'Error',       icon: <AlertCircle size={11} /> },
  INACTIVE:   { color: '#6B7280', bg: 'rgba(107,114,128,0.12)', label: 'Inactive',    icon: <Circle size={11} /> },
};

// ── Dropdown menu (superadmin only — never rendered when readOnly) ───────────

function AgentDropdown({ agent, onDelete }: { agent: FixtureAgent; onDelete: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: '#666', padding: '4px', borderRadius: '6px',
          display: 'flex', alignItems: 'center',
          transition: 'background 0.15s',
        }}
        onMouseEnter={e => { e.currentTarget.style.background = '#2A2A2A'; e.currentTarget.style.color = '#fff'; }}
        onMouseLeave={e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.color = '#666'; }}
      >
        <MoreVertical size={15} />
      </button>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: '100%', zIndex: 99,
          background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '10px',
          padding: '4px', minWidth: '148px', boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}>
          {[
            { icon: <Copy size={13} />, label: 'Duplicate', action: () => {} },
            { icon: <Pause size={13} />, label: 'Pause Agent', action: () => {} },
            { icon: <Trash2 size={13} />, label: 'Delete', action: () => { onDelete(agent.id); setOpen(false); }, danger: true },
          ].map(({ icon, label, action, danger }) => (
            <button
              key={label}
              onClick={action}
              style={{
                width: '100%', display: 'flex', alignItems: 'center', gap: '8px',
                padding: '8px 12px', borderRadius: '7px', border: 'none',
                background: 'none', cursor: 'pointer', fontSize: '13px',
                color: danger ? '#F87171' : '#A1A1A1', textAlign: 'left',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = '#2A2A2A'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'none'; }}
            >
              {icon} {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Agent Card ───────────────────────────────────────────────────────────────

export function AgentCard({
  agent, onEdit, onTest, onDelete, onWebCall, onPhoneCall, showDisambiguator, readOnly = false,
}: {
  agent: FixtureAgent;
  onTest: () => void;
  onWebCall: () => void;
  onPhoneCall: () => void;
  /** Required unless readOnly. */
  onEdit?: (id: string) => void;
  /** Required unless readOnly. */
  onDelete?: (id: string) => void;
  showDisambiguator?: boolean;
  /** Clinic-admin view: no Edit, no dropdown, no click-through to the editor. */
  readOnly?: boolean;
}) {
  const st = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.INACTIVE;
  const openEditor = () => { if (!readOnly && onEdit) onEdit(agent.id); };

  return (
    <div
      onClick={openEditor}
      style={{
        background: '#1A1A1A', border: '1px solid #2E2E2E', borderRadius: '14px',
        padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px',
        transition: 'border-color 0.2s, box-shadow 0.2s',
        cursor: readOnly ? 'default' : 'pointer',
      }}
      onMouseEnter={e => {
        if (readOnly) return;
        (e.currentTarget as HTMLDivElement).style.borderColor = '#3ECF8E44';
        (e.currentTarget as HTMLDivElement).style.boxShadow = '0 0 0 1px #3ECF8E22';
      }}
      onMouseLeave={e => {
        if (readOnly) return;
        (e.currentTarget as HTMLDivElement).style.borderColor = '#2E2E2E';
        (e.currentTarget as HTMLDivElement).style.boxShadow = 'none';
      }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div
          style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            background: st.bg, borderRadius: '20px', padding: '3px 10px 3px 6px',
            color: st.color, fontSize: '11px', fontWeight: 600,
          }}
        >
          <span style={{ color: st.color }}>{st.icon}</span>
          {st.label}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {!readOnly && (
            <button
              id={`edit-agent-${agent.id}`}
              onClick={(e) => { e.stopPropagation(); openEditor(); }}
              style={{
                padding: '5px 12px', borderRadius: '7px', fontSize: '12px', fontWeight: 500,
                border: '1px solid #2E2E2E', background: 'none', color: '#A1A1A1',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = '#3ECF8E'; e.currentTarget.style.color = '#3ECF8E'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = '#2E2E2E'; e.currentTarget.style.color = '#A1A1A1'; }}
            >
              Edit
            </button>
          )}
          <button
            id={`test-agent-${agent.id}`}
            onClick={(e) => { e.stopPropagation(); onTest(); }}
            style={{
              padding: '5px 12px', borderRadius: '7px', fontSize: '12px', fontWeight: 600,
              border: '1px solid #3ECF8E44', background: 'rgba(62,207,142,0.08)', color: '#3ECF8E',
              cursor: 'pointer', transition: 'all 0.15s', display: 'flex', alignItems: 'center', gap: '4px',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(62,207,142,0.16)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(62,207,142,0.08)'; }}
          >
            <FlaskConical size={11} /> Test
          </button>
          {!readOnly && onDelete && (
            <div onClick={e => e.stopPropagation()}>
              <AgentDropdown agent={agent} onDelete={onDelete} />
            </div>
          )}
        </div>
      </div>

      {/* Agent name + clinic */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
          <div
            style={{
              width: '32px', height: '32px', borderRadius: '8px',
              background: 'rgba(62,207,142,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center',
              border: '1px solid rgba(62,207,142,0.2)', flexShrink: 0,
            }}
          >
            <Headphones size={16} color="#3ECF8E" />
          </div>
          <div>
            <div style={{ fontSize: '15px', fontWeight: 600, color: '#fff', lineHeight: 1.3 }}>
              {agent.name}
              {showDisambiguator && (
                <span style={{ fontSize: '11px', fontWeight: 500, color: '#666', marginLeft: '6px' }}>
                  #{agent.id.slice(0, 6)}
                </span>
              )}
            </div>
            <div style={{ fontSize: '12px', color: '#666' }}>
              {agent.clinic_name}
              {showDisambiguator && agent.created_at && (
                <span> · created {new Date(agent.created_at).toLocaleDateString()}</span>
              )}
            </div>
          </div>
        </div>

        {/* First message preview */}
        <div
          style={{
            background: '#111', border: '1px solid #222', borderRadius: '8px',
            padding: '10px 12px', marginTop: '10px',
            fontSize: '12px', color: '#888', lineHeight: 1.6,
            fontStyle: 'italic', maxHeight: '52px', overflow: 'hidden',
          }}
        >
          "{(agent.first_message || '').slice(0, 100)}{(agent.first_message || '').length > 100 ? '…"' : '"'}"
        </div>
      </div>

      {/* Tech info pills */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <InfoPill icon={<Phone size={11} />} text={agent.ai_number} />
          <InfoPill icon={<Globe size={11} />} text={agent.languages.join(' + ')} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <InfoPill icon={<Mic size={11} />} text={`${agent.tts_provider} · ${agent.tts_model} · ${agent.tts_voice}`} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* LLM model — show exact provider + model from DB */}
          <InfoPill icon={<Brain size={11} />} text={`${agent.llm_provider || 'gemini'} · ${agent.llm_model || agent.model || 'gemini-2.5-flash'}`} />
        </div>
      </div>

      {/* Stats row */}
      <div
        style={{
          borderTop: '1px solid #222', paddingTop: '14px',
          display: 'grid', gridTemplateColumns: '1fr 1px 1fr', gap: '0',
        }}
      >
        <StatItem icon={<Phone size={12} color="#3ECF8E" />} label="Calls today" value={agent.calls_today > 0 ? agent.calls_today.toString() : '—'} />
        <div style={{ background: '#2E2E2E' }} />
        <StatItem icon={<CalendarCheck size={12} color="#A78BFA" />} label="Bookings" value={agent.bookings_today > 0 ? agent.bookings_today.toString() : '—'} right />
      </div>
      <div
        style={{
          display: 'grid', gridTemplateColumns: '1fr 1px 1fr', gap: '0',
        }}
      >
        <StatItem icon={<Zap size={12} color="#FBBF24" />} label="Avg latency" value={agent.avg_latency_ms > 0 ? `${agent.avg_latency_ms}ms` : '—'} />
        <div style={{ background: '#2E2E2E' }} />
        <StatItem icon={<TrendingUp size={12} color="#22C55E" />} label="Resolution" value={agent.resolution_rate > 0 ? `${agent.resolution_rate}%` : '—'} right />
      </div>

      {/* Web Call + Phone Call buttons — the one interactive capability a clinic keeps */}
      <div style={{
        display: 'flex', gap: '8px', borderTop: '1px solid #222', paddingTop: '14px',
      }}>
        <button
          onClick={(e) => { e.stopPropagation(); onWebCall(); }}
          title="Test via browser (no phone needed)"
          style={{
            flex: 1, padding: '7px 0', borderRadius: '8px', fontSize: '12px', fontWeight: 600,
            border: '1px solid #3B82F644', background: 'rgba(59,130,246,0.08)', color: '#3B82F6',
            cursor: 'pointer', transition: 'all 0.15s', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '5px',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(59,130,246,0.16)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(59,130,246,0.08)'; }}
        >
          🌐 Web Call
        </button>
        <button
          onClick={(e) => { e.stopPropagation(); onPhoneCall(); }}
          title="Make outbound phone call"
          disabled={!agent.sip_provider}
          style={{
            flex: 1, padding: '7px 0', borderRadius: '8px', fontSize: '12px', fontWeight: 600,
            border: '1px solid #A78BFA44', background: 'rgba(167,139,250,0.08)', color: '#A78BFA',
            cursor: agent.sip_provider ? 'pointer' : 'not-allowed', transition: 'all 0.15s',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '5px',
            opacity: agent.sip_provider ? 1 : 0.4,
          }}
          onMouseEnter={e => { if (agent.sip_provider) e.currentTarget.style.background = 'rgba(167,139,250,0.16)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(167,139,250,0.08)'; }}
        >
          📞 Phone Call
        </button>
      </div>
    </div>
  );
}

export function InfoPill({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '5px', color: '#666', fontSize: '12px' }}>
      <span style={{ color: '#555' }}>{icon}</span>
      <span>{text}</span>
    </div>
  );
}

export function StatItem({ icon, label, value, right }: { icon: React.ReactNode; label: string; value: string; right?: boolean }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', padding: right ? '0 0 0 16px' : '0 16px 0 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
        {icon}
        <span style={{ fontSize: '11px', color: '#555' }}>{label}</span>
      </div>
      <span style={{ fontSize: '15px', fontWeight: 600, color: value === '—' ? '#444' : '#fff' }}>{value}</span>
    </div>
  );
}

// ── Outbound phone-call dialog ───────────────────────────────────────────────
//
// Shared for the same reason as the card: "Phone Call must work identically to
// Superadmin's flow" is only true if it IS the same flow. POST
// /agents/{id}/outbound-call is CurrentUser + require_owns, so a clinic token
// dials its own agent exactly as a superadmin token does.

export function PhoneCallModal({ agent, onClose }: { agent: FixtureAgent; onClose: () => void }) {
  const [phoneNumber, setPhoneNumber] = useState('');
  const [dialing, setDialing] = useState(false);

  const dial = () => {
    const num = phoneNumber.trim();
    if (!num) return;
    setDialing(true);
    const fullNumber = num.startsWith('+') ? num : `+91${num.replace(/\s/g, '')}`;
    fetchWithAuth(`/agents/${agent.id}/outbound-call`, {
      method: 'POST',
      body: JSON.stringify({ phone_number: fullNumber }),
    }).then(data => {
      alert(data.message || 'Call initiated');
      onClose();
    }).catch((e) => {
      // Surface the server's reason instead of always blaming SIP config: this
      // endpoint also answers 503 when the voice worker is still waking up.
      alert((e as Error).message || 'Could not start the call.');
      onClose();
    });
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9998,
      background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(6px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: '#0F0F0F', border: '1px solid #1A1A1A', borderRadius: '14px',
        width: '420px', maxWidth: '90vw', padding: '24px',
      }}>
        <h3 style={{ fontSize: '16px', fontWeight: 700, color: '#fff', margin: '0 0 4px' }}>
          📞 Make Outbound Call
        </h3>
        <p style={{ fontSize: '12px', color: '#666', margin: '0 0 20px' }}>
          Call from: {agent.name}
        </p>
        <p style={{ fontSize: '12px', color: '#888', margin: '0 0 4px' }}>
          AI Number: <span style={{ color: '#3ECF8E', fontFamily: 'monospace' }}>{agent.ai_number}</span>
        </p>

        <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, color: '#888', margin: '16px 0 6px' }}>
          Dial number
        </label>
        <div style={{ display: 'flex', gap: '8px' }}>
          <span style={{
            padding: '8px 12px', borderRadius: '8px',
            background: '#111', border: '1px solid #2E2E2E',
            color: '#888', fontSize: '13px',
          }}>
            🇮🇳 +91
          </span>
          <input
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
            placeholder="98765 43210"
            style={{
              flex: 1, padding: '8px 12px', borderRadius: '8px',
              background: '#111', border: '1px solid #2E2E2E',
              color: '#ccc', fontSize: '13px', outline: 'none',
            }}
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '24px' }}>
          <button
            onClick={onClose}
            style={{
              padding: '8px 16px', borderRadius: '8px',
              background: 'transparent', border: '1px solid #2E2E2E',
              color: '#888', cursor: 'pointer', fontSize: '13px',
            }}
          >Cancel</button>
          <button
            onClick={dial}
            disabled={!phoneNumber.trim() || dialing}
            style={{
              padding: '8px 20px', borderRadius: '8px',
              background: '#3ECF8E', color: '#000', border: 'none',
              cursor: phoneNumber.trim() && !dialing ? 'pointer' : 'not-allowed',
              fontSize: '13px', fontWeight: 600,
              opacity: phoneNumber.trim() && !dialing ? 1 : 0.5,
            }}
          >{dialing ? 'Dialing…' : '📞 Call Now'}</button>
        </div>
      </div>
    </div>
  );
}

export default AgentCard;
