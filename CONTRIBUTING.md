# Contributing to python-yarbo

Thanks for your interest in contributing! python-yarbo is a community library
built on reverse-engineered protocol knowledge — every contribution matters.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Branch Strategy](#branch-strategy)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Tests](#tests)
- [Pull Request Process](#pull-request-process)
- [Protocol Knowledge](#protocol-knowledge)

---

## Code of Conduct

Be kind. This is a small community project — be respectful and constructive.

---

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) or pip
- Git

### Clone and install in dev mode

```bash
gh repo fork markus-lassfolk/python-yarbo --clone
cd python-yarbo
pip install -e ".[dev]"
```

### Install pre-commit hooks

```bash
pre-commit install
```

---

## Branch Strategy

| Branch      | Purpose                                      |
| ----------- | -------------------------------------------- |
| `main`      | Release-ready code. Protected. Tag = release.|
| `develop`   | Integration branch. All PRs target here.     |
| `feature/*` | New features — branch from `develop`.        |
| `fix/*`     | Bug fixes — branch from `develop`.           |
| `docs/*`    | Documentation-only changes.                  |
| `chore/*`   | Tooling, CI, dependency updates.             |

**Always branch from `develop`**, not `main`.

```bash
git checkout develop
git pull origin develop
git checkout -b feature/my-new-feature
```

---

## Development Workflow

1. Make your changes under `src/yarbo/`
2. Write or update tests in `tests/`
3. Run lint and tests locally before pushing:

```bash
# Lint + format
ruff check src/ tests/
ruff format src/ tests/

# Type-check
mypy src/yarbo/

# Tests with coverage
pytest --cov=yarbo --cov-report=term-missing tests/
```

4. Update `CHANGELOG.md` under `[Unreleased]`
5. Push and open a PR targeting `develop`

---

## Coding Standards

### Style

- **4-space indentation**, spaces not tabs
- **LF line endings** (`.editorconfig` enforces this)
- **Double quotes** for strings (ruff enforces this)
- **Type hints on all public functions** — `mypy --strict` must pass
- **Docstrings on all public classes and methods** (Google-style)

### Architecture

- **Async-first**: all I/O is `async def`; sync wrappers provided separately
- **Dataclasses for models**: use `@dataclass` with `from_dict()` factories
- **No global state**: all configuration passed at construction time
- **Codec separation**: all encode/decode in `_codec.py`, no inline `zlib` elsewhere
- **Transport isolation**: `MqttTransport` handles paho; clients never touch paho directly

### Naming

- Classes: `PascalCase` (e.g. `YarboLocalClient`)
- Functions/methods: `snake_case` (e.g. `lights_on()`)
- Constants: `UPPER_SNAKE_CASE` (e.g. `LOCAL_PORT`)
- Private: prefix `_` (e.g. `_transport`)

---

## Tests

- All tests use **pytest** + **pytest-asyncio**
- Tests live in `tests/` and mirror the source structure
- Mock MQTT with `unittest.mock` — do **not** require a live broker for unit tests
- Aim for ≥ 70% coverage on new code (enforced in CI)

```bash
pytest --cov=yarbo --cov-report=term-missing tests/
```

---

## Pull Request Process

1. Ensure all CI checks pass (lint, type-check, tests)
2. Update `CHANGELOG.md` under `[Unreleased]`
3. Keep PRs focused — one feature or fix per PR
4. Request review from `@markus-lassfolk`

### Merge criteria

- ✅ All CI checks green
- ✅ All review comments resolved
- ✅ `CHANGELOG.md` updated
- ✅ No credentials, IPs, or serial numbers in code or commits
- ✅ Branch up-to-date with `develop`

---

## Protocol Knowledge

python-yarbo is built on reverse-engineered protocol knowledge. If you discover
new commands, topics, or payload formats, please:

1. Document them in the related issue or PR
2. Add them to `src/yarbo/const.py` (topics) or as methods on the client
3. Reference the source of discovery (packet capture, APK analysis, etc.)
4. Cross-reference with `yarbo-reversing/docs/COMMAND_CATALOGUE.md`

### Key protocol facts

- All MQTT payloads: `zlib.compress(json.dumps(payload).encode())`
- `get_controller` handshake required before action commands
- Topics: `snowbot/{SN}/app/{cmd}` (publish) and `snowbot/{SN}/device/{type}` (subscribe)
- Light keys: `led_head`, `led_left_w`, `led_right_w`, `body_left_r`, `body_right_r`, `tail_left_r`, `tail_right_r`
- Values are integers 0–255, **not** booleans

See [`yarbo-reversing`](https://github.com/markus-lassfolk/yarbo-reversing) for full protocol docs.
