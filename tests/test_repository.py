from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.backend.repository import ChatRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def repository(tmp_path):
    repo = ChatRepository(tmp_path / "chat.db")
    await repo.initialize()
    await repo.ensure_session("session-1")
    try:
        yield repo
    finally:
        await repo.close()


@pytest.mark.anyio
async def test_structured_content_roundtrip(repository):
    payload = [{"type": "text", "text": "hello"}]
    metadata = {"foo": "bar"}

    await repository.add_message(
        "session-1",
        role="user",
        content=payload,
        metadata=dict(metadata),
    )

    messages = await repository.get_messages("session-1")

    assert len(messages) == 1
    message = messages[0]

    assert message["content"] == payload
    assert message["foo"] == "bar"
    assert metadata == {"foo": "bar"}


@pytest.mark.anyio
async def test_string_content_preserved(repository):
    await repository.add_message(
        "session-1",
        role="assistant",
        content="plain text",
    )

    messages = await repository.get_messages("session-1")

    assert messages[0]["content"] == "plain text"


@pytest.mark.anyio
async def test_attachment_roundtrip(repository):
    expiry = datetime.now(timezone.utc) + timedelta(days=1)

    record = await repository.add_attachment(
        attachment_id="att-1",
        session_id="session-1",
        storage_path="session-1/image.png",
        mime_type="image/png",
        size_bytes=128,
        display_url="https://example.com/uploads/att-1",
        delivery_url="https://example.com/uploads/att-1",
        metadata={"filename": "image.png"},
        expires_at=expiry,
        gcs_blob="session-1/att-1__image.png",
        signed_url="https://example.com/uploads/att-1",
        signed_url_expires_at=expiry,
    )

    fetched = await repository.get_attachment("att-1")

    assert fetched is not None
    assert fetched["attachment_id"] == "att-1"
    assert fetched["session_id"] == "session-1"
    assert fetched["mime_type"] == "image/png"
    assert fetched["size_bytes"] == 128
    assert fetched["signed_url"] == "https://example.com/uploads/att-1"
    assert fetched["gcs_blob"] == "session-1/att-1__image.png"
    assert fetched["metadata"]["filename"] == "image.png"

    # Add a small delay to ensure timestamp difference
    import asyncio

    await asyncio.sleep(1.1)  # SQLite CURRENT_TIMESTAMP has 1-second resolution

    await repository.mark_attachments_used("session-1", ["att-1"])
    refreshed = await repository.get_attachment("att-1")
    assert refreshed is not None
    assert refreshed["last_used_at"] != record["last_used_at"]

    removed = await repository.delete_attachment("att-1")
    assert removed is True
    assert await repository.get_attachment("att-1") is None


@pytest.mark.anyio
async def test_message_timestamp_details(repository):
    await repository.add_message("session-1", role="user", content="hello")

    messages = await repository.get_messages("session-1")

    assert messages, "expected at least one message"
    message = messages[0]

    created_at = message.get("created_at")
    created_at_utc = message.get("created_at_utc")
    assert isinstance(created_at, str)
    assert isinstance(created_at_utc, str)
    assert created_at.endswith(("-04:00", "-05:00"))
    assert created_at_utc.endswith("+00:00")


@pytest.mark.anyio
async def test_update_latest_system_message_returns_false_without_entry(repository):
    updated = await repository.update_latest_system_message(
        "session-1", "Updated prompt"
    )

    assert updated is False


@pytest.mark.anyio
async def test_update_latest_system_message_overwrites_content(repository):
    await repository.add_message("session-1", role="system", content="Legacy")

    updated = await repository.update_latest_system_message(
        "session-1", "Legacy\n\nInstruction"
    )

    assert updated is True

    messages = await repository.get_messages("session-1")
    assert messages
    assert messages[0]["content"] == "Legacy\n\nInstruction"


@pytest.mark.anyio
async def test_update_latest_system_message_resets_structured_flag(repository):
    payload = [{"type": "text", "text": "Legacy"}]
    await repository.add_message("session-1", role="system", content=payload)

    updated = await repository.update_latest_system_message("session-1", "Updated")

    assert updated is True

    messages = await repository.get_messages("session-1")
    assert messages[0]["content"] == "Updated"


# ─── Conversation persistence tests ───


@pytest.mark.anyio
async def test_save_and_list_conversations(repository):
    await repository.add_message("session-1", role="user", content="Hello world")
    await repository.save_session("session-1")

    results = await repository.list_saved_conversations()
    assert len(results) == 1
    assert results[0]["session_id"] == "session-1"
    assert results[0]["message_count"] == 1


@pytest.mark.anyio
async def test_save_with_title(repository):
    await repository.save_session("session-1", title="My Chat")
    results = await repository.list_saved_conversations()
    assert results[0]["title"] == "My Chat"


@pytest.mark.anyio
async def test_unsave_session(repository):
    await repository.save_session("session-1")
    results = await repository.list_saved_conversations()
    assert len(results) == 1

    await repository.unsave_session("session-1")
    results = await repository.list_saved_conversations()
    assert len(results) == 0


@pytest.mark.anyio
async def test_update_session_title(repository):
    await repository.save_session("session-1", title="Old Title")
    updated = await repository.update_session_title("session-1", "New Title")
    assert updated is True

    results = await repository.list_saved_conversations()
    assert results[0]["title"] == "New Title"


@pytest.mark.anyio
async def test_auto_title_from_first_user_message(repository):
    await repository.add_message(
        "session-1", role="user", content="Tell me about Python programming"
    )

    metadata = await repository.get_session_metadata("session-1")
    assert metadata is not None
    assert metadata["title"] == "Tell me about Python programming"


@pytest.mark.anyio
async def test_auto_title_truncates_long_messages(repository):
    long_msg = "A" * 100
    await repository.add_message("session-1", role="user", content=long_msg)

    metadata = await repository.get_session_metadata("session-1")
    assert metadata is not None
    assert metadata["title"] == "A" * 57 + "..."
    assert len(metadata["title"]) == 60


@pytest.mark.anyio
async def test_auto_title_handles_structured_content(repository):
    payload = [{"type": "text", "text": "Structured message"}]
    import json

    await repository.add_message("session-1", role="user", content=json.dumps(payload))

    metadata = await repository.get_session_metadata("session-1")
    assert metadata is not None
    assert metadata["title"] == "Structured message"


@pytest.mark.anyio
async def test_auto_title_does_not_overwrite_existing(repository):
    await repository.save_session("session-1", title="Custom Title")
    await repository.add_message(
        "session-1", role="user", content="This should not become the title"
    )

    metadata = await repository.get_session_metadata("session-1")
    assert metadata["title"] == "Custom Title"


@pytest.mark.anyio
async def test_delete_saved_conversation(repository):
    await repository.add_message("session-1", role="user", content="Hello")
    await repository.save_session("session-1")

    deleted = await repository.delete_saved_conversation("session-1")
    assert deleted is True

    results = await repository.list_saved_conversations()
    assert len(results) == 0


@pytest.mark.anyio
async def test_delete_nonexistent_conversation(repository):
    deleted = await repository.delete_saved_conversation("nonexistent")
    assert deleted is False


@pytest.mark.anyio
async def test_list_conversations_pagination(repository):
    for i in range(5):
        sid = f"session-page-{i}"
        await repository.ensure_session(sid)
        await repository.add_message(sid, role="user", content=f"Message {i}")
        await repository.save_session(sid)

    page1 = await repository.list_saved_conversations(limit=2, offset=0)
    page2 = await repository.list_saved_conversations(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert page1[0]["session_id"] != page2[0]["session_id"]


@pytest.mark.anyio
async def test_unsaved_sessions_not_in_list(repository):
    await repository.add_message("session-1", role="user", content="Hello")
    results = await repository.list_saved_conversations()
    assert len(results) == 0


# ─── Search tests ───


@pytest.mark.anyio
async def test_search_conversations_by_title(repository):
    await repository.save_session("session-1", title="Python programming tips")
    await repository.ensure_session("session-2")
    await repository.save_session("session-2", title="Cooking recipes")

    results = await repository.list_saved_conversations(search="Python")
    assert len(results) == 1
    assert results[0]["session_id"] == "session-1"


@pytest.mark.anyio
async def test_search_conversations_by_preview(repository):
    await repository.add_message(
        "session-1", role="user", content="Tell me about quantum physics"
    )
    await repository.save_session("session-1")
    await repository.ensure_session("session-2")
    await repository.add_message("session-2", role="user", content="Best pasta recipes")
    await repository.save_session("session-2")

    results = await repository.list_saved_conversations(search="quantum")
    assert len(results) == 1
    assert results[0]["session_id"] == "session-1"


@pytest.mark.anyio
async def test_search_conversations_no_match(repository):
    await repository.save_session("session-1", title="Python tips")

    results = await repository.list_saved_conversations(search="nonexistent")
    assert len(results) == 0


@pytest.mark.anyio
async def test_search_conversations_empty_returns_all(repository):
    await repository.save_session("session-1", title="Topic A")
    await repository.ensure_session("session-2")
    await repository.save_session("session-2", title="Topic B")

    results = await repository.list_saved_conversations(search=None)
    assert len(results) == 2
