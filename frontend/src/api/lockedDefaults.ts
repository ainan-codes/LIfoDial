/**
 * The locked LLM PROVIDER, and the DEFAULT (not locked) LLM model + STT/TTS pair.
 *
 * The AUTHORITY is backend/services/agent_defaults.py — the backend applies the
 * provider lock on every write and ignores whatever a client sends, and it validates
 * the STT/TTS pair against the same whitelist this file mirrors. If you change a
 * value here, change it there too — that is the one that decides what actually
 * runs a call.
 *
 * What is locked, and what is a choice
 * ------------------------------------
 * All three used to be free-form provider+model pairs, which is how one live agent
 * came to hold llm_provider='groq' with llm_model='gemini-2.5-flash-8b' — a pair
 * Groq answers 404 for, so that agent's LLM was simply dead.
 *
 * The LLM PROVIDER choice is removed outright: nothing about a clinic makes one
 * vendor the right answer, so it is a platform decision with no dropdown. The LLM
 * MODEL is a real per-agent choice and has a dropdown in the agent editor, populated
 * LIVE from Groq's own API via GET /platform/llm/models — never from a list in this
 * file, because a hardcoded list is how the product came to offer four models Groq
 * had already decommissioned. Model choice matters per clinic in a way vendor choice
 * does not: Groq meters its free-tier token budget PER MODEL, so which model an
 * agent runs on decides how many calls a day it can serve (verified 2026-08-11:
 * 100K tokens/day on llama-3.3-70b vs 200K on gpt-oss-120b).
 *
 * STT and TTS keep their dropdowns too, because switching transcriber or voice
 * vendor is the product's fallback story when one degrades. The bug there was never
 * that a choice existed — it was that the dropdowns were populated from an
 * ASPIRATIONAL catalogue and offered providers with no build branch and no key. So
 * the options are a whitelist now, and the choice stays.
 *
 * Why these values, in short (full evidence in agent_defaults.py):
 *   LLM  groq — PROVIDER LOCKED. Stakeholder preference; probed working.
 *        llama-3.3-70b-versatile — DEFAULT model only: the one this project has run
 *        on throughout its history. Existing agents stay on it unless someone picks
 *        another in the editor.
 *   STT  deepgram/nova-3 — DEFAULT. The only configured provider that emits interim
 *        results, so the only genuinely real-time one. Sarvam AI is also
 *        selectable, and is the right choice for Malayalam/Punjabi/Odia, which
 *        Deepgram serves on no tier.
 *   TTS  sarvam/bulbul:v3 — DEFAULT, and currently the only selectable option: the
 *        only configured TTS with real Indic coverage, and what the Voice Library
 *        is built on.
 */
export const LOCKED_LLM_PROVIDER = 'groq';

/**
 * The model a row falls back to for DISPLAY while the agent is still loading, and
 * what the backend starts a new agent on. Not a lock — see above.
 */
export const DEFAULT_LLM_MODEL = 'llama-3.3-70b-versatile';

/**
 * Defaults, used only as the value to render against while the agent row is still
 * loading. The real per-agent value comes from the row, and the real option lists
 * come from GET /platform/agent/config-options — never from constants here, so
 * this file can never disagree with what the backend will accept.
 */
export const DEFAULT_STT_PROVIDER = 'deepgram';
export const DEFAULT_STT_MODEL = 'nova-3';

export const DEFAULT_TTS_PROVIDER = 'sarvam';
export const DEFAULT_TTS_MODEL = 'bulbul:v3';

/** Applied when an agent has no resolvable language at all. */
export const DEFAULT_LANGUAGE = 'en-IN';
