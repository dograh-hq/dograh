"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { loginApiV1AuthLoginPost } from "@/client/sdk.gen";
import { GoogleSignInButton } from "@/components/GoogleSignInButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await loginApiV1AuthLoginPost({ body: { email, password } });
      if (res.error || !res.data) {
        const detail = (res.error as { detail?: string })?.detail;
        toast.error(detail || "Login failed");
        return;
      }
      await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: res.data.token, user: res.data.user }),
      });
      window.location.href = "/after-sign-in";
    } catch {
      toast.error("An error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel (desktop) */}
      <div className="relative hidden flex-col justify-between overflow-hidden bg-[#0a0d1e] p-12 text-white lg:flex">
        <div
          aria-hidden
          className="pointer-events-none absolute -top-24 -left-24 h-[28rem] w-[28rem] rounded-full bg-[radial-gradient(circle,rgba(255,154,31,0.22),transparent_70%)]"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute right-0 bottom-0 h-[22rem] w-[22rem] rounded-full bg-[radial-gradient(circle,rgba(255,210,122,0.10),transparent_70%)]"
        />
        <div className="relative flex items-center gap-3">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand-logo.svg" alt="" width={36} height={36} />
          <span className="text-[17px] font-semibold tracking-[-0.01em]">Auto4You Voice</span>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-display">AI voice agents that actually close.</h2>
          <p className="text-body mt-5 text-white/60">
            Natural Hindi &amp; Hinglish calls at scale — qualify leads, follow up,
            and book meetings, 24/7.
          </p>
        </div>
        <p className="text-small relative text-white/40">© auto4you</p>
      </div>

      {/* Form */}
      <div className="flex items-center justify-center px-6 py-12">
        <div className="stagger w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2 lg:hidden">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand-logo.svg" alt="" width={32} height={32} />
            <span className="text-[17px] font-semibold tracking-[-0.01em]">
              Auto4You Voice
            </span>
          </div>
          <p className="text-eyebrow text-primary">Welcome back</p>
          <h1 className="text-h1 mt-1">Sign in</h1>
          <p className="text-body mt-2 text-muted-foreground">
            Enter your email and password to continue.
          </p>

          <form onSubmit={handleSubmit} className="mt-7 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" variant="brand" className="w-full" disabled={loading}>
              {loading ? "Signing in..." : "Sign in"}
            </Button>
          </form>

          <GoogleSignInButton />

          <p className="mt-6 text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link
              href="/auth/signup"
              className="font-medium text-primary underline-offset-4 hover:underline"
            >
              Sign up
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
