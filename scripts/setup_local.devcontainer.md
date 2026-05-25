# Devcontainer contributor setup

`setup_local.sh` and `setup_local.ps1` provision the OSS Docker stack for local
deployments. They are not the recommended contributor workflow for this
repository.

For day-to-day development, use the checked-in devcontainer under
`.devcontainer/`. The full contributor instructions live in
`../docs/contribution/setup.mdx`.

The devcontainer flow pins Python 3.13, installs backend and frontend
dependencies in-container, creates a container-specific API env file, and
starts Postgres, Redis, and MinIO automatically.
