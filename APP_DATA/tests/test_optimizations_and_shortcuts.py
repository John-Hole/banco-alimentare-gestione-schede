"""Unit test per le ottimizzazioni di performance, contrasto a 30 step,

protezione totali di fondo, shortcut Tab e Ctrl+Enter, ed eliminazione automatica a 0 kg.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import RawCardExtraction, BoxItem, DeclaredTotals, ProcessedCard, CategoryDetail
from src.scanner.scanner_service import TEST_IMAGES_DIR
from src.validation.validator import CardValidator
from src.extractors.base import BaseVisionExtractor
from src.excel.excel_manager import ExcelManager
from src.ui.components import ImageViewerV3, QuickEditBoxDialogV3, QuickEditRowDialogV3
from src.ui.app import BancoAlimentareAppV3


class TestOptimizationsAndShortcuts(unittest.TestCase):

    def test_contrast_max_steps_is_30(self):
        """Verifica che MAX_STEPS in ImageViewerV3 sia 30 e che si possa incrementare oltre 8 step."""
        self.assertEqual(ImageViewerV3.MAX_STEPS, 30)

        viewer = ImageViewerV3.__new__(ImageViewerV3)
        viewer.current_image_path = TEST_IMAGES_DIR / "dummy_contrast.jpg"
        base_img = Image.new("RGB", (40, 40), color=(128, 128, 128))
        viewer.pil_image = base_img.copy()
        viewer._original_pil_image = base_img.copy()
        viewer._base_processed_image = base_img.copy()
        viewer._contrast_steps = 0
        viewer._brightness_steps = 0
        viewer._display_image = MagicMock()

        # Incrementa il contrasto 15 volte
        for _ in range(15):
            viewer.adjust_contrast(1.15)

        self.assertEqual(viewer._contrast_steps, 15)
        # Verifica che si possa arrivare fino a 30 step
        for _ in range(20):
            viewer.adjust_contrast(1.15)

        self.assertEqual(viewer._contrast_steps, 30)
        # Non deve superare 30
        viewer.adjust_contrast(1.15)
        self.assertEqual(viewer._contrast_steps, 30)

    def test_recompute_from_base_does_not_save_to_disk(self):
        """Verifica che la regolazione luminosità/contrasto non salvi file su disco."""
        viewer = ImageViewerV3.__new__(ImageViewerV3)
        test_file = TEST_IMAGES_DIR / "should_not_exist.jpg"
        if test_file.exists():
            test_file.unlink()

        viewer.current_image_path = test_file
        base_img = Image.new("RGB", (30, 30), color=(100, 100, 100))
        viewer.pil_image = base_img.copy()
        viewer._original_pil_image = base_img.copy()
        viewer._base_processed_image = base_img.copy()
        viewer._contrast_steps = 0
        viewer._brightness_steps = 0
        viewer._display_image = MagicMock()

        viewer.adjust_contrast(1.15)
        # Il file NON deve essere stato creato su disco
        self.assertFalse(test_file.exists())
        viewer._display_image.assert_called()

    def test_excel_manager_caching_and_invalidation(self):
        """Verifica il caching dei nomi dei fogli e dello stato in ExcelManager e l'invalidazione corretta."""
        mgr = ExcelManager()
        mgr.is_connected = lambda: True
        mock_wb = MagicMock()
        sheet1 = MagicMock()
        sheet1.Name = "Foglio 1"
        sheet2 = MagicMock()
        sheet2.Name = "Todis"
        mock_wb.Sheets.Count = 2
        mock_wb.Sheets.side_effect = lambda idx: sheet1 if idx == 1 else sheet2
        mgr.workbook = mock_wb

        # Prima chiamata: interroga Sheets
        names1 = mgr.get_sheet_names()
        self.assertEqual(names1, ["Foglio 1", "Todis"])
        self.assertEqual(mock_wb.Sheets.call_count, 2)

        # Seconda chiamata: deve usare la cache in-memory senza interrogare mock_wb.Sheets
        names2 = mgr.get_sheet_names()
        self.assertEqual(names2, ["Foglio 1", "Todis"])
        self.assertEqual(mock_wb.Sheets.call_count, 2)

        # Invalida cache e richiedi
        mgr.clear_cache()
        names3 = mgr.get_sheet_names()
        self.assertEqual(mock_wb.Sheets.call_count, 4)

    def test_excel_manager_sheet_status_caching(self):
        """Verifica che get_sheet_status salvi in cache lo stato per evitare chiamate COM ripetute."""
        mgr = ExcelManager()
        mgr.is_connected = lambda: True
        mock_wb = MagicMock()
        sheet = MagicMock()
        sheet.Cells.return_value.Value = None
        mgr.workbook = mock_wb
        mgr.detect_sheet_layout = MagicMock(return_value={
            "transport_rows": {"A1": 5},
            "categories": [],
            "first_col": 3,
            "last_col": 28
        })
        mock_wb.Sheets.return_value = sheet

        st1 = mgr.get_sheet_status("Todis")
        self.assertEqual(st1["next_available_slot"], "A1")
        self.assertEqual(mock_wb.Sheets.call_count, 1)

        # Seconda chiamata immediata: usa la cache
        st2 = mgr.get_sheet_status("Todis")
        self.assertEqual(st2["next_available_slot"], "A1")
        self.assertEqual(mock_wb.Sheets.call_count, 1)

        # Con force_refresh=True deve interrogare di nuovo
        st3 = mgr.get_sheet_status("Todis", force_refresh=True)
        self.assertEqual(mock_wb.Sheets.call_count, 2)

    def test_protection_against_bottom_total_rows(self):
        """Verifica che righe spurie con 'TOTALE KG', 'TOT KG', 'TOT', 'TOT. N° SCATOLE' ecc. vengano escluse da righe_scatole."""
        raw = RawCardExtraction(
            supermercato="Test Supermarket",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="TOTALE KG", kg=50.0),
                BoxItem(categoria="TOT KG", kg=40.0),
                BoxItem(categoria="TOT. N° SCATOLE", kg=5.0),
                BoxItem(categoria="TOT SCATOLE", kg=4.0),
                BoxItem(categoria="TOT", kg=15.0),
                BoxItem(categoria="Totale", kg=15.0),
                BoxItem(categoria="Riso", kg=12.0),
            ],
            totali_dichiarati=DeclaredTotals(kg_totale=22.0, scatole_totale=2)
        )
        processed = CardValidator.validate(raw)

        # Devono essere presenti solo Pasta e Riso
        self.assertEqual(len(processed.righe_scatole), 2)
        cats = [b.categoria for b in processed.righe_scatole]
        self.assertIn("Pasta", cats)
        self.assertIn("Riso", cats)
        self.assertNotIn("TOTALE KG", cats)
        self.assertNotIn("TOT KG", cats)
        self.assertNotIn("TOT. N° SCATOLE", cats)
        self.assertNotIn("TOT SCATOLE", cats)
        self.assertNotIn("TOT", cats)
        self.assertEqual(processed.totale_scatole_calcolate, 2)
        self.assertEqual(processed.totale_kg_calcolati, 22.0)

    def test_extractor_json_parser_excludes_bottom_totals(self):
        """Verifica che il parser JSON scarti preventivamente elementi che descrivono i totali di fondo."""
        json_resp = """{
            "supermercato": "Conad",
            "numero_scheda": "1",
            "righe_scatole": [
                {"riga": "1a", "categoria": "Olio", "kg": 5.0},
                {"riga": "TOTALE KG", "categoria": "Olio", "kg": 5.0},
                {"riga": "TOT KG", "categoria": "Olio", "kg": 5.0},
                {"riga": "TOT. N° SCATOLE", "categoria": "Olio", "kg": 1.0},
                {"riga": "TOT", "categoria": "Olio", "kg": 1.0}
            ],
            "totali_dichiarati": {
                "per_categoria": {"Olio": {"kg": 5.0, "scatole": 1}}
            }
        }"""
        parsed = BaseVisionExtractor.parse_json_response(json_resp)
        self.assertEqual(len(parsed.righe_scatole), 1)
        self.assertEqual(parsed.righe_scatole[0].categoria, "Olio")
        self.assertEqual(parsed.righe_scatole[0].kg, 5.0)

    def test_quick_edit_box_dialog_zero_and_empty_calls_on_delete(self):
        """Verifica che impostando 0 kg o lasciando vuoto nel dialog venga invocato on_delete."""
        on_delete_called = []
        on_save_called = []

        dialog = QuickEditBoxDialogV3.__new__(QuickEditBoxDialogV3)
        dialog.kg_entry = MagicMock()
        dialog.kg_entry.get.return_value = "0"
        dialog.on_delete = lambda: on_delete_called.append(True)
        dialog.on_save = lambda val: on_save_called.append(val)
        dialog.destroy = MagicMock()

        dialog._do_save()
        self.assertEqual(len(on_delete_called), 1)
        self.assertEqual(len(on_save_called), 0)
        dialog.destroy.assert_called()

        # Test campo vuoto -> auto-elimina
        dialog.kg_entry.get.return_value = ""
        dialog._do_save()
        self.assertEqual(len(on_delete_called), 2)

    @patch("tkinter.messagebox.showerror")
    def test_quick_edit_box_dialog_invalid_text_no_unbound_error(self, mock_err):
        """Verifica che un input non valido mostri errore e non causi UnboundLocalError."""
        dialog = QuickEditBoxDialogV3.__new__(QuickEditBoxDialogV3)
        dialog.kg_entry = MagicMock()
        dialog.kg_entry.get.return_value = "abc"
        dialog.on_delete = MagicMock()
        dialog.on_save = MagicMock()
        dialog.destroy = MagicMock()

        dialog._do_save()
        mock_err.assert_called_once()
        dialog.on_save.assert_not_called()
        dialog.on_delete.assert_not_called()

    def test_quick_edit_row_dialog_zero_and_empty_saves_zero(self):
        """Verifica che impostando 0 o lasciando vuoto nel dialog riga categoria venga salvato (0, 0.0)."""
        saved_values = []
        dialog = QuickEditRowDialogV3.__new__(QuickEditRowDialogV3)
        dialog.scatole_entry = MagicMock()
        dialog.kg_entry = MagicMock()
        dialog.scatole_entry.get.return_value = "0"
        dialog.kg_entry.get.return_value = "0"
        dialog.on_save = lambda s, k: saved_values.append((s, k))
        dialog.destroy = MagicMock()

        dialog._do_save()
        self.assertEqual(saved_values, [(0, 0.0)])
        dialog.destroy.assert_called()

        # Test campi vuoti -> (0, 0.0)
        dialog.scatole_entry.get.return_value = ""
        dialog.kg_entry.get.return_value = ""
        dialog._do_save()
        self.assertEqual(saved_values, [(0, 0.0), (0, 0.0)])

    @patch("tkinter.messagebox.showerror")
    def test_quick_edit_row_dialog_invalid_text_no_unbound_error(self, mock_err):
        """Verifica che input non valido su riga categoria non causi UnboundLocalError."""
        dialog = QuickEditRowDialogV3.__new__(QuickEditRowDialogV3)
        dialog.scatole_entry = MagicMock()
        dialog.kg_entry = MagicMock()
        dialog.scatole_entry.get.return_value = "invalid"
        dialog.kg_entry.get.return_value = "10.0"
        dialog.on_save = MagicMock()
        dialog.destroy = MagicMock()

        dialog._do_save()
        mock_err.assert_called_once()
        dialog.on_save.assert_not_called()


class TestUIv3ShortcutsAndAutoElimination(unittest.TestCase):

    def setUp(self):
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

        raw = RawCardExtraction(
            supermercato="Coop Test",
            numero_scheda="A1",
            righe_scatole=[
                BoxItem(categoria="Latte", kg=6.0),
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Pasta", kg=12.0),
            ]
        )
        self.card = CardValidator.validate(raw, next_available_slot="A1")
        self.app.current_processed_card = self.card
        self.app._populate_ui_with_card(self.card)
        self.app.update()

    def tearDown(self):
        try:
            self.app.update()
            self.app.destroy()
        except Exception:
            pass

    def test_global_tab_shortcut_authorizes_and_focuses_write_btn(self):
        """Verifica che premere Tab autospunti la verifica e dia il focus al pulsante Salva."""
        self.app.override_var.set(False)
        self.app._on_override_toggle()
        self.assertFalse(self.app.override_var.get())
        self.assertEqual(self.app.write_btn.cget("state"), "disabled")

        # Simula pressione Tab con mock su focus_set
        with patch.object(self.app.write_btn, "focus_set") as mock_focus:
            result = self.app._on_tab_pressed()
            self.assertEqual(result, "break")
            self.assertTrue(self.app.override_var.get())
            self.assertEqual(self.app.write_btn.cget("state"), "normal")
            mock_focus.assert_called_once()

    def test_inline_edit_box_zero_kg_auto_eliminates_box_and_resets_category(self):
        """Verifica che inserendo 0 per l'unica scatola di Latte la scatola venga eliminata e Latte torni a '—' e '—'."""
        self.app._on_enter_edit_mode()
        self.app._toggle_category_expansion("Latte")
        self.app.update()

        latte_box_iid = "Latte__box__0"
        self.assertIn(latte_box_iid, self.app.tree.get_children())

        # Modifica il peso della scatola a 0
        self.app._start_inline_cell_edit(latte_box_iid, "#2")
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "0")
        self.app._commit_inline_cell_edit()

        # Latte non deve più avere scatole in righe_scatole
        remaining_latte = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Latte"]
        self.assertEqual(len(remaining_latte), 0)

        # Categoria Latte deve avere 0 scatole e 0.0 kg
        latte_cat = self.app.current_processed_card.categorie["Latte"]
        self.assertEqual(latte_cat.scatole_calcolate, 0)
        self.assertEqual(latte_cat.kg_calcolati, 0.0)

        # Nella tabella deve mostrare '—' e '—'
        vals = self.app.tree.item("Latte", "values")
        self.assertEqual(vals[1], "—")
        self.assertEqual(vals[2], "—")

        # Non deve essere più espansa
        self.assertNotIn("Latte", self.app.expanded_categories)

        # Totali scheda aggiornati: solo Pasta (22 kg, 2 scatole)
        self.assertEqual(self.app.current_processed_card.totale_scatole_calcolate, 2)
        self.assertEqual(self.app.current_processed_card.totale_kg_calcolati, 22.0)

    def test_inline_edit_box_zero_kg_with_multiple_boxes_removes_only_that_box(self):
        """Verifica che inserendo 0 per la prima scatola di Pasta (che ne ha 2), rimanga la seconda."""
        self.app._on_enter_edit_mode()
        self.app._toggle_category_expansion("Pasta")
        self.app.update()

        pasta_box0_iid = "Pasta__box__0"
        self.app._start_inline_cell_edit(pasta_box0_iid, "#2")
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "0")
        self.app._commit_inline_cell_edit()

        # Deve rimanere solo 1 scatola di Pasta (12.0 kg)
        remaining_pasta = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]
        self.assertEqual(len(remaining_pasta), 1)
        self.assertEqual(remaining_pasta[0].kg, 12.0)

        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        self.assertEqual(pasta_cat.scatole_calcolate, 1)
        self.assertEqual(pasta_cat.kg_calcolati, 12.0)

    def test_inline_edit_category_row_zero_clears_all_boxes(self):
        """Verifica che impostando 0 kg direttamente sulla riga madre Pasta vengano rimosse tutte le sue scatole."""
        self.app._on_enter_edit_mode()
        self.app._start_inline_cell_edit("Pasta", "#2")
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "0")
        self.app._commit_inline_cell_edit()

        remaining_pasta = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]
        self.assertEqual(len(remaining_pasta), 0)

        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        self.assertEqual(pasta_cat.scatole_calcolate, 0)
        self.assertEqual(pasta_cat.kg_calcolati, 0.0)

        vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(vals[1], "—")
        self.assertEqual(vals[2], "—")

    def test_ctrl_enter_calls_write_to_excel_even_when_tree_focused(self):
        """Verifica che Ctrl+Invio sia legato a _on_write_shortcut e non alla modifica su Treeview."""
        self.assertIn("<Control-Key-Return>", self.app.tree.bind())
        self.assertIn("<Control-Key-Return>", self.app.bind())

        # Verifica che sulla treeview Ctrl+Invio non punti più a _on_tree_ctrl_return_key / _on_tree_edit_action
        tree_bind = self.app.tree.bind("<Control-Return>")
        self.assertNotIn("_on_tree_ctrl_return_key", tree_bind)
        self.assertNotIn("_on_tree_edit_action", tree_bind)

        self.app.override_var.set(True)
        self.app._on_override_toggle()
        self.assertEqual(self.app.write_btn.cget("state"), "normal")

        # Metti il focus sulla treeview
        self.app.tree.focus("Pasta")
        self.app.tree.selection_set("Pasta")

        write_mock = MagicMock()
        self.app._on_write_to_excel = write_mock
        edit_mock = MagicMock()
        self.app._on_tree_edit_action = edit_mock

        self.app._on_write_shortcut()
        write_mock.assert_called_once()
        edit_mock.assert_not_called()

    def test_ctrl_kp_enter_calls_write_to_excel_on_tree(self):
        """Verifica che la scorciatoia Ctrl+KP_Enter sia associata al salvataggio sia a livello root che su Treeview."""
        self.assertIn("<Control-Key-KP_Enter>", self.app.tree.bind())
        self.assertIn("<Control-Key-KP_Enter>", self.app.bind())

        self.app.override_var.set(True)
        self.app._on_override_toggle()

        write_mock = MagicMock()
        self.app._on_write_to_excel = write_mock

        self.app._on_write_shortcut()
        write_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
