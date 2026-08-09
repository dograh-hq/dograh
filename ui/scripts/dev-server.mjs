// Launcher behind `npm run dev`: starts `next dev --turbopack` on this
// worktree's pinned UI port.
//
// Next reads its port from argv/process.env at CLI-parse time, BEFORE it loads
// .env files, so a UI_PORT line in ui/.env cannot reach it on its own -- this
// shim bridges the two. scripts/worktree-sync-env.sh assigns a unique UI_PORT
// per git worktree and derives CORS_ALLOWED_ORIGINS + UI_APP_URL in api/.env
// from it, so the backend's origin allowlist matches the port this UI binds.
//
// With UI_PORT unset -- the default for a plain clone -- no -p is passed and
// `next dev` behaves exactly as it did before: port 3000, auto-incrementing if
// busy. An exported UI_PORT wins over the ui/.env value. Extra args are
// forwarded, so `npm run dev -- --hostname 0.0.0.0` still works.
//
// Plain node, no dependencies: npm runs scripts through cmd.exe on Windows,
// where shell substitution in package.json would not expand.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const uiRoot = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Last UI_PORT assignment in ui/.env, or null when unset/absent. */
function pinnedPort() {
    let contents;
    try {
        contents = readFileSync(join(uiRoot, '.env'), 'utf8');
    } catch {
        return null;
    }
    // Last occurrence wins, matching how dotenv-style loaders resolve dupes.
    const matches = [...contents.matchAll(/^[ \t]*UI_PORT[ \t]*=[ \t]*"?(\d+)"?[ \t]*$/gm)];
    return matches.length > 0 ? matches[matches.length - 1][1] : null;
}

const port = process.env.UI_PORT || pinnedPort();

// Spawn Next's JS entry through node rather than node_modules/.bin/next: the
// bin shim is a .cmd on Windows and would need a shell to invoke.
const child = spawn(
    process.execPath,
    [
        join(uiRoot, 'node_modules', 'next', 'dist', 'bin', 'next'),
        'dev',
        '--turbopack',
        ...(port ? ['-p', port] : []),
        ...process.argv.slice(2),
    ],
    { cwd: uiRoot, stdio: 'inherit' }
);

child.on('exit', (code, signal) => process.exit(signal ? 1 : (code ?? 0)));
