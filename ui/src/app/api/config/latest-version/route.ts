import { NextResponse } from "next/server";

const GHCR_IMAGES = ["dograh-hq/dograh-ui", "dograh-hq/dograh-api"] as const;
const SEMVER_RE = /^(\d+)\.(\d+)\.(\d+)$/;
const REVALIDATE_SECONDS = 60 * 60;

type Semver = [number, number, number];

function parseSemver(tag: string): Semver | null {
  const m = tag.match(SEMVER_RE);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

function compareSemver(a: Semver, b: Semver): number {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

async function fetchLatestTag(image: string): Promise<string | null> {
  const tokenRes = await fetch(
    `https://ghcr.io/token?scope=repository:${image}:pull&service=ghcr.io`,
    { next: { revalidate: REVALIDATE_SECONDS } },
  );
  if (!tokenRes.ok) return null;
  const { token } = (await tokenRes.json()) as { token?: string };
  if (!token) return null;

  const tagsRes = await fetch(`https://ghcr.io/v2/${image}/tags/list`, {
    headers: { Authorization: `Bearer ${token}` },
    next: { revalidate: REVALIDATE_SECONDS },
  });
  if (!tagsRes.ok) return null;
  const { tags } = (await tagsRes.json()) as { tags?: string[] };

  let latest: { tag: string; parsed: Semver } | null = null;
  for (const tag of tags ?? []) {
    const parsed = parseSemver(tag);
    if (!parsed) continue;
    if (!latest || compareSemver(parsed, latest.parsed) > 0) {
      latest = { tag, parsed };
    }
  }
  return latest?.tag ?? null;
}

export async function GET() {
  try {
    const results = await Promise.all(GHCR_IMAGES.map(fetchLatestTag));

    // Only advertise an update once every image has published a tag at that
    // version — otherwise we'd nudge users to upgrade before the matching
    // container actually exists.
    let minLatest: { tag: string; parsed: Semver } | null = null;
    for (const tag of results) {
      if (!tag) return NextResponse.json({ latest: null }, { status: 200 });
      const parsed = parseSemver(tag);
      if (!parsed) return NextResponse.json({ latest: null }, { status: 200 });
      if (!minLatest || compareSemver(parsed, minLatest.parsed) < 0) {
        minLatest = { tag, parsed };
      }
    }

    return NextResponse.json(
      { latest: minLatest?.tag ?? null },
      {
        headers: {
          "Cache-Control": `public, max-age=${REVALIDATE_SECONDS}, s-maxage=${REVALIDATE_SECONDS}`,
        },
      },
    );
  } catch {
    return NextResponse.json({ latest: null }, { status: 200 });
  }
}
