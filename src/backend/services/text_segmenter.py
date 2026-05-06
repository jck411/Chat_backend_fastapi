"""Text segmentation utility for TTS processing pipeline."""

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def compile_delimiter_pattern(delimiters: list[str]) -> Optional[re.Pattern]:
    """
    Compile a regex pattern from a list of delimiters.

    Delimiters are sorted by length (longest first) to ensure proper matching.
    """
    if not delimiters:
        return None
    sorted_delims = sorted(delimiters, key=len, reverse=True)
    escaped = map(re.escape, sorted_delims)
    pattern = "|".join(escaped)
    return re.compile(pattern)


async def process_text_chunks(
    chunk_queue: asyncio.Queue,
    phrase_queue: asyncio.Queue,
    delimiters: list[str],
    use_segmentation: bool,
    first_phrase_min_chars: int,
    stop_event: Optional[asyncio.Event] = None,
    log_enabled: bool = False,
) -> None:
    """
    Process text chunks and segment them into phrases for TTS.

    Accumulates text chunks from chunk_queue and emits the first phrase
    once a delimiter occurs after the minimum character threshold is met.
    The remaining text is sent as a single final chunk to keep the pipeline
    at most two phrases.

    Args:
        chunk_queue: Queue receiving text chunks from LLM
        phrase_queue: Queue to send segmented phrases to TTS
        delimiters: List of delimiter strings to split at
        use_segmentation: Whether to enable segmentation
        first_phrase_min_chars: Minimum characters before emitting the first segmented phrase
        stop_event: Optional event to signal early stop
        log_enabled: Whether to log when waiting for a delimiter after reaching the minimum
    """
    working_string = ""
    delimiter_pattern = compile_delimiter_pattern(delimiters) if use_segmentation else None
    segmentation_active = use_segmentation and delimiter_pattern is not None
    waiting_logged = False

    async def maybe_emit_first_phrase() -> None:
        nonlocal working_string, segmentation_active, waiting_logged

        if not segmentation_active:
            return
        if not working_string:
            return
        if len(working_string) < first_phrase_min_chars:
            return

        delimiter_match = delimiter_pattern.search(working_string, first_phrase_min_chars)
        if not delimiter_match:
            if log_enabled and not waiting_logged:
                logger.info(
                    "Text segmenter: minimum reached, waiting for delimiter to emit first phrase"
                )
                waiting_logged = True
            return

        split_idx = delimiter_match.end()
        phrase = working_string[:split_idx].strip()
        if not phrase:
            working_string = working_string[split_idx:]
            return

        await phrase_queue.put(phrase)
        logger.info(f"Segment ({len(phrase)} chars): '{phrase[:80]}...'")
        working_string = working_string[split_idx:]
        segmentation_active = False
        waiting_logged = False

    try:
        while True:
            # Check stop event
            if stop_event and stop_event.is_set():
                logger.debug("Text segmenter: stop event triggered")
                break

            # Get next chunk (with timeout to check stop event)
            try:
                chunk = await asyncio.wait_for(chunk_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            # None signals end of text
            if chunk is None:
                if working_string.strip():
                    phrase = working_string.strip()
                    await phrase_queue.put(phrase)
                    logger.info(f"Final segment: '{phrase[:80]}...'")
                await phrase_queue.put(None)
                break

            # Accumulate the chunk
            working_string += chunk

            # Segment only until the first phrase is emitted
            if segmentation_active:
                await maybe_emit_first_phrase()

    except Exception as e:
        logger.error(f"Text segmenter error: {e}")
        await phrase_queue.put(None)
        raise
