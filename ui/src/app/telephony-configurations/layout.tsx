import type { ReactNode } from "react";

import { AdminGuard } from "@/components/AdminGuard";

export default function TelephonyConfigurationsLayout({ children }: { children: ReactNode }) {
    return <AdminGuard>{children}</AdminGuard>;
}
