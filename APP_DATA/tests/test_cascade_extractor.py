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
            paid_mode="ember_then_haiku",
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
                    self.assertIn("Piano B: Muse Ember (Minimal)", raw.note_o_anomalie)
                    self.assertEqual(raw.costo_usd, 0.0016)

    def test_cascade_v2_normalizes_to_gemini_35_without_gemini_38(self):
        """Verifica che la richiesta di Cascata v2 o modalità con Gemini 3.8 venga normalizzata a Gemini 3.5 Lite senza 3.8."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            variant="v2"
        )
        plan = extractor._build_cascade_plan()
        models_in_plan = [s["model"] for s in plan]
        self.assertNotIn("gemini-3.8-flash", models_in_plan)
        self.assertIn("gemini-3.5-flash-lite", models_in_plan)

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
            paid_mode="ember_then_haiku",
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
            if "ember" in model or "luna" in model:
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
                    self.assertIn("Nemotron Free", raw.note_o_anomalie)
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
            if "ember" in model or "luna" in model:
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
                    self.assertIn("Nemotron Free", raw.note_o_anomalie)
                    self.assertEqual(raw.costo_usd, 0.0)



    def test_json_repair_in_parse_json_response(self):
        """Verifica che parse_json_response ripari virgole mancanti o trailing commas tipiche degli LLM."""
        from src.extractors.base import BaseVisionExtractor

        # Test case: virgola mancante tra attributi (lo stesso errore riscontrato dall'utente)
        broken_json = '''{
            "supermercato": "COOP PERUGIA"
            "scheda_n": "1"
            "colonne": [
                {
                    "colonna_idx": 7,
                    "categoria": "Pasta",
                    "scatole": [
                        {"riga": "1a", "kg": 7.0}
                        {"riga": "2a", "kg": 6.0}
                    ]
                }
            ]
        }'''
        raw = BaseVisionExtractor.parse_json_response(broken_json)
        self.assertEqual(raw.supermercato, "COOP PERUGIA")
        self.assertEqual(len(raw.righe_scatole), 2)
        self.assertEqual(raw.righe_scatole[0].kg, 7.0)
        self.assertEqual(raw.righe_scatole[1].kg, 6.0)

    def test_is_valid_raw_extraction_helper(self):
        """Verifica che _is_valid_raw_extraction identifichi correttamente errori fatali rispetto a schede valide."""
        # 1. Scheda con scatole: valida
        raw_ok = RawCardExtraction(righe_scatole=[BoxItem(categoria="Pasta", kg=5.0)])
        self.assertTrue(CascadeVisionExtractor._is_valid_raw_extraction(raw_ok))

        # 2. Scheda vuota lecita: valida
        raw_empty = RawCardExtraction(righe_scatole=[], note_o_anomalie="")
        self.assertTrue(CascadeVisionExtractor._is_valid_raw_extraction(raw_empty))

        # 3. Scheda con errore di sintassi JSON: NON valida
        raw_syntax_err = RawCardExtraction(righe_scatole=[], note_o_anomalie="Impossibile interpretare JSON: Expecting ',' delimiter: line 30")
        self.assertFalse(CascadeVisionExtractor._is_valid_raw_extraction(raw_syntax_err))

        # 4. Risposta vuota dal modello: NON valida
        raw_empty_resp = RawCardExtraction(righe_scatole=[], note_o_anomalie="Risposta vuota dal modello")
        self.assertFalse(CascadeVisionExtractor._is_valid_raw_extraction(raw_empty_resp))

    def test_cascade_failover_on_malformed_json_response(self):
        """Verifica che se Gemini restituisce un JSON malformato (irrecuperabile), la cascata non si blocchi e passi a Piano B."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            paid_mode="ember_then_haiku",
            variant="v1"
        )

        # Gemini restituisce HTTP 200 ma con contenuto che non e' JSON
        gemini_bad_res = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Mi spiace, non posso elaborare questa immagine in formato JSON."}]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 500}
        }

        # Piano B (GPT-6 Luna) restituisce JSON valido
        luna_good_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "COOP SALVATA DA LUNA", "scheda_n": "1", "colonne": [{"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 15.0}]}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 120, "cost": 0.0015}
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(200, gemini_bad_res, "", 1.0)):
                with patch.object(extractor, "_call_openrouter", return_value=(200, luna_good_payload, "", 1.2)):
                    raw = extractor.extract_from_image("scheda_test.jpg")
                    # Verifica che il risultato finale provenga da Piano B e contenga i dati
                    self.assertEqual(raw.supermercato, "COOP SALVATA DA LUNA")
                    self.assertEqual(len(raw.righe_scatole), 1)
                    self.assertEqual(raw.righe_scatole[0].kg, 15.0)
                    self.assertIn("Piano B: Muse Ember (Minimal)", raw.note_o_anomalie)

    def test_default_customizable_cascade_plan_uses_ember_without_luna_and_logs_ascii(self):
        """Verifica il piano default (Gemini 3.5 -> Muse Ember Minimal -> Nemotron Free, senza Luna) e logging ASCII sicuro."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
        )
        plan = extractor._build_cascade_plan()
        models = [step["model"] for step in plan]
        self.assertEqual(
            models,
            ["gemini-3.5-flash-lite", "meta/muse-ember", "nvidia/nemotron-3.5-super-vl:free"],
        )
        self.assertNotIn("openai/gpt-6-luna", models)

        # Verifica che la sequenza di failover esegua Ember Minimal subito dopo Google 503 senza toccare Luna
        called_models = []
        ember_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "EMBER DEFAULT SHOP", "numero_scheda": "1", "colonne": [{"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 10.0}], "totale_dichiarato_kg": 10.0}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 110, "cost": 0.0010},
        }

        def mock_or(model, img, mime, tier_label="", custom_prompt=None):
            called_models.append(model)
            return (200, ember_payload, "", 0.7)

        import io
        import logging
        cp1252_stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        cp_handler = logging.StreamHandler(cp1252_stream)
        extractor.session_logger.logger.addHandler(cp_handler)
        try:
            with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
                with patch.object(extractor, "_call_gemini", return_value=(503, {}, "Overloaded", 0.5)):
                    with patch.object(extractor, "_call_openrouter", side_effect=mock_or):
                        raw = extractor.extract_from_image("scheda_default.jpg")
        finally:
            extractor.session_logger.logger.removeHandler(cp_handler)

        self.assertEqual(raw.supermercato, "EMBER DEFAULT SHOP")
        self.assertEqual(called_models, ["meta/muse-ember"])
        self.assertIn("Piano B: Muse Ember (Minimal)", raw.note_o_anomalie)
        cp1252_stream.flush()
        log_text = extractor.session_logger.read_current_log()
        self.assertIn("Gemini 3.5 Lite -> Muse Ember (Minimal) -> Nemotron Free", log_text)

    def test_customizable_cascade_section_combinations(self):
        """Verifica tutte le opzioni delle 3 sezioni (Gemini 3.8, entrambi, Haiku/Luna/none, Nemotron/none)."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        # 1. free_primary_mode="gemini_38" normalizza a gemini_35 (gemini-3.5-flash-lite)
        ext_38_hl = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR",
            free_primary_mode="gemini_38",
            paid_mode="haiku_then_ember",
            fallback_mode="nemotron_free",
            quota_tracker=mock_tracker,
        )
        self.assertEqual(
            [s["model"] for s in ext_38_hl._build_cascade_plan()],
            [
                "gemini-3.5-flash-lite",
                "anthropic/claude-haiku-5.5",
                "meta/muse-ember",
                "nvidia/nemotron-3.5-super-vl:free",
            ],
        )

        # 2. free_primary_mode="gemini_35" + paid_mode="none" -> Google 503 salta subito a Nemotron Free
        ext_no_paid = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR",
            free_primary_mode="gemini_35",
            paid_mode="none",
            fallback_mode="nemotron_free",
            quota_tracker=mock_tracker,
        )
        self.assertEqual(
            [s["model"] for s in ext_no_paid._build_cascade_plan()],
            ["gemini-3.5-flash-lite", "nvidia/nemotron-3.5-super-vl:free"],
        )

        gem_calls = []
        or_calls = []
        nem_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "NO PAID SHOP", "numero_scheda": "2", "colonne": [{"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 12.0}]}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 90, "cost": 0.0},
        }

        with patch.object(ext_no_paid, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(
                ext_no_paid,
                "_call_gemini",
                side_effect=lambda *a, **kw: (gem_calls.append(kw.get("model_name") or ext_no_paid.gemini_model), (503, {}, "Busy", 0.2))[1],
            ):
                with patch.object(
                    ext_no_paid,
                    "_call_openrouter",
                    side_effect=lambda m, *a, **kw: (or_calls.append(m), (200, nem_payload, "", 0.4))[1],
                ):
                    raw = ext_no_paid.extract_from_image("scheda_no_paid.jpg")

        self.assertEqual(gem_calls, ["gemini-3.5-flash-lite"])
        self.assertEqual(or_calls, ["nvidia/nemotron-3.5-super-vl:free"])
        self.assertEqual(raw.supermercato, "NO PAID SHOP")

        # 3. paid_mode="none" + fallback_mode="none" -> chiude con messaggio diagnostico chiaro senza chiamare OR
        ext_free_only = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR",
            free_primary_mode="gemini_35",
            paid_mode="none",
            fallback_mode="none",
            quota_tracker=mock_tracker,
        )
        with patch.object(ext_free_only, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(ext_free_only, "_call_gemini", return_value=(503, {}, "Busy", 0.2)):
                with patch.object(ext_free_only, "_call_openrouter") as mock_or_never:
                    raw_fail = ext_free_only.extract_from_image("scheda_fail.jpg")
                    mock_or_never.assert_not_called()
        self.assertIn("Tutti i piani della cascata sono falliti", raw_fail.note_o_anomalie)

    def test_dual_arbitrator_switches_free_vs_paid(self):
        """Verifica gli switch indipendenti enable_arbitrator_on_free e enable_arbitrator_on_paid."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        # A. Su Free con enable_arbitrator_on_free=False -> arbitro NON invocato su discrepanza
        ext_free_off = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            enable_arbitrator_on_free=False,
            enable_arbitrator_on_paid=False,
            quota_tracker=mock_tracker,
        )
        self.assertFalse(ext_free_off.enable_auto_arbitrator)

        gem_disc_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"supermercato": "DISC FREE", "numero_scheda": "1", "righe_scatole": [{"riga": "1a", "categoria": "Pasta", "kg": 10.0}], "totali_dichiarati": {"kg_totale": 25.0, "scatole_totale": 1, "per_categoria": {"Pasta": {"kg": 25.0, "scatole": 1}}}}'
                            }
                        ]
                    }
                }
            ],
            "usageMetadata": {"totalTokenCount": 180},
        }

        with patch.object(ext_free_off, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(ext_free_off, "_call_gemini", return_value=(200, gem_disc_payload, "", 0.4)):
                with patch.object(ext_free_off, "_arbitrate_with_gemini_flash") as mock_gem_arb:
                    with patch.object(ext_free_off, "_arbitrate_discrepancies") as mock_or_arb:
                        raw_a = ext_free_off.extract_from_image("disc_free_off.jpg")
                        mock_gem_arb.assert_not_called()
                        mock_or_arb.assert_not_called()
        self.assertIn("[Discrepanza matematica:", raw_a.note_o_anomalie)

        # B. Default (on_free=True, on_paid=False): Haiku vince con discrepanza -> nessun arbitro né falso log
        ext_paid_off = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            enable_arbitrator_on_free=True,
            enable_arbitrator_on_paid=False,
            quota_tracker=mock_tracker,
        )
        haiku_disc_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "DISC PAID OFF", "numero_scheda": "1", "colonne": [{"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 10.0}], "totale_dichiarato_kg": 18.0}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 140, "cost": 0.0025},
        }
        or_calls_b = []
        with patch.object(ext_paid_off, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(ext_paid_off, "_call_gemini", return_value=(503, {}, "Busy", 0.3)):
                with patch.object(
                    ext_paid_off,
                    "_call_openrouter",
                    side_effect=lambda m, *a, **kw: (or_calls_b.append(m), (200, haiku_disc_payload, "", 0.5))[1],
                ):
                    with patch.object(ext_paid_off.session_logger, "log_math_validation") as mock_log_math:
                        raw_b = ext_paid_off.extract_from_image("disc_paid_off.jpg")
                        mock_log_math.assert_called_once()
                        self.assertFalse(mock_log_math.call_args[1]["will_arbitrate"])
        self.assertEqual(len(or_calls_b), 1)
        self.assertIn("[Discrepanza matematica:", raw_b.note_o_anomalie)

        # C1. on_paid=True con arbitro IDENTICO al modello vincente (Haiku == Haiku) -> NON ri-chiama lo stesso modello
        ext_paid_same = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            enable_arbitrator_on_free=False,
            enable_arbitrator_on_paid=True,
            paid_mode="haiku",
            arbitrator_model="anthropic/claude-haiku-5.5",
            quota_tracker=mock_tracker,
        )
        self.assertTrue(ext_paid_same.enable_auto_arbitrator)
        or_calls_c1 = []
        with patch.object(ext_paid_same, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(ext_paid_same, "_call_gemini", return_value=(503, {}, "Busy", 0.3)):
                with patch.object(
                    ext_paid_same,
                    "_call_openrouter",
                    side_effect=lambda m, *a, **kw: (or_calls_c1.append(m), (200, haiku_disc_payload, "", 0.5))[1],
                ):
                    with patch.object(ext_paid_same, "_arbitrate_discrepancies", side_effect=lambda d, b, m, r: r) as mock_arb_same:
                        raw_same = ext_paid_same.extract_from_image("disc_paid_same.jpg")
                        mock_arb_same.assert_not_called()
        self.assertEqual(or_calls_c1, ["anthropic/claude-haiku-5.5"])
        self.assertIn("[Discrepanza matematica:", raw_same.note_o_anomalie)

        # C2. on_paid=True con arbitro DISTINTO (Sonnet != Haiku) -> arbitro viene invocato una volta
        ext_paid_on = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            enable_arbitrator_on_free=False,
            enable_arbitrator_on_paid=True,
            arbitrator_model="anthropic/claude-sonnet-5.5",
            quota_tracker=mock_tracker,
        )
        self.assertTrue(ext_paid_on.enable_auto_arbitrator)
        with patch.object(ext_paid_on, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(ext_paid_on, "_call_gemini", return_value=(503, {}, "Busy", 0.3)):
                with patch.object(ext_paid_on, "_call_openrouter", return_value=(200, haiku_disc_payload, "", 0.5)):
                    with patch.object(ext_paid_on, "_arbitrate_discrepancies", side_effect=lambda d, b, m, r: r) as mock_arb_paid:
                        ext_paid_on.extract_from_image("disc_paid_on.jpg")
                        mock_arb_paid.assert_called_once()

    def test_gemini_extractor_180_retry_sets_was_rotated_180_on_primary_and_backup(self):
        """GeminiVisionExtractor deve impostare was_rotated_180=True e propagare status_callback su primario e backup."""
        import tempfile
        from PIL import Image
        from src.extractors.gemini_extractor import GeminiVisionExtractor

        flipped_json = (
            '{"supermercato": "TEST", "numero_scheda": "1", "scheda_capovolta": true, '
            '"righe_scatole": [], "totali_dichiarati": {}}'
        )
        upright_json = (
            '{"supermercato": "CONAD", "numero_scheda": "1", "scheda_capovolta": false, '
            '"righe_scatole": [{"riga": "1a", "categoria": "Pasta", "kg": 5.0}], '
            '"totali_dichiarati": {"kg_totale": 5.0, "scatole_totale": 1, "per_categoria": {"Pasta": {"kg": 5.0, "scatole": 1}}}}'
        )
        ok_flipped = {"candidates": [{"content": {"parts": [{"text": flipped_json}]}}], "usageMetadata": {"totalTokenCount": 50}}
        ok_upright = {"candidates": [{"content": {"parts": [{"text": upright_json}]}}], "usageMetadata": {"totalTokenCount": 80}}

        with tempfile.TemporaryDirectory() as tmpdir:
            img_p = Path(tmpdir) / "scheda.jpg"
            Image.new("RGB", (120, 80), color=(240, 240, 240)).save(img_p, format="JPEG")

            # Caso 1: Primary 200 -> 180 flip -> retry 200
            gem = GeminiVisionExtractor(api_key="K_PRIM")
            gem.tracker = MagicMock()
            statuses = []
            with patch.object(gem, "encode_and_optimize_image", return_value=("image/jpeg", "b64")):
                with patch.object(gem, "_execute_gemini_call", side_effect=[(200, ok_flipped, "", 0.2), (200, ok_upright, "", 0.2)]):
                    res = gem.extract_from_image(img_p, status_callback=statuses.append)
            self.assertTrue(res.was_rotated_180)
            self.assertFalse(res.scheda_capovolta)
            self.assertEqual(res.supermercato, "CONAD")
            self.assertTrue(statuses)

            # Caso 2: Primary 429 -> Backup 200 capovolta -> retry 200
            gem_bk = GeminiVisionExtractor(api_key="K_PRIM", backup_api_key="K_BACK")
            gem_bk.tracker = MagicMock()
            with patch.object(gem_bk, "encode_and_optimize_image", return_value=("image/jpeg", "b64")):
                with patch.object(
                    gem_bk,
                    "_execute_gemini_call",
                    side_effect=[(429, {}, "Quota", 0.1), (200, ok_flipped, "", 0.2), (429, {}, "Quota", 0.1), (200, ok_upright, "", 0.2)],
                ):
                    res_bk = gem_bk.extract_from_image(img_p)
            self.assertTrue(res_bk.was_rotated_180)
            self.assertEqual(res_bk.supermercato, "CONAD")

    def test_app_process_next_pending_rotates_work_image_raw_backup_and_cache_on_ia_180(self):
        """Se l'IA ruota la copia ai_image_path (o rileva scheda_capovolta su modello singolo), app deve ruotare work image + .raw.jpg e svuotare cache visore."""
        import tempfile
        from PIL import Image, ImageDraw
        from src.ui.app import BancoAlimentareApp, BatchItem

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            work_path = tmp / "work_01.jpg"
            raw_path = work_path.with_suffix(".raw.jpg")
            ai_path = tmp / "work_01_p2.jpg"

            def _make_top_dark_img(path: Path):
                im = Image.new("RGB", (200, 120), color=(240, 240, 240))
                d = ImageDraw.Draw(im)
                d.rectangle([10, 5, 190, 25], fill=(15, 15, 15))
                im.save(path, format="JPEG", quality=95)

            # Work, .raw.jpg e copia AI partono capovolte (barra scura in basso y=105)
            for p in (work_path, raw_path, ai_path):
                _make_top_dark_img(p)
                with Image.open(p) as im:
                    im.rotate(180).save(p, format="JPEG", quality=95)

            item = BatchItem(
                image_path=str(work_path),
                preset_id=2,
                ai_image_path=str(ai_path),
            )
            fake_app = MagicMock()
            fake_app._extracting = False
            fake_app.batch_items = [item]
            fake_app.batch_index = 0
            fake_app._extraction_generation_id = 1
            fake_app._strip_visible = False
            fake_app._current_item.return_value = item
            fake_app.excel_mgr.get_target_filename.return_value = None
            fake_app.colletta_service.find_matches.return_value = []
            fake_app.config_data = {"auto_preset_assignment": False}
            fake_app.image_viewer = MagicMock()
            fake_app.image_viewer._original_cache = {str(work_path): "stale_orig"}
            fake_app.image_viewer._processed_cache = {str(work_path): "stale_proc"}
            fake_app.image_viewer._preset_state_cache = {str(work_path): "stale_state"}

            fake_extractor = MagicMock()
            def _fake_extract(sent_img, status_callback=None):
                # Simula Cascade/Gemini che ruota SOLO ai_image_path (not work_path!) e imposta was_rotated_180=True
                from src.scanner.scanner_service import ScannerService
                ScannerService.rotate_image_file_180(sent_img)
                return RawCardExtraction(supermercato="CONAD", numero_scheda="1", was_rotated_180=True, scheda_capovolta=False)
            fake_extractor.extract_from_image.side_effect = _fake_extract
            fake_app.active_extractor = fake_extractor
            fake_app._get_active_extractor.return_value = fake_extractor

            class _ImmediateTask:
                def __init__(self, parent, work_fn, done_fn, on_progress=None):
                    self.work_fn = work_fn
                    self.done_fn = done_fn
                def start(self):
                    res = self.work_fn(lambda _msg: None)
                    self.done_fn(res, None)

            with patch("src.ui.app.BackgroundTask", _ImmediateTask):
                with patch("src.ui.app.ScannerService.ensure_upright_orientation", return_value=False):
                    with patch("src.ui.app.create_ai_optimized_copy", return_value=str(ai_path)) as mock_recreate:
                        BancoAlimentareApp._process_next_pending(fake_app)

            mock_recreate.assert_called_once()
            # Verifica che work_path e .raw.jpg siano stati girati con barra scura in ALTO (y=12)
            for p in (work_path, raw_path):
                with Image.open(p) as checked:
                    self.assertLess(checked.getpixel((100, 12))[0], 60, f"{p.name} doveva risultare dritta dopo rotazione IA")
            # Cache visore invalidata e immagine ricaricata
            self.assertNotIn(str(work_path), fake_app.image_viewer._original_cache)
            fake_app.image_viewer.load_image.assert_called_once_with(str(work_path), initial_preset=2)

            # Caso B: Estrattore OpenRouter singolo (was_rotated_180=False ma scheda_capovolta=True)
            item_or = BatchItem(image_path=str(work_path), preset_id=None, ai_image_path=str(work_path))
            item_or.status = "pending"
            fake_app.batch_items = [item_or]
            fake_app._current_item.return_value = item_or
            fake_app._extracting = False
            fake_extractor.extract_from_image.side_effect = lambda *_a, **_kw: RawCardExtraction(
                supermercato="COOP", numero_scheda="2", was_rotated_180=False, scheda_capovolta=True
            )
            with patch("src.ui.app.BackgroundTask", _ImmediateTask):
                with patch("src.ui.app.ScannerService.ensure_upright_orientation", return_value=False):
                    BancoAlimentareApp._process_next_pending(fake_app)
            # Ruotato di altri 180° -> torna capovolta rispetto a prima (barra scura in basso y=107) su work e .raw.jpg
            for p in (work_path, raw_path):
                with Image.open(p) as checked:
                    self.assertLess(checked.getpixel((100, 107))[0], 60, f"{p.name} doveva ruotare 180° su scheda_capovolta=True")

    def test_max_tokens_partial_regex_recovery_marks_incomplete_and_fails_over(self):
        """Caso reale utente: JSON Gemini troncato MAX_TOKENS alla Colonna 1 -> is_incomplete=True e failover a Piano B."""
        from src.extractors.base import BaseVisionExtractor

        truncated_text = """{
  "colonne": [
    {
      "colonna_idx": 1,
      "categoria": "Omogeneizzati",
      "scatole": [
        {"riga": "1a", "kg": 10},
        {"riga": "2a", "kg": 7},
        {"riga": "3a", "kg": 8},
        {"riga": "4a", "kg": 4}
      ],
      "totale_dichiarato_kg": 29,
      "totale_dichiarato_scatole": 4
    },
    {
      "colonna_idx": 2,
      "categoria": "Alimenti"""

        parsed = BaseVisionExtractor.parse_json_response(truncated_text, finish_reason="MAX_TOKENS")
        self.assertTrue(parsed.is_incomplete)
        self.assertEqual(len(parsed.righe_scatole), 4)
        self.assertFalse(CascadeVisionExtractor._is_valid_raw_extraction(parsed))

        # Nella cascata: Google 1 torna HTTP 200 troncato -> DEVE fallire e passare a Piano B (Haiku)
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False
        extractor = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
        )

        gemini_truncated = {
            "candidates": [
                {
                    "finishReason": "MAX_TOKENS",
                    "content": {"parts": [{"text": truncated_text}]},
                }
            ],
            "usageMetadata": {"PromptTokenCount": 4727, "totalTokenCount": 8804},
        }
        haiku_full = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"supermercato": "TIGRE - PERUGIA", "numero_scheda": "A1", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}]}, {"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 15.0}]}], "scheda_capovolta": false}'
                    },
                }
            ],
            "usage": {"total_tokens": 3200, "cost": 0.0025},
        }

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(200, gemini_truncated, "", 14.1)):
                with patch.object(extractor, "_call_openrouter", return_value=(200, haiku_full, "", 2.5)):
                    raw = extractor.extract_from_image("scheda_troncata.jpg")

        self.assertFalse(raw.is_incomplete)
        self.assertEqual(raw.supermercato, "TIGRE - PERUGIA")
        self.assertEqual(len(raw.righe_scatole), 2)
        self.assertIn("Piano B: Muse Ember (Minimal)", raw.note_o_anomalie)

    def test_openrouter_finish_reason_length_marks_incomplete_and_fails_over(self):
        """Se OpenRouter termina per 'length', il risultato incompleto deve attivare failover a Nemotron Free."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False
        extractor = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            free_primary_mode="gemini_35",
            paid_mode="haiku",
            fallback_mode="nemotron_free",
            quota_tracker=mock_tracker,
        )

        haiku_truncated = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": '{"supermercato": "TIGRE PARZIALE", "numero_scheda": "1", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}]}]}'
                    },
                }
            ],
            "usage": {"total_tokens": 4100, "cost": 0.0025},
        }
        nemotron_ok = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": '{"supermercato": "TIGRE COMPLETO NEMOTRON", "numero_scheda": "1", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}]}, {"colonna_idx": 8, "categoria": "Riso", "scatole": [{"riga": "1a", "kg": 20.0}]}]}'
                    },
                }
            ],
            "usage": {"total_tokens": 2200, "cost": 0.0},
        }

        or_models = []
        def mock_or(model, img, mime, tier_label="", custom_prompt=None):
            or_models.append(model)
            if "haiku" in model:
                return (200, haiku_truncated, "", 4.0)
            return (200, nemotron_ok, "", 3.0)

        with patch.object(extractor, "encode_and_optimize_image", return_value=("image/jpeg", "fake_b64")):
            with patch.object(extractor, "_call_gemini", return_value=(503, {}, "Busy", 0.3)):
                with patch.object(extractor, "_call_openrouter", side_effect=mock_or):
                    raw = extractor.extract_from_image("scheda.jpg")

        self.assertEqual(or_models, ["anthropic/claude-haiku-5.5", "nvidia/nemotron-3.5-super-vl:free"])
        self.assertEqual(raw.supermercato, "TIGRE COMPLETO NEMOTRON")
        self.assertIn("Piano C: Nemotron Free", raw.note_o_anomalie)

    def test_repair_json_unclosed_truncation_marks_incomplete(self):
        """JSON troncato senza chiusura graffe viene richiuso da _repair_json ma segnalato come is_incomplete=True."""
        from src.extractors.base import BaseVisionExtractor

        unclosed_json = '{"supermercato": "COOP", "numero_scheda": "1", "colonne": [{"colonna_idx": 1, "categoria": "Omogeneizzati", "scatole": [{"riga": "1a", "kg": 10.0}]'
        parsed = BaseVisionExtractor.parse_json_response(unclosed_json)
        self.assertTrue(parsed.is_incomplete)
        self.assertIn("[JSON troncato e richiuso automaticamente]", parsed.note_o_anomalie)
        self.assertFalse(CascadeVisionExtractor._is_valid_raw_extraction(parsed))

    def test_build_gemini_generation_config_by_model_family(self):
        """Verifica thinkingConfig, temperature e maxOutputTokens corretti per Gemini 3.x, 2.5 e 2.0/1.5 e propertyOrdering."""
        from src.extractors.gemini_extractor import build_gemini_generation_config, GEMINI_RESPONSE_SCHEMA

        self.assertEqual(
            GEMINI_RESPONSE_SCHEMA["propertyOrdering"][:3],
            ["supermercato", "numero_scheda", "colonne"],
        )

        # Gemini 3.5 Flash-Lite: LOW per estrazione, MEDIUM per arbitraggio, NO temperature, 16384 output
        cfg_35 = build_gemini_generation_config("gemini-3.5-flash-lite")
        self.assertEqual(cfg_35["thinkingConfig"], {"thinkingLevel": "LOW"})
        self.assertEqual(cfg_35["maxOutputTokens"], 16384)
        self.assertNotIn("temperature", cfg_35)

        cfg_35_arb = build_gemini_generation_config("models/gemini-3.5-flash-lite", is_arbitration=True)
        self.assertEqual(cfg_35_arb["thinkingConfig"], {"thinkingLevel": "MEDIUM"})

        # Gemini 3.8 Flash: usa LOW (mai MINIMAL che non e' supportato su 3.8)
        cfg_38 = build_gemini_generation_config("gemini-3.8-flash")
        self.assertEqual(cfg_38["thinkingConfig"], {"thinkingLevel": "LOW"})
        self.assertEqual(cfg_38["maxOutputTokens"], 16384)
        self.assertNotIn("temperature", cfg_38)

        # Gemini 2.5: thinkingBudget=1024 + temperature=0.0
        cfg_25 = build_gemini_generation_config("gemini-2.5-flash")
        self.assertEqual(cfg_25["thinkingConfig"], {"thinkingBudget": 1024})
        self.assertEqual(cfg_25["temperature"], 0.0)
        self.assertEqual(cfg_25["maxOutputTokens"], 16384)

        # Gemini 2.0 / 1.5: NO thinkingConfig, cap 8192
        cfg_20 = build_gemini_generation_config("gemini-2.0-flash")
        self.assertNotIn("thinkingConfig", cfg_20)
        self.assertEqual(cfg_20["temperature"], 0.0)
        self.assertEqual(cfg_20["maxOutputTokens"], 8192)

    def test_arbitrators_ignore_truncated_outputs_and_no_duplicate_logs(self):
        """Gli arbitri NON devono sovrascrivere una scheda valida con output troncati e Gemini Flash Arbitro non duplica log."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False
        extractor = CascadeVisionExtractor(
            gemini_api_key_1="K1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            arbitrator_model="anthropic/claude-haiku-5.5",
        )

        raw_full = RawCardExtraction(
            supermercato="TIGRE",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Omogeneizzati", kg=29.0),
                BoxItem(categoria="Pasta", kg=31.0),
            ],
            totali_dichiarati=DeclaredTotals(
                per_categoria={
                    "Omogeneizzati": {"kg": 29.0, "scatole": 1},
                    "Pasta": {"kg": 35.0, "scatole": 1},
                }
            ),
        )
        discs = [{"categoria": "Pasta", "calcolato": 31.0, "dichiarato": 35.0, "scatole_trovate": 1, "scatole_dichiarate": 1, "delta_kg": 4.0}]

        # 1. OpenRouter arbitrator torna troncato ('length') con solo 1 scatola -> non deve sovrascrivere raw_full!
        or_trunc = {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": '{"supermercato": "TIGRE", "colonne": [{"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 35.0}]}]}'},
            }],
            "usage": {"total_tokens": 100, "cost": 0.001},
        }
        with patch.object(extractor, "_call_openrouter", return_value=(200, or_trunc, "", 1.0)):
            res_or = extractor._arbitrate_discrepancies(discs, "b64", "image/jpeg", raw_full)
        self.assertEqual(len(res_or.righe_scatole), 2)
        self.assertIn("Arbitro non valido", res_or.note_o_anomalie)

        # 2. Arbitro Gemini Flash riceve risposta MAX_TOKENS -> lascia raw_full intatto e non duplica log_ai_attempt
        gem_trunc = {
            "candidates": [{
                "finishReason": "MAX_TOKENS",
                "content": {"parts": [{"text": '{"colonne": [{"colonna_idx": 7, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 35.0}]}]}'}]},
            }],
            "usageMetadata": {"totalTokenCount": 4000},
        }
        with patch.object(extractor.session_logger, "log_ai_attempt") as mock_attempt:
            with patch("requests.post") as mock_post:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = gem_trunc
                mock_post.return_value = resp
                res_flash = extractor._arbitrate_with_gemini_flash(discs, "b64", "image/jpeg", raw_full)
        self.assertEqual(mock_attempt.call_count, 1)
        self.assertEqual(len(res_flash.righe_scatole), 2)
        self.assertAlmostEqual(sum(b.kg for b in res_flash.righe_scatole), 60.0)


if __name__ == "__main__":
    unittest.main()
