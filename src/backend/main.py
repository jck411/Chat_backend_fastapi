"""CLI entrypoint for running the FastAPI app with uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Run the ASGI server."""

    uvicorn.run(
        "backend.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
