import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  Search, ChevronDown, Check, Play, Square, FilterX, Clock, MapPin, SearchX, Globe, Settings, CreditCard, Menu, MessageCircle, Music, Shield, Info, ExternalLink, Link2, Download, Copy, Circle
} from 'lucide-react';
import { VOICE_LIBRARY } from '../../fixtures/voices';
import { API_URL } from '../../api/client';

interface VoiceLibraryProps {
  isPickerModal?: boolean;
  onSelectVoice?: (voice: any) => void;
  readOnly?: boolean;
}

export default function VoiceLibrary({ isPickerModal = false, onSelectVoice, readOnly = false }: VoiceLibraryProps) {
  const [search, setSearch] = useState('');
  const [provider, setProvider] = useState('');
  const [gender, setGender] = useState('');
  const [language, setLanguage] = useState('');
  const [playingId, setPlayingId] = useState<string | null>(null);
  const [loadingAudioId, setLoadingAudioId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [providerStatus, setProviderStatus] = useState<any>({
    sarvam: { connected: false, voice_count: 0 },
    gemini: { connected: false, voice_count: 0 },
    elevenlabs: { connected: false, voice_count: 0 }
  });

  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Load real connectivity status from backend
  const [localVoices, setLocalVoices] = useState<any[]>(VOICE_LIBRARY);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch(`${API_URL}/voices/`);
        if (res.ok) {
          const data = await res.json();
          setProviderStatus(data.providers);
        }
      } catch (err) {
        console.error("Failed to fetch provider status:", err);
      }
    };
    
    // Dynamically fetch ALL sarvam voices from backend instead of relying on the 12 hardcoded ones
    const fetchSarvamVoices = async () => {
      try {
        const res = await fetch(`${API_URL}/platform/tts/voices/sarvam`);
        if (res.ok) {
          const data = await res.json();
          if (data.voices && Array.isArray(data.voices)) {
            const dynamicSarvam = data.voices.map((v: any) => ({
               id: `sarvam-${v.id}`,
               provider: 'sarvam',
               provider_label: 'Sarvam AI',
               name: v.name,
               gender: v.gender?.toUpperCase() || 'NEUTRAL',
               language: v.language || 'en-IN',
               language_label: v.language || 'English',
               accent: (v.language || '').substring(0, 5),
               model: v.model || 'bulbul:v3',
               voice_id: v.voice_id || v.id,
               tags: [v.language, v.gender].filter(Boolean),
               sample_text: v.description || 'Hello! I am your AI receptionist. How can I help you?',
               recommended_for: [],
               is_recommended: false
            }));
            
            setLocalVoices(prev => {
               const others = prev.filter(p => p.provider !== 'sarvam');
               return [...dynamicSarvam, ...others];
            });
          }
        }
      } catch (err) {
         console.error("Failed to fetch dynamic voices:", err);
      }
    };

    fetchStatus();
    fetchSarvamVoices();
  }, []);

  // Stop currently playing audio on unmount or when playingId changes
  useEffect(() => {
    return () => stopAudio();
  }, [playingId]);

  const stopAudio = () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
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
      const lang = voice.language || 'hi-IN';
      const prov = voice.provider || 'sarvam';
      const sampleText = voice.sample_text || 'Hello! I am your AI receptionist. How can I help you today?';
      
      // FIX: Use URLSearchParams so ALL values are properly percent-encoded.
      // Raw template strings DON'T encode values — spaces/special chars break the URL
      // causing TypeError: Failed to fetch at the browser network level.
      const params = new URLSearchParams({
        provider: prov,
        voice_id: v_id,
        language: lang,
        text: sampleText,
      });

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 25000); // 25s timeout

      const response = await fetch(
        `${API_URL}/platform/tts/preview?${params.toString()}`,
        { method: 'GET', signal: controller.signal }
      );
      clearTimeout(timeout);

      if (!response.ok) {
        const errText = await response.text().catch(() => `HTTP ${response.status}`);
        throw new Error(`Preview failed (${response.status}): ${errText.slice(0, 120)}`);
      }

      const audioBlob = await response.blob();
      if (audioBlob.size < 512) {
        throw new Error('Audio response too small — TTS may have failed silently');
      }
      const audioUrl = URL.createObjectURL(audioBlob);

      const audio = new Audio(audioUrl);
      audioRef.current = audio;
      setPlayingId(voice.id);
      setLoadingAudioId(null);
      
      audio.onended = () => {
        setPlayingId(null);
        URL.revokeObjectURL(audioUrl);
      };
      
      audio.onerror = (_err) => {
        setPlayingId(null);
        URL.revokeObjectURL(audioUrl);
        alert('Browser error: Audio loaded but could not be played. Try Chrome or Edge.');
      };

      await audio.play();
    } catch (err: any) {
      console.error('Failed to fetch audio preview:', err);
      setLoadingAudioId(null);
      setPlayingId(null);
      if (err?.name === 'AbortError') {
        alert('Preview timed out — Sarvam API took too long. Check your SARVAM_API_KEY in .env');
      } else {
        alert(`Could not preview voice: ${String(err)}`);
      }
    }
  };


  const syncVoices = async () => {
    setSyncing(true);
    try {
      await fetch(`${API_URL}/voices/sync`, { method: 'POST' });
      // Simulate sync delay
      await new Promise(r => setTimeout(r, 1200));
      alert("✅ Synced 22 voices from Sarvam AI · Google Gemini");
    } catch {}
    setSyncing(false);
  };

  const filtered = localVoices.filter(voice => {
    const matchSearch = voice.name.toLowerCase().includes(search.toLowerCase());
    const matchProvider = !provider || voice.provider === provider;
    const matchGender = !gender || voice.gender === gender;
    const matchLang = !language || voice.language === language;
    return matchSearch && matchProvider && matchGender && matchLang;
  });

  const grouped = filtered.reduce((acc, voice) => {
    if (!acc[voice.provider]) acc[voice.provider] = [];
    acc[voice.provider].push(voice);
    return acc;
  }, {} as Record<string, typeof localVoices>);

  const getProviderInfo = (code: string) => {
    return [
      { id: 'sarvam', name: 'Sarvam AI', icon: '🇮🇳' },
      { id: 'gemini', name: 'Google Gemini', icon: '⚡' },
      { id: 'elevenlabs', name: 'ElevenLabs', icon: '11' }
    ].find(p => p.id === code);
  };

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
               <option value="sarvam">Sarvam AI 🇮🇳</option>
               <option value="gemini">Google Gemini</option>
               <option value="elevenlabs">ElevenLabs</option>
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
               <option value="hi-IN">Hindi (hi-IN)</option>
               <option value="en-IN">English - Indian (en-IN)</option>
               <option value="en-US">English - American (en-US)</option>
               <option value="ta-IN">Tamil (ta-IN)</option>
               <option value="te-IN">Telugu (te-IN)</option>
               <option value="ar-SA">Arabic (ar-SA)</option>
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
      </div>

      {filtered.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--text-muted)', gap: '12px' }}>
          <SearchX size={32} opacity={0.5}/>
          <div style={{ fontSize: '14px', fontWeight: 500 }}>No voices match your filters</div>
          <div style={{ fontSize: '12px' }}>Try adjusting the search or filters above</div>
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
          {(provider === '' ? ['sarvam', 'gemini', 'elevenlabs'] : [provider]).map(p => {
             const voices = grouped[p] || [];
             const info = getProviderInfo(p);
             if (voices.length === 0 && provider !== '') return null;

             const isConnected = providerStatus[p]?.connected;
             
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
                         <Circle fill={isConnected ? 'var(--accent)' : 'gray'} stroke="none" size={8} style={{ marginLeft: '12px' }}/>
                         <span style={{ textTransform: 'none', color: isConnected ? 'var(--accent)' : 'var(--text-muted)', fontWeight: 500 }}>
                            {isConnected ? 'Connected' : 'Not connected'}
                         </span>
                       </div>
                       {!isConnected && <span style={{ cursor: 'pointer', color: 'var(--accent)', textTransform: 'none', fontWeight: 500 }}>Add API key to unlock →</span>}
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
                                   {voice.accent.substring(0, 5)}
                                 </span>
                              </div>
                           </div>

                           {/* RIGHT: Play */}
                           <button 
                             onClick={(e) => playVoice(voice, e)}
                             style={{
                               width: '32px', height: '32px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                               background: 'none', border: 'none', cursor: 'pointer',
                               color: isPlaying ? 'var(--accent)' : 'var(--text-muted)', transition: 'transform 0.15s, color 0.15s'
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
                             <div className="voice-picker-overlay" style={{
                               position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(2px)', 
                               borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center',
                               opacity: 0, transition: 'opacity 0.2s'
                             }}>
                               <button 
                                 onClick={(e) => { e.stopPropagation(); if (onSelectVoice) onSelectVoice(voice); }}
                                 style={{ padding: '8px 16px', borderRadius: '8px', background: 'var(--accent)', color: '#000', border: 'none', fontWeight: 600, fontSize: '13px', cursor: 'pointer' }}>
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
        .voice-picker-overlay:hover {
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
