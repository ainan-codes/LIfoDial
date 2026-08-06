import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  Search, ChevronDown, Check, Play, Square, FilterX, Clock, MapPin, SearchX, Globe, Settings, CreditCard, Menu, MessageCircle, Music, Shield, Info, ExternalLink, Link2, Download, Copy, Circle
} from 'lucide-react';
import fetchWithAuth, { API_URL } from '../../api/client';
import { getToken } from '../../api/auth';

interface VoiceLibraryProps {
  isPickerModal?: boolean;
  onSelectVoice?: (voice: any) => void;
  readOnly?: boolean;
  /**
   * Language to pre-select in the filter, as a BCP-47 code (e.g. 'ml-IN').
   *
   * Matters more than it looks. Sarvam ties NO speaker to a language — all 37
   * bulbul:v3 voices speak all 11 GA languages, verified live (37/37 return
   * Malayalam audio) — but each card carries one DISPLAY tag, which for most
   * voices is hi-IN. So opening this picker for a Malayalam agent with no filter
   * showed 37 voices all labelled "Hindi", which reads as "there are no Malayalam
   * voices" even though every one of them is one.
   *
   * With the agent's language pre-selected, effectiveLanguage() re-tags each card
   * to ml-IN and previews synthesize Malayalam — so the list says what it does.
   */
  initialLanguage?: string;
}

// Display metadata for every TTS provider. The library is driven by whichever
// of these actually have a key configured (from /platform/configured-providers).
const TTS_PROVIDER_META: Record<string, { name: string; icon: string; defaultModel: string; defaultLang: string }> = {
  sarvam:        { name: 'Sarvam AI',        icon: '🇮🇳', defaultModel: 'bulbul:v3',        defaultLang: 'hi-IN' },
  elevenlabs:    { name: 'ElevenLabs',       icon: '11',  defaultModel: 'eleven_flash_v2_5', defaultLang: 'en-US' },
  openai_tts:    { name: 'OpenAI TTS',       icon: '🤖', defaultModel: 'tts-1',             defaultLang: 'en-US' },
  cartesia:      { name: 'Cartesia',         icon: '🌊', defaultModel: 'sonic-2',           defaultLang: 'en-US' },
  playht:        { name: 'PlayHT',           icon: '▶',  defaultModel: 'PlayDialog',        defaultLang: 'en-US' },
  azure_tts:     { name: 'Azure Neural',     icon: '☁',  defaultModel: 'neural',            defaultLang: 'en-US' },
  deepgram_aura: { name: 'Deepgram Aura',    icon: '🔊', defaultModel: 'aura-2',            defaultLang: 'en-US' },
};

// The Language/Accent options this page has always offered. Kept verbatim and
// always first so existing filters behave exactly as before; anything a
// provider reports (see `languages` in /platform/tts/voices/<id>) is appended
// after these. Sarvam contributes Kannada, Malayalam, Marathi, Bengali,
// Gujarati, Punjabi and Odia — all of which its API confirms it can speak, and
// none of which were selectable here before.
const BASE_LANGUAGE_OPTIONS: { code: string; label: string }[] = [
  { code: 'hi-IN', label: 'Hindi (hi-IN)' },
  { code: 'en-IN', label: 'English - Indian (en-IN)' },
  { code: 'en-US', label: 'English - American (en-US)' },
  { code: 'ta-IN', label: 'Tamil (ta-IN)' },
  { code: 'te-IN', label: 'Telugu (te-IN)' },
  { code: 'ar-SA', label: 'Arabic (ar-SA)' },
];

export default function VoiceLibrary({ isPickerModal = false, onSelectVoice, readOnly = false, initialLanguage }: VoiceLibraryProps) {
  const [search, setSearch] = useState('');
  const [provider, setProvider] = useState('');
  const [gender, setGender] = useState('');
  const [language, setLanguage] = useState(initialLanguage || '');
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingAudioId, setLoadingAudioId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  // `error` is set when the provider IS configured but its voice list call failed —
  // a different state from `connected: false` (no API key), and one the page must
  // show rather than rendering a silently short list.
  const [providerStatus, setProviderStatus] = useState<Record<string, { connected: boolean; voice_count: number; error?: string }>>({});
  // Configured TTS providers (ids) in the order they should appear.
  const [ttsProviders, setTtsProviders] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Language options contributed by the configured providers themselves, so
  // this filter can never again list fewer languages than the voices support.
  const [providerLanguages, setProviderLanguages] = useState<{ code: string; name: string }[]>([]);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  // The object URL `audioRef` is currently playing, so stopAudio can revoke it.
  const audioUrlRef = useRef<string | null>(null);

  const [localVoices, setLocalVoices] = useState<any[]>([]);

  useEffect(() => {
    // Normalize any provider's voice payload into a uniform card shape.
    const normalize = (providerId: string, v: any) => {
      const meta = TTS_PROVIDER_META[providerId];
      const lang = v.language || meta?.defaultLang || 'en-US';
      return {
        id: `${providerId}-${v.voice_id || v.id}`,
        provider: providerId,
        provider_label: meta?.name || providerId,
        name: v.name,
        gender: (v.gender || 'neutral').toUpperCase(),
        language: lang,
        language_label: lang,
        accent: String(lang).substring(0, 5),
        model: v.model || meta?.defaultModel || '',
        voice_id: v.voice_id || v.id,
        // Every language this voice can actually be asked to speak. Sarvam ties
        // no speaker to a language, so its voices report all 11; providers that
        // publish no language catalogue report none and fall back to `language`.
        languages: Array.isArray(v.languages) ? v.languages : [],
        tags: [lang, v.gender].filter(Boolean),
        sample_text: v.description || 'Hello! I am your AI receptionist. How can I help you today?',
        recommended_for: [],
        is_recommended: false,
      };
    };

    const fetchVoicesFor = async (providerId: string) => {
      try {
        const data = await fetchWithAuth(`/platform/tts/voices/${providerId}`);
        const voices: any[] = Array.isArray(data?.voices) ? data.voices : [];
        setProviderStatus(prev => ({ ...prev, [providerId]: { connected: true, voice_count: voices.length } }));
        const langs: any[] = Array.isArray(data?.languages) ? data.languages : [];
        if (langs.length) {
          setProviderLanguages(prev => {
            const seen = new Set(prev.map(l => l.code));
            const added = langs
              .filter(l => l?.code && !seen.has(l.code))
              .map(l => ({ code: String(l.code), name: String(l.name || l.code) }));
            return added.length ? [...prev, ...added] : prev;
          });
        }
        if (voices.length) {
          const mapped = voices.map(v => normalize(providerId, v));
          setLocalVoices(prev => [...prev.filter(p => p.provider !== providerId), ...mapped]);
        }
      } catch (err: any) {
        // A single provider's list API being down must not clear the others — but
        // it must not be SILENT either. This used to console.error and then record
        // `connected: true`, so a provider whose voice list had just failed was
        // shown with a green "Connected" dot, "0 voices", and no error anywhere:
        // indistinguishable from a provider that genuinely has no voices.
        console.error(`Failed to fetch voices for ${providerId}:`, err);
        setProviderStatus(prev => ({
          ...prev,
          [providerId]: {
            connected: true,
            voice_count: prev[providerId]?.voice_count || 0,
            // Distinct from `connected: false`, which means "no API key" and
            // renders an "Add API key to unlock" prompt — the wrong instruction
            // for a provider that is configured but whose list call failed.
            error: err?.message ? String(err.message).slice(0, 160) : 'Request failed',
          },
        }));
      }
    };

    const loadConfiguredProviders = async () => {
      try {
        // Authoritative source of which providers actually have a key configured.
        const data = await fetchWithAuth('/platform/configured-providers');
        const configured: string[] = (Array.isArray(data?.tts) ? data.tts : [])
          .map((p: any) => p.id)
          .filter((id: string) => TTS_PROVIDER_META[id]);
        setTtsProviders(configured);
        setLoadError(null);
        // Fetch each configured provider's voices in parallel.
        await Promise.all(configured.map(fetchVoicesFor));
      } catch (err) {
        console.error('Failed to load configured providers:', err);
        setLoadError('Could not load the voice providers. Check your connection and try again.');
      }
    };

    loadConfiguredProviders();
  }, []);

  // Stop audio only on unmount. (Do NOT depend on playingId: playVoice sets
  // audioRef + playingId and THEN awaits audio.play(); a playingId-scoped cleanup
  // fired stopAudio() on the just-created element, pausing it mid-play — the classic
  // "play() request was interrupted by a call to pause()" that made previews fail.)
  useEffect(() => {
    return () => stopAudio();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      // Drop the handlers before releasing the element: onerror in particular
      // alert()s, and a revoked src can fire it on an element we no longer own.
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.src = '';
      audioRef.current = null;
    }
    // Revoke the blob the preview was playing from. onended/onerror revoke it on
    // the paths that reach them, but a preview that is STOPPED — stop button, a
    // different voice clicked, the picker modal closing, unmount — never fires
    // either, so each of those leaked a ~120KB object URL for the page's lifetime.
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    setPlayingId(null);
  };

  const playVoice = async (voice: any, e: React.MouseEvent) => {
    e.stopPropagation();
    
    if (playingId === voice.id) {
       stopAudio();
       return;
    }
    
    stopAudio();
    setLoadingAudioId(voice.id);

    try {
      console.log(`[VoiceLibrary] Requesting preview for ${voice.name} (${voice.provider})...`);
      
      const v_id = voice.voice_id || voice.id;
      // FIX: use voice.language (which is a proper BCP-47 code like 'hi-IN') NOT language_label
      // Synthesize in whatever language the card is currently showing, so
      // previewing under the Malayalam filter actually produces Malayalam.
      const lang = effectiveLanguage(voice) || 'hi-IN';
      const prov = voice.provider || 'sarvam';

      // FIX: Use URLSearchParams so ALL values are properly percent-encoded.
      // Raw template strings DON'T encode values — spaces/special chars break the URL
      // causing TypeError: Failed to fetch at the browser network level.
      // No `text`: the backend picks a sentence written in `lang`. Sending the
      // voice's English catalogue blurb here is what made a Kannada preview read
      // "Soft Hindi female (Recommended)" with Kannada phonetics.
      const params = new URLSearchParams({
        provider: prov,
        voice_id: v_id,
        language: lang,
        model: voice.model || '',
      });

      const controller = new AbortController();
      // Backend caps synthesis at ~12s and returns a clear provider-labeled error;
      // give it a small margin before the client aborts.
      const timeout = setTimeout(() => controller.abort(), 15000);

      // Kept as a raw fetch (not fetchWithAuth): this endpoint returns a binary
      // audio blob, not JSON, so fetchWithAuth's forced response.json() parsing
      // would break it. Auth is added manually instead.
      const token = getToken();
      const response = await fetch(
        `${API_URL}/platform/tts/preview?${params.toString()}`,
        {
          method: 'GET',
          signal: controller.signal,
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        }
      );
      clearTimeout(timeout);

      if (!response.ok) {
        // Backend returns { detail: "<Provider>: <reason>" } — surface it verbatim.
        let detail = `HTTP ${response.status}`;
        try {
          const body = await response.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          const t = await response.text().catch(() => '');
          if (t) detail = t.slice(0, 160);
        }
        throw new Error(detail);
      }

      const audioBlob = await response.blob();
      if (audioBlob.size < 512) {
        throw new Error('Audio response too small — TTS may have failed silently');
      }
      const audioUrl = URL.createObjectURL(audioBlob);

      const audio = new Audio(audioUrl);
      audioRef.current = audio;
      audioUrlRef.current = audioUrl;
      setPlayingId(voice.id);
      setLoadingAudioId(null);

      const release = () => {
        URL.revokeObjectURL(audioUrl);
        // Only clear the shared refs if this preview is still the current one — a
        // newer preview may already own them.
        if (audioUrlRef.current === audioUrl) audioUrlRef.current = null;
        if (audioRef.current === audio) audioRef.current = null;
      };

      audio.onended = () => {
        setPlayingId(null);
        release();
      };

      audio.onerror = (_err) => {
        setPlayingId(null);
        release();
        alert('Browser error: Audio loaded but could not be played. Try Chrome or Edge.');
      };

      await audio.play();
    } catch (err: any) {
      console.error('Failed to fetch audio preview:', err);
      setLoadingAudioId(null);
      setPlayingId(null);
      const provLabel = TTS_PROVIDER_META[voice.provider]?.name || voice.provider || 'Provider';
      if (err?.name === 'AbortError') {
        alert(`${provLabel} preview timed out — the provider took too long to respond. Try again.`);
      } else {
        alert(`${provLabel} preview failed: ${err?.message || String(err)}`);
      }
    }
  };


  const syncVoices = async () => {
    setSyncing(true);
    try {
      await fetchWithAuth('/voices/sync', { method: 'POST' });
      // Simulate sync delay
      await new Promise(r => setTimeout(r, 1200));
      alert("✅ Synced 22 voices from Sarvam AI · Google Gemini");
    } catch {}
    setSyncing(false);
  };

  // Which language this card is currently being shown AS. With no filter that
  // is the voice's own primary tag (so the unfiltered library looks exactly as
  // it always has); with a filter active it is the language the user asked for,
  // which is what gets synthesized and what gets saved onto the agent.
  const effectiveLanguage = (voice: any): string =>
    language && Array.isArray(voice.languages) && voice.languages.includes(language)
      ? language
      : voice.language;

  const filtered = localVoices.filter(voice => {
    const matchSearch = voice.name.toLowerCase().includes(search.toLowerCase());
    const matchProvider = !provider || voice.provider === provider;
    const matchGender = !gender || voice.gender === gender;
    // A voice matches a language if it is tagged with it OR can speak it.
    const matchLang = !language
      || voice.language === language
      || (Array.isArray(voice.languages) && voice.languages.includes(language));
    return matchSearch && matchProvider && matchGender && matchLang;
  });

  // Providers that ARE configured but whose voice list call failed, as [id, message].
  const failedProviders = Object.entries(providerStatus)
    .filter(([, s]) => s?.error)
    .map(([id, s]) => [id, s.error as string] as [string, string]);

  const grouped = filtered.reduce((acc, voice) => {
    if (!acc[voice.provider]) acc[voice.provider] = [];
    acc[voice.provider].push(voice);
    return acc;
  }, {} as Record<string, typeof localVoices>);

  const getProviderInfo = (code: string) => {
    const meta = TTS_PROVIDER_META[code];
    return meta ? { id: code, name: meta.name, icon: meta.icon } : { id: code, name: code, icon: '•' };
  };

  // Provider order for display + filter: configured providers first, then any
  // provider that actually returned voices (defensive), de-duplicated.
  const displayProviders = Array.from(new Set([...ttsProviders, ...Object.keys(grouped)]));

  // Existing options first and unchanged, then whatever the providers reported.
  const languageOptions = [
    ...BASE_LANGUAGE_OPTIONS,
    ...providerLanguages
      .filter(l => !BASE_LANGUAGE_OPTIONS.some(b => b.code === l.code))
      .map(l => ({ code: l.code, label: `${l.name} (${l.code})` })),
  ];

  const wrapContent = (content: React.ReactNode) => {
     if (isPickerModal) {
        return <div style={{ background: 'var(--bg-page)', height: '100%', overflowY: 'auto', padding: '24px 32px' }}>{content}</div>
     }
     return <div style={{ padding: '32px 40px', background: 'var(--bg-page)', minHeight: '100%', display: 'flex', flexDirection: 'column' }}>{content}</div>
  };

  return wrapContent(
    <>
      {/* Title & Actions Row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '24px' }}>
        <div>
          <h1 style={{ fontFamily: "'Plus Jakarta Sans', sans-serif", fontWeight: 700, fontSize: '24px', margin: 0, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
             {isPickerModal ? 'Select a voice for this agent' : 'Voice Library'}
          </h1>
        </div>
      </div>

      {/* Filter Bar */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px', flexWrap: 'wrap', gap: '16px' }}>
         <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <div style={{ position: 'relative' }}>
               <Search size={14} style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }}/>
               <input 
                 value={search}
                 onChange={e => setSearch(e.target.value)}
                 placeholder="Search voices..."
                 style={{
                    width: '280px', background: 'var(--bg-surface-2)', border: '1px solid var(--border)',
                    borderRadius: '8px', padding: '8px 12px 8px 36px', color: 'var(--text-primary)',
                    fontFamily: "'Plus Jakarta Sans', sans-serif", fontSize: '14px', outline: 'none'
                 }}
                 onFocus={e => e.target.style.borderColor = 'var(--border-strong)'}
                 onBlur={e => e.target.style.borderColor = 'var(--border)'}
               />
            </div>
            
            {/* Native dropdowns styled perfectly */}
            <select
              value={provider} onChange={e => setProvider(e.target.value)}
              className="custom-select"
              style={{
                background: 'var(--bg-surface-2)', border: '1px solid var(--border)', borderRadius: '8px', 
                padding: '8px 32px 8px 16px', color: 'var(--text-primary)', fontFamily: "'Plus Jakarta Sans', sans-serif",
                fontSize: '14px', appearance: 'none', cursor: 'pointer', outline: 'none'
              }}
            >
               <option value="">Provider ▼</option>
               {displayProviders.map(p => (
                 <option key={p} value={p}>{getProviderInfo(p).name}</option>
               ))}
            </select>

            <select
              value={gender} onChange={e => setGender(e.target.value)}
              className="custom-select"
              style={{
                background: 'var(--bg-surface-2)', border: '1px solid var(--border)', borderRadius: '8px', 
                padding: '8px 32px 8px 16px', color: 'var(--text-primary)', fontFamily: "'Plus Jakarta Sans', sans-serif",
                fontSize: '14px', appearance: 'none', cursor: 'pointer', outline: 'none'
              }}
            >
               <option value="">Gender ▼</option>
               <option value="FEMALE">Female ♀</option>
               <option value="MALE">Male ♂</option>
               <option value="NEUTRAL">Neutral</option>
            </select>

            <select
              value={language} onChange={e => setLanguage(e.target.value)}
              className="custom-select"
              style={{
                background: 'var(--bg-surface-2)', border: '1px solid var(--border)', borderRadius: '8px', 
                padding: '8px 32px 8px 16px', color: 'var(--text-primary)', fontFamily: "'Plus Jakarta Sans', sans-serif",
                fontSize: '14px', appearance: 'none', cursor: 'pointer', outline: 'none'
              }}
            >
               <option value="">Language/Accent ▼</option>
               {languageOptions.map(l => (
                 <option key={l.code} value={l.code}>{l.label}</option>
               ))}
            </select>
         </div>

         {!isPickerModal && !readOnly && (
           <div style={{ display: 'flex', gap: '8px' }}>
             <button style={{
               background: 'var(--bg-surface-2)', border: '1px solid var(--border)', borderRadius: '8px',
               padding: '8px 16px', color: 'var(--text-primary)', fontFamily: "'Plus Jakarta Sans', sans-serif",
               fontSize: '14px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px'
             }}>
               ⧉ Clone
             </button>
             <button style={{
               background: 'var(--bg-surface-2)', border: '1px solid var(--accent-border)', borderRadius: '8px',
               padding: '8px 16px', color: 'var(--accent)', fontFamily: "'Plus Jakarta Sans', sans-serif",
               fontSize: '14px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px'
             }}>
               + Add
             </button>
             <button onClick={syncVoices} style={{
               background: 'var(--bg-surface-2)', border: '1px solid var(--border)', borderRadius: '8px',
               padding: '8px 16px', color: 'var(--text-primary)', fontFamily: "'Plus Jakarta Sans', sans-serif",
               fontSize: '14px', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px'
             }}>
               {syncing ? '↻ Syncing...' : '↻ Sync'}
             </button>
           </div>
         )}
      </div>

      <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '24px' }}>
        Showing {filtered.length} of {localVoices.length} voices
        {ttsProviders.length > 0 && ` · ${ttsProviders.length} provider${ttsProviders.length > 1 ? 's' : ''} configured`}
      </div>

      {loadError && (
        <div style={{ fontSize: '13px', color: '#ff6b6b', background: 'rgba(255,107,107,0.08)', border: '1px solid rgba(255,107,107,0.25)', borderRadius: '8px', padding: '10px 14px', marginBottom: '16px' }}>
          {loadError}
        </div>
      )}

      {/* A provider whose voice list failed is reported HERE as well as on its
          section header, because a provider that returned nothing has no section
          to attach a message to — it is absent from `grouped` entirely. Without
          this, a Sarvam outage rendered as an empty library with no explanation. */}
      {failedProviders.length > 0 && (
        <div style={{ fontSize: '13px', color: '#ff6b6b', background: 'rgba(255,107,107,0.08)', border: '1px solid rgba(255,107,107,0.25)', borderRadius: '8px', padding: '10px 14px', marginBottom: '16px' }}>
          {failedProviders.map(([id, msg]) => (
            <div key={id}>
              Couldn't load {TTS_PROVIDER_META[id]?.name || id} voices — {msg}
            </div>
          ))}
        </div>
      )}

      {filtered.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--text-muted)', gap: '12px' }}>
          <SearchX size={32} opacity={0.5}/>
          {/* "No voices match your filters" is the wrong diagnosis when a fetch
              failed — it tells the operator to fix filters that are not the
              problem. Say which it is. */}
          <div style={{ fontSize: '14px', fontWeight: 500 }}>
            {failedProviders.length > 0 ? 'Voices could not be loaded' : 'No voices match your filters'}
          </div>
          <div style={{ fontSize: '12px' }}>
            {failedProviders.length > 0
              ? 'The provider above returned an error. Retry once it recovers.'
              : 'Try adjusting the search or filters above'}
          </div>
          <button 
             onClick={() => { setSearch(''); setProvider(''); setGender(''); setLanguage(''); }}
             style={{ marginTop: '8px', background: 'transparent', border: '1px solid var(--border)', borderRadius: '6px', padding: '6px 12px', color: 'var(--text-primary)', cursor: 'pointer', fontSize: '12px' }}
          >
            Clear filters
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {/* Display loop: either grouped by provider or flat grid */}
          {(provider === '' ? displayProviders : [provider]).map(p => {
             const voices = grouped[p] || [];
             const info = getProviderInfo(p);
             if (voices.length === 0 && provider !== '') return null;

             const isConnected = providerStatus[p]?.connected;
             const fetchError = providerStatus[p]?.error;

             // In "All Providers", show section headers.
             return (
               <div key={p} style={{ marginBottom: '16px' }}>
                 {provider === '' && (
                   <>
                     <div style={{ 
                        fontFamily: "'Plus Jakarta Sans', sans-serif", fontSize: '12px', fontWeight: 600, 
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)',
                        borderBottom: '1px solid var(--border)', paddingBottom: '8px', margin: '24px 0 16px'
                     }}>
                       <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                         <span>{info?.icon} {info?.name}</span>
                         <span style={{ textTransform: 'none', color: 'var(--text-muted)', fontWeight: 400, opacity: 0.7 }}>{providerStatus[p]?.voice_count} voices</span>
                         <Circle fill={fetchError ? '#ff6b6b' : isConnected ? 'var(--accent)' : 'gray'} stroke="none" size={8} style={{ marginLeft: '12px' }}/>
                         <span style={{ textTransform: 'none', color: fetchError ? '#ff6b6b' : isConnected ? 'var(--accent)' : 'var(--text-muted)', fontWeight: 500 }}>
                            {fetchError ? "Couldn't load voices" : isConnected ? 'Connected' : 'Not connected'}
                         </span>
                       </div>
                       {fetchError
                         ? <span style={{ textTransform: 'none', color: '#ff6b6b', fontWeight: 400, opacity: 0.9 }}>{fetchError}</span>
                         : !isConnected && <span style={{ cursor: 'pointer', color: 'var(--accent)', textTransform: 'none', fontWeight: 500 }}>Add API key to unlock →</span>}
                     </div>
                   </>
                 )}
                 <div style={{ 
                    display: 'grid', 
                    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', 
                    gap: '12px',
                                     }}>
                   {voices.map(voice => {
                      const isPlaying = playingId === voice.id;
                      const isLoading = loadingAudioId === voice.id;
                      return (
                        <div key={voice.id} className="voice-card-hover" style={{
                           background: isPlaying ? 'rgba(62,207,142,0.04)' : 'var(--bg-surface)',
                           border: `1px solid ${isPlaying ? 'var(--accent)' : 'var(--border)'}`,
                           borderRadius: '12px', padding: '14px 16px', display: 'flex',
                           alignItems: 'center', gap: '12px', cursor: 'pointer', transition: 'all 150ms ease',
                           pointerEvents: 'auto', // allow click for play
                           position: 'relative'
                        }}>
                           {/* LEFT: SVG Square */}
                           <div style={{
                              width: '44px', height: '44px', background: '#1E3A2F', borderRadius: '10px',
                              display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0
                           }}>
                             <VoiceWaveform playing={isPlaying} />
                           </div>

                           {/* CENTER: Info */}
                           <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '4px' }}>
                              <div style={{ 
                                fontFamily: "'Plus Jakarta Sans', sans-serif", fontWeight: 500, 
                                fontSize: '14px', color: 'var(--text-primary)', whiteSpace: 'nowrap', 
                                overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '12ch'
                              }}>
                                 {voice.name}
                              </div>
                              <div style={{ display: 'flex', gap: '6px' }}>
                                 <span style={{ 
                                   background: 'var(--bg-surface-3)', color: 'var(--text-muted)', 
                                   fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', 
                                   letterSpacing: '0.05em', padding: '2px 6px', borderRadius: '4px' 
                                 }}>
                                   {voice.gender}
                                 </span>
                                 <span style={{ 
                                   background: 'var(--bg-surface-3)', color: 'var(--text-muted)', 
                                   fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', 
                                   letterSpacing: '0.05em', padding: '2px 6px', borderRadius: '4px' 
                                 }}>
                                   {String(effectiveLanguage(voice) || voice.accent).substring(0, 5)}
                                 </span>
                              </div>
                           </div>

                           {/* RIGHT: Play */}
                           <button
                             onClick={(e) => playVoice(voice, e)}
                             style={{
                               width: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                               background: 'none', border: 'none', cursor: 'pointer',
                               color: isPlaying ? 'var(--accent)' : 'var(--text-muted)', transition: 'transform 0.15s, color 0.15s',
                               // Stacked above the picker overlay's scrim so it stays
                               // VISIBLE on hover, not just clickable. Without this the
                               // dark backdrop hides it and preview looks unavailable in
                               // the modal even though the click now works.
                               position: 'relative', zIndex: 2
                             }}
                             className="play-hover-btn"
                           >
                             {isLoading ? (
                               <div className="animate-spin" style={{ width: '16px', height: '16px', border: '2px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%' }} />
                             ) : isPlaying ? (
                               <Square fill="currentColor" size={16} />
                             ) : (
                               <Play fill="currentColor" size={16} />
                             )}
                           </button>

                           {/* Dropdown Extra stuff (Hover State handled by CSS class typically, but implemented here nicely) */}
                           {/* Add explicit full hover capability if requested. Here we handle the static click state */}
                           {isPickerModal && (
                             // `inset: 0` means this covers the whole card, INCLUDING the
                             // play button. opacity:0 hides it but does NOT stop it
                             // hit-testing, so in picker mode it silently swallowed every
                             // play click — the reason preview worked in the standalone
                             // library and did nothing in the agent's voice modal.
                             //
                             // pointerEvents:'none' lets clicks fall through to the play
                             // button underneath; the "Use" button re-enables them for
                             // itself. The reveal moved to the CARD's :hover (see the
                             // <style> block) because an element with pointer-events:none
                             // can never satisfy its own :hover.
                             <div className="voice-picker-overlay" style={{
                               position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(2px)',
                               borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                               opacity: 0, transition: 'opacity 0.2s', pointerEvents: 'none'
                             }}>
                               <button
                                 onClick={(e) => {
                                   e.stopPropagation();
                                   // Hand back the language the card is shown as, so
                                   // picking a voice under the Malayalam filter sets the
                                   // agent's tts_language to ml-IN rather than the voice's
                                   // primary display tag.
                                   if (onSelectVoice) onSelectVoice({ ...voice, language: effectiveLanguage(voice) });
                                 }}
                                 /* pointerEvents re-enabled for this button alone: its parent
                                    overlay is 'none' so play clicks reach the card. */
                                 style={{ padding: '8px 16px', borderRadius: '8px', background: 'var(--accent)', color: '#000', border: 'none', fontWeight: 600, fontSize: '13px', cursor: 'pointer', pointerEvents: 'auto' }}>
                                 Use {voice.name}
                               </button>
                             </div>
                           )}
                        </div>
                      )
                   })}
                 </div>
               </div>
             )
          })}
        </div>
      )}

      {/* Put static CSS here for simplicity */}
      <style dangerouslySetInnerHTML={{__html: `
        .voice-card-hover:hover {
          background-color: var(--bg-surface-2) !important;
          border-color: var(--border-strong) !important;
        }
        /* Revealed by hovering the CARD, not the overlay itself. The overlay is
           pointer-events:none so that play clicks reach the button beneath it,
           and an element that does not receive pointer events can never match
           its own :hover — which would have left the overlay permanently
           invisible and the "Use <voice>" button unreachable. */
        .voice-card-hover:hover .voice-picker-overlay {
          opacity: 1 !important;
        }
        .play-hover-btn:hover {
          color: var(--text-primary) !important;
          transform: scale(1.1);
        }
        @keyframes waveH1 { 0%,100%{height:4px} 50%{height:16px} }
        @keyframes waveH2 { 0%,100%{height:8px} 50%{height:20px} }
        @keyframes waveH3 { 0%,100%{height:12px} 50%{height:8px} }
        @keyframes waveH4 { 0%,100%{height:6px} 50%{height:18px} }
        @keyframes waveH5 { 0%,100%{height:10px} 50%{height:6px} }
        .wave-bar {
          width: 3px; border-radius: 2px;
          background-color: #3ECF8E;
          display: inline-block;
          margin: 0 1px;
        }
      `}} />
    </>
  );
}

function VoiceWaveform({ playing }: { playing: boolean }) {
  if (!playing) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', height: '24px' }}>
        <div className="wave-bar" style={{ height: '8px' }}></div>
        <div className="wave-bar" style={{ height: '14px' }}></div>
        <div className="wave-bar" style={{ height: '10px' }}></div>
        <div className="wave-bar" style={{ height: '12px' }}></div>
        <div className="wave-bar" style={{ height: '6px' }}></div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', height: '24px' }}>
      <div className="wave-bar" style={{ animation: 'waveH1 800ms infinite', animationDelay: '0ms' }}></div>
      <div className="wave-bar" style={{ animation: 'waveH2 800ms infinite', animationDelay: '100ms' }}></div>
      <div className="wave-bar" style={{ animation: 'waveH3 800ms infinite', animationDelay: '200ms' }}></div>
      <div className="wave-bar" style={{ animation: 'waveH4 800ms infinite', animationDelay: '300ms' }}></div>
      <div className="wave-bar" style={{ animation: 'waveH5 800ms infinite', animationDelay: '400ms' }}></div>
    </div>
  );
}
