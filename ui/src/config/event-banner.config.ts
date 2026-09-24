// Event announcement bar — the only file to touch when an event changes.
//
// Cloud only: the bar is mounted in `ui/src/app/layout.tsx` behind
// `NEXT_PUBLIC_EVENT_BANNER === "1"`, so self-hosted/OSS installs never render
// it no matter what this file says.
//
// ADDING THE NEXT EVENT
// 1. Drop the partner logo in `ui/public/logos/` (white or light artwork on a
//    TRANSPARENT background; see `partner.logo` below for why).
// 2. Prepend an entry to `events`. The banner shows the FIRST entry whose
//    window contains "now", so a new event placed on top takes over the moment
//    its `startsAt` passes, without touching the old one.
// 3. Nothing else. No component edit, no layout edit.
//
//    {
//      id: "devday-2026-11",
//      title: "Ship a voice agent in an hour.",
//      subtitle: "Workshop · Thu Nov 12, 10:00 AM PT",
//      url: "https://lu.ma/...",
//      cta: "Save your seat",
//      startsAt: "2026-10-20T00:00:00+05:30",
//      endsAt: "2026-11-12T11:00:00-08:00",
//      partner: { name: "Acme", logo: "/logos/acme.png" },
//      analyticsId: "devday_ship_a_voice_agent",
//      storageKey: "dograh_event_banner_devday_dismissed",
//    }
//
// REMOVING AN EVENT
// Delete its entry, or let `endsAt` pass. An empty array renders no banner,
// `--event-banner-h` and `data-event-banner` are never set, and every layout
// that offsets by them returns to its normal geometry on its own — there is no
// second place to clean up.

export type EventBannerPartner = {
  /** Shown as the logo's alt text. */
  name: string;
  /** Path under `ui/public`, e.g. "/logos/acme.png". Ship WHITE/LIGHT artwork
   *  on a TRANSPARENT background: the component renders it untouched on the
   *  dark theme and flattens it to black with `brightness-0` on the light one,
   *  which only works if the file has no opaque backdrop. A logo on an opaque
   *  white background cannot be rescued by either filter — both drive backdrop
   *  and artwork to one colour and render a solid block. Ask for a white
   *  variant instead. */
  logo: string;
  /** Reserved for attribution. NOT rendered as a link: the whole bar is one
   *  <a> to `url`, and an <a> nested inside an <a> is invalid HTML. */
  href?: string;
};

export type EventBannerEvent = {
  /** Stable slug, also used for the branch name when shipping. */
  id: string;
  /** Lead sentence. Always rendered in full — never truncated. */
  title: string;
  /** Format and timing, e.g. "#SF Tech Week · Tue Oct 6, 12:30 PM PT".
   *  Rendered muted after the title, and dropped on narrow viewports where it
   *  would not fit whole. */
  subtitle: string;
  /** Registration link. The entire bar points here. */
  url: string;
  /** Button label. The arrow is drawn by the component — leave it off unless
   *  you want a different one. */
  cta: string;
  /** ISO 8601 WITH offset. Before this instant the bar does not render. */
  startsAt: string;
  /** ISO 8601 WITH offset. After this instant the bar retires itself. */
  endsAt: string;
  /** Optional co-brand. Present: [Dograh] × [partner]. Absent: [Dograh]. */
  partner?: EventBannerPartner;
  /** PostHog `event` property on the `event_banner_clicked` capture. */
  analyticsId: string;
  /** localStorage key holding this event's dismissal. Must be unique per
   *  event, or a visitor who dismissed the last one never sees this one. */
  storageKey: string;
};

export const events: EventBannerEvent[] = [
  {
    id: "sftechweek-2026-10",
    title: "Own your agentic voice.",
    subtitle: "#SF Tech Week · Tue Oct 6, 12:30 PM PT",
    url: "https://partiful.com/e/hdS7P4Rk0QIJlLT0sShF?c=6rozE9zq",
    cta: "Register for Free",
    startsAt: "2026-09-19T00:00:00+05:30",
    endsAt: "2026-10-06T13:30:00-07:00",
    partner: { name: "Cloudonix", logo: "/logos/cloudonix.png" },
    analyticsId: "sftechweek_own_your_agentic_voice",
    storageKey: "dograh_event_banner_sftechweek_dismissed",
  },
];
