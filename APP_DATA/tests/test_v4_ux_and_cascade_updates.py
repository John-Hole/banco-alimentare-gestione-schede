"""Test di verifica per le modifiche UX/UI e Cascade Updates:
1. Rimozione di Gemini 3.8 dalle cascate e rimozione di cascade_v2 (mantenuto solo gemini_38 fisso in alto).
2. Avvio a schermo intero massimizzato (_ensure_maximized e state zoomed).
3. Navigazione immediata con freccette della tastiera sulla tabella categorie.
4. Isolamento log per singola scheda e switch automatico al nuovo foglio in arrivo.
5. Doppio clic sul tasto Reset per ripristino scheda grezza (.raw.jpg) senza taratura scanner.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import (
    SUPPORTED_PROVIDERS,
    CASCADE_FREE_MODES,
    SUPPORTED_ARBITRATOR_MODELS,
    ARBITRATOR_DISPLAY_MAP,
    load_app_config,
)
from src.extractors.cascade_extractor import CascadeVisionExtractor
from src.ui.app import BancoAlimentareApp, BatchItem
from src.ui.components import ImageViewerV3
from src.models import RawCardExtraction, ProcessedCard


class TestCascadeAndModelUpdates(unittest.TestCase):
    """Test 1: Rimozione di Gemini 3.8 dalle cascate e rimozione di cascade_v2."""

    def test_cascade_v2_removed_from_supported_providers(self):
        self.assertNotIn("cascade_v2", SUPPORTED_PROVIDERS)
        self.assertIn("cascade", SUPPORTED_PROVIDERS)
        self.assertIn("gemini_38", SUPPORTED_PROVIDERS)
        self.assertIn("gemini_35", SUPPORTED_PROVIDERS)

    def test_gemini_38_removed_from_cascade_free_modes(self):
        self.assertNotIn("gemini_38", CASCADE_FREE_MODES)
        self.assertNotIn("gemini_38_then_35", CASCADE_FREE_MODES)
        self.assertNotIn("gemini_35_then_38", CASCADE_FREE_MODES)
        self.assertIn("gemini_35", CASCADE_FREE_MODES)

    def test_gemini_38_removed_from_arbitrator_models(self):
        self.assertNotIn("gemini-3.8-flash", SUPPORTED_ARBITRATOR_MODELS)
        self.assertNotIn("gemini-3.8-flash", ARBITRATOR_DISPLAY_MAP)
        self.assertIn("gemini-3.5-flash-lite", SUPPORTED_ARBITRATOR_MODELS)

    def test_cascade_extractor_plan_excludes_gemini_38(self):
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            free_primary_mode="gemini_35",
            paid_mode="ember",
            fallback_mode="nemotron_free",
        )
        plan = extractor._build_cascade_plan()
        models = [step["model"] for step in plan]
        self.assertNotIn("gemini-3.8-flash", models)
        self.assertEqual(models[0], "gemini-3.5-flash-lite")

    def test_cascade_extractor_normalizes_legacy_38_mode(self):
        mock_tracker = MagicMock()
        mock_tracker.is_exhausted.return_value = False

        extractor = CascadeVisionExtractor(
            gemini_api_key_1="KEY1",
            openrouter_api_key="OR_KEY",
            quota_tracker=mock_tracker,
            free_primary_mode="gemini_38",
            arbitrator_model="gemini-3.8-flash",
        )
        self.assertEqual(extractor.free_primary_mode, "gemini_35")
        self.assertEqual(extractor.arbitrator_model, "gemini-3.5-flash-lite")
        plan = extractor._build_cascade_plan()
        models = [step["model"] for step in plan]
        self.assertNotIn("gemini-3.8-flash", models)


class TestMaximizedStartupAndShortcuts(unittest.TestCase):
    """Test 2 & 3: Avvio massimizzato e navigazione frecce tastiera."""

    def test_ensure_maximized_method_exists(self):
        self.assertTrue(hasattr(BancoAlimentareApp, "_ensure_maximized"))

    def test_global_arrow_key_method_exists(self):
        self.assertTrue(hasattr(BancoAlimentareApp, "_on_global_arrow_key"))
        self.assertTrue(hasattr(BancoAlimentareApp, "_focus_tree_if_ready"))

    def test_on_global_arrow_key_focuses_tree(self):
        app = MagicMock(spec=BancoAlimentareApp)
        tree = MagicMock()
        tree.winfo_ismapped.return_value = True
        tree.get_children.return_value = ["cat1", "cat2"]
        tree.selection.return_value = []
        app.tree = tree
        app.focus_get.return_value = None

        event = MagicMock()
        event.keysym = "Down"

        res = BancoAlimentareApp._on_global_arrow_key(app, event)
        tree.selection_set.assert_called_with("cat1")
        tree.focus.assert_called_with("cat1")
        tree.focus_set.assert_called()
        self.assertEqual(res, "break")


class TestLogIsolationAndBatchSwitch(unittest.TestCase):
    """Test 4: Isolamento log per singola scheda e switch automatico sul nuovo foglio."""

    def test_batch_item_has_progress_logs_and_title(self):
        item = BatchItem(image_path="test.jpg")
        self.assertEqual(item.progress_logs, [])
        self.assertEqual(item.last_progress_title, "")

    def test_on_streamed_page_acquired_switches_to_new_page(self):
        app = MagicMock(spec=BancoAlimentareApp)
        app.batch_items = [
            BatchItem(image_path="p1.jpg", status="ready"),
        ]
        app.batch_index = 0
        app.config_data = {}
        app._strip_visible = True
        app._extracting = False
        app.session_logger = MagicMock()

        with patch("src.ui.app.prepare_scan_for_pipeline", return_value=(Path("p2_ai.jpg"), 0, "P0")), \
             patch("src.ui.app.ScannerService.get_raw_archive", return_value="raw_p2.jpg"):
            BancoAlimentareApp._on_streamed_page_acquired(app, Path("p2.jpg"))

        app._commit_current_item.assert_called_once()
        self.assertEqual(len(app.batch_items), 2)
        self.assertEqual(app.batch_index, 1)
        app._select_batch_item.assert_called_with(1)
        app._process_next_pending.assert_called_once()


class TestResetDoubleClickRawUntuned(unittest.TestCase):
    """Test 5: Doppio clic su Reset ripristina la scheda grezza senza taratura base."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.img_path = Path(self.temp_dir) / "sheet_01.jpg"
        self.raw_path = Path(self.temp_dir) / "sheet_01.raw.jpg"

        # Simula file calibrato (modificato) e file grezzo puro (diverso)
        with open(self.img_path, "wb") as f:
            f.write(b"CALIBRATED_IMAGE_DATA")
        with open(self.raw_path, "wb") as f:
            f.write(b"RAW_UNTUNED_ORIGINAL_DATA")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reset_image_to_raw_untuned_restores_raw_file(self):
        viewer = MagicMock(spec=ImageViewerV3)
        viewer.current_image_path = self.img_path
        viewer._original_cache = {"some": "cache"}
        viewer._processed_cache = {"some": "cache"}
        viewer._preset_state_cache = {"some": "cache"}
        raw_cb = MagicMock()
        viewer.on_raw_reset_callback = raw_cb

        success = ImageViewerV3.reset_image_to_raw_untuned(viewer)
        self.assertTrue(success)

        # Il file di lavoro deve essere stato ripristinato dal .raw.jpg grezzo
        with open(self.img_path, "rb") as f:
            restored_content = f.read()
        self.assertEqual(restored_content, b"RAW_UNTUNED_ORIGINAL_DATA")

        # Verifica svuotamento cache e ricaricamento a preset 0
        viewer.load_image.assert_called_with(str(self.img_path), initial_preset=0)
        raw_cb.assert_called_with(self.img_path)


if __name__ == "__main__":
    unittest.main()
