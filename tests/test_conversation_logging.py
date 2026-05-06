import json
import logging

import pytest

from src.backend.services.conversation_logging import ConversationLogWriter


@pytest.mark.asyncio
async def test_conversation_log_writer_creates_timestamped_files(tmp_path) -> None:
    writer = ConversationLogWriter(tmp_path, min_level=logging.INFO)
    session_id = "abc123"
    session_created_at = "2024-05-12T15:30:45+00:00"
    request_snapshot = {"model": "test/model", "messages": [{"role": "user", "content": "hi"}]}
    conversation = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    log_path = await writer.write(
        session_id=session_id,
        session_created_at=session_created_at,
        request_snapshot=request_snapshot,
        conversation=conversation,
    )

    assert log_path.exists()
    assert log_path.parent == tmp_path / "2024-05-12"
    assert log_path.name == "session_2024-05-12_11-30-45_EDT_abc123.log"

    payload = log_path.read_text(encoding="utf-8")
    lines = payload.splitlines()
    delimiter = "=" * 80
    assert delimiter in lines
    first_delim_index = lines.index(delimiter)
    second_delim_index = len(lines) - 1
    assert lines[second_delim_index] == delimiter
    json_payload = "\n".join(lines[first_delim_index + 1 : second_delim_index])
    data = json.loads(json_payload)

    assert data["session_id"] == session_id
    assert data["message_count"] == len(conversation)
