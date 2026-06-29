import { BRAND } from "@/lib/brand";
import { cn } from "@/lib/utils";

// Reusable brand wordmark. When BRAND.logoUrl is set, renders that image for
// every variant. Otherwise falls back to a neutral text wordmark of BRAND.name
// so no upstream logo is hardcoded. Pass `inverse` to force light-on-dark text
// on an always-dark surface (e.g. the auth brand panel). Pass `mark` to render
// a compact square mark (e.g. the app sidebar header). Height is controlled by
// the caller via className (e.g. "h-7").
export function BrandLogo({
  className,
  inverse = false,
  mark = false,
}: {
  className?: string;
  inverse?: boolean;
  mark?: boolean;
}) {
  if (BRAND.logoUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={BRAND.logoUrl} alt={BRAND.name} className={cn("w-auto select-none", className)} />
    );
  }

  // No logo configured — render a neutral text wordmark of the brand name.
  if (mark) {
    return (
      <span
        className={cn(
          "inline-flex select-none items-center font-semibold uppercase",
          inverse ? "text-zinc-50" : "text-foreground",
          className,
        )}
      >
        {BRAND.name.charAt(0)}
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex select-none items-center font-semibold tracking-tight",
        inverse ? "text-zinc-50" : "text-foreground",
        className,
      )}
    >
      {BRAND.name}
    </span>
  );
}
