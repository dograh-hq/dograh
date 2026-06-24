"use client";

import { useEffect } from "react";

// The backend Google callback redirects here with the JWT + user in the URL
// FRAGMENT (never sent to a server). We hand them to /api/auth/session, which
// sets the same HTTP-only cookie a normal login uses, then land on the dashboard.
export default function GoogleCallbackPage() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("token");
    const userB64 = params.get("user");
    if (!token) {
      window.location.href = "/auth/login?error=google";
      return;
    }
    let user: unknown = { provider: "google" };
    try {
      if (userB64) {
        const std = userB64.replace(/-/g, "+").replace(/_/g, "/");
        user = JSON.parse(atob(std));
      }
    } catch {
      /* fall back to the minimal user */
    }
    fetch("/api/auth/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, user }),
    })
      .then((r) => {
        window.location.href = r.ok ? "/" : "/auth/login?error=google";
      })
      .catch(() => {
        window.location.href = "/auth/login?error=google";
      });
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <p className="text-sm text-muted-foreground">Signing you in…</p>
    </div>
  );
}
