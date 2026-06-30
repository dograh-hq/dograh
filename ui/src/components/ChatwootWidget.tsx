"use client";

import { usePathname } from "next/navigation";
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
    $chatwoot?: {
      toggleBubbleVisibility?: (visibility: "hide" | "show") => void;
      toggle?: (state?: "open" | "close") => void;
    };
  }
}

const CHATWOOT_BASE_URL = process.env.NEXT_PUBLIC_CHATWOOT_URL;
const CHATWOOT_WEBSITE_TOKEN = process.env.NEXT_PUBLIC_CHATWOOT_TOKEN;

// Hide the support bubble only on the workflow builder (/workflow/<id> and its
// sub-routes), where the in-app chat tester occupies the same bottom-right
// corner. It stays visible everywhere else, including the /workflow list and
// /workflow/create.
const isBuilderPath = (pathname: string) =>
  /^\/workflow\/(?!create(?:$|\/))[^/]+(?:\/.*)?$/.test(pathname);

export default function ChatwootWidget() {
  const pathname = usePathname();

  // Load the Chatwoot SDK exactly once for the lifetime of the app.
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
      window.chatwootSDK?.run({
        websiteToken: CHATWOOT_WEBSITE_TOKEN,
        baseUrl: CHATWOOT_BASE_URL,
      });
      return;
    }

    // Create and inject the Chatwoot SDK script
    const script = document.createElement("script");
    script.src = `${CHATWOOT_BASE_URL}/packs/js/sdk.js`;
    script.async = true;
    script.defer = true;
    script.onload = () => {
      window.chatwootSDK?.run({
        websiteToken: CHATWOOT_WEBSITE_TOKEN,
        baseUrl: CHATWOOT_BASE_URL,
      });
    };

    document.body.appendChild(script);
  }, []);

  // Show/hide the bubble per route using Chatwoot's native API. We never tear
  // down and recreate the SDK — doing so left the bubble permanently hidden
  // once a user had visited the builder.
  useEffect(() => {
    const applyVisibility = () => {
      if (!window.$chatwoot) return;
      if (isBuilderPath(pathname)) {
        window.$chatwoot.toggle?.("close");
        window.$chatwoot.toggleBubbleVisibility?.("hide");
      } else {
        window.$chatwoot.toggleBubbleVisibility?.("show");
      }
    };

    // Apply immediately only once the bubble holder is actually in the DOM.
    // `window.$chatwoot` exists synchronously after run(), but `.woot--bubble-holder`
    // is appended later when the widget iframe loads, and toggleBubbleVisibility()
    // dereferences it with no null check. When it's absent, fall through to
    // `chatwoot:ready`, which the SDK fires once the holder exists.
    if (window.$chatwoot && document.querySelector(".woot--bubble-holder")) {
      applyVisibility();
      return;
    }

    window.addEventListener("chatwoot:ready", applyVisibility, { once: true });
    return () => window.removeEventListener("chatwoot:ready", applyVisibility);
  }, [pathname]);

  return null;
}
