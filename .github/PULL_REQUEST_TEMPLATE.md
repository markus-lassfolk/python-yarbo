## Summary

<!-- Describe your changes in a few sentences. Link the related issue(s). -->

Closes #<!-- issue number -->

## Type of change

- [ ] 🐛 Bug fix (non-breaking change that fixes an issue)
- [ ] ✨ New feature (non-breaking change that adds functionality)
- [ ] 💥 Breaking change (fix or feature that would cause existing functionality to change)
- [ ] 🧹 Refactor / code cleanup (no behaviour change)
- [ ] 📝 Documentation update
- [ ] ⚙️ CI/CD / tooling change

## Changes made

<!-- Bullet-point summary of what changed and why. -->

-
-

## Testing

<!-- Describe how you tested this. Include commands you ran. -->

- [ ] Added / updated pytest tests
- [ ] All tests pass locally (`pytest tests/`)
- [ ] Tested manually against Yarbo hardware (if applicable — describe setup below)

## Quality checklist

- [ ] **ruff lint**: Zero errors (`ruff check src/ tests/`)
- [ ] **ruff format**: No formatting issues (`ruff format --check src/ tests/`)
- [ ] **mypy**: Zero new type errors (`mypy src/yarbo/`)
- [ ] **Tests pass**: `pytest tests/` — all green
- [ ] **CHANGELOG.md** updated under `[Unreleased]`
- [ ] **Docs updated** (docstrings, README if API changed)
- [ ] **No secrets, credentials, IP addresses, or serial numbers** in code, comments, or commits
- [ ] **Follows coding standards** in [CONTRIBUTING.md](../CONTRIBUTING.md)

## Screenshots / output (optional)

<!-- Paste relevant terminal output, before/after if helpful. Redact any credentials/IPs. -->

---

> **Reviewer note**: Branch must be up-to-date with `develop` and all CI checks must pass before merge.
