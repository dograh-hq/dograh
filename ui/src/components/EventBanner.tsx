"use client";

import posthog from "posthog-js";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { BrandLogo } from "@/components/BrandLogo";

// Site-wide event announcement bar, ported from the dograh.com landing page.
// Sits ABOVE the app chrome in the root layout and is sticky at top-0, so the
// header sticks directly beneath it and the fixed sidebar starts beneath it.
//
// Why sticky rather than a bar that scrolls away: the app's chrome is a fixed
// sidebar (inset-y-0) plus a sticky header, both of which assume they own the
// top of the viewport. A bar in normal flow would push them down only until it
// scrolled off, so both would have to track scroll position to stay correct.
// Sticky makes the chrome offset a CONSTANT while the bar is up, which is what
// --event-banner-h publishes: this component measures itself and writes the
// value (plus a data-event-banner flag) onto <html>, and the consumers in
// globals.css / AppLayout offset by it. Consumers spell it
// `var(--event-banner-h,0px)`, so when the bar is absent or dismissed every one
// of those layouts is exactly what it was before this file existed.
//
// Rendered ONLY when NEXT_PUBLIC_EVENT_BANNER === "1" (see app/layout.tsx), so
// self-hosted/OSS installs never see it.
//
// To run a different event, change EVENT and nothing else. There is
// deliberately NO end-date gating: the bar stays up until this component is
// unmounted (drop NEXT_PUBLIC_EVENT_BANNER, or delete the mount) after the
// event.
const EVENT = {
  // One string for every breakpoint: it fits unclipped down to 360px, so there
  // is no phone-specific short form to keep in sync.
  title: "SOTA Multilingual Self-Hosted Voice agents",
  url: "https://luma.com/q956wodv",
  storageKey: "dograh_event_banner_cartesia_dismissed",
  // Inlined rather than added to constants/posthog-events.ts: the banner is a
  // temporary, cloud-only surface and the constant would outlive the event.
  analyticsId: "cartesia_sota_voice_agents",
};

// Accent wash over the card surface, so the bar reads as its own band above the
// chrome without introducing a colour of its own: it is color-mix'd from the
// app's --cta accent and re-themes with it. Inline rather than a Tailwind
// arbitrary value because the commas and spaces inside color-mix() do not
// survive class-name escaping legibly.
const BANNER_WASH =
  "linear-gradient(90deg, color-mix(in oklab, var(--cta) 10%, transparent), color-mix(in oklab, var(--cta) 4%, transparent) 55%, color-mix(in oklab, var(--cta) 9%, transparent))";

// Whether the bar was dismissed is a client-only fact, so the server must
// render nothing and the client must decide after hydration.
// useSyncExternalStore is the mismatch-free way to express that: the server
// snapshot is a hard false, the client snapshot is the real answer, and React
// reconciles the difference itself instead of a setState-in-effect that would
// flash the bar at people who dismissed it. getSnapshot returns a boolean, so
// it is stable across calls and cannot loop.
const subscribeNever = () => () => {};
const serverSnapshot = () => false;

function readEligible() {
  try {
    return localStorage.getItem(EVENT.storageKey) !== "1";
  } catch {
    // Storage blocked (Safari private mode, cookie settings) — show the bar.
    return true;
  }
}

export function EventBanner() {
  const eligible = useSyncExternalStore(subscribeNever, readEligible, serverSnapshot);
  // Dismissal is a same-session re-render; the localStorage write below is what
  // makes it stick across page views.
  const [dismissed, setDismissed] = useState(false);
  const visible = eligible && !dismissed;
  const ref = useRef<HTMLDivElement>(null);

  // Publish the bar's real height to the rest of the page. Measured rather
  // than hardcoded because it differs by breakpoint and would drift the moment
  // the copy or padding changes.
  useEffect(() => {
    const el = ref.current;
    if (!visible || !el) return;
    const root = document.documentElement;
    const publish = () =>
      root.style.setProperty("--event-banner-h", `${el.offsetHeight}px`);
    publish();
    root.setAttribute("data-event-banner", "1");
    const ro = new ResizeObserver(publish);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty("--event-banner-h");
      root.removeAttribute("data-event-banner");
    };
  }, [visible]);

  if (!visible) return null;

  return (
    <div
      ref={ref}
      style={{ backgroundImage: BANNER_WASH }}
      className="sticky top-0 z-50 w-full border-b border-cta/25 bg-card"
    >
      {/* The dismiss button is a SIBLING of this link, not a descendant: a
          <button> inside an <a> is invalid HTML and clicking it would navigate
          anyway. The right padding here reserves the strip it occupies. */}
      <a
        href={EVENT.url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() =>
          posthog.capture("event_banner_clicked", { event: EVENT.analyticsId })
        }
        aria-label={`Register for free: ${EVENT.title} — live virtual session (opens in a new tab)`}
        className="group mx-auto flex h-14 max-w-7xl items-center gap-2.5 pr-9 pl-4 transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-cta sm:h-12 sm:gap-4 sm:pr-12 sm:pl-6 md:justify-center md:gap-5 lg:pr-14 lg:pl-8"
      >
        {/* Partnership lockup — shown from lg only. At md the row (lockup +
            eyebrow + title + pill) needs ~875px, which truncated the title on a
            768px tablet; the announcement itself outranks the co-brand when
            only one of them fits. The file is cartesia-lockup.svg, not
            cartesia.svg: an earlier revision shipped different art under that
            name, and a browser that cached it keeps serving the old drawing
            forever at the same URL. A new filename is the cache bust.

            The Cartesia asset is dark-on-TRANSPARENT, so it needs no filter on
            the light theme and `dark:invert` (after brightness-0 normalises it
            to pure black) turns it white on the dark one. BrandLogo does the
            same swap for the Dograh wordmark on its own.

            Dograh sits at 15px and Cartesia at 10.5px (15px less 30%). The
            heights are deliberately unequal: the Cartesia lockup is a ~7:1 box
            whose caps occupy only ~70% of its height, so at matched heights it
            ran much wider and read heavier than the Dograh wordmark beside it.
            The two now balance on width and ink rather than on box height. */}
        <div className="hidden shrink-0 items-center gap-3 lg:flex">
          <BrandLogo className="h-[15px]" />
          <span aria-hidden className="font-mono text-[13px] leading-none text-muted-foreground">
            ×
          </span>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/logos/cartesia-lockup.svg"
            alt="Cartesia"
            className="h-[10.5px] w-auto brightness-0 dark:invert"
          />
        </div>

        {/* Divider between the lockup and the announcement itself. */}
        <span aria-hidden className="hidden h-4 w-px shrink-0 bg-border lg:block" />

        <div className="flex min-w-0 flex-1 flex-col items-start gap-0.5 sm:flex-row sm:items-center sm:gap-3 md:flex-initial">
          <span className="flex shrink-0 items-center gap-2">
            <span
              aria-hidden
              className="signal-pulse block h-1.5 w-1.5 shrink-0 rounded-full bg-cta"
            />
            {/* Compacts to "Live" only in the 640-767px band, where the bar is
                a single line but still narrow enough that the full label costs
                the title characters. Below 640 the bar is two lines and has the
                room. */}
            <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-cta sm:text-[10px]">
              <span className="hidden sm:inline md:hidden">Live</span>
              <span className="sm:hidden md:inline">Live virtual session</span>
            </span>
          </span>
          <span aria-hidden className="hidden h-3 w-px shrink-0 bg-border sm:block" />
          <span className="max-w-full truncate text-[11px] text-foreground/85 underline-offset-4 group-hover:underline sm:text-[13px]">
            {EVENT.title}
          </span>
        </div>

        {/* Ghost pill, not a solid fill: the app chrome's own solid CTAs sit
            directly below it, and two filled accent pills stacked 60px apart
            read as one control repeated. Inverting on hover keeps it the
            loudest thing in the bar at the moment of intent. group-hover, not
            hover, because the whole bar is the link. */}
        <span className="ml-auto inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-cta px-2.5 py-1 text-[11px] font-medium text-cta transition-colors duration-150 group-hover:bg-cta group-hover:text-cta-foreground sm:px-3 sm:py-1.5 sm:text-[12px] md:ml-0">
          <span>
            Register<span className="hidden sm:inline"> for Free</span>
          </span>
          <span aria-hidden>→</span>
        </span>
      </a>

      <button
        type="button"
        onClick={() => {
          try {
            localStorage.setItem(EVENT.storageKey, "1");
          } catch {
            // Storage blocked — at least hide it for this page view.
          }
          setDismissed(true);
        }}
        aria-label="Dismiss event announcement"
        className="absolute top-1/2 right-1.5 -translate-y-1/2 cursor-pointer rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cta sm:right-3"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path
            d="M1.5 1.5l9 9M10.5 1.5l-9 9"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>
    </div>
  );
}
