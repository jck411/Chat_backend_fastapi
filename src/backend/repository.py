"""SQLite-backed repository for chat sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from backend.utils.datetime_utils import (
    format_timestamp_for_client,
    normalize_db_timestamp,
    parse_db_timestamp,
)

MessageRecord = dict[str, Any]
AttachmentRecord = dict[str, Any]

_CONTENT_JSON_METADATA_KEY = "__structured_content__"


def _encode_content(value: Any) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, str):
        return value, False
    serialized = json.dumps(value)
    return serialized, True


def _decode_content(value: str | None, is_structured: bool) -> Any:
    if value is None:
        return None
    if is_structured:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class ChatRepository:
    """Persist chat sessions, messages, and auxiliary events."""

    def __init__(self, database_path: Path):
        self._path = database_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open the SQLite connection and ensure tables exist."""

        if self._connection is not None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL;")
        await self._connection.execute("PRAGMA foreign_keys=ON;")
        await self._create_schema()

    async def _create_schema(self) -> None:
        assert self._connection is not None
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                timezone TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                metadata TEXT,
                client_message_id TEXT,
                parent_client_message_id TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attachments (
                attachment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
                storage_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                display_url TEXT NOT NULL,
                delivery_url TEXT NOT NULL,
                gcs_blob TEXT,
                signed_url TEXT,
                signed_url_expires_at TEXT,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME,
                last_used_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES conversations(session_id) ON DELETE CASCADE,
                request_id TEXT,
                kind TEXT NOT NULL,
                payload TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- Performance indexes
            CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_messages_client_message_id ON messages(client_message_id);
            CREATE INDEX IF NOT EXISTS idx_messages_parent_client_message_id ON messages(parent_client_message_id);
            CREATE INDEX IF NOT EXISTS idx_attachments_session_id ON attachments(session_id);
            CREATE INDEX IF NOT EXISTS idx_attachments_last_used_at ON attachments(last_used_at);
            """
        )
        await self._connection.commit()
        await self._ensure_column("messages", "client_message_id", "TEXT")
        await self._ensure_column("messages", "parent_client_message_id", "TEXT")
        await self._ensure_column("attachments", "gcs_blob", "TEXT")
        await self._ensure_column("attachments", "signed_url", "TEXT")
        await self._ensure_column("attachments", "signed_url_expires_at", "TEXT")
        await self._ensure_column("conversations", "title", "TEXT")
        await self._ensure_column("conversations", "saved", "INTEGER DEFAULT 0")
        await self._ensure_column("conversations", "updated_at", "DATETIME")
        await self._ensure_column(
            "conversations", "title_source", "TEXT DEFAULT 'auto'"
        )
        await self._ensure_column("conversations", "llm_settings", "TEXT")

    async def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """Ensure a column exists on a table, adding it if necessary."""

        assert self._connection is not None
        cursor = await self._connection.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        await cursor.close()
        existing = {row[1] for row in rows}
        if column in existing:
            return
        await self._connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def ensure_session(self, session_id: str) -> None:
        """Insert the session if it does not already exist."""

        assert self._connection is not None
        await self._connection.execute(
            "INSERT OR IGNORE INTO conversations(session_id) VALUES (?)",
            (session_id,),
        )
        await self._connection.commit()

    async def session_exists(self, session_id: str) -> bool:
        """Return True if the session is present in the database."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "SELECT 1 FROM conversations WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def get_session_metadata(self, session_id: str) -> dict[str, Any] | None:
        """Return metadata for a stored session, if available."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT session_id, created_at, timezone, title, saved, updated_at, llm_settings
            FROM conversations
            WHERE session_id = ?
            LIMIT 1
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None

        created_at = normalize_db_timestamp(row["created_at"])
        timezone_value = row["timezone"]
        llm_settings_raw = row["llm_settings"]
        llm_settings = None
        if llm_settings_raw:
            try:
                llm_settings = json.loads(llm_settings_raw)
            except json.JSONDecodeError:
                pass
        return {
            "session_id": row["session_id"],
            "created_at": created_at,
            "timezone": timezone_value,
            "title": row["title"],
            "saved": bool(row["saved"]),
            "updated_at": normalize_db_timestamp(row["updated_at"])
            if row["updated_at"]
            else None,
            "llm_settings": llm_settings,
        }

    async def clear_session(self, session_id: str) -> None:
        """Remove all messages and events for the given session."""

        assert self._connection is not None
        await self._connection.execute(
            "DELETE FROM conversations WHERE session_id = ?", (session_id,)
        )
        await self._connection.commit()

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: Any,
        *,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        client_message_id: str | None = None,
        parent_client_message_id: str | None = None,
    ) -> tuple[int, str | None]:
        """Persist a single chat message."""

        assert self._connection is not None
        serialized_content, structured = _encode_content(content)

        stored_metadata: dict[str, Any] | None
        if metadata:
            stored_metadata = dict(metadata)
        else:
            stored_metadata = {}
        if structured:
            stored_metadata[_CONTENT_JSON_METADATA_KEY] = True
        metadata_json = json.dumps(stored_metadata) if stored_metadata else None
        cursor = await self._connection.execute(
            """
            INSERT INTO messages(
                session_id,
                role,
                content,
                tool_call_id,
                metadata,
                client_message_id,
                parent_client_message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                serialized_content,
                tool_call_id,
                metadata_json,
                client_message_id,
                parent_client_message_id,
            ),
        )
        await self._connection.commit()
        try:
            inserted_id = cursor.lastrowid
        finally:
            await cursor.close()
        if inserted_id is None:  # pragma: no cover - defensive
            raise RuntimeError("Insert failed: lastrowid is None")
        timestamp_cursor = await self._connection.execute(
            "SELECT created_at FROM messages WHERE id = ?",
            (inserted_id,),
        )
        timestamp_row = await timestamp_cursor.fetchone()
        await timestamp_cursor.close()
        created_at: str | None = None
        if timestamp_row is not None:
            created_at = normalize_db_timestamp(timestamp_row["created_at"])

        # Touch updated_at and auto-title saved sessions
        await self._connection.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )
        await self._connection.commit()
        if role == "user":
            await self._auto_title_if_needed(session_id)

        return int(inserted_id), created_at

    async def get_messages(self, session_id: str) -> list[MessageRecord]:
        """Return conversation messages ordered by insertion."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT
                id,
                role,
                content,
                tool_call_id,
                metadata,
                client_message_id,
                parent_client_message_id,
                created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        messages: list[MessageRecord] = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else None
            is_structured = False
            if metadata and metadata.pop(_CONTENT_JSON_METADATA_KEY, None):
                is_structured = True
            message: MessageRecord = {
                "role": row["role"],
            }
            message["message_id"] = row["id"]
            content = _decode_content(row["content"], is_structured)
            if content is not None:
                message["content"] = content
            if row["tool_call_id"]:
                message["tool_call_id"] = row["tool_call_id"]
            if metadata:
                message.update(metadata)
            client_message_id = row["client_message_id"]
            if client_message_id:
                message["client_message_id"] = client_message_id
            parent_client_message_id = row["parent_client_message_id"]
            if parent_client_message_id:
                message["parent_client_message_id"] = parent_client_message_id
            created_at = normalize_db_timestamp(row["created_at"])
            edt_iso, utc_iso = format_timestamp_for_client(created_at)
            if edt_iso is not None:
                message["created_at"] = edt_iso
            if utc_iso is not None:
                message["created_at_utc"] = utc_iso
            messages.append(message)
        return messages

    async def update_latest_system_message(self, session_id: str, content: Any) -> bool:
        """Update the most recent system message for a session."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT id, metadata
            FROM messages
            WHERE session_id = ? AND role = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id, "system"),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return False

        serialized_content, structured = _encode_content(content)

        metadata_json = row["metadata"]
        metadata: dict[str, Any] | None
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                metadata = None
        else:
            metadata = None

        if metadata:
            metadata = dict(metadata)
        else:
            metadata = {}

        if structured:
            metadata[_CONTENT_JSON_METADATA_KEY] = True
        else:
            metadata.pop(_CONTENT_JSON_METADATA_KEY, None)

        metadata_payload = json.dumps(metadata) if metadata else None

        await self._connection.execute(
            """
            UPDATE messages
            SET content = ?, metadata = ?
            WHERE id = ?
            """,
            (serialized_content, metadata_payload, row["id"]),
        )
        await self._connection.commit()
        return True

    async def add_event(
        self,
        session_id: str,
        kind: str,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> None:
        """Persist auxiliary metadata for debugging or replay."""

        assert self._connection is not None
        await self._connection.execute(
            """
            INSERT INTO events(session_id, request_id, kind, payload)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, request_id, kind, json.dumps(payload)),
        )
        await self._connection.commit()

    def _row_to_attachment(self, row: aiosqlite.Row) -> AttachmentRecord:
        record: AttachmentRecord = {
            "attachment_id": row["attachment_id"],
            "session_id": row["session_id"],
            "storage_path": row["storage_path"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "display_url": row["display_url"],
            "delivery_url": row["delivery_url"],
            "gcs_blob": row["gcs_blob"],
            "signed_url": row["signed_url"],
            "signed_url_expires_at": row["signed_url_expires_at"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "last_used_at": row["last_used_at"],
        }
        metadata = row["metadata"]
        record["metadata"] = json.loads(metadata) if metadata else None
        return record

    async def add_attachment(
        self,
        *,
        attachment_id: str,
        session_id: str,
        storage_path: str | None,
        mime_type: str,
        size_bytes: int,
        display_url: str,
        delivery_url: str,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
        gcs_blob: str | None = None,
        signed_url: str | None = None,
        signed_url_expires_at: datetime | str | None = None,
    ) -> AttachmentRecord:
        """Persist an uploaded attachment and return the stored record."""

        assert self._connection is not None
        metadata_json = json.dumps(metadata) if metadata else None
        expires_value = (
            expires_at.isoformat(timespec="seconds")
            if isinstance(expires_at, datetime)
            else expires_at
        )
        signed_url_expires_value: str | None
        if isinstance(signed_url_expires_at, datetime):
            signed_url_expires_value = signed_url_expires_at.isoformat(
                timespec="seconds"
            )
        else:
            signed_url_expires_value = signed_url_expires_at
        storage_value = storage_path or gcs_blob or attachment_id
        await self._connection.execute(
            """
            INSERT INTO attachments(
                attachment_id,
                session_id,
                storage_path,
                mime_type,
                size_bytes,
                display_url,
                delivery_url,
                gcs_blob,
                signed_url,
                signed_url_expires_at,
                metadata,
                expires_at,
                last_used_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                attachment_id,
                session_id,
                storage_value,
                mime_type,
                size_bytes,
                display_url,
                delivery_url,
                gcs_blob,
                signed_url,
                signed_url_expires_value,
                metadata_json,
                expires_value,
            ),
        )
        await self._connection.commit()
        record = await self.get_attachment(attachment_id)
        if record is None:  # pragma: no cover - defensive
            raise RuntimeError("Attachment failed to persist")
        return record

    async def get_attachment(self, attachment_id: str) -> AttachmentRecord | None:
        """Return a single attachment record, if present."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT
                attachment_id,
                session_id,
                storage_path,
                mime_type,
                size_bytes,
                display_url,
                delivery_url,
                gcs_blob,
                signed_url,
                signed_url_expires_at,
                metadata,
                created_at,
                expires_at,
                last_used_at
            FROM attachments
            WHERE attachment_id = ?
            LIMIT 1
            """,
            (attachment_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return self._row_to_attachment(row)

    async def get_attachments_by_ids(
        self, attachment_ids: Iterable[str]
    ) -> dict[str, AttachmentRecord]:
        """Return attachments keyed by ID for the provided collection."""

        ids = [attachment_id for attachment_id in dict.fromkeys(attachment_ids)]
        if not ids:
            return {}

        assert self._connection is not None
        placeholders = ",".join("?" for _ in ids)
        cursor = await self._connection.execute(
            f"""
            SELECT
                attachment_id,
                session_id,
                storage_path,
                mime_type,
                size_bytes,
                display_url,
                delivery_url,
                gcs_blob,
                signed_url,
                signed_url_expires_at,
                metadata,
                created_at,
                expires_at,
                last_used_at
            FROM attachments
            WHERE attachment_id IN ({placeholders})
            """,
            ids,
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return {row["attachment_id"]: self._row_to_attachment(row) for row in rows}

    async def get_attachment_by_storage_path(
        self, storage_path: str
    ) -> AttachmentRecord | None:
        """Return a single attachment matching the given storage path.

        The `storage_path` must match the relative path stored in the DB
        (e.g., `session_id/attachment_id.ext`).
        """

        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT
                attachment_id,
                session_id,
                storage_path,
                mime_type,
                size_bytes,
                display_url,
                delivery_url,
                gcs_blob,
                signed_url,
                signed_url_expires_at,
                metadata,
                created_at,
                expires_at,
                last_used_at
            FROM attachments
            WHERE storage_path = ?
            LIMIT 1
            """,
            (storage_path,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return self._row_to_attachment(row)

    async def touch_attachment(
        self, attachment_id: str, *, session_id: str | None = None
    ) -> bool:
        """Refresh the last-used timestamp for an attachment."""

        assert self._connection is not None
        if session_id:
            cursor = await self._connection.execute(
                """
                UPDATE attachments
                SET last_used_at = CURRENT_TIMESTAMP
                WHERE attachment_id = ? AND session_id = ?
                """,
                (attachment_id, session_id),
            )
        else:
            cursor = await self._connection.execute(
                """
                UPDATE attachments
                SET last_used_at = CURRENT_TIMESTAMP
                WHERE attachment_id = ?
                """,
                (attachment_id,),
            )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def delete_attachment(self, attachment_id: str) -> bool:
        """Remove an attachment record."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "DELETE FROM attachments WHERE attachment_id = ?",
            (attachment_id,),
        )
        deleted = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(deleted)

    async def update_attachment_signed_url(
        self,
        attachment_id: str,
        *,
        signed_url: str,
        signed_url_expires_at: datetime | str,
    ) -> None:
        """Persist refreshed signed URL metadata for an attachment."""

        assert self._connection is not None
        if isinstance(signed_url_expires_at, datetime):
            expires_value = signed_url_expires_at.isoformat(timespec="seconds")
        else:
            expires_value = signed_url_expires_at
        await self._connection.execute(
            """
            UPDATE attachments
            SET
                signed_url = ?,
                signed_url_expires_at = ?,
                display_url = ?,
                delivery_url = ?
            WHERE attachment_id = ?
            """,
            (
                signed_url,
                expires_value,
                signed_url,
                signed_url,
                attachment_id,
            ),
        )
        await self._connection.commit()

    async def find_expired_attachments(
        self,
        *,
        now: datetime,
    ) -> list[AttachmentRecord]:
        """Return attachment records whose retention windows have elapsed."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            """
            SELECT
                attachment_id,
                session_id,
                storage_path,
                mime_type,
                size_bytes,
                display_url,
                delivery_url,
                gcs_blob,
                signed_url,
                signed_url_expires_at,
                metadata,
                created_at,
                expires_at,
                last_used_at
            FROM attachments
            WHERE expires_at IS NOT NULL
               OR signed_url_expires_at IS NOT NULL
            """,
        )
        rows = await cursor.fetchall()
        await cursor.close()

        reference = now.astimezone(timezone.utc)
        expired: list[AttachmentRecord] = []
        for row in rows:
            record = self._row_to_attachment(row)
            expires_at = parse_db_timestamp(record.get("expires_at"))
            signed_expires = parse_db_timestamp(record.get("signed_url_expires_at"))
            candidate = expires_at or signed_expires
            if candidate is None:
                continue
            if candidate <= reference:
                expired.append(record)
        return expired

    async def mark_attachments_used(
        self, session_id: str, attachment_ids: Iterable[str]
    ) -> None:
        """Update usage timestamps for attachments tied to a chat turn."""

        if not attachment_ids:
            return

        assert self._connection is not None
        params = [(attachment_id, session_id) for attachment_id in attachment_ids]
        if params:
            await self._connection.executemany(
                """
                UPDATE attachments
                SET last_used_at = CURRENT_TIMESTAMP
                WHERE attachment_id = ? AND session_id = ?
                """,
                params,
            )
        await self._connection.commit()

    async def delete_message(
        self,
        session_id: str,
        client_message_id: str,
    ) -> int:
        """Delete a message and recursively remove dependent children."""

        assert self._connection is not None

        numeric_identifier: int | None
        try:
            numeric_identifier = int(client_message_id)
        except (TypeError, ValueError):
            numeric_identifier = None

        cursor = await self._connection.execute(
            """
            WITH RECURSIVE target_messages AS (
                SELECT id, client_message_id
                FROM messages
                WHERE session_id = ?
                  AND (
                    client_message_id = ?
                    OR (? IS NOT NULL AND id = ?)
                  )
                UNION ALL
                SELECT child.id, child.client_message_id
                FROM messages AS child
                JOIN target_messages AS parent
                  ON child.parent_client_message_id = parent.client_message_id
                WHERE child.session_id = ?
            )
            DELETE FROM messages
            WHERE id IN (SELECT id FROM target_messages)
            """,
            (
                session_id,
                client_message_id,
                numeric_identifier,
                numeric_identifier,
                session_id,
            ),
        )
        deleted = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return deleted

    async def list_saved_conversations(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return saved conversations with title, date, and message preview."""

        assert self._connection is not None
        params: list[Any] = []
        where_clauses = ["c.saved = 1"]

        if search:
            where_clauses.append(
                "(c.title LIKE ? OR "
                "(SELECT m.content FROM messages m "
                "WHERE m.session_id = c.session_id AND m.role = 'user' "
                "ORDER BY m.id ASC LIMIT 1) LIKE ?)"
            )
            like_term = f"%{search}%"
            params.extend([like_term, like_term])

        where_sql = " AND ".join(where_clauses)
        params.extend([limit, offset])

        cursor = await self._connection.execute(
            f"""
            SELECT
                c.session_id,
                c.title,
                c.title_source,
                c.created_at,
                c.updated_at,
                (SELECT COUNT(*) FROM messages m WHERE m.session_id = c.session_id) AS message_count,
                (SELECT m.content FROM messages m
                 WHERE m.session_id = c.session_id AND m.role = 'user'
                 ORDER BY m.id ASC LIMIT 1) AS preview
            FROM conversations c
            WHERE {where_sql}
            ORDER BY COALESCE(c.updated_at, c.created_at) DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        results: list[dict[str, Any]] = []
        for row in rows:
            preview_text = row["preview"] or ""
            if len(preview_text) > 200:
                preview_text = preview_text[:200]
            created_at = normalize_db_timestamp(row["created_at"])
            updated_at = (
                normalize_db_timestamp(row["updated_at"]) if row["updated_at"] else None
            )
            results.append(
                {
                    "session_id": row["session_id"],
                    "title": row["title"],
                    "title_source": row["title_source"] or "auto",
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "message_count": row["message_count"],
                    "preview": preview_text,
                }
            )
        return results

    async def save_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        llm_settings: dict[str, Any] | None = None,
    ) -> bool:
        """Mark a session as saved, optionally setting its title and LLM settings."""

        assert self._connection is not None
        llm_settings_json = json.dumps(llm_settings) if llm_settings else None
        if title:
            await self._connection.execute(
                "UPDATE conversations SET saved = 1, title = ?, title_source = 'user', llm_settings = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (title, llm_settings_json, session_id),
            )
        else:
            await self._connection.execute(
                "UPDATE conversations SET saved = 1, llm_settings = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (llm_settings_json, session_id),
            )
        await self._connection.commit()
        return True

    async def update_session_llm_settings(
        self,
        session_id: str,
        llm_settings: dict[str, Any] | None,
    ) -> bool:
        """Update the LLM settings for a session."""

        assert self._connection is not None
        llm_settings_json = json.dumps(llm_settings) if llm_settings else None
        cursor = await self._connection.execute(
            "UPDATE conversations SET llm_settings = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (llm_settings_json, session_id),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)
        await self._connection.commit()
        return True

    async def unsave_session(self, session_id: str) -> bool:
        """Remove the saved flag from a session."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "UPDATE conversations SET saved = 0 WHERE session_id = ?",
            (session_id,),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def update_session_title(self, session_id: str, title: str) -> bool:
        """Update the display title for a session."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "UPDATE conversations SET title = ?, title_source = 'user', updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (title, session_id),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def _auto_title_if_needed(self, session_id: str) -> None:
        """Set a title from the first user message if the session has no title."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "SELECT title FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None or row["title"]:
            return

        cursor = await self._connection.execute(
            """
            SELECT content FROM messages
            WHERE session_id = ? AND role = 'user'
            ORDER BY id ASC LIMIT 1
            """,
            (session_id,),
        )
        msg_row = await cursor.fetchone()
        await cursor.close()
        if msg_row is None or not msg_row["content"]:
            return

        content = msg_row["content"]
        # Handle structured content (JSON array)
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                text_parts = [
                    item.get("text", "")
                    for item in parsed
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                content = " ".join(text_parts)
        except (json.JSONDecodeError, TypeError):
            pass

        title = content.strip()
        if len(title) > 60:
            title = title[:57] + "..."
        if title:
            await self._connection.execute(
                "UPDATE conversations SET title = ?, title_source = 'auto' WHERE session_id = ? AND title IS NULL",
                (title, session_id),
            )
            await self._connection.commit()

    async def delete_saved_conversation(self, session_id: str) -> bool:
        """Permanently delete a saved conversation and all its data."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "DELETE FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        deleted = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(deleted)

    async def get_session_messages_for_title(
        self, session_id: str
    ) -> list[dict[str, str]]:
        """Fetch user/assistant messages for title generation, capped at ~4000 chars."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "SELECT role, content FROM messages WHERE session_id = ? AND role IN ('user', 'assistant') ORDER BY id ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        messages: list[dict[str, str]] = []
        total_chars = 0
        for row in rows:
            content = row["content"] or ""
            # Handle structured content (JSON arrays with text parts)
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    text_parts = [
                        item.get("text", "")
                        for item in parsed
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    content = " ".join(text_parts)
            except (json.JSONDecodeError, TypeError):
                pass
            if total_chars + len(content) > 4000:
                content = content[: 4000 - total_chars]
                messages.append({"role": row["role"], "content": content})
                break
            messages.append({"role": row["role"], "content": content})
            total_chars += len(content)
        return messages

    async def update_session_ai_title(self, session_id: str, title: str) -> bool:
        """Set an AI-generated title and mark title_source as 'ai'."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "UPDATE conversations SET title = ?, title_source = 'ai', updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
            (title, session_id),
        )
        updated = cursor.rowcount
        await cursor.close()
        await self._connection.commit()
        return bool(updated)

    async def get_conversation_metadata(self, session_id: str) -> dict[str, Any] | None:
        """Return basic metadata for a single conversation."""

        assert self._connection is not None
        cursor = await self._connection.execute(
            "SELECT session_id, title, title_source, saved, created_at, updated_at FROM conversations WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "title": row["title"],
            "title_source": row["title_source"] or "auto",
            "saved": bool(row["saved"]),
            "created_at": normalize_db_timestamp(row["created_at"]),
            "updated_at": normalize_db_timestamp(row["updated_at"])
            if row["updated_at"]
            else None,
        }


__all__ = ["ChatRepository", "format_timestamp_for_client"]
