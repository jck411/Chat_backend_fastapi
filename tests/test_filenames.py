from backend.utils import build_storage_name, slugify_filename


def test_slugify_filename_basic():
    assert slugify_filename("Quarterly Report 2024.pdf") == "quarterly-report-2024"


def test_slugify_filename_handles_empty():
    assert slugify_filename(None) == ""
    assert slugify_filename("") == ""


def test_build_storage_name_with_slug():
    result = build_storage_name("abc123", ".pdf", "My File.pdf")
    assert result == "abc123__my-file.pdf"


def test_build_storage_name_without_slug():
    result = build_storage_name("abc123", ".bin", None)
    assert result == "abc123.bin"
