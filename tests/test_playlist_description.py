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


class ExtractPlaylistDescriptionTests(unittest.TestCase):
    """Cobertura del FINAL QUALITY PATCH (2026-08-18): validación semántica
    ANTES del guard de longitud. Ver CONTEXT.md -- una descripción "válida"
    en longitud puede seguir siendo basura (el JSON completo de la IA)."""

    def test_a_normal_description_unchanged(self):
        value = "Lo-Fi instrumental para concentración"
        self.assertEqual(app.extract_playlist_description(value), value)

    def test_b_nested_json_string_extracts_description_field(self):
        value = '{"description":"Lo-Fi para trabajar","tracks":[{"name":"x","artist":"y"}]}'
        result = app.extract_playlist_description(value, fallback="mood x")
        self.assertEqual(result, "Lo-Fi para trabajar")

    def test_c_dict_value_extracts_description_field(self):
        result = app.extract_playlist_description({"description": "Lo-Fi"}, fallback="mood x")
        self.assertEqual(result, "Lo-Fi")

    def test_c_dict_without_description_falls_back(self):
        result = app.extract_playlist_description({"tracks": []}, fallback="mood x")
        self.assertEqual(result, "mood x")

    def test_d_json_object_without_description_falls_back_to_mood(self):
        value = '{"tracks":[{"name":"x","artist":"y"}]}'
        result = app.extract_playlist_description(value, fallback="mood x")
        self.assertEqual(result, "mood x")

    def test_e_json_array_falls_back_to_mood(self):
        value = '[{"name":"track"}]'
        result = app.extract_playlist_description(value, fallback="mood x")
        self.assertEqual(result, "mood x")

    def test_f_fenced_json_block_never_reaches_spotify_raw(self):
        value = '```json\n{"description": "Lo-Fi vibes", "tracks": []}\n```'
        result = app.extract_playlist_description(value, fallback="mood x")
        self.assertNotIn("```", result)
        self.assertNotIn('"tracks"', result)
        self.assertEqual(result, "Lo-Fi vibes")

    def test_f_fenced_json_without_description_falls_back(self):
        value = '```json\n{"tracks": []}\n```'
        result = app.extract_playlist_description(value, fallback="mood x")
        self.assertNotIn("```", result)
        self.assertEqual(result, "mood x")

    def test_g_normal_description_over_300_still_truncates_via_length_guard(self):
        """extract_playlist_description() no trunca -- eso sigue siendo trabajo
        de normalize_playlist_description(), llamado despues en el pipeline real."""
        long_value = "Una playlist muy larga. " * 30
        semantic = app.extract_playlist_description(long_value, fallback="mood x")
        self.assertEqual(semantic, long_value)  # sin cambios en esta capa
        final = app.normalize_playlist_description(semantic, fallback="mood x")
        self.assertLessEqual(len(final), app.SPOTIFY_PLAYLIST_DESCRIPTION_MAX)

    def test_h_unicode_description_intact_except_length(self):
        value = "Lo-Fi café 咖啡 🎧 relajante para trabajar"
        result = app.extract_playlist_description(value, fallback="mood x")
        self.assertEqual(result, value)

    def test_none_and_unexpected_type_fall_back_safely(self):
        self.assertEqual(app.extract_playlist_description(None, fallback="mood x"), "mood x")
        self.assertEqual(app.extract_playlist_description(12345, fallback="mood x"), "mood x")

    def test_string_mentioning_tracks_word_in_prose_is_not_touched(self):
        """No es una heuristica destructiva: una descripcion humana real que
        mencione la palabra "tracks" sin forma de JSON no debe tocarse."""
        value = "Una playlist con los mejores tracks para programar"
        self.assertEqual(app.extract_playlist_description(value, fallback="mood x"), value)


class LiveBugDescriptionRegressionTests(unittest.TestCase):
    def test_live_bug_regression_full_ai_response_as_description(self):
        """Regresión exacta del bug observado en el LIVE VALIDATION 50
        (2026-08-18): Spotify recibió como descripción el JSON completo de la
        respuesta de la IA. Simula esa misma forma de respuesta end-to-end."""
        sp = Mock()
        sp.current_user_playlist_create.return_value = {
            "id": "pl1",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"},
        }
        sp.playlist_add_items.return_value = None

        track = {
            "id": "track-1",
            "name": "aruarian dance",
            "artists": [{"name": "Nujabes"}],
            "album": {"images": []},
        }
        # Forma real observada: "description" contiene el objeto completo
        # serializado, incluyendo su propia clave "description" anidada.
        inner_payload = {
            "description": "Lo-fi e instrumental downtempo sin voces",
            "tracks": [{"name": "aruarian dance", "artist": "Nujabes"}],
        }
        broken_ai_payload = json.dumps({
            "description": json.dumps(inner_payload, ensure_ascii=False),
            "tracks": [{"name": "aruarian dance", "artist": "Nujabes"}],
        }, ensure_ascii=False)

        with (
            patch.object(app, "refresh_token_if_needed", return_value=True),
            patch.object(app, "get_sp", return_value=sp),
            patch.object(app, "ai_config_error", return_value=None),
            patch.object(app, "call_ai", return_value=broken_ai_payload),
            patch.object(app, "find_spotify_track", return_value=track),
        ):
            client = app.app.test_client()
            response = client.post(
                "/api/create",
                json={"name": "Test", "mood": "Lo-Fi instrumental sin voces", "count": 1},
            )
            response.get_data(as_text=True)

        self.assertTrue(sp.current_user_playlist_create.called)
        sent_description = sp.current_user_playlist_create.call_args.kwargs["description"]
        self.assertNotIn('"tracks"', sent_description)
        self.assertNotIn("{", sent_description)
        self.assertEqual(sent_description, "Lo-fi e instrumental downtempo sin voces")


if __name__ == "__main__":
    unittest.main()
