# Publishing releases

The **Release** workflow builds the package, publishes to PyPI, and creates a GitHub Release. It runs when you **push a tag** `v*` (e.g. `v2026.3.20`).

## Why is there no release / nothing on PyPI?

1. **Release runs only on tag push**  
   The **CI** workflow runs on every push to `main`; the **Release** workflow runs only when you push a tag like `v2026.3.20`. Green CI does not mean Release ran.

2. **Check the Release workflow**  
   In GitHub: **Actions** → select the **Release** workflow. See if there is a run for your tag and whether it failed (e.g. at “Publish to PyPI” or “Create GitHub Release”).

3. **PyPI Trusted Publisher**  
   Publishing uses [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/) (OIDC). You must configure it once:
   - On [pypi.org](https://pypi.org) → your project **python-yarbo** → **Publishing** → **Add a new trusted publisher**
   - **Owner:** `markus-lassfolk`  
   - **Repository:** `python-yarbo`  
   - **Workflow name:** `release.yml`  
   - **Environment name:** `release`  
   Without this, the “Publish to PyPI” step fails.

4. **`release` environment**  
   The release job uses the GitHub environment **release**. If that environment has **Required reviewers**, someone must approve the deployment before PyPI and the GitHub Release are created.

## Re-run a release (e.g. after fixing PyPI or environment)

1. **Actions** → **Release** → **Run workflow**
2. Choose branch **main**, set **Tag to release** to e.g. `v2026.3.20`, then **Run workflow**
3. When the run reaches the “Build & Release” job, approve the **release** environment if it has required reviewers
4. The workflow will build from that tag, publish to PyPI, and create the GitHub Release

## Release from a new tag (normal flow)

```bash
git tag v2026.3.20 -m "Release v2026.3.20"
git push origin v2026.3.20
```

Then check **Actions** → **Release** and approve the **release** environment if required.
