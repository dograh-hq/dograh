import "./globals.css";

import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";

import ChatwootWidget from "@/components/ChatwootWidget";
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
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <AuthProvider>
          <Suspense fallback={<SpinLoader />}>
            <UserConfigProvider>
              <OnboardingProvider>
                <PostHogIdentify />
                {children}
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
