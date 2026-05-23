"use client";

import { useEffect, useState } from "react";

interface Options {
    enabled: boolean;
}

interface Result {
    latest: string | null;
    isBehind: boolean;
    isLatest: boolean;
}

const CACHE_KEY = "dograh-latest-release";
const CACHE_TTL_MS = 6 * 60 * 60 * 1000;
const SEMVER_RE = /^v?(\d+)\.(\d+)\.(\d+)$/;

function parseSemver(tag: string): [number, number, number] | null {
    const m = tag.match(SEMVER_RE);
    if (!m) return null;
    return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function isOlder(current: string, latest: string): boolean {
    const c = parseSemver(current);
    const l = parseSemver(latest);
    if (!c || !l) return false;
    for (let i = 0; i < 3; i++) {
        if (c[i] < l[i]) return true;
        if (c[i] > l[i]) return false;
    }
    return false;
}

export function useLatestReleaseVersion(
    currentVersion: string | undefined,
    { enabled }: Options,
): Result {
    const [latest, setLatest] = useState<string | null>(null);

    useEffect(() => {
        if (!enabled || !currentVersion) return;

        try {
            const raw = localStorage.getItem(CACHE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw) as { tag: string; fetchedAt: number };
                if (Date.now() - parsed.fetchedAt < CACHE_TTL_MS) {
                    setLatest(parsed.tag);
                    return;
                }
            }
        } catch {
            // ignore malformed cache
        }

        let cancelled = false;
        fetch("/api/config/latest-version")
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
                if (cancelled || !data?.latest) return;
                const tag = data.latest as string;
                try {
                    localStorage.setItem(
                        CACHE_KEY,
                        JSON.stringify({ tag, fetchedAt: Date.now() }),
                    );
                } catch {
                    // storage may be full or disabled
                }
                setLatest(tag);
            })
            .catch(() => {
                // silent — don't break the sidebar if the lookup fails
            });

        return () => {
            cancelled = true;
        };
    }, [enabled, currentVersion]);

    const currentParsed = currentVersion ? parseSemver(currentVersion) : null;
    const latestParsed = latest ? parseSemver(latest) : null;

    const isBehind = !!(
        currentVersion &&
        latest &&
        isOlder(currentVersion, latest)
    );

    const isLatest = !!(
        currentParsed &&
        latestParsed &&
        currentParsed[0] === latestParsed[0] &&
        currentParsed[1] === latestParsed[1] &&
        currentParsed[2] === latestParsed[2]
    );

    return { latest, isBehind, isLatest };
}
