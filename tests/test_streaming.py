"""Tests for streaming handler functionality."""

from typing import Any

from backend.chat.streaming.tooling import finalize_tool_calls as _finalize_tool_calls
from backend.chat.streaming.tooling import merge_tool_calls as _merge_tool_calls


class TestFinalizeToolCalls:
    """Ensure finalized tool calls are filtered and normalized."""

    def test_ignores_incomplete_arguments(self):
        """Tool calls with empty or missing arguments should be filtered out."""
        raw = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": ""},
            }
        ]
        result = _finalize_tool_calls(raw)
        assert result == []

    def test_ignores_missing_name(self):
        """Tool calls without a function name should be filtered out."""
        raw = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "", "arguments": '{"location": "NYC"}'},
            }
        ]
        result = _finalize_tool_calls(raw)
        assert result == []

    def test_assigns_default_id(self):
        """Tool calls without an ID should get a default one."""
        raw = [
            {
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
            }
        ]
        result = _finalize_tool_calls(raw)
        assert len(result) == 1
        assert result[0]["id"] == "call_0"

    def test_preserves_valid_calls(self):
        """Valid tool calls should be preserved."""
        raw = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
            }
        ]
        result = _finalize_tool_calls(raw)
        assert len(result) == 1
        assert result[0]["id"] == "call_abc"
        assert result[0]["function"]["name"] == "get_weather"
        assert result[0]["function"]["arguments"] == '{"location": "NYC"}'

    def test_removes_rationale_field(self):
        """Rationale fields should be removed from tool calls."""
        raw = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
                "rationale": "Checking weather",
            }
        ]
        result = _finalize_tool_calls(raw)
        assert len(result) == 1
        assert "rationale" not in result[0]

    def test_filters_multiple_calls(self):
        """Mix of valid and invalid calls should be filtered correctly."""
        raw = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "get_time", "arguments": ""},
            },
            {
                "id": "call_3",
                "type": "function",
                "function": {"name": "get_date", "arguments": '{"timezone": "UTC"}'},
            },
        ]
        result = _finalize_tool_calls(raw)
        assert len(result) == 2
        assert result[0]["id"] == "call_1"
        assert result[1]["id"] == "call_3"


class TestMergeToolCalls:
    """Test tool call delta merging logic."""

    def test_creates_new_entry_for_new_index(self):
        """New tool call index should create a new entry."""
        accumulator: list[dict[str, Any]] = []
        deltas = [{"index": 0, "id": "call_1", "type": "function"}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert len(accumulator) == 1
        assert accumulator[0]["id"] == "call_1"

    def test_merges_into_existing_entry(self):
        """Deltas for existing index should merge."""
        accumulator = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": None, "arguments": ""},
            }
        ]
        deltas = [{"index": 0, "function": {"arguments": '{"loc"'}}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert accumulator[0]["function"]["arguments"] == '{"loc"'

    def test_merges_arguments_incrementally(self):
        """Arguments should accumulate across multiple deltas."""
        accumulator = [
            {"id": "call_1", "function": {"name": "get_weather", "arguments": ""}}
        ]
        _merge_tool_calls(
            accumulator=accumulator,
            deltas=[{"index": 0, "function": {"arguments": '{"location"'}}],
        )
        _merge_tool_calls(
            accumulator=accumulator,
            deltas=[{"index": 0, "function": {"arguments": ': "NYC"}'}}],
        )
        assert accumulator[0]["function"]["arguments"] == '{"location": "NYC"}'

    def test_sets_function_name(self):
        """Function name should be set when present in delta."""
        accumulator = [{"id": "call_1", "function": {"name": None, "arguments": ""}}]
        deltas = [{"index": 0, "function": {"name": "get_weather"}}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert accumulator[0]["function"]["name"] == "get_weather"

    def test_accumulates_rationale_from_delta(self):
        """Rationale from delta.rationale field should accumulate."""
        accumulator = [{"id": "call_1"}]
        deltas = [{"index": 0, "rationale": "Checking "}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert accumulator[0].get("rationale") == "Checking "

        deltas = [{"index": 0, "rationale": "weather"}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert accumulator[0].get("rationale") == "Checking weather"

    def test_accumulates_rationale_from_function(self):
        """Rationale from function.rationale field should accumulate."""
        accumulator = [{"id": "call_1", "function": {"name": None, "arguments": ""}}]
        deltas = [{"index": 0, "function": {"rationale": "Need to "}}]
        _merge_tool_calls(accumulator, deltas)
        assert accumulator[0].get("rationale") == "Need to "

        deltas = [{"index": 0, "function": {"rationale": "check"}}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert accumulator[0].get("rationale") == "Need to check"

    def test_handles_multiple_tool_calls(self):
        """Multiple tool calls in one delta should all be processed."""
        accumulator: list[dict[str, Any]] = []
        deltas = [
            {"index": 0, "id": "call_1", "type": "function"},
            {"index": 1, "id": "call_2", "type": "function"},
        ]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert len(accumulator) == 2
        assert accumulator[0]["id"] == "call_1"
        assert accumulator[1]["id"] == "call_2"

    def test_ignores_non_string_rationale(self):
        """Non-string rationale values should be ignored."""
        accumulator = [{"id": "call_1"}]
        deltas = [{"index": 0, "rationale": None}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert "rationale" not in accumulator[0]

    def test_ignores_empty_rationale(self):
        """Empty string rationale should be ignored."""
        accumulator = [{"id": "call_1"}]
        deltas = [{"index": 0, "rationale": ""}]
        _merge_tool_calls(accumulator=accumulator, deltas=deltas)
        assert "rationale" not in accumulator[0]
