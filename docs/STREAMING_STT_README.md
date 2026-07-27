# Sarvam Streaming Speech-to-Text Integration

This document describes the real-time speech-to-text (STT) streaming API integration using Sarvam AI's WebSocket endpoint.

## Overview

- **Streaming Transcription**: Real-time audio-to-text conversion via WebSocket
- **Multiple Output Modes**: transcribe, translate, verbatim, translit, codemix
- **Multi-Language**: 10+ Indian languages + English
- **Voice Activity Detection (VAD)**: Automatic speech boundary detection
- **Latency**: Ultra-low (~100ms) for real-time applications

## Architecture

```
Browser (WebCallInterface)
    ↓ [getUserMedia audio stream]
Frontend Hook (useStreamingSTT)
    → WebSocket: /ws/streaming-stt/{tenant_id}/{agent_id}
Backend WS Endpoint (backend/routers/ws.py)
    → SarvamStreamingSTT Client (backend/services/sarvam_streaming.py)
    → WebSocket: wss://api.sarvam.ai/speech_to_text_streaming
Sarvam AI Streaming API
    ← Speech-to-text results
```

## Setup & Configuration

### 1. Environment Variables

Ensure your `.env` file has:

```bash
SARVAM_API_KEY=your_sarvam_api_key_here
```

Get your API key from [Sarvam AI Console](https://sarvam.ai/console).

### 2. Backend Services

**File**: `backend/services/sarvam_streaming.py`

Core classes:
- `SarvamStreamingSTT` — WebSocket client for Sarvam streaming API
- `create_streaming_stt()` — Factory function to create and connect a client
- `transcribe_audio_stream()` — High-level streaming transcription helper

### 3. WebSocket Endpoint

**File**: `backend/routers/ws.py`

Endpoint: `GET /ws/streaming-stt/{tenant_id}/{agent_id}`

**Client → Server (JSON):**
```json
{
  "type": "config",
  "language_code": "en-IN",
  "mode": "transcribe",
  "sample_rate": 16000
}
```

```json
{
  "type": "audio",
  "audio": "<base64-encoded-audio>",
  "encoding": "audio/wav",
  "sample_rate": 16000
}
```

```json
{
  "type": "flush"
}
```

**Server → Client (JSON):**
```json
{
  "type": "ready",
  "language_code": "en-IN",
  "mode": "transcribe"
}
```

```json
{
  "type": "speech_start",
  "timestamp": 1234567890
}
```

```json
{
  "type": "transcript",
  "text": "I want to see a cardiologist",
  "confidence": 0.95,
  "language": "en-IN",
  "mode": "transcribe"
}
```

```json
{
  "type": "error",
  "message": "Failed to connect to Sarvam streaming API",
  "code": "SARVAM_CONNECTION_FAILED"
}
```

### 4. Frontend Hook

**File**: `frontend/src/hooks/useStreamingSTT.ts`

```typescript
const {
  isConnected,
  isListening,
  lastTranscript,
  sendAudio,
  flush,
  connect,
  disconnect,
} = useStreamingSTT({
  tenantId: 'tenant-001',
  agentId: 'agent-001',
  languageCode: 'en-IN',
  mode: 'transcribe',
  sampleRate: 16000,
  onTranscript: (text, confidence) => console.log(text),
  onSpeechStart: () => console.log('Speech detected'),
  onSpeechEnd: () => console.log('Speech ended'),
  onError: (error) => console.error(error),
});

// Send audio chunk
const audioChunk = new Uint8Array(/*...*/);
await sendAudio(audioChunk);

// Force immediate processing
flush();

// Disconnect
disconnect();
```

### 5. Integration in WebCallInterface

The `WebCallInterface` component uses the streaming STT hook to:

1. Connect to `/ws/streaming-stt/{tenant_id}/{agent_id}` on mount
2. Capture user's microphone audio via `getUserMedia()`
3. Send audio chunks to backend for real-time transcription
4. Display results in the call transcript
5. Disconnect on component unmount

**File**: `frontend/src/components/WebCallInterface.tsx`

## API Reference

### Sarvam Streaming Models

| Model | Description | Modes | Language Code |
|-------|-------------|-------|---|
| `saaras:v3` | Recommended | transcribe, translate, verbatim, translit, codemix | en-IN, hi-IN, ml-IN, etc. |
| `saarika:v2.5` | Legacy STT | Default | en-IN, hi-IN, ml-IN, etc. |
| `saaras:v2.5` | Legacy STTT | Default | Not required (auto-detect) |

### Supported Language Codes

- **Hindi**: `hi-IN`
- **English**: `en-IN`
- **Malayalam**: `ml-IN`
- **Tamil**: `ta-IN`
- **Kannada**: `kn-IN`
- **Telugu**: `te-IN`
- **Marathi**: `mr-IN`
- **Gujarati**: `gu-IN`
- **Bengali**: `bn-IN`
- **Punjabi**: `pa-IN`
- **Arabic**: `ar-SA`

### Output Modes (Saaras v3 only)

| Mode | Output |
|------|--------|
| `transcribe` | Standard text in source language |
| `translate` | English translation |
| `verbatim` | Word-for-word including fillers |
| `translit` | Romanized text (e.g., "namaste" from "नमस्ते") |
| `codemix` | Code-mixed speech (Hindi-English, etc.) |

## Example Usage: Live Call Transcription

```typescript
// In WebCallInterface component
const { sendAudio, lastTranscript } = useStreamingSTT({
  tenantId: 'clinic-001',
  agentId: 'ai-receptionist-001',
  languageCode: 'hi-IN',
  mode: 'transcribe',
  onTranscript: (text) => {
    // Update transcript display
    updateCallTranscript('user', text);
  },
});

// When audio becomes available
function handleAudioChunk(audioData: Uint8Array) {
  sendAudio(audioData);
}

// Rendering
<div>
  <p>User says: {lastTranscript}</p>
</div>
```

## Example Usage: Direct Backend Call

```python
from backend.services.sarvam_streaming import create_streaming_stt
import asyncio

async def transcribe_file(audio_bytes: bytes):
    client = await create_streaming_stt(
        language_code="hi-IN",
        mode="transcribe",
        sample_rate=16000,
    )
    
    if not client:
        print("Failed to connect")
        return
    
    # Send audio
    await client.send_audio(audio_bytes, encoding="audio/wav")
    
    # Receive results
    async for result in client.receive_results():
        if result["type"] == "transcript":
            print(f"Transcribed: {result['text']}")
        elif result["type"] == "speech_end":
            break
    
    await client.close()

# Run
asyncio.run(transcribe_file(audio_data))
```

## Troubleshooting

### No Sarvam API Key
```
WARNING: No Sarvam API key configured — streaming STT will be unavailable
```
**Fix**: Set `SARVAM_API_KEY` in `.env` and restart backend.

### WebSocket Connection Failed
```
ERROR: Failed to connect to Sarvam Streaming API
```
**Possible causes:**
- Invalid API key
- Network/firewall blocking WebSocket
- Sarvam API endpoint down

**Fix**: Verify API key, check network connectivity, check Sarvam status.

### Poor Transcription Quality
- **Too much noise**: Increase `high_vad_sensitivity=True`
- **Mismatched sample rate**: Ensure `sample_rate` matches actual audio (8000 or 16000 Hz)
- **Wrong language**: Set correct `language_code`

### Silence Timeout
- If no speech for 3+ seconds, connection may close
- Use `flush_signal=True` to manually trigger processing
- Or send continuous audio frames

## Performance Notes

- **Latency**: ~100-300ms from audio capture to transcript
- **Throughput**: Real-time (16kHz, 16-bit PCM = 32KB/sec)
- **Concurrency**: Handles multiple simultaneous STT connections per tenant
- **Memory**: ~5-10MB per active streaming connection

## Security

- All audio sent over secure WebSocket (`wss://`)
- API credentials never exposed to frontend
- Backend validates tenant/agent ownership before streaming
- Audio not persisted unless explicitly configured

## Further Reading

- [Sarvam Streaming STT Docs](https://docs.sarvam.ai/api-reference-docs/speech-to-text/apis/streaming)
- [Speech-to-Text API Reference](https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/speech-to-text/streaming-api)
- [WebSocket Endpoint Reference](https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe/ws)
