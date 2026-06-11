import { cn } from "@/lib/utils";

// Reusable Dograh wordmark. Theme-aware by default: the dark logo shows on light
// surfaces and the light/cream logo shows on dark. Pass `inverse` to force the
// light logo on an always-dark surface (e.g. the auth brand panel). Pass `mark`
// to render the square logo mark instead of the full wordmark (e.g. the app
// sidebar header). Height is controlled by the caller via className (e.g.
// "h-7"); width stays auto so each lockup keeps its aspect ratio.
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
      // eslint-disable-next-line @next/next/no-img-element
      <img src="/dograh-mark.png" alt="Dograh" className={cn("w-auto select-none", className)} />
    );
  }
  if (inverse) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src="/dograh-logo-inverse.png" alt="Dograh" className={cn("w-auto select-none", className)} />
    );
  }
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/dograh-logo.png" alt="Dograh" className={cn("block w-auto select-none dark:hidden", className)} />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/dograh-logo-inverse.png" alt="Dograh" className={cn("hidden w-auto select-none dark:block", className)} />
    </>
  );
}
