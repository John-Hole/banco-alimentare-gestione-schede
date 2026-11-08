"""Unit tests per le funzionalita v3:
- Conferma singola scatola (Invio, focus immobile, tag child_confirmed)
- Shift+Invio per modifica peso inline senza popup
- Modalita modifica attiva e auto-salvataggio senza tasti Salva/Annulla in calce
- Tasto Tab con toggle spunta/despunta
- Fine deskewing automatico delle scansioni storte
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
from pathlib import Path
import tempfile
import shutil
from PIL import Image, ImageDraw
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import RawCardExtraction, BoxItem, FlagSeverity
from src.validation.validator import CardValidator
from src.ui.app import BancoAlimentareAppV3
from src.scanner.scanner_service import ScannerService, SOURCE_FEEDER, WIA_ERROR_PAPER_EMPTY
from src.scanner.preset_engine import deskew_image


class TestV3BoxConfirmationAndDeskew(unittest.TestCase):

    def setUp(self):
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

        raw = RawCardExtraction(
            supermercato="Test Conad",
            numero_scheda="A1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0, confermata=False),
                BoxItem(categoria="Pasta", kg=12.0, confermata=False),
                BoxItem(categoria="Olio", kg=5.0, confermata=False),
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

    def test_box_item_confermata_field(self):
        """Verifica che BoxItem abbia il campo confermata con default False."""
        box = BoxItem(categoria="Pasta", kg=8.0)
        self.assertFalse(box.confermata)
        box.confermata = True
        self.assertTrue(box.confermata)

    def test_box_confirmation_toggle_and_focus_stays(self):
        """Premere Invio su una singola scatola commuta lo stato confermata, mostra ✓/—,
        applica il tag child_confirmed, e il focus RIMANE sulla stessa scatola ('No rimane la').
        La categoria madre NON deve essere approvata.
        """
        # Espandi la categoria Pasta
        self.app._toggle_category_expansion("Pasta")
        self.app.update()

        box0_iid = "Pasta__box__0"
        self.assertIn(box0_iid, self.app.tree.get_children())

        # Posiziona il focus sulla prima scatola
        self.app.tree.selection_set(box0_iid)
        self.app.tree.focus(box0_iid)

        # 1a pressione di Invio: la scatola viene confermata
        res = self.app._on_tree_return_key()
        self.assertEqual(res, "break")
        self.app.update()

        target_box = self.app.current_processed_card.righe_scatole[0]
        self.assertTrue(target_box.confermata)
        self.assertFalse(self.app.current_processed_card.righe_scatole[1].confermata)

        # Valori della riga nella Treeview: la colonna scatole (#3) mostra '—' (niente doppia spunta) e lo stato (#4) mostra '✓'
        vals = self.app.tree.item(box0_iid, "values")
        self.assertEqual(vals[2], "—")
        self.assertEqual(vals[3], "✓")
        tags = self.app.tree.item(box0_iid, "tags")
        self.assertIn("child_confirmed", tags)

        # Il focus e la selezione DEVONO RIMANERE su Pasta__box__0 ('No rimane la')
        self.assertEqual(self.app.tree.focus(), box0_iid)
        self.assertEqual(list(self.app.tree.selection()), [box0_iid])

        # La categoria madre NON deve essere stata approvata (scatole_dichiarate e kg_dichiarati intatti)
        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        self.assertIsNone(pasta_cat.scatole_dichiarate)
        self.assertIsNone(pasta_cat.kg_dichiarati)

        # 2a pressione di Invio: toglie la conferma
        self.app._on_tree_return_key()
        self.app.update()

        self.assertFalse(target_box.confermata)
        vals2 = self.app.tree.item(box0_iid, "values")
        self.assertEqual(vals2[2], "—")
        tags2 = self.app.tree.item(box0_iid, "tags")
        self.assertNotIn("child_confirmed", tags2)

        # Il focus rimane ancora fermo sulla scatola
        self.assertEqual(self.app.tree.focus(), box0_iid)
        self.assertEqual(list(self.app.tree.selection()), [box0_iid])

    def test_return_key_on_category_confirms_without_toggling_accordion(self):
        """Invio sulla riga madre conferma la categoria senza aprire/chiudere l'accordion (espansione affidata alle frecce)."""
        self.assertNotIn("Pasta", self.app.expanded_categories)
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")

        # Invio: conferma la categoria ma NON apre l'accordion
        self.app._on_tree_return_key()
        self.assertNotIn("Pasta", self.app.expanded_categories)
        self.assertNotIn("Pasta__box__0", self.app.tree.get_children())
        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        self.assertTrue(pasta_cat.confermata)

        # Frecce destra/sinistra gestiscono l'apertura e chiusura dell'accordion
        self.app._on_tree_expand()
        self.assertIn("Pasta", self.app.expanded_categories)
        self.assertIn("Pasta__box__0", self.app.tree.get_children())

        self.app._on_tree_collapse()
        self.assertNotIn("Pasta", self.app.expanded_categories)
        self.assertNotIn("Pasta__box__0", self.app.tree.get_children())

    def test_shift_return_opens_inline_edit_on_weight_without_popup(self):
        """Shift + Invio attiva la modalita modifica e avvia l'editing inline sulla colonna peso #2."""
        self.app._toggle_category_expansion("Pasta")
        self.app.update()

        box0_iid = "Pasta__box__0"
        self.app.tree.selection_set(box0_iid)
        self.app.tree.focus(box0_iid)

        self.assertFalse(self.app.is_edit_mode)
        self.assertIsNone(self.app._inline_entry)

        res = self.app._on_tree_shift_return_key()
        self.assertEqual(res, "break")

        # Modalita modifica attivata
        self.assertTrue(self.app.is_edit_mode)
        self.assertIsNotNone(self.app._inline_entry)
        self.assertEqual(self.app._editing_info["item_id"], box0_iid)
        self.assertEqual(self.app._editing_info["col"], "#2")
        self.assertEqual(self.app._inline_entry.get(), "10.0")

    def test_double_click_opens_inline_edit_without_popup(self):
        """Doppio clic attiva la modalita modifica ed apre l'editing inline sovrapposto alla cella peso."""
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")

        self.assertFalse(self.app.is_edit_mode)
        self.app._on_tree_edit_action()

        self.assertTrue(self.app.is_edit_mode)
        self.assertIsNotNone(self.app._inline_entry)
        self.assertEqual(self.app._editing_info["item_id"], "Pasta")
        self.assertEqual(self.app._editing_info["col"], "#2")

    def test_escape_exits_edit_mode(self):
        """Premere Escape mentre si e in modalita modifica esce e torna alla visualizzazione normale."""
        self.app._on_enter_edit_mode()
        self.assertTrue(self.app.is_edit_mode)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica Attiva")

        self.app._on_escape_pressed()
        self.assertFalse(self.app.is_edit_mode)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica")

    def test_enter_edit_mode_hides_action_buttons(self):
        """In modalita modifica non devono essere mostrati i pulsanti Salva e Annulla in calce."""
        self.app._on_enter_edit_mode()
        # edit_actions_box non deve essere mappato/visibile
        self.app.update()
        self.assertEqual(self.app.edit_actions_box.winfo_manager(), "")

    def test_tab_toggle_override_checkbox(self):
        """Il tasto Tab inverte la spunta dell'autorizzazione: prima pressione spunta e focus Salva, seconda toglie."""
        self.app.override_var.set(False)
        self.app._on_override_toggle()
        self.assertFalse(self.app.override_var.get())

        with patch.object(self.app.write_btn, "focus_set") as mock_focus:
            # 1a pressione: spunta
            res = self.app._on_tab_pressed()
            self.assertEqual(res, "break")
            self.assertTrue(self.app.override_var.get())
            mock_focus.assert_called_once()

            # 2a pressione: toglie spunta
            res2 = self.app._on_tab_pressed()
            self.assertEqual(res2, "break")
            self.assertFalse(self.app.override_var.get())

    def test_deskew_image_straightening(self):
        """Verifica che il fine deskewing rilevi e raddrizzi inclinazioni tra -5° e +5°."""
        # Crea un'immagine di test con griglia orizzontale
        w, h = 600, 400
        img = Image.new("RGB", (w, h), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        for y in range(40, 360, 25):
            draw.line([(30, y), (570, y)], fill=(0, 0, 0), width=2)

        # Inclina artificialmente di +2.0 gradi
        skewed = img.rotate(2.0, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))

        angle_detected = ScannerService.detect_skew_angle(skewed)
        self.assertTrue(-4.0 <= angle_detected <= 0.0)

        deskewed, final_ang = ScannerService.deskew_image(skewed, return_angle=True)
        # Dopo il deskewing l'angolo residuo deve essere inferiore alla soglia di tolleranza 0.2°
        res_angle = ScannerService.detect_skew_angle(deskewed)
        self.assertLess(abs(res_angle), 0.2)

        # Angolo sotto soglia 0.2° non deve ruotare
        unchanged = ScannerService.deskew_image(img)
        self.assertEqual(unchanged.size, img.size)

    def test_deskew_image_on_path_and_preset_engine_export(self):
        """Verifica deskew_image operando su percorso file e l'export in preset_engine."""
        tdir = tempfile.mkdtemp()
        try:
            tfile = Path(tdir) / "test_skew.jpg"
            img = Image.new("RGB", (600, 400), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            for y in range(40, 360, 25):
                draw.line([(30, y), (570, y)], fill=(0, 0, 0), width=2)
            skewed = img.rotate(-2.5, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
            skewed.save(str(tfile), format="JPEG")

            out_path, ang = deskew_image(tfile, return_angle=True)
            self.assertEqual(out_path, tfile)
            self.assertTrue(abs(ang) > 0.2)

            with Image.open(tfile) as checked:
                res_angle = ScannerService.detect_skew_angle(checked)
                self.assertLess(abs(res_angle), 0.2)
        finally:
            shutil.rmtree(tdir, ignore_errors=True)

    def test_box_confirmation_works_in_edit_mode(self):
        """Anche quando is_edit_mode è True, premere Invio su una scatola commuta confermata e non si blocca."""
        self.app._toggle_category_expansion("Pasta")
        self.app.update()

        self.app._on_enter_edit_mode()
        self.assertTrue(self.app.is_edit_mode)

        box0_iid = "Pasta__box__0"
        self.app.tree.selection_set(box0_iid)
        self.app.tree.focus(box0_iid)

        # 1a pressione: conferma
        self.app._on_tree_return_key()
        self.app.update()
        target_box = self.app.current_processed_card.righe_scatole[0]
        self.assertTrue(target_box.confermata)
        self.assertEqual(self.app.tree.focus(), box0_iid)

        # 2a pressione: toglie conferma
        self.app._on_tree_return_key()
        self.app.update()
        self.assertFalse(target_box.confermata)
        self.assertEqual(self.app.tree.focus(), box0_iid)

    def test_category_accordion_works_in_edit_mode(self):
        """Anche quando is_edit_mode è True, freccia destra e sinistra aprono e chiudono l'accordion."""
        self.app._on_enter_edit_mode()
        self.assertTrue(self.app.is_edit_mode)

        self.assertNotIn("Pasta", self.app.expanded_categories)
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")

        self.app._on_tree_expand()
        self.assertIn("Pasta", self.app.expanded_categories)

        self.app._on_tree_collapse()
        self.assertNotIn("Pasta", self.app.expanded_categories)

    def test_inline_box_weight_edit_preserves_declared_totals(self):
        """Modificare il peso di una scatola deve preservare i totali dichiarati della categoria e aggiornare la validazione."""
        from src.models import DeclaredTotals
        raw = RawCardExtraction(
            supermercato="Test Conad",
            numero_scheda="A1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Pasta", kg=12.0),
            ],
            totali_dichiarati=DeclaredTotals(
                kg_totale=25.0,
                scatole_totale=2,
                per_categoria={"Pasta": {"kg": 25.0, "scatole": 2}}
            )
        )
        card = CardValidator.validate(raw, next_available_slot="A1")
        self.app.current_processed_card = card
        self.app._populate_ui_with_card(card)
        self.app._toggle_category_expansion("Pasta")

        # Inizialmente c'è discrepanza: calcolato 22.0 vs dichiarato 25.0
        self.assertEqual(self.app.current_processed_card.categorie["Pasta"].kg_dichiarati, 25.0)
        self.assertTrue(self.app.current_processed_card.ha_flag_rossi)

        # Modifica il secondo collo da 12.0 a 15.0 -> totale diventa 25.0
        self.app._start_inline_cell_edit("Pasta__box__1", "#2")
        self.assertIsNotNone(self.app._inline_entry)
        self.app._inline_entry.delete(0, "end")
        self.app._inline_entry.insert(0, "15.0")
        self.app._commit_inline_cell_edit()

        # I totali dichiarati DEVONO essere rimasti intatti a 25.0!
        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        self.assertEqual(pasta_cat.kg_dichiarati, 25.0)
        self.assertEqual(pasta_cat.scatole_dichiarate, 2)
        self.assertEqual(pasta_cat.kg_calcolati, 25.0)
        self.assertEqual(pasta_cat.scatole_calcolate, 2)

        # La discrepanza è ora risolta (verde, nessun flag rosso su Pasta)
        has_pasta_red = any(f.severity == FlagSeverity.RED and "Pasta" in f.field for f in self.app.current_processed_card.flags)
        self.assertFalse(has_pasta_red)

    def test_inline_box_zero_deletes_box(self):
        """Impostare peso 0 elimina la scatola e aggiorna il conteggio colli."""
        self.app._toggle_category_expansion("Pasta")
        self.assertEqual(len([b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]), 2)

        self.app._start_inline_cell_edit("Pasta__box__1", "#2")
        self.app._inline_entry.delete(0, "end")
        self.app._inline_entry.insert(0, "0")
        self.app._commit_inline_cell_edit()

        remaining = [b for b in self.app.current_processed_card.righe_scatole if b.categoria == "Pasta"]
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].kg, 10.0)

    def test_inline_tab_returns_break(self):
        """_on_inline_tab deve restituire 'break' per non propagare il Tab al form padre."""
        self.app._toggle_category_expansion("Pasta")
        self.app._start_inline_cell_edit("Pasta__box__0", "#2")
        res = self.app._on_inline_tab()
        self.assertEqual(res, "break")

    def test_finalize_scan_deskews_with_optimize_false(self):
        """Verifica che _finalize_scan esegua il fine deskewing anche quando optimize=False."""
        tdir = tempfile.mkdtemp()
        try:
            tfile = Path(tdir) / "test_finalize_skew.jpg"
            img = Image.new("RGB", (600, 400), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            for y in range(40, 360, 25):
                draw.line([(30, y), (570, y)], fill=(0, 0, 0), width=2)
            skewed = img.rotate(3.0, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
            skewed.save(str(tfile), format="JPEG")

            ScannerService._finalize_scan(tfile, optimize=False)

            with Image.open(tfile) as checked:
                res_angle = ScannerService.detect_skew_angle(checked)
                self.assertLess(abs(res_angle), 0.2)
        finally:
            shutil.rmtree(tdir, ignore_errors=True)


    def test_box_edit_does_not_verify_category_if_sum_mismatches(self):
        """Modificare una scatola verifica solo quella scatola e non l'intera categoria se il totale non torna."""
        cat_data = self.app.current_processed_card.categorie["Pasta"]
        cat_data.kg_dichiarati = 25.0
        cat_data.scatole_dichiarate = 2
        cat_data.confermata = False
        
        # Scatole attuali: 10.0 + 12.0 = 22.0 kg != 25.0 kg
        recomputed = CardValidator.recompute_from_categories(self.app.current_processed_card)
        self.app.current_processed_card = recomputed
        self.app._populate_categories_table(recomputed)
        
        # Simula modifica inline della prima scatola a 11.0 kg (totale = 23.0 != 25.0)
        self.app._editing_info = {
            "item_id": "Pasta__box__0",
            "col": "#2",
            "orig_val": "10.0",
            "is_cat_row": False,
            "is_box_row": True
        }
        mock_entry = MagicMock()
        mock_entry.get.return_value = "11.0"
        self.app._inline_entry = mock_entry
        
        self.app._commit_inline_cell_edit()
        
        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        box0 = self.app.current_processed_card.righe_scatole[0]
        self.assertEqual(box0.kg, 11.0)
        self.assertTrue(box0.confermata)
        # La categoria NON deve essere confermata perché 23.0 != 25.0
        self.assertFalse(pasta_cat.confermata)
        self.assertEqual(self.app.tree.item("Pasta", "values")[3], "✗")

    def test_box_edit_auto_verifies_category_when_sum_matches(self):
        """Modificare una scatola in modo che il totale coincida col dichiarato verifica automaticamente tutta la categoria."""
        cat_data = self.app.current_processed_card.categorie["Pasta"]
        cat_data.kg_dichiarati = 25.0
        cat_data.scatole_dichiarate = 2
        cat_data.confermata = False
        
        # Scatole attuali: 10.0 + 12.0 = 22.0 kg
        recomputed = CardValidator.recompute_from_categories(self.app.current_processed_card)
        self.app.current_processed_card = recomputed
        
        # Modifica la prima scatola da 10.0 a 13.0 kg -> Totale = 13.0 + 12.0 = 25.0 kg (coincide con dichiarato!)
        self.app._editing_info = {
            "item_id": "Pasta__box__0",
            "col": "#2",
            "orig_val": "10.0",
            "is_cat_row": False,
            "is_box_row": True
        }
        mock_entry = MagicMock()
        mock_entry.get.return_value = "13.0"
        self.app._inline_entry = mock_entry
        
        self.app._commit_inline_cell_edit()
        
        pasta_cat = self.app.current_processed_card.categorie["Pasta"]
        box0 = self.app.current_processed_card.righe_scatole[0]
        box1 = self.app.current_processed_card.righe_scatole[1]
        self.assertEqual(box0.kg, 13.0)
        self.assertTrue(box0.confermata)
        self.assertTrue(box1.confermata)
        # La categoria DEVE essere verificata automaticamente
        self.assertTrue(pasta_cat.confermata)
        self.assertEqual(self.app.tree.item("Pasta", "values")[3], "✓")

    def test_streaming_pipeline_page_enqueued_and_extraction_triggered(self):
        """Verifica che durante la scansione ADF lo streaming inserisca la pagina e avvii l'IA in parallelo."""
        tdir = tempfile.mkdtemp()
        try:
            p1 = Path(tdir) / "stream_page1.jpg"
            img = Image.new("RGB", (200, 200), (255, 255, 255))
            img.save(str(p1), format="JPEG")
            
            with patch.object(self.app, "_process_next_pending") as mock_proc:
                self.app._extracting = False
                self.app._on_streamed_page_acquired(p1)
                
                self.assertEqual(len(self.app.batch_items), 1)
                self.assertEqual(self.app.batch_items[0].image_path, str(p1))
                mock_proc.assert_called_once()
        finally:
            shutil.rmtree(tdir, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()

