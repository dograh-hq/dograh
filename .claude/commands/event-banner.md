---
description: Add an event to the app-wide announcement bar from its registration URL
argument-hint: <event url> [partner name] [partner logo path]
allowed-tools: Bash, Read, Edit, Write, WebFetch, Glob, Grep, AskUserQuestion
---

Add an event to the app-wide announcement bar. Arguments: `$ARGUMENTS`
(first the registration URL, then an optional partner name, then an optional
path to that partner's logo file).

Everything the bar shows lives in `ui/src/config/event-banner.config.ts`. The
component `ui/src/components/EventBanner.tsx` is generic: it renders the first
entry whose `[startsAt, endsAt]` window contains "now". Do not edit the
component for an ordinary event.

The bar is Dograh Cloud only — `ui/src/app/layout.tsx` mounts it behind
`NEXT_PUBLIC_EVENT_BANNER === "1"`, which is blank in `ui/.env.example` and
never set in `ui/Dockerfile`, `deploy/` or the workflows. Self-hosters do not
see it whatever this config says. Do not add the var to any of those.

## 1. Pull the event details

WebFetch the URL for title, date, time and timezone. Luma and Partiful render
those client-side, so the fetch often returns the title and nothing else — say
so plainly rather than inventing a date, and get it from the user.

## 2. Confirm before writing

Use AskUserQuestion to confirm, in one round, with your best guess pre-filled:

- **eyebrow** — the small-caps label before the title. Leave it unset for a
  live event: the component then shows `Live virtual session` (compacted to
  `Live` in the 640-767px band). Set it for anything else — `New experiment`,
  `Workshop`, `Webinar`. A custom eyebrow has no short form and renders as
  given at every width, so keep it to a couple of words.
- **pulse** — whether the pulsing accent dot precedes the eyebrow. Default
  true. Set `false` when the thing is not happening live (a blog post, a
  benchmark, a release), where "happening now" would mislead.
- **title** — the lead sentence, ending in a period. Shown in full at every
  width, so keep it under ~40 characters.
- **titleMobile** — optional shorter title for viewports below 768px, where
  the title shares a line with the register pill at 11px. Measured at 360px
  wide with a 16-character CTA, about 30 characters fit; 33 already run under
  the pill. Only needed when `title` runs long; it is a copy change, not a
  truncation, so it still has to be a complete sentence.
- **subtitle** — optional. Format, hashtag and timing, e.g.
  `#SF Tech Week · Tue Oct 6, 12:30 PM PT`. Dropped below 1024px, so nothing
  load-bearing goes here; leave it out when there is nothing to add.
- **startsAt / endsAt** — ISO 8601 **with offset**. `endsAt` is when the bar
  retires itself; set it to the end of the session, not the start.
- **partner** — name and logo, or none.
- **cta** — defaults to `Register for Free`. The arrow is drawn by the
  component; do not put one in the string.

Derive `id` as `<event>-<YYYY-MM>`, `analyticsId` as a snake_case slug, and
`storageKey` as `dograh_event_banner_<event>_dismissed`. The storage key MUST
be unique per event, or anyone who dismissed the previous banner never sees
this one.

## 3. Place the partner logo

Skip this step when the event has no partner. Otherwise copy it to `ui/public/logos/<partner>.png` and check it is white/light art on
a TRANSPARENT background — the component renders it untouched on the dark
theme and flattens it with `brightness-0` on the light one, and both need the
background to be transparent:

```bash
cd ui && node -e "const s=require('sharp');Promise.all([s('public/logos/<partner>.png').flatten({background:'#1f1f1f'}).resize({width:400}).toFile('/tmp/logo-on-dark.png'),s('public/logos/<partner>.png').flatten({background:'#ffffff'}).resize({width:400}).toFile('/tmp/logo-on-light.png')])"
```

Read both files. On dark the artwork must be legible; on light it is expected
to disappear (that is what `brightness-0` fixes at render time) but must not
show as a solid block — a block means the source has an opaque backdrop, and
no CSS filter can rescue it. Ask for a white-on-transparent variant.

Also check the aspect ratio; the lockup renders Dograh in a 20px box and the
partner in a 13.6px one, so a very wide wordmark will still outweigh the
Dograh mark beside it.

## 4. Add the entry

Prepend it to `events` in `ui/src/config/event-banner.config.ts`, matching the
`EventBannerEvent` type. Leave older entries alone — expired ones are inert.

## 5. Verify

```bash
cd ui && npx eslint src/config/event-banner.config.ts src/components/EventBanner.tsx && npx tsc --noEmit && npm run build
```

Then start dev on 3021 in the background — the env var is required or nothing
renders — and check it yourself before handing it over:

```bash
cd ui && NEXT_PUBLIC_EVENT_BANNER=1 UI_PORT=3021 npm run dev
```

Screenshot desktop (1440×900) and mobile (390×844) and read the images.
Confirm, and say which you confirmed:

- the title renders complete at 360 / 390 / 640 / 768 / 1024 / 1280 / 1440 /
  1920 — it must never truncate, and it must not run under the register pill
  (measure the two bounding boxes; a nowrap title overflows silently rather
  than wrapping);
- if `titleMobile` is set, it is what shows below 768 and `title` from 768 up;
- the eyebrow reads as intended, and the dot is absent when `pulse: false`;
- the register pill clears the dismiss `×`;
- bar height is 41px from sm up / 48px on phones;
- the app header sits directly under the bar and the sidebar starts under it,
  with no phantom page scrollbar (that is `--event-banner-h` plus the
  `[data-event-banner]` rules in `ui/src/app/globals.css` doing their job);
- both themes — the partner logo must be legible on light as well as dark;
- with `endsAt` temporarily set in the past, nothing renders, `<html>` has no
  `data-event-banner` and the header is back at `top: 0` (restore the real
  date afterwards);
- with `NEXT_PUBLIC_EVENT_BANNER` unset, nothing renders.

Give the user the localhost link and the screenshots.

## 6. Ship, once they say so

Do not commit before the user confirms the render.

```bash
git checkout -b feat/event-banner-<id>
git add ui/src/config/event-banner.config.ts   # plus ui/public/logos/<partner>.png if there is one
git commit   # explain the event and the window, not the diff
git push -u origin feat/event-banner-<id>
```
