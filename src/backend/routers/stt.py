from __future__ import annotations

import base64
import logging

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stt", tags=["stt"])


class DeepgramToken(BaseModel):
    access_token: str
    expires_in: int | None = None


@router.post("/deepgram/token", response_model=DeepgramToken)
async def get_deepgram_token() -> DeepgramToken:
    logger.info("Deepgram token request received")
    settings = get_settings()
    if (
        not settings.deepgram_api_key
        or not settings.deepgram_api_key.get_secret_value()
    ):
        logger.error("Deepgram API key not configured")
        raise HTTPException(
            status_code=503, detail="Deepgram is not configured on server"
        )

    headers = {
        "Authorization": f"Token {settings.deepgram_api_key.get_secret_value()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"ttl_seconds": settings.deepgram_token_ttl_seconds}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.debug("Requesting Deepgram temporary token")
            resp = await client.post(
                "https://api.deepgram.com/v1/auth/grant", headers=headers, json=payload
            )
            logger.debug(f"Deepgram response status: {resp.status_code}")

            # Log request ID for debugging
            request_id = resp.headers.get("dg-request-id")
            if request_id:
                logger.debug(f"Deepgram request ID: {request_id}")

            if resp.status_code != 200:
                # Try to surface Deepgram error if present
                dg_error = resp.headers.get("dg-error")
                err_code = None
                err_msg = None
                body = None
                try:
                    body = resp.json()
                    # Deepgram uses err_code and err_msg in responses
                    err_code = body.get("err_code")
                    err_msg = (
                        body.get("err_msg") or body.get("error") or body.get("detail")
                    )
                    logger.error(
                        f"Deepgram error response: {body}"
                        + (f" (request_id: {request_id})" if request_id else "")
                    )
                except Exception:
                    pass

                # Optional dev fallback: return API key directly as a token so the browser can use Sec-WebSocket-Protocol auth
                if (
                    settings.deepgram_allow_apikey_fallback
                    and settings.deepgram_api_key
                ):
                    logger.warning("Using API key fallback for Deepgram token")
                    return DeepgramToken(
                        access_token=settings.deepgram_api_key.get_secret_value(),
                        expires_in=None,
                    )

                # Provide specific error messages based on status code
                if resp.status_code == 401:
                    if (
                        err_code == "FORBIDDEN"
                        or "permission" in (err_msg or "").lower()
                    ):
                        msg = "Deepgram API key lacks sufficient permissions (needs at least 'Member' role)"
                    else:
                        msg = "Deepgram API key is invalid or unauthorized"
                elif resp.status_code == 402:
                    msg = "Deepgram project has insufficient funds"
                elif resp.status_code == 403:
                    msg = "Deepgram API key lacks sufficient permissions for token generation"
                else:
                    msg = (
                        err_msg
                        or dg_error
                        or f"Deepgram token request failed ({resp.status_code})"
                    )

                logger.error(
                    f"Deepgram token request failed: {msg}"
                    + (f" [err_code: {err_code}]" if err_code else "")
                    + (f" (request_id: {request_id})" if request_id else "")
                )
                raise HTTPException(status_code=502, detail=msg)
            data = resp.json()
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        logger.error(f"Deepgram request timed out: {exc}")
        raise HTTPException(status_code=504, detail="Deepgram request timed out")
    except httpx.RequestError as exc:
        logger.error(f"Network error contacting Deepgram: {exc}")
        raise HTTPException(
            status_code=502, detail=f"Network error contacting Deepgram: {exc}"
        )
    except Exception as exc:
        logger.exception("Unexpected error in Deepgram token request")
        raise HTTPException(
            status_code=502, detail=f"Failed to contact Deepgram: {exc}"
        )

    token = data.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="Deepgram did not return a token")
    expires_in = data.get("expires_in")
    return DeepgramToken(
        access_token=token,
        expires_in=expires_in if isinstance(expires_in, int) else None,
    )


# =============================================================================
# WebSocket STT Streaming Endpoint
# =============================================================================
# This provides server-side STT for the main frontend, avoiding the need for
# Deepgram token generation permissions. Audio is sent to the backend,
# which handles the Deepgram connection server-side.


@router.websocket("/stream")
async def stt_stream(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for STT streaming.

    The frontend sends audio chunks, and the backend returns transcripts.

    Protocol:
    - Frontend sends: {"type": "audio_chunk", "data": {"audio": "<base64>"}}
    - Backend sends: {"type": "transcript", "text": "...", "is_final": bool}
    - Backend sends: {"type": "stt_session_ready"} when STT is ready
    - Backend sends: {"type": "error", "message": "..."} on errors
    - Frontend can send: {"type": "close"} to end the session
    """
    await websocket.accept()

    # Get STT service from app state
    stt_service = getattr(websocket.app.state, "stt_service", None)
    if not stt_service:
        await websocket.send_json(
            {"type": "error", "message": "STT service unavailable"}
        )
        await websocket.close(code=1011, reason="STT service unavailable")
        return

    # Generate a unique session ID for this WebSocket connection
    import uuid

    session_id = f"stt_stream_{uuid.uuid4().hex[:12]}"
    logger.info(f"STT stream connected: {session_id}")

    async def on_transcript(text: str, is_final: bool) -> None:
        """Callback when transcript is received from Deepgram."""
        try:
            await websocket.send_json(
                {
                    "type": "transcript",
                    "text": text,
                    "is_final": is_final,
                }
            )
        except Exception as e:
            logger.warning(f"Failed to send transcript for {session_id}: {e}")

    async def on_error(error: str) -> None:
        """Callback when STT error occurs."""
        try:
            await websocket.send_json({"type": "error", "message": error})
        except Exception as e:
            logger.warning(f"Failed to send error for {session_id}: {e}")

    try:
        # Create STT session using 'svelte' client settings
        # This uses the backend's STTService (same as voice frontend)
        success = await stt_service.create_session(
            session_id,
            on_transcript,
            on_error,
            settings_client_id="svelte",
        )

        if not success:
            await websocket.send_json(
                {"type": "error", "message": "Failed to start STT session"}
            )
            await websocket.close(code=1011, reason="STT session failed")
            return

        # Notify frontend that STT is ready
        await websocket.send_json({"type": "stt_session_ready"})

        # Main message loop
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")

            if event_type == "audio_chunk":
                # Extract audio data
                payload = data.get("data", {})
                audio_b64 = payload.get("audio") if isinstance(payload, dict) else None
                if audio_b64:
                    try:
                        audio_bytes = base64.b64decode(audio_b64)
                        await stt_service.stream_audio(session_id, audio_bytes)
                    except Exception as e:
                        logger.warning(
                            f"Failed to process audio chunk for {session_id}: {e}"
                        )

            elif event_type == "close":
                logger.info(f"Client requested close for {session_id}")
                break

            elif event_type == "pause":
                stt_service.pause_session(session_id)
                await websocket.send_json({"type": "paused"})

            elif event_type == "resume":
                stt_service.resume_session(session_id)
                await websocket.send_json({"type": "resumed"})

    except WebSocketDisconnect:
        logger.info(f"STT stream disconnected: {session_id}")
    except Exception as e:
        logger.error(f"STT stream error for {session_id}: {e}", exc_info=True)
    finally:
        await stt_service.close_session(session_id)
        logger.info(f"STT session closed: {session_id}")
