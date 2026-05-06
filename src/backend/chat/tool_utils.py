"""Shared utility functions for tool processing."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def compact_tool_digest(
    digest: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Create a compact version of a capability digest for efficient transmission.
    
    This function filters and compacts tool information by:
    - Removing invalid or empty entries
    - Keeping only essential fields (name, description, parameters, server, score)
    - Normalizing data types
    
    Args:
        digest: Raw capability digest with tool information per context.
            Example structure:
            {
                "calendar": [
                    {
                        "name": "calendar__list",
                        "description": "List calendar events",
                        "parameters": {"type": "object", "properties": {...}},
                        "server": "calendar-server",
                        "score": 0.9
                    }
                ],
                "tasks": [...]
            }
        
    Returns:
        Compacted digest with cleaned and normalized tool data
    """
    if not digest:
        return {}
    
    compact: dict[str, list[dict[str, Any]]] = {}
    
    for context, entries in digest.items():
        if not isinstance(entries, (list, tuple)):
            continue
        
        filtered: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
                
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            
            compact_entry: dict[str, Any] = {"name": name.strip()}
            
            description = entry.get("description")
            if isinstance(description, str) and description.strip():
                compact_entry["description"] = description.strip()
            
            parameters = entry.get("parameters")
            if isinstance(parameters, dict) and parameters:
                compact_entry["parameters"] = parameters
            
            server = entry.get("server")
            if isinstance(server, str) and server.strip():
                compact_entry["server"] = server.strip()
            
            score = entry.get("score")
            if isinstance(score, (int, float)):
                compact_entry["score"] = float(score)
            
            filtered.append(compact_entry)
        
        if filtered:
            compact[context] = filtered
    
    return compact


__all__ = ["compact_tool_digest"]
