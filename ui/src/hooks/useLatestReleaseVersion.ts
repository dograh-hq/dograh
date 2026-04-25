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
const SEMVER_RE = /^(?:[a-z][a-z0-9-]*-)?v?(\d+)\.(\d+)\.(\d+)$/i;

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
        fetch("https://api.github.com/repos/dograh-hq/dograh/releases/latest")
            .then((res) => (res.ok ? res.json() : null))
            .then((data) => {
                if (cancelled || !data?.tag_name) return;
                const tag = data.tag_name as string;
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
                // silent — don't break the sidebar if GitHub is unreachable
            });

        return () => {
            cancelled = true;
        };
    }, [enabled, currentVersion]);

    const normalizedCurrent = currentVersion
        ? currentVersion.startsWith("v")
            ? currentVersion
            : `v${currentVersion}`
        : null;

    const currentParsed = normalizedCurrent ? parseSemver(normalizedCurrent) : null;
    const latestParsed = latest ? parseSemver(latest) : null;

    const isBehind = !!(
        normalizedCurrent &&
        latest &&
        isOlder(normalizedCurrent, latest)
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
