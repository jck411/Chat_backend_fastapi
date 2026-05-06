import pathlib
import signal
import sys

import psutil
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture(scope="session", autouse=True)
def cleanup_processes():
    """Kill any lingering child processes after all tests complete."""
    yield

    # Kill any child processes (MCP servers) that are still running
    import os
    import time

    try:
        current_process = psutil.Process()
        children = current_process.children(recursive=True)

        if children:
            print(f"\n[CLEANUP] Found {len(children)} child processes, terminating...")

        for child in children:
            try:
                print(f"[CLEANUP] Terminating process {child.pid} ({child.name()})")
                child.send_signal(signal.SIGTERM)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Give processes a moment to terminate gracefully
        time.sleep(0.5)

        # Force kill any that are still alive
        for child in children:
            try:
                if child.is_running():
                    print(f"[CLEANUP] Force killing process {child.pid}")
                    child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Wait a bit more for processes to actually die
        time.sleep(0.3)

        print("[CLEANUP] Process cleanup complete - forcing exit")
    except Exception as e:
        print(f"[CLEANUP] Error during cleanup: {e}")
    finally:
        # Always force exit to avoid pytest hanging on cleanup
        os._exit(0)
