"use client";

import posthog from "posthog-js";
import { useEffect, useRef, useSyncExternalStore } from "react";

import { BrandLogo } from "@/components/BrandLogo";
import { type EventBannerEvent, events } from "@/config/event-banner.config";

// Site-wide event announcement bar, ported from the dograh.com landing page.
// Sits ABOVE the app chrome in the root layout and is sticky at top-0, so the
// header sticks directly beneath it and the fixed sidebar starts beneath it.
//
// WHICH EVENT IT SHOWS is entirely `config/event-banner.config.ts` — this file
// never names one. The first entry whose [startsAt, endsAt] window contains
// "now" and that the visitor has not dismissed wins; no match renders nothing.
// An ordinary event change is a config edit, not a component edit.
//
// Rendered ONLY when NEXT_PUBLIC_EVENT_BANNER === "1" (see app/layout.tsx), so
// self-hosted/OSS installs never see it regardless of the config.
//
// Why sticky rather than a bar that scrolls away: the app's chrome is a fixed
// sidebar (inset-y-0) plus a sticky header, both of which assume they own the
// top of the viewport. A bar in normal flow would push them down only until it
// scrolled off, so both would have to track scroll position to stay correct.
// Sticky makes the chrome offset a CONSTANT while the bar is up, which is what
// --event-banner-h publishes: this component measures itself and writes the
// value (plus a data-event-banner flag) onto <html>, and the consumers in
// globals.css / AppLayout offset by it. Consumers spell it
// `var(--event-banner-h,0px)`, so when no event is live — or the visitor
// dismissed it — every one of those layouts is exactly what it was before this
// file existed.

// Accent wash over the card surface, so the bar reads as its own band above the
// chrome without introducing a colour of its own: it is color-mix'd from the
// app's --cta accent and re-themes with it. Inline rather than a Tailwind
// arbitrary value because the commas and spaces inside color-mix() do not
// survive class-name escaping legibly.
const BANNER_WASH =
  "linear-gradient(90deg, color-mix(in oklab, var(--cta) 10%, transparent), color-mix(in oklab, var(--cta) 4%, transparent) 55%, color-mix(in oklab, var(--cta) 9%, transparent))";

// Eligibility is a client-only fact three times over — the clock, the visitor's
// localStorage, and neither existing during SSR — so the server snapshot is a
// hard empty string and React reconciles the difference itself. That is what
// useSyncExternalStore buys over a setState in an effect, which would flash the
// bar at people who dismissed it.
//
// getSnapshot returns the event ID, a STRING: returning the event object would
// be a fresh reference every call and re-render forever. The listener set is
// what the dismiss button pokes so the snapshot is re-read.
const listeners = new Set<() => void>();
function subscribe(onChange: () => void) {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}
const serverSnapshot = () => "";

function readActiveEventId() {
  const now = Date.now();
  for (const event of events) {
    const start = new Date(event.startsAt).getTime();
    const end = new Date(event.endsAt).getTime();
    // An unparseable date yields NaN and every comparison against NaN is
    // false, so a typo hides that entry rather than pinning a dead banner to
    // the top of every page.
    if (!(now >= start && now <= end)) continue;
    try {
      if (localStorage.getItem(event.storageKey) === "1") continue;
    } catch {
      // Storage blocked (Safari private mode, cookie settings) — show the bar.
    }
    return event.id;
  }
  return "";
}

export function EventBanner() {
  const activeId = useSyncExternalStore(subscribe, readActiveEventId, serverSnapshot);
  const event = events.find((e) => e.id === activeId);
  const ref = useRef<HTMLDivElement>(null);

  // Publish the bar's real height to the rest of the page. Measured rather
  // than hardcoded because it differs by breakpoint and would drift the moment
  // the copy or padding changes.
  useEffect(() => {
    const el = ref.current;
    if (!event || !el) return;
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
  }, [event]);

  if (!event) return null;

  return (
    <div
      ref={ref}
      style={{ backgroundImage: BANNER_WASH }}
      className="sticky top-0 z-50 w-full border-b border-cta/25 bg-card"
    >
      {/* The dismiss button is a SIBLING of this link, not a descendant: a
          <button> inside an <a> is invalid HTML and clicking it would navigate
          anyway. The right padding here reserves the strip it occupies.

          48 on phones, where the bar is two lines, and 41 from sm up. The bar
          measures itself, so --event-banner-h and every layout hanging off it
          follow these numbers without a second edit. */}
      <a
        href={event.url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() =>
          posthog.capture("event_banner_clicked", { event: event.analyticsId })
        }
        aria-label={`${event.cta}: ${event.title} ${event.subtitle} (opens in a new tab)`}
        className="group mx-auto flex h-12 max-w-7xl items-center gap-2.5 pr-9 pl-4 transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-cta sm:h-[41px] sm:gap-4 sm:pr-12 sm:pl-6 md:justify-center md:gap-5 lg:pr-14 lg:pl-8"
      >
        <Lockup partner={event.partner} />

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
          {/* The title never truncates: whitespace-nowrap + shrink-0 means the
              row gives up the subtitle before it gives up a single character of
              the sentence. The subtitle is the flexible half and is hidden
              outright below lg, where it cannot fit whole. */}
          <span className="flex min-w-0 items-baseline gap-1.5 text-[11px] sm:text-[13px]">
            <span className="shrink-0 whitespace-nowrap text-foreground/85 underline-offset-4 group-hover:underline">
              {event.title}
            </span>
            <span className="hidden whitespace-nowrap text-muted-foreground lg:inline">
              · {event.subtitle}
            </span>
          </span>
        </div>

        {/* Ghost pill, not a solid fill: the app chrome's own solid CTAs sit
            directly below it, and two filled accent pills stacked 60px apart
            read as one control repeated. Inverting on hover keeps it the
            loudest thing in the bar at the moment of intent. group-hover, not
            hover, because the whole bar is the link. */}
        <span className="ml-auto inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-cta px-2.5 py-1 text-[11px] font-medium text-cta transition-colors duration-150 group-hover:bg-cta group-hover:text-cta-foreground sm:px-3 sm:py-1.5 sm:text-[12px] md:ml-0">
          <span>{event.cta}</span>
          <span aria-hidden>→</span>
        </span>
      </a>

      <button
        type="button"
        onClick={() => {
          try {
            localStorage.setItem(event.storageKey, "1");
          } catch {
            // Storage blocked — the re-render below still hides it for this
            // page view.
          }
          listeners.forEach((l) => l());
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

// Dograh alone, or [Dograh] × [partner] when the event has one. Shown from xl
// only, which is the narrowest width where the lockup and the subtitle both
// fit: measured at 1024 the row has ~110px of slack, the subtitle needs ~250px
// and the lockup costs ~236px, so one of them has to go and the event's date
// beats the co-brand. Below 768 the lockup would have eaten into the title
// itself.
//
// Dograh sits in a 20px box and the partner in a 13.6px one. Partner wordmarks
// tend to run wider and heavier than the Dograh mark, so equal boxes let the
// guest outweigh the host; if a future partner's art is light or narrow, raise
// that number here.
//
// BrandLogo swaps the Dograh wordmark by theme on its own. Partner artwork is
// white on transparent (see the config's `logo` note), which is right for the
// dark theme but invisible on the light one, so `brightness-0` flattens it to
// black there and `dark:brightness-100` hands the original back. No `invert`:
// on white-on-transparent art it is a no-op in dark and, combined with
// brightness-0, a white slab in light.
function Lockup({ partner }: { partner?: EventBannerEvent["partner"] }) {
  return (
    <>
      <div className="hidden shrink-0 items-center gap-3 xl:flex">
        <BrandLogo className="h-[20px]" />
        {partner && (
          <>
            <span
              aria-hidden
              className="font-mono text-[13px] leading-none text-muted-foreground"
            >
              ×
            </span>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={partner.logo}
              alt={partner.name}
              className="h-[13.6px] w-auto brightness-0 dark:brightness-100"
            />
          </>
        )}
      </div>

      {/* Divider between the lockup and the announcement itself. */}
      <span aria-hidden className="hidden h-4 w-px shrink-0 bg-border xl:block" />
    </>
  );
}
