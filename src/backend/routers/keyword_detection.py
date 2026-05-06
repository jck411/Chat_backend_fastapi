"""Standalone keyword-detection WebSocket.

This endpoint is **service-agnostic**: it only listens for a wake-word using
the Azure Speech SDK keyword model and fires ``keyword_detected`` events.
The frontend decides what to do next (open Azure STT, Speechgram, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings

try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:  # pragma: no cover
    speechsdk = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/keyword", tags=["keyword"])


class KeywordListener:
    """Lightweight listener that only does keyword spotting."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.loop = asyncio.get_running_loop()
        self.stopped = False

        settings = get_settings()
        if speechsdk is None:
            raise RuntimeError("azure-cognitiveservices-speech is not installed")
        if not settings.azure_speech_key or not settings.azure_speech_region:
            raise RuntimeError("Azure Speech is not configured")

        model_path = settings.azure_keyword_model_path
        if model_path is None:
            raise RuntimeError("AZURE_KEYWORD_MODEL_PATH is not set")
        model_file = Path(model_path)
        if not model_file.exists():
            raise RuntimeError(f"Keyword model file not found: {model_file}")

        speech_config = speechsdk.SpeechConfig(
            subscription=settings.azure_speech_key.get_secret_value(),
            region=settings.azure_speech_region,
        )

        stream_format = speechsdk.audio.AudioStreamFormat(
            samples_per_second=16000,
            bits_per_sample=16,
            channels=1,
        )
        self.push_stream = speechsdk.audio.PushAudioInputStream(stream_format)
        audio_config = speechsdk.audio.AudioConfig(stream=self.push_stream)

        self.recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
        )
        self.keyword_model = speechsdk.KeywordRecognitionModel(str(model_file))

        self.recognizer.recognized.connect(self._on_recognized)
        self.recognizer.canceled.connect(self._on_canceled)

    def _send(self, payload: dict[str, object]) -> None:
        asyncio.run_coroutine_threadsafe(self.websocket.send_json(payload), self.loop)

    def _on_recognized(self, evt) -> None:
        result = evt.result
        if result and result.reason == speechsdk.ResultReason.RecognizedKeyword:  # type: ignore[union-attr]
            self._send({"type": "keyword_detected"})

    def _on_canceled(self, evt) -> None:
        details = getattr(evt, "error_details", "Unknown")
        self._send({"type": "error", "message": str(details)})

    def start(self) -> None:
        self.recognizer.start_keyword_recognition_async(self.keyword_model).get()
        self._send({"type": "armed"})

    def push_audio(self, chunk: bytes) -> None:
        if not self.stopped:
            self.push_stream.write(chunk)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        try:
            self.recognizer.stop_keyword_recognition_async().get()
        except Exception:
            pass
        self.push_stream.close()


@router.get("/status")
async def keyword_status() -> dict[str, object]:
    if speechsdk is None:
        return {"available": False}
    settings = get_settings()
    configured = bool(settings.azure_speech_key and settings.azure_speech_region)
    model_exists = bool(
        settings.azure_keyword_model_path
        and Path(settings.azure_keyword_model_path).exists()
    )
    return {"available": configured and model_exists}


@router.websocket("/listen")
async def keyword_listen(websocket: WebSocket) -> None:
    """Stream audio in; receive ``keyword_detected`` events back."""
    await websocket.accept()

    listener: KeywordListener | None = None
    try:
        listener = KeywordListener(websocket)
        await asyncio.to_thread(listener.start)

        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"] is not None:
                listener.push_audio(message["bytes"])
            elif "text" in message and message["text"]:
                payload = json.loads(message["text"])
                if payload.get("type") == "stop":
                    break

    except WebSocketDisconnect:
        logger.info("Keyword WebSocket disconnected")
    except Exception as exc:
        logger.exception("Keyword detection error")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if listener is not None:
            await asyncio.to_thread(listener.stop)
        try:
            await websocket.close()
        except Exception:
            pass
