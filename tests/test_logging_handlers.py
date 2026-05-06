import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.backend.logging_handlers import DateStampedFileHandler, cleanup_old_logs


def test_date_stamped_file_handler_creates_expected_path(tmp_path) -> None:
    current = datetime(2024, 5, 26, 12, 34, 56, tzinfo=timezone.utc)
    handler = DateStampedFileHandler(
        directory=tmp_path / "app",
        prefix="app",
        current_time=current,
        encoding="utf-8",
    )
    try:
        expected_dir = (tmp_path / "app" / "2024-05-26").resolve()
        expected_file = expected_dir / "app_2024-05-26_08-34-56_EDT.log"
        file_path = Path(handler.baseFilename)
        assert file_path == expected_file
        assert file_path.exists()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        contents = file_path.read_text(encoding="utf-8")
        assert "hello world" in contents
    finally:
        handler.close()


def test_handler_derives_prefix_from_filename(tmp_path) -> None:
    current = datetime(2023, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    handler = DateStampedFileHandler(
        filename=tmp_path / "custom.log",
        prefix=None,
        current_time=current,
    )
    try:
        expected_dir = (tmp_path / "2023-01-01").resolve()
        expected_file = expected_dir / "custom_2023-01-01_22-04-05_EST.log"
        file_path = Path(handler.baseFilename)
        assert file_path == expected_file
        assert file_path.exists()
    finally:
        handler.close()


def test_cleanup_old_logs(tmp_path) -> None:
    """Test that old log files are deleted based on retention hours."""
    log_dir = tmp_path / "logs" / "app"
    log_dir.mkdir(parents=True)

    # Create test log files with different ages
    now = datetime.now(timezone.utc)

    # Old file (3 days old) - should be deleted
    old_file = log_dir / "old_log.log"
    old_file.write_text("old content")
    old_time = (now - timedelta(days=3)).timestamp()
    old_file.touch()
    old_file.stat().st_mtime  # Verify it's accessible
    # Set modification time using os.utime
    import os

    os.utime(old_file, (old_time, old_time))

    # Recent file (1 day old) - should NOT be deleted with 48h retention
    recent_file = log_dir / "recent_log.log"
    recent_file.write_text("recent content")
    recent_time = (now - timedelta(days=1)).timestamp()
    recent_file.touch()
    os.utime(recent_file, (recent_time, recent_time))

    # Current file - should NOT be deleted
    current_file = log_dir / "current_log.log"
    current_file.write_text("current content")

    # Test with 48 hours retention
    files_deleted, errors = cleanup_old_logs([log_dir], retention_hours=48)

    assert files_deleted == 1, f"Expected 1 file deleted, got {files_deleted}"
    assert errors == 0, f"Expected no errors, got {errors}"
    assert not old_file.exists(), "Old file should be deleted"
    assert recent_file.exists(), "Recent file should still exist"
    assert current_file.exists(), "Current file should still exist"


def test_cleanup_old_logs_disabled(tmp_path) -> None:
    """Test that cleanup does nothing when retention_hours is 0."""
    log_dir = tmp_path / "logs" / "app"
    log_dir.mkdir(parents=True)

    old_file = log_dir / "old_log.log"
    old_file.write_text("content")

    # Set to very old
    import os

    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).timestamp()
    os.utime(old_file, (old_time, old_time))

    # Test with retention disabled (0 hours)
    files_deleted, errors = cleanup_old_logs([log_dir], retention_hours=0)

    assert files_deleted == 0, "No files should be deleted when retention is disabled"
    assert errors == 0
    assert old_file.exists(), "File should still exist when cleanup is disabled"


def test_cleanup_old_logs_removes_empty_directories(tmp_path) -> None:
    """Test that empty date directories are removed after cleanup."""
    log_dir = tmp_path / "logs" / "app"
    date_dir = log_dir / "2024-01-01"
    date_dir.mkdir(parents=True)

    old_file = date_dir / "old_log.log"
    old_file.write_text("content")

    # Set to very old
    import os

    old_time = (datetime.now(timezone.utc) - timedelta(days=100)).timestamp()
    os.utime(old_file, (old_time, old_time))

    files_deleted, errors = cleanup_old_logs([log_dir], retention_hours=48)

    assert files_deleted == 1
    assert not old_file.exists(), "Old file should be deleted"
    assert not date_dir.exists(), "Empty date directory should be removed"
