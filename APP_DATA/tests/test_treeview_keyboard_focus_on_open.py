"""Test per la selezione automatica della prima categoria e il focus della tastiera all'apertura o cambio scheda."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ui.app import BancoAlimentareAppV3, BatchItem
from src.models import RawCardExtraction, BoxItem
from src.validation.validator import CardValidator


class TestTreeviewKeyboardFocusOnOpen(unittest.TestCase):
    def setUp(self):
        self.app = BancoAlimentareAppV3()
        # Deiconify e aggiorna per consentire a Tk di gestire focus e navigazione tasti
        self.app.update()

        raw1 = RawCardExtraction(
            supermercato="Punto 1",
            numero_scheda="A1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Olio", kg=2.0)
            ]
        )
        self.card1 = CardValidator.validate(raw1, next_available_slot="A1")

        raw2 = RawCardExtraction(
            supermercato="Punto 2",
            numero_scheda="A2",
            righe_scatole=[
                BoxItem(categoria="Tonno", kg=5.0)
            ]
        )
        self.card2 = CardValidator.validate(raw2, next_available_slot="A2")

    def tearDown(self):
        try:
            self.app.update()
            self.app.destroy()
        except Exception:
            pass

    def test_first_category_selected_on_populate_card(self):
        """Verifica che all'apertura di una nuova scheda la prima categoria (Omogeneizzati) sia selezionata e focalizzata."""
        self.app._populate_ui_with_card(self.card1, preserve_selection=False)
        self.app.update()

        selection = self.app.tree.selection()
        self.assertEqual(selection, ("Omogeneizzati",))
        self.assertEqual(self.app.tree.focus(), "Omogeneizzati")

    def test_down_key_navigates_to_second_category(self):
        """Verifica che con la scheda aperta, la pressione di <Down> sposti immediatamente la selezione alla riga successiva."""
        self.app._populate_ui_with_card(self.card1, preserve_selection=False)
        self.app.update()

        self.app.tree.focus_force()
        self.app.update()
        self.app.tree.event_generate("<Down>")
        self.app.update()

        self.assertEqual(self.app.tree.selection(), ("Alimenti per bambini",))
        self.assertEqual(self.app.tree.focus(), "Alimenti per bambini")

    def test_batch_navigation_selects_first_category_on_each_sheet(self):
        """Verifica che navigando tra le schede del lotto (Successivo/Precedente), la nuova scheda abbia la prima riga pronta."""
        item1 = BatchItem(image_path="test1.jpg", card=self.card1, status="ready")
        item2 = BatchItem(image_path="test2.jpg", card=self.card2, status="ready")
        self.app.batch_items = [item1, item2]
        self.app.batch_index = -1

        # Seleziona primo foglio
        self.app._select_batch_item(0)
        self.app.update()
        self.assertEqual(self.app.tree.selection(), ("Omogeneizzati",))

        # Passa al secondo foglio (Successivo)
        self.app._on_batch_next()
        self.app.update()
        self.assertEqual(self.app.tree.selection(), ("Omogeneizzati",))

        # Torna al primo foglio (Precedente)
        self.app._on_batch_prev()
        self.app.update()
        self.assertEqual(self.app.tree.selection(), ("Omogeneizzati",))

    def test_preserve_selection_when_explicitly_requested(self):
        """Verifica che se preserve_selection=True, la selezione corrente (es. Pasta) venga mantenuta."""
        self.app._populate_ui_with_card(self.card1, preserve_selection=False)
        self.app.update()

        # Utente seleziona 'Pasta'
        self.app.tree.selection_set("Pasta")
        self.app.tree.focus("Pasta")

        # Ricaricamento con preserve_selection=True (es. dopo modifica o conferma)
        self.app._populate_categories_table(self.card1, preserve_selection=True)
        self.app.update()

        self.assertEqual(self.app.tree.selection(), ("Pasta",))
        self.assertEqual(self.app.tree.focus(), "Pasta")


if __name__ == "__main__":
    unittest.main()
