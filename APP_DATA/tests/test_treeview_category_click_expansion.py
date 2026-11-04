"""Test per l'apertura e chiusura dell'accordion categorie su clic e gestione eccezioni."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ui.app import BancoAlimentareAppV3
from src.models import RawCardExtraction, BoxItem
from src.validation.validator import CardValidator


class TestTreeviewCategoryClickExpansion(unittest.TestCase):
    def setUp(self):
        self.app = BancoAlimentareAppV3()
        self.app.withdraw()

        # Creazione scheda con categorie piene (Olio: 2 scatole, Pasta: 1 scatola)
        raw = RawCardExtraction(
            supermercato="Test Conad",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Olio", kg=1.0),
                BoxItem(categoria="Olio", kg=3.0),
                BoxItem(categoria="Pasta", kg=10.0),
            ]
        )
        self.card = CardValidator.validate(raw, next_available_slot="1")
        self.app.current_processed_card = self.card
        self.app._populate_ui_with_card(self.card)
        self.app.update()

    def tearDown(self):
        try:
            self.app.update()
            self.app.destroy()
        except Exception:
            pass

    def _simulate_click(self, item_id: str, col: str, x: int = 100, y: int = 20):
        """Simula l'evento di clic su una specifica cella del Treeview."""
        event = MagicMock()
        event.x = x
        event.y = y
        self.app.tree.identify_region = MagicMock(return_value="cell")
        self.app.tree.identify_row = MagicMock(return_value=item_id)
        self.app.tree.identify_column = MagicMock(return_value=col)
        return self.app._on_tree_click(event)

    def test_click_col1_anywhere_toggles_expansion(self):
        """Cliccando in qualsiasi punto della colonna #1 (anche ben oltre il triangolino x>45), la categoria si apre e si chiude."""
        self.assertNotIn("Olio", self.app.expanded_categories)

        # Clic a x=120 (ben a destra del vecchio limite di 45px)
        res = self._simulate_click("Olio", "#1", x=120)
        self.assertEqual(res, "break")
        self.assertIn("Olio", self.app.expanded_categories)

        # Secondo clic chiude la categoria
        res = self._simulate_click("Olio", "#1", x=120)
        self.assertEqual(res, "break")
        self.assertNotIn("Olio", self.app.expanded_categories)

    def test_click_col3_toggles_expansion(self):
        """Cliccando sulla colonna #3 (N° Scatole), l'accordion della categoria si apre e si chiude."""
        self.assertNotIn("Pasta", self.app.expanded_categories)

        res = self._simulate_click("Pasta", "#3")
        self.assertEqual(res, "break")
        self.assertIn("Pasta", self.app.expanded_categories)

        res = self._simulate_click("Pasta", "#3")
        self.assertEqual(res, "break")
        self.assertNotIn("Pasta", self.app.expanded_categories)

    def test_click_col2_opens_if_closed_but_does_not_close_if_open(self):
        """Cliccando sulla colonna #2 (Totale Kg), si apre se chiusa, ma NON si richiude se già aperta (permette doppio clic)."""
        self.assertNotIn("Olio", self.app.expanded_categories)

        # Primo clic su #2: apre la categoria
        res = self._simulate_click("Olio", "#2")
        self.assertEqual(res, "break")
        self.assertIn("Olio", self.app.expanded_categories)

        # Secondo clic su #2 (primo tempo di un eventuale doppio clic): NON la richiude
        res2 = self._simulate_click("Olio", "#2")
        # Non deve ritornare "break" per permettere a Tkinter di registrare la selezione e il Double-1
        self.assertNotEqual(res2, "break")
        self.assertIn("Olio", self.app.expanded_categories)

    def test_click_col4_toggles_status_without_accordion_toggle(self):
        """Cliccando sulla colonna #4 (Stato ✓/✗), si commuta la conferma senza alterare l'espansione dell'accordion."""
        self.assertNotIn("Olio", self.app.expanded_categories)
        self.assertFalse(self.card.categorie["Olio"].confermata)

        res = self._simulate_click("Olio", "#4")
        self.assertEqual(res, "break")
        # Stato confermato
        self.assertTrue(self.card.categorie["Olio"].confermata)
        # Accordion rimane invariato (chiuso)
        self.assertNotIn("Olio", self.app.expanded_categories)

    def test_empty_category_does_not_expand(self):
        """Una categoria senza scatole (es. Omogeneizzati) non deve espandersi su clic."""
        self.assertNotIn("Omogeneizzati", self.app.expanded_categories)
        res = self._simulate_click("Omogeneizzati", "#1")
        self.assertNotEqual(res, "break")
        self.assertNotIn("Omogeneizzati", self.app.expanded_categories)


if __name__ == "__main__":
    unittest.main()
