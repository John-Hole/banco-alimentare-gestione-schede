"""Unit test per la gestione universale dello scanner Windows WIA (ADF e gestione fogli)."""

import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scanner.scanner_service import (
    ScannerService,
    SOURCE_FEEDER,
    SOURCE_FLATBED,
    WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES,
    WIA_DPS_DOCUMENT_HANDLING_STATUS,
    WIA_HANDLING_FEEDER,
    WIA_ERROR_PAPER_EMPTY,
    WIA_ERROR_PAPER_PROBLEM,
    HTTP_STATUS_CONFLICT,
    ScanBatchError,
)


class MockWiaProp:
    def __init__(self, prop_id: int, value):
        self.PropertyID = prop_id
        self.Value = value


class MockWiaItem:
    def __init__(self):
        self.Properties = [
            MockWiaProp(6147, 200),
            MockWiaProp(6148, 200),
            MockWiaProp(6151, 1654),
            MockWiaProp(6152, 2338),
            MockWiaProp(6164, 0),
        ]
        self.transfer_calls = 0
        self.transfer_side_effect = None

    def Transfer(self, fmt):
        self.transfer_calls += 1
        if self.transfer_side_effect:
            if isinstance(self.transfer_side_effect, Exception):
                raise self.transfer_side_effect
            if callable(self.transfer_side_effect):
                return self.transfer_side_effect()
        return MockWiaImage()


class MockWiaImage:
    def __init__(self, blank: bool = False):
        self.blank = blank

    def SaveFile(self, path):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (600, 800), color=(255, 255, 255))
        if not self.blank:
            draw = ImageDraw.Draw(img)
            for y in range(50, 750, 30):
                draw.text((50, y), "Colletta Alimentare Test Scheda ADF", fill=(0, 0, 0))
        img.save(path, format="JPEG")


class MockWiaDevice:
    def __init__(self, dev_id: str = "{6BDD1FC6-810F-11D0-BEC7-08002BE2092F}\\0000"):
        self.DeviceID = dev_id
        self.item = MockWiaItem()
        self.Properties = [
            MockWiaProp(WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES, WIA_HANDLING_FEEDER),
            MockWiaProp(WIA_DPS_DOCUMENT_HANDLING_STATUS, 1),
        ]
        self.Items = self

    def Item(self, idx):
        return self.item


class TestFeederEmptyHandling(unittest.TestCase):
    """Verifica il comportamento dello scanner Windows WIA universale."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        ScannerService._device_id = None
        ScannerService._last_known_ip = None

    def tearDown(self):
        ScannerService._device_id = None
        ScannerService._last_known_ip = None
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch.object(ScannerService, "_connect")
    def test_feeder_empty_transfer_detects_paper_empty(self, mock_connect):
        """Se il cassetto ADF è vuoto, Transfer solleva subito WIA_ERROR_PAPER_EMPTY e termina senza errori."""
        device = MockWiaDevice()
        mock_connect.return_value = device
        device.item.transfer_side_effect = Exception(f"COM error {hex(WIA_ERROR_PAPER_EMPTY)}: Paper empty")

        pages = ScannerService.acquire_pages(
            device_id="mock_device",
            source=SOURCE_FEEDER,
            max_pages=30,
        )

        self.assertEqual(pages, [])
        self.assertEqual(device.item.transfer_calls, 1)

    @patch.object(ScannerService, "_connect")
    def test_feeder_multiple_sheets_then_empty(self, mock_connect):
        """Se il cassetto ha 2 fogli, acquisisce entrambi i fogli e al 3° Transfer termina per fine carta."""
        device = MockWiaDevice()
        mock_connect.return_value = device

        call_count = 0
        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                raise Exception(f"COM error {hex(WIA_ERROR_PAPER_EMPTY)}: Paper empty")
            return MockWiaImage()

        device.item.transfer_side_effect = side_effect

        with patch("src.scanner.scanner_service.TEST_IMAGES_DIR", Path(self.test_dir)):
            pages = ScannerService.acquire_pages(
                device_id="mock_device",
                source=SOURCE_FEEDER,
                max_pages=30,
                optimize=False,
            )

        self.assertEqual(len(pages), 2, "Devono essere acquisiti esattamente 2 fogli!")
        self.assertEqual(device.item.transfer_calls, 3)

    @patch.object(ScannerService, "_connect")
    def test_blank_page_archived_and_continues_batch(self, mock_connect):
        """Se tra i fogli c'è una pagina bianca, viene spostata in _scartate_bianche e il lotto prosegue."""
        device = MockWiaDevice()
        mock_connect.return_value = device

        call_count = 0
        def side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MockWiaImage(blank=False)  # Foglio 1 valido
            elif call_count == 2:
                return MockWiaImage(blank=True)   # Foglio 2 bianco
            elif call_count == 3:
                return MockWiaImage(blank=False)  # Foglio 3 valido
            else:
                raise Exception(f"COM error {hex(WIA_ERROR_PAPER_EMPTY)}: Paper empty")

        device.item.transfer_side_effect = side_effect

        with patch("src.scanner.scanner_service.TEST_IMAGES_DIR", Path(self.test_dir)):
            pages = ScannerService.acquire_pages(
                device_id="mock_device",
                source=SOURCE_FEEDER,
                max_pages=30,
                optimize=False,
                skip_blank_pages=True,
            )

        # Il lotto non deve essere interrotto: devono risultare i 2 fogli validi
        self.assertEqual(len(pages), 2, "Il lotto deve contenere i 2 fogli validi!")
        self.assertEqual(ScannerService.last_blank_skipped, 1)

        # La pagina bianca deve essere archiviata in _scartate_bianche
        discarded = list((Path(self.test_dir) / "_scartate_bianche").glob("*.jpg"))
        self.assertEqual(len(discarded), 1)

    def test_is_paper_empty_recognition(self):
        """Verifica che tutte le espressioni italiane e codici d'errore vengano riconosciuti come vassoio vuoto."""
        exc_native = Exception(f"WIA Error ({hex(WIA_ERROR_PAPER_EMPTY)})")
        self.assertTrue(ScannerService._is_paper_empty(exc_native))

        test_messages = [
            "carta esaurita nel caricatore",
            "nessun foglio presente",
            "caricatore vuoto",
            "cassetto vuoto",
            "vassoio vuoto",
            "feeder empty",
            "out of paper in adf",
            "device paper_empty signal",
        ]
        for msg in test_messages:
            self.assertTrue(ScannerService._is_paper_empty(Exception(msg)), f"Fallito per: {msg}")

        exc_other = Exception("Cover open on scanner (0x80210016)")
        self.assertFalse(ScannerService._is_paper_empty(exc_other))

    def test_escl_http_409_exception_tuple_recognized(self):
        """Verifica che l'eccezione esatta restituita dal driver eSCL Windows (0x80190199 / Conflitto 409)
        venga riconosciuta come fine naturale del lotto ADF (paper empty).
        """
        exc_exact = Exception(-2147352567, "Eccezione.", (0, None, "Conflitto (409).\r\n", None, 0, -2145844839), None)
        codes = ScannerService._com_codes(exc_exact)
        self.assertIn(HTTP_STATUS_CONFLICT, codes)
        self.assertIn(0x80190199, codes)

        self.assertTrue(
            ScannerService._is_paper_empty(exc_exact, is_feeder=True),
            "Eccezione Conflitto (409) su ADF deve essere interpretata come vassoio vuoto",
        )

        self.assertFalse(
            ScannerService._is_paper_empty(exc_exact, is_feeder=False),
            "Eccezione Conflitto (409) su FLATBED non deve essere scambiata per fine carta",
        )

    @patch.object(ScannerService, "_connect")
    def test_acquire_pages_ends_cleanly_on_escl_409(self, mock_connect):
        """Verifica che quando il foglio 1 ha successo e il foglio 2 riceve HTTP 409 Conflitto,
        acquire_pages termini con successo restituendo [foglio 1] senza sollevare ScanBatchError.
        """
        device = MockWiaDevice()
        mock_connect.return_value = device

        call_idx = 0
        def transfer_effect():
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return MockWiaImage()
            raise Exception(-2147352567, "Eccezione.", (0, None, "Conflitto (409).\r\n", None, 0, -2145844839), None)

        device.item.transfer_side_effect = transfer_effect

        with patch("src.scanner.scanner_service.TEST_IMAGES_DIR", Path(self.test_dir)):
            pages = ScannerService.acquire_pages(
                device_id="SWD\\Escl\\5d3c39df-1cb9-4168-9c50-caeb36adc0cc",
                source=SOURCE_FEEDER,
                max_pages=30,
                optimize=False,
            )

        self.assertEqual(len(pages), 1, "Deve conservare la pagina 1 acquisita con successo")
        self.assertEqual(device.item.transfer_calls, 2)

    def test_resolve_device_id_single_device(self):
        """Se Windows rileva un solo scanner, resolve_device_id lo usa direttamente."""
        with patch("win32com.client.Dispatch") as mock_dispatch:
            mock_mgr = MagicMock()
            info_mock = MagicMock()
            info_mock.DeviceID = "Lexmark_WIA_123"
            mock_mgr.DeviceInfos = [info_mock]
            mock_dispatch.return_value = mock_mgr

            resolved = ScannerService.resolve_device_id()
            self.assertEqual(resolved, "Lexmark_WIA_123")

    def test_resolve_device_id_no_devices_raises(self):
        """Se Windows non ha scanner installati o collegati, solleva ConnectionError chiaro."""
        with patch("win32com.client.Dispatch") as mock_dispatch:
            mock_mgr = MagicMock()
            mock_mgr.DeviceInfos = []
            mock_dispatch.return_value = mock_mgr

            with self.assertRaises(ConnectionError):
                ScannerService.resolve_device_id()

    def test_resolve_device_id_force_select_dialog(self):
        """select_device_dialog o force_select=True apre la finestra di dialogo nativa di Windows."""
        with patch("win32com.client.Dispatch") as mock_dispatch:
            mock_mgr = MagicMock()
            mock_mgr.DeviceInfos = [MagicMock(), MagicMock()]

            mock_dialog = MagicMock()
            chosen_dev = MagicMock()
            chosen_dev.DeviceID = "Chosen_Scanner_XYZ"
            mock_dialog.ShowSelectDevice.return_value = chosen_dev

            def dispatch_side_effect(progid):
                if progid == "WIA.DeviceManager":
                    return mock_mgr
                if progid == "WIA.CommonDialog":
                    return mock_dialog
                return MagicMock()

            mock_dispatch.side_effect = dispatch_side_effect

            chosen = ScannerService.select_device_dialog()
            self.assertEqual(chosen, "Chosen_Scanner_XYZ")
            mock_dialog.ShowSelectDevice.assert_called_once()

    def test_scan_batch_error_formatting_no_zero_pages(self):
        """Verifica la logica di visualizzazione del messaggio di ScanBatchError."""
        err_zero = ScanBatchError("Scanner occupato", pages=[])
        msg_zero = f"{err_zero}\n\nFogli acquisiti e conservati: {len(err_zero.pages)}" if err_zero.pages else str(err_zero)
        self.assertNotIn("Fogli acquisiti e conservati: 0", msg_zero)
        self.assertEqual(msg_zero, "Scanner occupato")

        err_two = ScanBatchError("Inceppamento", pages=[Path("p1.jpg"), Path("p2.jpg")])
        msg_two = f"{err_two}\n\nFogli acquisiti e conservati: {len(err_two.pages)}" if err_two.pages else str(err_two)
        self.assertIn("Fogli acquisiti e conservati: 2", msg_two)


if __name__ == "__main__":
    unittest.main()
