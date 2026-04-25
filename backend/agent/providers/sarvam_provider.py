import httpx
import base64
from backend.config import settings

class SarvamTTS:
    def __init__(self, model="bulbul:v3", 
                 voice="meera", language="hi-IN",
                 pitch=0.0, pace=1.0, loudness=1.0):
        self.model = model
        self.voice = voice
        self.language = language
        self.pitch = pitch
        self.pace = pace
        self.loudness = loudness
    
    async def synthesize(self, text: str,
                         lang: str = None,
                         voice: str = None) -> bytes:
        use_lang = lang or self.language
        use_voice = voice or self.voice
        
        if not text or not text.strip():
            return b""
        
        # Sarvam 500 char limit per request
        if len(text) > 450:
            return await self._synthesize_chunked(
                text, use_lang, use_voice
            )
        
        async with httpx.AsyncClient(
            timeout=15.0
        ) as client:
            response = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": 
                        settings.sarvam_api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "inputs": [text],
                    "target_language_code": use_lang,
                    "speaker": use_voice,
                    "model": self.model,
                    "pitch": self.pitch,
                    "pace": self.pace,
                    "loudness": self.loudness,
                    "speech_sample_rate": 16000,
                    "enable_preprocessing": True,
                }
            )
            
            if response.status_code != 200:
                print(f"Sarvam TTS error: {response.status_code}")
                print(f"Response: {response.text}")
                return b""
            
            data = response.json()
            if "audios" not in data or not data["audios"]:
                print("Sarvam TTS: no audio in response")
                return b""
                
            audio_b64 = data["audios"][0]
            return base64.b64decode(audio_b64)
    
    async def _synthesize_chunked(self, text, lang, 
                                    voice) -> bytes:
        import re
        sentences = re.split(r'(?<=[।.!?])\s+', text)
        chunks, current = [], ""
        for s in sentences:
            if len(current) + len(s) < 450:
                current += s + " "
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = s + " "
        if current.strip():
            chunks.append(current.strip())
        
        audio_parts = []
        for chunk in chunks:
            audio = await self.synthesize(
                chunk, lang, voice
            )
            if audio:
                audio_parts.append(audio)
        
        if not audio_parts:
            return b""
        
        return b"".join(audio_parts)
