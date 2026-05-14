# Changelog

## v0.1.0-alpha (in progress)

### Phase 0 — Repo bootstrap (2026-05-14)

- Initial repo structure: 8 module dirs + `.claude/`
- `README.md`, `CLAUDE.md` with design principles + lessons from parent
- `.gitignore` for Python + Claude Code conventions
- `config/vcs.py` — the 12-VC roster (Consider + Getro)
- `evaluation/ats_adapters.py` — ported verbatim from parent's `scripts/ats_adapters.py`
- Python package skeleton (`__init__.py` files)
- No features yet — foundation only

### Subsequent phases (planned, see CLAUDE.md)

- Phase 1: Notion workspace creation via `/fd-setup`
- Phase 2: end-to-end pipeline with stub Evaluation
- Phase 3: real LLM scoring (screener + scorer + JD fetch)
- Phase 4: QA learning loop + missed-fire handling + closure detection
- Phase 5: polish + v0.1.0 release
