import type { ReactNode } from "react";

import { PlanGuard } from "@/components/PlanGuard";

// API keys are a paid feature (Growth & Scale plans; superuser always).
// Starter / trial orgs are redirected to /settings to upgrade.
export default function ApiKeysLayout({ children }: { children: ReactNode }) {
    return <PlanGuard feature="api">{children}</PlanGuard>;
}
