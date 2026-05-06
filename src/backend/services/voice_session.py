import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import WebSocket


@dataclass
class VoiceSession:
    """Tracks the state of a single voice client connection."""

    client_id: str
    websocket: WebSocket
    state: str = "IDLE"  # IDLE, LISTENING, PROCESSING, SPEAKING
    transcript_buffer: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    stt_session_id: Optional[str] = None
    last_wakeword_time: Optional[datetime] = None
    stt_session_pending: bool = False

    def update_activity(self):
        """Update the last activity timestamp."""
        self.last_activity = datetime.utcnow()


class VoiceConnectionManager:
    """Manages active WebSocket connections and their sessions."""

    def __init__(self):
        self.active_connections: Dict[str, VoiceSession] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        """Accept a new WebSocket connection and create a session."""
        await websocket.accept()
        session = VoiceSession(client_id=client_id, websocket=websocket)
        self.active_connections[client_id] = session
        print(f"Client connected: {client_id}")

    def disconnect(self, client_id: str):
        """Remove a client session."""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            print(f"Client disconnected: {client_id}")

    def get_session(self, client_id: str) -> Optional[VoiceSession]:
        """Retrieve a session by client ID."""
        return self.active_connections.get(client_id)

    async def send_message(self, client_id: str, message: dict):
        """Send a JSON message to a specific client."""
        session = self.active_connections.get(client_id)
        if session:
            try:
                await session.websocket.send_json(message)
            except Exception as e:
                print(f"Error sending to {client_id}: {e}")
                self.disconnect(client_id)

    async def update_state(
        self, client_id: str, new_state: str, broadcast: bool = False
    ):
        """Update the state of a client session.

        Args:
            client_id: The client to update
            new_state: The new state (IDLE, LISTENING, PROCESSING, SPEAKING)
            broadcast: If True, broadcasts to all clients. If False (default),
                       sends only to the specific client for session isolation.
        """
        session = self.active_connections.get(client_id)
        if session:
            session.state = new_state
            session.update_activity()
            print(f"Client {client_id} state changed to {new_state}")
            message = {"type": "state", "state": new_state}
            if broadcast:
                await self.broadcast(message)
            else:
                await self.send_message(client_id, message)

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        clients = list(self.active_connections.keys())
        print(
            f"Broadcasting {message.get('type')} to {len(clients)} clients: {clients}"
        )
        for client_id in clients:
            await self.send_message(client_id, message)

    async def set_all_states(
        self, new_state: str, extra_data: Optional[Dict[str, Any]] = None
    ):
        """Update the state of ALL connected clients."""
        clients = list(self.active_connections.keys())
        logging.getLogger(__name__).debug(
            f"Setting ALL {len(clients)} clients to {new_state}"
        )

        for client_id in clients:
            session = self.active_connections.get(client_id)
            if session:
                session.state = new_state
                session.update_activity()

        # Broadcast the state change to everyone so UIs update
        msg = {"type": "state", "state": new_state}
        if extra_data:
            msg.update(extra_data)
        await self.broadcast(msg)
