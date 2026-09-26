import Image from "next/image";

import { cn } from "@/lib/utils";

// CALM logo artwork. Pass `mark` to render the compact square mark instead
// of the full lockup (e.g. the app sidebar header). Pass `inverse` when
// placing the logo on an always-dark surface (e.g. the auth brand panel).
// `className` sizes the lockup (e.g. "h-8 w-auto" for the wordmark spot, or
// "h-6 w-6" for the square mark).
export function BrandLogo({
  className,
  inverse = false,
  mark = false,
}: {
  className?: string;
  inverse?: boolean;
  mark?: boolean;
}) {
  return (
    <Image
      src="/calm-logo.png"
      alt="CALM"
      width={mark ? 32 : 160}
      height={mark ? 32 : 40}
      priority
      unoptimized
      data-inverse={inverse || undefined}
      className={cn(
        mark ? "size-full object-contain" : "h-8 w-auto object-contain",
        className,
      )}
    />
  );
}
