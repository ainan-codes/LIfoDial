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
 * The LLM PROVIDER was locked to Groq with no dropdown until 2026-08-13, when it was
 * UNLOCKED on the stakeholder's explicit instruction so Gemini can be chosen. It is
 * now a whitelisted choice exactly like STT/TTS — see SELECTABLE_LLM_PROVIDERS below.
 * Gemini was already buildable and already the first entry in the failover chain; the
 * only thing missing was the ability to select it deliberately.
 *
 * The LLM MODEL is a per-agent choice with a dropdown in the agent editor, populated
 * LIVE from the chosen vendor's own API via GET /platform/llm/models?provider=… —
 * never from a list in this file, because a hardcoded list is how the product came to
 * offer four models Groq had already decommissioned, and how the Gemini fallback came
 * to point at two ids Google had retired. Model choice matters per clinic in a way
 * vendor choice does not: Groq meters its free-tier token budget PER MODEL, so which
 * model an agent runs on decides how many calls a day it can serve (verified
 * 2026-08-11: 100K tokens/day on llama-3.3-70b vs 200K on gpt-oss-120b).
 *
 * Provider and model must always move TOGETHER. A model is meaningless without its
 * provider — llm_provider='groq' next to llm_model='gemini-2.5-flash-8b' is the exact
 * pair that left a live agent's LLM answering 404. When the provider changes, clear
 * the model and let the backend supply that provider's default.
 *
 * STT and TTS keep their dropdowns too, because switching transcriber or voice
 * vendor is the product's fallback story when one degrades. The bug there was never
 * that a choice existed — it was that the dropdowns were populated from an
 * ASPIRATIONAL catalogue and offered providers with no build branch and no key. So
 * the options are a whitelist now, and the choice stays.
 *
 * Why these values, in short (full evidence in agent_defaults.py):
 *   LLM  groq — DEFAULT provider (no longer a lock). gemini is also selectable.
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
/**
 * The DEFAULT LLM provider. Kept under its historical name because several call
 * sites import it; it is no longer enforced on write.
 */
export const LOCKED_LLM_PROVIDER = 'groq';
export const DEFAULT_LLM_PROVIDER = LOCKED_LLM_PROVIDER;

/**
 * Providers the LLM dropdown may offer. Mirrors
 * agent_defaults.SELECTABLE_LLM_PROVIDERS — the backend rejects anything else, so a
 * value added here without adding it there produces a 422 the operator cannot act on.
 *
 * The authoritative list also comes back on every GET /platform/llm/models response
 * as `providers`; prefer that when it is available and use this only for the first
 * render.
 */
export const SELECTABLE_LLM_PROVIDERS = ['groq', 'gemini'] as const;

export const LLM_PROVIDER_LABELS: Record<string, string> = {
  groq: 'Groq',
  gemini: 'Google Gemini',
};

/**
 * The model a row falls back to for DISPLAY while the agent is still loading, and
 * what the backend starts a new agent on. Not a lock — see above.
 */
export const DEFAULT_LLM_MODEL = 'llama-3.3-70b-versatile';

/**
 * Per provider, mirroring agent_defaults.DEFAULT_LLM_MODEL_BY_PROVIDER.
 *
 * Gemini's entry is an ALIAS, not a pinned snapshot: `gemini-2.5-flash` and
 * `gemini-2.0-flash` both still appear in Google's ListModels response and both
 * return 404 when actually called, so pinning a dated id here is a scheduled outage.
 */
export const DEFAULT_LLM_MODEL_BY_PROVIDER: Record<string, string> = {
  groq: DEFAULT_LLM_MODEL,
  gemini: 'gemini-flash-latest',
};

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
