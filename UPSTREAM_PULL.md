# Pulling from upstream Dograh

This repo is a fork of [`dograh-hq/dograh`](https://github.com/dograh-hq/dograh). All of our additions live exclusively under `overlay/`. Files outside `overlay/` should remain byte-identical to upstream so that merges are clean.

## Sync procedure

```bash
git fetch upstream
git merge upstream/main
# Conflicts should NOT occur if we never touched files outside overlay/.
```

If `pipecat/` submodule is bumped upstream:

```bash
git submodule update --init --recursive
```

## Hard rule

If we ever **need** to modify a file outside `overlay/`, the change must be contributed upstream first (PR to `dograh-hq/dograh`). Do not patch locally — the next `git merge upstream/main` will fight us.

The only exceptions:

- `overlay/` (our code)
- `UPSTREAM_PULL.md` (this file)
- `.github/workflows/overlay-*.yml` (CI for our overlay code)
- `.gitignore` additions (append-only, no removals of upstream entries)
