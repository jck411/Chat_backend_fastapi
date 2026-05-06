from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings

try:
    import azure.cognitiveservices.speech as speechsdk
except Exception:  # pragma: no cover - guarded at runtime
    speechsdk = None

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/azure-stt", tags=["azure-stt"])


class AzureSession:
    """Pure transcription session — no keyword logic."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.loop = asyncio.get_running_loop()
        self.stopped = False

        settings = get_settings()
        if speechsdk is None:
            raise RuntimeError(
                "azure-cognitiveservices-speech is not installed on the server"
            )
        if not settings.azure_speech_key or not settings.azure_speech_region:
            raise RuntimeError("Azure Speech is not configured on the server")

        speech_config = speechsdk.SpeechConfig(
            subscription=settings.azure_speech_key.get_secret_value(),
            region=settings.azure_speech_region,
        )
        speech_config.speech_recognition_language = settings.azure_speech_language

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
        self._wire_events()

    def _send_async(self, payload: dict[str, object]) -> None:
        asyncio.run_coroutine_threadsafe(self.websocket.send_json(payload), self.loop)

    def _wire_events(self) -> None:
        def on_recognizing(evt):
            text = evt.result.text if evt.result else ""
            if text:
                self._send_async({"type": "partial", "text": text})

        def on_recognized(evt):
            result = evt.result
            if (
                result
                and result.reason == speechsdk.ResultReason.RecognizedSpeech
                and result.text
            ):
                self._send_async({"type": "final", "text": result.text})

        def on_canceled(evt):
            details = getattr(evt, "error_details", "Unknown cancellation")
            self._send_async({"type": "error", "message": str(details)})

        self.recognizer.recognizing.connect(on_recognizing)
        self.recognizer.recognized.connect(on_recognized)
        self.recognizer.canceled.connect(on_canceled)

    def start(self) -> None:
        self.recognizer.start_continuous_recognition_async().get()
        self._send_async({"type": "started", "mode": "transcription"})

    def push_audio(self, chunk: bytes) -> None:
        if not self.stopped:
            self.push_stream.write(chunk)

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        try:
            self.recognizer.stop_continuous_recognition_async().get()
        except Exception:
            pass
        self.push_stream.close()


@router.get("/status")
async def azure_stt_status() -> dict[str, object]:
    settings = get_settings()
    configured = bool(settings.azure_speech_key and settings.azure_speech_region)
    return {
        "configured": configured,
        "language": settings.azure_speech_language,
    }


@router.websocket("/stream")
async def azure_stt_stream(websocket: WebSocket) -> None:
    await websocket.accept()

    session: AzureSession | None = None
    try:
        session = AzureSession(websocket)
        await asyncio.to_thread(session.start)

        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"] is not None:
                session.push_audio(message["bytes"])
            elif "text" in message and message["text"]:
                payload = json.loads(message["text"])
                if payload.get("type") == "stop":
                    break

    except WebSocketDisconnect:
        logger.info("Azure STT websocket disconnected")
    except Exception as exc:
        logger.exception("Azure STT stream error")
        await websocket.send_json({"type": "error", "message": str(exc)})
    finally:
        if session is not None:
            await asyncio.to_thread(session.stop)
        try:
            await websocket.close()
        except Exception:
            pass
