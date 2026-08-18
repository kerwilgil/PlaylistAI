"""Cobertura del patch de compatibilidad con el limite real de la Search API
de Spotify en Development Mode (maximo 10 resultados por GET /search, ver
CONTEXT.md). Estilo unittest + Mock, igual que el resto de la suite."""

import unittest
from unittest.mock import Mock

import app


def _search_response(n=1):
    return {"tracks": {"items": [{"id": f"id-{i}"} for i in range(n)]}}


class SpotifySearchLimitTests(unittest.TestCase):
    def setUp(self):
        app.SPOTIFY_SEARCH_CACHE.clear()

    def test_spotify_search_never_sends_more_than_10_to_spotify(self):
        sp = Mock()
        sp.search.return_value = _search_response()

        app.spotify_search(sp, "algun query", limit=20)

        sp.search.assert_called_once_with(q="algun query", type="track", limit=10)

    def test_spotify_search_limit_5_stays_intact(self):
        sp = Mock()
        sp.search.return_value = _search_response()

        app.spotify_search(sp, "otro query", limit=5)

        sp.search.assert_called_once_with(q="otro query", type="track", limit=5)

    def test_cache_key_uses_effective_clamped_limit(self):
        """limit=20 y limit=10 deben clampear al mismo valor efectivo (10) y
        por lo tanto compartir la misma entrada de cache -- sin una segunda
        llamada real a Spotify."""
        sp = Mock()
        sp.search.return_value = _search_response()

        app.spotify_search(sp, "mismo query", limit=20)
        app.spotify_search(sp, "mismo query", limit=10)

        sp.search.assert_called_once_with(q="mismo query", type="track", limit=10)
        self.assertIn(("mismo query", 10), app.SPOTIFY_SEARCH_CACHE)

    def test_find_artist_fallback_uses_max_10(self):
        sp = Mock()
        sp.search.return_value = _search_response()

        app.find_artist_fallback(sp, "Algun Artista")

        sp.search.assert_called_once()
        self.assertEqual(sp.search.call_args.kwargs.get("limit"), 10)


if __name__ == "__main__":
    unittest.main()
