from __future__ import annotations

import json
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.chat import (
    _invalidate_models_cache,
    get_openrouter_client,
    router,
)


class DummyOpenRouterClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls = 0

    async def list_models(
        self, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        # Simulate cacheable list_models call.
        self.calls += 1
        return self._payload


def make_client(payload: dict[str, Any]) -> TestClient:
    app = FastAPI()
    dummy_client = DummyOpenRouterClient(payload)

    def _override_client() -> DummyOpenRouterClient:
        return dummy_client

    app.dependency_overrides[get_openrouter_client] = _override_client
    app.include_router(router)

    app.test_dummy_client = dummy_client  # type: ignore[attr-defined]
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_models_cache() -> Iterator[None]:
    _invalidate_models_cache()
    yield
    _invalidate_models_cache()


def test_models_endpoint_marks_tool_support() -> None:
    payload = {
        "data": [
            {
                "id": "a",
                "capabilities": {"tools": True},
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {"prompt": "0.0000001"},
                "categories": ["programming", "science"],
            },
            {"id": "b", "capabilities": {"tools": False}},
            {"id": "c", "capabilities": {"function_calling": "enabled"}},
            {"id": "d"},
            {
                "id": "e",
                "supported_parameters": ["temperature", "tools"],
                "architecture": {
                    "input_modalities": ["image", "text"],
                    "output_modalities": ["text"],
                },
                "pricing": {"prompt": "0"},
            },
        ]
    }

    client = make_client(payload)
    response = client.get("/api/models")

    assert response.status_code == 200
    body = response.json()
    supports_tools = {item["id"]: item.get("supports_tools") for item in body["data"]}
    assert supports_tools == {
        "a": True,
        "b": False,
        "c": True,
        "d": False,
        "e": True,
    }

    enriched = {item["id"]: item for item in body["data"]}
    assert enriched["a"]["input_modalities"] == ["text"]
    assert enriched["e"]["input_modalities"] == ["image", "text"]
    assert enriched["a"]["prompt_price_per_million"] == pytest.approx(0.1)
    assert "Other" in enriched["d"].get("series", [])

    metadata = body.get("metadata")
    assert metadata is not None
    assert metadata["total"] == 5
    assert metadata["base_count"] == 5
    assert metadata["count"] == 5


def test_models_metadata_endpoint_returns_facets() -> None:
    payload = {
        "data": [
            {
                "id": "a",
                "capabilities": {"tools": True},
                "architecture": {
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
                "pricing": {"prompt": "0.0000001"},
                "categories": ["programming", "science"],
            },
            {
                "id": "b",
                "capabilities": {"tools": False},
                "pricing": {"prompt": "0.01"},
            },
        ]
    }

    client = make_client(payload)
    response = client.get("/api/models/metadata")

    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert body["base_count"] == 2

    properties = body["properties"]
    paths = {entry["path"] for entry in properties}
    assert "id" in paths
    assert "capabilities.tools" in paths

    facets = body["facets"]
    assert "text" in facets["input_modalities"]
    assert facets["prompt_price_per_million"]["min"] == pytest.approx(0.1)
    assert "categories" not in facets


def test_models_endpoint_uses_cached_payload() -> None:
    payload = {"data": [{"id": "cached-model"}]}

    client = make_client(payload)
    dummy_client = client.app.test_dummy_client  # type: ignore[attr-defined]

    first = client.get("/api/models")
    assert first.status_code == 200
    assert dummy_client.calls == 1

    dummy_client._payload = {"data": [{"id": "updated-model"}]}

    second = client.get("/api/models")
    assert second.status_code == 200
    assert dummy_client.calls == 1
    assert second.json()["data"][0]["id"] == "cached-model"


def test_models_endpoint_filters_for_tool_support() -> None:
    payload = {
        "data": [
            {"id": "a", "capabilities": {"tools": True}},
            {"id": "b", "supports_tools": False},
            {"id": "c", "tools": ["something"]},
        ]
    }

    client = make_client(payload)
    response = client.get("/api/models", params={"tools_only": "true"})

    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["data"]]
    assert ids == ["a", "c"]

    metadata = body["metadata"]
    assert metadata["base_count"] == 2
    assert metadata["count"] == 2


def test_models_endpoint_supports_search_query() -> None:
    payload = {
        "data": [
            {"id": "model-a", "name": "Fast Model", "description": "Great for coding"},
            {
                "id": "model-b",
                "name": "Accurate Model",
                "description": "Great for math",
            },
        ]
    }

    client = make_client(payload)
    response = client.get("/api/models", params={"search": "math"})

    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["data"]]
    assert ids == ["model-b"]


def test_models_endpoint_series_aliases_allow_search() -> None:
    payload = {
        "data": [
            {"id": "google/text-bison@001", "name": "Text Bison"},
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini"},
        ]
    }

    client = make_client(payload)
    response = client.get("/api/models", params={"search": "pam"})

    assert response.status_code == 200
    body = response.json()
    ids = {item["id"] for item in body["data"]}
    assert "google/text-bison@001" in ids
    # Ensure normalized series are included on the enriched model.
    item = next(
        model for model in body["data"] if model["id"] == "google/text-bison@001"
    )
    assert "palm" in item.get("series_normalized", [])


def test_models_endpoint_series_aliases_allow_filtering() -> None:
    payload = {
        "data": [
            {"id": "google/text-bison@001", "name": "Text Bison"},
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini"},
        ]
    }

    client = make_client(payload)
    response = client.get(
        "/api/models",
        params={"filters": json.dumps({"series": ["pam"]})},
    )

    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["data"]]
    assert ids == ["google/text-bison@001"]


def test_models_endpoint_classifies_palm_series() -> None:
    payload = {
        "data": [
            {"id": "google/text-bison@001", "name": "Text Bison"},
            {"id": "google/codey-codechat-bison", "name": "Codey for Bison"},
        ]
    }

    client = make_client(payload)
    response = client.get("/api/models")

    assert response.status_code == 200
    body = response.json()
    series_map = {item["id"]: item.get("series", []) for item in body["data"]}
    assert "PaLM" in series_map["google/text-bison@001"]
    assert "PaLM" in series_map["google/codey-codechat-bison"]


def test_models_endpoint_applies_advanced_filters() -> None:
    payload = {
        "data": [
            {
                "id": "model-a",
                "pricing": {"prompt": 0.001, "completion": 0.002},
                "capabilities": {"tools": True},
            },
            {
                "id": "model-b",
                "pricing": {"prompt": 0.01, "completion": 0.02},
                "capabilities": {"tools": False},
            },
        ]
    }

    client = make_client(payload)
    response = client.get(
        "/api/models",
        params={
            "filters": json.dumps(
                {
                    "pricing.prompt": {"max": 0.005},
                    "capabilities.tools": True,
                }
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["data"]]
    assert ids == ["model-a"]
