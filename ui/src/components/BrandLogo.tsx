import { cn } from "@/lib/utils";
import Image from "next/image";
import sativoiceLogo from "../../public/sativoice-logo.svg";

// Reusable Sativoice wordmark. Theme-aware by default: SVG adapts to currentColor.
// Height is controlled by the caller via className (e.g. "h-7").
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
      src={sativoiceLogo}
      alt="Sativoice"
      className={cn(
        "w-auto select-none",
        inverse && "brightness-0 invert",
        className
      )}
    />
  );
}
