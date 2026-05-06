"""Unified client settings router.

This router handles settings for all clients using the pattern:
  /api/clients/{client_id}/llm
  /api/clients/{client_id}/stt
  /api/clients/{client_id}/tts
  /api/clients/{client_id}/presets
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from backend.schemas.client_settings import (
    ClientPreset,
    ClientPresets,
    ClientPresetUpdate,
    ClientSettings,
    LlmSettings,
    LlmSettingsUpdate,
    SttSettings,
    SttSettingsUpdate,
    TtsSettings,
    TtsSettingsUpdate,
    UiSettings,
    UiSettingsUpdate,
)
from backend.services.client_settings_service import (
    ClientSettingsService,
    get_client_settings_service,
)
from backend.services.client_tool_preferences import ClientToolPreferences
from backend.services.mcp_management import MCPManagementService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/clients", tags=["Client Settings"])

# Valid client IDs to prevent arbitrary directory creation
VALID_CLIENTS = {"kiosk", "svelte", "cli", "voice"}


def validate_client_id(
    client_id: str = Path(..., description="Client identifier"),
) -> str:
    """Validate that the client_id is known."""
    if client_id not in VALID_CLIENTS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown client '{client_id}'. Valid clients: {', '.join(sorted(VALID_CLIENTS))}",
        )
    return client_id


def get_service(
    client_id: str = Depends(validate_client_id),
) -> ClientSettingsService:
    """Get the settings service for a client."""
    return get_client_settings_service(client_id)


def get_tool_preferences(request: Request) -> ClientToolPreferences:
    service = getattr(request.app.state, "client_tool_preferences", None)
    if service is None:
        raise RuntimeError("Client tool preferences service is not configured")
    return service


def get_mcp_management(request: Request) -> MCPManagementService:
    service = getattr(request.app.state, "mcp_management_service", None)
    if service is None:
        raise RuntimeError("MCP management service is not configured")
    return service


# =============================================================================
# LLM Settings
# =============================================================================


@router.get("/{client_id}/llm", response_model=LlmSettings)
async def get_llm_settings(
    service: ClientSettingsService = Depends(get_service),
) -> LlmSettings:
    """Get LLM settings for the client."""
    return service.get_llm()


@router.put("/{client_id}/llm", response_model=LlmSettings)
async def update_llm_settings(
    update: LlmSettingsUpdate,
    service: ClientSettingsService = Depends(get_service),
) -> LlmSettings:
    """Update LLM settings for the client."""
    return service.update_llm(update)


@router.post("/{client_id}/llm/reset", response_model=LlmSettings)
async def reset_llm_settings(
    service: ClientSettingsService = Depends(get_service),
) -> LlmSettings:
    """Reset LLM settings to defaults."""
    return service.replace_llm(LlmSettings())


# =============================================================================
# STT Settings
# =============================================================================


@router.get("/{client_id}/stt", response_model=SttSettings)
async def get_stt_settings(
    service: ClientSettingsService = Depends(get_service),
) -> SttSettings:
    """Get STT settings for the client."""
    return service.get_stt()


@router.put("/{client_id}/stt", response_model=SttSettings)
async def update_stt_settings(
    update: SttSettingsUpdate,
    service: ClientSettingsService = Depends(get_service),
) -> SttSettings:
    """Update STT settings for the client."""
    try:
        return service.update_stt(update)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{client_id}/stt/reset", response_model=SttSettings)
async def reset_stt_settings(
    service: ClientSettingsService = Depends(get_service),
) -> SttSettings:
    """Reset STT settings to defaults."""
    service._save_json("stt", SttSettings().model_dump())
    service._cache.pop("stt", None)
    return service.get_stt()


# =============================================================================
# TTS Settings
# =============================================================================


@router.get("/{client_id}/tts", response_model=TtsSettings)
async def get_tts_settings(
    service: ClientSettingsService = Depends(get_service),
) -> TtsSettings:
    """Get TTS settings for the client."""
    return service.get_tts()


@router.put("/{client_id}/tts", response_model=TtsSettings)
async def update_tts_settings(
    update: TtsSettingsUpdate,
    service: ClientSettingsService = Depends(get_service),
) -> TtsSettings:
    """Update TTS settings for the client."""
    try:
        return service.update_tts(update)
    except PermissionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{client_id}/tts/reset", response_model=TtsSettings)
async def reset_tts_settings(
    service: ClientSettingsService = Depends(get_service),
) -> TtsSettings:
    """Reset TTS settings to defaults."""
    service._save_json("tts", TtsSettings().model_dump())
    service._cache.pop("tts", None)
    return service.get_tts()


# TTS voice listing (shared across all clients)
@router.get("/{client_id}/tts/voices")
async def get_tts_voices(
    provider: str = "openai",
) -> list[dict[str, str]]:
    """Get available TTS voice models for the specified provider."""
    # OpenAI TTS voices
    if provider == "openai":
        return [
            {"id": "alloy", "name": "Alloy (Neutral)"},
            {"id": "echo", "name": "Echo (Male)"},
            {"id": "fable", "name": "Fable (British)"},
            {"id": "onyx", "name": "Onyx (Male, Deep)"},
            {"id": "nova", "name": "Nova (Female)"},
            {"id": "shimmer", "name": "Shimmer (Female)"},
        ]
    # Deepgram Aura voices
    elif provider == "deepgram":
        return [
            {"id": "aura-asteria-en", "name": "Asteria (Female)"},
            {"id": "aura-luna-en", "name": "Luna (Female)"},
            {"id": "aura-stella-en", "name": "Stella (Female)"},
            {"id": "aura-athena-en", "name": "Athena (Female)"},
            {"id": "aura-hera-en", "name": "Hera (Female)"},
            {"id": "aura-orion-en", "name": "Orion (Male)"},
            {"id": "aura-arcas-en", "name": "Arcas (Male)"},
            {"id": "aura-perseus-en", "name": "Perseus (Male)"},
            {"id": "aura-angus-en", "name": "Angus (Male, Irish)"},
            {"id": "aura-orpheus-en", "name": "Orpheus (Male)"},
            {"id": "aura-helios-en", "name": "Helios (Male, British)"},
            {"id": "aura-zeus-en", "name": "Zeus (Male)"},
        ]
    elif provider == "elevenlabs":
        return [
            {"id": "Rachel", "name": "Rachel"},
            {"id": "Drew", "name": "Drew"},
            {"id": "Clyde", "name": "Clyde"},
            {"id": "Paul", "name": "Paul"},
            {"id": "Domi", "name": "Domi"},
            {"id": "Dave", "name": "Dave"},
            {"id": "Fin", "name": "Fin"},
            {"id": "Sarah", "name": "Sarah"},
            {"id": "Antoni", "name": "Antoni"},
            {"id": "Thomas", "name": "Thomas"},
            {"id": "Charlie", "name": "Charlie"},
            {"id": "Emily", "name": "Emily"},
        ]
    return []


# =============================================================================
# UI Settings
# =============================================================================


@router.get("/{client_id}/ui", response_model=UiSettings)
async def get_ui_settings(
    service: ClientSettingsService = Depends(get_service),
) -> UiSettings:
    """Get UI settings for the client."""
    return service.get_ui()


@router.put("/{client_id}/ui", response_model=UiSettings)
async def update_ui_settings(
    update: UiSettingsUpdate,
    service: ClientSettingsService = Depends(get_service),
) -> UiSettings:
    """Update UI settings for the client."""
    return service.update_ui(update)


@router.post("/{client_id}/ui/reset", response_model=UiSettings)
async def reset_ui_settings(
    service: ClientSettingsService = Depends(get_service),
) -> UiSettings:
    """Reset UI settings to defaults."""
    service._save_json("ui", UiSettings().model_dump())
    service._cache.pop("ui", None)
    return service.get_ui()


# =============================================================================
# Presets
# =============================================================================


@router.get("/{client_id}/presets", response_model=ClientPresets)
async def get_presets(
    service: ClientSettingsService = Depends(get_service),
) -> ClientPresets:
    """Get all presets for the client."""
    return service.get_presets()


@router.post("/{client_id}/presets", response_model=ClientPresets)
async def create_preset(
    preset: ClientPreset,
    service: ClientSettingsService = Depends(get_service),
) -> ClientPresets:
    """Create a new preset."""
    return service.add_preset(preset)


@router.put("/{client_id}/presets/{index}", response_model=ClientPresets)
async def update_preset(
    index: int,
    update: ClientPresetUpdate,
    service: ClientSettingsService = Depends(get_service),
) -> ClientPresets:
    """Update a preset at the given index."""
    try:
        return service.update_preset(index, update)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{client_id}/presets/{index}", response_model=ClientPresets)
async def delete_preset(
    index: int,
    service: ClientSettingsService = Depends(get_service),
) -> ClientPresets:
    """Delete a preset at the given index."""
    try:
        return service.delete_preset(index)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


async def _apply_mcp_snapshot(
    preset: ClientPreset,
    client_id: str,
    prefs: ClientToolPreferences,
    mgmt: MCPManagementService,
) -> None:
    """Restore MCP preferences and tool toggles from a preset."""
    await prefs.set_enabled_servers(client_id, preset.enabled_servers)

    # Apply disabled_tools from preset, clearing any not in the snapshot
    current_status = await mgmt.get_status()
    for server in current_status:
        server_id = server["id"]
        preset_disabled = preset.disabled_tools.get(server_id, [])
        current_disabled = server.get("disabled_tools", [])
        if preset_disabled != current_disabled:
            try:
                await mgmt.update_disabled_tools(server_id, preset_disabled)
            except KeyError:
                logger.debug(
                    "Skipping unknown server '%s' during preset apply", server_id
                )


@router.post("/{client_id}/presets/{index}/activate", response_model=ClientSettings)
async def activate_preset(
    index: int,
    client_id: str = Depends(validate_client_id),
    service: ClientSettingsService = Depends(get_service),
    prefs: ClientToolPreferences = Depends(get_tool_preferences),
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> ClientSettings:
    """Activate a preset and apply its settings."""
    try:
        presets = service.get_presets()
        preset = presets.presets[index]
        result = service.activate_preset(index)
        await _apply_mcp_snapshot(preset, client_id, prefs, mgmt)
        return result
    except (ValueError, IndexError) as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# Name-based Preset Endpoints (for frontend compatibility)
# =============================================================================


@router.post("/{client_id}/presets/by-name/{name}/apply", response_model=ClientSettings)
async def apply_preset_by_name(
    name: str,
    client_id: str = Depends(validate_client_id),
    service: ClientSettingsService = Depends(get_service),
    prefs: ClientToolPreferences = Depends(get_tool_preferences),
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> ClientSettings:
    """Apply a preset by name.

    Applies LLM, STT, TTS settings, MCP client preferences, and tool toggles.
    """
    presets = service.get_presets()
    preset_index = None
    preset: ClientPreset | None = None
    for i, p in enumerate(presets.presets):
        if p.name == name:
            preset_index = i
            preset = p
            break

    if preset_index is None or preset is None:
        raise HTTPException(status_code=404, detail=f"Preset not found: {name}")

    result = service.load_preset_settings(preset_index)
    await _apply_mcp_snapshot(preset, client_id, prefs, mgmt)
    return result


@router.delete("/{client_id}/presets/by-name/{name}", response_model=ClientPresets)
async def delete_preset_by_name(
    name: str,
    service: ClientSettingsService = Depends(get_service),
) -> ClientPresets:
    """Delete a preset by name."""
    presets = service.get_presets()
    for i, preset in enumerate(presets.presets):
        if preset.name == name:
            return service.delete_preset(i)
    raise HTTPException(status_code=404, detail=f"Preset not found: {name}")


@router.post(
    "/{client_id}/presets/by-name/{name}/set-active", response_model=ClientPresets
)
async def set_active_preset_by_name(
    name: str,
    client_id: str = Depends(validate_client_id),
    service: ClientSettingsService = Depends(get_service),
    prefs: ClientToolPreferences = Depends(get_tool_preferences),
    mgmt: MCPManagementService = Depends(get_mcp_management),
) -> ClientPresets:
    """Set a preset as the active one by name."""
    presets = service.get_presets()
    for i, preset in enumerate(presets.presets):
        if preset.name == name:
            service.activate_preset(i)
            await _apply_mcp_snapshot(preset, client_id, prefs, mgmt)
            return service.get_presets()
    raise HTTPException(status_code=404, detail=f"Preset not found: {name}")


# =============================================================================
# Full Settings Bundle
# =============================================================================


@router.get("/{client_id}", response_model=ClientSettings)
async def get_all_settings(
    service: ClientSettingsService = Depends(get_service),
) -> ClientSettings:
    """Get all settings for the client."""
    return service.get_all()


@router.post("/{client_id}/reset", response_model=ClientSettings)
async def reset_all_settings(
    service: ClientSettingsService = Depends(get_service),
) -> ClientSettings:
    """Reset all settings to defaults."""
    return service.reset_all()


__all__ = ["router"]
