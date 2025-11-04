"use client";

import { useEffect } from "react";

declare global {
  interface Window {
    chatwootSDK?: {
      run: (config: {
        websiteToken: string;
        baseUrl: string;
      }) => void;
    };
    chatwootSettings?: {
      position?: "left" | "right";
      type?: "standard" | "expanded_bubble";
      launcherTitle?: string;
    };
  }
}

const CHATWOOT_BASE_URL = process.env.NEXT_PUBLIC_CHATWOOT_URL;
const CHATWOOT_WEBSITE_TOKEN = process.env.NEXT_PUBLIC_CHATWOOT_TOKEN;

export default function ChatwootWidget() {
  useEffect(() => {
    // Don't initialize if environment variables are not set
    if (!CHATWOOT_BASE_URL || !CHATWOOT_WEBSITE_TOKEN) {
      console.warn("Chatwoot not configured: Missing NEXT_PUBLIC_CHATWOOT_URL or NEXT_PUBLIC_CHATWOOT_TOKEN");
      return;
    }

    // Prevent duplicate initialization
    if (window.chatwootSettings) {
      return;
    }

    // Configure Chatwoot widget settings
    window.chatwootSettings = {
      position: "right",
      type: "standard",
      launcherTitle: "Chat with us",
    };

    // Check if script is already loaded
    const existingScript = document.querySelector(
      `script[src="${CHATWOOT_BASE_URL}/packs/js/sdk.js"]`
    );

    if (existingScript) {
      // Script already exists, just initialize if SDK is available
      if (window.chatwootSDK) {
        window.chatwootSDK.run({
          websiteToken: CHATWOOT_WEBSITE_TOKEN,
          baseUrl: CHATWOOT_BASE_URL,
        });
      }
      return;
    }

    // Create and inject the Chatwoot SDK script
    const script = document.createElement("script");
    script.src = `${CHATWOOT_BASE_URL}/packs/js/sdk.js`;
    script.async = true;
    script.defer = true;
    script.onload = () => {
      if (window.chatwootSDK) {
        window.chatwootSDK.run({
          websiteToken: CHATWOOT_WEBSITE_TOKEN,
          baseUrl: CHATWOOT_BASE_URL,
        });
      }
    };

    document.body.appendChild(script);
  }, []);

  return null;
}
