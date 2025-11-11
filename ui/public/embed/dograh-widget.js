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
    theme: 'light',
    buttonText: 'Start Voice Call',
    buttonColor: '#3B82F6',
    size: 'medium',
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
    audioElement: null
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
      apiBaseUrl = apiEndpoint.replace(/\/+$/, ''); // Remove trailing slashes
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
      const configResponse = await fetch(`${state.config.apiBaseUrl}/public/embed/config/${token}`, {
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
        position: configData.position || DEFAULT_CONFIG.position,
        theme: configData.theme || DEFAULT_CONFIG.theme,
        buttonText: configData.button_text || DEFAULT_CONFIG.buttonText,
        buttonColor: configData.button_color || DEFAULT_CONFIG.buttonColor,
        size: configData.size || DEFAULT_CONFIG.size,
        autoStart: configData.auto_start || false
      };

    } catch (error) {
      console.error('Dograh Widget: Failed to fetch configuration', error);
      return;
    }

    state.isInitialized = true;

    // Load styles
    injectStyles();

    // Create widget UI
    createWidget();

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
        background: var(--button-color, #3B82F6);
        color: white;
        border: none;
        border-radius: 50px;
        padding: 12px 24px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 16px;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        transition: all 0.2s ease;
      }

      .dograh-widget-button:hover {
        transform: scale(1.05);
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.2);
      }

      .dograh-widget-button.small {
        padding: 8px 16px;
        font-size: 14px;
      }

      .dograh-widget-button.large {
        padding: 16px 32px;
        font-size: 18px;
      }

      .dograh-widget-button.fab {
        width: 56px;
        height: 56px;
        padding: 0;
        justify-content: center;
      }

      .dograh-widget-modal {
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: white;
        border-radius: 12px;
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
        padding: 24px;
        min-width: 320px;
        max-width: 90vw;
        display: none;
        z-index: 1000000;
      }

      .dograh-widget-modal.open {
        display: block;
      }

      .dograh-widget-modal.dark {
        background: #1f2937;
        color: white;
      }

      .dograh-widget-modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 20px;
      }

      .dograh-widget-modal-title {
        font-size: 20px;
        font-weight: 600;
        margin: 0;
      }

      .dograh-widget-close {
        background: none;
        border: none;
        font-size: 24px;
        cursor: pointer;
        color: #6b7280;
        padding: 0;
        width: 32px;
        height: 32px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 4px;
        transition: background 0.2s;
      }

      .dograh-widget-close:hover {
        background: #f3f4f6;
      }

      .dark .dograh-widget-close {
        color: #9ca3af;
      }

      .dark .dograh-widget-close:hover {
        background: #374151;
      }

      .dograh-widget-status {
        text-align: center;
        padding: 40px 20px;
      }

      .dograh-widget-status-icon {
        width: 48px;
        height: 48px;
        margin: 0 auto 16px;
        animation: pulse 2s infinite;
      }

      .dograh-widget-status-text {
        font-size: 16px;
        color: #4b5563;
        margin: 0 0 8px;
      }

      .dark .dograh-widget-status-text {
        color: #d1d5db;
      }

      .dograh-widget-status-subtext {
        font-size: 14px;
        color: #9ca3af;
        margin: 0;
      }

      .dograh-widget-controls {
        display: flex;
        gap: 12px;
        justify-content: center;
        margin-top: 24px;
      }

      .dograh-widget-control-btn {
        padding: 10px 20px;
        border-radius: 8px;
        border: none;
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
        transition: all 0.2s;
      }

      .dograh-widget-control-btn.primary {
        background: #ef4444;
        color: white;
      }

      .dograh-widget-control-btn.primary:hover {
        background: #dc2626;
      }

      .dograh-widget-control-btn.secondary {
        background: #f3f4f6;
        color: #374151;
      }

      .dograh-widget-control-btn.secondary:hover {
        background: #e5e7eb;
      }

      .dark .dograh-widget-control-btn.secondary {
        background: #374151;
        color: #d1d5db;
      }

      .dark .dograh-widget-control-btn.secondary:hover {
        background: #4b5563;
      }

      .dograh-widget-error {
        background: #fee;
        color: #c00;
        padding: 12px;
        border-radius: 8px;
        margin-top: 16px;
        font-size: 14px;
      }

      .dark .dograh-widget-error {
        background: #7f1d1d;
        color: #fca5a5;
      }

      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
      }

      @keyframes spin {
        to { transform: rotate(360deg); }
      }

      .dograh-widget-spinner {
        animation: spin 1s linear infinite;
      }

      .dograh-widget-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        z-index: 999998;
        display: none;
      }

      .dograh-widget-overlay.open {
        display: block;
      }
    `;

    const styleSheet = document.createElement('style');
    styleSheet.id = 'dograh-widget-styles';
    styleSheet.textContent = styles;
    document.head.appendChild(styleSheet);
  }

  /**
   * Create widget UI
   */
  function createWidget() {
    // Create container
    const container = document.createElement('div');
    container.className = `dograh-widget-container ${state.config.position}`;
    container.id = 'dograh-widget';

    // Create button
    const button = document.createElement('button');
    button.className = `dograh-widget-button ${state.config.size}`;
    button.style.setProperty('--button-color', state.config.buttonColor);
    button.innerHTML = `
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
      </svg>
      <span>${state.config.buttonText}</span>
    `;
    button.onclick = toggleWidget;

    // Create overlay
    const overlay = document.createElement('div');
    overlay.className = 'dograh-widget-overlay';
    overlay.id = 'dograh-widget-overlay';
    overlay.onclick = (e) => {
      // Only close if clicking the overlay itself, not the modal
      if (e.target === overlay) {
        closeWidget();
      }
    };

    // Create modal
    const modal = document.createElement('div');
    modal.className = `dograh-widget-modal ${state.config.theme}`;
    modal.id = 'dograh-widget-modal';
    modal.innerHTML = `
      <div class="dograh-widget-modal-header">
        <h3 class="dograh-widget-modal-title">Voice Call</h3>
        <button class="dograh-widget-close" id="dograh-widget-close-btn">×</button>
      </div>
      <div class="dograh-widget-modal-content">
        <div class="dograh-widget-status">
          <div class="dograh-widget-status-icon">
            ${getStatusIcon('idle')}
          </div>
          <p class="dograh-widget-status-text">Ready to start</p>
          <p class="dograh-widget-status-subtext">Click below to begin your voice call</p>
        </div>
        <div class="dograh-widget-controls">
          <button class="dograh-widget-control-btn secondary" id="dograh-widget-start-btn">
            Start Call
          </button>
        </div>
      </div>
      <audio id="dograh-widget-audio" autoplay style="display: none;"></audio>
    `;

    // Append elements
    container.appendChild(button);
    document.body.appendChild(container);
    document.body.appendChild(overlay);
    document.body.appendChild(modal);

    // Store audio element reference
    state.audioElement = document.getElementById('dograh-widget-audio');

    // Attach event handlers after DOM is created
    const closeBtn = document.getElementById('dograh-widget-close-btn');
    if (closeBtn) {
      closeBtn.onclick = closeWidget;
    }

    const startBtn = document.getElementById('dograh-widget-start-btn');
    if (startBtn) {
      startBtn.onclick = startCall;
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

    const modal = document.getElementById('dograh-widget-modal');
    if (!modal) return;

    const statusIcon = modal.querySelector('.dograh-widget-status-icon');
    const statusText = modal.querySelector('.dograh-widget-status-text');
    const statusSubtext = modal.querySelector('.dograh-widget-status-subtext');
    const controls = modal.querySelector('.dograh-widget-controls');

    if (statusIcon) statusIcon.innerHTML = getStatusIcon(status);
    if (statusText) statusText.textContent = text;
    if (statusSubtext) statusSubtext.textContent = subtext;

    // Update controls based on status
    if (controls) {
      switch (status) {
        case 'idle':
          controls.innerHTML = `
            <button class="dograh-widget-control-btn secondary" id="dograh-widget-start-btn">
              Start Call
            </button>
          `;
          const startBtn = document.getElementById('dograh-widget-start-btn');
          if (startBtn) startBtn.onclick = startCall;
          break;
        case 'connecting':
          controls.innerHTML = `
            <button class="dograh-widget-control-btn primary" id="dograh-widget-cancel-btn">
              Cancel
            </button>
          `;
          const cancelBtn = document.getElementById('dograh-widget-cancel-btn');
          if (cancelBtn) cancelBtn.onclick = stopCall;
          break;
        case 'connected':
          controls.innerHTML = `
            <button class="dograh-widget-control-btn primary" id="dograh-widget-end-btn">
              End Call
            </button>
          `;
          const endBtn = document.getElementById('dograh-widget-end-btn');
          if (endBtn) endBtn.onclick = stopCall;
          break;
        case 'failed':
          controls.innerHTML = `
            <button class="dograh-widget-control-btn secondary" id="dograh-widget-retry-btn">
              Retry
            </button>
            <button class="dograh-widget-control-btn secondary" id="dograh-widget-close-failed-btn">
              Close
            </button>
          `;
          const retryBtn = document.getElementById('dograh-widget-retry-btn');
          if (retryBtn) retryBtn.onclick = retryCall;
          const closeFailedBtn = document.getElementById('dograh-widget-close-failed-btn');
          if (closeFailedBtn) closeFailedBtn.onclick = closeWidget;
          break;
      }
    }
  }

  /**
   * Toggle widget visibility
   */
  function toggleWidget() {
    if (state.isOpen) {
      closeWidget();
    } else {
      openWidget();
    }
  }

  /**
   * Open widget
   */
  function openWidget() {
    state.isOpen = true;
    const modal = document.getElementById('dograh-widget-modal');
    const overlay = document.getElementById('dograh-widget-overlay');
    if (modal) modal.classList.add('open');
    if (overlay) overlay.classList.add('open');
  }

  /**
   * Close widget
   */
  function closeWidget() {
    state.isOpen = false;
    const modal = document.getElementById('dograh-widget-modal');
    const overlay = document.getElementById('dograh-widget-overlay');
    if (modal) modal.classList.remove('open');
    if (overlay) overlay.classList.remove('open');

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
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      state.stream = stream;

      // Create WebRTC connection
      await createWebRTCConnection();

      // Connect WebSocket
      await connectWebSocket();

      // Start negotiation
      await negotiate();

    } catch (error) {
      console.error('Dograh Widget: Failed to start call', error);
      updateStatus('failed', 'Connection failed', error.message || 'Please check your microphone and try again');
    }
  }

  /**
   * Initialize embed session
   */
  async function initializeEmbedSession() {
    const response = await fetch(`${state.config.apiBaseUrl}/public/embed/init`, {
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
      const wsUrl = `${state.config.apiBaseUrl.replace('http', 'ws')}/ws/public/signaling/${state.sessionToken}`;

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
    init: init,
    open: openWidget,
    close: closeWidget,
    start: startCall,
    stop: stopCall,
    retry: retryCall,
    getState: () => state
  };

  // Auto-initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
