"""tests/test_preset_integration.py
=============================================================================
TEST DI INTEGRAZIONE DEI 5 PRESET, AUTO-CLASSIFICATORE E PIPELINE IA
=============================================================================
Progetto: Banco Alimentare - Acquisizione Schede Raccolta 'MOD. A'
Autore: Team Antigravity AI
Data: 2026-10-31

Questo modulo verifica:
1. Applicazione di ciascuno dei 5 preset (singolarmente e tramite dispatcher).
2. Auto-classificazione corretta di Scheda 1 (P1) e Scheda 2 (P2), oltre ai casi sintetici P3, P4, P5.
3. Generazione e rimozione delle copie ottimizzate per l'IA in test_images/temp_ai/.
4. Funzionalità di ImageViewerV3 (toolbar a 5 pulsanti, evidenziazione visiva, set_active_preset, save_temp_ai_copy).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scanner.presets_config import (
    PRESETS_CONFIG,
    AUTO_CLASSIFIER_THRESHOLDS,
)
from src.scanner.preset_engine import (
    apply_preset,
    apply_preset_0,
    apply_preset_1,
    apply_preset_2,
    apply_preset_3,
    apply_preset_4,
    apply_preset_5,
    detect_best_preset,
    prepare_scan_for_pipeline,
    create_ai_optimized_copy,
    clean_temp_ai_dir,
    TEMP_AI_DIR,
)
from src.ui.components import ImageViewerV3


class TestPresetIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.base_dir = Path(__file__).resolve().parent.parent
        cls.scheda1_path = cls.base_dir / "test_images" / "00_Originale_Nessuna_Modifica.jpg"
        cls.scheda2_path = cls.base_dir / "test_images" / "00_Originale_Nuova_Scansione.jpg"

    def setUp(self):
        # Assicura cartella temporanea IA pulita prima di ogni test
        clean_temp_ai_dir()

    def tearDown(self):
        # Pulizia dopo ogni test
        clean_temp_ai_dir()

    # =========================================================================
    # 1. TEST APPLICAZIONE DEI 5 PRESET
    # =========================================================================

    def test_presets_config_completeness(self):
        """Verifica che PRESETS_CONFIG contenga i preset (incluso P0 standard) con chiavi attese."""
        self.assertTrue({0, 1, 2, 3, 4, 5}.issubset(set(PRESETS_CONFIG.keys())))
        for pid in range(0, 6):
            cfg = PRESETS_CONFIG[pid]
            self.assertEqual(cfg["id"], pid)
            self.assertIn("name", cfg)
            self.assertIn("titolo", cfg)
            self.assertIn("descrizione", cfg)

    def test_apply_preset_0_direct(self):
        """Verifica che apply_preset_0 mantenga l'immagine identica senza alcuna alterazione (P0 standard)."""
        dummy = np.full((120, 160, 3), 210, dtype=np.uint8)
        cv2.line(dummy, (20, 30), (140, 30), (50, 50, 50), 2)
        p0_out = apply_preset_0(dummy)
        np.testing.assert_array_equal(p0_out, dummy)

    def test_apply_all_5_presets_direct(self):
        """Verifica l'esecuzione diretta delle funzioni apply_preset_1 .. apply_preset_5."""
        dummy = np.full((120, 160, 3), 210, dtype=np.uint8)
        # Disegna qualche linea scura per simulare scrittura
        cv2.line(dummy, (20, 30), (140, 30), (50, 50, 50), 2)
        cv2.putText(dummy, "123", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (80, 80, 80), 2)

        p1_out = apply_preset_1(dummy)
        self.assertEqual(p1_out.shape, dummy.shape)

        p2_out = apply_preset_2(dummy)
        self.assertEqual(p2_out.shape, dummy.shape)

        p3_out = apply_preset_3(dummy)
        self.assertEqual(p3_out.shape, dummy.shape)

        p4_out = apply_preset_4(dummy)
        self.assertEqual(p4_out.shape, dummy.shape)

        p5_out = apply_preset_5(dummy)
        self.assertEqual(p5_out.shape, dummy.shape)

    def test_apply_preset_dispatcher(self):
        """Verifica che apply_preset accetti int (0..5), codici ('P1'..'P5'), minuscole e return_pil."""
        dummy = np.full((80, 80, 3), 220, dtype=np.uint8)

        # Preset 0 / Reset
        res0 = apply_preset(dummy, 0)
        self.assertTrue(np.array_equal(res0, dummy))

        res0_str = apply_preset(dummy, "RESET")
        self.assertTrue(np.array_equal(res0_str, dummy))

        # Preset 1..5 con ID int
        for pid in range(1, 6):
            out = apply_preset(dummy, pid)
            self.assertEqual(out.shape, (80, 80, 3))

        # Preset con codice stringa 'P1'..'P5' e minuscole 'p1'..'p5'
        for pid in range(1, 6):
            out_upper = apply_preset(dummy, f"P{pid}")
            out_lower = apply_preset(dummy, f"p{pid}")
            self.assertEqual(out_upper.shape, (80, 80, 3))
            self.assertEqual(out_lower.shape, (80, 80, 3))

        # Test return_pil=True
        pil_res = apply_preset(dummy, 1, return_pil=True)
        self.assertIsInstance(pil_res, Image.Image)
        self.assertEqual(pil_res.size, (80, 80))

        # Preset non valido -> ValueError
        with self.assertRaises(ValueError):
            apply_preset(dummy, 99)

        with self.assertRaises(ValueError):
            apply_preset(dummy, "P99_NonEsiste")

    def test_boundary_edge_cases(self):
        """Verifica robustezza su 1x1 px, all-white (255) e all-black (0)."""
        tiny = np.array([[[245, 245, 245]]], dtype=np.uint8)
        for pid in range(1, 6):
            out = apply_preset(tiny, pid)
            self.assertEqual(out.shape, tiny.shape)

        white = np.full((60, 60, 3), 255, dtype=np.uint8)
        for pid in range(1, 6):
            out = apply_preset(white, pid)
            self.assertEqual(out.min(), 255)
            self.assertEqual(out.max(), 255)

        black = np.full((60, 60, 3), 0, dtype=np.uint8)
        for pid in range(1, 6):
            out = apply_preset(black, pid)
            self.assertEqual(out.min(), 0)

    # =========================================================================
    # 2. TEST AUTO-CLASSIFICATORE (DETECT_BEST_PRESET)
    # =========================================================================

    def test_auto_detect_benchmark_sheets(self):
        """Verifica classificazione reale: Scheda 1 -> P1 (Matita) e Scheda 2 -> P2 (Biro)."""
        if self.scheda1_path.exists():
            pid1, name1, m1 = detect_best_preset(self.scheda1_path)
            self.assertEqual(pid1, 1, f"Scheda 1 doveva essere P1, invece è P{pid1} ({name1})")
            self.assertEqual(name1, "P1_Matita_UltraLeggera")
            self.assertGreater(m1["faint_chars_count"], 0)

        if self.scheda2_path.exists():
            pid2, name2, m2 = detect_best_preset(self.scheda2_path)
            self.assertEqual(pid2, 2, f"Scheda 2 doveva essere P2, invece è P{pid2} ({name2})")
            self.assertEqual(name2, "P2_Biro_Marcata")
            self.assertEqual(m2["faint_chars_count"], 0)

    def test_auto_detect_synthetic_scenarios(self):
        """Verifica trigger sintetici: Sfondo scuro -> P4, Tratto fine -> P5, Penna standard -> P3."""
        # Scenario Sfondo Scuro (Carta riciclata grigia, mediana 190 < 225) -> P4
        dark_paper = np.full((400, 600, 3), 190, dtype=np.uint8)
        pid_dark, name_dark, _ = detect_best_preset(dark_paper)
        self.assertEqual(pid_dark, 4)
        self.assertEqual(name_dark, "P4_Sbianca_Fondo")

        # Scenario Ombra di scansione ADF (bordo 160 vs centro 250 -> delta > 26) -> P4
        shadow_paper = np.full((500, 700), 250, dtype=np.uint8)
        shadow_paper[:30, :] = 160
        pid_shadow, name_shadow, _ = detect_best_preset(shadow_paper)
        self.assertEqual(pid_shadow, 4)

        # Scenario Tratto ultra-fine (1 pixel su larghezza 2000 px) -> P5
        thin_doc = np.full((1600, 2000), 255, dtype=np.uint8)
        y_start, y_end = int(1600 * 0.35), int(1600 * 0.82)
        x_start, x_end = int(2000 * 0.18), int(2000 * 0.92)
        cv2.rectangle(thin_doc, (x_start, y_start), (x_end, y_end), 0, 2)
        for i in range(12):
            x = x_start + 40 + i * 50
            y = y_start + 80
            cv2.line(thin_doc, (x, y), (x + 12, y), 40, 1)
            cv2.line(thin_doc, (x + 12, y), (x, y + 25), 40, 1)
        pid_thin, name_thin, _ = detect_best_preset(thin_doc)
        self.assertEqual(pid_thin, 5)
        self.assertEqual(name_thin, "P5_Micro_Dettaglio")

        # Scenario Penna a sfera standard (tratto 2px a 2000px, raggio normale) -> P3
        std_doc = np.full((1600, 2000), 255, dtype=np.uint8)
        cv2.rectangle(std_doc, (x_start, y_start), (x_end, y_end), 0, 2)
        for i in range(12):
            cv2.line(std_doc, (x_start + 50 + i * 60, y_start + 60), (x_start + 50 + i * 60, y_start + 120), 50, 2)
            cv2.circle(std_doc, (x_start + 50 + i * 60, y_start + 80), 8, 50, 1)
        pid_std, name_std, _ = detect_best_preset(std_doc)
        self.assertEqual(pid_std, 3)
        self.assertEqual(name_std, "P3_Standard_Bilanciato")

    # =========================================================================
    # 3. TEST PIPELINE IA (PREPARE, CREATE COPY, CLEAN)
    # =========================================================================

    def test_create_ai_optimized_copy(self):
        """Verifica generazione copia JPEG per l'IA con preset specificato."""
        if not self.scheda2_path.exists():
            self.skipTest("Scheda 2 non trovata")

        # Copia con Preset 2
        ai_file = create_ai_optimized_copy(self.scheda2_path, 2)
        self.assertTrue(ai_file.exists())
        self.assertTrue(ai_file.is_file())
        self.assertGreater(ai_file.stat().st_size, 1000)
        self.assertIn("_p2.jpg", ai_file.name)

        # Copia con Preset 0 (originale pulito)
        ai_file0 = create_ai_optimized_copy(self.scheda2_path, 0)
        self.assertTrue(ai_file0.exists())
        self.assertIn("_p0.jpg", ai_file0.name)

    def test_prepare_scan_for_pipeline(self):
        """Verifica prepare_scan_for_pipeline: orientamento, auto-detect e salvataggio copia IA."""
        if not self.scheda1_path.exists():
            self.skipTest("Scheda 1 non trovata")

        temp_ai_path, pid, pname = prepare_scan_for_pipeline(self.scheda1_path)
        self.assertEqual(pid, 1)
        self.assertEqual(pname, "P1_Matita_UltraLeggera")
        self.assertTrue(temp_ai_path.exists())
        self.assertIn("_p1.jpg", temp_ai_path.name)
        self.assertGreater(temp_ai_path.stat().st_size, 1000)

        # Test auto_detect_preset=False (modalità predefinita P0)
        p0_path, p0_id, p0_name = prepare_scan_for_pipeline(self.scheda1_path, auto_detect_preset=False)
        self.assertEqual(p0_id, 0)
        self.assertEqual(p0_name, "P0")
        self.assertTrue(p0_path.exists())
        self.assertIn("_p0.jpg", p0_path.name)

    def test_clean_temp_ai_dir(self):
        """Verifica che clean_temp_ai_dir rimuova tutti i file in temp_ai/."""
        TEMP_AI_DIR.mkdir(parents=True, exist_ok=True)
        # Crea alcuni file fittizi
        f1 = TEMP_AI_DIR / "test_scheda_p1.jpg"
        f2 = TEMP_AI_DIR / "test_scheda_p2.jpg"
        f1.write_bytes(b"dummy1")
        f2.write_bytes(b"dummy2")

        self.assertTrue(f1.exists())
        self.assertTrue(f2.exists())

        deleted = clean_temp_ai_dir()
        self.assertGreaterEqual(deleted, 2)
        self.assertFalse(f1.exists())
        self.assertFalse(f2.exists())

    # =========================================================================
    # 4. TEST IMAGEVIEWER V3 UI & TOOLBAR INTEGRATION
    # =========================================================================

    def test_image_viewer_v3_preset_methods(self):
        """Verifica i metodi di ImageViewerV3: set_active_preset, apply_preset_1..5, save_temp_ai_copy."""
        if not self.scheda1_path.exists():
            self.skipTest("Scheda 1 non trovata")

        # Inizializza ImageViewer headless (senza loop Tkinter)
        viewer = ImageViewerV3.__new__(ImageViewerV3)
        viewer.current_image_path = self.scheda1_path
        with Image.open(self.scheda1_path) as img:
            base_rgb = img.convert("RGB")
            viewer._original_pil_image = base_rgb
            viewer.pil_image = base_rgb
        viewer._base_processed_image = viewer.pil_image.copy()
        viewer._original_cache = {str(self.scheda1_path): viewer._original_pil_image}
        viewer._processed_cache = {}
        viewer._preset_state_cache = {}
        viewer._active_preset = 0
        viewer._brightness_steps = 0
        viewer._contrast_steps = 0
        viewer.on_image_changed_callback = None
        viewer._display_image = MagicMock()
        viewer._update_preset_buttons = MagicMock()

        # Test set_active_preset(pid) per 1..5
        for pid in range(1, 6):
            ok = viewer.set_active_preset(pid)
            self.assertTrue(ok, f"set_active_preset({pid}) deve restituire True")
            self.assertEqual(viewer.get_active_preset(), pid)
            self.assertEqual(viewer._active_preset, pid)
            self.assertIn(str(self.scheda1_path), viewer._processed_cache)
            self.assertEqual(viewer._preset_state_cache[str(self.scheda1_path)], pid)

        # Test save_temp_ai_copy()
        viewer.set_active_preset(3)
        saved_copy = viewer.save_temp_ai_copy()
        self.assertIsNotNone(saved_copy)
        self.assertTrue(saved_copy.exists())
        self.assertIn("_p3.jpg", saved_copy.name)
        self.assertGreater(saved_copy.stat().st_size, 1000)

        # Test reset_image() tramite set_active_preset(0)
        ok_reset = viewer.set_active_preset(0)
        self.assertTrue(ok_reset)
        self.assertEqual(viewer.get_active_preset(), 0)
        self.assertEqual(viewer._active_preset, 0)

    def test_image_viewer_v3_cache_hit_and_callbacks(self):
        """Verifica che la cache di ImageViewerV3 eviti ricalcoli se il preset è già memorizzato e che i callback funzionino."""
        if not self.scheda1_path.exists():
            self.skipTest("Scheda 1 non trovata")

        preset_callback_called = []
        viewer = ImageViewerV3.__new__(ImageViewerV3)
        viewer.current_image_path = self.scheda1_path
        with Image.open(self.scheda1_path) as img:
            base_rgb = img.convert("RGB")
            base_rgb._ba_mtime = self.scheda1_path.stat().st_mtime
            viewer._original_pil_image = base_rgb
            viewer.pil_image = base_rgb
        viewer._base_processed_image = viewer.pil_image.copy()
        viewer._original_cache = {str(self.scheda1_path): viewer._original_pil_image}
        viewer._processed_cache = {}
        viewer._preset_state_cache = {}
        viewer._active_preset = 0
        viewer._brightness_steps = 0
        viewer._contrast_steps = 0
        viewer.on_image_changed_callback = None
        viewer.on_preset_changed_callback = lambda pid: preset_callback_called.append(pid)
        viewer._display_image = MagicMock()
        viewer._update_preset_buttons = MagicMock()

        # 1. Imposta Preset 2
        viewer.set_active_preset(2)
        self.assertIn(2, preset_callback_called)
        cached_img_p2 = viewer._processed_cache[str(self.scheda1_path)]
        self.assertIsNotNone(cached_img_p2)

        # 2. Chiama load_image con initial_preset=2: deve colpire la cache senza rigenerare
        viewer._display_image.reset_mock()
        viewer.load_image(str(self.scheda1_path), initial_preset=2)
        self.assertIs(viewer.pil_image, cached_img_p2)
        self.assertEqual(viewer.get_active_preset(), 2)
        viewer._display_image.assert_called_once()

    def test_unicode_path_support(self):
        """Verifica la robustezza di preset_engine su percorsi con caratteri accentati/Unicode (tipici di Windows IT)."""
        TEMP_AI_DIR.mkdir(parents=True, exist_ok=True)
        unicode_img_path = TEMP_AI_DIR / "scheda_caffè_città_test.jpg"
        try:
            dummy = np.full((100, 100, 3), 230, dtype=np.uint8)
            cv2.line(dummy, (10, 20), (80, 20), (30, 30, 30), 2)
            # Salva con Pillow o imencode
            ok_enc, buf = cv2.imencode(".jpg", dummy, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            self.assertTrue(ok_enc)
            unicode_img_path.write_bytes(buf.tobytes())
            self.assertTrue(unicode_img_path.exists())

            # 1. Test apply_preset su percorso con accenti
            enhanced = apply_preset(unicode_img_path, 1)
            self.assertIsNotNone(enhanced)
            self.assertEqual(enhanced.shape, (100, 100, 3))

            # 2. Test detect_best_preset su percorso con accenti
            pid, pname, metrics = detect_best_preset(unicode_img_path)
            self.assertIn(pid, [1, 2, 3, 4, 5])
            self.assertIsInstance(pname, str)

            # 3. Test create_ai_optimized_copy su percorso con accenti
            ai_copy = create_ai_optimized_copy(unicode_img_path, pid)
            self.assertTrue(ai_copy.exists())
            self.assertGreater(ai_copy.stat().st_size, 500)
        finally:
            unicode_img_path.unlink(missing_ok=True)

    def test_app_v3_batch_pipeline_flow(self):
        """Verifica la coerenza del flusso multi-scheda e della sincronizzazione preset in app_v3."""
        from src.ui.app import BatchItem, BancoAlimentareAppV3

        # 1. Verifica campi della dataclass BatchItem
        item = BatchItem(
            image_path=str(self.scheda1_path),
            preset_id=1,
            ai_image_path="test_images/temp_ai/scheda1_p1.jpg",
            force_strong=False
        )
        self.assertEqual(item.preset_id, 1)
        self.assertEqual(item.ai_image_path, "test_images/temp_ai/scheda1_p1.jpg")
        self.assertFalse(item.force_strong)

        # 2. Verifica la logica di _commit_current_item e _on_preset_changed
        app = BancoAlimentareAppV3.__new__(BancoAlimentareAppV3)
        app.batch_items = [item]
        app.batch_index = 0
        app.current_processed_card = None
        app._inline_entry = None
        app.is_edit_mode = False

        mock_viewer = MagicMock()
        mock_viewer.get_active_preset.return_value = 3
        app.image_viewer = mock_viewer

        # Cambio preset via callback
        app._on_preset_changed(3)
        self.assertEqual(item.preset_id, 3)

        # Commit slot corrente
        app._commit_current_item()
        self.assertEqual(item.preset_id, 3)


if __name__ == "__main__":
    unittest.main()
