// Shared reassurance line shown beneath every lead-form submit. A small,
// consistent trust signal — keeps the promise identical across all forms.

export function FormTrustLine() {
  return (
    <p className="text-center text-xs text-muted-foreground">
      Average response: under 10 minutes during business hours.
    </p>
  );
}
