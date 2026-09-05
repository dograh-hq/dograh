"use client";

import { usePathname } from "next/navigation";
import Script from "next/script";
import { useEffect, useRef } from "react";

import { trackMetaPageView } from "@/lib/metaPixel";

/**
 * MetaPixel
 * ---------
 * Loads the Meta (Facebook) Pixel base code and tracks client-side route changes.
 *
 * Rendered in the root layout (`app/layout.tsx`) ONLY when NEXT_PUBLIC_META_PIXEL_ID
 * is set, so it never loads for OSS/self-hosted deployments (which leave the var
 * blank) — mirroring how Google Tag Manager is gated.
 *
 * The base snippet fires the initial PageView. Next.js App Router navigations are
 * client-side, so subsequent PageViews are fired manually on pathname change.
 */
export default function MetaPixel({ pixelId }: { pixelId: string }) {
    const pathname = usePathname();
    // Track the last path we emitted a PageView for. The base snippet already
    // fires PageView for the initial path, so we only fire on subsequent
    // *changes* — this also stays correct under React StrictMode's double-invoke.
    const lastPathRef = useRef<string | null>(null);

    useEffect(() => {
        if (lastPathRef.current === null) {
            lastPathRef.current = pathname; // initial PageView handled by the base snippet
            return;
        }
        if (pathname !== lastPathRef.current) {
            lastPathRef.current = pathname;
            trackMetaPageView();
        }
    }, [pathname]);

    return (
        <>
            <Script id="meta-pixel" strategy="afterInteractive">
                {`!function(f,b,e,v,n,t,s)
{if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '${pixelId}');
fbq('track', 'PageView');`}
            </Script>
            <noscript>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                    height="1"
                    width="1"
                    style={{ display: "none" }}
                    alt=""
                    src={`https://www.facebook.com/tr?id=${pixelId}&ev=PageView&noscript=1`}
                />
            </noscript>
        </>
    );
}
