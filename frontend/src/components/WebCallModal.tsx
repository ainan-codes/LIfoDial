import { useState, useEffect } from 'react'
import {
  LiveKitRoom,
  BarVisualizer,
  RoomAudioRenderer,
  StartAudio,
  useRemoteParticipants,
  useTracks,
  useConnectionState,
  useRoomContext,
} from '@livekit/components-react'
import { Track, ConnectionState, RoomEvent } from 'livekit-client'
import '@livekit/components-styles'
import fetchWithAuth from '../api/client'

interface Agent {
  id: string
  name: string
  clinic_name: string
  tts_language: string
  tts_voice: string
  llm_model: string
}

export function WebCallModal({ 
  agent, 
  onClose 
}: { 
  agent: Agent
  onClose: () => void 
}) {
  const [token, setToken] = useState("")
  const [wsUrl, setWsUrl] = useState("")
  const [isConnecting, setIsConnecting] = useState(true)
  const [error, setError] = useState("")
  const [callSeconds, setCallSeconds] = useState(0)
  
  // Timer
  useEffect(() => {
    if (isConnecting) return
    const t = setInterval(() => setCallSeconds(s => s + 1), 1000)
    return () => clearInterval(t)
  }, [isConnecting])
  
  const formatTime = (s: number) => {
    const m = Math.floor(s / 60).toString().padStart(2, '0')
    const sec = (s % 60).toString().padStart(2, '0')
    return `${m}:${sec}`
  }
  
  // Get LiveKit token from backend
  useEffect(() => {
    const getToken = async () => {
      try {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
          stream.getTracks().forEach(t => t.stop())
        } catch (micErr: any) {
          setError(
            micErr.name === 'NotAllowedError'
              ? 'Microphone permission denied. Please allow mic access and try again.'
              : micErr.name === 'NotFoundError'
              ? 'No microphone found. Please connect a microphone.'
              : `Microphone error: ${micErr.message}`
          )
          setIsConnecting(false)
          return
        }

        const data = await fetchWithAuth(`/agents/${agent.id}/web-call-token`, { method: 'POST' })
        setToken(data.token)
        setWsUrl(data.wsUrl)
        setIsConnecting(false)
      } catch (e: any) {
        setError(e.message || 'Connection failed')
        setIsConnecting(false)
      }
    }
    getToken()
  }, [agent.id])
  
  if (isConnecting) {
    return (
      <div className="webcall-overlay">
        <div className="webcall-card">
          <div className="connecting-state">
            <div className="spinner-ring" />
            <h3>Connecting to {agent.name}</h3>
            <p>Setting up your AI call...</p>
          </div>
        </div>
      </div>
    )
  }
  
  if (error) {
    return (
      <div className="webcall-overlay">
        <div className="webcall-card error-state">
          <div className="error-icon">⚠️</div>
          <h3>Connection Failed</h3>
          <p>{error}</p>
          <p className="error-hint">
            Make sure LIVEKIT_URL, LIVEKIT_API_KEY, and 
            LIVEKIT_API_SECRET are set in your .env file.
          </p>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    )
  }
  
  return (
    <div className="webcall-overlay">
      <LiveKitRoom
        token={token}
        serverUrl={wsUrl}
        connect={true}
        audio={true}
        video={false}
        onDisconnected={onClose}
        className="webcall-room"
      >
        <CallUI
          agent={agent}
          duration={callSeconds}
          formatTime={formatTime}
          onClose={onClose}
        />
        <RoomAudioRenderer />
      </LiveKitRoom>
    </div>
  )
}

function CallUI({ agent, duration, formatTime, onClose }: { agent: Agent, duration: number, formatTime: (s: number) => string, onClose: () => void }) {
  const room = useRoomContext()
  const connState = useConnectionState()
  const remoteParticipants = useRemoteParticipants()

  useEffect(() => {
    if (connState === ConnectionState.Connected && room) {
      room.startAudio().catch((e) => console.warn('Audio auto-start notice:', e))
    }
  }, [connState, room])

  // Find agent's audio track — re-renders on all relevant RoomEvents
  // updateOnlyOn:[] was the bug (disabled all re-renders → track never found)
  const allTracks = useTracks(
    [Track.Source.Microphone, Track.Source.ScreenShareAudio, Track.Source.Unknown],
    {
      onlySubscribed: false,
      updateOnlyOn: [
        RoomEvent.TrackSubscribed,
        RoomEvent.TrackUnsubscribed,
        RoomEvent.ParticipantConnected,
        RoomEvent.ParticipantDisconnected,
        RoomEvent.TrackPublished,
        RoomEvent.TrackUnpublished,
      ],
    }
  )

  // Agent track: prefer identity starting with 'lifodial-agent', else any remote audio
  const agentTrackRef = allTracks.find(
    (t) =>
      t.participant &&
      t.participant.identity !== room?.localParticipant?.identity &&
      t.participant.identity.startsWith('lifodial-agent')
  ) ??
  allTracks.find(
    (t) =>
      t.participant &&
      t.participant.identity !== room?.localParticipant?.identity
  ) ?? null

  const agentParticipant =
    agentTrackRef?.participant ??
    remoteParticipants.find((p) => p.identity.startsWith('lifodial-agent')) ??
    remoteParticipants[0] ??
    null

  // Full TrackReference for BarVisualizer (not just .publication)
  const agentAudioTrackRef = agentTrackRef

  const [agentState, setAgentState] = useState<string>('connecting')

  useEffect(() => {
    if (!agentParticipant) {
      setAgentState('connecting')
      return
    }
    const attrs = (agentParticipant as any).attributes || {}
    setAgentState(attrs['lk.agent.state'] || 'idle')

    const handler = () => {
      const updated = (agentParticipant as any).attributes || {}
      setAgentState(updated['lk.agent.state'] || 'idle')
    }
    agentParticipant.on('attributesChanged', handler)
    return () => { agentParticipant.off('attributesChanged', handler) }
  }, [agentParticipant])

  const agentReady = (!!agentParticipant || remoteParticipants.length > 0) && connState === ConnectionState.Connected
  
  const stateConfig: Record<string, { label: string, color: string }> = {
    "connecting": { label: "Connecting...", color: "#F59E0B" },
    "listening": { label: "🎤 Listening", color: "#3B82F6" },
    "thinking": { label: "💭 Processing", color: "#F59E0B" },
    "speaking": { label: "🔊 Speaking", color: "#3ECF8E" },
    "idle": { label: "● Ready", color: "#3ECF8E" },
  }
  
  const { label, color } = agentReady
    ? (stateConfig[agentState] || { label: "● Live", color: "#3ECF8E" })
    : { label: "Connecting...", color: "#F59E0B" }
  
  const langLabel = ({
    "hi-IN": "🇮🇳 Hindi", "ta-IN": "🇮🇳 Tamil",
    "ml-IN": "🇮🇳 Malayalam", "ar-SA": "🇦🇪 Arabic",
    "en-IN": "🇮🇳 English", "te-IN": "🇮🇳 Telugu",
    "kn-IN": "🇮🇳 Kannada", "bn-IN": "🇮🇳 Bengali",
  } as Record<string, string>)[agent.tts_language] || agent.tts_language
  
  return (
    <div className="call-ui">
      {/* Autoplay unblock button */}
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 8 }}>
        <StartAudio label="🔊 Click to Unmute / Enable Agent Audio" style={{ background: '#3ECF8E', color: '#000', padding: '6px 16px', borderRadius: 20, fontWeight: 700, border: 'none', cursor: 'pointer' }} />
      </div>

      {/* Header */}
      <div className="call-header">
        <div className="call-info">
          <div className="call-avatar">🤖</div>
          <div>
            <div className="call-name">{agent.name}</div>
            <div className="call-clinic">{agent.clinic_name}</div>
          </div>
        </div>
        <div className="call-timer">
          <span className="timer-dot" />
          {formatTime(duration)}
        </div>
      </div>
      
      {/* Visualizer */}
      <div className="call-visualizer">
        {agentReady && agentAudioTrackRef ? (
          <BarVisualizer
            trackRef={agentAudioTrackRef}
            barCount={36}
            options={{ minHeight: 4 }}
            style={{
              "--lk-va-bar-width": "4px",
              "--lk-va-bar-gap": "3px",
              "--lk-fg": color,
              height: "80px",
              width: "100%",
            } as React.CSSProperties}
          />
        ) : (
          <div style={{ height: '80px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ width: 36, height: 36, border: '3px solid #1f1f1f', borderTopColor: color, borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
          </div>
        )}
        <p className="call-state-label" style={{ color }}>
          {label}
        </p>
      </div>
      
      {/* Info row */}
      <div className="call-meta">
        <span className="call-language">{langLabel}</span>
        <span className="call-model">🧠 {agent.llm_model}</span>
        <span className="call-voice">🎙 {agent.tts_voice}</span>
      </div>
      
      {/* Controls */}
      <div className="call-controls" style={{ display: 'flex', justifyContent: 'center', margin: '16px 0' }}>
        <button
          onClick={onClose}
          style={{
            padding: '10px 28px', borderRadius: 40, border: '1px solid #ef4444',
            background: 'rgba(239,68,68,0.1)', color: '#ef4444',
            fontWeight: 600, fontSize: 14, cursor: 'pointer',
          }}
        >
          Disconnect Call
        </button>
      </div>
      
      {/* Mic hint */}
      {agentReady && (
        <p className="mic-hint">
          Speak naturally — the AI will respond
        </p>
      )}
    </div>
  )
}
