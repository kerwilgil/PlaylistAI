"""Cobertura del guard de descripcion de playlist (ver CONTEXT.md, "Guard de
compatibilidad: descripcion de playlist"). Estilo unittest + Mock, igual que
el resto de la suite."""

import json
import unittest
from unittest.mock import Mock, patch

import app


class NormalizePlaylistDescriptionTests(unittest.TestCase):
    def test_a_short_description_unchanged(self):
        value = "Una playlist tranquila para trabajar."
        self.assertEqual(app.normalize_playlist_description(value), value)

    def test_b_description_over_limit_is_clamped(self):
        long_value = "x" * 500
        result = app.normalize_playlist_description(long_value)
        self.assertLessEqual(len(result), app.SPOTIFY_PLAYLIST_DESCRIPTION_MAX)
        self.assertTrue(result.endswith("..."))

    def test_c_description_exactly_at_limit_stays_intact(self):
        exact_value = "x" * app.SPOTIFY_PLAYLIST_DESCRIPTION_MAX
        result = app.normalize_playlist_description(exact_value)
        self.assertEqual(result, exact_value)
        self.assertEqual(len(result), app.SPOTIFY_PLAYLIST_DESCRIPTION_MAX)

    def test_d_none_empty_and_unexpected_type_are_safe(self):
        self.assertEqual(app.normalize_playlist_description(None, fallback="mood x"), "mood x")
        self.assertEqual(app.normalize_playlist_description("", fallback="mood x"), "mood x")
        self.assertEqual(app.normalize_playlist_description(12345, fallback="mood x"), "mood x")
        # sin fallback tampoco debe fallar -- string vacio es un resultado seguro.
        self.assertEqual(app.normalize_playlist_description(None), "")
        self.assertEqual(app.normalize_playlist_description(None, fallback=None), "")

    def test_e_unicode_does_not_error_and_respects_limit(self):
        unicode_value = ("Lo-Fi café 咖啡 🎧 relajante " * 20) + "☕" * 50
        result = app.normalize_playlist_description(unicode_value)
        self.assertLessEqual(len(result), app.SPOTIFY_PLAYLIST_DESCRIPTION_MAX)

    def test_whitespace_is_normalized_and_stripped(self):
        messy = "  Playlist   con   \n\n espacios  raros  \t "
        result = app.normalize_playlist_description(messy)
        self.assertEqual(result, "Playlist con espacios raros")


class ApiCreateDescriptionGuardTests(unittest.TestCase):
    def test_f_long_ai_description_reaches_spotify_sanitized_not_raw(self):
        """La creacion de playlist debe usar la version saneada de la
        descripcion, nunca la original que vino de la IA sin procesar."""
        sp = Mock()
        sp.current_user_playlist_create.return_value = {
            "id": "pl1",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"},
        }
        sp.playlist_add_items.return_value = None

        long_description = "y" * 500
        track = {
            "id": "track-1",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "album": {"images": []},
        }
        ai_payload = json.dumps({
            "description": long_description,
            "tracks": [{"name": "Song", "artist": "Artist"}],
        })

        with (
            patch.object(app, "refresh_token_if_needed", return_value=True),
            patch.object(app, "get_sp", return_value=sp),
            patch.object(app, "ai_config_error", return_value=None),
            patch.object(app, "call_ai", return_value=ai_payload),
            patch.object(app, "find_spotify_track", return_value=track),
        ):
            client = app.app.test_client()
            response = client.post(
                "/api/create",
                json={"name": "Test", "mood": "mood de prueba", "count": 1},
            )
            response.get_data(as_text=True)  # fuerza a consumir el generador NDJSON

        self.assertTrue(sp.current_user_playlist_create.called)
        sent_description = sp.current_user_playlist_create.call_args.kwargs["description"]
        self.assertNotEqual(sent_description, long_description)
        self.assertLessEqual(len(sent_description), app.SPOTIFY_PLAYLIST_DESCRIPTION_MAX)


if __name__ == "__main__":
    unittest.main()
