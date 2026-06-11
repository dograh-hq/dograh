import { BRAND } from "@/lib/brand";

export default function Footer() {
  // Legal links are white-label config; hide the footer entirely when the
  // deployment owner hasn't configured any.
  if (!BRAND.privacyUrl && !BRAND.termsUrl) {
    return null;
  }

  return (
    <footer className="fixed bottom-0 left-0 right-0 bg-background border-t border-border py-4 px-6">
      <div className="flex justify-center items-center gap-6 text-sm text-muted-foreground">
        {BRAND.privacyUrl && (
          <a
            href={BRAND.privacyUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground transition-colors"
          >
            Privacy Policy
          </a>
        )}
        {BRAND.privacyUrl && BRAND.termsUrl && <span className="text-border">|</span>}
        {BRAND.termsUrl && (
          <a
            href={BRAND.termsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground transition-colors"
          >
            Terms of Service
          </a>
        )}
      </div>
    </footer>
  );
}
