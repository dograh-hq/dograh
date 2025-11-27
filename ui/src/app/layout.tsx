import "./globals.css";

import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";

import ChatwootWidget from "@/components/ChatwootWidget";
import AppLayout from "@/components/layout/AppLayout";
import PostHogIdentify from "@/components/PostHogIdentify";
import SpinLoader from "@/components/SpinLoader";
import { Toaster } from "@/components/ui/sonner";
import { OnboardingProvider } from "@/context/OnboardingContext";
import { UserConfigProvider } from "@/context/UserConfigContext";
import { AuthProvider } from "@/lib/auth";


const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Dograh",
  description: "Open Source Voice Assistant Workflow Builder",
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode
}) {

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Inline script to prevent flash of light theme - runs before React hydrates */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function() {
                try {
                  var theme = localStorage.getItem('theme');
                  if (theme === 'dark' || (!theme && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                    document.documentElement.classList.add('dark');
                  } else {
                    document.documentElement.classList.remove('dark');
                  }
                } catch (e) {}
              })();
            `,
          }}
        />
      </head>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <AuthProvider>
          <Suspense fallback={<SpinLoader />}>
            <UserConfigProvider>
              <OnboardingProvider>
                <PostHogIdentify />
                <AppLayout>
                  {children}
                </AppLayout>
                <Toaster />
                <ChatwootWidget />
              </OnboardingProvider>
            </UserConfigProvider>
          </Suspense>
        </AuthProvider>
      </body>
    </html>
  );
}
