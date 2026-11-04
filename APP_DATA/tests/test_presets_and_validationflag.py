"""Unit tests for Presets (P1, P2, P3, Reset) with OpenCV & Pillow fallback,
and ValidationFlag duplicate warning handling in UI v3.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models import ProcessedCard, FlagSeverity, ValidationFlag, RawCardExtraction, BoxItem, DeclaredTotals
from src.scanner.scanner_service import TEST_IMAGES_DIR
from src.ui.components import ImageViewerV3
from src.ui.app import BancoAlimentareAppV3


class TestPresetsAndValidationFlag(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sample_image_path = TEST_IMAGES_DIR / "scan_batch_20261031_100552_page01.jpg"
        if not cls.sample_image_path.exists():
            # Fallback synthetic image if sample scan is missing
            img = Image.new("RGB", (200, 200), color=(240, 240, 240))
            cls.sample_image_path = TEST_IMAGES_DIR / "test_synthetic_scan.jpg"
            img.save(str(cls.sample_image_path), format="JPEG")

    def setUp(self):
        # Create headless ImageViewerV3 without launching CTk GUI
        self.viewer = ImageViewerV3.__new__(ImageViewerV3)
        self.viewer.current_image_path = self.sample_image_path
        with Image.open(self.sample_image_path) as img:
            self.viewer._original_pil_image = img.convert("RGB")
            self.viewer.pil_image = img.convert("RGB")
        self.viewer._base_processed_image = self.viewer.pil_image.copy()
        self.viewer._original_cache = {}
        self.viewer._processed_cache = {}
        self.viewer._preset_state_cache = {}
        self.viewer._active_preset = 0
        self.viewer._brightness_steps = 0
        self.viewer._contrast_steps = 0
        self.viewer._display_image = MagicMock()
        self.viewer._update_preset_buttons = MagicMock()

    def test_validation_flag_import_and_duplicate_warning_no_crash(self):
        """Verifica che ValidationFlag sia importato e che duplicate_warning popoli card.flags senza NameError."""
        # 1. Verifica che ValidationFlag sia presente nel modulo src.ui.app
        import src.ui.app as app_module
        self.assertTrue(hasattr(app_module, "ValidationFlag"), "ValidationFlag deve essere importato in app_v3")
        self.assertIs(app_module.ValidationFlag, ValidationFlag)

        # 2. Simula una card e il dizionario rec con duplicate_warning
        raw = RawCardExtraction(
            supermercato="COOP SAN SISTO",
            numero_scheda="2",
            righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=10.0, scatole_totale=1)
        )
        card = ProcessedCard(
            raw=raw,
            supermercato="COOP SAN SISTO",
            numero_scheda_rilevato="2",
            flags=[]
        )

        rec = {
            "excel_summary_text": "Riga 8: Scheda A2 - COOP SAN SISTO (già presente)",
            "recommended_slot": "A2",
            "is_collision": True,
            "status_message": "⚠️ Riga A2 già occupata da 'COOP SAN SISTO'",
            "duplicate_warning": "Attenzione: Scheda '2' già registrata su Excel per COOP SAN SISTO alla riga 8."
        }

        # Simula il blocco di codice di app_v3.py (riga 2420-2430)
        dup_msg = rec.get("duplicate_warning")
        self.assertIsNotNone(dup_msg)
        if not any(f.field == "numero_scheda" and f.message == dup_msg for f in card.flags):
            card.flags.append(
                app_module.ValidationFlag(
                    severity=FlagSeverity.WARNING,
                    field="numero_scheda",
                    message=dup_msg
                )
            )

        self.assertEqual(len(card.flags), 1)
        self.assertEqual(card.flags[0].severity, FlagSeverity.WARNING)
        self.assertEqual(card.flags[0].field, "numero_scheda")
        self.assertIn("già registrata su Excel", card.flags[0].message)

        # Verifica idempotenza: chiamate ripetute non duplicano il flag
        if not any(f.field == "numero_scheda" and f.message == dup_msg for f in card.flags):
            card.flags.append(
                app_module.ValidationFlag(
                    severity=FlagSeverity.WARNING,
                    field="numero_scheda",
                    message=dup_msg
                )
            )
        self.assertEqual(len(card.flags), 1)

    def test_presets_opencv_execution(self):
        """Testa l'esecuzione dei preset 1, 2, 3 e Reset con OpenCV attivo."""
        orig_arr = np.array(self.viewer._original_pil_image.convert("L"))

        # Preset 1: Matita 00
        ok_p1 = self.viewer.apply_preset_1()
        self.assertTrue(ok_p1)
        self.assertEqual(self.viewer._active_preset, 1)
        p1_arr = np.array(self.viewer.pil_image.convert("L"))
        self.assertFalse(np.array_equal(orig_arr, p1_arr))

        # Preset 2: Nero Deciso
        ok_p2 = self.viewer.apply_preset_2()
        self.assertTrue(ok_p2)
        self.assertEqual(self.viewer._active_preset, 2)
        p2_arr = np.array(self.viewer.pil_image.convert("L"))
        self.assertFalse(np.array_equal(p1_arr, p2_arr))

        # Preset 3: Super Contrasto (sfondo bianco puro, grafite scura)
        ok_p3 = self.viewer.apply_preset_3()
        self.assertTrue(ok_p3)
        self.assertEqual(self.viewer._active_preset, 3)
        p3_arr = np.array(self.viewer.pil_image.convert("L"))
        self.assertFalse(np.array_equal(orig_arr, p3_arr))
        # Verifica che lo sfondo non diventi grigio scuro: media > 200 e > 80% pixel bianchi
        self.assertGreater(p3_arr.mean(), 200.0, "Preset 3 non deve scurire lo sfondo carta!")
        self.assertGreater(np.mean(p3_arr > 240), 0.80, "Preset 3 deve mantenere lo sfondo carta oltre l'80% bianco!")

        # Reset
        ok_reset = self.viewer.reset_image()
        self.assertTrue(ok_reset)
        self.assertEqual(self.viewer._active_preset, 0)
        reset_arr = np.array(self.viewer.pil_image.convert("L"))
        self.assertTrue(np.array_equal(orig_arr, reset_arr))

    def test_presets_pillow_fallback_when_opencv_unavailable(self):
        """Testa il fallback 100% puro Pillow per i preset 1, 2, 3 e Reset quando cv2 non è disponibile."""
        orig_arr = np.array(self.viewer._original_pil_image.convert("L"))

        # Blocchiamo l'importazione di cv2 simulando un ambiente privo di OpenCV
        with patch.dict(sys.modules, {"cv2": None}):
            # Preset 1 Fallback
            ok_p1 = self.viewer.apply_preset_1()
            self.assertTrue(ok_p1, "Preset 1 deve funzionare con il fallback Pillow")
            self.assertEqual(self.viewer._active_preset, 1)
            p1_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertFalse(np.array_equal(orig_arr, p1_arr))

            # Preset 2 Fallback
            ok_p2 = self.viewer.apply_preset_2()
            self.assertTrue(ok_p2, "Preset 2 deve funzionare con il fallback Pillow")
            self.assertEqual(self.viewer._active_preset, 2)
            p2_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertFalse(np.array_equal(p1_arr, p2_arr))

            # Preset 3 Fallback
            ok_p3 = self.viewer.apply_preset_3()
            self.assertTrue(ok_p3, "Preset 3 deve funzionare con il fallback Pillow")
            self.assertEqual(self.viewer._active_preset, 3)
            p3_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertGreater(p3_arr.mean(), 200.0, "Preset 3 Pillow fallback non deve scurire lo sfondo")
            self.assertGreater(np.mean(p3_arr > 240), 0.80, "Preset 3 Pillow fallback deve preservare lo sfondo bianco")

            # Reset Fallback
            ok_reset = self.viewer.reset_image()
            self.assertTrue(ok_reset)
            self.assertEqual(self.viewer._active_preset, 0)
            reset_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertTrue(np.array_equal(orig_arr, reset_arr))

    def test_defensive_base_image_recovery(self):
        """Verifica che i preset recuperino correttamente l'immagine anche se _original_pil_image è None."""
        # Caso 1: _original_pil_image è None ma pil_image è impostata
        self.viewer._original_pil_image = None
        base = self.viewer._get_base_image()
        self.assertIsNotNone(base)
        self.assertEqual(self.viewer._original_pil_image.size, base.size)

        # Caso 2: sia _original_pil_image che pil_image sono None, ma current_image_path esiste
        self.viewer._original_pil_image = None
        self.viewer.pil_image = None
        self.viewer.current_image_path = self.sample_image_path
        base2 = self.viewer._get_base_image()
        self.assertIsNotNone(base2)
        self.assertIsNotNone(self.viewer._original_pil_image)
        self.assertIsNotNone(self.viewer.pil_image)

        # Caso 3: tutto è None -> restituisce None e i preset ritornano False senza crashare
        self.viewer._original_pil_image = None
        self.viewer.pil_image = None
        self.viewer.current_image_path = None
        self.assertIsNone(self.viewer._get_base_image())
        self.assertFalse(self.viewer.apply_preset_1())
        self.assertFalse(self.viewer.apply_preset_2())
        self.assertFalse(self.viewer.apply_preset_3())
        self.assertFalse(self.viewer.reset_image())

    def test_session_logger_called_on_unexpected_preset_error(self):
        """Verifica che in caso di errore anomalo venga invocato session_logger senza sollevare eccezioni."""
        self.viewer._get_base_image = MagicMock(return_value=self.viewer.pil_image)
        with patch.object(self.viewer, "_apply_preset_result", side_effect=RuntimeError("Test error")):
            with patch("src.services.session_logger.get_session_logger") as mock_get_logger:
                mock_logger_inst = MagicMock()
                mock_get_logger.return_value = mock_logger_inst

                res = self.viewer.apply_preset_1()
                self.assertFalse(res)
                mock_logger_inst.logger.warning.assert_called_once()
                self.assertIn("Errore Preset 1", mock_logger_inst.logger.warning.call_args[0][0])


    def test_presets_pillow_fallback_when_both_opencv_and_numpy_missing(self):
        """Testa che i preset funzionino anche in assenza sia di cv2 che di numpy (puro Pillow)."""
        orig_arr = np.array(self.viewer._original_pil_image.convert("L"))

        with patch.dict(sys.modules, {"cv2": None, "numpy": None}):
            self.assertTrue(self.viewer.apply_preset_1(), "P1 deve funzionare senza cv2 e numpy")
            p1_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertFalse(np.array_equal(orig_arr, p1_arr))

            self.assertTrue(self.viewer.apply_preset_2(), "P2 deve funzionare senza cv2 e numpy")
            p2_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertFalse(np.array_equal(p1_arr, p2_arr))

            self.assertTrue(self.viewer.apply_preset_3(), "P3 deve funzionare senza cv2 e numpy")
            p3_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertGreater(p3_arr.mean(), 200.0)

            self.assertTrue(self.viewer.reset_image())
            reset_arr = np.array(self.viewer.pil_image.convert("L"))
            self.assertTrue(np.array_equal(orig_arr, reset_arr))

    def test_fullscreen_viewer_preserves_preset_image(self):
        """Verifica che FullScreenViewerDialogV3 riceva e mantenga l'anteprima elaborata con preset."""
        from src.ui.components import FullScreenViewerDialogV3
        import tkinter as tk
        import customtkinter as ctk

        root = ctk.CTk()
        root.withdraw()
        try:
            enhanced_sample = Image.new("RGB", (100, 100), color=(123, 45, 67))
            fs = FullScreenViewerDialogV3(root, str(self.sample_image_path), initial_image=enhanced_sample)
            self.assertIsNotNone(fs.pil_image)
            self.assertEqual(fs.pil_image.getpixel((0, 0)), (123, 45, 67))
            fs.destroy()
        finally:
            root.destroy()

    def test_populate_ui_with_duplicate_warning_populates_table_and_alerts(self):
        """Verifica end-to-end che in presenza di duplicate_warning in app_v3 la tabella categorie venga popolata regolarmente."""
        import tkinter as tk
        from src.validation.validator import CardValidator

        app = BancoAlimentareAppV3()
        app.withdraw()
        try:
            raw = RawCardExtraction(
                supermercato="COOP SAN SISTO",
                numero_scheda="2",
                righe_scatole=[
                    BoxItem(categoria="Pasta", kg=12.5),
                    BoxItem(categoria="Olio", kg=5.0),
                ],
                totali_dichiarati=DeclaredTotals(kg_totale=17.5, scatole_totale=2)
            )
            card = CardValidator.validate(raw, next_available_slot="A2")

            rec_collision = {
                "excel_summary_text": "Riga 8: Scheda A2 - COOP SAN SISTO (già presente)",
                "recommended_slot": "A2",
                "is_collision": True,
                "status_message": "⚠️ Riga A2 già occupata da 'COOP SAN SISTO'",
                "duplicate_warning": "Attenzione: Scheda '2' già registrata su Excel per COOP SAN SISTO alla riga 8."
            }

            # Simula analyze_transport_recommendation restituendo rec_collision
            app.excel_mgr.analyze_transport_recommendation = MagicMock(return_value=rec_collision)
            app._check_excel_status_silent = MagicMock(return_value=True)

            # Esegue il popolamento UI che in precedenza crashava con NameError: ValidationFlag
            app._populate_ui_with_card(card)
            app.update_idletasks()

            # 1. Verifica che la tabella categorie abbia le 13 righe popolate
            children = app.tree.get_children()
            self.assertEqual(len(children), 13, "La tabella deve contenere tutte le 13 categorie alimentari!")

            # 2. Verifica che il box allarmi contenga il duplicate warning
            self.assertIn("AVVISI", app.alerts_title_lbl.cget("text"))
            alerts_content = app.alerts_text.get("1.0", tk.END)
            self.assertIn("già registrata su Excel", alerts_content)

            # 3. Verifica che il flag sia in card.flags
            self.assertTrue(any(f.field == "numero_scheda" and f.severity == FlagSeverity.WARNING for f in card.flags))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()

