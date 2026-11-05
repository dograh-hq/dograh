// Fork override: upstream links to dograh.com legal pages. Point these at our
// own via NEXT_PUBLIC_PRIVACY_URL / NEXT_PUBLIC_TERMS_URL; render nothing if unset.
export default function Footer() {
  const privacy = process.env.NEXT_PUBLIC_PRIVACY_URL?.trim();
  const terms = process.env.NEXT_PUBLIC_TERMS_URL?.trim();
  if (!privacy && !terms) return null;
  const cls = "hover:text-foreground transition-colors";
  return (
    <footer className="fixed bottom-0 left-0 right-0 bg-background border-t border-border py-4 px-6">
      <div className="flex justify-center items-center gap-6 text-sm text-muted-foreground">
        {privacy && <a href={privacy} target="_blank" rel="noopener noreferrer" className={cls}>Privacy Policy</a>}
        {privacy && terms && <span className="text-border">|</span>}
        {terms && <a href={terms} target="_blank" rel="noopener noreferrer" className={cls}>Terms of Service</a>}
      </div>
    </footer>
  );
}
