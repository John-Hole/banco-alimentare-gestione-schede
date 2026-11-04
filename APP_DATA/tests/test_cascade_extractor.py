"""Test unitari per CascadeVisionExtractor e proprietà di compatibilità di RawCardExtraction."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import RawCardExtraction, BoxItem, DeclaredTotals
from src.extractors.cascade_extractor import CascadeVisionExtractor


class TestCascadeExtractor(unittest.TestCase):

    def test_raw_card_extraction_compat_properties(self):
        """Verifica che raw.scatole e raw.totali_dichiarati_kg restituiscano i dati corretti senza errori."""
        raw = RawCardExtraction(
            supermercato="TIGRE TEST",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Pasta", kg=12.0),
                BoxItem(categoria="Tonno", kg=5.0),
            ],
            totali_dichiarati=DeclaredTotals(
                kg_totale=27.0,
                scatole_totale=3,
                per_categoria={
                    "Pasta": {"kg": 22.0, "scatole": 2},
                    "Tonno": {"kg": 5.0, "scatole": 1},
                },
            ),
        )

        self.assertEqual(len(raw.scatole), 3)
        self.assertEqual(raw.scatole[0].categoria, "Pasta")
        self.assertEqual(raw.totali_dichiarati_kg, {"Pasta": 22.0, "Tonno": 5.0})

    def test_validate_math_no_discrepancies(self):
        """Verifica che _validate_math non segnali discrepanze quando i totali coincidono."""
        extractor = CascadeVisionExtractor(gemini_api_key_1="KEY1", quota_tracker=MagicMock())
        raw = RawCardExtraction(
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Pasta", kg=13.0),
                BoxItem(categoria="Olio", kg=4.0),
            ],
            totali_dichiarati=DeclaredTotals(
                per_categoria={
                    "Pasta": {"kg": 23.0, "scatole": 2},
                    "Olio": {"kg": 4.0, "scatole": 1},
                }
            ),
        )
        discrepancies = extractor._validate_math(raw)
        self.assertEqual(discrepancies, [])

    def test_validate_math_detects_discrepancy(self):
        """Verifica che _validate_math individui un mismatch tra somma kg scatole e totale dichiarato."""
        extractor = CascadeVisionExtractor(gemini_api_key_1="KEY1", quota_tracker=MagicMock())
        raw = RawCardExtraction(
            righe_scatole=[
                BoxItem(categoria="Tonno", kg=12.0),
                BoxItem(categoria="Pasta", kg=15.0),
            ],
            totali_dichiarati=DeclaredTotals(
                per_categoria={
                    "Tonno": {"kg": 25.0, "scatole": 2},  # Mismatch: 12.0 vs 25.0
                    "Pasta": {"kg": 15.0, "scatole": 1},
                }
            ),
        )
        discrepancies = extractor._validate_math(raw)
        self.assertEqual(len(discrepancies), 1)
        self.assertEqual(discrepancies[0]["categoria"], "Tonno")
        self.assertEqual(discrepancies[0]["calcolato"], 12.0)
        self.assertEqual(discrepancies[0]["dichiarato"], 25.0)
        self.assertEqual(discrepancies[0]["scatole_trovate"], 1)

    def test_extract_from_image_end_to_end_upside_down_and_normal(self):
        """Verifica l'intero flusso di extract_from_image (inclusa foto capovolta) senza AttributeError."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="",
            quota_tracker=mock_tracker,
        )

        gemini_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": """{
                                    "supermercato": "CONAD TEST",
                                    "numero_scheda": "1",
                                    "righe_scatole": [
                                        {"riga": "1a", "categoria": "Pasta", "kg": 10.0},
                                        {"riga": "2a", "categoria": "Olio", "kg": 5.0}
                                    ],
                                    "totali_dichiarati": {
                                        "kg_totale": 15.0,
                                        "scatole_totale": 2,
                                        "per_categoria": {
                                            "Pasta": {"kg": 10.0, "scatole": 1},
                                            "Olio": {"kg": 5.0, "scatole": 1}
                                        }
                                    },
                                    "note_o_anomalie": null,
                                    "scheda_capovolta": true
                                }"""
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 250},
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(200, gemini_payload, "", 0.3)):
                raw = extractor.extract_from_image("foto_testa.jpg")
                self.assertEqual(raw.supermercato, "CONAD TEST")
                self.assertTrue(raw.scheda_capovolta)
                self.assertEqual(len(raw.righe_scatole), 2)
                self.assertEqual(len(raw.scatole), 2)
                self.assertIn("Piano A: Google 1", raw.note_o_anomalie)

    def test_cascade_v1_failover_to_luna(self):
        """Verifica che in Cascata v1, se Google fallisce (503/408/429), intervenga Piano B (GPT-6 Luna)."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            variant="v1"
        )

        luna_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "LUNA SHOP", "numero_scheda": "A1", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}]}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 120, "cost": 0.0016}
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(503, {}, "Service Unavailable", 1.5)):
                with patch.object(extractor, "_call_openrouter", return_value=(200, luna_payload, "", 1.2)):
                    raw = extractor.extract_from_image("scheda_test.jpg")
                    self.assertEqual(raw.supermercato, "LUNA SHOP")
                    self.assertIn("Piano B: GPT-6 Luna", raw.note_o_anomalie)
                    self.assertEqual(raw.costo_usd, 0.0016)

    def test_cascade_v2_gemini_38_to_35_fallback(self):
        """Verifica che in Cascata v2 si tenti prima Gemini 3.8, poi Gemini 3.5 se il primo fallisce."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            variant="v2"
        )

        gemini_35_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"supermercato": "FALLBACK 3.5", "numero_scheda": "A2", "colonne": [{"colonna_idx": 1, "categoria": "Tonno", "scatole": [{"riga": "1a", "kg": 5.0}]}]}'
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 200}
        }

        call_records = []
        def mock_gemini_call(key, img_data, mime, key_label="Google", model_name=None, tier_label=""):
            call_records.append(model_name)
            if model_name == "gemini-3.8-flash":
                return (503, {}, "Service Unavailable", 1.0)
            return (200, gemini_35_payload, "", 0.8)

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", side_effect=mock_gemini_call):
                raw = extractor.extract_from_image("scheda_test.jpg")
                self.assertEqual(raw.supermercato, "FALLBACK 3.5")
                self.assertIn("Piano B: Google 1 (Gemini 3.5 Lite", raw.note_o_anomalie)
                self.assertIn("gemini-3.8-flash", call_records)
                self.assertIn("gemini-3.5-flash-lite", call_records)

    def test_arbitrator_uses_haiku_on_discrepancy(self):
        """Verifica che l'arbitraggio matematico invochi Claude Haiku 5.5 e non Sonnet."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            enable_auto_arbitrator=True
        )

        haiku_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "TEST DISCREPANCY", "numero_scheda": "1", "colonne": [{"colonna_idx": 1, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 20.0}], "totale_dichiarato_kg": 20.0}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 150, "cost": 0.0025}
        }

        # Simula estrazione iniziale con discrepanza
        raw_initial = RawCardExtraction(
            supermercato="TEST DISCREPANCY",
            righe_scatole=[BoxItem(categoria="Pasta", kg=15.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=20.0, per_categoria={"Pasta": {"kg": 20.0, "scatole": 1}})
        )

        with patch.object(extractor, "_call_openrouter", return_value=(200, haiku_payload, "", 0.9)) as mock_or:
            resolved = extractor._arbitrate_discrepancies(
                [{"categoria": "Pasta", "calcolato": 15.0, "dichiarato": 20.0}],
                "fake_b64", "image/jpeg", raw_initial
            )
            self.assertEqual(extractor.arbitrator_model, "anthropic/claude-haiku-5.5")
            mock_or.assert_called_once()
            self.assertEqual(mock_or.call_args[0][0], "anthropic/claude-haiku-5.5")
            self.assertIn("Arbitrato con successo da Claude Haiku 5.5", resolved.note_o_anomalie)

    def test_cascade_v1_failover_to_nemotron_free_when_haiku_fails(self):
        """Verifica che in Cascata v1, se Haiku fallisce (es. crediti esauriti 402), intervenga Nemotron Free (Piano D)."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            variant="v1"
        )

        nemotron_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "EMERGENCY FREE SHOP", "numero_scheda": "A1", "colonne": [{"colonna_idx": 1, "categoria": "Riso", "scatole": [{"riga": "1a", "kg": 8.0}]}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 100, "cost": 0.0}
        }

        call_models = []
        def mock_or_call(model, img, mime, tier_label="", custom_prompt=None):
            call_models.append(model)
            if "luna" in model:
                return (429, {}, "Rate limited", 0.5)
            if "haiku" in model:
                return (402, {}, "Credits exhausted", 0.5)
            if "nemotron" in model:
                return (200, nemotron_payload, "", 0.8)
            return (500, {}, "Unknown model", 0.1)

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(503, {}, "Unavailable", 1.0)):
                with patch.object(extractor, "_call_openrouter", side_effect=mock_or_call):
                    raw = extractor.extract_from_image("scheda_test.jpg")
                    self.assertEqual(raw.supermercato, "EMERGENCY FREE SHOP")
                    self.assertIn("Piano D: Nemotron Free", raw.note_o_anomalie)
                    self.assertEqual(raw.costo_usd, 0.0)
                    self.assertEqual(len(call_models), 3)

    def test_cascade_v2_failover_to_nemotron_free_when_all_fail(self):
        """Verifica che in Cascata v2, se Haiku fallisce (crediti esauriti), intervenga Nemotron Free (Piano E)."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            variant="v2"
        )

        nemotron_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "V2 EMERGENCY FREE", "numero_scheda": "A2", "colonne": [{"colonna_idx": 1, "categoria": "Olio", "scatole": [{"riga": "1a", "kg": 3.0}]}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 90, "cost": 0.0}
        }

        call_models = []
        def mock_or_call(model, img, mime, tier_label="", custom_prompt=None):
            call_models.append(model)
            if "luna" in model:
                return (429, {}, "Rate limited", 0.5)
            if "haiku" in model:
                return (402, {}, "Credits exhausted", 0.5)
            if "nemotron" in model:
                return (200, nemotron_payload, "", 0.8)
            return (500, {}, "Unknown model", 0.1)

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(503, {}, "Unavailable", 1.0)):
                with patch.object(extractor, "_call_openrouter", side_effect=mock_or_call):
                    raw = extractor.extract_from_image("scheda_test.jpg")
                    self.assertEqual(raw.supermercato, "V2 EMERGENCY FREE")
                    self.assertIn("Piano E: Nemotron Free", raw.note_o_anomalie)
                    self.assertEqual(raw.costo_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
