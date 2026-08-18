"""Cobertura del loop de resolución incremental por rondas de
`stream_resolve_from_prompt` (ver CONTEXT.md, sección "Resolución incremental
por rondas"). Estilo unittest + Mock, igual que el resto de la suite."""

import json
import re
import time
import unittest
from unittest.mock import Mock, patch

from spotipy.exceptions import SpotifyException

import app


def _spotify_track(track_id, name, artist):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"images": []},
    }


def _tracks_payload(names, with_description=False):
    """Construye el JSON crudo que normalmente devolvería la IA para una ronda."""
    tracks = [{"name": name, "artist": f"Artist-{name}"} for name in names]
    payload = {"tracks": tracks}
    if with_description:
        payload = {"description": "Una playlist de prueba", **payload}
    return json.dumps(payload, ensure_ascii=False)


def _always_found(sp, name, artist=None, exclude_ids=None, mood=None, filtered_out=None):
    """`find_spotify_track` falso: siempre resuelve exitosamente."""
    return _spotify_track(f"id-{name}", name, artist or "Artista")


def _found_up_to(valid_max):
    """`find_spotify_track` falso: solo resuelve nombres 'Item<N>' con N<=valid_max."""
    def _find(sp, name, artist=None, exclude_ids=None, mood=None, filtered_out=None):
        match = re.search(r"(\d+)$", name or "")
        if not match:
            return None
        if int(match.group(1)) <= valid_max:
            return _spotify_track(f"id-{name}", name, artist or "Artista")
        return None
    return _find


class IncrementalRoundsTests(unittest.TestCase):
    def test_a_exact_count_no_extra_rounds(self):
        """10 pedidas, Spotify encuentra las 10 -> 10/10 sin rondas de más."""
        names = [f"Song{i}" for i in range(1, 11)]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[_tracks_payload(names, True)]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_always_found),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 10, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(call_ai.call_count, 1)
        self.assertEqual(len(result["track_ids"]), 10)
        self.assertEqual(len(set(result["track_ids"])), 10)
        self.assertEqual(result["stop_reason"], "completed")
        self.assertFalse(result["fatal"])

    def test_b_incremental_rounds_reach_fifty(self):
        """50 pedidas, primera ronda parcial -> rondas incrementales hasta 50."""
        round0 = [f"Song{i}" for i in range(1, 19)]     # 18
        round1 = [f"Song{i}" for i in range(19, 37)]    # 18 (total 36)
        round2 = [f"Song{i}" for i in range(37, 64)]    # sobra, se corta al llegar a 50
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[
                _tracks_payload(round0, True),
                _tracks_payload(round1),
                _tracks_payload(round2),
            ]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_always_found),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 50, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(len(result["track_ids"]), 50)
        self.assertEqual(len(set(result["track_ids"])), 50)
        self.assertEqual(call_ai.call_count, 3)
        self.assertEqual(result["stop_reason"], "completed")

    def test_c_partial_result_when_only_42_valid(self):
        """50 pedidas pero solo existen 42 válidas -> resultado parcial, sin loop infinito."""
        items = lambda start, end: [f"Item{i}" for i in range(start, end)]
        batches = [
            items(1, 19),    # 1-18 validos -> 18
            items(19, 37),   # 19-36 validos -> +18 = 36
            items(37, 55),   # 37-42 validos (+6=42), 43-54 invalidos
            items(55, 67),   # todos invalidos -> progreso 0 (1/2)
            items(67, 79),   # todos invalidos -> progreso 0 (2/2) -> corta
        ]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[
                _tracks_payload(batches[0], True),
                _tracks_payload(batches[1]),
                _tracks_payload(batches[2]),
                _tracks_payload(batches[3]),
                _tracks_payload(batches[4]),
            ]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_found_up_to(42)),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 50, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(len(result["track_ids"]), 42)
        self.assertEqual(len(set(result["track_ids"])), 42)
        self.assertEqual(call_ai.call_count, 5)
        self.assertEqual(result["stop_reason"], "no_progress")
        self.assertFalse(result["fatal"])

    def test_d_duplicates_from_ai_are_not_added_twice(self):
        """IA devuelve duplicados -> no se agregan Spotify IDs repetidos."""
        names = ["Song1", "Song1", "Song2", "Song3", "Song4", "Song5"]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[_tracks_payload(names, True)]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_always_found),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 5, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(call_ai.call_count, 1)
        self.assertEqual(len(result["track_ids"]), 5)
        self.assertEqual(len(set(result["track_ids"])), 5)

    def test_e_songs_already_in_playlist_are_discarded_and_dont_count(self):
        """IA sugiere canciones ya en la playlist -> se descartan, no cuentan para el target."""
        def _find(sp, name, artist=None, exclude_ids=None, mood=None, filtered_out=None):
            if name == "AlreadyIn":
                return _spotify_track("exist-1", name, artist)
            return _spotify_track(f"id-{name}", name, artist)

        names = ["AlreadyIn", "Song2", "Song3", "Song4"]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[_tracks_payload(names, True)]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_find),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 3, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, {"exist-1"}, result,
            ))

        self.assertEqual(call_ai.call_count, 1)
        self.assertEqual(len(result["track_ids"]), 3)
        self.assertNotIn("exist-1", result["track_ids"])
        self.assertEqual(result["stop_reason"], "completed")

    def test_f_two_consecutive_zero_progress_rounds_stop_the_loop(self):
        """Una ronda en 0 permite reintentar; 2 seguidas en 0 cortan el loop."""
        items = lambda start, end: [f"Item{i}" for i in range(start, end)]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[
                _tracks_payload(items(1, 7), True),   # Item1, Item2 validos (<=2)
                _tracks_payload(items(7, 13)),        # todos invalidos -> progreso 0 (1/2)
                _tracks_payload(items(13, 19)),       # todos invalidos -> progreso 0 (2/2) -> corta
            ]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_found_up_to(2)),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 5, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(call_ai.call_count, 3)
        self.assertEqual(len(result["track_ids"]), 2)
        self.assertEqual(result["stop_reason"], "no_progress")
        self.assertFalse(result["fatal"])

    def test_g_deadline_exhausted_stops_cleanly_with_partial_results(self):
        """Deadline agotado -> resultados parciales conservados, stop_reason=deadline,
        el generador termina limpio (sin excepción sin manejar)."""
        names = [f"Song{i}" for i in range(1, 4)]  # solo 3 de las 10 pedidas
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[_tracks_payload(names, True)]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_always_found),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            # Deadline corto (5s) < presupuesto estimado por ronda para "anthropic"
            # (60s de requests.post + margen): la ronda 0 siempre se intenta, pero
            # la 2da no arranca porque no alcanzaría el tiempo.
            events = list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 10, "anthropic", "claude-sonnet-5",
                time.monotonic() + 5, set(), result,
            ))

        self.assertEqual(call_ai.call_count, 1)
        self.assertEqual(len(result["track_ids"]), 3)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertFalse(result["fatal"])
        self.assertTrue(events)

    def test_h_spotify_429_sets_fatal_and_stops_without_hanging(self):
        """Spotify 429 en medio de una ronda -> fatal seteado, no queda colgado."""
        def _raise_rate_limited(sp, name, artist=None, exclude_ids=None, mood=None, filtered_out=None):
            raise SpotifyException(429, -1, "rate limited")

        names = [f"Song{i}" for i in range(1, 6)]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[_tracks_payload(names, True)]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_raise_rate_limited),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 5, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(call_ai.call_count, 1)
        self.assertIn("429", result["fatal"].get("error", ""))
        self.assertEqual(result["track_ids"], [])

    def test_i_invalid_json_recovers_in_a_later_round_without_crashing(self):
        """Respuesta de IA invalida/truncada -> el pipeline se recupera cuando puede
        (reintento existente) y nunca tira una excepción sin manejar."""
        round0 = ["Song1", "Song2"]
        round1_recovered = ["Song3", "Song4", "Song5"]
        result = app._new_result("mood")

        with (
            patch.object(app, "call_ai", side_effect=[
                _tracks_payload(round0, True),
                "esto no es JSON valido {",             # ronda 1, intento 1: invalido
                _tracks_payload(round1_recovered),        # ronda 1, intento 2 (retry): valido
            ]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_always_found),
            patch.object(app, "find_artist_fallback", return_value=None),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", "mood", 5, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(call_ai.call_count, 3)
        self.assertEqual(len(result["track_ids"]), 5)
        self.assertFalse(result["fatal"])
        self.assertEqual(result["stop_reason"], "completed")


class BatchSizeAndRoundLimitTests(unittest.TestCase):
    def test_round_batch_size_pure_values(self):
        self.assertEqual(app._round_batch_size(0), 0)
        self.assertEqual(app._round_batch_size(1), 6)    # piso: vale la pena la llamada
        self.assertEqual(app._round_batch_size(20), 18)  # techo de lote
        self.assertEqual(app._round_batch_size(50), 18)  # nunca crece sin control

    def test_max_rounds_grows_with_count_but_has_a_ceiling(self):
        self.assertGreaterEqual(app._max_rounds_for_count(50), app._max_rounds_for_count(10))
        self.assertLessEqual(app._max_rounds_for_count(50), 12)
        self.assertGreaterEqual(app._max_rounds_for_count(5), 4)


if __name__ == "__main__":
    unittest.main()
