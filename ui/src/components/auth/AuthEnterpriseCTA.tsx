"use client";

// Bland-style enterprise call-to-action rendered inside the auth brand panel.
// Links out to the main marketing site's enterprise intake form rather than the
// in-app modal, since the visitor is not yet authenticated here. Shared by the
// Stack Auth handler and the local/OSS auth pages.

import { Button } from "@/components/ui/button";

export function AuthEnterpriseCTA() {
  return (
    <a
      href="https://dograh.com/contact?intent=enterprise"
      target="_blank"
      rel="noopener noreferrer"
      className="block"
    >
      <Button
        variant="outline"
        className="w-full border-white/20 bg-white/5 text-zinc-100 hover:bg-white/10 hover:text-white"
      >
        Enterprise Enquiry
      </Button>
    </a>
  );
}
