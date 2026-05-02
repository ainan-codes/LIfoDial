import asyncio
import logging
import os
import base64
import httpx
from dotenv import load_dotenv

from livekit import rtc
from livekit.agents import (
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice import Agent
from livekit.plugins import silero

load_dotenv()

# --- Config ---
SYSTEM_PROMPT = "You are a helpful medical receptionist for Lifodial. Be polite and concise."
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- Custom Gemini Wrapper (Using google-genai) ---
class GeminiLLM:
    def __init__(self, api_key: str):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model_id = "gemini-2.5-flash"

    async def chat(self, messages: list[llm.ChatMessage]) -> str:
        prompt = "\n".join([f"{m.role}: {m.content}" for m in messages])
        response = await asyncio.to_thread(self.client.models.generate_content, model=self.model_id, contents=prompt)
        return response.text.strip()

# --- Custom Sarvam Wrapper (STT) ---
class SarvamSTT:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://api.sarvam.ai/v1/stt"
        
    async def transcribe(self, audio: bytes) -> str:
        # Note: Implement actual API call based on Sarvam documentation
        # This is a placeholder for the integration
        return ""

# --- Custom Sarvam Wrapper (TTS) ---
class SarvamTTS:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://api.sarvam.ai/v1/tts"

    async def synthesize(self, text: str) -> bytes:
        # Note: Implement actual API call based on Sarvam documentation
        return b""

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    logging.info(f"Incoming call in room: {ctx.room.name}")
    await ctx.connect()

    # Create the agent session
    # For now, using placeholders for Sarvam until we finalize the exact API schema
    # But using the structural intent the user requested
    agent = Agent(
        vad=ctx.proc.userdata["vad"],
        stt=SarvamSTT(SARVAM_API_KEY),
        llm=GeminiLLM(GEMINI_API_KEY),
        tts=SarvamTTS(SARVAM_API_KEY),
        chat_ctx=llm.ChatContext(messages=[llm.ChatMessage(role="system", content=SYSTEM_PROMPT)]),
    )

    agent.start(ctx.room)
    await agent.say("Hello! This is Lifodial. How can I help you?", allow_interruptions=True)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm, agent_name="inbound-agent"))
