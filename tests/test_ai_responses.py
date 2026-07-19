import unittest
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

        with patch.object(app.requests, "post", return_value=response):
            result = app.call_ai("prueba", provider="anthropic")

        self.assertEqual(result, '{"summary":"ok"}\nfin')

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


if __name__ == "__main__":
    unittest.main()
