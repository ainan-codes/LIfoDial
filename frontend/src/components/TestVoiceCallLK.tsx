import { useEffect, useRef, useState } from 'react';
import {
  LiveKitRoom,
  useVoiceAssistant,
  BarVisualizer,
  RoomAudioRenderer,
  VoiceAssistantControlBar,
} from '@livekit/components-react';
import '@livekit/components-styles';
import fetchWithAuth from '../api/client';

/**
 * TestVoiceCallLK — the "Test Agent" voice tab, running on the SAME real-time
 * LiveKit + Pipecat pipeline as production web/phone calls (test_mode=true).
 *
 * Restored in this file (audit regression fix):
 *  - Live TRANSCRIPT of the agent's speech (chat bubbles), which the pre-LiveKit
 *    WebSocket harness used to show and the LiveKit migration (2b22342) dropped.
 *  - A real TIMEOUT + error/retry on "Connecting…" so a cold-started worker or a
 *    failed connect never leaves an indefinite spinner.
 *  - Listen-only fallback when there's no microphone.
 *  - Mobile-responsive layout (transcript scrolls; controls wrap) at ~375px+.
 */

// After this long still "connecting", warn the user it may be a cold start.
const SLOW_MS = 12_000;
// After this long, stop pretending and offer a retry (accommodates free-tier
// worker cold starts, which can take ~30–60s, without being infinite).
const TIMEOUT_MS = 45_000;

export default function TestVoiceCallLK({
  agent,
  agentId,
  agentName,
  onClose,
}: {
  agent?: any;
  agentId?: string;
  agentName?: string;
  onClose?: () => void;
}) {
  const [token, setToken] = useState('');
  const [wsUrl, setWsUrl] = useState('');
  const [phase, setPhase] = useState<'connecting' | 'live' | 'error' | 'demo'>('connecting');
  const [error, setError] = useState('');
  // No mic is NOT fatal — connect listen-only so the agent can still be heard/read.
  const [micAvailable, setMicAvailable] = useState(true);
  // Connecting is taking a while (likely a free-tier cold start).
  const [slow, setSlow] = useState(false);
  // Bumping this re-runs the connect effect (Retry).
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setPhase('connecting');
    setError('');
    setSlow(false);

    const slowTimer = setTimeout(() => { if (!cancelled) setSlow(true); }, SLOW_MS);
    const hardTimer = setTimeout(() => {
      if (!cancelled) {
        setError('Connection timed out. The voice service may be starting up (cold start). Please try again.');
        setPhase('error');
      }
    }, TIMEOUT_MS);

    const connect = async () => {
      try {
        // Try to grab the mic first (so LiveKit publishes audio for STT). Missing
        // or denied mic is non-fatal → listen-only.
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          stream.getTracks().forEach(t => t.stop());
          if (!cancelled) setMicAvailable(true);
        } catch (micErr: any) {
          if (cancelled) return;
          console.warn('Mic unavailable — connecting listen-only:', micErr?.name || micErr);
          setMicAvailable(false);
        }

        // Same endpoint as real web calls — test_mode flags no-billing and lets the
        // worker bypass the publish gate so unpublished agents are testable.
        const data = await fetchWithAuth(`/agents/${agentId}/web-call-token?test_mode=true`, { method: 'POST' });
        if (cancelled) return;
        if (data?.demo || !data?.token) {
          setPhase('demo');
          return;
        }
        setToken(data.token);
        setWsUrl(data.wsUrl);
        setPhase('live');
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.message || 'Failed to start the test call.');
        setPhase('error');
      } finally {
        clearTimeout(hardTimer);
        if (!cancelled) clearTimeout(slowTimer);
      }
    };
    connect();
    return () => { cancelled = true; clearTimeout(slowTimer); clearTimeout(hardTimer); };
  }, [agentId, attempt]);

  const retry = () => setAttempt(a => a + 1);

  const shell = (children: React.ReactNode) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '14px', padding: '20px', textAlign: 'center', color: '#fff' }}>
      {children}
    </div>
  );

  if (phase === 'connecting') {
    return shell(
      <>
        <div style={{ width: 34, height: 34, border: '3px solid #2e2e2e', borderTopColor: '#3ECF8E', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        <div style={{ fontSize: 14, fontWeight: 600 }}>Connecting to {agentName || 'agent'}…</div>
        <div style={{ fontSize: 12, color: '#666' }}>Setting up the live pipeline</div>
        {slow && (
          <div style={{ fontSize: 12, color: '#F59E0B', maxWidth: 320, lineHeight: 1.5, marginTop: 4 }}>
            Taking longer than expected — the voice service may be starting up (free-tier cold start can take ~30–60s). Please wait…
          </div>
        )}
      </>
    );
  }

  if (phase === 'error') {
    return shell(
      <>
        <div style={{ fontSize: 28 }}>⚠️</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Couldn’t start the test call</div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 300 }}>{error}</div>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button onClick={retry} style={{ padding: '6px 16px', borderRadius: 8, border: 'none', background: '#3ECF8E', color: '#000', fontWeight: 600, cursor: 'pointer' }}>Retry</button>
          {onClose && <button onClick={onClose} style={{ padding: '6px 16px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#fff', cursor: 'pointer' }}>Close</button>}
        </div>
      </>
    );
  }

  if (phase === 'demo') {
    return shell(
      <>
        <div style={{ fontSize: 28 }}>🔌</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>LiveKit not configured</div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 320 }}>
          Set <code>LIVEKIT_URL</code>, <code>LIVEKIT_API_KEY</code>, and <code>LIVEKIT_API_SECRET</code> in
          <code>.env</code>, and run the agent worker (<code>python -m backend.agent start</code>) to test voice on the real pipeline.
        </div>
      </>
    );
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <LiveKitRoom
        token={token}
        serverUrl={wsUrl}
        connect={true}
        audio={micAvailable}
        video={false}
        onDisconnected={onClose}
        style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
      >
        <TestCallUI agentName={agentName} micAvailable={micAvailable} onRetry={retry} />
        <RoomAudioRenderer />
      </LiveKitRoom>
    </div>
  );
}

function TestCallUI({ agentName, micAvailable, onRetry }: { agentName?: string; micAvailable?: boolean; onRetry?: () => void }) {
  const { state, audioTrack, agentTranscriptions } = useVoiceAssistant();
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const stateConfig: Record<string, { label: string; color: string }> = {
    connecting: { label: 'Connecting…', color: '#F59E0B' },
    initializing: { label: 'Initializing…', color: '#F59E0B' },
    listening: { label: '🎤 Listening', color: '#3B82F6' },
    thinking: { label: '💭 Thinking', color: '#F59E0B' },
    speaking: { label: '🔊 Speaking', color: '#3ECF8E' },
    idle: { label: '● Ready', color: '#888' },
    disconnected: { label: 'Disconnected', color: '#ef4444' },
  };
  const { label, color } = stateConfig[state] || stateConfig.idle;

  // The agent has "arrived" once the pipeline reaches a live conversational state.
  const agentReady = ['listening', 'thinking', 'speaking', 'idle'].includes(state);
  const [waitedLong, setWaitedLong] = useState(false);
  useEffect(() => {
    if (agentReady) { setWaitedLong(false); return; }
    const t = setTimeout(() => setWaitedLong(true), SLOW_MS);
    return () => clearTimeout(t);
  }, [agentReady, state]);

  // Auto-scroll transcript to the newest line.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [agentTranscriptions]);

  const hasTranscript = agentTranscriptions && agentTranscriptions.length > 0;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: '16px', gap: 12 }}>
      {/* Visualizer + status (compact so the transcript gets the room) */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <div style={{ width: '100%', maxWidth: 320 }}>
          <BarVisualizer
            state={state}
            trackRef={audioTrack}
            barCount={28}
            options={{ minHeight: 4 }}
            style={{ '--lk-fg': color, height: '56px', width: '100%' } as React.CSSProperties}
          />
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, color }}>{label}</div>
      </div>

      {/* Waiting-for-agent notice (worker cold start) */}
      {!agentReady && waitedLong && (
        <div style={{ fontSize: 12, color: '#F59E0B', textAlign: 'center', maxWidth: 340, alignSelf: 'center', lineHeight: 1.5 }}>
          Waiting for the voice service to respond — it may be starting up (cold start).
          {onRetry && <> <button onClick={onRetry} style={{ background: 'none', border: 'none', color: '#3ECF8E', textDecoration: 'underline', cursor: 'pointer', font: 'inherit', padding: 0 }}>Retry</button></>}
        </div>
      )}

      {/* Live transcript */}
      <div
        ref={scrollRef}
        style={{
          flex: 1, minHeight: 0, overflowY: 'auto',
          display: 'flex', flexDirection: 'column', gap: 8,
          padding: 12, borderRadius: 10,
          background: 'rgba(0,0,0,0.25)', border: '1px solid #1f1f1f',
        }}
      >
        {hasTranscript ? (
          agentTranscriptions.map(seg => (
            <div key={seg.id} style={{ display: 'flex', justifyContent: 'flex-start' }}>
              <div style={{ maxWidth: '85%', padding: '6px 12px', borderRadius: 12, borderTopLeftRadius: 4, fontSize: 13, lineHeight: 1.5, background: 'rgba(99,102,241,0.22)', color: '#c7d2fe' }}>
                <div style={{ fontSize: 10, opacity: 0.6, marginBottom: 2, fontWeight: 600 }}>🤖 {agentName || 'Agent'}</div>
                {seg.text}
              </div>
            </div>
          ))
        ) : (
          <div style={{ margin: 'auto', textAlign: 'center', color: '#555', fontSize: 12, lineHeight: 1.6 }}>
            {agentReady
              ? (micAvailable === false
                  ? 'Listening for the agent… (transcript appears as it speaks)'
                  : 'Say hello — the transcript appears here as you and the agent speak.')
              : 'Connecting the transcript…'}
          </div>
        )}
      </div>

      {/* Mode note */}
      {micAvailable === false ? (
        <div style={{ fontSize: 11, color: '#F59E0B', textAlign: 'center', lineHeight: 1.5 }}>
          🔇 No microphone — <strong>listen-only</strong>. You'll hear and read {agentName || 'the agent'}, but can't speak back.
        </div>
      ) : (
        <div style={{ fontSize: 11, color: '#666', textAlign: 'center' }}>
          Speak naturally — you can interrupt {agentName || 'the agent'} mid-sentence.
        </div>
      )}

      {/* Controls (wrap on narrow screens) */}
      <div className="lk-test-controls" style={{ display: 'flex', justifyContent: 'center', flexWrap: 'wrap' }}>
        <VoiceAssistantControlBar controls={{ leave: true, microphone: micAvailable !== false }} saveUserChoices={false} />
      </div>
    </div>
  );
}
