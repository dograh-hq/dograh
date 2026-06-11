import type { ReactNode } from "react";

import { SuperuserGuard } from "@/components/SuperuserGuard";

export default function ClientsLayout({ children }: { children: ReactNode }) {
    return <SuperuserGuard>{children}</SuperuserGuard>;
}
