from google import genai
import tempfile, os, asyncio
from .base import STTProvider, TTSProvider
from backend.config import settings

client = genai.Client(api_key=settings.gemini_api_key)

class GeminiSTT(STTProvider):
    async def transcribe(
        self, audio: bytes, lang: str = "en-IN"
    ) -> str:
        lang_names = {
            "hi-IN": "Hindi", "ta-IN": "Tamil",
            "te-IN": "Telugu", "kn-IN": "Kannada",
            "ml-IN": "Malayalam", "bn-IN": "Bengali",
            "ar-SA": "Arabic", "en-IN": "English",
            "mr-IN": "Marathi", "pa-IN": "Punjabi"
        }
        lang_name = lang_names.get(lang, "English")
        
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio)
            tmp_path = tmp.name
        
        try:
            # Reusing the structure from the prompt with a check for genai version if needed, 
            # but sticking to newer Client approach.
            audio_upload = client.files.upload(path=tmp_path)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    audio_upload,
                    f"Transcribe this audio. Speaker is likely speaking {lang_name}. Return ONLY the transcript, nothing else."
                ]
            )
            return response.text.strip()
        finally:
            os.unlink(tmp_path)

class GeminiTTS(TTSProvider):
    async def synthesize(
        self, text: str, 
        lang: str = "en-IN",
        voice: str = None
    ) -> bytes:
        voices = {
            "hi-IN": "Charon", "en-IN": "Puck", "ta-IN": "Kore",
            "ar-SA": "Aoede", "te-IN": "Fenrir", "kn-IN": "Charon", "ml-IN": "Puck"
        }
        selected_voice = voice or voices.get(lang, "Puck")
        
        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash-preview-tts",
                contents=text,
                config=genai.types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=genai.types.SpeechConfig(
                        voice_config=genai.types.VoiceConfig(
                            prebuilt_voice_config=genai.types.PrebuiltVoiceConfig(
                                voice_name=selected_voice
                            )
                        )
                    )
                )
            )
            return response.candidates[0].content.parts[0].inline_data.data
        except Exception as e:
            print(f"TTS error: {e}")
            return b""
