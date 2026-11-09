"""Test unitari per HistoryService (Archivio Storico Scansioni e Dataset Ground Truth)."""

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
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
from src.services.history_service import (
    HistoryService,
    PENDING_RAW_DIR_NAME,
    UNRECOGNIZED_MARKET_DIR_NAME,
    sanitize_filename,
)


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
        """Verifica il salvataggio preventivo delle scansioni grezze in <YYYY-MM-DD>/_da_assegnare/."""
        archived = self.service.archive_raw_scan(self.dummy_img, prefix="test_raw")
        self.assertIsNotNone(archived)
        self.assertTrue(archived.exists())
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(archived.parent.name, PENDING_RAW_DIR_NAME)
        self.assertEqual(archived.parent.parent.name, today_str)
        self.assertTrue(archived.name.startswith("test_raw_"))

    def test_archive_raw_scan_with_supermarket_goes_directly_to_market_dir(self):
        """Verifica che se il supermercato è già noto, la grezza venga archiviata in <Data>/<Supermercato>/."""
        archived = self.service.archive_raw_scan(
            self.dummy_img,
            prefix="scanner",
            supermarket_name="COOP: TEST",
            date_str="2026-11-10",
        )
        self.assertIsNotNone(archived)
        self.assertTrue(archived.exists())
        self.assertEqual(archived.parent, self.temp_dir / "2026-11-10" / "COOP_ TEST")

    def test_assign_raw_scan_to_supermarket_move_reassign_idempotent_and_edge_cases(self):
        """Verifica spostamento da _da_assegnare -> Supermercato, cambio foglio, _non_riconosciuto, idempotenza ed edge case."""
        # 1. File mancante / None -> None
        self.assertIsNone(self.service.assign_raw_scan_to_supermarket(None, "CONAD"))
        self.assertIsNone(self.service.assign_raw_scan_to_supermarket(self.temp_dir / "missing.jpg", "CONAD"))

        # 2. Archiviazione grezza iniziale in una data specifica
        raw_path = self.service.archive_raw_scan(self.dummy_img, prefix="adf", date_str="2026-11-10")
        self.assertEqual(raw_path.parent, self.temp_dir / "2026-11-10" / "_da_assegnare")

        # 3. Spostamento in supermercato rilevato (senza passare date_str: deve preservare 2026-11-10)
        moved_1 = self.service.assign_raw_scan_to_supermarket(raw_path, "CONAD TEST")
        self.assertIsNotNone(moved_1)
        self.assertTrue(moved_1.exists())
        self.assertFalse(raw_path.exists())
        self.assertEqual(moved_1.parent, self.temp_dir / "2026-11-10" / "CONAD TEST")
        # _da_assegnare rimasta vuota deve essere rimossa
        self.assertFalse((self.temp_dir / "2026-11-10" / "_da_assegnare").exists())

        # 4. Idempotenza se già nella stessa cartella
        same_path = self.service.assign_raw_scan_to_supermarket(moved_1, "CONAD TEST")
        self.assertEqual(same_path.resolve(), moved_1.resolve())

        # 5. Cambio manuale del supermercato -> riallineamento immediato + pulizia vecchia cartella vuota
        moved_2 = self.service.assign_raw_scan_to_supermarket(moved_1, "COOP CAMBIATA")
        self.assertTrue(moved_2.exists())
        self.assertFalse(moved_1.exists())
        self.assertEqual(moved_2.parent, self.temp_dir / "2026-11-10" / "COOP CAMBIATA")
        self.assertFalse((self.temp_dir / "2026-11-10" / "CONAD TEST").exists())

        # 6. Supermercato vuoto/non rilevato -> _non_riconosciuto
        moved_unknown = self.service.assign_raw_scan_to_supermarket(moved_2, "")
        self.assertTrue(moved_unknown.exists())
        self.assertEqual(moved_unknown.parent, self.temp_dir / "2026-11-10" / UNRECOGNIZED_MARKET_DIR_NAME)

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
        self.assertIsNone(result["raw_archive_path"])
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
        self.assertEqual(data["immagine_grezza"], "")
        self.assertEqual(data["immagine_grezza_relativo"], "")
        self.assertIn("Pasta", data["totali_confermati_excel"]["categorie"])

    def test_archive_card_with_raw_archive_aligns_all_files_in_same_market_dir(self):
        """Verifica che archive_card porti la grezza nella stessa cartella <Data>/<Supermercato>/ con JSON e indice allineati."""
        raw_path = self.service.archive_raw_scan(self.dummy_img, prefix="upload", date_str="2026-11-10")
        processed = ProcessedCard(
            supermercato_rilevato="CONAD GREZZO",
            supermercato_confermato="CONAD / SAN SISTO",
            totale_kg_calcolati=18.0,
            totale_scatole_calcolate=2,
        )
        res = self.service.archive_card(
            image_path=self.dummy_img,
            sheet_name="CONAD / SAN SISTO",
            transport="A3",
            processed_card=processed,
            raw_archive_path=raw_path,
            date_str="2026-11-10",
        )
        self.assertTrue(res["success"])
        market_dir = Path(res["market_dir"])
        moved_raw = Path(res["raw_archive_path"])
        dest_img = Path(res["dest_img_path"])
        dest_json = Path(res["dest_json_path"])

        self.assertEqual(market_dir, self.temp_dir / "2026-11-10" / "CONAD _ SAN SISTO")
        self.assertEqual(moved_raw.parent, market_dir)
        self.assertEqual(dest_img.parent, market_dir)
        self.assertEqual(dest_json.parent, market_dir)
        self.assertTrue(moved_raw.exists())
        self.assertFalse(raw_path.exists())

        with open(dest_json, "r", encoding="utf-8") as f:
            json_data = json.load(f)
        self.assertEqual(json_data["data_colletta"], "2026-11-10")
        self.assertEqual(json_data["immagine_grezza"], moved_raw.name)
        self.assertEqual(
            json_data["immagine_grezza_relativo"],
            f"2026-11-10/CONAD _ SAN SISTO/{moved_raw.name}",
        )

        with open(self.service.index_file, "r", encoding="utf-8") as f:
            idx_data = json.load(f)
        rec = idx_data["schede"][-1]
        self.assertEqual(rec["data"], "2026-11-10")
        self.assertEqual(rec["percorso_relativo"], "2026-11-10/CONAD _ SAN SISTO")
        self.assertEqual(rec["file_grezza"], f"2026-11-10/CONAD _ SAN SISTO/{moved_raw.name}")

    def test_archive_card_preserves_raw_archive_date_across_midnight(self):
        """Se la scansione grezza nasce il giorno X in X/_da_assegnare e si salva dopo mezzanotte (X+1), tutto resta in X/<Market>/."""
        from unittest.mock import patch

        raw_prev_day = self.service.archive_raw_scan(
            self.dummy_img, prefix="scanner", date_str="2026-11-09"
        )
        self.assertEqual(raw_prev_day.parent.parent.name, "2026-11-09")

        class _MidnightFakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 10, 0, 2, 15)

        processed = ProcessedCard(
            supermercato_confermato="CONAD",
            numero_scheda="1",
            totale_kg_calcolati=10.0,
            totale_scatole_calcolate=1,
        )
        with patch("src.services.history_service.datetime", _MidnightFakeDatetime):
            res = self.service.archive_card(
                image_path=self.dummy_img,
                sheet_name="CONAD",
                transport="M1",
                processed_card=processed,
                raw_archive_path=raw_prev_day,
                date_str=None,
            )

        self.assertTrue(res["success"])
        self.assertEqual(res["date_str"], "2026-11-09")
        self.assertEqual(Path(res["market_dir"]).parent.name, "2026-11-09")
        self.assertTrue(Path(res["raw_archive_path"]).exists())
        self.assertEqual(Path(res["raw_archive_path"]).parent, Path(res["market_dir"]))
        self.assertFalse((self.temp_dir / "2026-11-10").exists())

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
