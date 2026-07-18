// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it, vi } from "vitest";

import { GET } from "./route";

const PROJECT_ID = "proj-123";

vi.mock("@/lib/auth/config", () => ({
    getStackConfig: vi.fn(async () => ({
        projectId: "proj-123",
        publishableClientKey: "pck_test",
    })),
}));

function makeRequest(
    url: string,
    { cookie, forwardedProto }: { cookie?: string; forwardedProto?: string } = {},
) {
    const headers = new Headers();
    if (cookie) headers.set("cookie", cookie);
    if (forwardedProto) headers.set("x-forwarded-proto", forwardedProto);
    return new NextRequest(url, { headers });
}

function parseSetCookie(header: string) {
    const [nameValue, ...attrParts] = header.split("; ");
    const eq = nameValue.indexOf("=");
    const attrs = attrParts.map((a) => a.toLowerCase());
    return {
        name: nameValue.slice(0, eq),
        value: decodeURIComponent(nameValue.slice(eq + 1)),
        partitioned: attrs.includes("partitioned"),
        secure: attrs.includes("secure"),
        maxAge: Number(
            attrParts.find((a) => a.toLowerCase().startsWith("max-age="))?.slice(8),
        ),
        domain: attrParts
            .find((a) => a.toLowerCase().startsWith("domain="))
            ?.slice(7),
    };
}

describe("GET /impersonate", () => {
    it("returns 400 when refresh_token is missing", async () => {
        const response = await GET(
            makeRequest("https://app.dograh.com/impersonate"),
        );
        expect(response.status).toBe(400);
    });

    it("redirects to redirect_path on the same origin", async () => {
        const response = await GET(
            makeRequest(
                "https://app.dograh.com/impersonate?refresh_token=rt-new&redirect_path=/workflow/42",
            ),
        );
        expect(response.status).toBe(307);
        expect(response.headers.get("location")).toBe(
            "https://app.dograh.com/workflow/42",
        );
    });

    it("falls back to /workflow/create for a cross-origin redirect_path", async () => {
        const response = await GET(
            makeRequest(
                "https://app.dograh.com/impersonate?refresh_token=rt-new&redirect_path=https://evil.com/phish",
            ),
        );
        expect(response.headers.get("location")).toBe(
            "https://app.dograh.com/workflow/create",
        );
    });

    it("falls back to /workflow/create for a malformed redirect_path", async () => {
        const response = await GET(
            makeRequest(
                "https://app.dograh.com/impersonate?refresh_token=rt-new&redirect_path=https%3A%2F%2F",
            ),
        );
        expect(response.status).toBe(307);
        expect(response.headers.get("location")).toBe(
            "https://app.dograh.com/workflow/create",
        );
    });

    it("clears presented session cookies in both jars and all domain scopes on https", async () => {
        const hostRefresh = `__Host-hexclave-refresh-${PROJECT_ID}--default`;
        const response = await GET(
            makeRequest(
                "https://app.dograh.com/impersonate?refresh_token=rt-new",
                {
                    cookie: [
                        `${hostRefresh}=old-session`,
                        "hexclave-access=old-access",
                        `stack-refresh-${PROJECT_ID}=old-legacy`,
                        `hexclave-refresh-${PROJECT_ID}--custom-abc=old-custom`,
                        "stack-oauth-inner-xyz=oauth-state",
                        "hexclave-is-https=true",
                        "theme=dark",
                    ].join("; "),
                },
            ),
        );
        const cookies = response.headers.getSetCookie().map(parseSetCookie);

        // Deletions exist for both jars for a regular-named cookie.
        const accessDeletions = cookies.filter(
            (c) => c.name === "hexclave-access" && c.maxAge === 0,
        );
        expect(accessDeletions.some((c) => c.partitioned)).toBe(true);
        expect(accessDeletions.some((c) => !c.partitioned)).toBe(true);
        // ...and for the host-only plus each parent-domain scope.
        const accessDomains = new Set(accessDeletions.map((c) => c.domain));
        expect(accessDomains).toEqual(
            new Set([undefined, "app.dograh.com", "dograh.com"]),
        );

        // Enumerated --custom-* cookies are cleared too.
        expect(
            cookies.some(
                (c) =>
                    c.name === `hexclave-refresh-${PROJECT_ID}--custom-abc` &&
                    c.maxAge === 0 &&
                    c.partitioned,
            ),
        ).toBe(true);

        // __Host- deletions never carry a Domain attribute but still cover both jars.
        const hostDeletions = cookies.filter(
            (c) => c.name === hostRefresh && c.maxAge === 0,
        );
        expect(hostDeletions.length).toBeGreaterThan(0);
        expect(hostDeletions.every((c) => c.domain === undefined)).toBe(true);
        expect(hostDeletions.some((c) => c.partitioned)).toBe(true);

        // The legacy raw refresh cookie is only ever deleted, never re-set.
        expect(
            cookies
                .filter((c) => c.name === `stack-refresh-${PROJECT_ID}`)
                .every((c) => c.maxAge === 0),
        ).toBe(true);

        // Non-identity cookies are left alone: app cookies, in-flight OAuth
        // state, and the SDK's is-https flag.
        expect(cookies.some((c) => c.name === "theme")).toBe(false);
        expect(cookies.some((c) => c.name === "stack-oauth-inner-xyz")).toBe(
            false,
        );
        expect(cookies.some((c) => c.name === "hexclave-is-https")).toBe(false);

        // Deletions cover only cookies the browser presented (plus the fresh
        // set) — no blanket static list bloating the header block.
        expect(cookies.some((c) => c.name === "stack-access")).toBe(false);
    });

    it("sets the fresh refresh cookie once with Partitioned, after the deletions", async () => {
        const hostRefresh = `__Host-hexclave-refresh-${PROJECT_ID}--default`;
        const response = await GET(
            makeRequest(
                "https://app.dograh.com/impersonate?refresh_token=rt-new",
                { cookie: `${hostRefresh}=old-session` },
            ),
        );
        const cookies = response.headers.getSetCookie().map(parseSetCookie);

        // Exactly one fresh set: CHIPS browsers store it partitioned (where
        // the SDK writes), non-CHIPS browsers ignore the attribute and store
        // it in the regular jar (also where the SDK writes). A second copy in
        // the other jar would become a stale shadow the SDK never updates.
        const freshSets = cookies.filter(
            (c) => c.name === hostRefresh && c.maxAge > 0,
        );
        expect(freshSets).toHaveLength(1);
        expect(freshSets[0].partitioned).toBe(true);
        expect(freshSets[0].secure).toBe(true);
        expect(JSON.parse(freshSets[0].value).refresh_token).toBe("rt-new");

        // The fresh set must come after every deletion of the same name,
        // otherwise the browser would apply a deletion last.
        const lastDeletionIdx = cookies.reduce(
            (acc, c, i) =>
                c.name === hostRefresh && c.maxAge === 0 ? i : acc,
            -1,
        );
        const firstFreshIdx = cookies.findIndex(
            (c) => c.name === hostRefresh && c.maxAge > 0,
        );
        expect(firstFreshIdx).toBeGreaterThan(lastDeletionIdx);
    });

    it("honors x-forwarded-proto case-insensitively when the request is http", async () => {
        const response = await GET(
            makeRequest("http://app.dograh.com/impersonate?refresh_token=rt", {
                forwardedProto: "HTTPS",
            }),
        );
        const cookies = response.headers.getSetCookie().map(parseSetCookie);
        const freshSets = cookies.filter(
            (c) =>
                c.name === `__Host-hexclave-refresh-${PROJECT_ID}--default` &&
                c.maxAge > 0,
        );
        expect(freshSets).toHaveLength(1);
        expect(freshSets[0].partitioned).toBe(true);
    });

    it("uses no __Host- prefix and no partitioned attribute on plain http", async () => {
        const response = await GET(
            makeRequest("http://localhost:3010/impersonate?refresh_token=rt", {
                cookie: `hexclave-refresh-${PROJECT_ID}--default=old`,
            }),
        );
        const cookies = response.headers.getSetCookie().map(parseSetCookie);

        expect(cookies.some((c) => c.partitioned)).toBe(false);
        expect(cookies.some((c) => c.domain !== undefined)).toBe(false);

        const freshSets = cookies.filter(
            (c) =>
                c.name === `hexclave-refresh-${PROJECT_ID}--default` &&
                c.maxAge > 0,
        );
        expect(freshSets).toHaveLength(1);
        expect(freshSets[0].secure).toBe(false);
    });
});
