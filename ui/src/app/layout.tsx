import "./globals.css";

import type { Metadata } from "next";
import { DM_Sans, Fraunces, Geist_Mono } from "next/font/google";
import { Suspense } from "react";


import AppLayout from "@/components/layout/AppLayout";
import PostHogIdentify from "@/components/PostHogIdentify";
import { SentryErrorBoundary } from "@/components/SentryErrorBoundary";
import SpinLoader from "@/components/SpinLoader";
import { ThemeProvider } from "@/components/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";
import { AppConfigProvider } from "@/context/AppConfigContext";
import { OnboardingProvider } from "@/context/OnboardingContext";
import { OrgConfigProvider } from "@/context/OrgConfigContext";
import { TelephonyConfigWarningsProvider } from "@/context/TelephonyConfigWarningsContext";
import { AuthProvider } from "@/lib/auth";


const dmSans = DM_Sans({
  variable: "--font-dm-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Vani Studio",
  description: "Voice Agent Studio",
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode
}) {

  return (
    <html lang="en" className={`dark ${dmSans.variable} ${fraunces.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <head>
        {/* Inline script to prevent flash of light theme - runs before React hydrates.
            Dark is the locked default: only an explicit stored 'light' opts out. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var theme = localStorage.getItem('theme');
                  if (theme === 'light') {
                    document.documentElement.classList.remove('dark');
                  } else {
                    document.documentElement.classList.add('dark');
                  }
                } catch (e) {
                  document.documentElement.classList.add('dark');
                }
              })();
            `,
          }}
        />
      </head>
      <body
        className={`antialiased ${dmSans.className}`}>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false} disableTransitionOnChange>
          <SentryErrorBoundary>
            <AuthProvider>
              <AppConfigProvider>
                <Suspense fallback={<SpinLoader />}>
                  <OrgConfigProvider>
                    <TelephonyConfigWarningsProvider>
                      <OnboardingProvider>
                        <PostHogIdentify />
                        <AppLayout>
                          {children}
                        </AppLayout>
                        <Toaster />
                      </OnboardingProvider>
                    </TelephonyConfigWarningsProvider>
                  </OrgConfigProvider>
                </Suspense>
              </AppConfigProvider>
            </AuthProvider>
          </SentryErrorBoundary>
        </ThemeProvider>
      </body>
    </html>
  );
}
