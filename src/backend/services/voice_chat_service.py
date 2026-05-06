"""Voice chat service for LLM integration via ChatOrchestrator."""

import json
import logging
from typing import AsyncGenerator

from backend.chat.orchestrator import ChatOrchestrator
from backend.schemas.chat import ChatCompletionRequest, ChatMessage
from backend.services.client_settings_service import get_client_settings_service

logger = logging.getLogger(__name__)


class VoiceChatService:
    """Chat service for voice PWA interactions using the main orchestrator."""

    def __init__(self, orchestrator: ChatOrchestrator):
        self._orchestrator = orchestrator
        self._settings_service = get_client_settings_service("voice")

    def clear_history(self, client_id: str):
        """Clear conversation history for a client by clearing session."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._orchestrator.clear_session(f"voice_{client_id}"))
        except RuntimeError:
            pass
        logger.info(f"Cleared conversation history for voice_{client_id}")

    async def generate_response_streaming(
        self,
        user_message: str,
        client_id: str = "default",
    ) -> AsyncGenerator[dict, None]:
        """Generate LLM response with streaming, yielding events as they occur.

        Yields:
            {"type": "text_chunk", "content": "..."} for text content
            {"type": "tool_status", "name": "...", "status": "started|finished|error"} for tools
        """
        settings = self._settings_service.get_llm()
        session_id = f"voice_{client_id}"

        logger.info(f"Voice streaming LLM request: session={session_id}, model={settings.model}")

        # Build messages list
        messages = [
            ChatMessage(role="user", content=user_message)
        ]

        if settings.system_prompt:
            messages.insert(0, ChatMessage(role="system", content=settings.system_prompt))

        request = ChatCompletionRequest(
            session_id=session_id,
            messages=messages,
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )

        try:
            async for event in self._orchestrator.process_stream(request):
                event_type = event.get("event")
                data = event.get("data")

                if event_type == "message" and data and data != "[DONE]":
                    try:
                        chunk = json.loads(data)
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                yield {"type": "text_chunk", "content": content}
                    except (json.JSONDecodeError, TypeError):
                        continue

                elif event_type == "tool":
                    try:
                        tool_data = json.loads(data) if data else {}
                        status = tool_data.get("status")
                        name = tool_data.get("name")

                        if status and name:
                            if status == "started":
                                logger.info(f"Tool started: {name}")
                            elif status == "finished":
                                logger.info(f"Tool finished: {name}")
                            elif status == "error":
                                logger.warning(f"Tool error: {name}")

                            yield {"type": "tool_status", "name": name, "status": status}
                    except (json.JSONDecodeError, TypeError):
                        pass

        except Exception as e:
            logger.error(f"Voice streaming LLM error: {e}", exc_info=True)
            yield {"type": "error", "message": "I encountered an error processing your request."}


__all__ = ["VoiceChatService"]
