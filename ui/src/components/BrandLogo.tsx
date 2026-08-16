import { cn } from "@/lib/utils";

// CALMOS wordmark, rendered as styled text (theme-aware via CSS variables,
// so no separate light/dark artwork is needed). Pass `mark` to render the
// square letter mark instead of the full wordmark (e.g. the app sidebar
// header). Pass `inverse` when placing the wordmark on an always-dark surface
// (e.g. the auth brand panel) so it stays legible regardless of the active
// theme. `className` sizes the lockup (e.g. "text-2xl" for the wordmark, or
// "h-6" for the square mark).
export function BrandLogo({
  className,
  inverse = false,
  mark = false,
}: {
  className?: string;
  inverse?: boolean;
  mark?: boolean;
}) {
  if (mark) {
    return (
      <span
        aria-label="CALMOS"
        className={cn(
          "inline-flex aspect-square items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground select-none",
          className,
        )}
      >
        C
      </span>
    );
  }
  return (
    <span
      className={cn(
        "font-bold tracking-tight select-none",
        inverse ? "text-zinc-50" : "text-foreground",
        className,
      )}
    >
      CALMOS
    </span>
  );
}
