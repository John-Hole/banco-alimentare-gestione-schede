"""Test per i provider di estrazione e parsing JSON."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extractors.mock_extractor import MockVisionExtractor
from src.extractors.base import BaseVisionExtractor


class TestExtractors(unittest.TestCase):

    def test_mock_extractor_scenarios(self):
        """Verifica che tutti gli scenari del mock producano dati coerenti."""
        for sc in MockVisionExtractor.get_available_scenarios():
            extractor = MockVisionExtractor(default_scenario=sc["id"])
            raw = extractor.extract_from_image("dummy_path.png")
            self.assertIsNotNone(raw)
            self.assertTrue(len(raw.supermercato) > 0)

    def test_json_parser_markdown_fence_cleaning(self):
        """Verifica la pulizia automatica di markdown ```json ... ```."""
        sample_resp = """```json
        {
            "supermercato": "Punto Vendita Test",
            "numero_scheda": "A2",
            "righe_scatole": [
                {"categoria": "Pasta", "kg": 15.0}
            ],
            "totali_dichiarati": {
                "kg_totale": 15.0,
                "scatole_totale": 1
            }
        }
        ```"""
        parsed = BaseVisionExtractor.parse_json_response(sample_resp)
        self.assertEqual(parsed.supermercato, "Punto Vendita Test")
        self.assertEqual(parsed.numero_scheda, "2")
        self.assertEqual(len(parsed.righe_scatole), 1)
        self.assertEqual(parsed.righe_scatole[0].categoria, "Pasta")
        self.assertEqual(parsed.righe_scatole[0].kg, 15.0)

    def test_gemini_dual_key_failover(self):
        """Verifica che l'estrattore Gemini passi alla seconda chiave sullo stesso modello se riceve 429."""
        from unittest.mock import patch, MagicMock
        from src.extractors.gemini_extractor import GeminiVisionExtractor

        extractor = GeminiVisionExtractor(
            api_key="KEY_PRIMARY",
            backup_api_key="KEY_BACKUP",
            primary_label="Gemini 1",
            backup_label="Gemini 2",
            model="gemini-3.5-flash-lite",
            enable_auto_fallback=True
        )
        extractor.tracker = MagicMock()

        dummy_json = {
            "candidates": [{
                "content": {
                    "parts": [{"text": '{"supermercato": "Punto Test", "numero_scheda": "A1", "righe_scatole": []}'}]
                }
            }],
            "usageMetadata": {"totalTokenCount": 120}
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "dummy_base64")):
            # Simuliamo che la prima chiamata con KEY_PRIMARY dia 429, e la seconda con KEY_BACKUP dia 200
            def mock_execute(api_key, model_name, img_data, mime_type):
                if api_key == "KEY_PRIMARY":
                    return 429, {}, "Quota Exceeded", 0.1
                elif api_key == "KEY_BACKUP":
                    return 200, dummy_json, "", 0.2
                return 500, {}, "Error", 0.0

            with patch.object(extractor, "_execute_gemini_call", side_effect=mock_execute):
                card = extractor.extract_from_image("dummy.jpg")
                self.assertEqual(card.supermercato, "Punto Test")
                self.assertIn("[Failover Gemini 2]", card.note_o_anomalie)

    def test_gemini_both_keys_fail_falls_back_to_openrouter(self):
        """Verifica che se entrambe le chiavi Gemini danno 429, interviene OpenRouter."""
        from unittest.mock import patch, MagicMock
        from src.extractors.gemini_extractor import GeminiVisionExtractor
        from src.models import RawCardExtraction

        mock_openrouter = MagicMock()
        mock_openrouter.model = "minimax/minimax-m3:free"
        mock_openrouter.extract_from_image.return_value = RawCardExtraction(
            supermercato="Punto OpenRouter",
            numero_scheda="A1"
        )

        extractor = GeminiVisionExtractor(
            api_key="KEY_PRIMARY",
            backup_api_key="KEY_BACKUP",
            primary_label="Gemini 1",
            backup_label="Gemini 2",
            model="gemini-3.5-flash-lite",
            fallback_extractor=mock_openrouter,
            enable_auto_fallback=True
        )
        extractor.tracker = MagicMock()

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "dummy_base64")):
            with patch.object(extractor, "_execute_gemini_call", return_value=(429, {}, "Quota Exceeded", 0.1)):
                card = extractor.extract_from_image("dummy.jpg")
                self.assertEqual(card.supermercato, "Punto OpenRouter")
                self.assertIn("[Fallback OpenRouter]", card.note_o_anomalie)

    def test_gemini_extractor_max_tokens_partial_heuristic_triggers_failover(self):
        """Verifica che GeminiVisionExtractor NON accetti JSON troncato MAX_TOKENS e faccia failover alla chiave 2."""
        from unittest.mock import patch, MagicMock
        from src.extractors.gemini_extractor import GeminiVisionExtractor

        extractor = GeminiVisionExtractor(
            api_key="KEY_PRIMARY",
            backup_api_key="KEY_BACKUP",
            primary_label="Gemini 1",
            backup_label="Gemini 2",
            model="gemini-3.5-flash-lite",
            enable_auto_fallback=True,
        )
        extractor.tracker = MagicMock()
        extractor.tracker.is_exhausted.return_value = False

        truncated_payload = {
            "candidates": [{
                "finishReason": "MAX_TOKENS",
                "content": {
                    "parts": [{
                        "text": '{"colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}, {"riga": "2a", "kg": 7.0'
                    }]
                }
            }],
            "usageMetadata": {"totalTokenCount": 8804},
        }
        full_payload = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {
                    "parts": [{
                        "text": '{"supermercato": "TIGRE COMPLETO", "numero_scheda": "A1", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}]}, {"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 12.0}]}], "scheda_capovolta": false}'
                    }]
                }
            }],
            "usageMetadata": {"totalTokenCount": 5900},
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "dummy_b64")):
            with patch.object(
                extractor,
                "_execute_gemini_call",
                side_effect=[(200, truncated_payload, "", 14.1), (200, full_payload, "", 3.9)],
            ):
                card = extractor.extract_from_image("dummy.jpg")

        self.assertEqual(card.supermercato, "TIGRE COMPLETO")
        self.assertEqual(len(card.righe_scatole), 2)
        self.assertFalse(card.is_incomplete)
        self.assertIn("[Failover Gemini 2]", card.note_o_anomalie)
        # Non deve duplicare record_call su KEY_PRIMARY HTTP 200 troncata
        self.assertEqual(extractor.tracker.record_call.call_count, 2)

    def test_openrouter_extractor_length_finish_reason_triggers_fallback(self):
        """Verifica che OpenRouterVisionExtractor rifiuti finish_reason='length' e attivi fallback_extractor."""
        from unittest.mock import patch, MagicMock
        from src.extractors.openrouter_extractor import OpenRouterVisionExtractor
        from src.models import RawCardExtraction, BoxItem

        mock_fallback = MagicMock()
        mock_fallback.display_name = "Nemotron Free"
        mock_fallback.model = "nvidia/nemotron-3.5-super-vl:free"
        mock_fallback.extract_from_image.return_value = RawCardExtraction(
            supermercato="COOP FALLBACK",
            numero_scheda="1",
            righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)],
            is_incomplete=False,
        )

        extractor = OpenRouterVisionExtractor(
            api_key="OR_KEY",
            model="anthropic/claude-haiku-5.5",
            fallback_extractor=mock_fallback,
            enable_auto_fallback=True,
        )
        extractor.tracker = MagicMock()

        truncated_or_payload = {
            "choices": [{
                "finish_reason": "length",
                "message": {
                    "content": '{"supermercato": "COOP PARZIALE", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 5.0}]}]}'
                }
            }],
            "usage": {"total_tokens": 4100, "cost": 0.002},
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "dummy_b64")):
            with patch.object(extractor, "_execute_openrouter_call", return_value=(200, truncated_or_payload, "", 8.0)):
                card = extractor.extract_from_image("dummy.jpg")

        self.assertEqual(card.supermercato, "COOP FALLBACK")
        mock_fallback.extract_from_image.assert_called_once()
        # Una sola registrazione tracker per primario HTTP 200
        self.assertEqual(extractor.tracker.record_call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
