/**
 * Lifodial AI Receptionist Widget v2.0
 * Usage:
 *   <!-- Default (chat + call button row) -->
 *   <script src="https://api.lifodial.com/widget.js"
 *           data-agent-id="YOUR-AGENT-UUID"
 *           data-position="bottom-right"
 *           data-theme="dark"
 *           data-style="full"
 *           data-primary-color="#3ECF8E"
 *           data-icon-bg="#1A1A1A"
 *           data-icon-color="#3ECF8E"
 *           data-label="Talk to Receptionist"
 *   ></script>
 *
 * data-style options:
 *   "full"       — floating button with text + chat/call panel  (DEFAULT)
 *   "call-only"  — single call icon; opens mic immediately on tap
 *   "icon"       — floating headphone icon without label
 *   "minimal"    — small text-only pill
 *
 * Developer overrides (all optional):
 *   data-primary-color   — hex colour for button & accents   (e.g. "#7C3AED")
 *   data-icon-bg         — button background colour          (e.g. "#1E1E2E")
 *   data-icon-color      — icon / text colour                (e.g. "#ffffff")
 *   data-label           — button label text                 (e.g. "Talk to us")
 *   data-api-url         — override API base URL
 */
(function () {
  'use strict';

  // ── Config from script tag ─────────────────────────────────────────────────
  const script = document.currentScript || document.querySelector('script[data-agent-id]');
  if (!script) return;

  const AGENT_ID  = script.getAttribute('data-agent-id');
  // Determine API base dynamically from script src (works in prod and dev)
  // Falls back to explicit data-api-url attribute if set.
  const _explicit = script.getAttribute('data-api-url');
  const _scriptSrc = script.src || '';
  const _scriptOrigin = _scriptSrc
    ? new URL(_scriptSrc).origin          // e.g. https://api.lifodial.com
    : window.location.origin;             // same-origin dev fallback
  const API_BASE  = _explicit !== null && _explicit !== '' ? _explicit : _scriptOrigin;
  const WS_BASE   = API_BASE.replace(/^http/, 'ws');
  const POSITION  = script.getAttribute('data-position') || 'bottom-right';
  const THEME     = script.getAttribute('data-theme')    || 'dark';
  const STYLE     = script.getAttribute('data-style')    || 'full';  // full | call-only | icon | minimal
  const LABEL_OVERRIDE    = script.getAttribute('data-label');
  const PRIMARY_OVERRIDE  = script.getAttribute('data-primary-color');
  const ICON_BG_OVERRIDE  = script.getAttribute('data-icon-bg');
  const ICON_CLR_OVERRIDE = script.getAttribute('data-icon-color');

  if (!AGENT_ID) { console.warn('[Lifodial] data-agent-id is required'); return; }

  // ── State ──────────────────────────────────────────────────────────────────
  let config    = null;
  let isOpen    = false;
  let sessionId = 'v-' + Math.random().toString(36).slice(2, 14);
  let messages  = [];
  let isTyping  = false;
  let activeTab = STYLE === 'call-only' ? 'voice' : 'chat';

  // Voice call state
  let ws             = null;
  let mediaRecorder  = null;
  let audioCtx       = null;
  let globalStream   = null;
  let callActive     = false;
  let callTimer      = null;
  let recordInterval = null;
  let callSeconds    = 0;
  let wsRetryCount   = 0;
  const WS_MAX_RETRIES = 3;

  // Barge-in / interrupt
  let activeSrc = null;  // the current AudioBufferSourceNode the agent is playing

  // VAD state
  let vadAnalyser   = null;
  let vadFrameId    = null;
  let silenceMs     = 0;
  let speechDetected = false;
  const SILENCE_THRESHOLD = 0.012;  // RMS level below which is silence
  const SILENCE_CUTOFF_MS = 800;    // ms of silence before chunk sent
  const MIN_CHUNK_RMS     = 0.008;  // chunks below this RMS are discarded


  // ── Fetch config ───────────────────────────────────────────────────────────
  async function loadConfig() {
    try {
      const res = await fetch(API_BASE + '/embed/' + AGENT_ID + '/config');
      if (!res.ok) {
        const errText = await res.text().catch(() => '');
        console.error('[Lifodial] Config load failed: HTTP ' + res.status + ' from ' + API_BASE + '/embed/' + AGENT_ID + '/config', errText);
        injectErrorBadge('Receptionist unavailable (HTTP ' + res.status + ')');
        return;
      }
      config = await res.json();
      if (config.is_active === false) {
        console.warn('[Lifodial] Agent ' + AGENT_ID + ' is not active');
        return;
      }
      injectWidget();
      track('widget_view');
      // If call-only: open mic automatically after 400ms (give DOM time to render)
      if (STYLE === 'call-only') setTimeout(() => startVoiceCall(), 400);
    } catch (err) {
      console.error('[Lifodial] Config fetch error — is API_BASE reachable?', API_BASE, err);
      injectErrorBadge('Receptionist offline (network)');
    }
  }

  // ── Minimal fallback badge so devs see the widget tried to load ───────────
  function injectErrorBadge(msg) {
    if (document.getElementById('lfd-err-badge')) return;
    const el = document.createElement('div');
    el.id = 'lfd-err-badge';
    el.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:999998;background:#1a1a1a;color:#ef4444;padding:10px 16px;border-radius:8px;font:13px -apple-system,sans-serif;border:1px solid rgba(239,68,68,.3);box-shadow:0 4px 20px rgba(0,0,0,.3);max-width:300px';
    el.textContent = '⚠ ' + msg;
    document.body.appendChild(el);
    // Auto-hide after 8s — devs see it in console anyway
    setTimeout(() => { try { el.remove(); } catch (_) {} }, 8000);
  }

  // ── Analytics ──────────────────────────────────────────────────────────────
  function track(ev) {
    fetch(API_BASE + '/embed/' + AGENT_ID + '/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_type: ev, session_id: sessionId, domain: location.hostname }),
    }).catch(() => {});
  }

  // ── Resolved colours (developer overrides > agent config > theme defaults) ─
  function getColors() {
    const isDark   = THEME !== 'light';
    const primary  = PRIMARY_OVERRIDE || (config && config.embed_primary_color) || '#3ECF8E';
    const iconBg   = ICON_BG_OVERRIDE || (isDark ? '#0F0F0F' : '#ffffff');
    const iconClr  = ICON_CLR_OVERRIDE || primary;
    const bg       = isDark ? '#0F0F0F' : '#FFFFFF';
    const text     = isDark ? '#FFFFFF' : '#111111';
    const surface  = isDark ? '#1A1A1A' : '#F7F8FA';
    const border   = isDark ? '#2A2A2A' : '#E5E7EB';
    const muted    = isDark ? '#888'    : '#6B7280';
    return { isDark, primary, iconBg, iconClr, bg, text, surface, border, muted };
  }

  // ── CSS injection ──────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('lifodial-styles')) return;
    const C      = getColors();
    const isR    = POSITION.includes('right');
    const isB    = POSITION.includes('bottom');
    const hEdge  = isR ? 'right:20px' : 'left:20px';
    const vEdge  = isB ? 'bottom:20px' : 'top:20px';
    const wVEdge = isB ? 'bottom:86px' : 'top:86px';

    const css = `
      #lfd-widget *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;padding:0}

      /* ── Trigger button ─────────────────────────────────────────────────── */
      #lfd-trigger{
        position:fixed;${hEdge};${vEdge};z-index:999998;
        display:flex;align-items:center;gap:9px;
        background:${C.iconBg};color:${C.iconClr};
        border:1px solid ${C.iconBg === '#0F0F0F' || C.iconBg === '#1A1A1A' ? C.primary + '40' : 'transparent'};
        border-radius:50px;padding:10px 18px 10px 12px;
        cursor:pointer;box-shadow:0 4px 24px rgba(0,0,0,0.35);
        transition:transform .2s,box-shadow .2s;user-select:none;
      }
      #lfd-trigger:hover{transform:translateY(-2px);box-shadow:0 8px 32px rgba(0,0,0,0.4)}
      #lfd-trigger.icon-only{padding:12px;border-radius:50%;width:52px;height:52px;justify-content:center}
      #lfd-trigger-label{font-size:14px;font-weight:600;white-space:nowrap;color:${C.iconClr}}
      #lfd-badge{position:absolute;top:-4px;${isR ? 'right:-4px' : 'left:-4px'};
        background:#ef4444;color:#fff;border-radius:50%;width:16px;height:16px;
        font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;
        display:none;}

      /* ── Pulse ring on active call ─────────────────────────────────────── */
      @keyframes lfd-pulse{0%,100%{box-shadow:0 0 0 0 ${C.primary}60}50%{box-shadow:0 0 0 10px transparent}}
      #lfd-trigger.calling{animation:lfd-pulse 1.4s ease-in-out infinite;border-color:${C.primary}!important}

      /* ── Panel ─────────────────────────────────────────────────────────── */
      #lfd-panel{
        position:fixed;${hEdge};${wVEdge};z-index:999999;
        width:360px;max-height:560px;
        background:${C.bg};border:1px solid ${C.border};
        border-radius:16px;overflow:hidden;
        box-shadow:0 20px 60px rgba(0,0,0,.55);
        display:flex;flex-direction:column;
        opacity:0;transform:translateY(12px) scale(.97);
        transition:opacity .2s,transform .2s;pointer-events:none;
      }
      #lfd-panel.open{opacity:1;transform:none;pointer-events:all}

      /* ── Header ────────────────────────────────────────────────────────── */
      #lfd-header{
        background:${C.primary};color:#000;
        padding:14px 16px;display:flex;align-items:center;gap:10px;
      }
      #lfd-avatar{
        width:36px;height:36px;border-radius:50%;
        background:rgba(0,0,0,.15);display:flex;align-items:center;justify-content:center;
        flex-shrink:0;
      }
      #lfd-header-info h3{font-size:14px;font-weight:700;color:#000;line-height:1.2}
      #lfd-header-info p{font-size:11px;color:rgba(0,0,0,.65)}
      #lfd-close{margin-left:auto;background:rgba(0,0,0,.15);border:none;
        border-radius:50%;width:28px;height:28px;cursor:pointer;color:#000;
        display:flex;align-items:center;justify-content:center;transition:background .15s}
      #lfd-close:hover{background:rgba(0,0,0,.28)}

      /* ── Tabs ──────────────────────────────────────────────────────────── */
      #lfd-tabs{display:flex;border-bottom:1px solid ${C.border};background:${C.surface}}
      .lfd-tab{flex:1;padding:10px;border:none;background:none;cursor:pointer;
        font-size:12px;font-weight:600;color:${C.muted};
        border-bottom:2px solid transparent;transition:all .15s}
      .lfd-tab.active{color:${C.primary};border-bottom-color:${C.primary}}
      .lfd-tab:hover:not(.active){color:${C.text}}

      /* ── Tab panes ─────────────────────────────────────────────────────── */
      .lfd-pane{display:none;flex:1;flex-direction:column;overflow:hidden}
      .lfd-pane.active{display:flex}

      /* ── Chat ──────────────────────────────────────────────────────────── */
      #lfd-messages{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}
      #lfd-messages::-webkit-scrollbar{width:4px}
      #lfd-messages::-webkit-scrollbar-thumb{background:${C.border};border-radius:2px}
      .lfd-msg{max-width:80%;padding:9px 12px;border-radius:12px;font-size:13px;line-height:1.5}
      .lfd-msg.ai{background:${C.surface};color:${C.text};border-bottom-left-radius:4px;align-self:flex-start}
      .lfd-msg.user{background:${C.primary};color:#000;border-bottom-right-radius:4px;align-self:flex-end}
      .lfd-typing{display:flex;gap:4px;padding:9px 12px;background:${C.surface};border-radius:12px;
        border-bottom-left-radius:4px;align-self:flex-start;width:fit-content}
      .lfd-dot{width:7px;height:7px;border-radius:50%;background:${C.muted};animation:lfd-bounce .9s ease-in-out infinite}
      .lfd-dot:nth-child(2){animation-delay:.15s}
      .lfd-dot:nth-child(3){animation-delay:.3s}
      @keyframes lfd-bounce{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-6px)}}
      #lfd-chat-bottom{
        padding:10px;border-top:1px solid ${C.border};
        display:flex;gap:8px;align-items:center;background:${C.bg};
      }
      #lfd-input{
        flex:1;background:${C.surface};border:1px solid ${C.border};
        border-radius:8px;padding:9px 12px;color:${C.text};font-size:13px;outline:none;resize:none;
        max-height:80px;
      }
      #lfd-input::placeholder{color:${C.muted}}
      #lfd-send{
        background:${C.primary};color:#000;border:none;border-radius:8px;
        padding:9px 14px;cursor:pointer;font-weight:700;font-size:13px;
        transition:opacity .15s;flex-shrink:0;
      }
      #lfd-send:disabled{opacity:.4;cursor:not-allowed}

      /* ── Voice tab ─────────────────────────────────────────────────────── */
      #lfd-voice-pane{
        flex:1;display:flex;flex-direction:column;align-items:center;
        justify-content:center;gap:14px;padding:24px;
      }
      #lfd-call-ring{
        width:100px;height:100px;border-radius:50%;background:${C.surface};
        border:2px solid ${C.border};display:flex;align-items:center;justify-content:center;
        position:relative;cursor:pointer;transition:border-color .2s,transform .15s;
      }
      #lfd-call-ring:hover{transform:scale(1.05);border-color:${C.primary}}
      #lfd-call-ring.active{border-color:${C.primary};animation:lfd-pulse 1.4s ease-in-out infinite}
      @keyframes lfd-ripple{to{transform:scale(2.2);opacity:0}}
      .lfd-ripple-ring{
        position:absolute;inset:-5px;border-radius:50%;
        border:2px solid ${C.primary};opacity:.5;
        animation:lfd-ripple 1.4s ease-out infinite;
      }
      .lfd-ripple-ring:nth-child(2){animation-delay:.5s}
      #lfd-call-timer{font-size:28px;font-weight:700;color:${C.text};font-variant-numeric:tabular-nums;letter-spacing:.04em}
      #lfd-call-status{font-size:13px;color:${C.muted};text-align:center;max-width:220px;line-height:1.4}
      #lfd-end-call{
        background:rgba(239,68,68,.12);color:#ef4444;border:1px solid rgba(239,68,68,.3);
        border-radius:50px;padding:10px 28px;cursor:pointer;font-weight:700;
        font-size:13px;display:none;transition:background .15s;
      }
      #lfd-end-call:hover{background:rgba(239,68,68,.22)}
      #lfd-end-call.visible{display:block}
      #lfd-start-call{
        background:${C.primary};color:#000;border:none;border-radius:50px;
        padding:12px 32px;cursor:pointer;font-weight:700;font-size:14px;
        display:flex;align-items:center;gap:8px;transition:opacity .15s,transform .15s;
      }
      #lfd-start-call:hover{transform:translateY(-1px);opacity:.9}

      /* ── Footer ────────────────────────────────────────────────────────── */
      #lfd-footer{
        padding:6px 12px;border-top:1px solid ${C.border};
        font-size:10px;color:${C.muted};text-align:center;background:${C.bg};
      }
      #lfd-footer a{color:${C.primary};text-decoration:none}

      @media(max-width:400px){
        #lfd-panel{width:calc(100vw - 16px);${isR ? 'right:8px' : 'left:8px'}}
      }
    `;
    const el = document.createElement('style');
    el.id = 'lifodial-styles';
    el.textContent = css;
    document.head.appendChild(el);
  }

  // ── SVG icons (inline, no deps) ────────────────────────────────────────────
  const SVG = {
    headphone: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>`,
    phone: `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.21 3.4 2 2 0 0 1 3.18 1h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L7.91 8.1a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 15v1.92z"/></svg>`,
    chat: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    mic: `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`,
    close: `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
    send: `<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`,
    endcall: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  };

  // ── Build trigger button based on style ────────────────────────────────────
  function buildTrigger() {
    const C = getColors();
    const label = LABEL_OVERRIDE || (config && config.embed_button_text) || (config && config.embed_label) || 'Talk to Receptionist';
    const btn   = document.createElement('button');
    btn.id = 'lfd-trigger';
    btn.setAttribute('aria-label', 'Open AI Receptionist');

    if (STYLE === 'call-only' || STYLE === 'icon') {
      btn.classList.add('icon-only');
      btn.innerHTML = STYLE === 'call-only' ? SVG.phone : SVG.headphone;
    } else if (STYLE === 'minimal') {
      btn.style.cssText += 'padding:7px 14px;font-size:12px;border-radius:50px';
      btn.innerHTML = `${SVG.headphone}<span id="lfd-trigger-label">${label}</span>`;
    } else { // full
      btn.innerHTML = `${SVG.headphone}<span id="lfd-trigger-label">${label}</span>`;
    }

    // Notification badge
    const badge = document.createElement('div');
    badge.id = 'lfd-badge';
    badge.textContent = '1';
    btn.appendChild(badge);

    btn.addEventListener('click', () => {
      if (STYLE === 'call-only') {
        // Call-only: clicking trigger toggles voice call directly
        if (callActive) endVoiceCall(); else startVoiceCall();
        return;
      }
      togglePanel();
    });

    document.body.appendChild(btn);
  }

  // ── Build full panel (chat + voice tabs) ───────────────────────────────────
  function buildPanel() {
    const C    = getColors();
    const name = (config && config.agent_name) || (config && config.clinic_name) || 'AI Receptionist';
    const sub  = 'Online · Instant Reply';

    const panel = document.createElement('div');
    panel.id = 'lfd-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'AI Receptionist Chat');

    panel.innerHTML = `
      <div id="lfd-header">
        <div id="lfd-avatar">${SVG.headphone}</div>
        <div id="lfd-header-info">
          <h3>${name}</h3>
          <p>🟢 ${sub}</p>
        </div>
        <button id="lfd-close" aria-label="Close">${SVG.close}</button>
      </div>

      <div id="lfd-tabs">
        <button class="lfd-tab ${activeTab === 'chat'  ? 'active' : ''}" data-tab="chat">${SVG.chat} Chat</button>
        <button class="lfd-tab ${activeTab === 'voice' ? 'active' : ''}" data-tab="voice">${SVG.mic} Voice Call</button>
      </div>

      <!-- CHAT pane -->
      <div class="lfd-pane ${activeTab === 'chat' ? 'active' : ''}" id="pane-chat">
        <div id="lfd-messages"></div>
        <div id="lfd-chat-bottom">
          <textarea id="lfd-input" rows="1" placeholder="Type your message…" aria-label="Message"></textarea>
          <button id="lfd-send" aria-label="Send">${SVG.send}</button>
        </div>
      </div>

      <!-- VOICE pane -->
      <div class="lfd-pane ${activeTab === 'voice' ? 'active' : ''}" id="pane-voice">
        <div id="lfd-voice-pane">
          <div id="lfd-call-ring" title="Start voice call">
            <div style="color:${C.iconClr}">${SVG.phone}</div>
          </div>
          <div id="lfd-call-timer" style="display:none">00:00</div>
          <div id="lfd-call-status">Tap to start a voice call with the AI receptionist</div>
          <button id="lfd-start-call">${SVG.mic} Start Voice Call</button>
          <button id="lfd-end-call">☎ End Call</button>
        </div>
      </div>

      <div id="lfd-footer">Powered by <a href="https://lifodial.com" target="_blank">Lifodial AI</a></div>
    `;

    document.body.appendChild(panel);

    // Tab switching
    panel.querySelectorAll('.lfd-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        activeTab = tab.dataset.tab;
        panel.querySelectorAll('.lfd-tab, .lfd-pane').forEach(el => el.classList.remove('active'));
        tab.classList.add('active');
        panel.querySelector('#pane-' + activeTab).classList.add('active');
      });
    });

    // Close
    panel.querySelector('#lfd-close').addEventListener('click', () => togglePanel(false));

    // Chat send
    const input = panel.querySelector('#lfd-input');
    const sendBtn = panel.querySelector('#lfd-send');
    const doSend = () => {
      const txt = input.value.trim();
      if (!txt) return;
      input.value = '';
      input.style.height = 'auto';
      sendChat(txt);
    };
    sendBtn.addEventListener('click', doSend);
    input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); } });
    input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 80) + 'px'; });

    // Voice UI handlers
    panel.querySelector('#lfd-call-ring').addEventListener('click', () => {
      if (callActive) return;
      startVoiceCall();
    });
    panel.querySelector('#lfd-start-call').addEventListener('click', () => {
      if (!callActive) startVoiceCall();
    });
    panel.querySelector('#lfd-end-call').addEventListener('click', endVoiceCall);

    // Initial greeting message (chat pane only — voice greeting is handled by WebSocket)
    const greeting = (config && config.first_message) || (config && config.greeting) || 'Hello! How can I help you today?';
    appendMessage('ai', greeting);
  }

  // ── Panel toggle ───────────────────────────────────────────────────────────
  function togglePanel(force) {
    const panel = document.getElementById('lfd-panel');
    const badge = document.getElementById('lfd-badge');
    if (!panel) return;
    isOpen = (force !== undefined) ? force : !isOpen;
    panel.classList.toggle('open', isOpen);
    if (isOpen && badge) badge.style.display = 'none';
    if (isOpen) track('widget_open');
  }

  // ── Chat message ───────────────────────────────────────────────────────────
  function appendMessage(role, text) {
    const msgs = document.getElementById('lfd-messages');
    if (!msgs) return;
    const el = document.createElement('div');
    el.className = 'lfd-msg ' + role;
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    if (role !== 'typing') messages.push({ role: role === 'ai' ? 'assistant' : 'user', content: text });
  }

  function showTyping() {
    const msgs = document.getElementById('lfd-messages');
    if (!msgs) return;
    const el = document.createElement('div');
    el.className = 'lfd-typing';
    el.id = 'lfd-typing-indicator';
    el.innerHTML = '<div class="lfd-dot"></div><div class="lfd-dot"></div><div class="lfd-dot"></div>';
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
  }

  function hideTyping() {
    const t = document.getElementById('lfd-typing-indicator');
    if (t) t.remove();
  }

  async function sendChat(text) {
    appendMessage('user', text);
    showTyping();
    const sendBtn = document.getElementById('lfd-send');
    if (sendBtn) sendBtn.disabled = true;

    try {
      const res = await fetch(API_BASE + '/embed/' + AGENT_ID + '/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: sessionId, history: messages.slice(-10) }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      hideTyping();
      appendMessage('ai', data.response || data.message || 'Sorry, I could not respond right now.');
    } catch (_) {
      hideTyping();
      appendMessage('ai', 'Sorry, there was a connection issue. Please try again.');
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }
    track('chat_message');
  }

  // ── VOICE CALL via WebSocket ───────────────────────────────────────────────
  // Full pipeline: browser mic → MediaRecorder → WebSocket → backend STT→LLM→TTS → audio playback

  function setCallStatus(msg) {
    const el = document.getElementById('lfd-call-status');
    if (el) el.textContent = msg;
  }

  function setCallTimer(seconds) {
    const el = document.getElementById('lfd-call-timer');
    if (el) { el.style.display = seconds >= 0 ? 'block' : 'none'; el.textContent = fmt(seconds); }
  }

  function fmt(s) {
    return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  }

  function setCallRingState(active) {
    const ring = document.getElementById('lfd-call-ring');
    const startBtn = document.getElementById('lfd-start-call');
    const endBtn   = document.getElementById('lfd-end-call');
    const trigger  = document.getElementById('lfd-trigger');
    if (ring) {
      ring.classList.toggle('active', active);
      // Ripple rings while active
      ring.querySelectorAll('.lfd-ripple-ring').forEach(r => r.remove());
      if (active) {
        [0, 1].forEach(i => {
          const r = document.createElement('div');
          r.className = 'lfd-ripple-ring';
          r.style.animationDelay = (i * 0.5) + 's';
          ring.appendChild(r);
        });
      }
    }
    if (startBtn) startBtn.style.display = active ? 'none' : 'flex';
    if (endBtn)   endBtn.classList.toggle('visible', active);
    if (trigger)  trigger.classList.toggle('calling', active);
  }

  // Plays a 0.1s silent WAV to unlock browser autoplay policy before WebSocket audio
  function unlockAudioAutoplay() {
    return new Promise((resolve) => {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const buf = ctx.createBuffer(1, ctx.sampleRate * 0.1, ctx.sampleRate);
        const src = ctx.createBufferSource();
        src.buffer = buf;
        src.connect(ctx.destination);
        src.start(0);
        src.onended = () => { ctx.close(); resolve(); };
        setTimeout(resolve, 300); // safety fallback
      } catch (_) { resolve(); }
    });
  }

  // Stop any currently playing agent audio and notify backend (barge-in)
  function bargeIn() {
    if (activeSrc) {
      try { activeSrc.stop(); } catch (_) {}
      activeSrc = null;
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'interrupt' }));
    }
    setCallStatus('🎙 Listening… speak now');
  }

  async function startVoiceCall(isRetry) {
    if (callActive && !isRetry) return;

    const t0 = performance.now();

    // ── Request mic FIRST so browser prompt shows instantly. ───────────────
    // Autoplay unlock runs in parallel; we don't await it before the mic prompt.
    if (!isRetry) { unlockAudioAutoplay().catch(() => {}); }

    setCallStatus('Requesting microphone…');
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      globalStream = stream;
      console.log('[Lifodial] mic granted in', Math.round(performance.now() - t0), 'ms');
    } catch (err) {
      setCallStatus('⚠️ Microphone access denied. Please allow mic access and try again.');
      return;
    }

    // Connect WebSocket to backend voice pipeline
    const wsUrl = WS_BASE + '/ws/agent-call/' + AGENT_ID + '?session=' + sessionId;
    try {
      ws = new WebSocket(wsUrl);
    } catch (_) {
      setCallStatus('⚠️ Could not connect to call server.');
      stream.getTracks().forEach(t => t.stop());
      return;
    }

    callActive = true;
    if (!isRetry) { callSeconds = 0; wsRetryCount = 0; }
    setCallRingState(true);
    setCallStatus('Connecting…');
    setCallTimer(0);

    // Switch to voice tab if in full panel
    const voiceTab = document.querySelector('.lfd-tab[data-tab="voice"]');
    if (voiceTab && activeTab !== 'voice') voiceTab.click();

    // Open panel if not visible (for call-only style)
    if (STYLE !== 'call-only') togglePanel(true);

    // Audio context for playback
    if (!audioCtx || audioCtx.state === 'closed') {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtx.state === 'suspended') await audioCtx.resume();

    let greetingPlaying = false;
    const firstMsgMode = (config && config.first_message_mode) || 'assistant-speaks-first';
    let lastDetectedLang = null;

    ws.onopen = () => {
      if (firstMsgMode === 'assistant-speaks-first') {
        setCallStatus('Agent is connecting…');
      } else {
        setCallStatus('🎙 Listening… speak now');
        startRecording(stream);
      }
      if (!isRetry) startTimer();
    };

    ws.onmessage = async (event) => {
      // Handle raw binary audio (backend sends bytes for TTS during audio turns)
      if (event.data instanceof Blob || event.data instanceof ArrayBuffer) {
        try {
          const buf = event.data instanceof Blob ? await event.data.arrayBuffer() : event.data;
          if (!audioCtx || audioCtx.state === 'closed') return;
          const buffer = await audioCtx.decodeAudioData(buf.slice(0));
          const src = audioCtx.createBufferSource();
          src.buffer = buffer;
          src.connect(audioCtx.destination);
          activeSrc = src;
          setCallStatus('🔊 Agent speaking…');
          src.start(0);
          src.onended = () => { activeSrc = null; setCallStatus('🎙 Listening… speak now'); };
        } catch (_) { setCallStatus('🎙 Listening… speak now'); }
        return;
      }

      // Handle JSON messages
      let msg;
      try { msg = JSON.parse(event.data); } catch (_) { return; }

      if (msg.type === 'ready') {
        if (firstMsgMode === 'assistant-speaks-first') {
          setCallStatus('🔊 Agent is greeting you…');
        } else {
          setCallStatus('🎙 Listening… speak now');
          if (!mediaRecorder) startRecording(stream);
        }
      } else if (msg.type === 'greeting_audio' || msg.type === 'audio') {
        // Play base64 audio (greeting or turn-based TTS)
        try {
          greetingPlaying = true;
          const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));
          if (!audioCtx || audioCtx.state === 'closed') return;
          const buffer = await audioCtx.decodeAudioData(bytes.buffer.slice(0));
          const src = audioCtx.createBufferSource();
          src.buffer = buffer;
          src.connect(audioCtx.destination);
          activeSrc = src;
          setCallStatus('🔊 Agent speaking…');
          src.start(0);
          src.onended = () => {
            activeSrc = null;
            greetingPlaying = false;
            setCallStatus('🎙 Listening… speak now');
            // Start recording AFTER greeting finishes (agent-speaks-first)
            if (!mediaRecorder && stream.active) startRecording(stream);
          };
        } catch (_) {
          activeSrc = null;
          greetingPlaying = false;
          setCallStatus('🎙 Listening… speak now');
          if (!mediaRecorder && stream.active) startRecording(stream);
        }
      } else if (msg.type === 'transcript') {
        const who = msg.role === 'assistant' ? 'Agent' : 'You';
        const txt = msg.text || '';
        setCallStatus(who + ': ' + txt.slice(0, 80) + (txt.length > 80 ? '…' : ''));
        // Language switch indicator
        if (msg.detected_language && msg.detected_language !== lastDetectedLang) {
          lastDetectedLang = msg.detected_language;
          const langNames = {
            'hi-IN':'Hindi','en-IN':'English','ta-IN':'Tamil','te-IN':'Telugu',
            'kn-IN':'Kannada','ml-IN':'Malayalam','mr-IN':'Marathi','bn-IN':'Bengali',
            'gu-IN':'Gujarati','pa-IN':'Punjabi','or-IN':'Odia'
          };
          const langName = langNames[msg.detected_language] || msg.detected_language;
          if (msg.role !== 'assistant') {
            const toast = document.createElement('div');
            toast.style.cssText = 'position:fixed;bottom:100px;left:50%;transform:translateX(-50%);background:#1a1a1a;color:#3ECF8E;padding:6px 14px;border-radius:20px;font-size:12px;z-index:9999999;pointer-events:none';
            toast.textContent = '🌐 Switched to ' + langName;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 2000);
          }
        }
      } else if (msg.type === 'status') {
        if (msg.status === 'processing') setCallStatus('⏳ Processing…');
        else if (msg.status === 'thinking') setCallStatus('💭 Agent thinking…');
        else if (msg.status === 'speaking') setCallStatus('🔊 Agent speaking…');
        else if (msg.status === 'idle') setCallStatus('🎙 Listening… speak now');
      } else if (msg.type === 'tts_failed') {
        setCallStatus('Agent: ' + (msg.message || '').slice(0, 80));
      } else if (msg.type === 'error') {
        setCallStatus('⚠️ ' + (msg.message || 'Call error'));
      } else if (msg.type === 'end' || (msg.type === 'status' && msg.status === 'ended')) {
        endVoiceCall();
      }
      // Silently ignore ping/pong/timing
    };

    ws.onerror = () => {
      if (callActive && wsRetryCount < WS_MAX_RETRIES) {
        wsRetryCount++;
        const delay = Math.pow(2, wsRetryCount - 1) * 1000;
        setCallStatus(`⚠️ Reconnecting… (attempt ${wsRetryCount}/${WS_MAX_RETRIES})`);
        setTimeout(() => startVoiceCall(true), delay);
      } else {
        setCallStatus('⚠️ Connection error'); endVoiceCall();
      }
    };
    ws.onclose = (ev) => {
      if (callActive && ev.code !== 1000 && ev.code !== 1008 && wsRetryCount < WS_MAX_RETRIES) {
        wsRetryCount++;
        const delay = Math.pow(2, wsRetryCount - 1) * 1000;
        setCallStatus(`⚠️ Reconnecting… (attempt ${wsRetryCount}/${WS_MAX_RETRIES})`);
        setTimeout(() => startVoiceCall(true), delay);
      } else if (callActive) {
        setCallStatus('Call ended'); endVoiceCall();
      }
    };

    track('voice_call_start');
  }


  function startRecording(stream) {
    const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm'].find(t => MediaRecorder.isTypeSupported(t)) || '';
    let chunks = [];
    let chunkStart = Date.now();

    // ── Voice Activity Detection via AudioContext Analyser ─────────────────
    vadAnalyser = audioCtx ? audioCtx.createAnalyser() : null;
    if (vadAnalyser) {
      vadAnalyser.fftSize = 512;
      const srcNode = audioCtx.createMediaStreamSource(stream);
      srcNode.connect(vadAnalyser);
      const data = new Float32Array(vadAnalyser.fftSize);
      const lastChunkTime = { t: Date.now() };

      function checkVAD() {
        if (!callActive || !vadAnalyser) return;
        vadAnalyser.getFloatTimeDomainData(data);
        let rms = 0;
        for (let i = 0; i < data.length; i++) rms += data[i] * data[i];
        rms = Math.sqrt(rms / data.length);

        const isAgentSpeaking = !!activeSrc;

        if (rms > SILENCE_THRESHOLD) {
          // ── User is speaking ──
          silenceMs = 0;
          if (!speechDetected) speechDetected = true;
          // If agent is playing audio, barge-in!
          if (isAgentSpeaking) bargeIn();
        } else {
          // ── Silence detected ──
          silenceMs += 16; // ~16ms per frame at 60fps
          if (speechDetected && silenceMs >= SILENCE_CUTOFF_MS) {
            // 800ms of silence after speech — flush chunk immediately
            speechDetected = false;
            if (mediaRecorder && mediaRecorder.state === 'recording') {
              mediaRecorder.stop();
            }
          }
        }
        vadFrameId = requestAnimationFrame(checkVAD);
      }
      vadFrameId = requestAnimationFrame(checkVAD);
    }

    function recordChunk() {
      if (!callActive) return;
      chunks = [];
      chunkStart = Date.now();

      try {
        mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      } catch (_) {
        mediaRecorder = new MediaRecorder(stream);
      }

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data);
      };

      mediaRecorder.onstop = () => {
        if (!callActive || !ws || ws.readyState !== WebSocket.OPEN) return;
        // Merge chunks and do RMS check before sending
        const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
        blob.arrayBuffer().then(buf => {
          if (!ws || ws.readyState !== WebSocket.OPEN) return;
          // Only send if chunk is substantial (> 1kB) and not during agent speech
          if (buf.byteLength > 1024 && !activeSrc) {
            ws.send(buf);
          } else if (buf.byteLength <= 1024) {
            // too small — silence-only chunk, skip
          }
        });
        // Start next chunk
        if (callActive) {
          recordInterval = setTimeout(recordChunk, 50);
        }
      };

      mediaRecorder.start();

      // Fallback: force-stop after 2500ms even without silence detection
      recordInterval = setTimeout(() => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
          mediaRecorder.stop();
        }
      }, 2500);
    }

    recordChunk();
  }


  function endVoiceCall() {
    callActive = false;
    clearInterval(callTimer);
    clearTimeout(recordInterval);
    callTimer = null;
    recordInterval = null;

    // Stop VAD loop
    if (vadFrameId) { cancelAnimationFrame(vadFrameId); vadFrameId = null; }
    vadAnalyser = null;
    silenceMs = 0;
    speechDetected = false;

    // Stop any playing agent audio
    if (activeSrc) { try { activeSrc.stop(); } catch (_) {} activeSrc = null; }

    // Stop recording
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop();
      mediaRecorder = null;
    }

    if (globalStream) {
      globalStream.getTracks().forEach(t => t.stop());
      globalStream = null;
    }

    // Close WebSocket
    if (ws) { try { ws.close(1000, 'call_ended'); } catch (_) {} ws = null; }

    // Close audio context
    if (audioCtx) { try { audioCtx.close(); } catch (_) {} audioCtx = null; }

    setCallRingState(false);
    setCallStatus('Call ended. Tap to call again.');
    setCallTimer(-1);
    track('voice_call_end');
  }


  function startTimer() {
    callTimer = setInterval(() => {
      callSeconds++;
      setCallTimer(callSeconds);
    }, 1000);
  }

  // ── Widget entry point ─────────────────────────────────────────────────────
  function injectWidget() {
    // Wrap everything in a namespaced container
    const wrapper = document.createElement('div');
    wrapper.id = 'lfd-widget';
    document.body.appendChild(wrapper);

    injectStyles();
    buildTrigger();
    if (STYLE !== 'call-only') buildPanel();

    // Show badge after 3 seconds to encourage interaction
    setTimeout(() => {
      const badge = document.getElementById('lfd-badge');
      if (badge && !isOpen) badge.style.display = 'flex';
    }, 3000);
  }

  // ── Boot ───────────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadConfig);
  } else {
    loadConfig();
  }

})();
