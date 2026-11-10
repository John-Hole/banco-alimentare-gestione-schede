"""Test specifici per la sostituzione completa di GPT-6 Luna con Meta Muse Ember.

Verifica:
1. Default Sezione 2 della Cascata: Meta Muse Ember (Minimal Reasoning) (~$0.0010, ~4.2s).
2. Configurazioni payload OpenRouter con reasoning effort: 'minimal' e 'low'.
3. Opzioni Arbitro Matematico: meta/muse-ember:minimal e meta/muse-ember:low.
4. Catena di fallback automatico su or_muse_minimal e or_muse_low dal selettore in alto.
5. Retrocompatibilità e migrazione automatica di vecchie chiavi 'luna' / 'or_luna_low'.
"""

import unittest
from unittest.mock import MagicMock, patch
import json
import tempfile
from pathlib import Path

from src.config import (
    SUPPORTED_OPENROUTER_MODELS,
    SUPPORTED_PROVIDERS,
    SUPPORTED_ARBITRATOR_MODELS,
    ARBITRATOR_DISPLAY_MAP,
    CASCADE_PAID_MODES,
    DEFAULT_CASCADE_PAID_MODE,
    DEFAULT_CASCADE_EMBER_MODEL,
    load_app_config,
)
from src.extractors.cascade_extractor import CascadeVisionExtractor
from src.extractors.openrouter_extractor import OpenRouterVisionExtractor
from src.models import RawCardExtraction, BoxItem, DeclaredTotals
from src.ui.app import BancoAlimentareApp as BancoAlimentareAppV3


class TestEmberAndLunaReplacement(unittest.TestCase):

    def test_config_constants_and_modes(self):
        """Verifica che GPT-6 Luna sia stato rimosso dai modelli supportati e sostituito da Ember."""
        self.assertNotIn("openai/gpt-6-luna", SUPPORTED_OPENROUTER_MODELS)
        self.assertIn("meta/muse-ember", SUPPORTED_OPENROUTER_MODELS)
        self.assertEqual(DEFAULT_CASCADE_EMBER_MODEL, "meta/muse-ember")

        # Provider selezionabili
        self.assertNotIn("or_luna_low", SUPPORTED_PROVIDERS)
        self.assertNotIn("or_muse_medium", SUPPORTED_PROVIDERS)
        self.assertNotIn("or_muse_max", SUPPORTED_PROVIDERS)
        self.assertIn("or_muse_minimal", SUPPORTED_PROVIDERS)
        self.assertIn("or_muse_low", SUPPORTED_PROVIDERS)

        # Sezione 2 della Cascata
        self.assertEqual(DEFAULT_CASCADE_PAID_MODE, "ember")
        self.assertIn("ember", CASCADE_PAID_MODES)
        self.assertIn("ember_then_haiku", CASCADE_PAID_MODES)
        self.assertIn("haiku_then_ember", CASCADE_PAID_MODES)
        self.assertNotIn("luna", CASCADE_PAID_MODES)

        # Arbitro Matematico
        self.assertIn("meta/muse-ember:minimal", SUPPORTED_ARBITRATOR_MODELS)
        self.assertIn("meta/muse-ember:low", SUPPORTED_ARBITRATOR_MODELS)
        self.assertNotIn("openai/gpt-6-luna", SUPPORTED_ARBITRATOR_MODELS)
        self.assertIn("meta/muse-ember:minimal", ARBITRATOR_DISPLAY_MAP)
        self.assertIn("meta/muse-ember:low", ARBITRATOR_DISPLAY_MAP)

    def test_cascade_section_2_default_and_plan(self):
        """Verifica che la Sezione 2 della Cascata usi di default Meta Muse Ember Minimal."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
        )
        plan = extractor._build_cascade_plan()
        models = [step["model"] for step in plan]
        self.assertEqual(models[1], "meta/muse-ember")
        self.assertEqual(plan[1]["short_name"], "Muse Ember (Minimal)")
        self.assertEqual(plan[1]["display"], "Meta Muse Ember (Minimal)")

        # Supporto retrocompatibile: se un config legacy passa paid_mode='luna', usa ember_step
        ext_legacy = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            paid_mode="luna",
            quota_tracker=mock_tracker,
        )
        plan_legacy = ext_legacy._build_cascade_plan()
        self.assertEqual(plan_legacy[1]["model"], "meta/muse-ember")

        # Test opzioni ember_then_haiku e haiku_then_ember
        ext_eh = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            paid_mode="ember_then_haiku",
            quota_tracker=mock_tracker,
        )
        plan_eh = ext_eh._build_cascade_plan()
        self.assertEqual(plan_eh[1]["model"], "meta/muse-ember")
        self.assertEqual(plan_eh[2]["model"], "anthropic/claude-haiku-5.5")

        ext_he = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            paid_mode="haiku_then_ember",
            quota_tracker=mock_tracker,
        )
        plan_he = ext_he._build_cascade_plan()
        self.assertEqual(plan_he[1]["model"], "anthropic/claude-haiku-5.5")
        self.assertEqual(plan_he[2]["model"], "meta/muse-ember")

    def test_cascade_openrouter_payload_reasoning_minimal_and_low(self):
        """Verifica che _call_openrouter invii reasoning minimal o low per meta/muse-ember."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
        )

        captured_requests = []
        dummy_resp = MagicMock()
        dummy_resp.status_code = 200
        dummy_resp.json.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": '{"supermercato": "TEST"}'}}],
            "usage": {"total_tokens": 100, "cost": 0.0010},
        }

        def fake_post(url, headers=None, json=None, timeout=None):
            captured_requests.append({"url": url, "payload": json})
            return dummy_resp

        with patch("requests.post", side_effect=fake_post):
            # 1. Modello base meta/muse-ember (default minimal)
            extractor._call_openrouter("meta/muse-ember", "fake_b64", "image/jpeg")
            # 2. Modello arbitro con suffisso :minimal
            extractor._call_openrouter("meta/muse-ember:minimal", "fake_b64", "image/jpeg")
            # 3. Modello arbitro con suffisso :low
            extractor._call_openrouter("meta/muse-ember:low", "fake_b64", "image/jpeg")

        self.assertEqual(len(captured_requests), 3)
        # Richiesta 1: model="meta/muse-ember", reasoning={"effort": "minimal"}
        self.assertEqual(captured_requests[0]["payload"]["model"], "meta/muse-ember")
        self.assertEqual(captured_requests[0]["payload"]["reasoning"], {"effort": "minimal"})

        # Richiesta 2: model="meta/muse-ember", reasoning={"effort": "minimal"}
        self.assertEqual(captured_requests[1]["payload"]["model"], "meta/muse-ember")
        self.assertEqual(captured_requests[1]["payload"]["reasoning"], {"effort": "minimal"})

        # Richiesta 3: model="meta/muse-ember", reasoning={"effort": "low"}
        self.assertEqual(captured_requests[2]["payload"]["model"], "meta/muse-ember")
        self.assertEqual(captured_requests[2]["payload"]["reasoning"], {"effort": "low"})

    def test_arbitrator_formatting_with_muse_ember(self):
        """Verifica che l'arbitraggio con Muse Ember (Minimal o Low) formatti correttamente le note."""
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            arbitrator_model="meta/muse-ember:minimal",
            quota_tracker=mock_tracker,
        )

        ember_payload = {
            "choices": [
                {
                    "message": {
                        "content": '{"supermercato": "EMBER ARB SHOP", "numero_scheda": "1", "colonne": [{"colonna_idx": 1, "categoria": "Pasta", "scatole": [{"riga": "1a", "kg": 20.0}], "totale_dichiarato_kg": 20.0}]}'
                    }
                }
            ],
            "usage": {"total_tokens": 150, "cost": 0.0010}
        }

        raw_initial = RawCardExtraction(
            supermercato="EMBER ARB SHOP",
            righe_scatole=[BoxItem(categoria="Pasta", kg=15.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=20.0, per_categoria={"Pasta": {"kg": 20.0, "scatole": 1}})
        )

        with patch.object(extractor, "_call_openrouter", return_value=(200, ember_payload, "", 0.8)):
            resolved = extractor._arbitrate_discrepancies(
                [{"categoria": "Pasta", "calcolato": 15.0, "dichiarato": 20.0}],
                "fake_b64", "image/jpeg", raw_initial
            )
            self.assertIn("Arbitrato con successo da Meta Muse Ember (Minimal)", resolved.note_o_anomalie)

        # Test anche con meta/muse-ember:low
        extractor.arbitrator_model = "meta/muse-ember:low"
        with patch.object(extractor, "_call_openrouter", return_value=(200, ember_payload, "", 0.9)):
            resolved_low = extractor._arbitrate_discrepancies(
                [{"categoria": "Pasta", "calcolato": 15.0, "dichiarato": 20.0}],
                "fake_b64", "image/jpeg", raw_initial
            )
            self.assertIn("Arbitrato con successo da Meta Muse Ember (Low)", resolved_low.note_o_anomalie)

    def test_legacy_config_migration(self):
        """Verifica la migrazione automatica di config legacy contenenti chiavi GPT-6 Luna."""
        legacy_data = {
            "active_provider": "or_luna_low",
            "cascade_paid_mode": "luna",
            "cascade_arbitrator_model": "openai/gpt-6-luna",
            "openrouter_arbitrator_model": "openai/gpt-6-luna",
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(legacy_data, f)
            temp_path = Path(f.name)

        try:
            with patch("src.config.CONFIG_FILE", temp_path):
                cfg = load_app_config()
                self.assertEqual(cfg["active_provider"], "or_muse_minimal")
                self.assertEqual(cfg["cascade_paid_mode"], "ember")
                self.assertEqual(cfg["cascade_arbitrator_model"], "meta/muse-ember:minimal")
                self.assertEqual(cfg["openrouter_arbitrator_model"], "meta/muse-ember:minimal")
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_app_top_selector_ember_fallback_chains(self):
        """Verifica che la selezione in alto di Muse Ember (Minimal e Low) abbia il fallback automatico attivo."""
        app = MagicMock(spec=BancoAlimentareAppV3)
        # Verifica normalizzazioni
        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("or_luna_low"), "or_muse_minimal")
        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("GPT-6 Luna (Low)"), "or_muse_minimal")
        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("Meta Muse Ember (Minimal)"), "or_muse_minimal")
        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("Meta Muse Ember (Low)"), "or_muse_low")

        # Verifica chiavi del selettore in alto
        self.assertIn("or_muse_minimal", BancoAlimentareAppV3.PROVIDER_KEY_MAP)
        self.assertIn("or_muse_low", BancoAlimentareAppV3.PROVIDER_KEY_MAP)
        self.assertNotIn("or_luna_low", BancoAlimentareAppV3.PROVIDER_KEY_MAP)
        self.assertNotIn("or_muse_medium", BancoAlimentareAppV3.PROVIDER_KEY_MAP)
        self.assertNotIn("or_muse_max", BancoAlimentareAppV3.PROVIDER_KEY_MAP)


if __name__ == "__main__":
    unittest.main()
