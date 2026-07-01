import { redirect } from "next/navigation";

// The old MPS "Billing" page is retired — Credits & Billing (/credits, PayU) is
// the single billing surface. Redirect any stale links/bookmarks there.
export default function BillingPage() {
  redirect("/credits");
}
