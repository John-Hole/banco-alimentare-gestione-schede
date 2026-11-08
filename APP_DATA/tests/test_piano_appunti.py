"""Unit tests dedicati per il piano modifiche, appunti e ottimizzazioni:
1. 4a colonna 'Stato' (✓/✗) a destra nella Treeview, cliccabile per confermare riga
2. Tasto Invio su Categoria conferma tutte le scatole e aggiorna 'Stato'
3. Tutte le scatole confermate portano automaticamente la riga Categoria a '✓'
4. Clic diretto con il mouse sulla colonna Stato (#4) commuta la conferma
5. Modifica Rapida (doppio clic / Shift+Invio) esce automaticamente al salvataggio;
   Modifica Continua (pulsante '✏️ Modifica') rimane attiva
6. Frecce Su/Giù durante l'inline editing salvano ed escono tornando alla navigazione
7. Configurazione scorciatoie da tastiera (DEFAULT_SHORTCUTS, tab impostazioni, persistenza)
   e ottimizzazione ScreenUpdating / DisplayAlerts in ExcelManager
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import RawCardExtraction, BoxItem, DeclaredTotals, ProcessedCard, CategoryDetail
from src.validation.validator import CardValidator
from src.config import DEFAULT_SHORTCUTS, load_app_config, save_app_config, CATEGORIES
from src.excel.excel_manager import ExcelManager
from src.ui.app import BancoAlimentareAppV3
from src.ui.components_dialogs import SettingsDialogV3


class TestPianoAppuntiEOttimizzazioni(unittest.TestCase):

    def setUp(self):
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

        raw = RawCardExtraction(
            supermercato="Coop Centro Italia",
            numero_scheda="A1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0, confermata=False),
                BoxItem(categoria="Pasta", kg=12.0, confermata=False),
                BoxItem(categoria="Olio", kg=5.0, confermata=False),
                BoxItem(categoria="Riso", kg=8.0, confermata=False),
            ],
            totali_dichiarati=DeclaredTotals(
                kg_totale=45.0,
                scatole_totale=5,
                per_categoria={
                    "Pasta": {"kg": 30.0, "scatole": 2},  # Discrepanza kg: 22 calcolati vs 30 dichiarati
                    "Olio": {"kg": 5.0, "scatole": 1},   # OK coerente
                    "Riso": {"kg": 8.0, "scatole": 1},   # OK coerente
                }
            )
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

    def test_four_columns_state_display(self):
        """Verifica la presenza di 4 colonne con 'Stato' come ultima colonna a destra,
        e la visualizzazione di ✓ / ✗ sia su righe categoria che su righe scatola."""
        cols = self.app.tree["columns"]
        self.assertEqual(len(cols), 4)
        self.assertEqual(list(cols), ["cat", "kg", "scatole", "stato"])
        self.assertEqual(self.app.tree.heading("stato")["text"], "Stato")

        # Categoria Pasta con discrepanza (rossa): mostra ✗
        pasta_vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(len(pasta_vals), 4)
        self.assertEqual(pasta_vals[3], "✗")
        self.assertNotIn("✓", pasta_vals[1])
        self.assertNotIn("✕", pasta_vals[1])

        # Categoria Olio senza discrepanze (verde): mostra ✓
        olio_vals = self.app.tree.item("Olio", "values")
        self.assertEqual(len(olio_vals), 4)
        self.assertEqual(olio_vals[3], "✓")
        self.assertNotIn("✓", olio_vals[1])
        self.assertNotIn("✕", olio_vals[1])

        # Espandi la categoria Pasta per mostrare le scatole
        self.app._toggle_category_expansion("Pasta")
        self.app.update()

        box0_iid = "Pasta__box__0"
        self.assertIn(box0_iid, self.app.tree.get_children())
        box0_vals = self.app.tree.item(box0_iid, "values")
        self.assertEqual(len(box0_vals), 4)
        # La 4a colonna mostra lo stato (✗ iniziale per categoria con discrepanza)
        self.assertEqual(box0_vals[3], "✗")
        # La colonna scatole del singolo collo non deve avere spunte
        self.assertEqual(box0_vals[2], "—")

    def test_click_on_state_column_confirms_row(self):
        """Verifica che cliccare con il mouse sulla colonna #4 ('stato') commuti
        la conferma della singola scatola o di tutte le scatole della categoria."""
        self.app._toggle_category_expansion("Pasta")
        self.app.update()

        box0_iid = "Pasta__box__0"
        target_box = self.app.current_processed_card.righe_scatole[0]
        self.assertFalse(target_box.confermata)

        mock_event = MagicMock(x=380, y=30)
        # 1. Clic su colonna #4 della scatola
        with patch.object(self.app.tree, "identify_region", return_value="cell"), \
             patch.object(self.app.tree, "identify_row", return_value=box0_iid), \
             patch.object(self.app.tree, "identify_column", return_value="#4"):
            res = self.app._on_tree_click(mock_event)
            self.assertEqual(res, "break")

        self.assertTrue(target_box.confermata)
        vals = self.app.tree.item(box0_iid, "values")
        self.assertEqual(vals[3], "✓")

        # Clic di nuovo: toglie la conferma
        with patch.object(self.app.tree, "identify_region", return_value="cell"), \
             patch.object(self.app.tree, "identify_row", return_value=box0_iid), \
             patch.object(self.app.tree, "identify_column", return_value="#4"):
            self.app._on_tree_click(mock_event)

        self.assertFalse(target_box.confermata)
        vals = self.app.tree.item(box0_iid, "values")
        self.assertEqual(vals[3], "✗")

        # 2. Clic su colonna #4 della riga Categoria Pasta: conferma tutte le sue scatole
        with patch.object(self.app.tree, "identify_region", return_value="cell"), \
             patch.object(self.app.tree, "identify_row", return_value="Pasta"), \
             patch.object(self.app.tree, "identify_column", return_value="#4"):
            self.app._on_tree_click(mock_event)

        pasta_boxes = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]
        self.assertTrue(all(b.confermata for b in pasta_boxes))
        pasta_vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(pasta_vals[3], "✓")

    def test_all_boxes_confirmed_confirms_category(self):
        """Verifica che quando tutte le scatole figlie sono confermate e la somma coincide,
        la categoria madre mostri automaticamente ✓ nella colonna Stato."""
        pasta_boxes = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]
        self.assertEqual(len(pasta_boxes), 2)

        # Conferma solo la prima scatola
        pasta_boxes[0].confermata = True
        self.app._populate_categories_table(self.app.current_processed_card)
        pasta_vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(pasta_vals[3], "✗")

        # Conferma anche la seconda scatola, ma con somma discordante (22 vs 30 kg dichiarati):
        # la categoria rimane ✗ come richiesto dalla regola di quadratura
        pasta_boxes[1].confermata = True
        self.app._populate_categories_table(self.app.current_processed_card)
        pasta_vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(pasta_vals[3], "✗")

        # Quando la somma coincide con il dichiarato (es. seconda scatola a 20.0 kg per totale 30.0 kg):
        pasta_boxes[1].kg = 20.0
        cat_data = self.app.current_processed_card.categorie.get("Pasta")
        if cat_data:
            cat_data.kg_calcolati = 30.0
        self.app._populate_categories_table(self.app.current_processed_card)
        pasta_vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(pasta_vals[3], "✓")

    def test_enter_on_category_confirms_all_boxes(self):
        """Verifica che premere Invio sulla riga Categoria confermi tutte le scatole collegate
        e mostri la spunta ✓."""
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")

        pasta_boxes = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]
        self.assertFalse(any(b.confermata for b in pasta_boxes))

        res = self.app._on_tree_return_key()
        self.assertEqual(res, "break")
        self.app.update()

        self.assertTrue(all(b.confermata for b in pasta_boxes))
        pasta_vals = self.app.tree.item("Pasta", "values")
        self.assertEqual(pasta_vals[3], "✓")

    def test_quick_edit_vs_button_edit_persistence(self):
        """Verifica che la Modifica Rapida (source='quick') auto-esca al salvataggio cella,
        mentre la Modifica Continua (source='button') rimanga attiva per modifiche consecutive."""
        # --- Caso 1: Modifica Rapida (Quick Edit via Shift+Enter o doppio clic) ---
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")
        self.app._on_tree_edit_action()
        self.assertTrue(self.app.is_edit_mode)
        self.assertEqual(self.app._edit_mode_source, "quick")
        self.assertIsNotNone(self.app._inline_entry)

        # Modifica valore e commit
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "25.0")
        self.app._commit_inline_cell_edit()

        # Deve essere uscito automaticamente dalla modalità modifica
        self.assertFalse(self.app.is_edit_mode)
        self.assertIsNone(self.app._inline_entry)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica")

        # --- Caso 2: Modifica Continua via pulsante '✏️ Modifica' ---
        self.app._on_enter_edit_mode(source="button")
        self.assertTrue(self.app.is_edit_mode)
        self.assertEqual(self.app._edit_mode_source, "button")

        # Avvia cell edit su Pasta
        self.app._start_inline_cell_edit("Pasta", "#2")
        self.assertIsNotNone(self.app._inline_entry)

        # Modifica valore e commit
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "30.0")
        self.app._commit_inline_cell_edit()

        # In modalità continua, NON deve uscire: rimane pronta per la prossima cella
        self.assertTrue(self.app.is_edit_mode)
        self.assertIsNone(self.app._inline_entry)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica Attiva")

        # Uscita manuale tramite salvataggio modifica
        self.app._on_save_edit_mode()
        self.assertFalse(self.app.is_edit_mode)

    def test_arrow_keys_exit_edit_mode(self):
        """Verifica che le frecce Su/Giù durante l'inline editing salvino il valore,
        escano dalla modalità modifica e spostino il focus alla riga precedente o successiva."""
        # Inizia editing su riga 0 (Pasta)
        self.app._start_inline_cell_edit("Pasta", "#2")
        self.assertIsNotNone(self.app._inline_entry)
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "45.0")

        # Pressione Freccia Giù
        self.app._on_inline_arrow_key("down")
        self.assertFalse(self.app.is_edit_mode)
        self.assertIsNone(self.app._inline_entry)
        # Valore salvato correttamente
        self.assertEqual(self.app.current_processed_card.categorie["Pasta"].kg_calcolati, 45.0)

        # Focus spostato alla riga successiva
        current_focus = self.app.tree.focus()
        children = list(self.app.tree.get_children())
        pasta_idx = children.index("Pasta")
        expected_next = children[pasta_idx + 1]
        self.assertEqual(current_focus, expected_next)

        # Inizia editing su riga successiva e premi Freccia Su
        self.app._start_inline_cell_edit(expected_next, "#2")
        self.assertIsNotNone(self.app._inline_entry)
        self.app._on_inline_arrow_key("up")
        self.assertFalse(self.app.is_edit_mode)
        self.assertIsNone(self.app._inline_entry)
        self.assertEqual(self.app.tree.focus(), "Pasta")

    def test_shortcuts_config_and_settings(self):
        """Verifica che DEFAULT_SHORTCUTS contenga tutte le scorciatoie attese,
        che vengano caricate da load_app_config, e che SettingsDialogV3 le mostri e permetta il salvataggio."""
        # 1. Verifica DEFAULT_SHORTCUTS
        expected_keys = {
            "scan", "scan_flatbed", "upload_image", "write_excel", "write_excel_alt",
            "edit_mode", "confirm_row", "toggle_override", "reset_session", "settings"
        }
        self.assertTrue(expected_keys.issubset(set(DEFAULT_SHORTCUTS.keys())))

        # 2. Verifica load_app_config
        cfg = load_app_config()
        self.assertIn("shortcuts", cfg)
        for k in expected_keys:
            self.assertIn(k, cfg["shortcuts"])

        # 3. Test interfaccia SettingsDialogV3
        saved_values = {}
        def mock_on_save(vals):
            nonlocal saved_values
            saved_values = vals

        dlg = SettingsDialogV3(self.app, config=cfg, on_save=mock_on_save)
        dlg.withdraw()

        # Tab presente
        self.assertIn("⌨️ Scorciatoie da Tastiera", dlg.tabs._tab_dict)
        # Variabili create
        self.assertIn("write_excel", dlg._shortcut_vars)

        # Modifica una scorciatoia
        dlg._shortcut_vars["write_excel"].set("Control-Shift-Return")
        with patch("tkinter.messagebox.askyesno", return_value=True):
            dlg._save()

        self.assertIn("shortcuts", saved_values)
        self.assertEqual(saved_values["shortcuts"]["write_excel"], "Control-Shift-Return")

        # 4. Ottimizzazione Excel COM ScreenUpdating / DisplayAlerts
        mgr = ExcelManager()
        mgr.is_connected = lambda: True
        mock_wb = MagicMock()
        mock_sheet = MagicMock()
        mock_wb.Sheets.return_value = mock_sheet
        mgr.workbook = mock_wb
        mgr.get_sheet_names = lambda: ["TestSheet"]
        mgr.detect_sheet_layout = lambda s: {
            "is_real_format": False,
            "transport_rows": {"A1": 6},
            "categories": CATEGORIES,
            "protected_rows": [],
            "formula_rows": [],
            "first_col": 2,
            "last_col": 27
        }

        mock_excel_app = MagicMock()
        mgr.excel_app = mock_excel_app

        with patch("src.excel.backup_service.BackupService.create_backup", return_value=Path("dummy_backup.xlsx")):
            res = mgr.write_card_data("TestSheet", "A1", self.card, create_backup_first=False)

        # Verifica che ScreenUpdating e DisplayAlerts siano stati impostati a False e poi ripristinati a True
        self.assertEqual(mock_excel_app.ScreenUpdating, True)
        self.assertEqual(mock_excel_app.DisplayAlerts, True)

    def test_category_without_boxes_can_be_confirmed_and_toggled(self):
        """Verifica che una categoria con peso dichiarato/calcolato ma senza scatole figlie
        possa essere confermata con clic colonna #4 o Invio, passando a ✓, e togglata indietro a ✗."""
        # Imposta categoria Legumi con 15.0 kg e 1 scatola calcolata ma senza colli in righe_scatole
        self.app.current_processed_card.categorie["Legumi"].kg_calcolati = 15.0
        self.app.current_processed_card.categorie["Legumi"].scatole_calcolate = 1
        self.app._populate_categories_table(self.app.current_processed_card)

        leg_vals = self.app.tree.item("Legumi", "values")
        self.assertEqual(leg_vals[3], "✗")

        # 1. Clic su colonna #4 per confermare
        mock_event = MagicMock(x=380, y=30)
        with patch.object(self.app.tree, "identify_region", return_value="cell"), \
             patch.object(self.app.tree, "identify_row", return_value="Legumi"), \
             patch.object(self.app.tree, "identify_column", return_value="#4"):
            self.app._on_tree_click(mock_event)

        leg_vals = self.app.tree.item("Legumi", "values")
        self.assertEqual(leg_vals[3], "✓")
        self.assertTrue(self.app.current_processed_card.categorie["Legumi"].confermata)

        # 2. Clic di nuovo: commuta indietro a ✗
        with patch.object(self.app.tree, "identify_region", return_value="cell"), \
             patch.object(self.app.tree, "identify_row", return_value="Legumi"), \
             patch.object(self.app.tree, "identify_column", return_value="#4"):
            self.app._on_tree_click(mock_event)

        leg_vals = self.app.tree.item("Legumi", "values")
        self.assertEqual(leg_vals[3], "✗")
        self.assertFalse(self.app.current_processed_card.categorie["Legumi"].confermata)

        # 3. Pressione Invio su Legumi: conferma di nuovo a ✓
        self.app.tree.selection_set("Legumi")
        self.app.tree.focus("Legumi")
        self.app._on_tree_return_key()

        leg_vals = self.app.tree.item("Legumi", "values")
        self.assertEqual(leg_vals[3], "✓")
        self.assertTrue(self.app.current_processed_card.categorie["Legumi"].confermata)

    def test_custom_shortcuts_dynamic_rebinding_and_cleanup(self):
        """Verifica che cambiare scorciatoie disassoci i vecchi tasti, associ i nuovi (compresi edit_mode e confirm_row)
        e non lasci listener orfani."""
        # 1. Scorciatoie iniziali
        self.assertTrue(bool(self.app.bind("<F2>")))
        self.assertFalse(bool(self.app.bind("<F3>")))

        # 2. Applica nuove scorciatoie
        new_sc = dict(DEFAULT_SHORTCUTS)
        new_sc["scan"] = "F3"
        new_sc["edit_mode"] = "<Control-e>"
        new_sc["confirm_row"] = "<space>"

        with patch("src.ui.app.save_app_config"):
            self.app._apply_settings({"shortcuts": new_sc})

            # Il vecchio <F2> deve essere stato disassociato
            self.assertFalse(bool(self.app.bind("<F2>")))
            # Il nuovo <F3> deve essere associato
            self.assertTrue(bool(self.app.bind("<F3>")))
            # edit_mode deve essere associato a <Control-e>
            self.assertTrue(bool(self.app.bind("<Control-e>")))
            # confirm_row deve essere associato su tree a <space>
            self.assertTrue(bool(self.app.tree.bind("<space>")))

            # Ripristina shortcuts di default
            self.app._apply_settings({"shortcuts": dict(DEFAULT_SHORTCUTS)})
            self.assertTrue(bool(self.app.bind("<F2>")))
            self.assertFalse(bool(self.app.bind("<F3>")))

    def test_quick_edit_escape_cancels_and_exits_edit_mode(self):
        """Verifica che premendo Escape durante un quick edit la casella venga distrutta
        e la modalità modifica venga disattivata (senza rimanere bloccati in is_edit_mode)."""
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")
        self.app._on_tree_edit_action()

        self.assertTrue(self.app.is_edit_mode)
        self.assertEqual(self.app._edit_mode_source, "quick")
        self.assertIsNotNone(self.app._inline_entry)

        # Simula Escape chiamando _cancel_inline_cell_edit
        self.app._cancel_inline_cell_edit()

        self.assertFalse(self.app.is_edit_mode)
        self.assertIsNone(self.app._inline_entry)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica")

    def test_right_arrow_on_expanded_category_enters_first_child(self):
        """Verifica che premere la freccia Destra su una categoria già espansa
        sposti il focus sulla prima riga scatola figlia."""
        self.app._toggle_category_expansion("Pasta")
        self.app.update()
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")

        box0_iid = "Pasta__box__0"
        self.assertIn(box0_iid, self.app.tree.get_children())

        # Premi Freccia Destra quando già espansa
        res = self.app._on_tree_expand()
        self.assertEqual(res, "break")
        self.assertEqual(self.app.tree.focus(), box0_iid)

    def test_invalid_shortcut_rejected_in_settings_dialog(self):
        """Verifica che una scorciatoia con sintassi non valida venga respinta
        da SettingsDialogV3 senza essere salvata."""
        cfg = load_app_config()
        saved = False

        def mock_on_save(vals):
            nonlocal saved
            saved = True

        dlg = SettingsDialogV3(self.app, config=cfg, on_save=mock_on_save)
        dlg.withdraw()

        dlg._shortcut_vars["scan"].set("Invalid_Bad_Key_123")
        with patch("tkinter.messagebox.showerror") as mock_err:
            dlg._save()
            mock_err.assert_called_once()

        self.assertFalse(saved)
        dlg.destroy()


if __name__ == "__main__":
    unittest.main()
