from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.routers import clients as clients_router
from backend.services.client_settings_service import ClientSettingsService


def make_client(tmp_path) -> TestClient:
    """Build a TestClient with an isolated data directory."""

    def _override_service(
        client_id: str = Depends(clients_router.validate_client_id),
    ) -> ClientSettingsService:
        return ClientSettingsService(client_id, data_dir=tmp_path)

    app = FastAPI()
    app.dependency_overrides[clients_router.get_service] = _override_service
    app.include_router(clients_router.router)
    return TestClient(app)


def test_voice_client_is_allowed(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/clients/voice/stt")

    assert response.status_code == 200
    assert response.json()["eot_threshold"] == 0.7


def test_unknown_client_is_rejected(tmp_path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/clients/unknown/stt")

    assert response.status_code == 404
