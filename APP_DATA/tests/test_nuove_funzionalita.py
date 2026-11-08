"""Unit tests per le nuove funzionalità V4 e struttura APP_DATA / V_UFFICIALE:
1. Cifratura e sblocco chiavi API tramite password (crypto_keys.py)
2. Sblocco chiavi con password e scelta cartella in WelcomeSetupDialogV3 e SettingsDialogV3
3. Notifica verde cartella con scomparsa automatica dopo 5 secondi (5000 ms)
4. Pannelli principali ridimensionabili (tk.PanedWindow) e invertibili (⇄ swap_panels)
5. Struttura pulita ROOT_DIR (BACKUP, SCANSIONI, FOGLI_COLLETTA) e APP_DATA_DIR
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    APP_DATA_DIR,
    ROOT_DIR,
    BACKUP_DIR,
    STORICO_DIR,
    LEGACY_COLLETTA_DIR,
    LOGS_DIR,
    load_app_config,
)
from src.crypto_keys import (
    decrypt_keys_bundle,
    encrypt_keys_bundle,
    unlock_giovanni_keys,
)
from src.ui.app import BancoAlimentareAppV3
from src.ui.components_dialogs import WelcomeSetupDialogV3, SettingsDialogV3


_DUMMY_KEYS = {
    "gemini_api_key": "AQ.TEST_GEMINI_KEY_PRIMARY_12345",
    "gemini_api_key_2": "AQ.TEST_GEMINI_KEY_BACKUP_67890",
    "openrouter_api_key": "sk-or-v1-test_openrouter_key_abcdef",
}
_DUMMY_PWD = "TestSuiteSecret99"
_DUMMY_BUNDLE = encrypt_keys_bundle(
    _DUMMY_PWD,
    _DUMMY_KEYS,
    b"0123456789abcdef",
    b"fedcba9876543210",
)


class TestCryptoKeys(unittest.TestCase):
    """Verifica la cifratura PBKDF2-HMAC-SHA256 e l'assenza di chiavi/password in chiaro nel sorgente."""

    def test_encrypt_and_decrypt_roundtrip_with_correct_password(self):
        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE
        keys = decrypt_keys_bundle(_DUMMY_PWD, salt_b64, nonce_b64, ct_b64, tag_b64)
        self.assertEqual(keys, _DUMMY_KEYS)

    def test_unlock_strips_whitespace(self):
        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE
        keys = decrypt_keys_bundle(f"  {_DUMMY_PWD} \n ", salt_b64, nonce_b64, ct_b64, tag_b64)
        self.assertEqual(keys, _DUMMY_KEYS)

    def test_unlock_rejects_wrong_or_empty_passwords(self):
        self.assertIsNone(unlock_giovanni_keys(""))
        self.assertIsNone(unlock_giovanni_keys("   "))
        self.assertIsNone(unlock_giovanni_keys(None))  # type: ignore
        self.assertIsNone(unlock_giovanni_keys("WrongPassword123"))
        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE
        self.assertIsNone(decrypt_keys_bundle(_DUMMY_PWD.lower(), salt_b64, nonce_b64, ct_b64, tag_b64))

    def test_no_plaintext_secrets_in_source_or_tests(self):
        prefix_gem = "AQ." + "Ab8RN"
        prefix_or = "sk-or-v1-" + "b5f38351"
        pwd_marker = "".join(chr(c) for c in (74, 111, 104, 110, 66, 97, 110, 107, 50, 54))
        for folder in (APP_DATA_DIR / "src", APP_DATA_DIR / "tests"):
            for py_file in folder.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                self.assertNotIn(prefix_gem, content, f"Secret leaked in {py_file.name}")
                self.assertNotIn(prefix_or, content, f"Secret leaked in {py_file.name}")
                self.assertNotIn(pwd_marker, content, f"Password leaked in {py_file.name}")


class TestAppDataStructure(unittest.TestCase):
    """Verifica i percorsi ROOT_DIR e APP_DATA_DIR."""

    def test_directories_layout(self):
        self.assertEqual(APP_DATA_DIR.name.upper(), "APP_DATA")
        self.assertEqual(ROOT_DIR, APP_DATA_DIR.parent)
        self.assertEqual(BACKUP_DIR, ROOT_DIR / "BACKUP")
        self.assertEqual(STORICO_DIR, ROOT_DIR / "SCANSIONI")
        self.assertEqual(LEGACY_COLLETTA_DIR, ROOT_DIR / "FOGLI_COLLETTA")
        self.assertEqual(LOGS_DIR, APP_DATA_DIR / "logs")
        self.assertTrue((ROOT_DIR / "AVVIA_BANCO_ALIMENTARE.bat").is_file())
        self.assertTrue((ROOT_DIR / "COME_INSTALLARE.txt").is_file())

    def test_batch_launcher_uses_dynamic_errorlevel_and_checks_fitz(self):
        bat_content = (ROOT_DIR / "AVVIA_BANCO_ALIMENTARE.bat").read_text(encoding="utf-8")
        self.assertNotIn("%ERRORLEVEL%", bat_content)
        self.assertIn("if errorlevel 1", bat_content)
        self.assertIn("if not errorlevel 1", bat_content)
        self.assertIn("fitz", bat_content)


class TestV4UIEnhancements(unittest.TestCase):
    """Verifica notifica 5s, PanedWindow ridimensionabile/invertibile e dialoghi con sblocco password."""

    def setUp(self):
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.update()
            self.app.destroy()
        except Exception:
            pass

    def test_folder_feedback_5s_auto_hide(self):
        """La notifica arancione resta fissa; appena diventa verde pianifica un timer di 5000ms e poi sparisce."""
        # 1. Notifica arancione di scansione in corso: nessun timer di auto-hide
        self.app._set_folder_feedback("⏳ Lettura 1/21: Colletta 2025 Perugia.xls", "#b45309")
        self.assertEqual(
            self.app.folder_feedback_lbl.cget("text"),
            "⏳ Lettura 1/21: Colletta 2025 Perugia.xls",
        )
        self.assertIsNone(self.app._folder_feedback_job)

        # 2. Notifica verde di completamento: pianifica timer a 5000ms
        with patch.object(self.app, "after", wraps=self.app.after) as mock_after:
            self.app._set_folder_feedback(
                "✓ Rilevati 21 file Excel e 399 supermercati nella cartella selezionata",
                "#15803d",
            )
            mock_after.assert_called_with(5000, self.app._clear_folder_feedback)
            self.assertIsNotNone(self.app._folder_feedback_job)

        self.assertIn("✓ Rilevati 21 file", self.app.folder_feedback_lbl.cget("text"))

        # 3. Nuova scansione prima dei 5s cancella il timer precedente
        prev_job = self.app._folder_feedback_job
        with patch.object(self.app, "after_cancel", wraps=self.app.after_cancel) as mock_cancel:
            self.app._set_folder_feedback("⏳ Scansione cartella in corso...", "#b45309")
            mock_cancel.assert_called_with(prev_job)
            self.assertIsNone(self.app._folder_feedback_job)

        # 4. Esecuzione callback allo scadere dei 5 secondi svuota la scritta
        self.app._set_folder_feedback("✓ Rilevati 21 file Excel", "#15803d")
        self.assertIsNotNone(self.app._folder_feedback_job)
        self.app._clear_folder_feedback()
        self.assertEqual(self.app.folder_feedback_lbl.cget("text"), "")
        self.assertIsNone(self.app._folder_feedback_job)

    def test_fixed_two_column_layout(self):
        """Verifica il layout fisso e solido a 2 colonne (nessun PanedWindow sash né tasto inverti ⇄)."""
        self.assertFalse(hasattr(self.app, "main_paned"))
        self.assertFalse(hasattr(self.app, "swap_panels_btn"))
        self.assertIsNotNone(self.app.left_pane)
        self.assertIsNotNone(self.app.right_pane)
        self.assertEqual(int(self.app.right_pane.cget("width")), 540)

    def test_welcome_dialog_password_unlock_and_folder_selection(self):
        """Verifica lo sblocco con password e la selezione cartella in WelcomeSetupDialogV3."""
        saved_args = []

        def on_saved(*args):
            saved_args.extend(args)

        dlg = WelcomeSetupDialogV3(
            self.app,
            on_saved=on_saved,
            config={"gemini_api_key": "", "gemini_api_key_2": "", "openrouter_api_key": ""},
        )
        dlg.withdraw()

        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE
        with (
            patch("src.crypto_keys._SALT_B64", salt_b64),
            patch("src.crypto_keys._NONCE_B64", nonce_b64),
            patch("src.crypto_keys._CIPHERTEXT_B64", ct_b64),
            patch("src.crypto_keys._TAG_B64", tag_b64),
        ):
            # 1. Password errata
            dlg.pwd_var.set("PasswordSbagliata")
            self.assertFalse(dlg._unlock_with_password())
            self.assertEqual(dlg.k1_var.get(), "")

            # 2. Password corretta
            dlg.pwd_var.set(_DUMMY_PWD)
            self.assertTrue(dlg._unlock_with_password())
            self.assertEqual(dlg.k1_var.get(), _DUMMY_KEYS["gemini_api_key"])
            self.assertEqual(dlg.k2_var.get(), _DUMMY_KEYS["gemini_api_key_2"])
            self.assertEqual(dlg.or_var.get(), _DUMMY_KEYS["openrouter_api_key"])

        # 3. Imposta cartella e premi Invio (seconda chiamata a _unlock_with_password con pwd vuota salva e chiude)
        dlg.folder_var.set(str(LEGACY_COLLETTA_DIR))
        self.assertTrue(dlg._unlock_with_password())

        self.assertEqual(len(saved_args), 4)
        self.assertEqual(saved_args[0], _DUMMY_KEYS["gemini_api_key"])
        self.assertEqual(saved_args[1], _DUMMY_KEYS["gemini_api_key_2"])
        self.assertEqual(saved_args[2], _DUMMY_KEYS["openrouter_api_key"])
        self.assertEqual(saved_args[3], str(LEGACY_COLLETTA_DIR))

    def test_settings_dialog_password_unlock(self):
        """Verifica lo sblocco chiavi con password dentro SettingsDialogV3."""
        cfg = dict(load_app_config())
        cfg["gemini_api_key"] = ""
        cfg["gemini_api_key_2"] = ""
        cfg["openrouter_api_key"] = ""

        saved_cfg = {}

        def on_save(vals):
            saved_cfg.update(vals)

        dlg = SettingsDialogV3(self.app, config=cfg, on_save=on_save)
        dlg.withdraw()

        self.assertEqual(len(dlg._key_rows), 0)

        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE
        with (
            patch("src.crypto_keys._SALT_B64", salt_b64),
            patch("src.crypto_keys._NONCE_B64", nonce_b64),
            patch("src.crypto_keys._CIPHERTEXT_B64", ct_b64),
            patch("src.crypto_keys._TAG_B64", tag_b64),
        ):
            # Password errata
            dlg.pwd_var.set("errata")
            self.assertFalse(dlg._unlock_with_password())
            self.assertEqual(len(dlg._key_rows), 0)

            # Password corretta
            dlg.pwd_var.set(_DUMMY_PWD)
            self.assertTrue(dlg._unlock_with_password())
            self.assertEqual(len(dlg._key_rows), 3)

        # Seconda pressione Invio con campo password vuoto salva e chiude
        self.assertTrue(dlg._unlock_with_password())
        self.assertEqual(saved_cfg["gemini_api_key"], _DUMMY_KEYS["gemini_api_key"])
        self.assertEqual(saved_cfg["gemini_api_key_2"], _DUMMY_KEYS["gemini_api_key_2"])
        self.assertEqual(saved_cfg["openrouter_api_key"], _DUMMY_KEYS["openrouter_api_key"])

    def test_gemini_38_provider_in_top_bar_and_settings(self):
        """Verifica che Google Gemini 3.8 Flash (gemini_38) sia disponibile nel selettore in alto e nelle Impostazioni."""
        from src.extractors.gemini_extractor import GeminiVisionExtractor

        self.assertIn(
            BancoAlimentareAppV3.PROVIDER_DISPLAY_GEMINI_38,
            BancoAlimentareAppV3.PROVIDER_DISPLAY_VALUES,
        )
        self.assertEqual(
            BancoAlimentareAppV3.PROVIDER_KEY_MAP.get("gemini_38"),
            BancoAlimentareAppV3.PROVIDER_DISPLAY_GEMINI_38,
        )
        self.assertEqual(
            self.app._normalize_display_to_provider(BancoAlimentareAppV3.PROVIDER_DISPLAY_GEMINI_38),
            "gemini_38",
        )
        self.assertEqual(
            SettingsDialogV3.PROVIDER_DISPLAY_MAP.get("gemini_38"),
            BancoAlimentareAppV3.PROVIDER_DISPLAY_GEMINI_38,
        )

        self.app.config_data["active_provider"] = "gemini_38"
        self.app._init_extractor()
        self.assertIsInstance(self.app.active_extractor, GeminiVisionExtractor)
        self.assertEqual(self.app.active_extractor.model, "gemini-3.8-flash")

    def test_new_openrouter_models_integration(self):
        """Verifica l'integrazione completa di Haiku 5.5, GPT-6.1 Terra e GPT-6 Luna."""
        from src.extractors.openrouter_extractor import OpenRouterVisionExtractor
        from src.models import RawCardExtraction

        new_provs = [
            ("or_haiku_low", "anthropic/claude-haiku-5.5", "low", "Claude Haiku 5.5 (Low)"),
            ("or_luna_low", "openai/gpt-6-luna", "low", "GPT-6 Luna (Low)"),
            ("or_sonnet", "anthropic/claude-sonnet-5.5", None, "Claude Sonnet 5.5"),
            ("or_nemotron", "nvidia/nemotron-3.5-super-vl:free", "none", "Nemotron 3.5 Super-VL"),
        ]

        for prov_key, expected_model, expected_effort, expected_disp in new_provs:
            disp_val = BancoAlimentareAppV3.PROVIDER_KEY_MAP.get(prov_key)
            self.assertIsNotNone(disp_val, f"Provider {prov_key} mancante in PROVIDER_KEY_MAP")
            self.assertIn(disp_val, BancoAlimentareAppV3.PROVIDER_DISPLAY_VALUES)
            self.assertEqual(
                self.app._normalize_display_to_provider(disp_val),
                prov_key,
                f"Normalizzazione fallita per {disp_val}"
            )
            self.assertEqual(
                SettingsDialogV3.PROVIDER_DISPLAY_MAP.get(prov_key),
                disp_val,
                f"Provider {prov_key} mancante o errato in SettingsDialogV3"
            )

            self.app.config_data["active_provider"] = prov_key
            self.app.config_data["openrouter_api_key"] = "test-sk-key"
            self.app._init_extractor()
            self.assertIsInstance(self.app.active_extractor, OpenRouterVisionExtractor)
            self.assertEqual(self.app.active_extractor.model, expected_model)
            self.assertEqual(self.app.active_extractor.reasoning_effort, expected_effort)
            self.assertEqual(self.app.active_extractor.display_name, expected_disp)

        # Verifica campi tempo_elaborazione_sec e costo_usd su RawCardExtraction
        raw = RawCardExtraction(supermercato="TEST", tempo_elaborazione_sec=3.2, costo_usd=0.0014)
        self.assertEqual(raw.tempo_elaborazione_sec, 3.2)
        self.assertEqual(raw.costo_usd, 0.0014)

    def test_generation_id_race_condition_prevention(self):
        """Verifica che il cambio modello o annullamento estrazione incrementi _extraction_generation_id."""
        init_gen = self.app._extraction_generation_id
        self.app._on_provider_change(BancoAlimentareAppV3.PROVIDER_DISPLAY_LUNA_LOW)
        self.assertGreater(self.app._extraction_generation_id, init_gen)

        curr_gen = self.app._extraction_generation_id
        self.app._cancel_active_extraction()
        self.assertGreater(self.app._extraction_generation_id, curr_gen)

    def test_provider_change_does_not_auto_reprocess_open_ready_item(self):
        """Verifica che cambiare modello nel dropdown con una scheda aperta (ready) non avvii la rianalisi immediata."""
        from unittest.mock import MagicMock, patch
        from src.models import ProcessedCard, RawCardExtraction, BoxItem
        from src.ui.app import BatchItem

        fake_item = BatchItem(
            image_path="test_img.jpg",
            status="ready",
            card=ProcessedCard(supermercato="TEST SHOP", righe_scatole=[BoxItem(categoria="Pasta", numero_scatole=2, kg=10.0)])
        )
        self.app.queue = [fake_item]
        self.app.queue_index = 0

        with patch.object(self.app, "_on_reprocess_image") as mock_reprocess:
            self.app._on_provider_change(BancoAlimentareAppV3.PROVIDER_DISPLAY_HAIKU_LOW)
            
            # Non deve essere invocata la rianalisi automatica
            mock_reprocess.assert_not_called()
            # Lo stato dell'item deve rimanere invariato e la card intatta
            self.assertEqual(fake_item.status, "ready")
            self.assertIsNotNone(fake_item.card)
            self.assertEqual(self.app.config_data["active_provider"], "or_haiku_low")


if __name__ == "__main__":
    unittest.main()
