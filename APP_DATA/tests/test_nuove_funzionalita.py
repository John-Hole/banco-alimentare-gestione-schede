"""Unit tests per le nuove funzionalità V4 e struttura APP_DATA / V_UFFICIALE:
1. Cifratura e sblocco chiavi API tramite password (crypto_keys.py)
2. Sblocco chiavi con password e scelta cartella in WelcomeSetupDialogV3 e SettingsDialogV3
3. Notifica verde cartella con scomparsa automatica dopo 5 secondi (5000 ms)
4. Pannelli principali ridimensionabili (tk.PanedWindow) e invertibili (⇄ swap_panels)
5. Struttura pulita ROOT_DIR (BACKUP, SCANSIONI, FOGLI_COLLETTA) e APP_DATA_DIR
"""

import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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
    seal_from_config_interactive,
    unlock_giovanni_keys,
    update_encrypted_bundle_file,
)
from src.ui.app import BancoAlimentareAppV3
from src.ui.components_dialogs import WelcomeSetupDialogV3, SettingsDialogV3


_DUMMY_KEYS = {
    "gemini_api_key": "AQ.TEST_GEMINI_KEY_PRIMARY_12345",
    "gemini_api_key_2": "AQ.TEST_GEMINI_KEY_BACKUP_67890",
    "openrouter_api_key": "sk-or-v1-test_openrouter_key_abcdef",
}
_DUMMY_KEYS_WITH_TRELLO = {
    **_DUMMY_KEYS,
    "trello_api_key": "dummy_trello_api_key_999",
    "trello_token": "dummy_trello_token_888",
    "trello_list_id": "dummy_trello_list_777",
}
_DUMMY_PWD = "TestSuiteSecret99"
_DUMMY_BUNDLE = encrypt_keys_bundle(
    _DUMMY_PWD,
    _DUMMY_KEYS,
    b"0123456789abcdef",
    b"fedcba9876543210",
)
_DUMMY_BUNDLE_WITH_TRELLO = encrypt_keys_bundle(
    _DUMMY_PWD,
    _DUMMY_KEYS_WITH_TRELLO,
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
        prefix_trello = "ATTA" + "4d929f"
        pwd_marker = "".join(chr(c) for c in (74, 111, 104, 110, 66, 97, 110, 107, 50, 54))
        files_to_check = list((APP_DATA_DIR / "src").rglob("*.py")) + list((APP_DATA_DIR / "tests").rglob("*.py"))
        for extra_file in (
            APP_DATA_DIR / "config.template.json",
            ROOT_DIR / "README.md",
            ROOT_DIR / "COME_INSTALLARE.txt",
        ):
            if extra_file.is_file():
                files_to_check.append(extra_file)
        for checked_file in files_to_check:
            content = checked_file.read_text(encoding="utf-8")
            self.assertNotIn(prefix_gem, content, f"Secret leaked in {checked_file.name}")
            self.assertNotIn(prefix_or, content, f"Secret leaked in {checked_file.name}")
            self.assertNotIn(prefix_trello, content, f"Trello token leaked in {checked_file.name}")
            self.assertNotIn(pwd_marker, content, f"Password leaked in {checked_file.name}")

    def test_update_encrypted_bundle_file_isolated_roundtrip_and_errors(self):
        src_crypto = APP_DATA_DIR / "src" / "crypto_keys.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_crypto = Path(tmpdir) / "crypto_keys_copy.py"
            shutil.copy2(src_crypto, tmp_crypto)
            original_text = tmp_crypto.read_text(encoding="utf-8")

            # Error paths do not corrupt file
            with self.assertRaises(ValueError):
                update_encrypted_bundle_file("   ", _DUMMY_KEYS_WITH_TRELLO, target_path=tmp_crypto)
            with self.assertRaises(ValueError):
                update_encrypted_bundle_file(_DUMMY_PWD, {}, target_path=tmp_crypto)
            with self.assertRaises(ValueError):
                update_encrypted_bundle_file(
                    _DUMMY_PWD,
                    {"gemini_api_key": "abc", "trello_token": None},  # type: ignore[dict-item]
                    target_path=tmp_crypto,
                )
            with self.assertRaises(FileNotFoundError):
                update_encrypted_bundle_file(_DUMMY_PWD, _DUMMY_KEYS_WITH_TRELLO, target_path=Path(tmpdir) / "missing.py")
            bad_file = Path(tmpdir) / "bad.py"
            bad_file.write_text("x = 1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                update_encrypted_bundle_file(_DUMMY_PWD, _DUMMY_KEYS_WITH_TRELLO, target_path=bad_file)
            self.assertEqual(tmp_crypto.read_text(encoding="utf-8"), original_text)

            # 1st update with AI + Trello keys
            update_encrypted_bundle_file(_DUMMY_PWD, _DUMMY_KEYS_WITH_TRELLO, target_path=tmp_crypto)
            spec1 = importlib.util.spec_from_file_location("tmp_crypto_mod_1", tmp_crypto)
            mod1 = importlib.util.module_from_spec(spec1)
            spec1.loader.exec_module(mod1)
            self.assertEqual(mod1.unlock_giovanni_keys(_DUMMY_PWD), _DUMMY_KEYS_WITH_TRELLO)
            self.assertIsNone(mod1.unlock_giovanni_keys("WrongPwd"))

            # 2nd update generates fresh salt and nonce
            update_encrypted_bundle_file(_DUMMY_PWD, _DUMMY_KEYS_WITH_TRELLO, target_path=tmp_crypto)
            spec2 = importlib.util.spec_from_file_location("tmp_crypto_mod_2", tmp_crypto)
            mod2 = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(mod2)
            self.assertNotEqual(mod1._SALT_B64, mod2._SALT_B64)
            self.assertNotEqual(mod1._NONCE_B64, mod2._NONCE_B64)
            self.assertEqual(mod2.unlock_giovanni_keys(_DUMMY_PWD), _DUMMY_KEYS_WITH_TRELLO)

            # Short single-line ciphertext + single-quoted constants roundtrip
            sq_file = Path(tmpdir) / "single_quoted.py"
            sq_file.write_text(
                "_SALT_B64 = 'a'\n_NONCE_B64 = 'b'\n_CIPHERTEXT_B64 = ('c'\n    'd')\n_TAG_B64 = 'e'\n",
                encoding="utf-8",
            )
            short_keys = {"gemini_api_key": "k"}
            update_encrypted_bundle_file(_DUMMY_PWD, short_keys, target_path=sq_file)
            sq_text = sq_file.read_text(encoding="utf-8")
            self.assertIn('_CIPHERTEXT_B64 = "', sq_text)
            update_encrypted_bundle_file(_DUMMY_PWD, _DUMMY_KEYS_WITH_TRELLO, target_path=sq_file)
            from src.crypto_keys import _extract_bundle_constants_from_source
            consts = _extract_bundle_constants_from_source(sq_file.read_text(encoding="utf-8"))
            self.assertEqual(decrypt_keys_bundle(_DUMMY_PWD, *consts), _DUMMY_KEYS_WITH_TRELLO)

    def test_seal_from_config_interactive_isolated(self):
        src_crypto = APP_DATA_DIR / "src" / "crypto_keys.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            tmp_crypto = tmp_dir / "crypto_keys_copy.py"
            shutil.copy2(src_crypto, tmp_crypto)
            tmp_cfg = tmp_dir / "config.json"

            # Missing config -> False
            with redirect_stdout(io.StringIO()):
                self.assertFalse(seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto))

            # Empty sealable keys -> False
            tmp_cfg.write_text(json.dumps({"gemini_api_key": "  ", "gemini_model": "gemini-3.5-flash-lite"}), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertFalse(seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto))

            # Valid config with AI + Trello + unrelated key
            tmp_cfg.write_text(
                json.dumps({**_DUMMY_KEYS_WITH_TRELLO, "gemini_model": "gemini-3.5-flash-lite"}),
                encoding="utf-8",
            )

            # Password differs from existing bundle and user refuses confirmation -> False
            with (
                patch("src.crypto_keys.getpass.getpass", return_value=_DUMMY_PWD),
                patch("builtins.input", return_value="NO"),
                redirect_stdout(io.StringIO()) as out_no,
            ):
                ok_no = seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto)
            self.assertFalse(ok_no)
            self.assertNotIn(_DUMMY_KEYS_WITH_TRELLO["trello_token"], out_no.getvalue())

            # Password differs and user confirms with 'SI' on a Windows cp1252 stdout -> no UnicodeEncodeError
            cp1252_buf = io.BytesIO()
            cp1252_out = io.TextIOWrapper(cp1252_buf, encoding="cp1252", errors="strict", write_through=True)
            with (
                patch("src.crypto_keys.getpass.getpass", return_value=_DUMMY_PWD),
                patch("builtins.input", return_value="SI"),
                redirect_stdout(cp1252_out),
            ):
                ok_yes = seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto)
            self.assertTrue(ok_yes)
            printed_cp1252 = cp1252_buf.getvalue().decode("cp1252")
            self.assertIn("[CHIAVI]", printed_cp1252)
            self.assertIn("[OK]", printed_cp1252)
            for secret_val in _DUMMY_KEYS_WITH_TRELLO.values():
                self.assertNotIn(secret_val, printed_cp1252)

            spec = importlib.util.spec_from_file_location("tmp_crypto_cli", tmp_crypto)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertEqual(mod.unlock_giovanni_keys(_DUMMY_PWD), _DUMMY_KEYS_WITH_TRELLO)

            # Subsequent run with matching password does NOT require 'SI' confirmation
            with (
                patch("src.crypto_keys.getpass.getpass", return_value=_DUMMY_PWD),
                patch("builtins.input", side_effect=AssertionError("input() non doveva essere chiamato")),
                redirect_stdout(io.StringIO()),
            ):
                ok_match = seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto)
            self.assertTrue(ok_match)

            # Empty password or EOFError / KeyboardInterrupt -> False cleanly
            with (
                patch("src.crypto_keys.getpass.getpass", return_value="   "),
                redirect_stdout(io.StringIO()),
            ):
                self.assertFalse(seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto))
            with (
                patch("src.crypto_keys.getpass.getpass", side_effect=EOFError),
                redirect_stdout(io.StringIO()),
            ):
                self.assertFalse(seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_crypto))

            # Malformed or missing target_path -> False
            bad_target = tmp_dir / "bad_target.py"
            bad_target.write_text("x = 1\n", encoding="utf-8")
            with (
                patch("src.crypto_keys.getpass.getpass", return_value=_DUMMY_PWD),
                redirect_stdout(io.StringIO()),
            ):
                self.assertFalse(seal_from_config_interactive(config_path=tmp_cfg, target_path=bad_target))

            with (
                patch("src.crypto_keys.getpass.getpass", side_effect=AssertionError("getpass non doveva essere chiamato")),
                redirect_stdout(io.StringIO()),
            ):
                self.assertFalse(
                    seal_from_config_interactive(config_path=tmp_cfg, target_path=tmp_dir / "missing_target.py")
                )


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
        self._save_cfg_patcher = patch("src.ui.app.save_app_config")
        self._save_cfg_patcher.start()
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

    def tearDown(self):
        try:
            self.app.update()
            self.app.destroy()
        except Exception:
            pass
        finally:
            self._save_cfg_patcher.stop()

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
        cfg["trello_api_key"] = ""
        cfg["trello_token"] = ""
        cfg["trello_list_id"] = ""

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

    def test_welcome_dialog_unlocks_and_propagates_trello_and_app_callback(self):
        """Verifica sblocco unificato IA+Trello in WelcomeSetupDialogV3 mantenendo 4 argomenti su on_saved e integrazione app.py."""
        call_order = []
        saved_args = []
        unlocked_trello_payloads = []

        def on_saved(*args):
            call_order.append("on_saved")
            saved_args.extend(args)

        def on_trello(t_cfg):
            call_order.append("on_trello")
            unlocked_trello_payloads.append(t_cfg)

        dlg_cfg = {"gemini_api_key": "", "gemini_api_key_2": "", "openrouter_api_key": ""}
        dlg = WelcomeSetupDialogV3(
            self.app,
            on_saved=on_saved,
            config=dlg_cfg,
            on_trello_unlocked=on_trello,
        )
        dlg.withdraw()

        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE_WITH_TRELLO
        with (
            patch("src.crypto_keys._SALT_B64", salt_b64),
            patch("src.crypto_keys._NONCE_B64", nonce_b64),
            patch("src.crypto_keys._CIPHERTEXT_B64", ct_b64),
            patch("src.crypto_keys._TAG_B64", tag_b64),
        ):
            dlg.pwd_var.set(_DUMMY_PWD)
            self.assertTrue(dlg._unlock_with_password())

        self.assertEqual(dlg._unlocked_trello["trello_api_key"], _DUMMY_KEYS_WITH_TRELLO["trello_api_key"])
        self.assertEqual(dlg._unlocked_trello["trello_token"], _DUMMY_KEYS_WITH_TRELLO["trello_token"])
        self.assertEqual(dlg._unlocked_trello["trello_list_id"], _DUMMY_KEYS_WITH_TRELLO["trello_list_id"])
        self.assertEqual(dlg_cfg["trello_api_key"], _DUMMY_KEYS_WITH_TRELLO["trello_api_key"])
        self.assertIn("Trello", dlg.unlock_status_lbl.cget("text"))
        self.assertIn("Trello", dlg.status_lbl.cget("text"))

        dlg.folder_var.set(str(LEGACY_COLLETTA_DIR))
        dlg._save_and_activate()

        self.assertEqual(call_order, ["on_trello", "on_saved"])
        self.assertEqual(len(saved_args), 4)
        self.assertEqual(len(unlocked_trello_payloads), 1)
        self.assertEqual(unlocked_trello_payloads[0]["trello_token"], _DUMMY_KEYS_WITH_TRELLO["trello_token"])

        # Verifica metodo reale app._on_welcome_trello_unlocked e wiring in _show_welcome
        self.app.config_data["trello_api_key"] = ""
        self.app.config_data["trello_token"] = ""
        self.app.config_data["trello_list_id"] = ""
        self.app._on_welcome_trello_unlocked(
            {
                "trello_api_key": _DUMMY_KEYS_WITH_TRELLO["trello_api_key"],
                "trello_token": _DUMMY_KEYS_WITH_TRELLO["trello_token"],
                "trello_list_id": _DUMMY_KEYS_WITH_TRELLO["trello_list_id"],
                "ignored_key": "should_not_copy",
            }
        )
        self.assertEqual(self.app.config_data["trello_api_key"], _DUMMY_KEYS_WITH_TRELLO["trello_api_key"])
        self.assertEqual(self.app.config_data["trello_token"], _DUMMY_KEYS_WITH_TRELLO["trello_token"])
        self.assertEqual(self.app.config_data["trello_list_id"], _DUMMY_KEYS_WITH_TRELLO["trello_list_id"])
        self.assertNotIn("ignored_key", self.app.config_data)

        with patch("src.ui.app.WelcomeSetupDialogV3") as mock_welcome:
            self.app._show_welcome()
            mock_welcome.assert_called_once()
            self.assertEqual(
                mock_welcome.call_args.kwargs.get("on_trello_unlocked"),
                self.app._on_welcome_trello_unlocked,
            )

    def test_settings_dialog_trello_banner_hidden_advanced_and_unlock(self):
        """Verifica pannello avanzato Trello chiuso di default, banner dinamico, sblocco e preservazione al _save()."""
        cfg = dict(load_app_config())
        cfg["gemini_api_key"] = ""
        cfg["gemini_api_key_2"] = ""
        cfg["openrouter_api_key"] = ""
        cfg["trello_api_key"] = ""
        cfg["trello_token"] = ""
        cfg["trello_list_id"] = ""

        saved_cfg = {}
        dlg = SettingsDialogV3(self.app, config=cfg, on_save=lambda vals: saved_cfg.update(vals))
        dlg.withdraw()

        # 1. Pannello avanzato chiuso/nascosto di default, stato in attesa di sblocco
        self.assertFalse(dlg._trello_adv_expanded)
        self.assertEqual(dlg.trello_adv_frame.winfo_manager(), "")
        self.assertIn("Trello in attesa di sblocco", dlg.trello_status_lbl.cget("text"))
        self.assertIn("Opzioni Avanzate Trello", dlg.trello_adv_toggle_btn.cget("text"))

        # 2. Toggle apre e richiude il pannello avanzato
        dlg._toggle_trello_advanced()
        self.assertTrue(dlg._trello_adv_expanded)
        self.assertEqual(dlg.trello_adv_frame.winfo_manager(), "pack")
        dlg._toggle_trello_advanced()
        self.assertFalse(dlg._trello_adv_expanded)
        self.assertEqual(dlg.trello_adv_frame.winfo_manager(), "")

        # 3. Sblocco con password popola anche Trello e aggiorna il banner fisso a verde
        salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE_WITH_TRELLO
        with (
            patch("src.crypto_keys._SALT_B64", salt_b64),
            patch("src.crypto_keys._NONCE_B64", nonce_b64),
            patch("src.crypto_keys._CIPHERTEXT_B64", ct_b64),
            patch("src.crypto_keys._TAG_B64", tag_b64),
        ):
            dlg.pwd_var.set(_DUMMY_PWD)
            self.assertTrue(dlg._unlock_with_password())

        self.assertEqual(dlg.trello_api_key_var.get(), _DUMMY_KEYS_WITH_TRELLO["trello_api_key"])
        self.assertEqual(dlg.trello_token_var.get(), _DUMMY_KEYS_WITH_TRELLO["trello_token"])
        self.assertEqual(dlg.trello_list_id_var.get(), _DUMMY_KEYS_WITH_TRELLO["trello_list_id"])
        self.assertIn("Collegamento Fisso Attivo", dlg.trello_status_lbl.cget("text"))
        self.assertIn("Trello", dlg.unlock_status_lbl.cget("text"))

        # 4. Chiave parziale (manca list_id) riporta il banner in attesa; ripristinando list_id torna attivo
        dlg.trello_list_id_var.set("")
        self.assertIn("Trello in attesa di sblocco", dlg.trello_status_lbl.cget("text"))
        dlg.trello_list_id_var.set(_DUMMY_KEYS_WITH_TRELLO["trello_list_id"])
        self.assertIn("Collegamento Fisso Attivo", dlg.trello_status_lbl.cget("text"))

        # 5. Anche con pannello avanzato chiuso, _save() preserva e salva tutte e 3 le chiavi Trello
        self.assertFalse(dlg._trello_adv_expanded)
        dlg._save()
        self.assertEqual(saved_cfg["trello_api_key"], _DUMMY_KEYS_WITH_TRELLO["trello_api_key"])
        self.assertEqual(saved_cfg["trello_token"], _DUMMY_KEYS_WITH_TRELLO["trello_token"])
        self.assertEqual(saved_cfg["trello_list_id"], _DUMMY_KEYS_WITH_TRELLO["trello_list_id"])

        # 6. Apertura successiva con Trello già configurato e initial_tab su Flusso IA: banner verde, avanzate ancora chiuse, occhio token funzionante
        dlg2 = SettingsDialogV3(
            self.app,
            config=saved_cfg,
            on_save=lambda vals: saved_cfg.update(vals),
            initial_tab="🚀 Flusso IA & Modelli",
        )
        dlg2.withdraw()
        self.assertEqual(dlg2.tabs.get(), "🚀 Flusso IA & Modelli")
        self.assertFalse(dlg2._trello_adv_expanded)
        self.assertEqual(dlg2.trello_adv_frame.winfo_manager(), "")
        self.assertIn("Collegamento Fisso Attivo", dlg2.trello_status_lbl.cget("text"))
        dlg2._toggle_trello_advanced()
        self.assertEqual(dlg2.trello_token_entry.cget("show"), "•")
        dlg2._toggle_trello_token_vis()
        self.assertEqual(dlg2.trello_token_entry.cget("show"), "")
        dlg2._toggle_trello_token_vis()
        self.assertEqual(dlg2.trello_token_entry.cget("show"), "•")
        dlg2.destroy()

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
            ("or_muse_minimal", "meta/muse-ember", "minimal", "Meta Muse Ember (Minimal)"),
            ("or_muse_low", "meta/muse-ember", "low", "Meta Muse Ember (Low)"),
            ("or_haiku_low", "anthropic/claude-haiku-5.5", "low", "Claude Haiku 5.5 (Low)"),
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
            # Modelli ufficiali: fallback automatico attivo
            self.assertIsNotNone(self.app.active_extractor.fallback_extractor)
            self.assertTrue(self.app.active_extractor.enable_auto_fallback)

        # Verifica normalizzazione retrocompatibile da chiavi ed etichette legacy
        self.assertEqual(self.app._normalize_display_to_provider("or_luna_low"), "or_muse_minimal")
        self.assertEqual(self.app._normalize_display_to_provider("GPT-6 Luna (Low)"), "or_muse_minimal")

        # Verifica campi tempo_elaborazione_sec e costo_usd su RawCardExtraction
        raw = RawCardExtraction(supermercato="TEST", tempo_elaborazione_sec=3.2, costo_usd=0.0014)
        self.assertEqual(raw.tempo_elaborazione_sec, 3.2)
        self.assertEqual(raw.costo_usd, 0.0014)

    def test_generation_id_race_condition_prevention(self):
        """Verifica che il cambio modello o annullamento estrazione incrementi _extraction_generation_id."""
        init_gen = self.app._extraction_generation_id
        self.app._on_provider_change(BancoAlimentareAppV3.PROVIDER_DISPLAY_MUSE_MINIMAL)
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

    def test_consigli_moved_to_settings_and_live_trello_unlock_sync(self):
        """Verifica rimozione feedback_btn dalla barra alta, menu -> Settings(Consigli) e sblocco live Trello nel tab Consigli."""
        import time
        from src.services.feedback_service import FeedbackService, TRELLO_CARDS_ENDPOINT

        # 1. Nessun pulsante 💡 Consigli nella barra superiore principale (né in alcun widget CTkButton/Button)
        import tkinter as tk
        import customtkinter as ctk

        self.assertFalse(hasattr(self.app, "feedback_btn"))

        def _walk_button_texts(widget):
            out = []
            for child in widget.winfo_children():
                if isinstance(child, (ctk.CTkButton, tk.Button)):
                    try:
                        t = child.cget("text")
                        if isinstance(t, str) and t:
                            out.append(t)
                    except Exception:
                        pass
                out.extend(_walk_button_texts(child))
            return out

        btn_texts = _walk_button_texts(self.app)
        self.assertFalse(
            any("Consigli" in txt for txt in btn_texts),
            f"Nessun pulsante 'Consigli' deve restare nella finestra principale: {btn_texts!r}",
        )

        # 2. _build_feedback_context legge provider, excel_file e supermercato correnti
        self.app.config_data["active_provider"] = "gemini_38"
        if hasattr(self.app, "supermarket_var"):
            self.app.supermarket_var.set("CONAD PERUGIA TEST")
        ctx = self.app._build_feedback_context()
        self.assertEqual(ctx["provider"], "gemini_38")
        self.assertIn("excel_file", ctx)
        self.assertEqual(ctx["supermercato"], "CONAD PERUGIA TEST")

        # 3. _open_settings e _open_feedback_dialog (anche da menu ☰ Altre Opzioni) passano context e initial_tab
        with patch("src.ui.app.SettingsDialogV3") as mock_settings_cls:
            self.app._open_settings()
            mock_settings_cls.assert_called_once()
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("feedback_context"), ctx)
            self.assertNotIn("initial_tab", mock_settings_cls.call_args.kwargs)

            mock_settings_cls.reset_mock()
            self.app._open_feedback_dialog()
            mock_settings_cls.assert_called_once()
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("initial_tab"), "💡 Consigli")
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("feedback_context"), ctx)

            # Verifica voce menu ☰ Altre Opzioni -> 💡 Invia Consiglio / Segnalazione...
            mock_settings_cls.reset_mock()
            end_idx = self.app.more_options_menu.index("end")
            found_idx = None
            for idx in range((end_idx or 0) + 1):
                try:
                    lbl = self.app.more_options_menu.entrycget(idx, "label")
                except Exception:
                    continue
                if "Invia Consiglio" in lbl:
                    found_idx = idx
                    break
            self.assertIsNotNone(found_idx, "Voce 'Invia Consiglio / Segnalazione...' mancante in more_options_menu")
            self.app.more_options_menu.invoke(found_idx)
            mock_settings_cls.assert_called_once()
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("initial_tab"), "💡 Consigli")
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("feedback_context"), ctx)

            mock_settings_cls.reset_mock()
            self.app._open_ia_stats_dialog()
            mock_settings_cls.assert_called_once()
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("initial_tab"), "🚀 Flusso IA & Modelli")
            self.assertEqual(mock_settings_cls.call_args.kwargs.get("feedback_context"), ctx)

        # 4. Apertura reale di _open_feedback_dialog(), sblocco Trello con password in Tab 1 e invio live dal Tab Consigli
        self.app.config_data["trello_api_key"] = ""
        self.app.config_data["trello_token"] = ""
        self.app.config_data["trello_list_id"] = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = FeedbackService(custom_dir=Path(tmpdir))
            dlg = None
            try:
                with patch("src.ui.components_dialogs.get_feedback_service", return_value=svc):
                    dlg = self.app._open_feedback_dialog()
                dlg.withdraw()

                self.assertIsInstance(dlg, SettingsDialogV3)
                self.assertEqual(dlg.tabs.get(), "💡 Consigli")
                self.assertIsNotNone(dlg.feedback_panel)
                self.assertEqual(dlg.feedback_panel._ctx, ctx)
                self.assertIn("Solo Documento Locale", dlg.feedback_panel.trello_badge.cget("text"))

                # Sblocco password in Tab 1 deve aggiornare subito trello_badge nel Tab Consigli
                salt_b64, nonce_b64, ct_b64, tag_b64 = _DUMMY_BUNDLE_WITH_TRELLO
                with (
                    patch("src.crypto_keys._SALT_B64", salt_b64),
                    patch("src.crypto_keys._NONCE_B64", nonce_b64),
                    patch("src.crypto_keys._CIPHERTEXT_B64", ct_b64),
                    patch("src.crypto_keys._TAG_B64", tag_b64),
                ):
                    dlg.pwd_var.set(_DUMMY_PWD)
                    self.assertTrue(dlg._unlock_with_password())

                self.assertIn("Trello Collegato", dlg.feedback_panel.trello_badge.cget("text"))

                # Invio consiglio PRIMA di cliccare Salva Modifiche: usa subito le chiavi Trello sbloccate e NON chiude SettingsDialog
                mock_resp = MagicMock(status_code=201)
                mock_resp.json.return_value = {"id": "card_unlock_1", "shortUrl": "https://trello.com/c/unlock1"}
                with patch("src.services.feedback_service.requests.post", return_value=mock_resp) as mock_post:
                    dlg.feedback_panel.author_var.set("Volontario Test")
                    dlg.feedback_panel.message_box.insert("1.0", "Consiglio inviato subito dopo sblocco")
                    dlg.feedback_panel._submit()

                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        self.app.update()
                        if (
                            "creata Card su Trello" in dlg.feedback_panel.status_lbl.cget("text")
                            and dlg.feedback_panel.send_btn.cget("state") == "normal"
                        ):
                            break
                        time.sleep(0.02)

                self.assertIn("creata Card su Trello", dlg.feedback_panel.status_lbl.cget("text"))
                self.assertTrue(dlg.winfo_exists(), "SettingsDialogV3 non deve chiudersi dopo l'invio di un consiglio")
                mock_post.assert_called_once()
                self.assertEqual(mock_post.call_args[0][0], TRELLO_CARDS_ENDPOINT)
                self.assertEqual(
                    mock_post.call_args[1]["params"],
                    {
                        "key": _DUMMY_KEYS_WITH_TRELLO["trello_api_key"],
                        "token": _DUMMY_KEYS_WITH_TRELLO["trello_token"],
                    },
                )
                self.assertEqual(
                    mock_post.call_args[1]["data"]["idList"],
                    _DUMMY_KEYS_WITH_TRELLO["trello_list_id"],
                )
                saved_items = svc.list_local_feedback(limit=5)
                self.assertEqual(len(saved_items), 1)
                self.assertTrue(saved_items[0]["trello_synced"])
                self.assertEqual(saved_items[0]["context"].get("supermercato"), "CONAD PERUGIA TEST")

                # Svuotando list_id il badge nel Tab Consigli torna subito locale
                dlg.trello_list_id_var.set("")
                self.assertIn("Solo Documento Locale", dlg.feedback_panel.trello_badge.cget("text"))
            finally:
                if dlg is not None:
                    try:
                        if dlg.winfo_exists():
                            dlg.destroy()
                    except Exception:
                        pass


if __name__ == "__main__":
    unittest.main()
