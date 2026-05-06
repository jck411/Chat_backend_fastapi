# AI Playbook

Detailed guidelines for AI assistants working on this codebase.

## Project Structure
- **Backend** (this repo): `src/backend/` — FastAPI app with MCP orchestration, streaming, persistence
- **Admin UI** (this repo): `src/backend/admin/` — vanilla HTML/JS, served at `/admin/`, no build step
- **Kiosk frontend** (separate repo `kiosk_echo_frontend`) — built `dist/` is mounted at `/kiosk/` from the path in `KIOSK_STATIC_DIR`

## Rule 0: Context7
- If the **Context7** MCP server is available, consult its docs before architectural or dependency changes. If unavailable, rely on existing repo docs and established patterns.

## Project Rules
- Source code in `src/`, tests in `tests/`
- Use `uv` for dependency management and packaging
- Keep dependencies current: check for available updates before starting work
- Virtual environment: `.venv/` at project root (VS Code configured to use `.venv/bin/python`)
- Store secrets in `.env` files only (never hardcode credentials)
- Prefer **Model Context Protocol (MCP)** when applicable

## Code Standards
- Python 3.11 or higher
- Follow PEP 8 with type hints required
- Use `ruff` for formatting, linting, and import sorting
- Use Pydantic models for all data validation and schemas
- Single responsibility per file
- Tests: `pytest` framework, files named `test_*.py`

## Reliability & Async
- Fail fast with clear, descriptive errors
- Catch broad exceptions only at system boundaries
- Prefer async/event-driven patterns over polling
- Use async for potentially blocking I/O (HTTP, DB, disk, subprocess). Avoid blocking calls inside async code
- Always configure timeouts
- Never suppress `CancelledError` exceptions

## Principles
- Favor simple, maintainable solutions
- Prefer minimal, test-backed changes; refactor only when it reduces complexity
- Reduce codebase size whenever possible
- Actively remove obsolete and legacy code
- Edit existing documentation rather than creating duplicates
- Prohibit fake/mock data outside of test environments
