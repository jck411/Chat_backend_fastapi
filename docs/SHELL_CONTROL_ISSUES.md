# Shell Control Server - Issue Tracker

## ✅ 1. Missing State Update Tool (RESOLVED)

**Problem:** `_save_state()` helper existed but no MCP tool exposed it. State was read-only.

**Solution Implemented:**
- Added `host_update_state` tool - merges updates into state with timestamp
- Added `host_update_profile` tool - merges updates into profile
- Added `_deep_merge()` - recursive merge with `None` = delete key
- Added `_append_delta()` - audit log to `deltas.log` (fixes issue #2)
- Added `_save_profile()` - atomic write for profile.json
- **Auto-snapshot system**: After package/service/default changes, state auto-updates with:
  - `packages`: Tracked apps by category (browsers, editors, terminals, etc.)
  - `defaults`: XDG defaults (browser, file_manager, text_editor, etc.)
  - `enabled_services`: Tracked systemd services

**Token cost:** ~114 tokens per snapshot

---

## ✅ 2. Unused `_get_deltas_path` (RESOLVED)

**Problem:** `_get_deltas_path()` returned `deltas.log` path but was never used.

**Solution Implemented:**
- `_append_delta()` now writes to `deltas.log` on every profile/state update
- Each entry is JSONL with: `ts`, `type`, `changes`, `reason`

---

## ✅ 3. No Atomic Profile/State Operations (RESOLVED)

**Problem:** TOCTOU race condition - file could change between `exists()` check and `read_text()`.

**Solution Implemented:**
- Changed `_load_profile()` and `_load_state()` to use EAFP pattern
- Removed `if not path.exists()` checks
- Now uses `try/except FileNotFoundError` directly on `read_text()`
- Eliminates the time gap between check and use

---

## ✅ 4. Missing Input Validation for host_id (RESOLVED)

**Problem:** `_get_host_dir()` accepts any string. A malicious `host_id` like `../../../etc` could cause path traversal.

**Solution Implemented:**
- Added `_VALID_HOST_ID` regex pattern: `^[a-zA-Z0-9_-]+$`
- Updated `_get_host_dir()` to validate `host_id` before using it
- Raises `ValueError` for invalid host IDs containing path separators, dots, spaces, etc.
- Added tests: `test_host_id_validation_prevents_path_traversal` and `test_host_id_validation_allows_valid_ids`

---

## ~~5. No Tool to List Available Hosts~~ (REMOVED)

**Original Problem:** Users can't discover what hosts exist.

**Resolution:** Removed `host_list` tool. Host profiles are hardcoded in the frontend dropdown since:
- Each machine only operates on its own profile
- Profiles are stored in GDrive (synced across machines)
- `HOST_ROOT_PATH` env var points to the GDrive-synced folder
- No need for dynamic discovery

---

## Summary

| Issue | Status | Priority |
|-------|--------|----------|
| 1. Missing State Update Tool | ✅ Resolved | - |
| 2. Unused `_get_deltas_path` | ✅ Resolved | - |
| 3. TOCTOU Race Condition | ✅ Resolved | - |
| 4. Path Traversal Vulnerability | ✅ Resolved | - |
| 5. Host Listing Tool | ❌ Removed | - |
