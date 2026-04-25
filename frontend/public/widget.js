/**
 * Lifodial AI Receptionist Widget v1.0
 * CDN: https://api.lifodial.com/widget.js
 * Usage: <script src="..." data-agent-id="agent-001"></script>
 *
 * No dependencies. Vanilla JS only. Works on any website.
 */
(function () {
  'use strict';

  // ── Config from script tag ──────────────────────────────────────────────────
  const script =
    document.currentScript ||
    document.querySelector('script[data-agent-id]');
  if (!script) return;

  const AGENT_ID = script.getAttribute('data-agent-id');
  // Detect API base dynamically from the script's own src URL.
  // Explicit data-api-url attribute takes priority.
  const _explicit = script.getAttribute('data-api-url');
  const _scriptSrc = script.src || '';
  const _scriptOrigin = _scriptSrc
    ? new URL(_scriptSrc).origin          // e.g. https://api.lifodial.com
    : window.location.origin;             // same-origin dev fallback
  const API_BASE = (_explicit !== null && _explicit !== '') ? _explicit : _scriptOrigin;
  const POSITION = script.getAttribute('data-position') || 'bottom-right';
  const THEME    = script.getAttribute('data-theme')    || 'dark';

  if (!AGENT_ID) {
    console.warn('[Lifodial] data-agent-id is required');
    return;
  }

  // ── State ───────────────────────────────────────────────────────────────────
  let config    = null;
  let isOpen    = false;
  let sessionId = 'visitor-' + Math.random().toString(36).slice(2, 14);
  let messages  = [];
  let isTyping  = false;
  let activeTab = 'chat';

  // ── Fetch agent config from backend ─────────────────────────────────────────
  async function loadConfig() {
    try {
      const res = await fetch(API_BASE + '/embed/' + AGENT_ID + '/config');
      if (!res.ok) return;
      config = await res.json();
      if (config.is_active === false) return; // agent not published
      injectWidget();
      // Track initial view
      track('widget_view');
    } catch (e) {
      // Silently fail — never break the clinic's website
    }
  }

  // ── Analytics fire-and-forget ────────────────────────────────────────────────
  function track(eventType) {
    fetch(API_BASE + '/embed/' + AGENT_ID + '/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: eventType,
        session_id: sessionId,
        domain: window.location.hostname,
        language: navigator.language || '',
      }),
    }).catch(() => {});
  }

  // ── Colour helpers ───────────────────────────────────────────────────────────
  function getColors() {
    const isDark    = THEME !== 'light';
    const primary   = (config && config.embed_primary_color) || '#3ECF8E';
    const bg        = isDark ? '#0F0F0F' : '#FFFFFF';
    const text      = isDark ? '#FFFFFF' : '#111111';
    const surface   = isDark ? '#1A1A1A' : '#F7F8FA';
    const border    = isDark ? '#2A2A2A' : '#E5E7EB';
    return { isDark, primary, bg, text, surface, border };
  }

  // ── Inject CSS ───────────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('lifodial-styles')) return;
    const { primary, bg, text, surface, border } = getColors();
    const isRight  = POSITION.includes('right');
    const isBottom = POSITION.includes('bottom');
    const hEdge    = isRight  ? 'right:20px' : 'left:20px';
    const vEdge    = isBottom ? 'bottom:20px' : 'top:20px';
    const wVEdge   = isBottom ? 'bottom:86px' : 'top:86px';

    const css = `
      #lfd-widget *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:0}
      /* ── Trigger button ── */
      #lfd-trigger{
        position:fixed;${hEdge};${vEdge};z-index:999998;
        display:flex;align-items:center;gap:9px;
        background:${primary};color:#000;
        border:none;border-radius:50px;
        padding:13px 22px;cursor:pointer;
        box-shadow:0 6px 28px rgba(0,0,0,0.28);
        font-size:14px;font-weight:700;white-space:nowrap;
        transition:transform .2s,box-shadow .2s;
      }
      #lfd-trigger:hover{transform:translateY(-3px);box-shadow:0 10px 32px rgba(0,0,0,0.35)}
      #lfd-trigger svg{width:18px;height:18px;flex-shrink:0}
      #lfd-trigger::before{
        content:'';position:absolute;top:-4px;right:-4px;
        width:12px;height:12px;background:#22C55E;
        border-radius:50%;border:2px solid ${bg};
        animation:lfd-pulse 2s infinite;
      }
      @keyframes lfd-pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.65;transform:scale(1.15)}}
      /* ── Chat window ── */
      #lfd-window{
        position:fixed;${hEdge};${wVEdge};z-index:999999;
        width:360px;max-height:540px;
        background:${bg};border:1px solid ${border};
        border-radius:20px;overflow:hidden;
        display:none;flex-direction:column;
        box-shadow:0 24px 64px rgba(0,0,0,.35);
      }
      #lfd-window.open{display:flex;animation:lfd-up .22s ease}
      @keyframes lfd-up{from{opacity:0;transform:translateY(18px) scale(.96)}to{opacity:1;transform:none}}
      /* ── Header ── */
      #lfd-header{
        padding:14px 16px;
        background:${surface};border-bottom:1px solid ${border};
        display:flex;align-items:center;justify-content:space-between;flex-shrink:0;
      }
      #lfd-agent-info{display:flex;align-items:center;gap:10px}
      #lfd-avatar{
        width:38px;height:38px;border-radius:50%;
        background:${primary};display:flex;align-items:center;
        justify-content:center;font-size:18px;flex-shrink:0;
      }
      #lfd-agent-name{font-size:14px;font-weight:700;color:${text}}
      #lfd-online{font-size:11px;color:#22C55E;display:flex;align-items:center;gap:4px;margin-top:2px}
      #lfd-online::before{content:'';width:6px;height:6px;background:#22C55E;border-radius:50%;display:inline-block}
      #lfd-close{background:none;border:none;color:${text};cursor:pointer;opacity:.45;font-size:20px;line-height:1;transition:opacity .15s}
      #lfd-close:hover{opacity:1}
      /* ── Tabs ── */
      #lfd-tabs{display:flex;border-bottom:1px solid ${border};flex-shrink:0}
      .lfd-tab{
        flex:1;padding:10px 8px;background:none;
        border:none;border-bottom:2px solid transparent;
        color:#888;font-size:13px;font-weight:500;cursor:pointer;
        transition:all .18s;
      }
      .lfd-tab.active{color:${primary};border-bottom-color:${primary}}
      /* ── Messages ── */
      #lfd-msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;scrollbar-width:thin;scrollbar-color:${border} transparent}
      .lfd-msg{max-width:82%;padding:10px 14px;border-radius:16px;font-size:14px;line-height:1.55;animation:lfd-in .18s ease}
      @keyframes lfd-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
      .lfd-msg.ai{background:${surface};border:1px solid ${border};border-bottom-left-radius:4px;color:${text};align-self:flex-start}
      .lfd-msg.user{background:${primary};color:#000;font-weight:600;border-bottom-right-radius:4px;align-self:flex-end}
      .lfd-typing{display:flex;gap:5px;align-items:center;padding:12px 14px;align-self:flex-start;background:${surface};border:1px solid ${border};border-radius:16px;border-bottom-left-radius:4px}
      .lfd-dot{width:6px;height:6px;background:#888;border-radius:50%;animation:lfd-b 1.2s infinite}
      .lfd-dot:nth-child(2){animation-delay:.2s}.lfd-dot:nth-child(3){animation-delay:.4s}
      @keyframes lfd-b{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}
      /* ── Input area ── */
      #lfd-input-area{
        padding:12px 14px;border-top:1px solid ${border};
        display:flex;gap:8px;align-items:flex-end;flex-shrink:0;
        background:${surface};
      }
      #lfd-input{
        flex:1;background:${bg};border:1px solid ${border};
        border-radius:20px;padding:10px 16px;font-size:14px;
        color:${text};outline:none;resize:none;max-height:80px;
        line-height:1.4;transition:border-color .18s;
      }
      #lfd-input:focus{border-color:${primary}}
      #lfd-input::placeholder{color:#888}
      #lfd-send{
        width:38px;height:38px;background:${primary};
        border:none;border-radius:50%;cursor:pointer;
        display:flex;align-items:center;justify-content:center;
        flex-shrink:0;transition:transform .18s,opacity .18s;
      }
      #lfd-send:hover{transform:scale(1.1)}
      #lfd-send:disabled{opacity:.45;cursor:default}
      /* ── Voice panel ── */
      #lfd-voice-panel{
        flex:1;display:flex;flex-direction:column;
        align-items:center;justify-content:center;
        gap:18px;padding:28px;text-align:center;
      }
      #lfd-voice-btn{
        width:88px;height:88px;
        background:rgba(62,207,142,.08);
        border:2px solid ${primary};
        border-radius:50%;display:flex;
        align-items:center;justify-content:center;
        font-size:34px;cursor:pointer;
        transition:all .2s;
      }
      #lfd-voice-btn:hover{background:rgba(62,207,142,.18);transform:scale(1.06)}
      #lfd-voice-btn.listening{
        background:rgba(239,68,68,.15);
        border-color:#EF4444;
        animation:lfd-pulse 1s infinite;
      }
      #lfd-voice-status{font-size:14px;color:#888}
      #lfd-voice-lang{font-size:12px;color:#555;max-width:200px;line-height:1.5}
      /* ── Branding ── */
      #lfd-brand{
        text-align:center;padding:6px;font-size:10px;
        color:#555;border-top:1px solid ${border};flex-shrink:0;
      }
      #lfd-brand a{color:${primary};text-decoration:none}
      /* ── Mobile ── */
      @media(max-width:400px){
        #lfd-window{width:calc(100vw - 20px);right:10px!important;left:10px!important;bottom:80px!important;max-height:75vh}
      }
    `;
    const style = document.createElement('style');
    style.id = 'lifodial-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ── Inject HTML ───────────────────────────────────────────────────────────────
  function injectWidget() {
    if (document.getElementById('lfd-widget')) return;
    injectStyles();

    const { primary } = getColors();
    const btnText = (config && config.embed_button_text) || 'Talk to Receptionist';
    const clinicName = (config && config.clinic_name) || 'AI Receptionist';
    const showBranding = config ? config.embed_show_branding !== false : true;
    const langLabel   = {
      'hi-IN': 'Hindi', 'en-IN': 'English', 'ta-IN': 'Tamil',
      'ml-IN': 'Malayalam', 'te-IN': 'Telugu', 'ar-SA': 'Arabic',
    }[(config && config.language) || 'en-IN'] || 'your language';

    const wrapper = document.createElement('div');
    wrapper.id = 'lfd-widget';
    wrapper.innerHTML = `
      <button id="lfd-trigger">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81a19.79 19.79 0 01-3.07-8.67A2 2 0 012 .84h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 8.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/>
        </svg>
        ${btnText}
      </button>

      <div id="lfd-window">
        <div id="lfd-header">
          <div id="lfd-agent-info">
            <div id="lfd-avatar">🤖</div>
            <div>
              <div id="lfd-agent-name">${clinicName}</div>
              <div id="lfd-online">AI Receptionist Online</div>
            </div>
          </div>
          <button id="lfd-close" title="Close">×</button>
        </div>

        <div id="lfd-tabs">
          <button class="lfd-tab active" data-tab="chat">💬 Chat</button>
          <button class="lfd-tab"        data-tab="voice">🎙 Voice</button>
        </div>

        <div id="lfd-msgs"></div>

        <div id="lfd-voice-panel" style="display:none">
          <div id="lfd-voice-btn">🎙</div>
          <p id="lfd-voice-status">Tap to speak</p>
          <p id="lfd-voice-lang">Speak naturally — AI responds in ${langLabel}</p>
        </div>

        <div id="lfd-input-area">
          <textarea id="lfd-input" placeholder="Type your message..." rows="1"></textarea>
          <button id="lfd-send">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M2 21l21-9L2 3v7l15 2-15 2v7z"/></svg>
          </button>
        </div>

        ${showBranding ? '<div id="lfd-brand">Powered by <a href="https://lifodial.com" target="_blank">Lifodial</a></div>' : ''}
      </div>
    `;
    document.body.appendChild(wrapper);

    // Bind events
    document.getElementById('lfd-trigger').addEventListener('click', toggle);
    document.getElementById('lfd-close').addEventListener('click', toggle);
    document.getElementById('lfd-send').addEventListener('click', sendMessage);
    document.getElementById('lfd-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    document.querySelectorAll('.lfd-tab').forEach(function (btn) {
      btn.addEventListener('click', function () { setTab(btn.getAttribute('data-tab')); });
    });
    document.getElementById('lfd-voice-btn').addEventListener('click', startVoice);

    // Initial greeting
    addMsg('ai', (config && config.greeting) || 'Hello! How can I help you today?');
  }

  // ── Messages ─────────────────────────────────────────────────────────────────
  function addMsg(role, text) {
    const container = document.getElementById('lfd-msgs');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'lfd-msg ' + role;
    el.textContent = text;
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    messages.push({ role, text });
  }

  function showTyping() {
    const container = document.getElementById('lfd-msgs');
    if (!container) return;
    const t = document.createElement('div');
    t.className = 'lfd-typing';
    t.id = 'lfd-typing';
    t.innerHTML = '<div class="lfd-dot"></div><div class="lfd-dot"></div><div class="lfd-dot"></div>';
    container.appendChild(t);
    container.scrollTop = container.scrollHeight;
  }

  function hideTyping() {
    const t = document.getElementById('lfd-typing');
    if (t) t.remove();
  }

  // ── Send message ──────────────────────────────────────────────────────────────
  async function sendMessage() {
    const input = document.getElementById('lfd-input');
    const text  = input.value.trim();
    if (!text || isTyping) return;

    input.value = '';
    input.style.height = 'auto';
    addMsg('user', text);

    isTyping = true;
    const sendBtn = document.getElementById('lfd-send');
    if (sendBtn) sendBtn.disabled = true;
    showTyping();

    try {
      const res = await fetch(API_BASE + '/embed/' + AGENT_ID + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          session_id: sessionId,
          visitor_language: (config && config.language) || 'en-IN',
          history: messages.slice(-8),
        }),
      });
      hideTyping();
      if (!res.ok) throw new Error('api_error');
      const data = await res.json();
      addMsg('ai', data.response);

      if (data.intent === 'booking_confirmed' && data.booking) {
        showBookingCard(data.booking);
      }
      track('chat_started');
    } catch (e) {
      hideTyping();
      addMsg('ai', "I'm having trouble connecting. Please try again or call us directly.");
    } finally {
      isTyping = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  // ── Booking confirmation ───────────────────────────────────────────────────────
  function showBookingCard(booking) {
    const container = document.getElementById('lfd-msgs');
    if (!container) return;
    const card = document.createElement('div');
    card.className = 'lfd-msg ai';
    card.style.cssText = 'max-width:100%;border-color:#22C55E;background:rgba(34,197,94,.06)';
    card.innerHTML =
      '<div style="color:#22C55E;font-weight:700;margin-bottom:6px">✅ Appointment Confirmed!</div>' +
      '<div style="font-size:13px;line-height:1.7">' +
      'Doctor: ' + (booking.doctor_name || '—') + '<br>' +
      'Time: '   + (booking.slot_time   || '—') + '<br>' +
      'ID: '     + (booking.appointment_id || '—') +
      '</div>';
    container.appendChild(card);
    container.scrollTop = container.scrollHeight;
    track('booking_completed');
  }

  // ── Tab switching ─────────────────────────────────────────────────────────────
  function setTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.lfd-tab').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
    });
    const msgs      = document.getElementById('lfd-msgs');
    const inputArea = document.getElementById('lfd-input-area');
    const voicePanel= document.getElementById('lfd-voice-panel');
    if (tab === 'voice') {
      if (msgs)       msgs.style.display       = 'none';
      if (inputArea)  inputArea.style.display   = 'none';
      if (voicePanel) voicePanel.style.display  = 'flex';
    } else {
      if (msgs)       msgs.style.display       = 'flex';
      if (inputArea)  inputArea.style.display   = 'flex';
      if (voicePanel) voicePanel.style.display  = 'none';
    }
  }

  // ── Voice (Web Speech API) ────────────────────────────────────────────────────
  function startVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      alert('Voice input requires Chrome or Edge browser.');
      return;
    }
    const btn    = document.getElementById('lfd-voice-btn');
    const status = document.getElementById('lfd-voice-status');
    const recognition = new SR();
    recognition.lang         = (config && config.language) || 'en-IN';
    recognition.interimResults= false;
    recognition.maxAlternatives = 1;

    recognition.onstart = function () {
      if (btn)    { btn.classList.add('listening'); btn.textContent = '🔴'; }
      if (status)   status.textContent = '🎤 Listening...';
    };
    recognition.onend = function () {
      if (btn)    { btn.classList.remove('listening'); btn.textContent = '🎙'; }
      if (status)   status.textContent = 'Tap to speak';
    };
    recognition.onresult = function (e) {
      const transcript = e.results[0][0].transcript;
      // Switch to chat tab and send the transcribed text
      setTab('chat');
      const input = document.getElementById('lfd-input');
      if (input) input.value = transcript;
      setTimeout(sendMessage, 250);
    };
    recognition.onerror = function (e) {
      if (btn)    { btn.classList.remove('listening'); btn.textContent = '🎙'; }
      if (status)   status.textContent = 'Error: ' + e.error + '. Tap to retry.';
    };
    recognition.start();
  }

  // ── Toggle open / close ───────────────────────────────────────────────────────
  function toggle() {
    isOpen = !isOpen;
    const win = document.getElementById('lfd-window');
    if (win) win.classList.toggle('open', isOpen);
    if (isOpen) track('widget_open');
  }

  // ── Init ──────────────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadConfig);
  } else {
    loadConfig();
  }

})();
