// Fork override: the upstream badge links to and queries the dograh-hq GitHub
// repo. White-labelled builds render nothing. Keep the props signature in sync
// with upstream so call sites keep compiling (branding/apply.py warns if the
// upstream file changes).
interface GitHubStarBadgeProps {
  className?: string;
  label?: string;
  showCount?: boolean;
  source: string;
}

export function GitHubStarBadge(_props: GitHubStarBadgeProps) {
  return null;
}
