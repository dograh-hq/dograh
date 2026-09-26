# Versioning convention

`VERSION` is the canonical application version. Keep it synchronized with
`api/pyproject.toml`, `ui/package.json`, and the two root package versions in
`ui/package-lock.json` by running:

```bash
scripts/bump_version.sh X.Y.Z[.N]
```

The CALMOS Connect white-label line may use a fourth numeric component for its
internal build sequence (for example, `1.46.0.3`).

Every source change commit must increase the patch number unless the change is
an intentional minor or major release. Build images with both the synchronized
version and the Git short SHA as immutable tags, for example `1.46.0.3` and
`<short-sha>`; `latest` is only a convenience pointer. The API health response
and UI version endpoint expose the synchronized version for deployment checks.
