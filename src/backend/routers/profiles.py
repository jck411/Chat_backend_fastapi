"""API router for client profile management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..schemas.client_profiles import (
    ClientProfile,
    ClientProfileCreate,
    ClientProfileList,
    ClientProfileUpdate,
)
from ..services.client_profiles import ClientProfileService

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def get_profile_service(request: Request) -> ClientProfileService:
    """Dependency to get the profile service from app state."""
    service = getattr(request.app.state, "client_profile_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Profile service not initialized",
        )
    return service


ProfileServiceDep = Annotated[ClientProfileService, Depends(get_profile_service)]


@router.get("/", response_model=ClientProfileList)
async def list_profiles(service: ProfileServiceDep) -> ClientProfileList:
    """List all client profiles."""
    profiles = await service.list_profiles()
    return ClientProfileList(profiles=profiles)


@router.get("/{profile_id}", response_model=ClientProfile)
async def get_profile(profile_id: str, service: ProfileServiceDep) -> ClientProfile:
    """Get a specific profile by ID."""
    profile = await service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile not found: {profile_id}",
        )
    return profile


@router.post("/", response_model=ClientProfile, status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ClientProfileCreate,
    service: ProfileServiceDep,
) -> ClientProfile:
    """Create a new client profile."""
    profile = ClientProfile(
        profile_id=body.profile_id,
        enabled_servers=body.enabled_servers,
        description=body.description,
    )
    try:
        return await service.create_profile(profile)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.patch("/{profile_id}", response_model=ClientProfile)
async def update_profile(
    profile_id: str,
    body: ClientProfileUpdate,
    service: ProfileServiceDep,
) -> ClientProfile:
    """Update an existing profile."""
    try:
        return await service.update_profile(profile_id, body)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, service: ProfileServiceDep):
    """Delete a profile."""
    deleted = await service.delete_profile(profile_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile not found: {profile_id}",
        )


@router.post("/{profile_id}/servers/{server_id}", response_model=ClientProfile)
async def add_server_to_profile(
    profile_id: str,
    server_id: str,
    service: ProfileServiceDep,
) -> ClientProfile:
    """Add a server to a profile's enabled list."""
    try:
        return await service.add_server(profile_id, server_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete("/{profile_id}/servers/{server_id}", response_model=ClientProfile)
async def remove_server_from_profile(
    profile_id: str,
    server_id: str,
    service: ProfileServiceDep,
) -> ClientProfile:
    """Remove a server from a profile's enabled list."""
    try:
        return await service.remove_server(profile_id, server_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


__all__ = ["router"]
