"""Assistant content assembly and inline data helpers."""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

import httpx

from ...services.attachments import AttachmentService
from .attachments import process_assistant_fragment


logger = logging.getLogger(__name__)


INLINE_DATA_URI_PATTERN = re.compile(
    r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+",
    re.IGNORECASE,
)


class AssistantContentBuilder:
    """Accumulate assistant content fragments and persist generated images."""

    __slots__ = ("_segments", "_created_attachment_ids")

    def __init__(self) -> None:
        self._segments: list[tuple[str, Any]] = []
        self._created_attachment_ids: list[str] = []

    def add_text(self, text: str) -> None:
        if not isinstance(text, str) or not text:
            return

        if self._segments and self._segments[-1][0] == "text":
            _, previous_value = self._segments.pop()
            if isinstance(previous_value, str):
                text = previous_value + text
            else:
                self._segments.append(("text", previous_value))

        segments = list(split_text_and_inline_images(text))
        if not segments:
            return

        if len(segments) == 1 and segments[0][0] == "text":
            text_segment = segments[0][1]
            self._segments.append(("text", text_segment))
            return

        for kind, value in segments:
            if kind == "text":
                if value:
                    self._segments.append(("text", value))
            elif kind == "image":
                self._segments.append(
                    (
                        "fragment",
                        {
                            "type": "image_url",
                            "image_url": {"url": value.strip()},
                        },
                    )
                )

    def add_structured(self, fragments: Sequence[Any]) -> None:
        if not fragments:
            return
        for fragment in fragments:
            if isinstance(fragment, dict):
                self._segments.append(("fragment", fragment))
            elif isinstance(fragment, str):
                self.add_text(fragment)

    @property
    def created_attachment_ids(self) -> Sequence[str]:
        return tuple(self._created_attachment_ids)

    def register_attachment(self, attachment_id: str) -> None:
        if attachment_id and attachment_id not in self._created_attachment_ids:
            self._created_attachment_ids.append(attachment_id)

    async def finalize(
        self,
        session_id: str,
        attachment_service: AttachmentService | None,
        http_client: httpx.AsyncClient | None = None,
    ) -> str | list[dict[str, Any]] | None:
        if not self._segments:
            return None

        structured_mode = False
        text_buffer: list[str] = []
        structured_parts: list[dict[str, Any]] = []

        for kind, payload in self._segments:
            if kind == "text":
                if isinstance(payload, str):
                    text_buffer.append(payload)
                continue

            if not isinstance(payload, dict):
                continue

            if text_buffer:
                structured_parts.append({"type": "text", "text": "".join(text_buffer)})
                text_buffer.clear()
            structured_mode = True

            processed, attachment_id = await process_assistant_fragment(
                payload,
                session_id,
                attachment_service,
                http_client,
            )
            if attachment_id:
                self._created_attachment_ids.append(attachment_id)
            if processed is None:
                continue
            structured_parts.append(processed)

        if not structured_mode:
            return "".join(text_buffer)

        if text_buffer:
            structured_parts.append({"type": "text", "text": "".join(text_buffer)})

        return structured_parts if structured_parts else None


def split_text_and_inline_images(text: str) -> list[tuple[str, str]]:
    """Split text into tuples of (kind, value) for text and inline images."""

    if not text:
        return []

    segments: list[tuple[str, str]] = []
    cursor = 0
    for match in INLINE_DATA_URI_PATTERN.finditer(text):
        start, end = match.span()
        if start > cursor:
            segments.append(("text", text[cursor:start]))
        data_uri = match.group(0).strip()

        if segments and segments[-1][0] == "text":
            prefix_text = segments[-1][1]
            md_match = re.search(r"!\[([^\]]*)\]\($", prefix_text)
            if md_match:
                segments.pop()
                before_prefix = prefix_text[: md_match.start()]
                if before_prefix:
                    segments.append(("text", before_prefix))
                alt_text = md_match.group(1).strip()
                if alt_text:
                    segments.append(("text", f"{alt_text}: "))

        segments.append(("image", data_uri))
        cursor = end

    if cursor < len(text):
        segments.append(("text", text[cursor:]))

    return segments


__all__ = ["AssistantContentBuilder", "split_text_and_inline_images"]
