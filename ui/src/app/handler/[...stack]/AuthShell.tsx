// Dark two-column auth shell. LEFT (lg+ only): a brand/value panel with a
// CSS-only audio-waveform motif, proof points, and a Bland-style enterprise CTA
// block at the bottom (passed in as `enterpriseSlot`). RIGHT: a centered
// zinc-900 card that wraps the Stack Auth form (`children`). Mobile collapses to
// the single card column. Palette is the app's blacks/greys with one warm CTA
// accent on the waveform + focus.

import type { ReactNode } from "react";

const PROOF_POINTS = [
  "Open source",
  "7+ telephony providers",
  "Open architecture",
];

export function AuthShell({
  children,
  enterpriseSlot,
}: {
  children: ReactNode;
  enterpriseSlot?: ReactNode;
}) {
  return (
    <div className="grid min-h-screen w-full bg-background lg:grid-cols-[45%_55%]">
      {/* Brand / value panel — hidden on mobile */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border/60 bg-zinc-950 p-10 lg:flex xl:p-14">
        {/* Ambient depth: soft radial glow behind the content */}
        <div
          aria-hidden
          className="pointer-events-none absolute -left-24 top-1/3 size-[28rem] rounded-full opacity-20 blur-3xl"
          style={{ background: "radial-gradient(circle, var(--cta), transparent 70%)" }}
        />

        <div className="relative flex items-center gap-3">
          <div className="auth-waveform" aria-hidden>
            <span /><span /><span /><span /><span /><span /><span /><span />
          </div>
          <span className="text-xl font-semibold tracking-tight text-zinc-50">Dograh</span>
        </div>

        <div className="relative max-w-md space-y-6">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight text-zinc-50 xl:text-4xl">
            Voice AI for outbound calling, built in the open.
          </h1>
          <ul className="flex flex-wrap gap-x-3 gap-y-2 text-sm text-zinc-400">
            {PROOF_POINTS.map((point, i) => (
              <li key={point} className="flex items-center gap-3">
                {i > 0 && <span aria-hidden className="text-zinc-700">·</span>}
                <span>{point}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Enterprise CTA block (Bland-style) */}
        <div className="relative max-w-md space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-5">
          <h2 className="text-sm font-semibold text-zinc-100">
            Need on-prem, data residency &amp; a data perimeter?
          </h2>
          <p className="text-sm text-zinc-400">
            We deploy Dograh inside your environment for regulated and high-scale teams.
          </p>
          {enterpriseSlot}
        </div>
      </aside>

      {/* Form column */}
      <main className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-md space-y-6 rounded-2xl border border-border/60 bg-card p-6 shadow-lg sm:p-8">
          {/* Mobile-only wordmark (brand panel is hidden) */}
          <div className="flex items-center gap-3 lg:hidden">
            <div className="auth-waveform" aria-hidden>
              <span /><span /><span /><span /><span /><span /><span /><span />
            </div>
            <span className="text-lg font-semibold tracking-tight">Dograh</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
