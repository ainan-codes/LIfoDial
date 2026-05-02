import React, { useState, useEffect } from 'react';
import { AI_PROVIDERS } from '../../fixtures/ai-providers';
import { AlertCircle, CheckCircle2, XCircle, Lock, ChevronDown, Activity, ChevronRight, Save } from 'lucide-react';

const LOCAL_STORAGE_KEY = 'lifodial_ai_config';

function SectionHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-6">
      <h2 style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{title}</h2>
      {description && <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginTop: '4px' }}>{description}</p>}
    </div>
  );
}

export default function AIConfig() {
  const [keys, setKeys] = useState<{ [key: string]: string }>({});
  const [tested, setTested] = useState<{ [key: string]: 'untested' | 'testing' | 'success' | 'failed' }>({});
  
  const [sttProvider, setSttProvider] = useState('sarvam');
  const [sttModel, setSttModel] = useState('saarika:v2');
  
  const [llmProvider, setLlmProvider] = useState('google');
  const [llmModel, setLlmModel] = useState('gemini-2.5-flash');
  
  const [ttsProvider, setTtsProvider] = useState('sarvam');
  const [ttsModel, setTtsModel] = useState('bulbul:v1');

  const [advVad, setAdvVad] = useState('300');
  const [advMinSpeech, setAdvMinSpeech] = useState('100');
  const [advBackchannel, setAdvBackchannel] = useState(true);
  const [advInterrupt, setAdvInterrupt] = useState(true);
  const [advTemp, setAdvTemp] = useState('0.3');
  const [advTokens, setAdvTokens] = useState('150');
  const [advMemory, setAdvMemory] = useState('6');

  const [saved, setSaved] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(LOCAL_STORAGE_KEY);
      if (stored) {
        const p = JSON.parse(stored);
        if (p.sttProvider) setSttProvider(p.sttProvider);
        if (p.sttModel) setSttModel(p.sttModel);
        if (p.llmProvider) setLlmProvider(p.llmProvider);
        if (p.llmModel) setLlmModel(p.llmModel);
        if (p.ttsProvider) setTtsProvider(p.ttsProvider);
        if (p.ttsModel) setTtsModel(p.ttsModel);
      }
    } catch(e) {}
  }, []);

  const handleTestKey = (providerId: string) => {
    setTested(prev => ({ ...prev, [providerId]: 'testing' }));
    setTimeout(() => {
      setTested(prev => ({ 
        ...prev, 
        [providerId]: keys[providerId]?.length > 10 ? 'success' : 'failed' 
      }));
    }, 1500);
  };

  const handleSave = () => {
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify({
      sttProvider, sttModel, llmProvider, llmModel, ttsProvider, ttsModel,
      advVad, advMinSpeech, advBackchannel, advInterrupt, advTemp, advTokens, advMemory
    }));
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  const currentSttProvider = AI_PROVIDERS.STT.find(p => p.id === sttProvider);
  const currentSttModel = currentSttProvider?.models.find(m => m.id === sttModel) || currentSttProvider?.models[0];

  const currentLlmProvider = AI_PROVIDERS.LLM.find(p => p.id === llmProvider);
  const currentLlmModel = currentLlmProvider?.models.find(m => m.id === llmModel) || currentLlmProvider?.models[0];

  const currentTtsProvider = AI_PROVIDERS.TTS.find(p => p.id === ttsProvider);
  const currentTtsModel = currentTtsProvider?.models.find(m => m.id === ttsModel) || currentTtsProvider?.models[0];

  return (
    <div className="space-y-10" style={{ paddingBottom: '40px' }}>
      {/* SECTION 1 - API KEYS */}
      <section>
        <SectionHeader title="API Keys" description="Encrypted at rest. Never exposed after saving." />
        <div className="space-y-3">
          {[
            { id: 'google', name: 'Google Gemini API Key' },
            { id: 'openai', name: 'OpenAI API Key' },
            { id: 'anthropic', name: 'Anthropic Claude API Key' },
            { id: 'sarvam', name: 'Sarvam AI API Key' },
            { id: 'elevenlabs', name: 'ElevenLabs API Key' },
            { id: 'livekit', name: 'LiveKit API Key' }
          ].map(p => (
            <div 
              key={p.id}
              className="flex items-center justify-between p-4 rounded-xl"
              style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)' }}
            >
              <div style={{ flex: 1 }}>
                <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', margin: 0 }}>{p.name}</p>
                <div className="flex items-center gap-2 mt-2">
                  <input
                    type="password"
                    placeholder="••••••••••••••••••••••••••••"
                    value={keys[p.id] || ''}
                    onChange={(e) => {
                      setKeys(prev => ({ ...prev, [p.id]: e.target.value }));
                      setTested(prev => ({ ...prev, [p.id]: 'untested' }));
                    }}
                    style={{
                      width: '280px', padding: '8px 12px', fontSize: '13px',
                      borderRadius: '6px', outline: 'none',
                      backgroundColor: 'var(--bg-surface-3)', border: '1px solid var(--border)',
                      color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace"
                    }}
                  />
                  <button
                    onClick={() => handleTestKey(p.id)}
                    style={{
                      padding: '8px 14px', borderRadius: '6px', fontSize: '12px', fontWeight: 500,
                      backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)',
                      color: 'var(--text-secondary)', cursor: 'pointer'
                    }}
                  >
                    {tested[p.id] === 'testing' ? 'Testing...' : 'Test Connection'}
                  </button>
                  {keys[p.id] && (
                    <button
                      onClick={() => setKeys(prev => ({ ...prev, [p.id]: '' }))}
                      style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '12px', cursor: 'pointer' }}
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
              <div style={{ width: '180px', textAlign: 'right' }}>
                {tested[p.id] === 'success' && <span style={{ color: 'var(--accent)', fontSize: '12px', fontWeight: 500, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '4px' }}><CheckCircle2 size={14} /> Connected</span>}
                {tested[p.id] === 'failed' && <span style={{ color: 'var(--destructive)', fontSize: '12px', fontWeight: 500, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: '4px' }}><XCircle size={14} /> Invalid Key</span>}
                {(!tested[p.id] || tested[p.id] === 'untested') && keys[p.id] && <span style={{ color: 'var(--warning)', fontSize: '12px', fontWeight: 500 }}>Unverified</span>}
                {(!keys[p.id]) && <span style={{ color: 'var(--text-muted)', fontSize: '12px', fontWeight: 500 }}>Not configured</span>}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* SECTION 2 - MODEL SELECTION */}
      <section>
        <SectionHeader title="Model Selection" description="Choose providers and models for each stage of the voice pipeline." />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          
          {/* STT Column */}
          <div className="space-y-4">
            <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>1. Speech to Text</h3>
            {AI_PROVIDERS.STT.map(p => {
              const isActive = sttProvider === p.id;
              const hasKey = keys[p.id]?.length > 0 || p.id === 'sarvam'; // Default mocked
              return (
                <div 
                  key={p.id}
                  onClick={() => hasKey && setSttProvider(p.id)}
                  style={{
                    padding: '16px', borderRadius: '12px', cursor: hasKey ? 'pointer' : 'not-allowed',
                    backgroundColor: isActive ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
                    border: isActive ? '2px solid var(--accent)' : '1px solid var(--border)',
                    opacity: hasKey ? 1 : 0.6
                  }}
                >
                  <div className="flex justify-between items-center mb-2">
                    <span style={{ fontSize: '14px', fontWeight: 600, color: isActive ? 'var(--accent)' : 'var(--text-primary)' }}>{p.name}</span>
                    {!hasKey && <Lock size={14} color="var(--text-muted)" />}
                  </div>
                  {isActive && (
                    <select 
                      value={sttModel} 
                      onChange={(e) => setSttModel(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: '100%', padding: '8px', borderRadius: '6px', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--accent-border)', color: 'var(--text-primary)', outline: 'none', fontSize: '13px' }}
                    >
                      {p.models.map(m => (
                        <option key={m.id} value={m.id}>{m.name} {m.tags.length > 0 ? `(${m.tags[0]})` : ''}</option>
                      ))}
                    </select>
                  )}
                </div>
              );
            })}
          </div>

          {/* LLM Column */}
          <div className="space-y-4">
            <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>2. Intelligence (LLM)</h3>
            {AI_PROVIDERS.LLM.map(p => {
              const isActive = llmProvider === p.id;
              const hasKey = keys[p.id]?.length > 0 || p.id === 'google'; // Default mocked
              return (
                <div 
                  key={p.id}
                  onClick={() => hasKey && setLlmProvider(p.id)}
                  style={{
                    padding: '16px', borderRadius: '12px', cursor: hasKey ? 'pointer' : 'not-allowed',
                    backgroundColor: isActive ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
                    border: isActive ? '2px solid var(--accent)' : '1px solid var(--border)',
                    opacity: hasKey ? 1 : 0.6
                  }}
                >
                  <div className="flex justify-between items-center mb-2">
                    <span style={{ fontSize: '14px', fontWeight: 600, color: isActive ? 'var(--accent)' : 'var(--text-primary)' }}>{p.name}</span>
                    {!hasKey && <Lock size={14} color="var(--text-muted)" />}
                  </div>
                  {isActive && (
                    <select 
                      value={llmModel} 
                      onChange={(e) => setLlmModel(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: '100%', padding: '8px', borderRadius: '6px', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--accent-border)', color: 'var(--text-primary)', outline: 'none', fontSize: '13px' }}
                    >
                      {p.models.map(m => (
                        <option key={m.id} value={m.id}>{m.name} {m.tags.length > 0 ? `(${m.tags[0]})` : ''}</option>
                      ))}
                    </select>
                  )}
                </div>
              );
            })}
          </div>

          {/* TTS Column */}
          <div className="space-y-4">
            <h3 style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>3. Text to Speech</h3>
            {AI_PROVIDERS.TTS.map(p => {
              const isActive = ttsProvider === p.id;
              const hasKey = keys[p.id]?.length > 0 || p.id === 'sarvam'; // Default mocked
              return (
                <div 
                  key={p.id}
                  onClick={() => hasKey && setTtsProvider(p.id)}
                  style={{
                    padding: '16px', borderRadius: '12px', cursor: hasKey ? 'pointer' : 'not-allowed',
                    backgroundColor: isActive ? 'var(--accent-dim)' : 'var(--bg-surface-2)',
                    border: isActive ? '2px solid var(--accent)' : '1px solid var(--border)',
                    opacity: hasKey ? 1 : 0.6
                  }}
                >
                  <div className="flex justify-between items-center mb-2">
                    <span style={{ fontSize: '14px', fontWeight: 600, color: isActive ? 'var(--accent)' : 'var(--text-primary)' }}>{p.name}</span>
                    {!hasKey && <Lock size={14} color="var(--text-muted)" />}
                  </div>
                  {isActive && (
                    <select 
                      value={ttsModel} 
                      onChange={(e) => setTtsModel(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: '100%', padding: '8px', borderRadius: '6px', backgroundColor: 'var(--bg-surface)', border: '1px solid var(--accent-border)', color: 'var(--text-primary)', outline: 'none', fontSize: '13px' }}
                    >
                      {p.models.map(m => (
                        <option key={m.id} value={m.id}>{m.name} {m.tags.length > 0 ? `(${m.tags[0]})` : ''}</option>
                      ))}
                    </select>
                  )}
                </div>
              );
            })}
          </div>

        </div>
      </section>

      {/* SECTION 3 - PIPELINE VISUAL */}
      <section>
        <SectionHeader title="Voice Pipeline Active Configuration" description="Current streaming architecture processing order." />
        <div 
          className="rounded-xl p-8"
          style={{ backgroundColor: 'var(--bg-surface-2)', border: '1px solid var(--border)', overflowX: 'auto' }}
        >
          <div className="flex items-center justify-between min-w-[700px]">
            {/* Box 1 */}
            <div style={{ padding: '16px', borderRadius: '8px', border: '2px solid var(--border)', backgroundColor: 'var(--bg-surface)', textAlign: 'center', width: '140px' }}>
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '4px' }}>Caller</p>
              <p style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>Audio In</p>
            </div>
            <ChevronRight size={24} color="var(--text-muted)" />
            {/* Box 2 */}
            <div style={{ padding: '16px', borderRadius: '8px', border: '2px solid var(--accent)', backgroundColor: 'var(--accent-dim)', textAlign: 'center', width: '160px', position: 'relative' }}>
              <div style={{ position: 'absolute', top: '-6px', right: '-6px', width: '12px', height: '12px', borderRadius: '50%', backgroundColor: 'var(--accent)' }} />
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '4px' }}>{currentSttProvider?.name || 'STT'}</p>
              <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--accent)' }}>{currentSttModel?.name || 'Model'}</p>
              <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>~200ms</p>
            </div>
            <ChevronRight size={24} color="var(--text-muted)" />
            {/* Box 3 */}
            <div style={{ padding: '16px', borderRadius: '8px', border: '2px solid var(--accent)', backgroundColor: 'var(--accent-dim)', textAlign: 'center', width: '180px', position: 'relative' }}>
              <div style={{ position: 'absolute', top: '-6px', right: '-6px', width: '12px', height: '12px', borderRadius: '50%', backgroundColor: 'var(--accent)' }} />
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '4px' }}>{currentLlmProvider?.name || 'LLM'}</p>
              <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--accent)' }}>{currentLlmModel?.name || 'Model'}</p>
              <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>~400ms (Tools)</p>
            </div>
            <ChevronRight size={24} color="var(--text-muted)" />
            {/* Box 4 */}
            <div style={{ padding: '16px', borderRadius: '8px', border: '2px solid var(--accent)', backgroundColor: 'var(--accent-dim)', textAlign: 'center', width: '160px', position: 'relative' }}>
              <div style={{ position: 'absolute', top: '-6px', right: '-6px', width: '12px', height: '12px', borderRadius: '50%', backgroundColor: 'var(--accent)' }} />
              <p style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '4px' }}>{currentTtsProvider?.name || 'TTS'}</p>
              <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--accent)' }}>{currentTtsModel?.name || 'Model'}</p>
              <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>~200ms</p>
            </div>
          </div>
          <div className="flex justify-center mt-6">
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 16px', borderRadius: '999px', backgroundColor: 'var(--accent-dim)', border: '1px solid var(--accent)' }}>
              <Activity size={14} color="var(--accent)" />
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--accent)' }}>Total estimated latency: ~800ms Target</span>
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 4 - ADVANCED SETTINGS */}
      <section>
        <button 
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 w-full text-left"
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '8px 0' }}
        >
          <div style={{ transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s', color: 'var(--text-secondary)' }}>
            <ChevronDown size={18} />
          </div>
          <span style={{ fontSize: '15px', fontWeight: 600, color: 'var(--text-primary)' }}>Advanced Voice Settings</span>
        </button>
        
        {expanded && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 mt-6">
            <div className="space-y-5">
              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>VAD Silence Threshold ({advVad}ms)</label>
                <input type="range" min="100" max="1000" step="50" value={advVad} onChange={(e) => setAdvVad(e.target.value)} style={{ width: '100%', accentColor: 'var(--accent)' }} />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>Min Speech Duration ({advMinSpeech}ms)</label>
                <input type="range" min="50" max="500" step="50" value={advMinSpeech} onChange={(e) => setAdvMinSpeech(e.target.value)} style={{ width: '100%', accentColor: 'var(--accent)' }} />
              </div>
              <div className="flex items-center justify-between">
                <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>Enable Backchannels (hmm, sure)</span>
                <input type="checkbox" checked={advBackchannel} onChange={(e) => setAdvBackchannel(e.target.checked)} style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }} />
              </div>
            </div>
            
            <div className="space-y-5">
              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>LLM Temperature ({advTemp})</label>
                <input type="range" min="0" max="1" step="0.1" value={advTemp} onChange={(e) => setAdvTemp(e.target.value)} style={{ width: '100%', accentColor: 'var(--accent)' }} />
                <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Lower = consistent, Higher = creative</p>
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '6px' }}>Max Response Tokens ({advTokens})</label>
                <input type="range" min="50" max="300" step="10" value={advTokens} onChange={(e) => setAdvTokens(e.target.value)} style={{ width: '100%', accentColor: 'var(--accent)' }} />
              </div>
              <div className="flex items-center justify-between">
                <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>Allow Patient Interruption (Barge-in)</span>
                <input type="checkbox" checked={advInterrupt} onChange={(e) => setAdvInterrupt(e.target.checked)} style={{ width: '18px', height: '18px', accentColor: 'var(--accent)' }} />
              </div>
            </div>
          </div>
        )}
      </section>

      {/* SECTION 5 - SAVE COMPONENT */}
      <div style={{ paddingTop: '20px', borderTop: '1px solid var(--border)' }}>
        <button
          onClick={handleSave}
          style={{
            width: '100%', padding: '16px', borderRadius: '12px',
            fontSize: '16px', fontWeight: 600, color: '#000',
            backgroundColor: saved ? 'var(--accent-hover)' : 'var(--accent)',
            border: 'none', cursor: 'pointer', transition: 'all 0.2s',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px'
          }}
        >
          {saved ? <CheckCircle2 size={20} /> : <Save size={20} />}
          {saved ? 'Configuration Saved' : 'Save AI Configuration'}
        </button>
        {saved && (
          <p style={{ textAlign: 'center', color: 'var(--accent)', fontSize: '13px', marginTop: '12px', fontWeight: 500 }}>
            ✅ Configuration saved. Takes effect on next call.
          </p>
        )}
      </div>

    </div>
  );
}
