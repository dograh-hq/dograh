# Dograh Future Plans & UX Improvements

This document tracks future features and UI/UX improvements that need to be built later.

## 1. Custom Hold Music (UI & Backend)
**Goal:** Allow users to upload their own custom hold music.
- **Current State:** The system statically plays `/app/api/assets/transfer_hold_ring_8000.wav` during a transfer call while waiting for the destination to answer.
- **Future Implementation:** 
  - Build UI configuration in the Dashboard for users to upload custom `.wav` files (or select from a library).
  - Update the Dograh Engine to dynamically pull the user's specific hold music file path from the database/S3 when a transfer is initiated.

## 2. Agent Runs View Redesign
**Goal:** Make viewing agent runs much more accessible and detailed from the main interface.
- **Current State:** The agent runs view is not detailed on the main dashboard. To see detailed runs, a user has to go into "Agent Edit" and click the 3-dots menu, which is bad UX and cumbersome.
- **Future Implementation:**
  - Bring the detailed Agent Runs view out to a more prominent, top-level dashboard location (e.g., a dedicated "Logs" or "Runs" tab on the main agent card or sidebar).
  - Redesign the layout so users don't have to drill deep into edit menus just to review past calls.

## 3. DTMF (Keypad) Input Support
**Goal:** Allow the AI Engine to recognize and react to keypad presses (e.g., "Press 1 for Sales") during a live streaming call.
- **Current State:** The system relies entirely on spoken voice for input; DTMF tones are ignored or not fed into the AI context.
- **Future Implementation (Architecture):**
  - **Inbound Capture:** Modify WebSocket receivers (for providers like Twilio) or create new webhook routes (for providers like Plivo) to catch the DTMF digit event.
  - **Redis Routing:** For webhook-based providers, broadcast the received digit via Redis Pub/Sub (`PUBLISH dtmf_events:<CallUUID> "1"`) so the exact server running that call's Pipecat engine can receive it.
  - **Context Injection:** The Pipecat engine intercepts the digit, wraps it in a silent system message (e.g., *"System: The user pressed '1' on their keypad"*), and injects it into the LLM's conversation history.
  - **Action:** The LLM reads the system note instead of listening for speech, and triggers standard tools (like Transfer Call) accordingly.
