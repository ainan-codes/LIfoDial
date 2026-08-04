/**
 * ConfigureTab — the editable half of My Agent.
 *
 * My Agent used to be 100% read-only ("Read-only — managed by Lifodial team"),
 * with a padlock on every card and no save button anywhere in the file. A clinic
 * admin could see their receptionist but never change a word it said.
 *
 * This tab gives them real control over BEHAVIOUR — greeting, instructions,
 * voice, language, capabilities, call handling — while provider/model internals
 * stay superadmin-only (see `showInternals` in MyAgent.tsx) and the platform's
 * LiveKit/SIP credentials never leave the server at all
 * (backend/routers/agents.py::redact_agent_for_clinic).
 *
 * Everything here maps 1:1 onto PATCH /agents/{id} fields that already exist on
 * AgentPatchPayload, so no new backend surface is required.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Save, RotateCcw } from 'lucide-react';
import fetchWithAuth from '../../api/client';

export interface ConfigurableAgent {
  id: string;
  agent_name?: string;
  first_message?: string;
  system_prompt?: string;
  tts_voice?: string;
  language?: string;
  tts_provider?: string;
  // Read only to ask which languages this agent's transcriber can hear. This tab
  // never renders or writes it — provider choice is a superadmin concern.
  stt_provider?: string;
  llm_temperature?: number;
  max_response_tokens?: number;
  silence_timeout_seconds?: number;
  max_duration_seconds?: number;
  end_call_message?: string;
  can_book_appointments?: boolean;
  can_cancel_appointments?: boolean;
  can_check_availability?: boolean;
  can_transfer_emergency?: boolean;
  emergency_transfer_number?: string;
}

/* The hardcoded LANGUAGES array that used to live here is gone.
 *
 * It carried a comment telling the next reader to "keep it in step with" the
 * backend list — which is an instruction that a duplicate source of truth exists,
 * and it had already drifted once (Odia was missing here while the superadmin
 * editor offered it, so the two surfaces disagreed about what a clinic could
 * pick). The list is fetched from GET /platform/agent/config-options now, the same
 * endpoint the superadmin editor and the creation wizard read, so there is nothing
 * left to keep in step. */

/** The subset of fields this tab owns. Only these are ever PATCHed, so the tab
 *  can never accidentally clobber something it doesn't render. */
type Draft = {
  agent_name: string;
  first_message: string;
  system_prompt: string;
  tts_voice: string;
  language: string;
  llm_temperature: number;
  max_response_tokens: number;
  silence_timeout_seconds: number;
  max_duration_seconds: number;
  end_call_message: string;
  can_book_appointments: boolean;
  can_cancel_appointments: boolean;
  can_check_availability: boolean;
  can_transfer_emergency: boolean;
  emergency_transfer_number: string;
};

function toDraft(a: ConfigurableAgent): Draft {
  return {
    agent_name: a.agent_name ?? '',
    first_message: a.first_message ?? '',
    system_prompt: a.system_prompt ?? '',
    tts_voice: a.tts_voice ?? '',
    language: a.language ?? 'en-IN',
    llm_temperature: a.llm_temperature ?? 0.3,
    max_response_tokens: a.max_response_tokens ?? 120,
    silence_timeout_seconds: a.silence_timeout_seconds ?? 10,
    max_duration_seconds: a.max_duration_seconds ?? 300,
    end_call_message: a.end_call_message ?? '',
    can_book_appointments: a.can_book_appointments ?? true,
    can_cancel_appointments: a.can_cancel_appointments ?? true,
    can_check_availability: a.can_check_availability ?? true,
    can_transfer_emergency: a.can_transfer_emergency ?? true,
    emergency_transfer_number: a.emergency_transfer_number ?? '',
  };
}

// ── Small styled primitives (match MyAgent's dark card look) ─────────────────
const card: React.CSSProperties = {
  background: '#111', borderRadius: 12, border: '1px solid #1A1A1A',
  padding: '20px 22px', marginBottom: 16,
};
const title: React.CSSProperties = { margin: '0 0 4px', fontSize: 14, fontWeight: 600, color: '#fff' };
const subtitle: React.CSSProperties = { margin: '0 0 16px', fontSize: 12, color: '#666' };
const label: React.CSSProperties = {
  display: 'block', fontSize: 11, fontWeight: 600, color: '#888',
  textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6,
};
const inputBase: React.CSSProperties = {
  width: '100%', padding: '9px 12px', fontSize: 13, borderRadius: 8,
  background: '#0C0C0C', border: '1px solid #222', color: '#eee', outline: 'none',
};

function Field({ children, hint, labelText }: { children: React.ReactNode; hint?: string; labelText: string }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label style={label}>{labelText}</label>
      {children}
      {hint && <p style={{ fontSize: 11, color: '#555', margin: '6px 0 0' }}>{hint}</p>}
    </div>
  );
}

function Toggle({ value, onChange, labelText, hint }: {
  value: boolean; onChange: (v: boolean) => void; labelText: string; hint?: string;
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 0', borderBottom: '1px solid #1A1A1A' }}>
      <div style={{ marginRight: 20 }}>
        <p style={{ margin: 0, fontSize: 13, color: '#ddd', fontWeight: 500 }}>{labelText}</p>
        {hint && <p style={{ margin: '2px 0 0', fontSize: 11, color: '#555' }}>{hint}</p>}
      </div>
      <button
        type="button"
        onClick={() => onChange(!value)}
        aria-pressed={value}
        style={{
          position: 'relative', width: 42, height: 23, borderRadius: 9999, flexShrink: 0,
          border: 'none', cursor: 'pointer',
          background: value ? '#3ECF8E' : '#2A2A2A', transition: 'background 0.2s',
        }}
      >
        <span style={{
          position: 'absolute', top: 2, left: value ? 21 : 2,
          width: 19, height: 19, borderRadius: '50%', background: '#fff',
          transition: 'left 0.2s',
        }} />
      </button>
    </div>
  );
}

export default function ConfigureTab({ agent, onSaved }: {
  agent: ConfigurableAgent;
  onSaved: () => void;
}) {
  const initial = useMemo(() => toDraft(agent), [agent]);
  const [draft, setDraft] = useState<Draft>(initial);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState('');

  // Re-seed when a fresh agent arrives (e.g. after Refresh), but only while the
  // user has no unsaved edits — otherwise a background refetch would wipe them.
  const dirtyFields = useMemo(
    () => (Object.keys(initial) as (keyof Draft)[]).filter(k => draft[k] !== initial[k]),
    [draft, initial],
  );
  const isDirty = dirtyFields.length > 0;

  useEffect(() => {
    if (!isDirty) setDraft(toDraft(agent));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, agent.agent_name, agent.first_message, agent.system_prompt]);

  // Voices for whichever TTS provider this agent uses. Fetched by provider id so
  // the clinic sees real, selectable voice names without this tab hardcoding any
  // provider's catalogue. GET /platform/tts/voices/{provider} answers
  // { voices: [{ id, voice_id, name, gender, ... }] } — but some branches return a
  // bare array, so both shapes are handled.
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => {
    const provider = agent.tts_provider || 'sarvam';
    let cancelled = false;
    fetchWithAuth(`/platform/tts/voices/${provider}`)
      .then((res: any) => {
        if (cancelled) return;
        const list: any[] = Array.isArray(res) ? res : (res?.voices ?? []);
        setVoices(
          list
            .map(v => {
              if (typeof v === 'string') return { id: v, name: v };
              const id = v?.id ?? v?.voice_id ?? '';
              return { id, name: v?.name || id };
            })
            .filter(v => v.id),
        );
      })
      // A failed voice fetch must not block editing — the field falls back to a
      // free-text input below.
      .catch(() => { if (!cancelled) setVoices([]); });
    return () => { cancelled = true; };
  }, [agent.tts_provider]);

  // The languages this agent's OWN providers genuinely support, from the one
  // endpoint that knows. Asked per-agent rather than fetched once for the app,
  // because the answer depends on the transcriber and voice provider this agent is
  // on — a platform-wide list is precisely what drifted before.
  const [languages, setLanguages] = useState<{ code: string; name: string }[]>([]);
  useEffect(() => {
    const q = new URLSearchParams({
      stt_provider: agent.stt_provider || '',
      tts_provider: agent.tts_provider || '',
      language: agent.language || '',
    });
    let cancelled = false;
    fetchWithAuth(`/platform/agent/config-options?${q}`)
      .then((res: any) => {
        if (!cancelled) setLanguages(Array.isArray(res?.languages) ? res.languages : []);
      })
      // A failed fetch must not strand the clinic on a dropdown with no options:
      // fall back to whatever the agent is already set to, so the field still shows
      // the truth and a save does not silently change the language.
      .catch(() => { if (!cancelled) setLanguages([]); });
    return () => { cancelled = true; };
  }, [agent.stt_provider, agent.tts_provider, agent.language]);

  const set = <K extends keyof Draft>(key: K) => (v: Draft[K]) =>
    setDraft(d => ({ ...d, [key]: v }));

  const handleSave = async () => {
    setSaving(true);
    setError('');
    try {
      // Send ONLY what changed. A full-object PATCH would rewrite fields this tab
      // doesn't render, and for a clinic admin would also include the redacted
      // credential keys — which the backend now (correctly) rejects with a 403.
      const body: Partial<Draft> = {};
      for (const k of dirtyFields) (body as any)[k] = draft[k];

      if (Object.keys(body).length === 0) {
        setSavedAt(Date.now());
        return;
      }
      await fetchWithAuth(`/agents/${agent.id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
      setSavedAt(Date.now());
      onSaved();
    } catch (e) {
      setError((e as Error).message || 'Could not save changes');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => { setDraft(initial); setError(''); };

  return (
    <div>
      {/* Sticky action bar so Save is reachable from any scroll position. */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20,
        padding: '12px 16px', borderRadius: 10,
        background: isDirty ? 'rgba(245,158,11,0.08)' : '#111',
        border: `1px solid ${isDirty ? 'rgba(245,158,11,0.3)' : '#1A1A1A'}`,
        position: 'sticky', top: 12, zIndex: 5, backdropFilter: 'blur(6px)',
      }}>
        <div style={{ flex: 1 }}>
          {error ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, color: '#ef4444' }}>
              <AlertCircle size={14} /> {error}
            </span>
          ) : isDirty ? (
            <span style={{ fontSize: 13, color: '#F59E0B' }}>
              {dirtyFields.length} unsaved change{dirtyFields.length === 1 ? '' : 's'}
            </span>
          ) : savedAt ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, color: '#3ECF8E' }}>
              <CheckCircle2 size={14} /> Saved — changes apply to the next call
            </span>
          ) : (
            <span style={{ fontSize: 13, color: '#666' }}>Edit your receptionist's behaviour</span>
          )}
        </div>

        {isDirty && (
          <button
            onClick={handleReset}
            style={{
              display: 'flex', alignItems: 'center', gap: 6, padding: '8px 14px',
              borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: 'pointer',
              background: 'transparent', border: '1px solid #2A2A2A', color: '#888',
            }}
          >
            <RotateCcw size={13} /> Discard
          </button>
        )}
        <button
          onClick={handleSave}
          disabled={saving || !isDirty}
          style={{
            display: 'flex', alignItems: 'center', gap: 7, padding: '8px 18px',
            borderRadius: 8, fontSize: 13, fontWeight: 600, border: 'none',
            background: isDirty ? '#3ECF8E' : '#1A1A1A',
            color: isDirty ? '#000' : '#555',
            cursor: saving || !isDirty ? 'not-allowed' : 'pointer',
            opacity: saving ? 0.7 : 1,
          }}
        >
          <Save size={14} /> {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, alignItems: 'start' }}>
        {/* ── Identity & greeting ── */}
        <div style={card}>
          <h3 style={title}>Identity &amp; Greeting</h3>
          <p style={subtitle}>The first thing every caller hears.</p>

          <Field labelText="Agent Name">
            <input style={inputBase} value={draft.agent_name}
                   onChange={e => set('agent_name')(e.target.value)} placeholder="Receptionist" />
          </Field>

          <Field labelText="Greeting Message"
                 hint="Spoken aloud the moment the call connects. Keep it to 1–2 sentences.">
            <textarea style={{ ...inputBase, minHeight: 84, resize: 'vertical', lineHeight: 1.5 }}
                      value={draft.first_message}
                      onChange={e => set('first_message')(e.target.value)}
                      placeholder="Namaste! Thank you for calling. How may I help you today?" />
          </Field>

          <Field labelText="Goodbye Message" hint="Spoken just before the call ends.">
            <input style={inputBase} value={draft.end_call_message}
                   onChange={e => set('end_call_message')(e.target.value)}
                   placeholder="Thank you for calling. Goodbye!" />
          </Field>
        </div>

        {/* ── Voice ── */}
        <div style={card}>
          <h3 style={title}>Voice &amp; Language</h3>
          <p style={subtitle}>How your receptionist sounds.</p>

          <Field labelText="Language" hint="The language the agent speaks by default. It can still switch mid-call if the caller does.">
            <select style={{ ...inputBase, cursor: 'pointer' }} value={draft.language}
                    onChange={e => set('language')(e.target.value)}>
              {/* Keep the current value selectable even if the fetch failed or the
                  agent sits on a legacy code, so opening this tab can never
                  silently change the agent's language just by rendering. */}
              {!languages.some(l => l.code === draft.language) && draft.language && (
                <option value={draft.language}>{draft.language}</option>
              )}
              {languages.map(l => <option key={l.code} value={l.code}>{l.name} ({l.code})</option>)}
            </select>
          </Field>

          <Field labelText="Voice">
            {voices.length > 0 ? (
              <select style={{ ...inputBase, cursor: 'pointer' }} value={draft.tts_voice}
                      onChange={e => set('tts_voice')(e.target.value)}>
                {/* Keep the current value selectable even if the provider's list
                    doesn't contain it — otherwise opening this tab would silently
                    change the agent's voice to whatever sorts first. */}
                {draft.tts_voice && !voices.some(v => v.id === draft.tts_voice) && (
                  <option value={draft.tts_voice}>{draft.tts_voice}</option>
                )}
                {voices.map(v => (
                  <option key={v.id} value={v.id}>{v.name}</option>
                ))}
              </select>
            ) : (
              <input style={inputBase} value={draft.tts_voice}
                     onChange={e => set('tts_voice')(e.target.value)} placeholder="meera" />
            )}
          </Field>
        </div>

        {/* ── Instructions ── */}
        <div style={{ ...card, gridColumn: '1 / -1' }}>
          <h3 style={title}>Instructions</h3>
          <p style={subtitle}>
            Tell the receptionist how to behave — tone, what to ask, what never to say.
            Clinic details and doctor availability are added automatically.
          </p>
          <textarea
            style={{ ...inputBase, minHeight: 180, resize: 'vertical', lineHeight: 1.6, fontFamily: 'inherit' }}
            value={draft.system_prompt}
            onChange={e => set('system_prompt')(e.target.value)}
            placeholder="You are a warm, efficient receptionist for our clinic. Always confirm the patient's name and phone number before booking…"
          />
        </div>

        {/* ── Capabilities ── */}
        <div style={card}>
          <h3 style={title}>What It Can Do</h3>
          <p style={subtitle}>Turn individual abilities on or off.</p>
          <Toggle labelText="Book appointments" hint="Collect details and confirm a slot"
                  value={draft.can_book_appointments} onChange={set('can_book_appointments')} />
          <Toggle labelText="Cancel appointments"
                  value={draft.can_cancel_appointments} onChange={set('can_cancel_appointments')} />
          <Toggle labelText="Check availability" hint="Read out open slots for a doctor"
                  value={draft.can_check_availability} onChange={set('can_check_availability')} />
          <Toggle labelText="Transfer emergencies" hint="Hand urgent calls to a human"
                  value={draft.can_transfer_emergency} onChange={set('can_transfer_emergency')} />

          {draft.can_transfer_emergency && (
            <div style={{ marginTop: 16 }}>
              <Field labelText="Emergency Transfer Number"
                     hint="Urgent calls are forwarded here.">
                <input style={inputBase} value={draft.emergency_transfer_number}
                       onChange={e => set('emergency_transfer_number')(e.target.value)}
                       placeholder="+91 98765 43210" />
              </Field>
            </div>
          )}
        </div>

        {/* ── Conversation handling ── */}
        <div style={card}>
          <h3 style={title}>Call Handling</h3>
          <p style={subtitle}>How the agent paces and ends a conversation.</p>

          <Field labelText={`Reply Style — ${draft.llm_temperature <= 0.3 ? 'Focused' : draft.llm_temperature <= 0.6 ? 'Balanced' : 'Creative'} (${draft.llm_temperature.toFixed(1)})`}
                 hint="Lower is more predictable and consistent; higher is more varied.">
            <input type="range" min={0} max={1} step={0.1} value={draft.llm_temperature}
                   onChange={e => set('llm_temperature')(Number(e.target.value))}
                   style={{ width: '100%', accentColor: '#3ECF8E' }} />
          </Field>

          <Field labelText="Reply Length (max words ≈)"
                 hint="Shorter replies feel faster on a phone call.">
            <input type="number" min={50} max={2000} step={10} style={inputBase}
                   value={draft.max_response_tokens}
                   onChange={e => set('max_response_tokens')(Number(e.target.value))} />
          </Field>

          <Field labelText="Hang Up After Silence (seconds)"
                 hint="If the caller says nothing for this long, the agent ends the call.">
            <input type="number" min={5} max={120} style={inputBase}
                   value={draft.silence_timeout_seconds}
                   onChange={e => set('silence_timeout_seconds')(Number(e.target.value))} />
          </Field>

          <Field labelText="Maximum Call Length (seconds)">
            <input type="number" min={60} max={3600} step={30} style={inputBase}
                   value={draft.max_duration_seconds}
                   onChange={e => set('max_duration_seconds')(Number(e.target.value))} />
          </Field>
        </div>
      </div>
    </div>
  );
}
