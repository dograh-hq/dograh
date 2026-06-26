'use client';

import { useEffect, useRef } from 'react';

// Cloudflare Turnstile site key is PUBLIC (rendered into the page). Overridable
// per-deployment via env; falls back to the auto4you production widget.
const SITE_KEY =
  process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || '0x4AAAAAADrQMLbIt9_qoG52';
const SCRIPT_SRC =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

type TurnstileApi = {
  render: (
    el: HTMLElement,
    opts: {
      sitekey: string;
      callback: (token: string) => void;
      'expired-callback'?: () => void;
      'error-callback'?: () => void;
      theme?: 'auto' | 'light' | 'dark';
      appearance?: 'always' | 'execute' | 'interaction-only';
    },
  ) => string;
  remove: (id: string) => void;
  reset: (id?: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let scriptPromise: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise<void>((resolve) => {
    const s = document.createElement('script');
    s.src = SCRIPT_SRC;
    s.async = true;
    s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => resolve(); // fail-open in UI; server decides
    document.head.appendChild(s);
  });
  return scriptPromise;
}

/**
 * Renders a Cloudflare Turnstile widget and reports the token via onVerify
 * (null when it expires/errors). Used on login + signup; the server only
 * enforces when TURNSTILE_SECRET_KEY is configured.
 */
export function Turnstile({
  onVerify,
}: {
  onVerify: (token: string | null) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const cb = useRef(onVerify);
  cb.current = onVerify;

  useEffect(() => {
    let cancelled = false;
    loadScript().then(() => {
      if (cancelled || !ref.current || !window.turnstile) return;
      widgetId.current = window.turnstile.render(ref.current, {
        sitekey: SITE_KEY,
        theme: 'auto',
        callback: (token: string) => cb.current(token),
        'expired-callback': () => cb.current(null),
        'error-callback': () => cb.current(null),
      });
    });
    return () => {
      cancelled = true;
      if (widgetId.current && window.turnstile) {
        try {
          window.turnstile.remove(widgetId.current);
        } catch {
          /* widget already gone */
        }
      }
    };
  }, []);

  return <div ref={ref} className="flex min-h-[65px] justify-center" />;
}
