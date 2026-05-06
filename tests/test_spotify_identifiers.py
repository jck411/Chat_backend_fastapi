"""Tests for Spotify identifier normalization utilities."""

import pytest

from backend.services.spotify_auth.identifiers import (
    normalize_context_uri,
    normalize_playlist_id,
    normalize_track_uri,
)


class TestNormalizeTrackUri:
    """Tests for normalize_track_uri function."""

    def test_track_url_to_uri(self):
        """Convert track URL to URI."""
        url = "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6"
        result = normalize_track_uri(url)
        assert result == "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"

    def test_track_url_with_query_params(self):
        """Convert track URL with query params to URI."""
        url = "https://open.spotify.com/track/6rqhFgbbKwnb9MLmUQDhG6?si=abc123"
        result = normalize_track_uri(url)
        assert result == "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"

    def test_track_uri_passthrough(self):
        """URI format passes through unchanged."""
        uri = "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"
        result = normalize_track_uri(uri)
        assert result == uri

    def test_track_id_to_uri(self):
        """Convert bare track ID to URI."""
        track_id = "6rqhFgbbKwnb9MLmUQDhG6"
        result = normalize_track_uri(track_id)
        assert result == "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"


class TestNormalizePlaylistId:
    """Tests for normalize_playlist_id function."""

    def test_playlist_url_to_id(self):
        """Extract playlist ID from URL."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        result = normalize_playlist_id(url)
        assert result == "37i9dQZF1DXcBWIGoYBM5M"

    def test_playlist_url_with_query_params(self):
        """Extract playlist ID from URL with query params."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=xyz789"
        result = normalize_playlist_id(url)
        assert result == "37i9dQZF1DXcBWIGoYBM5M"

    def test_playlist_uri_to_id(self):
        """Extract playlist ID from URI."""
        uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        result = normalize_playlist_id(uri)
        assert result == "37i9dQZF1DXcBWIGoYBM5M"

    def test_playlist_id_passthrough(self):
        """Bare playlist ID passes through unchanged."""
        playlist_id = "37i9dQZF1DXcBWIGoYBM5M"
        result = normalize_playlist_id(playlist_id)
        assert result == playlist_id


class TestNormalizeContextUri:
    """Tests for normalize_context_uri function."""

    def test_playlist_url_to_uri(self):
        """Convert playlist URL to URI with type."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        uri, context_type = normalize_context_uri(url)
        assert uri == "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        assert context_type == "playlist"

    def test_album_url_to_uri(self):
        """Convert album URL to URI with type."""
        url = "https://open.spotify.com/album/6DEjYFkNZh67HP7R9PSZvv"
        uri, context_type = normalize_context_uri(url)
        assert uri == "spotify:album:6DEjYFkNZh67HP7R9PSZvv"
        assert context_type == "album"

    def test_artist_url_to_uri(self):
        """Convert artist URL to URI with type."""
        url = "https://open.spotify.com/artist/3WrFJ7ztbogyGnTHbHJFl2"
        uri, context_type = normalize_context_uri(url)
        assert uri == "spotify:artist:3WrFJ7ztbogyGnTHbHJFl2"
        assert context_type == "artist"

    def test_playlist_uri_passthrough(self):
        """Playlist URI format passes through with type."""
        uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        result_uri, context_type = normalize_context_uri(uri)
        assert result_uri == uri
        assert context_type == "playlist"

    def test_album_uri_passthrough(self):
        """Album URI format passes through with type."""
        uri = "spotify:album:6DEjYFkNZh67HP7R9PSZvv"
        result_uri, context_type = normalize_context_uri(uri)
        assert result_uri == uri
        assert context_type == "album"

    def test_artist_uri_passthrough(self):
        """Artist URI format passes through with type."""
        uri = "spotify:artist:3WrFJ7ztbogyGnTHbHJFl2"
        result_uri, context_type = normalize_context_uri(uri)
        assert result_uri == uri
        assert context_type == "artist"

    def test_url_with_query_params(self):
        """Handle URLs with query parameters."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=test123"
        uri, context_type = normalize_context_uri(url)
        assert uri == "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        assert context_type == "playlist"

    def test_invalid_context_raises_error(self):
        """Invalid context raises ValueError."""
        with pytest.raises(ValueError, match="Invalid context URI"):
            normalize_context_uri("invalid_format")

    def test_track_uri_raises_error(self):
        """Track URI raises error (not a valid context)."""
        with pytest.raises(ValueError, match="Invalid context URI"):
            normalize_context_uri("spotify:track:6rqhFgbbKwnb9MLmUQDhG6")
