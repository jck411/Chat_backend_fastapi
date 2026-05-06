"""Custom logging handler utilities."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from backend.services.time_context import EASTERN_TIMEZONE


class DateStampedFileHandler(logging.FileHandler):
    """File handler that stores logs under date-stamped directories."""

    def __init__(
        self,
        filename: str | None = None,
        *,
        directory: str | Path | None = None,
        prefix: str | None = None,
        encoding: str | None = "utf-8",
        mode: str = "a",
        delay: bool = False,
        errors: Optional[str] = None,
        current_time: datetime | None = None,
    ) -> None:
        timestamp = (current_time or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )

        base_dir: Path
        base_prefix: str

        if filename:
            filename_path = Path(filename)
            if filename_path.suffix:
                base_dir = filename_path.parent if filename_path.parent else Path.cwd()
                derived = filename_path.stem or "app"
                base_prefix = prefix or derived
            else:
                base_dir = filename_path
                base_prefix = prefix or "app"
        elif directory:
            base_dir = Path(directory)
            base_prefix = prefix or "app"
        else:
            base_dir = Path("logs/app")
            base_prefix = prefix or "app"

        base_dir = base_dir.resolve()
        local_time = timestamp.astimezone(EASTERN_TIMEZONE)
        tz_abbr = local_time.tzname() or "ET"
        date_folder = local_time.strftime("%Y-%m-%d")
        human_time = local_time.strftime("%Y-%m-%d_%H-%M-%S")
        file_name = f"{base_prefix}_{human_time}_{tz_abbr}.log"
        log_path = (base_dir / date_folder / file_name).resolve()

        log_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            log_path,
            mode=mode,
            encoding=encoding,
            delay=delay,
            errors=errors,
        )


def cleanup_old_logs(
    log_directories: list[str | Path],
    retention_hours: int,
    logger: logging.Logger | None = None,
) -> tuple[int, int]:
    """
    Delete log files older than the specified retention period.

    Args:
        log_directories: List of directories to clean (e.g., ['logs/app', 'logs/conversations'])
        retention_hours: Files older than this many hours will be deleted (0 = disabled)
        logger: Optional logger for reporting cleanup activity

    Returns:
        Tuple of (files_deleted, errors_encountered)
    """
    if retention_hours <= 0:
        return (0, 0)

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    files_deleted = 0
    errors = 0

    for directory in log_directories:
        dir_path = Path(directory).resolve()
        if not dir_path.exists():
            continue

        try:
            # Recursively find all .log files
            for log_file in dir_path.rglob("*.log"):
                try:
                    # Get file modification time
                    mtime = datetime.fromtimestamp(
                        log_file.stat().st_mtime, tz=timezone.utc
                    )

                    if mtime < cutoff_time:
                        log_file.unlink()
                        files_deleted += 1
                        if logger:
                            logger.debug(f"Deleted old log file: {log_file}")
                except (OSError, PermissionError) as e:
                    errors += 1
                    if logger:
                        logger.warning(f"Failed to delete {log_file}: {e}")

            # Clean up empty date directories
            for date_dir in dir_path.iterdir():
                if date_dir.is_dir() and not any(date_dir.iterdir()):
                    try:
                        date_dir.rmdir()
                        if logger:
                            logger.debug(f"Removed empty directory: {date_dir}")
                    except OSError:
                        pass  # Ignore errors removing directories

        except Exception as e:
            errors += 1
            if logger:
                logger.error(f"Error cleaning logs in {dir_path}: {e}")

    if logger and files_deleted > 0:
        logger.info(
            f"Log cleanup complete: {files_deleted} file(s) deleted, "
            f"{errors} error(s) encountered"
        )

    return (files_deleted, errors)


__all__ = ["DateStampedFileHandler", "cleanup_old_logs"]
