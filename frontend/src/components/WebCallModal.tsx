import { useState, useEffect } from 'react'
import {
  LiveKitRoom,
  useVoiceAssistant,
  BarVisualizer,
  RoomAudioRenderer,
  VoiceAssistantControlBar,
} from '@livekit/components-react'
import '@livekit/components-styles'
import { API_URL } from '../api/client'

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
        // ── CRITICAL: Request microphone BEFORE connecting ──
        // Without this, LiveKit connects but no audio track is published,
        // causing STT to silently fail.
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
          // Release the stream immediately — LiveKit will re-acquire it
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

        const res = await fetch(
          `${API_URL}/agents/${agent.id}/web-call-token`,
          { method: 'POST' }
        )
        if (!res.ok) {
          const err = await res.json()
          throw new Error(err.detail || 'Failed to connect')
        }
        const data = await res.json()
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
  const { state: agentState, audioTrack } = useVoiceAssistant()
  
  const stateConfig: Record<string, { label: string, color: string }> = {
    "connecting": { label: "Connecting...", color: "#F59E0B" },
    "listening": { label: "🎤 Listening", color: "#3B82F6" },
    "thinking": { label: "💭 Processing", color: "#F59E0B" },
    "speaking": { label: "🔊 Speaking", color: "#3ECF8E" },
    "idle": { label: "● Ready", color: "#666" },
    "initializing": { label: "Initializing...", color: "#F59E0B" },
    "disconnected": { label: "Disconnected", color: "#ef4444" }
  }
  
  const { label, color } = stateConfig[agentState] || 
    stateConfig["idle"]
  
  const langLabel = ({
    "hi-IN": "🇮🇳 Hindi", "ta-IN": "🇮🇳 Tamil",
    "ml-IN": "🇮🇳 Malayalam", "ar-SA": "🇦🇪 Arabic",
    "en-IN": "🇮🇳 English", "te-IN": "🇮🇳 Telugu",
    "kn-IN": "🇮🇳 Kannada", "bn-IN": "🇮🇳 Bengali",
  } as Record<string, string>)[agent.tts_language] || agent.tts_language
  
  return (
    <div className="call-ui">
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
        <BarVisualizer
          state={agentState}
          trackRef={audioTrack}
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
      <div className="call-controls">
        <VoiceAssistantControlBar
          controls={{ leave: true, microphone: true }}
          onLeaveClick={onClose}
          saveUserChoices={false}
        />
      </div>
      
      {/* Mic hint */}
      {agentState === "idle" && (
        <p className="mic-hint">
          Speak naturally — the AI will respond
        </p>
      )}
    </div>
  )
}
