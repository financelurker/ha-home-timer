# CLAUDE.md – Working rules for this repository

## Git workflow

- **Never commit or push to `main`.** `main` is protected by a ruleset.
- Every change goes through its own **branch + pull request** against `main`; the owner reviews and merges.
- Branch names: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, `refactor/<topic>`.
- One topic per PR. A PR description states what changed, why, how it was tested, and open points.
- Commits by Claude use the author `Claude <noreply@anthropic.com>`.

## Versioning and releases

- The version is set **only by the release tag** (`v0.1.0`, `v0.1.0-rc1`). `manifest.json` stays at `0.0.0`;
  `.github/workflows/release.yml` writes the tag's version into it and attaches `home_timer.zip`.
- The owner creates releases and chooses version numbers.

## Language

- All artifacts are in **English**: code, comments, docs, README, commit messages, PRs.
- The UI is translated: `translations/en.json` (base) and `translations/de.json` – keep both in sync.

## Documentation

- `README.md` stays short and scannable: pitch, what it does (links into `docs/`), installation,
  getting started, a documentation table, support, license. Details live on `docs/*.md`;
  every page starts with `[← Back to the overview](../README.md)`.
- README links are absolute GitHub URLs (HACS renders the README).

## Code

- Minimum Home Assistant version: 2026.11 (`hacs.json`). Tests run on Python 3.14 with
  `pytest-homeassistant-custom-component` (`pip install -r requirements_test.txt`, `pytest -q`).
- Brand icon: edit `scripts/brand/icon.svg`, render with `node scripts/brand/render.js`, commit both.
