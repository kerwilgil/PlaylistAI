import unittest
import time
from unittest.mock import Mock, patch

import app


class AnthropicResponseTests(unittest.TestCase):
    def test_extracts_text_after_thinking_block(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "content": [
                {"type": "thinking", "thinking": "razonamiento interno"},
                {"type": "text", "text": '{"summary":"ok"}'},
                {"type": "text", "text": "fin"},
            ]
        }

        with patch.object(app.requests, "post", return_value=response) as post:
            result = app.call_ai("prueba", provider="anthropic")

        self.assertEqual(result, '{"summary":"ok"}\nfin')
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "claude-sonnet-5")
        self.assertEqual(payload["max_tokens"], 5000)
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_anthropic_uses_requested_budget_and_json_schema(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "content": [{"type": "text", "text": '{"tracks":[]}'}],
            "stop_reason": "end_turn",
        }
        schema = app._playlist_output_schema(include_description=False)

        with patch.object(app.requests, "post", return_value=response) as post:
            result = app.call_ai(
                "prueba",
                provider="anthropic",
                max_output_tokens=9000,
                output_schema=schema,
            )

        self.assertEqual(result, '{"tracks":[]}')
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], 9000)
        self.assertEqual(
            payload["output_config"],
            {"format": {"type": "json_schema", "schema": schema}},
        )

    def test_missing_text_block_returns_controlled_error(self):
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "content": [{"type": "thinking", "thinking": "razonamiento interno"}]
        }

        with patch.object(app.requests, "post", return_value=response):
            result = app.call_ai("prueba", provider="anthropic")

        self.assertEqual(
            result,
            "Error Anthropic: la respuesta no incluyó ningún bloque de texto.",
        )


class AIJsonParsingTests(unittest.TestCase):
    def test_accepts_fenced_json(self):
        result = app.parse_ai_json(
            '```json\n{"description":"cálida","tracks":[{"name":"snow","artist":"idealis"}]}\n```'
        )

        self.assertEqual(result["tracks"][0]["name"], "snow")

    def test_extracts_json_with_surrounding_text(self):
        result = app.parse_ai_json(
            'Aquí está:\n{"tracks":[{"name":"snow","artist":"idealis"}]}\nListo.'
        )

        self.assertEqual(result["tracks"][0]["artist"], "idealis")

    def test_rejects_truncated_playlist_json(self):
        with self.assertRaises((ValueError, app.json.JSONDecodeError)):
            app.parse_playlist_json(
                '{"description":"cálida","tracks":[{"name":"snow","artist":"idealis"'
            )

    def test_playlist_generation_retries_truncated_json_once(self):
        valid = (
            '{"description":"cálida","tracks":'
            '[{"name":"snow","artist":"idealis"}]}'
        )
        spotify_track = {
            "id": "track-1",
            "name": "snow",
            "artists": [{"name": "idealis"}],
            "album": {"images": []},
        }
        result = app._new_result("mood")

        with (
            patch.object(
                app,
                "call_ai",
                side_effect=['{"description":"cálida","tracks":[', valid],
            ) as call,
            patch.object(app, "find_spotify_track", return_value=spotify_track),
        ):
            events = list(
                app.stream_resolve_from_prompt(
                    Mock(),
                    "Lo-Fi Work",
                    "mood",
                    1,
                    "anthropic",
                    "claude-sonnet-5",
                    time.monotonic() + 10,
                    set(),
                    result,
                )
            )

        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["track_ids"], ["track-1"])
        self.assertFalse(result["fatal"])
        self.assertTrue(events)


if __name__ == "__main__":
    unittest.main()
