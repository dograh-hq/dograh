import type { ReactNode } from "react";

import { SuperuserGuard } from "@/components/SuperuserGuard";

export default function SuperadminLayout({ children }: { children: ReactNode }) {
    return <SuperuserGuard>{children}</SuperuserGuard>;
}
