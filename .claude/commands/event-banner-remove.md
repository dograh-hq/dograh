---
description: Retire an event from the app-wide announcement bar
argument-hint: <event id>
allowed-tools: Bash, Read, Edit, Glob, Grep
---

Retire the event with id `$ARGUMENTS` from the app-wide announcement bar.

Delete that entry from `events` in `ui/src/config/event-banner.config.ts`.
That is the whole change — the component, the `AppLayout` sticky offsets and
the `[data-event-banner]` rules in `ui/src/app/globals.css` all stand down on
their own when no event matches, and an empty array is a valid state. Do not
edit `ui/src/components/EventBanner.tsx`, `ui/src/app/layout.tsx` or
`ui/.env.example`; the plumbing is meant to outlive any one event.

Leave the partner logo in `ui/public/logos/` unless the user asks for it gone;
it is a few KB and is likely wanted again.

## Verify

```bash
cd ui && npx tsc --noEmit && npm run build
```

Then start dev on 3021 in the background:

```bash
cd ui && NEXT_PUBLIC_EVENT_BANNER=1 UI_PORT=3021 npm run dev
```

Confirm the bar is gone, `<html>` carries no `data-event-banner` and no
`--event-banner-h`, the app header sits at `top: 0`, the sidebar is full
height and there is no phantom scrollbar.

## Ship, once they say so

```bash
git checkout -b chore/event-banner-remove-<id>
git add ui/src/config/event-banner.config.ts
git commit   # say which event retired and when it ended
git push -u origin chore/event-banner-remove-<id>
```

Note: removing an entry does NOT clear the `storageKey` from anyone's browser.
That is fine — the key is per event, and the next event brings its own.
