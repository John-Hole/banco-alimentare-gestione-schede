"""Test unitari per SessionLogger e tracciamento trasparente dei modelli IA."""

import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.session_logger import SessionLogger, mask_api_key, get_session_logger


class TestSessionLogger(unittest.TestCase):

    def test_mask_api_key(self):
        """Verifica che le chiavi vengano mascherate correttamente per la privacy."""
        self.assertEqual(mask_api_key(None), "[NON CONFIGURATA]")
        self.assertEqual(mask_api_key(""), "[NON CONFIGURATA]")
        self.assertEqual(mask_api_key("12345"), "***")
        self.assertEqual(mask_api_key("12345678"), "***")

        key = "AQ.Ab8FAKE_DUMMY_KEY_FOR_UNIT_TESTING_ONLY_0000_cvtw"
        masked = mask_api_key(key)
        self.assertTrue(masked.startswith("AQ.Ab8"))
        self.assertTrue(masked.endswith("cvtw"))
        self.assertIn("...", masked)
        self.assertNotIn("FAKE_DUMMY_KEY_FOR_UNIT_TESTING_ONLY", masked)

    def test_singleton_instance(self):
        """Verifica che get_session_logger restituisca la stessa istanza singleton."""
        logger1 = get_session_logger()
        logger2 = get_session_logger()
        self.assertIs(logger1, logger2)

    def test_log_file_creation(self):
        """Verifica che il file di log di sessione esista fisicamente su disco."""
        logger = get_session_logger()
        log_path = logger.get_current_log_path()
        self.assertTrue(log_path.exists())
        self.assertTrue(log_path.name.startswith("session_"))
        self.assertTrue(log_path.name.endswith(".log"))

    def test_log_session_start_banner(self):
        """Verifica che il banner iniziale scriva le 3 sezioni cascata, i 2 switch arbitro, la taratura e mascheri le chiavi."""
        logger = get_session_logger()
        cfg = {
            "active_provider": "cascade",
            "gemini_api_key": "AQ.TEST_KEY_GEMINI_1_LONG",
            "gemini_api_key_2": "AQ.TEST_KEY_GEMINI_2_LONG",
            "gemini_model": "gemini-3.5-flash-lite",
            "openrouter_api_key": "sk-or-v1-TEST_OPENROUTER_LONG",
            "cascade_free_primary_mode": "gemini_35",
            "cascade_paid_mode": "haiku",
            "cascade_fallback_mode": "nemotron_free",
            "openrouter_free_model": "nvidia/nemotron-3.5-super-vl:free",
            "cascade_arbitrator_model": "anthropic/claude-haiku-5.5",
            "openrouter_arbitrator_model": "anthropic/claude-haiku-5.5",
            "enable_arbitrator_on_free": True,
            "enable_arbitrator_on_paid": False,
            "last_excel_path": "D:/test/Colletta 2025 Test.xls",
            "scanner_dpi": 300,
            "scanner_default_source": "adf",
            "image_brightness": 1.15,
            "image_contrast": 2.25,
            "image_sharpness": 1.40,
        }
        logger.log_session_start(cfg)
        content = logger.read_current_log()

        self.assertIn("BANCO ALIMENTARE — LOG DI SESSIONE", content)
        self.assertIn("MODALITÀ PROVIDER ATTIVA: CASCADE", content)
        self.assertIn("SEZIONE 1 - Google Free", content)
        self.assertIn("SEZIONE 2 - OpenRouter Pagamento", content)
        self.assertIn("SEZIONE 3 - Fallback Free OR", content)
        self.assertIn("ARBITRO MATEMATICO", content)
        self.assertIn("Su modelli Free: ATTIVO | Su modelli a Pagamento: DISATTIVATO", content)
        self.assertIn("gemini-3.5-flash-lite", content)
        self.assertIn("anthropic/claude-haiku-5.5", content)
        self.assertIn("nvidia/nemotron-3.5-super-vl:free", content)
        self.assertIn("Luminosità=1.15 | Contrasto=2.25 | Nitidezza=1.40", content)
        self.assertIn(mask_api_key("AQ.TEST_KEY_GEMINI_1_LONG"), content)
        self.assertIn(mask_api_key("AQ.TEST_KEY_GEMINI_2_LONG"), content)
        self.assertIn(mask_api_key("sk-or-v1-TEST_OPENROUTER_LONG"), content)
        # Le chiavi in chiaro NON devono comparire
        self.assertNotIn("TEST_KEY_GEMINI_1_LONG", content)
        self.assertNotIn("TEST_KEY_GEMINI_2_LONG", content)
        self.assertNotIn("TEST_OPENROUTER_LONG", content)

    def test_log_config_change_masks_secrets(self):
        """Verifica che log_config_change mascheri tutte le chiavi API, token Trello e password."""
        logger = get_session_logger()
        secret_gem = "AIzaSySECRET_GEMINI_PLAINTEXT_VALUE_999"
        secret_or = "sk-or-v1-SECRET_OPENROUTER_PLAINTEXT_888"
        secret_trello_k = "trello_secret_apikey_777777"
        secret_trello_t = "ATTA_secret_trello_token_666666"

        logger.log_config_change("gemini_api_key", "", secret_gem)
        logger.log_config_change("openrouter_api_key", secret_or, "")
        logger.log_config_change("trello_api_key", "", secret_trello_k)
        logger.log_config_change("trello_token", "", secret_trello_t)
        logger.log_config_change("scanner_dpi", 200, 300)
        logger.log_config_change("active_provider", "cascade", "or_haiku_low")

        content = logger.read_current_log()
        self.assertNotIn(secret_gem, content)
        self.assertNotIn(secret_or, content)
        self.assertNotIn(secret_trello_k, content)
        self.assertNotIn(secret_trello_t, content)
        self.assertIn(mask_api_key(secret_gem), content)
        self.assertIn(mask_api_key(secret_or), content)
        self.assertIn(mask_api_key(secret_trello_k), content)
        self.assertIn(mask_api_key(secret_trello_t), content)
        self.assertIn("Aggiornato parametro 'scanner_dpi': '200' -> '300'", content)
        self.assertIn("Aggiornato parametro 'active_provider': 'cascade' -> 'or_haiku_low'", content)

    def test_log_ai_calls_and_results(self):
        """Verifica la corretta registrazione di chiamate a modelli specifici ed esiti."""
        logger = get_session_logger()

        # 1. Tentativo con Gemini 3.5 Lite
        logger.log_ai_attempt("PIANO A - Google Gemini", "Google", "gemini-3.5-flash-lite", key_label="Google 1")
        logger.log_ai_result("PIANO A - Google Gemini", "Google", "gemini-3.5-flash-lite", 200, 1.35, tokens=1450, is_success=True)

        # 2. Fallback su errore 429
        logger.log_ai_result("PIANO A - Google Gemini", "Google", "gemini-3.5-flash-lite", 429, 0.45, is_success=False, error_msg="Resource Exhausted")
        logger.log_ai_fallback("Piano A", "Piano B", "google/gemini-2.5-flash", reason="Quota 429 su Google")

        # 3. Controllo somme matematiche e arbitro
        logger.log_math_validation(
            [{"categoria": "Pasta", "calcolato": 25.0, "dichiarato": 20.0}],
            will_arbitrate=True,
            arbitrator_model="anthropic/claude-sonnet-5.5",
        )
        logger.log_arbitration_result("anthropic/claude-sonnet-5.5", success=True, duration_sec=2.1, details="Somma corretta a 25.0 kg")

        content = logger.read_current_log()
        self.assertIn("gemini-3.5-flash-lite", content)
        self.assertIn("Token totali: 1450", content)
        self.assertIn("QUOTA ESAURITA (HTTP 429)", content)
        self.assertIn("FALLBACK IA", content)
        self.assertIn("google/gemini-2.5-flash", content)
        self.assertIn("anthropic/claude-sonnet-5.5", content)
        self.assertIn("Arbitraggio completato con successo", content)

    def test_orientation_and_excel_logging(self):
        """Verifica la registrazione di eventi di scanner/orientamento e scrittura Excel."""
        logger = get_session_logger()
        logger.log_orientation_event("test_scheda.jpg", 180, "Rilevata capovolta: ruotata di 180°")
        logger.log_excel_event("Salvataggio scheda completato", sheet_name="CONAD PERUGIA", row=7, details="Viaggio: A2")

        content = logger.read_current_log()
        self.assertIn("[ORIENTAMENTO] 'test_scheda.jpg': angolo rilevato=180°", content)
        self.assertIn("[EXCEL] Salvataggio scheda completato su foglio 'CONAD PERUGIA' riga 7", content)


if __name__ == "__main__":
    unittest.main()
