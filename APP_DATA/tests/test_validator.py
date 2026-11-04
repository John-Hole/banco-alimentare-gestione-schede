"""Test automatici per il motore di validazione e le regole di business 6.1 - 6.5."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import (
    RawCardExtraction,
    BoxItem,
    DeclaredTotals,
    FlagSeverity,
)
from src.validation.validator import CardValidator


class TestCardValidator(unittest.TestCase):

    def test_regola_6_1_aggregazione_scatole(self):
        """Verifica aggregazione kg_calcolati e scatole_calcolate per categoria."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Pasta", kg=12.5),
                BoxItem(categoria="Tonno", kg=5.0),
            ],
            totali_dichiarati=DeclaredTotals(kg_totale=27.5, scatole_totale=3)
        )
        card = CardValidator.validate(raw)

        self.assertEqual(card.categorie["Pasta"].scatole_calcolate, 2)
        self.assertEqual(card.categorie["Pasta"].kg_calcolati, 22.5)
        self.assertEqual(card.categorie["Tonno"].scatole_calcolate, 1)
        self.assertEqual(card.categorie["Tonno"].kg_calcolati, 5.0)
        self.assertEqual(card.totale_scatole_calcolate, 3)
        self.assertEqual(card.totale_kg_calcolati, 27.5)
        self.assertFalse(card.ha_flag_rossi)
        self.assertTrue(card.pronto_per_scrittura)

    def test_regola_6_2_numero_scheda_mancante(self):
        """Se il numero scheda non è scritto, assegna il prossimo slot libero."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="NON_PRESENTE",
            righe_scatole=[BoxItem(categoria="Riso", kg=10.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=10.0, scatole_totale=1)
        )
        card = CardValidator.validate(raw, next_available_slot="A3")

        self.assertEqual(card.numero_scheda_risolto, "A3")
        self.assertFalse(card.ha_flag_rossi)
        self.assertTrue(card.pronto_per_scrittura)
        self.assertTrue(any("assegnato automaticamente 'A3'" in f.message for f in card.flags))

    def test_regola_6_2_numero_scheda_illeggibile(self):
        """Se il numero scheda è illeggibile: FLAG ROSSO BLOCCANTE, nessuna auto-assegnazione."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="ILLEGGIBILE",
            righe_scatole=[BoxItem(categoria="Riso", kg=10.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=10.0, scatole_totale=1)
        )
        card = CardValidator.validate(raw, next_available_slot="A2")

        self.assertIsNone(card.numero_scheda_risolto)
        self.assertTrue(card.ha_flag_rossi)
        self.assertFalse(card.pronto_per_scrittura)
        self.assertTrue(any(f.field == "numero_scheda" and f.severity == FlagSeverity.RED for f in card.flags))

    def test_regola_6_3_totale_discordante(self):
        """Se il totale scritto a mano non coincide con la somma: FLAG ROSSO."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Pasta", kg=10.0),
                BoxItem(categoria="Pasta", kg=12.0),
            ],
            totali_dichiarati=DeclaredTotals(kg_totale=35.0, scatole_totale=2)  # 35 != 22
        )
        card = CardValidator.validate(raw)

        self.assertTrue(card.ha_flag_rossi)
        self.assertFalse(card.pronto_per_scrittura)
        red_flags = [f for f in card.flags if f.severity == FlagSeverity.RED]
        self.assertTrue(any("Discrepanza Totale Kg" in f.message for f in red_flags))

    def test_regola_6_4_solo_totale_senza_scatole(self):
        """Mancano i dati delle singole scatole con solo totale presente: FLAG ROSSO."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="1",
            righe_scatole=[],
            totali_dichiarati=DeclaredTotals(kg_totale=50.0, scatole_totale=4)
        )
        card = CardValidator.validate(raw)

        self.assertTrue(card.ha_flag_rossi)
        self.assertFalse(card.pronto_per_scrittura)
        self.assertTrue(any(f.field == "dati_scatole" and f.severity == FlagSeverity.RED for f in card.flags))

    def test_regola_6_4_categoria_totale_senza_scatole(self):
        """Totale dichiarato per una categoria ma nessuna scatola riga per riga: FLAG ROSSO."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="1",
            righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)],
            totali_dichiarati=DeclaredTotals(
                kg_totale=30.0,
                scatole_totale=2,
                per_categoria={"Tonno": {"kg": 20.0, "scatole": 1}}
            )
        )
        card = CardValidator.validate(raw)

        self.assertTrue(card.ha_flag_rossi)
        self.assertTrue(any("Tonno" in f.message and f.severity == FlagSeverity.RED for f in card.flags))

    def test_recompute_from_categories(self):
        """Verifica ricalcolo dinamico quando l'operatore modifica una categoria."""
        raw = RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="1",
            righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=20.0, scatole_totale=2)  # Inizialmente discordante
        )
        card = CardValidator.validate(raw)
        self.assertTrue(card.ha_flag_rossi)

        # L'operatore aggiunge la seconda scatola mancante impostando kg=20.0, scatole=2
        card.categorie["Pasta"].kg_calcolati = 20.0
        card.categorie["Pasta"].scatole_calcolate = 2
        recomputed = CardValidator.recompute_from_categories(card)

        # Ora deve coincidere
        self.assertEqual(recomputed.totale_kg_calcolati, 20.0)
        self.assertEqual(recomputed.totale_scatole_calcolate, 2)
        self.assertFalse(recomputed.ha_flag_rossi)
        self.assertTrue(recomputed.pronto_per_scrittura)


if __name__ == "__main__":
    unittest.main()
