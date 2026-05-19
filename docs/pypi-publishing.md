# PyPI publishing runbook

OpenACA publishes the Python package with PyPI Trusted Publishing. No
long-lived PyPI API token is stored in GitHub.

## One-time PyPI setup

If the PyPI project does **not** yet exist (first publish via the
workflow):

1. Log in to PyPI with the maintainer account that owns the `openaca`
   project.
2. Go to Account settings -> Publishing -> Add a new pending publisher.
3. Use these values:
   - PyPI project name: `openaca`
   - Owner: `open-agent-security`
   - Repository name: `openaca`
   - Workflow name: `publish-pypi.yml`
   - Environment name: `pypi`

If the PyPI project **already exists** (e.g., earlier releases were
published manually via `twine upload`), the pending-publisher path
won't work — Trusted Publishing rejects it with
`invalid-pending-publisher: valid token, but project already
exists`. Use the regular publisher path instead:

1. Go to the project's publishing settings:
   `https://pypi.org/manage/project/openaca/settings/publishing/`.
2. Add a new GitHub publisher with the same values listed above
   (Owner / Repository / Workflow name / Environment).

In GitHub, create the `pypi` environment if you want release
approvals before publishing:
- Repository settings -> Environments -> New environment -> `pypi`.
- Optional but recommended: add required reviewers.

## Publish a release

1. Ensure `pyproject.toml` has the version you intend to publish.
2. Merge the release-prep PR to `main`.
3. Tag the exact version on `main` and push the tag:

   ```bash
   git checkout main
   git pull --ff-only
   git tag v0.1.0b2
   git push origin v0.1.0b2
   ```

The `Publish PyPI` workflow builds the package, verifies the tag name
matches `pyproject.toml`, runs the local quality gates, checks package
metadata with `twine check`, and publishes to PyPI through OIDC.

PyPI versions are immutable. If a publish succeeds with the wrong
contents, bump to the next version instead of trying to overwrite it.
