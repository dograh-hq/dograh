import type { ReactNode } from "react";

// Client-accessible: orgs self-serve their integrations (WhatsApp, CRM, Credits,
// Phone Numbers, etc.) here. Model configuration stays gated on its own page.
export default function SettingsLayout({ children }: { children: ReactNode }) {
    return <>{children}</>;
}
