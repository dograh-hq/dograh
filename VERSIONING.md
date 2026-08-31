# Versioning convention

`VERSION` is the canonical application version. Keep it synchronized with
`api/pyproject.toml`, `ui/package.json`, and the two root package versions in
`ui/package-lock.json` by running:

```bash
scripts/bump_version.sh X.Y.Z
```

Every source change commit must increase the patch number unless the change is
an intentional minor or major release. Build images with both the synchronized
version and the Git short SHA as immutable tags, for example `1.45.1` and
`<short-sha>`; `latest` is only a convenience pointer. The API health response
and UI version endpoint expose the synchronized version for deployment checks.
