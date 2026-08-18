"""Cobertura del fix de restricciones duras vs preferencias suaves (ver
CONTEXT.md, "Restricciones duras vs preferencias suaves"). Estilo unittest +
Mock, igual que el resto de la suite (`tests/test_incremental_rounds.py`)."""

import json
import time
import unittest
from unittest.mock import Mock, patch

import app


def _spotify_track(track_id, name, artist):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"images": []},
    }


def _tracks_payload(names_artists, with_description=False):
    """`names_artists`: lista de (name, artist). Construye el JSON crudo que
    normalmente devolvería la IA para una ronda."""
    tracks = [{"name": n, "artist": a} for n, a in names_artists]
    payload = {"tracks": tracks}
    if with_description:
        payload = {"description": "Una playlist de prueba", **payload}
    return json.dumps(payload, ensure_ascii=False)


class DetectHardConstraintsTests(unittest.TestCase):
    def test_a_lofi_instrumental_activates_filter_without_electronic_context(self):
        """Caso A: mood Lo-Fi (sin género electrónico) con 'instrumental'/'sin
        voces' SÍ debe activar la restricción dura de instrumental -- a
        diferencia del comportamiento roto donde exigía además contexto de
        género electrónico."""
        mood = "Lo-Fi instrumental sin voces para trabajar y concentrarse"
        constraints = app.detect_hard_constraints(mood)

        self.assertTrue(constraints["instrumental"])
        self.assertFalse(constraints["electronic_context"])
        self.assertTrue(constraints["any"])

        # NOTA: `normalize_search_text` elimina contenido entre paréntesis
        # (gotcha documentado en CONTEXT.md), así que un "(feat. X)" real de
        # Spotify no llegaría al filtro. Se usa un formato sin paréntesis
        # ("Vocal Edit"), consistente con lo que la heurística SÍ puede ver.
        sp = Mock()
        vocal_candidate = _spotify_track("id-1", "Midnight Feels Vocal Edit", "Nujabes Tribute")
        self.assertFalse(app.track_allowed_by_prompt(sp, vocal_candidate, mood))

        instrumental_candidate = _spotify_track("id-2", "Rainy Study Loop", "Nujabes Tribute")
        self.assertTrue(app.track_allowed_by_prompt(sp, instrumental_candidate, mood))

    def test_e_simple_work_mood_does_not_over_apply_instrumental_filter(self):
        """Caso E: 'Más canciones Lofi Work' no menciona instrumental/voces
        explícitamente -- NO debe activar la restricción dura, y candidatos
        con voz normales deben pasar sin ser rechazados."""
        mood = "Más canciones Lofi Work"
        constraints = app.detect_hard_constraints(mood)

        self.assertFalse(constraints["instrumental"])
        self.assertFalse(constraints["any"])

        sp = Mock()
        vocal_candidate = _spotify_track("id-3", "Coffee Shop Vibes Vocal Edit", "LoFi Girl Sessions")
        self.assertTrue(app.track_allowed_by_prompt(sp, vocal_candidate, mood))

    def test_soft_preferences_alone_never_trigger_instrumental(self):
        """Los términos de preferencia suave (trabajo/enfoque/concentración/
        estudiar/focus/programar) ya NO deben, por sí solos, activar la
        restricción dura -- ese era el diseño original mezclado que se
        corrige en este fix."""
        for mood in [
            "Programar sin distracciones toda la tarde",
            "Playlist para enfocarme y estudiar química",
            "Deep house para concentración en la oficina",
        ]:
            constraints = app.detect_hard_constraints(mood)
            self.assertFalse(constraints["instrumental"], msg=f"mood={mood!r}")


class FallbackNeverViolatesHardConstraintTests(unittest.TestCase):
    def test_b_exact_and_fallback_reject_vocal_track(self):
        """Caso B: mood con hard constraint instrumental/sin voces.
        `find_spotify_track` no encuentra el título exacto (Spotify no lo
        tiene); `find_artist_fallback` encuentra una canción real del mismo
        artista pero con 'feat.' en el título -> NO debe agregarse por
        ninguna de las dos rutas."""
        mood = "Chillhop instrumental, sin voces, energía baja para concentrarse"
        sp = Mock()
        # Formato sin paréntesis a propósito: `normalize_search_text` elimina
        # contenido entre paréntesis antes del chequeo de rejected_terms (ver
        # gotcha documentado en CONTEXT.md).
        vocal_track = _spotify_track("id-vocal", "Late Night Talk Vocal Version", "Idealis")

        # find_spotify_track: no hay match exacto real en Spotify.
        with patch.object(app, "spotify_search", return_value=[]):
            exact = app.find_spotify_track(sp, "Nonexistent Title", "Idealis", mood=mood)
        self.assertIsNone(exact)

        # find_artist_fallback: sí hay canciones del artista, pero la única
        # candidata viola la restricción dura (tiene "feat." en el título).
        with patch.object(app, "spotify_search", return_value=[vocal_track]):
            fallback = app.find_artist_fallback(sp, "Idealis", mood=mood)
        self.assertIsNone(fallback)

    def test_fallback_accepts_instrumental_track_from_same_artist(self):
        """Control positivo: si el fallback SÍ respeta la restricción dura,
        debe devolverse con normalidad."""
        mood = "Chillhop instrumental, sin voces"
        sp = Mock()
        instrumental_track = _spotify_track("id-clean", "Quiet Study Loop", "Idealis")

        with patch.object(app, "spotify_search", return_value=[instrumental_track]):
            fallback = app.find_artist_fallback(sp, "Idealis", mood=mood)
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["id"], "id-clean")


class RoundPromptReinforcesConstraintsTests(unittest.TestCase):
    def test_c_round_prompt_repeats_instrumental_constraint_in_later_rounds(self):
        """Caso C: el prompt de una ronda posterior (round_index > 0) sigue
        mencionando la restricción de instrumental/sin voces -- no se
        'olvida' al pedir reemplazos."""
        mood = "Chillhop instrumental, sin voces, para trabajar"
        constraints = app.detect_hard_constraints(mood)

        prompt_round0 = app._round_prompt("Mi Playlist", mood, 20, 10, 0, [], [], constraints)
        prompt_round1 = app._round_prompt("Mi Playlist", mood, 20, 10, 1, ["Idealis – Quiet Loop"], ["Idealis – Bad Take"], constraints)

        for prompt in (prompt_round0, prompt_round1):
            lowered = prompt.lower()
            self.assertIn("restricciones obligatorias", lowered)
            self.assertIn("instrumental", lowered)
            self.assertIn("no las relajes", lowered)

    def test_round_prompt_without_constraints_has_no_hard_constraint_block(self):
        mood = "Más canciones Lofi Work"
        constraints = app.detect_hard_constraints(mood)
        prompt = app._round_prompt("Mi Playlist", mood, 20, 10, 0, [], [], constraints)
        self.assertNotIn("RESTRICCIONES OBLIGATORIAS", prompt)


class PartialResultNeverViolatesHardConstraintTests(unittest.TestCase):
    def test_d_partial_result_excludes_all_violating_tracks(self):
        """Caso D: de 20 pedidas, solo 17 candidatos reales no violan la
        restricción dura; los otros 3 siempre resuelven a un track que la
        viola o no se pueden resolver. El resultado final debe ser 17/20,
        stop_reason parcial, y NINGUNO de los 3 tracks que violan la
        restricción debe terminar en track_ids."""
        mood = "Chillhop instrumental, sin voces, para concentrarse"
        result = app._new_result(mood)

        valid_names = [f"Clean{i}" for i in range(1, 18)]     # 17 validas
        violating_names = ["Vocal1", "Vocal2", "Vocal3"]       # 3 violan la constraint

        def _find_spotify_track(sp, name, artist=None, exclude_ids=None, mood=None, filtered_out=None):
            if name in violating_names:
                # Se "encuentra" en Spotify pero el filtro de prompt la rechaza
                # -- comportamiento real de find_spotify_track con mood seteado.
                # Sin paréntesis a propósito (ver gotcha de normalize_search_text
                # en CONTEXT.md: "(feat. X)" real de Spotify no llegaría al filtro).
                track = _spotify_track(f"id-{name}", f"{name} Vocal Version", artist or "Artista")
                if mood and not app.track_allowed_by_prompt(sp, track, mood):
                    if filtered_out is not None:
                        filtered_out.append(app.candidate_label(track))
                    return None
                return track
            if name in valid_names:
                return _spotify_track(f"id-{name}", name, artist or "Artista")
            return None

        def _find_artist_fallback(sp, artist, exclude_ids=None, mood=None):
            # El fallback tampoco logra resolver un reemplazo limpio para las
            # 3 conflictivas -- simula que no hay alternativa real disponible.
            return None

        # Ronda 0 pide 20 (oversampling ceil(20*1.4)=28 -> tope 18), incluye
        # las 3 violadoras + válidas; rondas siguientes reintentan sin éxito
        # para las 3 pendientes hasta que el loop corta por falta de progreso.
        round0 = [(n, f"Artist-{n}") for n in (valid_names[:15] + violating_names)]  # 18
        round1 = [(n, f"Artist-{n}") for n in valid_names[15:17]]                     # 2 (llega a 17)
        round2 = [(n, f"Artist-{n}") for n in violating_names]                        # solo violadoras, 0 progreso
        round3 = [(n, f"Artist-{n}") for n in violating_names]                        # 0 progreso otra vez -> corta

        with (
            patch.object(app, "call_ai", side_effect=[
                _tracks_payload(round0, True),
                _tracks_payload(round1),
                _tracks_payload(round2),
                _tracks_payload(round3),
            ]) as call_ai,
            patch.object(app, "find_spotify_track", side_effect=_find_spotify_track),
            patch.object(app, "find_artist_fallback", side_effect=_find_artist_fallback),
        ):
            list(app.stream_resolve_from_prompt(
                Mock(), "Playlist", mood, 20, "anthropic", "claude-sonnet-5",
                time.monotonic() + 9999, set(), result,
            ))

        self.assertEqual(len(result["track_ids"]), 17)
        self.assertEqual(len(set(result["track_ids"])), 17)
        self.assertIn(result["stop_reason"], {"no_progress", "max_rounds"})
        self.assertFalse(result["fatal"])
        for violating in violating_names:
            self.assertNotIn(f"id-{violating}", result["track_ids"])


if __name__ == "__main__":
    unittest.main()
