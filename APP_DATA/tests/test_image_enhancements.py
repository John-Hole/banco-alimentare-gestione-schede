"""Test per le funzionalità di regolazione immagine (luminosità, contrasto) e rilevamento drift."""

import sys
import unittest
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scanner.scanner_service import ScannerService, TEST_IMAGES_DIR
from src.validation.validator import CardValidator
from src.models import RawCardExtraction, BoxItem, DeclaredTotals, FlagSeverity


class TestImageEnhancements(unittest.TestCase):

    def test_non_destructive_optimization(self):
        import tempfile

        img = Image.new("RGB", (200, 200), color=(235, 235, 235))
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test_unit_curve.jpg"
            img.save(str(tmp_path), format="JPEG", quality=90)

            optimized = ScannerService.auto_optimize_scan(tmp_path)
            self.assertTrue(optimized.exists())
            with Image.open(optimized) as res:
                self.assertEqual(res.mode, "RGB")
                arr = np.array(res)
                # Verifica che l'immagine mantenga tonalità corrette
                self.assertGreaterEqual(arr.mean(), 180)

    def test_adjacent_column_drift_detection(self):
        # Simula il caso esatto: Olio manca di 3 kg (dichiarati 4 vs calcolati 1)
        # e Pelati ha 3 kg in eccesso (dichiarati 0 vs calcolati 3)
        raw = RawCardExtraction(
            supermercato="Test Market",
            numero_scheda="1",
            righe_scatole=[
                BoxItem(categoria="Olio", kg=1.0),
                BoxItem(categoria="Pelati", kg=3.0),
            ],
            totali_dichiarati=DeclaredTotals(
                per_categoria={
                    "Olio": {"kg": 4.0, "scatole": 2},
                    "Pelati": {"kg": 0.0, "scatole": 0},
                }
            )
        )
        processed = CardValidator.validate(raw)
        # Deve generare un warning per possibile slittamento tra Olio e Pelati
        drift_flags = [f for f in processed.flags if "drift:Olio_Pelati" in f.field]
        self.assertEqual(len(drift_flags), 1)
        self.assertIn("Possibile slittamento tra colonne adiacenti 'Olio' e 'Pelati'", drift_flags[0].message)

    def test_step_based_brightness_is_relative_to_base_preset(self):
        """Verifica che +20% (2 click +) seguito da -10% (1 click -) produca esattamente +10% sul preset base."""
        from PIL import ImageEnhance
        from src.ui.components import ImageViewerV3

        # Creiamo un'istanza headless simulata senza finestra per testare la pipeline matematica
        viewer = ImageViewerV3.__new__(ImageViewerV3)
        viewer.current_image_path = TEST_IMAGES_DIR / "test_unit_lum_step.jpg"
        base_img = Image.new("RGB", (60, 60), color=(100, 100, 100))
        viewer.pil_image = base_img.copy()
        viewer._original_pil_image = base_img.copy()
        viewer._base_processed_image = base_img.copy()
        viewer._brightness_steps = 0
        viewer._contrast_steps = 0
        viewer.on_image_changed_callback = None
        viewer._display_image = lambda: None

        # +10%, poi +10% (totale +20%, step = 2)
        viewer.adjust_brightness(1.10)
        viewer.adjust_brightness(1.10)
        self.assertEqual(viewer._brightness_steps, 2)

        # Ora clicco '-' (-10%): deve diventare step = 1 (+10% rispetto alla base, NON 1.20 * 0.90 = 1.08!)
        viewer.adjust_brightness(0.90)
        self.assertEqual(viewer._brightness_steps, 1)

        expected_plus_10 = ImageEnhance.Brightness(base_img).enhance(1.10)
        self.assertEqual(list(viewer.pil_image.getdata()), list(expected_plus_10.getdata()))

        # Un altro '-' riporta a step = 0, identico al 100% all'immagine base
        viewer.adjust_brightness(0.90)
        self.assertEqual(viewer._brightness_steps, 0)
        self.assertEqual(list(viewer.pil_image.getdata()), list(base_img.getdata()))

        if viewer.current_image_path.exists():
            viewer.current_image_path.unlink()

    def test_provider_modes_mapping(self):
        """Verifica che le 3 modalità IA siano mappate correttamente (Efficienza=3.8->Xiaomi, Solo Xiaomi, Free=3.5)."""
        from src.ui.app import BancoAlimentareAppV3

        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("⚡ Efficienza"), "efficiency")
        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("Solo Xiaomi (OpenRouter)"), "openrouter")
        self.assertEqual(BancoAlimentareAppV3._normalize_display_to_provider("🆓 Free (Gemini 3.5 Lite)"), "gemini_35")


if __name__ == "__main__":
    unittest.main()
