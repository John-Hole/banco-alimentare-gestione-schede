"""Test automatico per l'interfaccia v3 e l'inline cell editing."""

import sys
import unittest
from pathlib import Path
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ui.app import BancoAlimentareAppV3
from src.models import RawCardExtraction, BoxItem
from src.validation.validator import CardValidator


class TestUIv3InlineEdit(unittest.TestCase):
    def setUp(self):
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

        # Creazione scheda di prova con righe_scatole corrette
        raw = RawCardExtraction(
            supermercato="Coop Test",
            numero_scheda="A1",
            righe_scatole=[
                BoxItem(categoria="Olio", kg=1.0),
                BoxItem(categoria="Olio", kg=2.0),
                BoxItem(categoria="Pasta", kg=10.0),
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

    def test_initial_totals_label_format(self):
        """Verifica che la label non contenga 'Totale da Scrivere' ma solo 'Totale:'."""
        text = self.app.calc_totals_lbl.cget("text")
        self.assertTrue(text.startswith("Totale: "))
        self.assertNotIn("Totale da Scrivere", text)
        self.assertNotIn("Totale Calcolato", text)
        self.assertIn("3 scatole", text)
        self.assertIn("13.0 Kg", text)

    def test_enter_and_exit_edit_mode(self):
        """Verifica che l'attivazione della modalità modifica non apra automaticamente nessuna cella."""
        self.assertFalse(self.app.is_edit_mode)
        self.app._on_enter_edit_mode()
        self.assertTrue(self.app.is_edit_mode)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica Attiva")
        # Non deve essere entrato automaticamente in alcuna cella
        self.assertIsNone(self.app._inline_entry)

        # Salva / Esce
        self.app._on_save_edit_mode()
        self.assertFalse(self.app.is_edit_mode)
        self.assertEqual(self.app.edit_btn.cget("text"), "✏️ Modifica")

    def test_override_checkbox_and_write_button_state(self):
        """Verifica che in presenza di avvisi la casella sia cliccabile e il tasto diventi verde solo dopo autorizzazione."""
        from src.models import ValidationFlag, FlagSeverity
        # Aggiungiamo un avviso alla scheda
        self.card.flags.append(
            ValidationFlag(severity=FlagSeverity.WARNING, field="generico", message="Attenzione discrepanza peso")
        )
        self.app._populate_ui_with_card(self.card)

        # La casella deve essere abilitata (cliccabile)
        self.assertEqual(self.app.override_check.cget("state"), "normal")
        # Il pulsante salva deve essere disabilitato e grigio
        self.assertEqual(self.app.write_btn.cget("state"), "disabled")
        self.assertEqual(self.app.write_btn.cget("fg_color"), "#cbd5e1")

        # L'operatore seleziona la casella di autorizzazione
        self.app.override_var.set(True)
        self.app._on_override_toggle()

        # Ora il pulsante deve essere abilitato e verde!
        self.assertEqual(self.app.write_btn.cget("state"), "normal")
        self.assertEqual(self.app.write_btn.cget("fg_color"), "#16a34a")

        # Se deseleziona la casella, torna disabilitato e grigio
        self.app.override_var.set(False)
        self.app._on_override_toggle()
        self.assertEqual(self.app.write_btn.cget("state"), "disabled")
        self.assertEqual(self.app.write_btn.cget("fg_color"), "#cbd5e1")

    def test_inline_edit_category_kg(self):
        """Verifica modifica in-place dei Kg di una categoria."""
        self.app._on_enter_edit_mode()

        # Avvia editing sulla colonna #2 (Kg) di Pasta
        self.app._start_inline_cell_edit("Pasta", "#2")
        self.assertIsNotNone(self.app._inline_entry)
        self.assertEqual(self.app._inline_entry.get(), "10.0")

        # Cambia valore a 15.5
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "15.5")
        self.app._commit_inline_cell_edit()

        self.assertIsNone(self.app._inline_entry)
        self.assertEqual(self.app.current_processed_card.categorie["Pasta"].kg_calcolati, 15.5)
        text = self.app.calc_totals_lbl.cget("text")
        self.assertIn("18.5 Kg", text)

    def test_inline_edit_single_box(self):
        """Verifica modifica in-place dei Kg di una singola scatola espansa."""
        self.app._on_enter_edit_mode()

        # Espandi Olio per mostrare i colli
        self.app._toggle_category_expansion("Olio")
        self.app.update()

        box0_iid = "Olio__box__0"
        self.assertIn(box0_iid, self.app.tree.get_children())

        # Modifica la prima scatola
        self.app._start_inline_cell_edit(box0_iid, "#2")
        self.assertIsNotNone(self.app._inline_entry)
        self.assertEqual(self.app._inline_entry.get(), "1.0")

        # Modifica da 1.0 a 3.5
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "3.5")
        self.app._commit_inline_cell_edit()

        # La categoria Olio deve ora avere 3.5 + 2.0 = 5.5 Kg
        olio_cat = self.app.current_processed_card.categorie["Olio"]
        self.assertEqual(olio_cat.kg_calcolati, 5.5)
        text = self.app.calc_totals_lbl.cget("text")
        self.assertIn("15.5 Kg", text)

    def test_cancel_edit_reverts_backup(self):
        """Verifica che cliccare Annulla ripristini perfettamente lo stato iniziale della scheda."""
        initial_kg = self.app.current_processed_card.totale_kg_calcolati
        self.app._on_enter_edit_mode()

        # Modifica qualcosa
        self.app._start_inline_cell_edit("Pasta", "#2")
        self.app._inline_entry.delete(0, tk.END)
        self.app._inline_entry.insert(0, "99.0")
        self.app._commit_inline_cell_edit()

        self.assertEqual(self.app.current_processed_card.categorie["Pasta"].kg_calcolati, 99.0)

        # Annulla
        self.app._on_cancel_edit_mode()
        self.assertFalse(self.app.is_edit_mode)
        self.assertEqual(self.app.current_processed_card.totale_kg_calcolati, initial_kg)
        self.assertEqual(self.app.current_processed_card.categorie["Pasta"].kg_calcolati, 10.0)

    def test_tab_navigation(self):
        """Verifica che premere Tab confermi la cella corrente e apra la cella successiva."""
        self.app._on_enter_edit_mode()
        self.app._start_inline_cell_edit("Pasta", "#2")
        self.assertEqual(self.app._editing_info["item_id"], "Pasta")
        self.assertEqual(self.app._editing_info["col"], "#2")

        # Simula pressione Tab
        self.app._on_inline_tab()
        import time
        time.sleep(0.05)
        self.app.update()

        # Deve essere passata a Pasta col #3 (Scatole)
        self.assertIsNotNone(self.app._editing_info)
        self.assertEqual(self.app._editing_info["item_id"], "Pasta")
        self.assertEqual(self.app._editing_info["col"], "#3")


if __name__ == "__main__":
    unittest.main()
