"""Test unitari per HistoryService (Archivio Storico Scansioni e Dataset Ground Truth)."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import (
    BoxItem,
    CategoryDetail,
    DeclaredTotals,
    FlagSeverity,
    ProcessedCard,
    RawCardExtraction,
    ValidationFlag,
)
from src.services.history_service import HistoryService, sanitize_filename


class TestHistoryService(unittest.TestCase):

    def setUp(self):
        # Cartella temporanea isolata per i test
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_storico_"))
        self.service = HistoryService(custom_storico_dir=self.temp_dir)

        # Crea un'immagine di test
        self.dummy_img = self.temp_dir / "dummy_scan.jpg"
        img = Image.new("RGB", (100, 100), color="white")
        img.save(self.dummy_img, format="JPEG")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sanitize_filename(self):
        """Verifica la corretta sanificazione dei nomi per Windows."""
        # Caratteri vietati: \ / : * ? " < > |
        self.assertEqual(sanitize_filename("COOP: PERUGIA / VIA SAN SISTO *?"), "COOP_ PERUGIA _ VIA SAN SISTO __")
        self.assertEqual(sanitize_filename(""), "SENZA_NOME")
        self.assertEqual(sanitize_filename("   Punto Vendita...   "), "Punto Vendita")
        self.assertEqual(sanitize_filename("A" * 120, max_length=50), "A" * 50)

    def test_archive_raw_scan(self):
        """Verifica il salvataggio preventivo delle scansioni grezze."""
        archived = self.service.archive_raw_scan(self.dummy_img, prefix="test_raw")
        self.assertIsNotNone(archived)
        self.assertTrue(archived.exists())
        self.assertIn("_scansioni_grezze", str(archived))
        self.assertTrue(archived.name.startswith("test_raw_"))

    def test_archive_card_creates_folder_and_files(self):
        """Verifica che archive_card crei la cartella del supermercato, l'immagine e il JSON."""
        sheet_name = "COOP - C.C. IL TRIANGOLO"
        transport = "A1"

        raw = RawCardExtraction(
            supermercato=sheet_name,
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Olio", kg=5.0),
            ],
            totali_dichiarati=DeclaredTotals(kg_totale=15.0, scatole_totale=2),
            note_o_anomalie="Test IA",
        )

        processed = ProcessedCard(
            supermercato_rilevato=sheet_name,
            supermercato_confermato=sheet_name,
            numero_scheda_rilevato="1",
            numero_scheda_risolto="A1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Olio", kg=5.0),
            ],
            totali_dichiarati=DeclaredTotals(kg_totale=15.0, scatole_totale=2),
            totale_kg_calcolati=15.0,
            totale_scatole_calcolate=2,
            categorie={
                "Pasta": CategoryDetail(
                    categoria="Pasta",
                    kg_calcolati=10.0,
                    scatole_calcolate=1,
                    kg_dichiarati=10.0,
                    scatole_dichiarate=1,
                    kg_col=14,
                    box_col=15,
                ),
                "Olio": CategoryDetail(
                    categoria="Olio",
                    kg_calcolati=5.0,
                    scatole_calcolate=1,
                    kg_dichiarati=5.0,
                    scatole_dichiarate=1,
                    kg_col=8,
                    box_col=9,
                ),
            },
            flags=[
                ValidationFlag(
                    severity=FlagSeverity.GREEN,
                    field="Totale",
                    message="Verifica matematica OK",
                )
            ],
            pronto_per_scrittura=True,
        )

        result = self.service.archive_card(
            image_path=self.dummy_img,
            sheet_name=sheet_name,
            transport=transport,
            processed_card=processed,
            raw_extraction=raw,
            model_name="gemini-3.5-flash-lite",
            excel_target="Colletta 2025 Perugia.xls",
            row_idx=6,
            preset_id=3,
            session_id="session_123",
        )

        self.assertTrue(result["success"])
        market_dir = Path(result["market_dir"])
        self.assertTrue(market_dir.exists())
        self.assertTrue(market_dir.is_dir())

        dest_img = Path(result["dest_img_path"])
        self.assertTrue(dest_img.exists())
        self.assertTrue(dest_img.name.startswith("A1_"))

        dest_json = Path(result["dest_json_path"])
        self.assertTrue(dest_json.exists())

        # Verifica contenuto JSON
        with open(dest_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["supermercato"]["confermato"], sheet_name)
        self.assertEqual(data["trasporto"]["confermato"], transport)
        self.assertEqual(data["totali_confermati_excel"]["kg_totali"], 15.0)
        self.assertEqual(data["totali_confermati_excel"]["scatole_totali"], 2)
        self.assertEqual(data["modello_ia"], "gemini-3.5-flash-lite")
        self.assertFalse(data["modifiche_manuali_operatore"])
        self.assertIn("Pasta", data["totali_confermati_excel"]["categorie"])

    def test_indice_storico_updated(self):
        """Verifica che l'indice globale indice_storico.json venga aggiornato con totali corretti."""
        processed1 = ProcessedCard(
            supermercato_confermato="CONAD_A",
            totale_kg_calcolati=25.0,
            totale_scatole_calcolate=3,
        )
        processed2 = ProcessedCard(
            supermercato_confermato="COOP_B",
            totale_kg_calcolati=35.0,
            totale_scatole_calcolate=4,
        )

        self.service.archive_card(
            image_path=self.dummy_img,
            sheet_name="CONAD_A",
            transport="A1",
            processed_card=processed1,
        )
        self.service.archive_card(
            image_path=self.dummy_img,
            sheet_name="COOP_B",
            transport="A2",
            processed_card=processed2,
        )

        stats = self.service.get_history_stats()
        self.assertEqual(stats["totale_schede"], 2)
        self.assertEqual(stats["totale_supermercati"], 2)
        self.assertEqual(stats["totale_kg"], 60.0)
        self.assertEqual(stats["totale_scatole"], 7)

        # Legge il file indice
        with open(self.service.index_file, "r", encoding="utf-8") as f:
            index_data = json.load(f)

        self.assertEqual(len(index_data["schede"]), 2)
        self.assertEqual(index_data["schede"][0]["supermercato"], "CONAD_A")
        self.assertEqual(index_data["schede"][1]["supermercato"], "COOP_B")

    def test_operator_modifications_detected(self):
        """Verifica che differenze tra IA grezza e scheda finale vengano segnalate come modifiche manuali."""
        raw = RawCardExtraction(
            righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)]
        )
        # L'operatore ha aggiunto una scatola a mano
        processed = ProcessedCard(
            supermercato_confermato="TEST_MARKET",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Riso", kg=5.0),
            ],
            totale_kg_calcolati=15.0,
            totale_scatole_calcolate=2,
        )

        res = self.service.archive_card(
            image_path=self.dummy_img,
            sheet_name="TEST_MARKET",
            transport="A1",
            processed_card=processed,
            raw_extraction=raw,
        )

        with open(res["dest_json_path"], "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertTrue(data["modifiche_manuali_operatore"])


if __name__ == "__main__":
    unittest.main()
