/**
 * Dograh Widget
 * Embeddable voice & chat widget for Dograh agents
 * Version: 1.1.0
 */

(function() {
  'use strict';

  // Widget configuration defaults
  const DEFAULT_CONFIG = {
    position: 'bottom-right',
    autoStart: false,
    apiBaseUrl: window.location.hostname === 'localhost'
      ? 'http://localhost:8000'
      : 'https://api.dograh.com'
  };

  // Widget state
  const state = {
    config: {},
    isInitialized: false,
    isOpen: false,
    pc: null,
    ws: null,
    stream: null,
    sessionToken: null,
    workflowRunId: null,
    pcId: null,
    connectionStatus: 'idle', // idle, connecting, connected, failed
    audioElement: null,
    turnCredentials: null, // TURN server credentials
    callStartedAt: null, // Timestamp when call connected (for duration tracking)
    gracefulDisconnect: false,
    // Chat widget state (widgetType === 'chat'). The server transcript is the
    // source of truth: every successful response full-replaces turns/revision.
    chat: {
      status: 'idle', // idle | starting | ready | waiting | ended | expired | error
      panelOpen: false,
      revision: null,
      turns: [],
      pendingUserText: null, // optimistic bubble while a POST is in flight
      ending: false,
      confirmingEnd: false,
      draft: '',
      banner: null,
      seenAssistantTurnIds: new Set() // for onMessage diffing
    },
    chatEls: null, // { panel, messages, banner, input, sendBtn, endBtn, endConfirmation, confirmEndBtn } — null in headless
    callbacks: {
      onReady: null,
      onCallStart: null,
      onCallConnected: null,
      onCallDisconnected: null,
      onCallEnd: null,
      onError: null,
      onStatusChange: null,
      onMessage: null,
      onChatStateChange: null
    }
  };

  // Initialization is single-flight so API calls made while configuration is
  // loading can await the same request instead of guessing the widget type.
  let initializationPromise = null;

  /**
   * Initialize the widget once and expose the in-flight work to public APIs.
   */
  function init() {
    if (state.isInitialized) return Promise.resolve();
    if (!initializationPromise) {
      initializationPromise = initializeWidget().finally(() => {
        if (!state.isInitialized) {
          initializationPromise = null;
        }
      });
    }
    return initializationPromise;
  }

  async function initializeWidget() {
    // Get token from script URL
    const script = document.currentScript || document.querySelector('script[src*="dograh-widget.js"]');
    if (!script) {
      console.error('Dograh Widget: Script not found');
      return;
    }

    // Extract parameters from URL
    const scriptUrl = new URL(script.src);
    const token = scriptUrl.searchParams.get('token');
    const apiEndpoint = scriptUrl.searchParams.get('apiEndpoint');
    const environment = scriptUrl.searchParams.get('environment');

    if (!token) {
      console.error('Dograh Widget: No token found in script URL');
      return;
    }

    // Determine API base URL
    let apiBaseUrl = DEFAULT_CONFIG.apiBaseUrl;
    if (apiEndpoint) {
      // Use the apiEndpoint from URL parameter if provided
      // Ensure it has a protocol
      if (!apiEndpoint.startsWith('http://') && !apiEndpoint.startsWith('https://')) {
        // Default to https for production endpoints
        apiBaseUrl = 'https://' + apiEndpoint.replace(/\/+$/, '');
      } else {
        apiBaseUrl = apiEndpoint.replace(/\/+$/, ''); // Remove trailing slashes
      }
    } else if (scriptUrl.origin.includes('localhost')) {
      apiBaseUrl = 'http://localhost:8000';
    } else {
      apiBaseUrl = scriptUrl.origin.replace(/:\d+$/, ':8000');
    }

    // Store base configuration
    state.config = {
      ...DEFAULT_CONFIG,
      token: token,
      apiBaseUrl: apiBaseUrl,
      environment: environment || 'production',
      // Visitor context the host page attached to the script tag. It lands in
      // the run's initial_context, addressable from prompts as
      // {{initial_context.<name>}}. setContext() can run before this config
      // assignment (init awaits a fetch), so anything already set is merged
      // back on top rather than overwritten.
      contextVariables: {
        ...parseContextVariables(script.getAttribute('data-dograh-context')),
        ...(state.config.contextVariables || {})
      }
    };

    try {
      // Fetch configuration from API
      const configResponse = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/config/${token}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin
        }
      });

      if (!configResponse.ok) {
        throw new Error(`Failed to fetch config: ${configResponse.status}`);
      }

      const configData = await configResponse.json();

      const widgetType = configData.settings?.widgetType === 'chat' ? 'chat' : 'voice';

      // Merge fetched configuration with defaults
      state.config = {
        ...state.config,
        workflowId: configData.workflow_id,
        widgetType: widgetType,
        embedMode: configData.settings?.embedMode || 'floating',
        containerId: configData.settings?.containerId || 'dograh-inline-container',
        position: configData.position || DEFAULT_CONFIG.position,
        buttonColor: configData.settings?.buttonColor || '#10b981',
        buttonText: configData.settings?.buttonText || (widgetType === 'chat' ? 'Chat with Agent' : 'Talk to Agent'),
        callToActionText: configData.settings?.callToActionText || (widgetType === 'chat' ? 'Click to start chatting' : 'Click to start voice conversation'),
        autoStart: configData.auto_start || false,
        // WebRTC transport hints from the server. Left undefined by older
        // backends that don't send them, in which case we keep the previous
        // behaviour: attempt the TURN fetch and don't force relay.
        turnEnabled: configData.turn_enabled,
        forceTurnRelay: Boolean(configData.force_turn_relay)
      };
    } catch (error) {
      console.error('Dograh Widget: Failed to fetch configuration', error);
      return;
    }

    state.isInitialized = true;

    // Create widget UI based on widget type and mode
    if (isChatWidget()) {
      if (state.config.embedMode === 'inline') {
        injectStyles();
        injectChatStyles();
        createInlineChatWidget();
      } else if (state.config.embedMode === 'headless') {
        // No UI — the host page drives chat via the window.DograhWidget API.
      } else {
        injectStyles();
        injectChatStyles();
        createFloatingChatWidget();
      }
    } else if (state.config.embedMode === 'inline') {
      injectStyles();
      createInlineWidget();
    } else if (state.config.embedMode === 'headless') {
      createHeadlessWidget();
    } else {
      injectStyles();
      createFloatingWidget();
    }

    // Trigger ready callback
    if (state.callbacks.onReady) {
      state.callbacks.onReady();
    }

    // Auto-start if configured
    if (state.config.autoStart) {
      setTimeout(() => (isChatWidget() ? startChat() : startCall()), 1000);
    }
  }

  /**
   * Generic public start alias. Widget type comes from async server config, so
   * never dispatch to voice/chat until initialization has resolved.
   */
  async function startWidget(...args) {
    await init();
    if (!state.isInitialized) {
      console.warn('Dograh Widget: Cannot start before initialization succeeds');
      return;
    }
    return isChatWidget() ? startChat() : startCall(...args);
  }

  /**
   * Generic public end alias. In chat mode this ends the server session; stop()
   * remains the non-destructive way to hide a floating chat panel.
   */
  async function endWidget(...args) {
    await init();
    if (!state.isInitialized) {
      console.warn('Dograh Widget: Cannot end before initialization succeeds');
      return;
    }
    return isChatWidget() ? endChatSession() : stopCall(...args);
  }

  /**
   * Parse context variables from JSON string
   */
  function parseContextVariables(contextStr) {
    if (!contextStr) return {};
    try {
      return JSON.parse(contextStr);
    } catch (e) {
      console.warn('Dograh Widget: Invalid context variables', e);
      return {};
    }
  }

  /**
   * True while a conversation is live and its context is already fixed
   */
  function isConversationActive() {
    if (isChatWidget()) {
      return ['starting', 'ready', 'waiting'].indexOf(state.chat.status) !== -1;
    }
    return ['connecting', 'connected'].indexOf(state.connectionStatus) !== -1;
  }

  /**
   * Merge visitor context supplied by the host page after the script loaded.
   *
   * The context is read when a conversation starts, so this applies to the next
   * one — a conversation already under way keeps what it was created with.
   */
  function setContextVariables(vars) {
    if (!vars || typeof vars !== 'object' || Array.isArray(vars)) {
      console.warn('Dograh Widget: setContext expects a plain object');
      return { ...(state.config.contextVariables || {}) };
    }

    state.config.contextVariables = {
      ...(state.config.contextVariables || {}),
      ...vars
    };

    if (isConversationActive()) {
      console.warn('Dograh Widget: context set during a conversation applies to the next one');
    }

    return { ...state.config.contextVariables };
  }

  /**
   * Inject widget styles
   */
  function injectStyles() {
    if (document.getElementById('dograh-widget-styles')) return;

    const styles = `
      .dograh-widget-container {
        position: fixed;
        z-index: 999999;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }

      .dograh-widget-container.bottom-right {
        bottom: 20px;
        right: 20px;
      }

      .dograh-widget-container.bottom-left {
        bottom: 20px;
        left: 20px;
      }

      .dograh-widget-container.top-right {
        top: 20px;
        right: 20px;
      }

      .dograh-widget-container.top-left {
        top: 20px;
        left: 20px;
      }

      .dograh-widget-cta {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 12px 20px;
        border: none;
        border-radius: 9999px;
        color: #ffffff;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        white-space: nowrap;
        max-width: calc(100vw - 40px);
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.2);
        transition: filter 150ms ease, transform 100ms ease, box-shadow 200ms ease;
        animation: dograh-cta-in 220ms ease-out;
      }

      .dograh-widget-cta:hover {
        color: #ffffff !important;
        filter: brightness(1.08);
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.28);
      }
      .dograh-widget-cta:active { transform: scale(0.98); }

      .dograh-widget-cta.dograh-state-connecting { background: #f59e0b !important; animation: dograh-pulse 1.6s infinite; }
      .dograh-widget-cta.dograh-state-connected  { background: #ef4444 !important; }
      .dograh-widget-cta.dograh-state-failed     { background: #ef4444 !important; opacity: 0.85; }

      @keyframes dograh-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
      }

      @keyframes dograh-cta-in {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
      }
    `;

    const styleSheet = document.createElement('style');
    styleSheet.id = 'dograh-widget-styles';
    styleSheet.textContent = styles;
    document.head.appendChild(styleSheet);
  }

  function ctaLabelForStatus(status) {
    switch (status) {
      case 'connecting': return 'Connecting…';
      case 'connected':  return 'End Call';
      case 'failed':     return 'Retry';
      default:           return state.config.buttonText || 'Talk to Agent';
    }
  }

  /**
   * Create floating widget UI — a single CTA pill button anchored to the
   * configured corner of the viewport.
   */
  function createFloatingWidget() {
    const container = document.createElement('div');
    container.className = `dograh-widget-container ${state.config.position}`;
    container.id = 'dograh-widget-root';

    const audio = document.createElement('audio');
    audio.id = 'dograh-widget-audio';
    audio.autoplay = true;
    audio.style.display = 'none';
    container.appendChild(audio);
    state.audioElement = audio;

    document.body.appendChild(container);
    renderFloating();
  }

  /**
   * Render the floating CTA pill. Re-renders preserve the hidden audio
   * element so an in-progress call is not interrupted on status changes.
   */
  function renderFloating() {
    const container = document.getElementById('dograh-widget-root');
    if (!container) return;

    Array.from(container.children).forEach((child) => {
      if (child !== state.audioElement) container.removeChild(child);
    });

    const status = state.connectionStatus || 'idle';

    const button = document.createElement('button');
    button.id = 'dograh-widget-cta';
    button.type = 'button';
    button.className = `dograh-widget-cta dograh-state-${status}`;
    // Idle uses configured color; status states use CSS-defined colors.
    if (status === 'idle') {
      button.style.backgroundColor = state.config.buttonColor;
    }
    button.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" y1="19" x2="12" y2="23"/>
        <line x1="8" y1="23" x2="16" y2="23"/>
      </svg>
      <span></span>
    `;
    button.querySelector('span').textContent = ctaLabelForStatus(status);
    button.onclick = toggleCall;

    container.appendChild(button);
  }

  /**
   * Create headless widget (no UI — host page drives everything via window.DograhWidget API)
   */
  function createHeadlessWidget() {
    const audio = document.createElement('audio');
    audio.id = 'dograh-widget-audio';
    audio.autoplay = true;
    audio.style.display = 'none';
    document.body.appendChild(audio);
    state.audioElement = audio;
  }

  /**
   * Toggle call (start or stop based on current state)
   */
  function toggleCall() {
    if (state.connectionStatus === 'idle' || state.connectionStatus === 'failed') {
      startCall();
    } else {
      stopCall();
    }
  }

  function updateFloatingButton(status) {
    state.connectionStatus = status;
    renderFloating();
  }

  /**
   * Create inline widget UI
   */
  function createInlineWidget() {
    // Find container element
    const container = document.getElementById(state.config.containerId);
    if (!container) {
      console.error(`Dograh Widget: Container element with id "${state.config.containerId}" not found`);
      if (state.callbacks.onError) {
        state.callbacks.onError(new Error('Container element not found'));
      }
      return;
    }

    // Clear container
    container.innerHTML = '';
    container.className = 'dograh-inline-container';

    // Add minimal inline styles
    const inlineStyles = `
      .dograh-inline-container {
        min-height: 200px;
        padding: 20px;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .dograh-inline-status {
        text-align: center;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }

      .dograh-inline-status-icon {
        width: 64px;
        height: 64px;
        margin: 0 auto 20px;
      }

      .dograh-inline-status-text {
        font-size: 18px;
        font-weight: 500;
        margin: 0 0 8px;
        color: #111827;
      }

      .dograh-inline-status-subtext {
        font-size: 14px;
        color: #6b7280;
        margin: 0 0 20px;
      }

      .dograh-inline-button-container {
        display: flex;
        gap: 12px;
        justify-content: center;
        margin-top: 20px;
      }

      .dograh-inline-btn {
        padding: 12px 32px;
        border-radius: 8px;
        border: none;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s;
        color: white;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      }

      .dograh-inline-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
      }

      .dograh-inline-btn:active {
        transform: translateY(0);
      }

      .dograh-inline-btn-start {
        background: #10b981;
      }

      .dograh-inline-btn-start:hover {
        background: #059669;
      }

      .dograh-inline-btn-end {
        background: #ef4444;
      }

      .dograh-inline-btn-end:hover {
        background: #dc2626;
      }

      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
      }

      .dograh-inline-pulse {
        animation: pulse 2s infinite;
      }
    `;

    // Add inline styles if not already added
    if (!document.getElementById('dograh-inline-styles')) {
      const styleSheet = document.createElement('style');
      styleSheet.id = 'dograh-inline-styles';
      styleSheet.textContent = inlineStyles;
      document.head.appendChild(styleSheet);
    }

    // Create initial status display
    updateInlineStatus('idle');

    // Store audio element (hidden)
    state.audioElement = document.createElement('audio');
    state.audioElement.autoplay = true;
    state.audioElement.style.display = 'none';
    container.appendChild(state.audioElement);

    // Mark widget as open (for inline mode, it's always "open")
    state.isOpen = true;
  }

  /**
   * Update inline widget status
   */
  function updateInlineStatus(status, text, subtext) {
    const container = document.getElementById(state.config.containerId);
    if (!container) return;

    // Update state
    state.connectionStatus = status;

    // Determine display text
    const displayText = text || {
      idle: 'Ready to Connect',
      connecting: 'Connecting...',
      connected: 'Call Active',
      failed: 'Connection Failed'
    }[status];

    const displaySubtext = subtext || {
      idle: state.config.callToActionText,
      connecting: 'Please wait while we establish connection',
      connected: 'You can speak now',
      failed: 'Please check your microphone and try again'
    }[status];

    // Simple button design: green to start, red to end
    let buttonHTML = '';
    if (status === 'idle' || status === 'failed') {
      // Button to start with configured color
      buttonHTML = `
        <button class="dograh-inline-btn dograh-inline-btn-start" id="dograh-inline-start-btn" style="background: ${state.config.buttonColor};">
          ${status === 'failed' ? 'Retry' : state.config.buttonText}
        </button>
      `;
    } else if (status === 'connecting' || status === 'connected') {
      // Red button to end
      buttonHTML = `
        <button class="dograh-inline-btn dograh-inline-btn-end" id="dograh-inline-end-btn">
          End Call
        </button>
      `;
    }

    // Update container content (preserve audio element)
    const audioElement = state.audioElement;
    container.innerHTML = `
      <div class="dograh-inline-status">
        <div class="dograh-inline-status-icon ${status === 'connecting' ? 'dograh-inline-pulse' : ''}">
          ${getStatusIcon(status)}
        </div>
        <p class="dograh-inline-status-text">${displayText}</p>
        <p class="dograh-inline-status-subtext">${displaySubtext}</p>
        <div class="dograh-inline-button-container">
          ${buttonHTML}
        </div>
      </div>
    `;

    // Re-append audio element
    if (audioElement) {
      container.appendChild(audioElement);
    }

    // Attach event handlers
    const startBtn = document.getElementById('dograh-inline-start-btn');
    if (startBtn) startBtn.onclick = startCall;

    const endBtn = document.getElementById('dograh-inline-end-btn');
    if (endBtn) endBtn.onclick = stopCall;

    // Trigger status change callback
    if (state.callbacks.onStatusChange) {
      state.callbacks.onStatusChange(status, displayText, displaySubtext);
    }
  }

  /**
   * Get status icon SVG
   */
  function getStatusIcon(status) {
    const icons = {
      idle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" y1="19" x2="12" y2="23"/>
        <line x1="8" y1="23" x2="16" y2="23"/>
      </svg>`,
      connecting: `<svg class="dograh-widget-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2v4"/>
        <path d="M12 18v4"/>
        <path d="M4.93 4.93l2.83 2.83"/>
        <path d="M16.24 16.24l2.83 2.83"/>
        <path d="M2 12h4"/>
        <path d="M18 12h4"/>
        <path d="M4.93 19.07l2.83-2.83"/>
        <path d="M16.24 7.76l2.83-2.83"/>
      </svg>`,
      connected: `<svg viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72"/>
        <path d="M15 7a2 2 0 0 1 2 2"/>
        <path d="M15 3a6 6 0 0 1 6 6"/>
      </svg>`,
      failed: `<svg viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>`
    };
    return icons[status] || icons.idle;
  }

  /**
   * Update widget status
   */
  function updateStatus(status, text, subtext) {
    state.connectionStatus = status;

    // Use appropriate update function based on mode
    if (state.config.embedMode === 'inline') {
      updateInlineStatus(status, text, subtext);
    } else if (state.config.embedMode === 'headless') {
      if (state.callbacks.onStatusChange) {
        state.callbacks.onStatusChange(status, text, subtext);
      }
    } else {
      updateFloatingButton(status);
    }
  }

  /**
   * Open widget (deprecated - kept for backwards compatibility)
   */
  function openWidget() {
    // No-op since we removed the modal
  }

  /**
   * Close widget (deprecated - kept for backwards compatibility)
   */
  function closeWidget() {
    // Stop call if active
    if (state.connectionStatus === 'connected' || state.connectionStatus === 'connecting') {
      stopCall();
    }
  }

  /**
   * Start voice call
   */
  async function startCall() {
    state.gracefulDisconnect = false;
    updateStatus('connecting', 'Connecting...', 'Please wait while we establish the connection');

    if (state.callbacks.onCallStart) {
      state.callbacks.onCallStart();
    }

    try {
      // Initialize session if using embed token
      if (state.config.token) {
        await initializeEmbedSession();
      } else {
        // Direct mode with workflow and run IDs
        state.sessionToken = 'direct-mode';
        state.workflowRunId = state.config.runId;
      }

      // Request microphone permission
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        // Release any stream still held from a prior attempt before retaining
        // the new one, so a re-entrant start can't leak the microphone.
        if (state.stream) {
          state.stream.getTracks().forEach(track => track.stop());
        }
        state.stream = stream;
      } catch (micError) {
        // Handle specific microphone permission errors
        let errorMessage = 'Please check your microphone and try again';

        if (micError.name === 'NotAllowedError' || micError.name === 'PermissionDeniedError') {
          errorMessage = 'Microphone permission denied. Please allow microphone access to start the call.';
        } else if (micError.name === 'NotFoundError' || micError.name === 'DevicesNotFoundError') {
          errorMessage = 'No microphone found. Please connect a microphone and try again.';
        } else if (micError.name === 'NotReadableError' || micError.name === 'TrackStartError') {
          errorMessage = 'Microphone is already in use by another application.';
        }

        throw new Error(errorMessage);
      }

      // Create WebRTC connection
      await createWebRTCConnection();

      // Connect WebSocket
      await connectWebSocket();

      // Start negotiation
      await negotiate();

    } catch (error) {
      console.error('Dograh Widget: Failed to start call', error);

      // Release anything acquired before the failure so a retry starts clean.
      // getUserMedia may have succeeded before a later step (WebSocket /
      // negotiation) threw, which would otherwise leave the mic held and block
      // the next getUserMedia(). Null the refs before close() so the peer/ws
      // state handlers short-circuit instead of re-entering teardown.
      if (state.stream) {
        state.stream.getTracks().forEach(track => track.stop());
        state.stream = null;
      }
      if (state.pc) {
        const pc = state.pc;
        state.pc = null;
        if (pc.signalingState !== 'closed') {
          pc.close();
        }
      }
      if (state.ws) {
        const ws = state.ws;
        state.ws = null;
        if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
          ws.close();
        }
      }

      updateStatus('failed', 'Connection failed', error.message || 'Please check your microphone and try again');

      // Trigger error callback
      if (state.callbacks.onError) {
        state.callbacks.onError(error);
      }
    }
  }

  /**
   * Initialize embed session
   */
  async function initializeEmbedSession() {
    const response = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/init`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Origin': window.location.origin
      },
      body: JSON.stringify({
        token: state.config.token,
        context_variables: state.config.contextVariables
      })
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to initialize session');
    }

    const data = await response.json();
    state.sessionToken = data.session_token;
    state.workflowRunId = data.workflow_run_id;
    state.workflowId = data.config.workflow_id;

    // Fetch TURN credentials after session initialization
    await fetchTurnCredentials();
  }

  /**
   * Fetch TURN credentials for WebRTC connection
   */
  async function fetchTurnCredentials() {
    // Skip the request entirely when the server reports no TURN server is
    // configured — it would only 503. Deployments without coturn (OSS/local)
    // fall back to STUN.
    if (state.config.turnEnabled === false) {
      console.log('Dograh Widget: TURN server disabled in server config, using STUN only');
      return;
    }

    if (!state.sessionToken) {
      console.warn('Dograh Widget: No session token available for TURN credentials');
      return;
    }

    try {
      const response = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/turn-credentials/${state.sessionToken}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin
        }
      });

      if (response.ok) {
        state.turnCredentials = await response.json();
        console.log(`TURN credentials obtained, TTL: ${state.turnCredentials.ttl}s`);
      } else if (response.status === 503) {
        // TURN not configured on server - this is OK, we'll use STUN only
        console.log('TURN server not configured, using STUN only');
      } else {
        console.warn(`Failed to fetch TURN credentials: ${response.status}`);
      }
    } catch (error) {
      console.warn('Failed to fetch TURN credentials, continuing without TURN:', error);
    }
  }

  /**
   * Create WebRTC peer connection
   */
  function createWebRTCConnection() {
    // Build ICE servers list
    const iceServers = [{ urls: ['stun:stun.l.google.com:19302'] }];

    // Add TURN server if credentials are available
    if (state.turnCredentials && state.turnCredentials.uris && state.turnCredentials.uris.length > 0) {
      iceServers.push({
        urls: state.turnCredentials.uris,
        username: state.turnCredentials.username,
        credential: state.turnCredentials.password
      });
      console.log(`TURN server configured with ${state.turnCredentials.uris.length} URIs`);
    }

    const config = {
      iceServers: iceServers
    };

    // Diagnostic: when the backend runs with FORCE_TURN_RELAY=true, restrict the
    // browser to relay-only candidates so media must traverse TURN. A TURN
    // misconfiguration then surfaces as an ICE failure instead of silently
    // falling back to host/srflx.
    if (state.config.forceTurnRelay) {
      config.iceTransportPolicy = 'relay';
      console.log('Dograh Widget: FORCE_TURN_RELAY is on — restricting ICE to relay candidates only');
    }

    state.pc = new RTCPeerConnection(config);

    // Add audio track
    if (state.stream) {
      state.stream.getTracks().forEach(track => {
        state.pc.addTrack(track, state.stream);
      });
    }

    // Handle incoming audio
    state.pc.ontrack = (event) => {
      if (event.track.kind === 'audio' && state.audioElement) {
        state.audioElement.srcObject = event.streams[0];
      }
    };

    // Monitor connection state
    state.pc.oniceconnectionstatechange = handlePeerConnectionStateChange;
    state.pc.onconnectionstatechange = handlePeerConnectionStateChange;
    state.pc.onicecandidate = sendIceCandidate;
  }

  function handlePeerConnectionStateChange() {
    const pc = state.pc;
    if (!pc) return;

    console.log('Peer connection state:', pc.connectionState, 'ICE:', pc.iceConnectionState);

    if (pc.connectionState === 'connected' || pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
      const wasAlreadyConnected = state.callStartedAt !== null;
      updateStatus('connected', 'Connected', 'Your voice call is now active');
      if (!wasAlreadyConnected) {
        state.callStartedAt = Date.now();
        if (state.callbacks.onCallConnected) {
          state.callbacks.onCallConnected({
            agentId: state.config.workflowId || null,
            token: state.config.token || null,
            workflowRunId: state.workflowRunId || null
          });
        }
      }
      return;
    }

    if (pc.connectionState === 'failed' || pc.iceConnectionState === 'failed') {
      stopCall({
        graceful: false,
        status: 'failed',
        text: 'Connection lost',
        subtext: 'The call has been disconnected'
      });
      return;
    }

    if (
      pc.connectionState === 'closed' ||
      pc.connectionState === 'disconnected' ||
      pc.iceConnectionState === 'closed' ||
      pc.iceConnectionState === 'disconnected'
    ) {
      stopCall({ graceful: true });
    }
  }

  function sendIceCandidate(event) {
    // Handle ICE candidates for trickling
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      const message = {
        type: 'ice-candidate',
        payload: {
          candidate: event.candidate ? {
            candidate: event.candidate.candidate,
            sdpMid: event.candidate.sdpMid,
            sdpMLineIndex: event.candidate.sdpMLineIndex
          } : null,
          pc_id: state.pcId
        }
      };
      state.ws.send(JSON.stringify(message));
    }
  }

  /**
   * Connect WebSocket for signaling
   */
  async function connectWebSocket() {
    return new Promise((resolve, reject) => {
      // Use public signaling endpoint for embed tokens
      const wsUrl = `${state.config.apiBaseUrl.replace('http', 'ws')}/api/v1/ws/public/signaling/${state.sessionToken}`;

      state.ws = new WebSocket(wsUrl);
      state.pcId = generatePeerId();

      state.ws.onopen = () => {
        console.log('WebSocket connected');
        resolve();
      };

      state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        reject(error);
      };

      state.ws.onclose = (event) => {
        console.log('WebSocket closed');
        state.ws = null;

        if (event.reason === 'call ended') {
          stopCall({ graceful: true, closeWebSocket: false });
          return;
        }

        if (state.connectionStatus === 'connected' && !state.gracefulDisconnect) {
          updateStatus('failed', 'Connection lost', 'The call has been disconnected');
        }
      };

      state.ws.onmessage = async (event) => {
        try {
          const message = JSON.parse(event.data);
          await handleWebSocketMessage(message);
        } catch (e) {
          console.error('Failed to handle WebSocket message:', e);
        }
      };
    });
  }

  /**
   * Handle WebSocket messages
   */
  async function handleWebSocketMessage(message) {
    switch (message.type) {
      case 'answer':
        const answer = message.payload;
        console.log('Received answer from server');

        await state.pc.setRemoteDescription({
          type: 'answer',
          sdp: answer.sdp
        });
        break;

      case 'ice-candidate':
        const candidate = message.payload.candidate;
        if (candidate) {
          try {
            await state.pc.addIceCandidate({
              candidate: candidate.candidate,
              sdpMid: candidate.sdpMid,
              sdpMLineIndex: candidate.sdpMLineIndex
            });
            console.log('Added remote ICE candidate');
          } catch (e) {
            console.error('Failed to add ICE candidate:', e);
          }
        }
        break;

      case 'error':
        console.error('Server error:', message.payload);
        updateStatus('failed', 'Server error', message.payload.message || 'An error occurred');
        break;

      case 'call-ended':
        console.log('Call ended by server:', message.payload);
        stopCall({ graceful: true });
        break;

      default:
        console.warn('Unknown message type:', message.type);
    }
  }

  /**
   * Negotiate WebRTC connection
   */
  async function negotiate() {
    const offer = await state.pc.createOffer();
    await state.pc.setLocalDescription(offer);

    const message = {
      type: 'offer',
      payload: {
        sdp: offer.sdp,
        type: 'offer',
        pc_id: state.pcId,
        workflow_id: parseInt(state.config.workflowId),
        workflow_run_id: parseInt(state.workflowRunId)
      }
    };

    state.ws.send(JSON.stringify(message));
    console.log('Sent offer via WebSocket');
  }

  /**
   * Stop voice call
   */
  function stopCall(options = {}) {
    const graceful = options.graceful !== false;
    const closeWebSocket = options.closeWebSocket !== false;
    const status = options.status || 'idle';
    const text = options.text || 'Call ended';
    const subtext = options.subtext || 'Click below to start a new call';

    state.gracefulDisconnect = graceful;

    // Fire onCallDisconnected only if the call had actually connected, with
    // identifiers and duration. Must run before we clear callStartedAt.
    if (state.callStartedAt && state.callbacks.onCallDisconnected) {
      const durationSeconds = Math.round((Date.now() - state.callStartedAt) / 1000);
      state.callbacks.onCallDisconnected({
        agentId: state.config.workflowId || null,
        token: state.config.token || null,
        workflowRunId: state.workflowRunId || null,
        durationSeconds
      });
    }
    state.callStartedAt = null;

    updateStatus(status, text, subtext);

    if (state.callbacks.onCallEnd) {
      state.callbacks.onCallEnd();
    }

    // Close WebSocket
    if (closeWebSocket && state.ws) {
      const ws = state.ws;
      state.ws = null;
      if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
        ws.close();
      }
    } else if (!closeWebSocket) {
      state.ws = null;
    }

    // Stop media tracks
    if (state.stream) {
      state.stream.getTracks().forEach(track => track.stop());
      state.stream = null;
    }

    // Close peer connection
    if (state.pc) {
      const pc = state.pc;
      state.pc = null;
      if (pc.signalingState !== 'closed') {
        pc.close();
      }
    }

    // Clear audio
    if (state.audioElement) {
      state.audioElement.srcObject = null;
    }
  }

  /**
   * Retry connection
   */
  function retryCall() {
    updateStatus('idle', 'Ready to start', 'Click below to begin your voice call');
    setTimeout(() => startCall(), 500);
  }

  /**
   * Generate unique peer ID
   */
  function generatePeerId() {
    const array = new Uint8Array(16);
    crypto.getRandomValues(array);
    return 'PC-' + Array.from(array)
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');
  }

  // ===========================================================================
  // Chat widget (widgetType === 'chat')
  //
  // Chat speaks plain REST to the public embed chat endpoints — no WebRTC, no
  // WebSocket, no microphone. One blocking POST per turn (the server caps a
  // turn at 60s, so no client timeout below that); the typing indicator covers
  // the wait. XSS boundary: message text only ever flows through textContent.
  // ===========================================================================

  const CHAT_ICON_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
  </svg>`;

  const SEND_ICON_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <line x1="22" y1="2" x2="11" y2="13"/>
    <polygon points="22 2 15 22 11 13 2 9 22 2"/>
  </svg>`;

  function isChatWidget() {
    return state.config.widgetType === 'chat';
  }

  /**
   * Inject chat widget styles
   */
  function injectChatStyles() {
    if (document.getElementById('dograh-chat-styles')) return;

    const styles = `
      .dograh-chat-panel {
        display: flex;
        flex-direction: column;
        width: 360px;
        height: min(520px, calc(100vh - 120px));
        background: #ffffff;
        border-radius: 12px;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
        overflow: hidden;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }

      .dograh-widget-container.bottom-right .dograh-chat-panel { position: absolute; bottom: 60px; right: 0; }
      .dograh-widget-container.bottom-left  .dograh-chat-panel { position: absolute; bottom: 60px; left: 0; }
      .dograh-widget-container.top-right    .dograh-chat-panel { position: absolute; top: 60px; right: 0; }
      .dograh-widget-container.top-left     .dograh-chat-panel { position: absolute; top: 60px; left: 0; }

      @media (max-width: 480px) {
        .dograh-widget-container .dograh-chat-panel {
          position: fixed;
          left: 0;
          right: 0;
          bottom: 0;
          top: auto;
          width: 100%;
          height: min(75vh, 560px);
          border-radius: 12px 12px 0 0;
        }
      }

      .dograh-chat-panel--inline {
        position: static;
        width: 100%;
        height: 100%;
        min-height: 320px;
        box-shadow: none;
        border: 1px solid #e5e7eb;
      }

      .dograh-chat-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 12px 16px;
        color: #ffffff;
        font-size: 14px;
        font-weight: 600;
        flex: 0 0 auto;
      }

      .dograh-chat-header-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .dograh-chat-header-actions {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        margin-left: 12px;
      }

      .dograh-chat-end {
        display: none;
        border: 1px solid rgba(255, 255, 255, 0.55);
        border-radius: 6px;
        background: rgba(0, 0, 0, 0.12);
        color: #ffffff;
        font: inherit;
        font-size: 12px;
        font-weight: 600;
        line-height: 1;
        cursor: pointer;
        padding: 6px 8px;
        white-space: nowrap;
      }
      .dograh-chat-end:hover { background: rgba(0, 0, 0, 0.22); }
      .dograh-chat-end:disabled { cursor: default; opacity: 0.55; }

      .dograh-chat-end-confirmation {
        display: none;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 8px 12px;
        color: #7f1d1d;
        background: #fef2f2;
        border-bottom: 1px solid #fecaca;
        font-size: 12px;
        font-weight: 500;
        flex: 0 0 auto;
      }
      .dograh-chat-end-confirm-actions {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        flex: 0 0 auto;
      }
      .dograh-chat-end-confirm-cancel,
      .dograh-chat-end-confirm-submit {
        border-radius: 6px;
        font: inherit;
        font-size: 12px;
        font-weight: 600;
        line-height: 1;
        cursor: pointer;
        padding: 6px 8px;
        white-space: nowrap;
      }
      .dograh-chat-end-confirm-cancel {
        border: 1px solid #d1d5db;
        background: #ffffff;
        color: #374151;
      }
      .dograh-chat-end-confirm-cancel:hover { background: #f9fafb; }
      .dograh-chat-end-confirm-submit {
        border: 1px solid #dc2626;
        background: #dc2626;
        color: #ffffff;
      }
      .dograh-chat-end-confirm-submit:hover {
        border-color: #b91c1c;
        background: #b91c1c;
        color: #ffffff;
      }

      .dograh-chat-close {
        background: none;
        border: none;
        color: #ffffff;
        font-size: 20px;
        line-height: 1;
        cursor: pointer;
        padding: 0 2px;
        opacity: 0.85;
      }
      .dograh-chat-close:hover { opacity: 1; }

      .dograh-chat-messages {
        flex: 1 1 auto;
        overflow-y: auto;
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        background: #f9fafb;
      }

      .dograh-chat-bubble {
        max-width: 80%;
        padding: 8px 12px;
        border-radius: 12px;
        font-size: 14px;
        line-height: 1.45;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .dograh-chat-bubble--user {
        align-self: flex-end;
        color: #ffffff;
        border-bottom-right-radius: 4px;
      }
      .dograh-chat-bubble--assistant {
        align-self: flex-start;
        background: #e5e7eb;
        color: #111827;
        border-bottom-left-radius: 4px;
      }
      .dograh-chat-bubble--failed { opacity: 0.55; }

      .dograh-chat-typing {
        align-self: flex-start;
        display: inline-flex;
        gap: 4px;
        padding: 10px 14px;
        background: #e5e7eb;
        border-radius: 12px;
        border-bottom-left-radius: 4px;
      }
      .dograh-chat-typing span {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #6b7280;
        animation: dograh-typing 1.2s infinite;
      }
      .dograh-chat-typing span:nth-child(2) { animation-delay: 0.15s; }
      .dograh-chat-typing span:nth-child(3) { animation-delay: 0.3s; }

      @keyframes dograh-typing {
        0%, 60%, 100% { opacity: 0.35; transform: translateY(0); }
        30% { opacity: 1; transform: translateY(-3px); }
      }

      .dograh-chat-banner {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px 12px;
        font-size: 12px;
        color: #991b1b;
        background: #fef2f2;
        border-top: 1px solid #fecaca;
        flex: 0 0 auto;
      }
      .dograh-chat-banner--info {
        color: #374151;
        background: #f3f4f6;
        border-top: 1px solid #e5e7eb;
      }
      .dograh-chat-restart {
        border: none;
        background: none;
        color: #1d4ed8;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        padding: 0;
        white-space: nowrap;
      }

      .dograh-chat-composer {
        display: flex;
        align-items: flex-end;
        gap: 8px;
        padding: 10px 12px;
        border-top: 1px solid #e5e7eb;
        background: #ffffff;
        flex: 0 0 auto;
      }
      .dograh-chat-input {
        flex: 1;
        resize: none;
        border: 1px solid #d1d5db;
        border-radius: 8px;
        padding: 8px 10px;
        font: inherit;
        font-size: 14px;
        max-height: 96px;
        outline: none;
        background: #ffffff;
        color: #111827;
      }
      .dograh-chat-input:focus { border-color: #9ca3af; }
      .dograh-chat-input:disabled { background: #f9fafb; color: #9ca3af; }
      .dograh-chat-send {
        width: 36px;
        height: 36px;
        border: none;
        border-radius: 8px;
        color: #ffffff;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 auto;
      }
      .dograh-chat-send:disabled { opacity: 0.5; cursor: default; }

      .dograh-chat-inline-container {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 420px;
      }
      .dograh-chat-inline-cta {
        text-align: center;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      .dograh-chat-inline-icon { color: #9ca3af; margin-bottom: 16px; }
      .dograh-chat-inline-icon svg { width: 56px; height: 56px; }
      .dograh-chat-inline-subtext {
        font-size: 14px;
        color: #6b7280;
        margin: 0 0 20px;
      }
      .dograh-chat-inline-start {
        padding: 12px 32px;
        border-radius: 8px;
        border: none;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        color: #ffffff;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      }
    `;

    const styleSheet = document.createElement('style');
    styleSheet.id = 'dograh-chat-styles';
    styleSheet.textContent = styles;
    document.head.appendChild(styleSheet);
  }

  /**
   * Build the chat panel skeleton once; renders update its parts in place so
   * the composer keeps focus while typing.
   */
  function buildChatPanel(options) {
    const withClose = Boolean(options && options.withClose);

    const panel = document.createElement('div');
    panel.className = 'dograh-chat-panel';

    const header = document.createElement('div');
    header.className = 'dograh-chat-header';
    header.style.backgroundColor = state.config.buttonColor;
    const title = document.createElement('span');
    title.className = 'dograh-chat-header-title';
    title.textContent = state.config.buttonText || 'Chat with Agent';
    header.appendChild(title);

    const headerActions = document.createElement('div');
    headerActions.className = 'dograh-chat-header-actions';

    const endBtn = document.createElement('button');
    endBtn.className = 'dograh-chat-end';
    endBtn.type = 'button';
    endBtn.textContent = 'End chat';
    endBtn.setAttribute('aria-controls', 'dograh-chat-end-confirmation');
    endBtn.setAttribute('aria-expanded', 'false');
    endBtn.onclick = () => {
      state.chat.confirmingEnd = true;
      renderChat();
      if (state.chatEls) state.chatEls.confirmEndBtn.focus();
    };
    headerActions.appendChild(endBtn);

    if (withClose) {
      const closeBtn = document.createElement('button');
      closeBtn.className = 'dograh-chat-close';
      closeBtn.type = 'button';
      closeBtn.setAttribute('aria-label', 'Close chat');
      closeBtn.textContent = '×';
      closeBtn.onclick = closeChatPanel;
      headerActions.appendChild(closeBtn);
    }
    header.appendChild(headerActions);
    panel.appendChild(header);

    const endConfirmation = document.createElement('div');
    endConfirmation.id = 'dograh-chat-end-confirmation';
    endConfirmation.className = 'dograh-chat-end-confirmation';
    endConfirmation.setAttribute('role', 'group');
    endConfirmation.setAttribute('aria-label', 'Confirm ending chat');

    const endConfirmationText = document.createElement('span');
    endConfirmationText.textContent = 'End this chat?';
    endConfirmation.appendChild(endConfirmationText);

    const endConfirmActions = document.createElement('div');
    endConfirmActions.className = 'dograh-chat-end-confirm-actions';

    const cancelEndBtn = document.createElement('button');
    cancelEndBtn.className = 'dograh-chat-end-confirm-cancel';
    cancelEndBtn.type = 'button';
    cancelEndBtn.textContent = 'Cancel';
    cancelEndBtn.onclick = () => {
      state.chat.confirmingEnd = false;
      renderChat();
      if (state.chatEls && state.chat.panelOpen && state.chat.status === 'ready') {
        state.chatEls.input.focus();
      }
    };
    endConfirmActions.appendChild(cancelEndBtn);

    const confirmEndBtn = document.createElement('button');
    confirmEndBtn.className = 'dograh-chat-end-confirm-submit';
    confirmEndBtn.type = 'button';
    confirmEndBtn.textContent = 'End chat';
    confirmEndBtn.onclick = () => {
      state.chat.confirmingEnd = false;
      endChatSession();
    };
    endConfirmActions.appendChild(confirmEndBtn);
    endConfirmation.appendChild(endConfirmActions);
    panel.appendChild(endConfirmation);

    const messages = document.createElement('div');
    messages.className = 'dograh-chat-messages';
    panel.appendChild(messages);

    const banner = document.createElement('div');
    banner.className = 'dograh-chat-banner';
    banner.style.display = 'none';
    panel.appendChild(banner);

    const composer = document.createElement('div');
    composer.className = 'dograh-chat-composer';
    const input = document.createElement('textarea');
    input.className = 'dograh-chat-input';
    input.rows = 1;
    input.placeholder = 'Type a message…';
    input.oninput = () => {
      state.chat.draft = input.value;
      autoGrowChatInput(input);
    };
    input.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitChatComposer();
      }
    };
    const sendBtn = document.createElement('button');
    sendBtn.className = 'dograh-chat-send';
    sendBtn.type = 'button';
    sendBtn.style.backgroundColor = state.config.buttonColor;
    sendBtn.setAttribute('aria-label', 'Send message');
    sendBtn.innerHTML = SEND_ICON_SVG; // static markup, never user data
    sendBtn.onclick = submitChatComposer;
    composer.appendChild(input);
    composer.appendChild(sendBtn);
    panel.appendChild(composer);

    state.chatEls = {
      panel,
      messages,
      banner,
      input,
      sendBtn,
      endBtn,
      endConfirmation,
      confirmEndBtn
    };
    return panel;
  }

  function autoGrowChatInput(input) {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 96) + 'px';
  }

  /**
   * Create floating chat widget — CTA pill toggling an anchored chat panel.
   */
  function createFloatingChatWidget() {
    const container = document.createElement('div');
    container.className = `dograh-widget-container ${state.config.position}`;
    container.id = 'dograh-widget-root';
    document.body.appendChild(container);

    const panel = buildChatPanel({ withClose: true });
    panel.style.display = 'none';
    container.appendChild(panel);

    const button = document.createElement('button');
    button.id = 'dograh-widget-cta';
    button.type = 'button';
    button.className = 'dograh-widget-cta';
    button.style.backgroundColor = state.config.buttonColor;
    button.innerHTML = `${CHAT_ICON_SVG}<span></span>`;
    button.querySelector('span').textContent = state.config.buttonText || 'Chat with Agent';
    button.onclick = toggleChatPanel;
    container.appendChild(button);
  }

  /**
   * Create inline chat widget — pre-chat CTA screen in the host container; the
   * CTA, autoStart, and public API all open the same panel/session lifecycle.
   */
  function createInlineChatWidget() {
    const container = document.getElementById(state.config.containerId);
    if (!container) {
      console.error(`Dograh Widget: Container element with id "${state.config.containerId}" not found`);
      if (state.callbacks.onError) {
        state.callbacks.onError(new Error('Container element not found'));
      }
      return;
    }

    container.innerHTML = '';
    container.className = 'dograh-chat-inline-container';

    const cta = document.createElement('div');
    cta.className = 'dograh-chat-inline-cta';

    const icon = document.createElement('div');
    icon.className = 'dograh-chat-inline-icon';
    icon.innerHTML = CHAT_ICON_SVG; // static markup
    cta.appendChild(icon);

    const subtext = document.createElement('p');
    subtext.className = 'dograh-chat-inline-subtext';
    subtext.textContent = state.config.callToActionText || 'Click to start chatting';
    cta.appendChild(subtext);

    const startBtn = document.createElement('button');
    startBtn.className = 'dograh-chat-inline-start';
    startBtn.type = 'button';
    startBtn.style.backgroundColor = state.config.buttonColor;
    startBtn.textContent = state.config.buttonText || 'Chat with Agent';
    startBtn.onclick = () => startChat();
    cta.appendChild(startBtn);

    container.appendChild(cta);
    state.isOpen = true;
  }

  /**
   * Replace the inline CTA with the chat panel. Keeping this in the shared
   * startChat path ensures autoStart and the public API never create a hidden
   * session behind the CTA.
   */
  function openInlineChatPanel() {
    const container = document.getElementById(state.config.containerId);
    if (!container) {
      console.error(`Dograh Widget: Container element with id "${state.config.containerId}" not found`);
      return false;
    }

    const cta = container.querySelector('.dograh-chat-inline-cta');
    if (cta) {
      cta.remove();
    }

    if (!state.chatEls || !state.chatEls.panel || !container.contains(state.chatEls.panel)) {
      const panel = buildChatPanel({ withClose: false });
      panel.classList.add('dograh-chat-panel--inline');
      container.appendChild(panel);
    }

    return true;
  }

  function toggleChatPanel() {
    if (state.chat.panelOpen) {
      closeChatPanel();
    } else {
      startChat();
    }
  }

  function closeChatPanel() {
    state.chat.panelOpen = false;
    state.chat.confirmingEnd = false;
    if (state.chatEls && state.chatEls.panel) {
      state.chatEls.panel.style.display = 'none';
    }
  }

  /**
   * Open the chat (public API). Shows the panel (if any) and starts the
   * session on first open, including an open requested by autoStart.
   */
  async function startChat() {
    if (!isChatWidget()) {
      console.warn('Dograh Widget: startChat() called on a voice widget');
      return;
    }
    if (state.config.embedMode === 'inline' && !openInlineChatPanel()) {
      return;
    }
    state.chat.panelOpen = true;
    if (state.chatEls && state.chatEls.panel) {
      state.chatEls.panel.style.display = 'flex';
    }
    if (state.chat.status === 'idle' || state.chat.status === 'error') {
      await startChatSession();
    } else {
      renderChat();
    }
  }

  function resetChatSession() {
    state.sessionToken = null;
    state.workflowRunId = null;
    state.chat.revision = null;
    state.chat.turns = [];
    state.chat.pendingUserText = null;
    state.chat.ending = false;
    state.chat.confirmingEnd = false;
    state.chat.seenAssistantTurnIds = new Set();
  }

  async function startNewChatSession() {
    resetChatSession();
    await startChatSession();

    if (state.chatEls && state.chat.panelOpen && state.chat.status === 'ready') {
      state.chatEls.input.focus();
    }
  }

  /**
   * Initialize the embed session for chat. The server derives the run type
   * from the token settings and returns the greeting transcript inline.
   */
  async function startChatSession() {
    if (state.chat.status === 'starting') return;
    updateChatStatus('starting');

    try {
      const response = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/init`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin
        },
        body: JSON.stringify({
          token: state.config.token,
          context_variables: state.config.contextVariables
        })
      });

      if (!response.ok) {
        if (response.status === 402) {
          updateChatStatus('ended', 'The agent is unavailable right now.');
          return;
        }
        throw new Error(`Failed to start chat: ${response.status}`);
      }

      const data = await response.json();
      state.sessionToken = data.session_token;
      state.workflowRunId = data.workflow_run_id;
      const completed = applyChatSession(data.chat_session);
      updateChatStatus(completed ? 'ended' : 'ready', completed ? 'Conversation ended.' : null);
    } catch (error) {
      console.error('Dograh Widget: Failed to start chat', error);
      updateChatStatus('error', 'Could not start the chat.');
      if (state.callbacks.onError) {
        state.callbacks.onError(error);
      }
    }
  }

  /**
   * Replace local transcript state from a server chat_session payload.
   * Returns true when the conversation is completed server-side.
   */
  function applyChatSession(chatSession) {
    if (!chatSession) return false;
    state.chat.revision = chatSession.revision;
    state.chat.turns = chatSession.turns || [];
    state.chat.pendingUserText = null;
    notifyNewAssistantMessages();
    return Boolean(chatSession.is_completed);
  }

  function notifyNewAssistantMessages() {
    state.chat.turns.forEach((turn) => {
      if (turn.status !== 'completed' || !turn.assistant_message || !turn.assistant_message.text) return;
      if (state.chat.seenAssistantTurnIds.has(turn.id)) return;
      state.chat.seenAssistantTurnIds.add(turn.id);
      if (state.callbacks.onMessage) {
        state.callbacks.onMessage(turn.assistant_message.text, turn);
      }
    });
  }

  /**
   * Send a visitor message. Resolves with the updated turns array, or null if
   * the message could not be delivered.
   */
  async function sendChatMessage(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return null;
    if (!isChatWidget()) {
      console.warn('Dograh Widget: sendMessage() called on a voice widget');
      return null;
    }
    if (!state.sessionToken || state.chat.status === 'ended' || state.chat.status === 'expired') {
      console.warn('Dograh Widget: no active chat session');
      return null;
    }
    if (state.chat.status === 'waiting' || state.chat.status === 'starting') {
      return null; // one turn at a time; the composer is disabled anyway
    }

    state.chat.pendingUserText = trimmed;
    updateChatStatus('waiting');

    try {
      const response = await fetch(
        `${state.config.apiBaseUrl}/api/v1/public/embed/chat/${state.sessionToken}/messages`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Origin': window.location.origin
          },
          body: JSON.stringify({ text: trimmed, expected_revision: state.chat.revision })
        }
      );

      if (response.ok) {
        const data = await response.json();
        const completed = applyChatSession(data);
        updateChatStatus(completed ? 'ended' : 'ready', completed ? 'Conversation ended.' : null);
        return state.chat.turns;
      }

      state.chat.pendingUserText = null;

      if (response.status === 409) {
        // Someone else advanced the session (e.g. a second tab). Resync the
        // transcript and hand the text back as a draft.
        const resynced = await resyncChatSession();
        if (!resynced && state.chat.status === 'expired') {
          updateChatStatus('expired');
          return null;
        }
        state.chat.draft = trimmed;
        updateChatStatus('ready', 'Message not sent — please try again.');
        return null;
      }
      if (response.status === 402) {
        updateChatStatus('ended', 'The agent is unavailable right now.');
        return null;
      }
      if (response.status === 429) {
        updateChatStatus('ended', 'Message limit reached for this conversation.');
        return null;
      }
      if (response.status === 403 || response.status === 404) {
        // The 1-hour embed session expired (or the token was deactivated).
        updateChatStatus('expired');
        return null;
      }
      if (response.status === 400) {
        // Conversation completed server-side (e.g. reached an end node).
        updateChatStatus('ended', 'Conversation ended.');
        return null;
      }
      throw new Error(`Chat message failed: ${response.status}`);
    } catch (error) {
      console.error('Dograh Widget: Failed to send message', error);
      state.chat.pendingUserText = null;
      state.chat.draft = trimmed;
      updateChatStatus('ready', 'Message not sent — please try again.');
      if (state.callbacks.onError) {
        state.callbacks.onError(error);
      }
      return null;
    }
  }

  /**
   * End the active chat and persist completion before updating local UI state.
   * The endpoint is revision-guarded, and one resync/retry handles a session
   * advanced from another browser tab.
   */
  async function endChatSession(retryOnConflict = true) {
    if (!state.isInitialized) {
      await init();
    }
    if (!state.isInitialized) {
      return null;
    }
    if (!isChatWidget()) {
      console.warn('Dograh Widget: endChat() called on a voice widget');
      return null;
    }
    if (!state.sessionToken || state.chat.status === 'ended' || state.chat.status === 'expired') {
      return state.chat.turns.slice();
    }
    if (state.chat.status === 'starting' || state.chat.status === 'waiting' || state.chat.ending) {
      return null;
    }

    state.chat.confirmingEnd = false;
    state.chat.ending = true;
    renderChat();

    try {
      const response = await fetch(
        `${state.config.apiBaseUrl}/api/v1/public/embed/chat/${state.sessionToken}/end`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Origin': window.location.origin
          },
          body: JSON.stringify({ expected_revision: state.chat.revision })
        }
      );

      if (response.ok) {
        const data = await response.json();
        applyChatSession(data);
        state.chat.ending = false;
        updateChatStatus('ended', 'Conversation ended.');
        return state.chat.turns.slice();
      }

      if (response.status === 409 && retryOnConflict) {
        const resynced = await resyncChatSession();
        state.chat.ending = false;
        if (resynced) {
          return endChatSession(false);
        }
        if (state.chat.status === 'expired') {
          updateChatStatus('expired');
          return null;
        }
      }
      if (response.status === 403 || response.status === 404) {
        state.chat.ending = false;
        updateChatStatus('expired');
        return null;
      }
      throw new Error(`Failed to end chat: ${response.status}`);
    } catch (error) {
      console.error('Dograh Widget: Failed to end chat', error);
      state.chat.ending = false;
      updateChatStatus('ready', 'Could not end the chat. Please try again.');
      if (state.callbacks.onError) {
        state.callbacks.onError(error);
      }
      return null;
    }
  }

  /**
   * Refetch the transcript (409 recovery). Marks the session expired on
   * 403/404 so the caller can surface the restart UI.
   */
  async function resyncChatSession() {
    try {
      const response = await fetch(
        `${state.config.apiBaseUrl}/api/v1/public/embed/chat/${state.sessionToken}`,
        {
          headers: {
            'Content-Type': 'application/json',
            'Origin': window.location.origin
          }
        }
      );
      if (response.ok) {
        applyChatSession(await response.json());
        return true;
      }
      if (response.status === 403 || response.status === 404) {
        state.chat.status = 'expired';
      }
    } catch (error) {
      console.warn('Dograh Widget: chat resync failed', error);
    }
    return false;
  }

  async function submitChatComposer() {
    const text = (state.chat.draft || '').trim();
    if (!text || state.chat.status !== 'ready') return;
    state.chat.draft = '';
    if (state.chatEls) {
      state.chatEls.input.value = '';
      autoGrowChatInput(state.chatEls.input);
    }
    await sendChatMessage(text);

    if (state.chatEls && state.chat.panelOpen && state.chat.status === 'ready') {
      state.chatEls.input.focus();
    }
  }

  function updateChatStatus(status, banner) {
    if (status !== 'ready') state.chat.confirmingEnd = false;
    state.chat.status = status;
    state.chat.banner = banner || null;
    renderChat();
    if (state.callbacks.onChatStateChange) {
      state.callbacks.onChatStateChange(status);
    }
  }

  /**
   * Update the panel in place from chat state. No-op in headless mode.
   */
  function renderChat() {
    if (!state.chatEls) return;
    renderChatMessages();
    renderChatBanner();
    updateChatEndConfirmationState();
    updateChatComposerState();
    updateChatEndButtonState();
  }

  function renderChatMessages() {
    const messages = state.chatEls.messages;
    // Keep autoscroll only when the viewer is already pinned to the bottom.
    const pinned = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 40;
    messages.textContent = '';

    state.chat.turns.forEach((turn) => {
      if (turn.user_message && turn.user_message.text) {
        messages.appendChild(buildChatBubble(turn.user_message.text, 'user', turn.status === 'failed'));
      }
      if (turn.assistant_message && turn.assistant_message.text) {
        messages.appendChild(buildChatBubble(turn.assistant_message.text, 'assistant', false));
      }
    });

    if (state.chat.pendingUserText) {
      messages.appendChild(buildChatBubble(state.chat.pendingUserText, 'user', false));
    }
    if (state.chat.status === 'waiting' || state.chat.status === 'starting') {
      const typing = document.createElement('div');
      typing.className = 'dograh-chat-typing';
      typing.innerHTML = '<span></span><span></span><span></span>';
      messages.appendChild(typing);
    }

    if (pinned) {
      messages.scrollTop = messages.scrollHeight;
    }
  }

  function buildChatBubble(text, role, failed) {
    const bubble = document.createElement('div');
    bubble.className = `dograh-chat-bubble dograh-chat-bubble--${role}${failed ? ' dograh-chat-bubble--failed' : ''}`;
    if (role === 'user') {
      bubble.style.backgroundColor = state.config.buttonColor;
    }
    bubble.textContent = text; // XSS boundary: never innerHTML for message text
    return bubble;
  }

  function renderChatBanner() {
    const banner = state.chatEls.banner;
    banner.textContent = '';

    let text = state.chat.banner;
    if (state.chat.status === 'expired') {
      text = 'This chat session has expired.';
    }
    if (!text) {
      banner.style.display = 'none';
      return;
    }

    banner.style.display = 'flex';
    banner.className = `dograh-chat-banner${state.chat.status === 'ended' ? ' dograh-chat-banner--info' : ''}`;

    const span = document.createElement('span');
    span.textContent = text;
    banner.appendChild(span);

    if (state.chat.status === 'expired' || state.chat.status === 'ended') {
      const restart = document.createElement('button');
      restart.type = 'button';
      restart.className = 'dograh-chat-restart';
      restart.textContent = 'Start new chat';
      restart.onclick = startNewChatSession;
      banner.appendChild(restart);
    } else if (state.chat.status === 'error') {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'dograh-chat-restart';
      retry.textContent = 'Retry';
      retry.onclick = () => startChatSession();
      banner.appendChild(retry);
    }
  }

  function updateChatComposerState() {
    const input = state.chatEls.input;
    const sendBtn = state.chatEls.sendBtn;
    const canType = state.chat.status === 'ready' && !state.chat.ending && !state.chat.confirmingEnd;
    input.disabled = !canType && state.chat.status !== 'idle';
    sendBtn.disabled = !canType;
    if (document.activeElement !== input) {
      input.value = state.chat.draft || '';
    }
  }

  function updateChatEndConfirmationState() {
    const showConfirmation = state.chat.confirmingEnd
      && state.chat.status === 'ready'
      && !state.chat.ending;
    state.chatEls.endConfirmation.style.display = showConfirmation ? 'flex' : 'none';
    state.chatEls.endBtn.setAttribute('aria-expanded', showConfirmation ? 'true' : 'false');
  }

  function updateChatEndButtonState() {
    const endBtn = state.chatEls.endBtn;
    const hasActiveSession = Boolean(
      state.sessionToken && state.chat.status !== 'ended' && state.chat.status !== 'expired'
    );
    endBtn.style.display = hasActiveSession ? 'inline-flex' : 'none';
    endBtn.disabled = state.chat.ending || state.chat.confirmingEnd || state.chat.status !== 'ready';
    endBtn.textContent = state.chat.ending ? 'Ending…' : 'End chat';
  }

  // Public API
  window.DograhWidget = {
    // Core methods. In chat mode start() opens the chat, stop() only hides a
    // floating panel, and end() completes the server-side session.
    init: init,
    start: startWidget,
    stop: (...args) => (isChatWidget() ? closeChatPanel() : stopCall(...args)),
    end: endWidget,
    retry: retryCall,

    // Visitor context (voice and chat, every embed mode). Merges into whatever
    // data-dograh-context supplied; applies to the next conversation started.
    setContext: setContextVariables,
    getContext: () => ({ ...(state.config.contextVariables || {}) }),

    // Floating widget specific
    open: openWidget,
    close: closeWidget,

    // Chat API (widgetType === 'chat')
    startChat: startChat,
    endChat: endChatSession,
    sendMessage: sendChatMessage,
    getMessages: () => state.chat.turns.slice(),
    isChatMode: () => isChatWidget(),
    onMessage: (callback) => { state.callbacks.onMessage = callback; },
    onChatStateChange: (callback) => { state.callbacks.onChatStateChange = callback; },

    // State and callbacks
    getState: () => state,
    onReady: (callback) => { state.callbacks.onReady = callback; },
    onCallStart: (callback) => { state.callbacks.onCallStart = callback; },
    onCallConnected: (callback) => { state.callbacks.onCallConnected = callback; },
    onCallDisconnected: (callback) => { state.callbacks.onCallDisconnected = callback; },
    onCallEnd: (callback) => { state.callbacks.onCallEnd = callback; },
    onError: (callback) => { state.callbacks.onError = callback; },
    onStatusChange: (callback) => { state.callbacks.onStatusChange = callback; },

    // Check if inline mode
    isInlineMode: () => state.config.embedMode === 'inline',

    // Re-render the inline widget (useful when React component remounts)
    refresh: () => {
      if (state.config.embedMode === 'inline') {
        // Re-render inline widget with current status
        updateInlineStatus(state.connectionStatus);
      }
    },

    // Initialize inline mode manually (for advanced use cases)
    initInline: (options) => {
      if (options.container) {
        state.config.containerId = options.container.id || 'dograh-inline-container';
      }
      state.config.embedMode = 'inline';

      // Set callbacks if provided
      if (options.onReady) state.callbacks.onReady = options.onReady;
      if (options.onCallStart) state.callbacks.onCallStart = options.onCallStart;
      if (options.onCallConnected) state.callbacks.onCallConnected = options.onCallConnected;
      if (options.onCallDisconnected) state.callbacks.onCallDisconnected = options.onCallDisconnected;
      if (options.onCallEnd) state.callbacks.onCallEnd = options.onCallEnd;
      if (options.onError) state.callbacks.onError = options.onError;
      if (options.onStatusChange) state.callbacks.onStatusChange = options.onStatusChange;

      // Initialize
      if (!state.isInitialized) {
        init();
      }
    }
  };

  // Auto-initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
