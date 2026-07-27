# Streaming STT Integration Test Guide

## Setup

1. **Start Backend**
   ```bash
   cd lifodial
   .venv\Scripts\activate
   uvicorn backend.main:app --port 8000
   ```

2. **Start Frontend**
   ```bash
   cd frontend
   npm run dev
   ```

3. **Verify Connectivity**
   - Backend: http://localhost:8000/health
   - Frontend: http://localhost:5173
   - WS endpoint should be: `ws://localhost:8000/ws/streaming-stt/{tenant_id}/{agent_id}`

## Test 1: Health Check

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "database": "connected",
  "database_type": "sqlite",
  "version": "1.0.0",
  "environment": "development"
}
```

## Test 2: WebSocket Connection

Using a WebSocket client (e.g., `websocat` or browser console):

```bash
# Install websocat (if not already installed)
# cargo install websocat

websocat ws://localhost:8000/ws/streaming-stt/default-tenant/agent-001

# Send config
{"type":"config","language_code":"en-IN","mode":"transcribe","sample_rate":16000}

# Send audio (base64-encoded WAV)
{"type":"audio","audio":"UklGRi4AAABE...","encoding":"audio/wav"}

# Expected responses
{"type":"ready","language_code":"en-IN","mode":"transcribe"}
{"type":"speech_start","timestamp":1234567890}
{"type":"transcript","text":"I want to see a cardiologist","confidence":0.95,"language":"en-IN","mode":"transcribe"}
{"type":"speech_end","timestamp":1234568890}
```

## Test 3: Frontend WebCallInterface

1. Navigate to http://localhost:5173/superadmin/agents
2. Click on any agent card's **"🌐 Web Call"** button
3. Grant microphone permission when prompted
4. Speak into your microphone
5. Watch the transcript appear in real-time

**Expected behavior:**
- "Connecting..." → "Connected" status
- Your speech is transcribed in real-time below the state indicator
- Confidence scores appear with transcripts
- Call duration timer increments

## Test 4: Different Languages

In browser console, modify the useStreamingSTT hook configuration:

```typescript
// Change language_code to test different languages
const { sendAudio } = useStreamingSTT({
  tenantId: 'default-tenant',
  agentId: 'agent-001',
  languageCode: 'hi-IN',  // Change to: ta-IN, ml-IN, kn-IN, etc.
  mode: 'transcribe',
  onTranscript: (text) => console.log('Transcript:', text),
});
```

Speak in the selected language and verify transcription.

## Test 5: Translation Mode

Modify the hook to use translation mode:

```typescript
const { sendAudio } = useStreamingSTT({
  // ...
  languageCode: 'hi-IN',
  mode: 'translate',  // Translates to English
  // ...
});
```

Speak in Hindi, get English translation.

## Test 6: Error Handling

### Scenario: No Sarvam API Key
1. Remove `SARVAM_API_KEY` from `.env`
2. Restart backend
3. Open Web Call in browser
4. Check browser console for error message
5. Expected: Connection error, graceful fallback to demo mode

### Scenario: Network Failure
1. Disconnect internet
2. Try to use Web Call
3. WebSocket should timeout and show error
4. Expected: Browser shows error, connection button disabled

## Test 7: Backend Logs

Watch backend terminal for logs:

```
✅ Connected to Sarvam Streaming API
Streaming STT WS connected — tenant_id=default-tenant, agent_id=agent-001
STT config: lang=en-IN, mode=transcribe, sr=16000
Streaming STT WS disconnected — agent_id=agent-001
```

## Debugging Tips

**1. Check API Key**
```bash
echo %SARVAM_API_KEY%  # Windows
echo $SARVAM_API_KEY   # macOS/Linux
```

**2. Monitor WebSocket Traffic**
- Use Chrome DevTools → Network → WS
- Filter for `streaming-stt`
- Inspect messages sent/received

**3. Enable Debug Logging**
```python
# In backend/routers/ws.py or backend/services/sarvam_streaming.py
logger.setLevel(logging.DEBUG)
```

**4. Test Audio Encoding**
```python
import base64
# Ensure audio is properly base64-encoded
audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
```

**5. Sample Rate Mismatch**
- Always set same_rate in both:
  - Connection parameter: `sample_rate=16000`
  - Audio data parameter: `sample_rate=16000`

## Performance Metrics

Monitor these during testing:

- **Connection time**: Should be < 500ms
- **Transcription latency**: Should be 100-300ms
- **Memory usage**: ~5-10MB per active connection
- **CPU usage**: < 5% per streaming connection

## Next Steps

After verifying all tests pass:

1. **Integration with LiveKit**: Connect Web Call audio stream to live video
2. **Call Recording**: Save transcripts to database
3. **Production Deployment**: Set `SARVAM_API_KEY` in production `.env`
4. **Multi-tenant Testing**: Test with different tenant_ids
5. **Load Testing**: Simulate multiple concurrent STT connections

## Support

- Sarvam Docs: https://docs.sarvam.ai/
- API Status: https://status.sarvam.ai/
- Issue Tracking: GitHub Issues in this repo
