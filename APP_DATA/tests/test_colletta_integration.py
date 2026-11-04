"""Test di integrazione per il selettore file, il rilevamento formato reale 2025 e il suggeritore intelligente."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.colletta_service import CollettaService, COLLETTA_DIR
from src.excel.excel_manager import ExcelManager
from src.models import RawCardExtraction, BoxItem
from src.ui.app import BancoAlimentareAppV3


class TestCollettaIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.service = CollettaService.get_instance()

    def test_available_files_detected(self):
        """Verifica che tutti i 21 file della Colletta siano stati trovati e catalogati."""
        files = self.service.list_available_files()
        self.assertGreaterEqual(len(files), 20, "Almeno 20 file di zona devono essere presenti")
        
        # Verifica la presenza di Perugia, Assisi, Foligno
        zone_names = [f["zone"].lower() for f in files]
        self.assertTrue(any("perugia" in z for z in zone_names))
        self.assertTrue(any("assisi" in z for z in zone_names))
        self.assertTrue(any("foligno" in z for z in zone_names))

    def test_supermarket_indexed_count(self):
        """Verifica che i supermercati siano stati indicizzati correttamente."""
        supermarkets = self.service.index_data.get("supermarkets", [])
        self.assertGreater(len(supermarkets), 300, "Almeno 300 supermercati indicizzati")

    def test_smart_matching_same_zone(self):
        """Verifica che una scheda di Perugia sia agganciata a Perugia."""
        matches = self.service.find_matches(
            "Tigre Strada Ponte d'Oddi Perugia",
            current_file="Colletta 2025 Perugia.xls",
            top_n=1
        )
        self.assertTrue(len(matches) > 0)
        best = matches[0]
        self.assertEqual(best["sheet_name"], "Tigre Ponte d'Oddi")
        self.assertTrue(best["is_current_file"])
        self.assertGreaterEqual(best["score"], 0.90)

    def test_smart_matching_different_zone(self):
        """Verifica che una scheda di Bastia/Assisi sia riconosciuta anche se è attivo Foligno."""
        matches = self.service.find_matches(
            "Conad Superstore Bastia Tabacchificio",
            current_file="Colletta 2025 Foligno.xls",
            top_n=1
        )
        self.assertTrue(len(matches) > 0)
        best = matches[0]
        self.assertIn("Assisi", best["zone"])
        self.assertEqual(best["sheet_name"], "Conad Supers Tabac Bastia")
        self.assertFalse(best["is_current_file"])
        self.assertGreaterEqual(best["score"], 0.70)

    def test_ui_components_and_suggestion_banner(self):
        """Verifica che l'app UI abbia i controlli file in alto e il banner di suggerimento."""
        app = BancoAlimentareAppV3()
        app.withdraw()
        try:
            # Verifica presenza selettore file e pill Excel
            self.assertTrue(hasattr(app, "file_menu"))
            self.assertTrue(hasattr(app, "browse_file_btn"))
            self.assertTrue(hasattr(app, "open_excel_btn"))
            self.assertTrue(hasattr(app, "suggestion_banner"))

            # Simula elaborazione di una scheda di un'altra zona
            raw = RawCardExtraction(
                supermercato="Coop Il Ducato Via dei Filosofi Spoleto",
                numero_scheda="A1",
                righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)]
            )
            # Forza file corrente su Foligno
            app.excel_mgr.set_target_file("Colletta 2025 Foligno.xls")
            app._apply_raw_extraction(raw)

            # Verifica che il banner di suggerimento sia stato attivato per Spoleto!
            self.assertIsNotNone(app._pending_suggestion_action)
            self.assertIn("Spoleto", app.suggestion_title_lbl.cget("text"))

            app.update()
        finally:
            try:
                app.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
