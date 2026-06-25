import type { ReactNode } from "react";

// Client-accessible: orgs manage their own API keys (for the public API / n8n).
export default function ApiKeysLayout({ children }: { children: ReactNode }) {
    return <>{children}</>;
}
