import { useEffect, useState } from 'react';
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
 * This replaces the old batch STT→LLM→TTS WebSocket harness (which caused the
 * ~4s first-audio delay and had no real barge-in). Native barge-in and streaming
 * come for free from the pipeline via useVoiceAssistant's state machine.
 */
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
  // No mic is NOT fatal — we still connect in listen-only mode so the agent can
  // be heard and tested (you just can't speak back).
  const [micAvailable, setMicAvailable] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const connect = async () => {
      try {
        // Try to grab the mic BEFORE connecting so LiveKit publishes an audio
        // track for STT. If there's no mic (or permission is denied), DON'T
        // abort — fall back to listen-only so the agent is still testable
        // (previously this hard-blocked with "No microphone found").
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          stream.getTracks().forEach(t => t.stop());
        } catch (micErr: any) {
          if (cancelled) return;
          console.warn('Mic unavailable — connecting listen-only:', micErr?.name || micErr);
          setMicAvailable(false);
        }

        // Same endpoint as real web calls — test_mode flags it for no-billing and
        // lets the worker bypass the publish gate so unpublished agents testable.
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
      }
    };
    connect();
    return () => { cancelled = true; };
  }, [agentId]);

  const shell = (children: React.ReactNode) => (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '14px', padding: '24px', textAlign: 'center', color: '#fff' }}>
      {children}
    </div>
  );

  if (phase === 'connecting') {
    return shell(
      <>
        <div style={{ width: 34, height: 34, border: '3px solid #2e2e2e', borderTopColor: '#3ECF8E', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        <div style={{ fontSize: 14, fontWeight: 600 }}>Connecting to {agentName || 'agent'}…</div>
        <div style={{ fontSize: 12, color: '#666' }}>Setting up the live pipeline</div>
      </>
    );
  }

  if (phase === 'error') {
    return shell(
      <>
        <div style={{ fontSize: 28 }}>⚠️</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Couldn’t start the test call</div>
        <div style={{ fontSize: 12, color: '#888', maxWidth: 300 }}>{error}</div>
        {onClose && <button onClick={onClose} style={{ marginTop: 8, padding: '6px 16px', borderRadius: 8, border: '1px solid #2e2e2e', background: 'none', color: '#fff', cursor: 'pointer' }}>Close</button>}
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
        style={{ flex: 1, display: 'flex', flexDirection: 'column' }}
      >
        <TestCallUI agent={agent} agentName={agentName} micAvailable={micAvailable} />
        <RoomAudioRenderer />
      </LiveKitRoom>
    </div>
  );
}

function TestCallUI({ agent, agentName, micAvailable }: { agent?: any; agentName?: string; micAvailable?: boolean }) {
  const { state, audioTrack } = useVoiceAssistant();
  const stateConfig: Record<string, { label: string; color: string }> = {
    connecting: { label: 'Connecting…', color: '#F59E0B' },
    initializing: { label: 'Initializing…', color: '#F59E0B' },
    listening: { label: '🎤 Listening', color: '#3B82F6' },
    thinking: { label: '💭 Thinking', color: '#F59E0B' },
    speaking: { label: '🔊 Speaking', color: '#3ECF8E' },
    idle: { label: '● Ready — just speak', color: '#888' },
    disconnected: { label: 'Disconnected', color: '#ef4444' },
  };
  const { label, color } = stateConfig[state] || stateConfig.idle;

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '20px', padding: '24px' }}>
      <div style={{ width: '100%', maxWidth: 320 }}>
        <BarVisualizer
          state={state}
          trackRef={audioTrack}
          barCount={32}
          options={{ minHeight: 4 }}
          style={{ '--lk-fg': color, height: '72px', width: '100%' } as React.CSSProperties}
        />
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, color }}>{label}</div>
      {micAvailable === false ? (
        <div style={{ fontSize: 12, color: '#F59E0B', textAlign: 'center', maxWidth: 320, lineHeight: 1.5 }}>
          🔇 No microphone detected — <strong>listen-only mode</strong>. You'll hear {agentName || 'the agent'}
          greet and respond, but can't speak back. Connect a mic for a full two-way conversation.
        </div>
      ) : (
        <div style={{ fontSize: 12, color: '#666', textAlign: 'center' }}>
          Speak naturally — you can interrupt {agentName || 'the agent'} mid-sentence.
        </div>
      )}
      <div className="lk-test-controls">
        <VoiceAssistantControlBar controls={{ leave: true, microphone: micAvailable !== false }} saveUserChoices={false} />
      </div>
    </div>
  );
}
