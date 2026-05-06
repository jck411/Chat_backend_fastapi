"""OpenAI TTS processor with queue-based streaming."""

import asyncio
import logging
import os
from typing import Optional

import openai
from dotenv import load_dotenv

from backend.schemas.client_settings import TtsSettings

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


async def openai_text_to_speech_processor(
    phrase_queue: asyncio.Queue,
    audio_queue: asyncio.Queue,
    stop_event: asyncio.Event,
    settings: TtsSettings,
    openai_client: Optional[openai.AsyncOpenAI] = None,
) -> None:
    """
    Process phrases from phrase_queue, convert to speech with OpenAI TTS,
    and push audio chunks to audio_queue.

    Uses OpenAI's streaming TTS API for low latency. Respects stop_event
    for barge-in interruption.

    Args:
        phrase_queue: Queue receiving text phrases to synthesize
        audio_queue: Queue to send audio chunks to
        stop_event: Event to signal early stop (barge-in)
        settings: TTS settings with voice, model, speed, etc.
        openai_client: Optional pre-configured OpenAI client
    """
    logger.info("openai_text_to_speech_processor: ENTERING function")

    api_key = os.getenv("OPENAI_API_KEY")
    logger.info(f"OpenAI TTS: API key present: {bool(api_key)}")

    if openai_client is None:
        logger.info("OpenAI TTS: Creating AsyncOpenAI client...")
        openai_client = openai.AsyncOpenAI(api_key=api_key)
        logger.info("OpenAI TTS: Client created successfully")

    chunk_size = settings.stream_chunk_bytes  # Bytes per audio chunk

    logger.info(
        f"OpenAI TTS processor started (model={settings.model}, voice={settings.voice})"
    )
    logger.info("OpenAI TTS: Entering main while loop")

    try:
        while True:
            # Check stop event
            if stop_event.is_set():
                logger.info("OpenAI TTS: stop event triggered, exiting")
                await audio_queue.put(None)
                return

            # Get next phrase (with timeout to check stop event)
            try:
                phrase = await asyncio.wait_for(phrase_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            # None signals end of input
            if phrase is None:
                logger.debug("OpenAI TTS: received end signal")
                await audio_queue.put(None)
                return

            # Skip empty phrases
            stripped_phrase = phrase.strip()
            if not stripped_phrase:
                continue

            logger.info(
                f"OpenAI TTS: synthesizing phrase ({len(stripped_phrase)} chars): '{stripped_phrase[:80]}...'"
            )

            try:
                # Use streaming response for low latency
                async with openai_client.audio.speech.with_streaming_response.create(
                    model=settings.model,
                    voice=settings.voice,
                    input=stripped_phrase,
                    speed=settings.speed,
                    response_format=settings.response_format,
                ) as response:
                    async for audio_chunk in response.iter_bytes(chunk_size):
                        # Check stop event during streaming
                        if stop_event.is_set():
                            logger.info("OpenAI TTS: stop event triggered mid-stream")
                            await audio_queue.put(None)
                            return

                        await audio_queue.put(audio_chunk)

                # Add a small silence buffer between phrases (prevents audio glitches)
                await audio_queue.put(b"\x00" * 512)
                logger.info("OpenAI TTS: phrase synthesis completed, sent audio chunks")

            except openai.APIError as e:
                logger.error(f"OpenAI TTS API error: {e}")
                await audio_queue.put(None)
                return
            except Exception as e:
                logger.error(f"OpenAI TTS error: {e}")
                await audio_queue.put(None)
                return

    except Exception as e:
        logger.error(f"OpenAI TTS processor error: {e}")
        await audio_queue.put(None)
        raise


async def process_tts_streams(
    phrase_queue: asyncio.Queue,
    audio_queue: asyncio.Queue,
    stop_event: asyncio.Event,
    settings: TtsSettings,
    openai_client: Optional[openai.AsyncOpenAI] = None,
) -> None:
    """
    Orchestrate TTS processing. Currently only supports OpenAI.

    Args:
        phrase_queue: Queue receiving text phrases
        audio_queue: Queue to send audio chunks to
        stop_event: Event to signal early stop
        settings: TTS settings
        openai_client: Optional pre-warmed OpenAI client for faster first request
    """
    if not settings.enabled:
        # TTS disabled - drain phrase queue
        logger.debug("TTS disabled, draining phrase queue")
        while True:
            phrase = await phrase_queue.get()
            if phrase is None:
                break
        await audio_queue.put(None)
        return

    provider = settings.provider.lower()
    logger.info(
        f"process_tts_streams started (provider={provider}, enabled={settings.enabled})"
    )

    if provider == "openai":
        await openai_text_to_speech_processor(
            phrase_queue, audio_queue, stop_event, settings, openai_client
        )
    else:
        logger.error(f"Unsupported TTS provider: {provider}")
        await audio_queue.put(None)
