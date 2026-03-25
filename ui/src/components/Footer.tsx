export default function Footer() {
  return (
    <footer className="fixed bottom-0 left-0 right-0 bg-background border-t border-border py-4 px-6">
      <div className="flex justify-center items-center gap-6 text-sm text-muted-foreground">
        <a
          href="https://zoren-voice.ashtra.ai/privacy-policy"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-foreground transition-colors"
        >
          Privacy Policy
        </a>
        <span className="text-border">|</span>
        <a
          href="https://zoren-voice.ashtra.ai/terms-of-service"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-foreground transition-colors"
        >
          Terms of Service
        </a>
      </div>
    </footer>
  );
}
