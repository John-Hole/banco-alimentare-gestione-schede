"""Test automatici per la gestione di Excel, matching fogli, backup e preservazione formule."""

import shutil
import sys
import unittest
from pathlib import Path
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (
    DEFAULT_EXCEL_PATH,
    BACKUP_DIR,
    CATEGORIES,
    TRANSPORT_ROWS,
    FORMULA_ROWS,
)
from src.excel.excel_manager import ExcelManager
from src.excel.backup_service import BackupService
from src.models import ProcessedCard, CategoryDetail


class TestExcelManager(unittest.TestCase):

    def setUp(self):
        self.temp_excel = Path(__file__).parent / "temp_gestionale_test.xlsx"
        if DEFAULT_EXCEL_PATH.exists():
            shutil.copy2(DEFAULT_EXCEL_PATH, self.temp_excel)

    def tearDown(self):
        if self.temp_excel.exists():
            try:
                self.temp_excel.unlink()
            except Exception:
                pass

    def test_sheet_name_normalization_and_matching(self):
        """Verifica che il matching tollerante riconosca il foglio anche con differenze OCR."""
        mgr = ExcelManager()
        # Mocking sheet list
        mgr.workbook = None

        norm1 = mgr._normalize_string("Casa Circ. Capanne Via Pievaiola, 222")
        norm2 = mgr._normalize_string("CASA CIRCONDARIALE CAPANNE")

        self.assertIn("capanne", norm1)
        self.assertIn("capanne", norm2)

        # Mocking get_sheet_names per testare find_matching_sheet
        mgr.get_sheet_names = lambda: [
            "Casa Circ. Capanne",
            "Todis Ponte D'Oddi",
            "Tigre Ponte D'Oddi",
            "Coop Fontivegge",
            "Coop San Sisto",
            "Conad Superstore Collestrada"
        ]

        sheet, score = mgr.find_matching_sheet("Todis, Perugia, strada Pontedoddi otto A")
        self.assertEqual(sheet, "Todis Ponte D'Oddi")
        self.assertGreaterEqual(score, 0.90)

        sheet_tigre, score_tigre = mgr.find_matching_sheet("TIGRE - Supermercato PERUGIA STRADA PONTE D'ODDI, 8/A")
        self.assertEqual(sheet_tigre, "Tigre Ponte D'Oddi")
        self.assertGreaterEqual(score_tigre, 0.90)

    def test_all_supermarket_sheets_exist_with_formulas(self):
        """Verifica che nel master siano presenti tutti i fogli supermercati e che contengano le formule intatte."""
        if not DEFAULT_EXCEL_PATH.exists():
            self.skipTest("Master Excel non trovato")

        wb = openpyxl.load_workbook(DEFAULT_EXCEL_PATH, data_only=False)
        self.assertIn("Todis Ponte D'Oddi", wb.sheetnames)
        self.assertIn("Tigre Ponte D'Oddi", wb.sheetnames)
        self.assertIn("Casa Circ. Capanne", wb.sheetnames)
        self.assertGreaterEqual(len(wb.sheetnames), 12)

        todis_ws = wb["Todis Ponte D'Oddi"]
        self.assertIn("Todis", todis_ws["A2"].value)
        self.assertIn("Ponte D'Oddi", todis_ws["A2"].value)
        self.assertEqual(todis_ws.cell(16, 2).value, "=SUM(B6:B15)")
        self.assertIn("=C16+E16", str(todis_ws.cell(18, 2).value))
        self.assertIn("=B16+D16", str(todis_ws.cell(19, 2).value))
        wb.close()

        backup_file = BackupService.create_backup(file_path=str(DEFAULT_EXCEL_PATH))
        try:
            self.assertTrue(backup_file.exists())
            self.assertGreater(backup_file.stat().st_size, 0)
            self.assertTrue(backup_file.name.startswith("gestionale_banco_alimentare_MASTER_BACKUP_"))
            self.assertTrue(backup_file.name.endswith(".xlsx"))
        finally:
            if backup_file.exists():
                backup_file.unlink()

    def test_writing_preserves_formulas_and_structure(self):
        """Verifica che la scrittura popoli le celle di input senza toccare formule."""
        wb = openpyxl.load_workbook(self.temp_excel, data_only=False)
        sheet = wb.active

        # Verifica iniziale che le formule esistano
        self.assertEqual(sheet.cell(16, 2).value, "=SUM(B6:B15)")
        self.assertIn("=C16+E16", str(sheet.cell(18, 2).value))
        self.assertIn("=B16+D16", str(sheet.cell(19, 2).value))

        # Simulazione scrittura riga A1 (riga 6)
        row_idx = TRANSPORT_ROWS["A1"]
        self.assertEqual(row_idx, 6)

        # Pasta è colonna N (14) Kg e O (15) Scatole
        sheet.cell(row_idx, 14).value = 25.0
        sheet.cell(row_idx, 15).value = 2

        # Tonno è colonna F (6) Kg e G (7) Scatole
        sheet.cell(row_idx, 6).value = 15.5
        sheet.cell(row_idx, 7).value = 1

        wb.save(self.temp_excel)

        # Ricarica e verifica
        wb_check = openpyxl.load_workbook(self.temp_excel, data_only=False)
        sheet_check = wb_check.active

        # Valori scritti
        self.assertEqual(sheet_check.cell(6, 14).value, 25.0)
        self.assertEqual(sheet_check.cell(6, 15).value, 2)
        self.assertEqual(sheet_check.cell(6, 6).value, 15.5)
        self.assertEqual(sheet_check.cell(6, 7).value, 1)

        # Formule INTATTE
        self.assertEqual(sheet_check.cell(16, 2).value, "=SUM(B6:B15)")
        self.assertEqual(sheet_check.cell(16, 14).value, "=SUM(N6:N15)")
        self.assertIn("=C16+E16", str(sheet_check.cell(18, 2).value))
        self.assertIn("=B16+D16", str(sheet_check.cell(19, 2).value))

    def test_anti_crash_excel_not_running(self):
        """Verifica che la mancata esecuzione di Excel sollevi ExcelNotRunningError senza crash."""
        from unittest.mock import patch
        from src.excel.excel_manager import ExcelNotRunningError

        mgr = ExcelManager()
        with patch("win32com.client.GetActiveObject", side_effect=Exception("GetActiveObject failed")):
            with self.assertRaises(ExcelNotRunningError) as ctx:
                mgr.connect()
            self.assertIn("Aprire Excel e riprovare", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

