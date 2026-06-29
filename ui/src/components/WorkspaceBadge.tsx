"use client";

import { useEffect, useState } from "react";

/**
 * Dev-only badge that proves which Conductor workspace this UI belongs to.
 *
 * Renders nothing unless NEXT_PUBLIC_WORKSPACE_NAME is set — which only happens
 * when the UI is launched via .conductor/run-ui.sh. So production builds and a
 * plain `npm run dev` are completely unaffected.
 *
 * The pill color is derived deterministically from the workspace name, so two
 * worktrees running side by side are instantly distinguishable at a glance.
 */
export default function WorkspaceBadge() {
  const workspace = process.env.NEXT_PUBLIC_WORKSPACE_NAME;
  const backend = process.env.NEXT_PUBLIC_BACKEND_URL;
  const [port, setPort] = useState("");

  useEffect(() => {
    setPort(window.location.port);
  }, []);

  if (!workspace) return null;

  // Deterministic hue from the workspace name.
  let hash = 0;
  for (let i = 0; i < workspace.length; i++) {
    hash = (hash * 31 + workspace.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;

  return (
    <div
      className="pointer-events-none fixed bottom-2 left-2 z-[9999] select-none rounded-full px-2.5 py-1 font-mono text-[11px] font-medium text-white shadow-md"
      style={{ backgroundColor: `hsl(${hue} 70% 38% / 0.92)` }}
      title={`Conductor workspace: ${workspace}\nUI port: ${port}\nBackend: ${backend ?? "?"}`}
    >
      ⬡ {workspace}
      {port ? ` :${port}` : ""}
    </div>
  );
}
