export const AI_PROVIDERS = {
  STT: [
    { id: 'sarvam', name: 'Sarvam AI', recommended: true, models: [
      { id: 'saarika:v2', name: 'saarika:v2', tags: ['Best for Indian languages', 'Recommended'] }
    ]},
    { id: 'deepgram', name: 'Deepgram', models: [
      { id: 'nova-2', name: 'nova-2', tags: ['Fastest'] },
      { id: 'nova-2-medical', name: 'nova-2-medical', tags: ['High Accuracy'] }
    ]},
    { id: 'openai', name: 'OpenAI', models: [
      { id: 'whisper-1', name: 'whisper-1', tags: [] }
    ]},
    { id: 'google', name: 'Google Cloud', models: [
      { id: 'chirp', name: 'Chirp', tags: [] }
    ]}
  ],
  LLM: [
    { id: 'google', name: 'Google Gemini', recommended: true, models: [
      { id: 'gemini-2.5-flash', name: 'gemini-2.5-flash', tags: ['Fastest', 'Recommended'] },
      { id: 'gemini-2.5-pro', name: 'gemini-2.5-pro', tags: ['Best Quality'] },
      { id: 'gemini-1.5-flash', name: 'gemini-1.5-flash', tags: ['Cheapest'] },
      { id: 'gemini-1.5-pro', name: 'gemini-1.5-pro', tags: [] }
    ]},
    { id: 'openai', name: 'OpenAI', models: [
      { id: 'gpt-4o-mini', name: 'gpt-4o-mini', tags: ['Recommended'] },
      { id: 'gpt-4o', name: 'gpt-4o', tags: [] },
      { id: 'gpt-4-turbo', name: 'gpt-4-turbo', tags: [] },
      { id: 'o1-mini', name: 'o1-mini', tags: [] },
      { id: 'o3-mini', name: 'o3-mini', tags: [] }
    ]},
    { id: 'anthropic', name: 'Anthropic', models: [
      { id: 'claude-3-5-haiku', name: 'claude-3-5-haiku', tags: ['Recommended', 'Fastest'] },
      { id: 'claude-3-5-sonnet', name: 'claude-3-5-sonnet', tags: ['Best Quality'] },
      { id: 'claude-opus-4-5', name: 'claude-opus-4-5', tags: [] }
    ]}
  ],
  TTS: [
    { id: 'sarvam', name: 'Sarvam AI', recommended: true, models: [
      { id: 'bulbul:v1', name: 'bulbul:v1', tags: ['Best for Indian languages', 'Recommended'] }
    ]},
    { id: 'elevenlabs', name: 'ElevenLabs', models: [
      { id: 'eleven_multilingual_v2', name: 'eleven_multilingual_v2', tags: ['Best Quality'] },
      { id: 'eleven_turbo_v2_5', name: 'eleven_turbo_v2_5', tags: ['Fastest'] }
    ]},
    { id: 'openai', name: 'OpenAI TTS', models: [
      { id: 'tts-1', name: 'tts-1', tags: ['Fastest'] },
      { id: 'tts-1-hd', name: 'tts-1-hd', tags: ['High Quality'] }
    ]}
  ]
};
