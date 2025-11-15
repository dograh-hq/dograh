/**
 * Dograh Voice Widget
 * Embeddable voice call widget for Dograh workflows
 * Version: 1.0.0
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
    connectionStatus: 'idle', // idle, connecting, connected, failed
    audioElement: null,
    callbacks: {
      onReady: null,
      onCallStart: null,
      onCallEnd: null,
      onError: null,
      onStatusChange: null
    }
  };

  /**
   * Initialize the widget
   */
  async function init() {
    if (state.isInitialized) return;

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
      // Allow data attributes to override fetched config
      contextVariables: parseContextVariables(script.getAttribute('data-dograh-context'))
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

      // Merge fetched configuration with defaults
      state.config = {
        ...state.config,
        workflowId: configData.workflow_id,
        embedMode: configData.settings?.embedMode || 'floating',
        containerId: configData.settings?.containerId || 'dograh-inline-container',
        position: configData.position || DEFAULT_CONFIG.position,
        autoStart: configData.auto_start || false
      };
    } catch (error) {
      console.error('Dograh Widget: Failed to fetch configuration', error);
      return;
    }

    state.isInitialized = true;

    // Load styles
    injectStyles();

    // Create widget UI based on mode
    if (state.config.embedMode === 'inline') {
      createInlineWidget();
    } else {
      createFloatingWidget();
    }

    // Trigger ready callback
    if (state.callbacks.onReady) {
      state.callbacks.onReady();
    }

    // Auto-start if configured
    if (state.config.autoStart) {
      setTimeout(() => startCall(), 1000);
    }
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

      .dograh-widget-button {
        color: white;
        border: none;
        border-radius: 50%;
        width: 60px;
        height: 60px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        transition: all 0.3s ease;
      }

      .dograh-widget-button:hover {
        transform: scale(1.1);
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25);
      }

      .dograh-widget-button:active {
        transform: scale(0.95);
      }

      /* Green button for idle/ready state */
      .dograh-widget-button-idle {
        background: #10b981;
      }

      .dograh-widget-button-idle:hover {
        background: #059669;
      }

      /* Orange button for connecting state */
      .dograh-widget-button-connecting {
        background: #f59e0b;
        animation: pulse 2s infinite;
      }

      /* Red button for connected state (to end call) */
      .dograh-widget-button-connected {
        background: #ef4444;
      }

      .dograh-widget-button-connected:hover {
        background: #dc2626;
      }

      /* Red button for failed state */
      .dograh-widget-button-failed {
        background: #ef4444;
        opacity: 0.8;
      }

      @keyframes pulse {
        0%, 100% {
          opacity: 1;
        }
        50% {
          opacity: 0.6;
        }
      }

    `;

    const styleSheet = document.createElement('style');
    styleSheet.id = 'dograh-widget-styles';
    styleSheet.textContent = styles;
    document.head.appendChild(styleSheet);
  }

  /**
   * Create floating widget UI (simplified - no modal)
   */
  function createFloatingWidget() {
    // Create container
    const container = document.createElement('div');
    container.className = `dograh-widget-container ${state.config.position}`;
    container.id = 'dograh-widget';

    // Create button (green to start, red to end)
    const button = document.createElement('button');
    button.className = 'dograh-widget-button dograh-widget-button-idle';
    button.id = 'dograh-widget-button';
    button.innerHTML = `
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
      </svg>
    `;
    button.onclick = toggleCall;

    // Create hidden audio element
    const audio = document.createElement('audio');
    audio.id = 'dograh-widget-audio';
    audio.autoplay = true;
    audio.style.display = 'none';

    // Append elements
    container.appendChild(button);
    container.appendChild(audio);
    document.body.appendChild(container);

    // Store audio element reference
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

  /**
   * Update floating widget button appearance
   */
  function updateFloatingButton(status) {
    const button = document.getElementById('dograh-widget-button');
    if (!button) return;

    // Remove all status classes
    button.classList.remove('dograh-widget-button-idle', 'dograh-widget-button-connecting', 'dograh-widget-button-connected', 'dograh-widget-button-failed');

    // Add current status class
    button.classList.add(`dograh-widget-button-${status}`);

    // Update title attribute for tooltip
    const titles = {
      idle: 'Start Call',
      connecting: 'Connecting...',
      connected: 'End Call',
      failed: 'Retry Call'
    };
    button.title = titles[status] || 'Voice Call';
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
      idle: 'Click to start voice conversation',
      connecting: 'Please wait while we establish connection',
      connected: 'You can speak now',
      failed: 'Please check your microphone and try again'
    }[status];

    // Simple button design: green to start, red to end
    let buttonHTML = '';
    if (status === 'idle' || status === 'failed') {
      // Green button to start
      buttonHTML = `
        <button class="dograh-inline-btn dograh-inline-btn-start" id="dograh-inline-start-btn">
          ${status === 'failed' ? 'Retry' : 'Start Call'}
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
    updateStatus('connecting', 'Connecting...', 'Please wait while we establish the connection');

    // Trigger call start callback
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
  }

  /**
   * Create WebRTC peer connection
   */
  function createWebRTCConnection() {
    const config = {
      iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }]
    };

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
    state.pc.oniceconnectionstatechange = () => {
      console.log('ICE connection state:', state.pc.iceConnectionState);

      if (state.pc.iceConnectionState === 'connected' || state.pc.iceConnectionState === 'completed') {
        updateStatus('connected', 'Connected', 'Your voice call is now active');
      } else if (state.pc.iceConnectionState === 'failed' || state.pc.iceConnectionState === 'disconnected') {
        updateStatus('failed', 'Connection lost', 'The call has been disconnected');
        stopCall();
      }
    };

    // Handle ICE candidates for trickling
    state.pc.onicecandidate = (event) => {
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
    };
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

      state.ws.onclose = () => {
        console.log('WebSocket closed');
        if (state.connectionStatus === 'connected') {
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
        workflow_run_id: parseInt(state.workflowRunId),
        call_context_vars: state.config.contextVariables || {}
      }
    };

    state.ws.send(JSON.stringify(message));
    console.log('Sent offer via WebSocket');
  }

  /**
   * Stop voice call
   */
  function stopCall() {
    updateStatus('idle', 'Call ended', 'Click below to start a new call');

    // Trigger call end callback
    if (state.callbacks.onCallEnd) {
      state.callbacks.onCallEnd();
    }

    // Close WebSocket
    if (state.ws) {
      state.ws.close();
      state.ws = null;
    }

    // Stop media tracks
    if (state.stream) {
      state.stream.getTracks().forEach(track => track.stop());
      state.stream = null;
    }

    // Close peer connection
    if (state.pc) {
      state.pc.close();
      state.pc = null;
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

  // Public API
  window.DograhWidget = {
    // Core methods
    init: init,
    start: startCall,
    stop: stopCall,
    end: stopCall, // Alias for stop
    retry: retryCall,

    // Floating widget specific
    open: openWidget,
    close: closeWidget,

    // State and callbacks
    getState: () => state,
    onReady: (callback) => { state.callbacks.onReady = callback; },
    onCallStart: (callback) => { state.callbacks.onCallStart = callback; },
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
