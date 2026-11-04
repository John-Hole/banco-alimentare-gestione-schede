"""Test per il rilevamento e la rotazione automatica delle schede capovolte / rovesciate."""

import sys
import unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scanner.scanner_service import ScannerService, TEST_IMAGES_DIR
from src.models import RawCardExtraction
from src.extractors.base import BaseVisionExtractor


class TestAutoOrientation(unittest.TestCase):

    def setUp(self):
        self.tmp_files = []

    def tearDown(self):
        for p in self.tmp_files:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    def test_is_page_upside_down_detection(self):
        """Verifica che una scheda dritta venga riconosciuta come non capovolta, e capovolta se ruotata di 180°."""
        p01 = TEST_IMAGES_DIR / "scan_batch_20261024_105521_page01.jpg"
        if not p01.exists():
            self.skipTest("Immagine di test page01 non presente")

        with Image.open(p01) as im:
            # 1. Scheda normale diritta: non deve risultare capovolta
            self.assertFalse(ScannerService.is_page_upside_down(im))

            # 2. Ruotata di 180°: deve essere rilevata come capovolta
            rot180 = im.rotate(180, expand=True)
            self.assertTrue(ScannerService.is_page_upside_down(rot180))

    def test_ensure_upright_orientation_flips_and_is_idempotent(self):
        """Verifica che ensure_upright_orientation rigiri la scheda capovolta e poi non la modifichi più."""
        src_path = TEST_IMAGES_DIR / "scan_batch_20261024_105521_page01.jpg"
        if not src_path.exists():
            self.skipTest("Immagine di test page01 non presente")

        copy_path = TEST_IMAGES_DIR / "tmp_test_inverted.jpg"
        self.tmp_files.append(copy_path)

        # Crea un'immagine sicuramente capovolta (180°)
        with Image.open(src_path) as im:
            im.rotate(180, expand=True).save(str(copy_path), format="JPEG", quality=95)

        # 1. Prima chiamata: deve rilevare che è capovolta e ruotarla a 180°
        modificato = ScannerService.ensure_upright_orientation(copy_path)
        self.assertTrue(modificato, "L'immagine capovolta doveva essere rigirata automaticamente")

        # Verifica che ora l'immagine sia diritta
        with Image.open(copy_path) as im:
            self.assertFalse(ScannerService.is_page_upside_down(im))

        # 2. Seconda chiamata: l'immagine è già diritta, non deve essere più modificata (idempotente)
        seconda_modifica = ScannerService.ensure_upright_orientation(copy_path)
        self.assertFalse(seconda_modifica, "L'immagine già diritta non doveva subire ulteriori rotazioni")

    def test_ensure_upright_orientation_on_portrait(self):
        """Verifica che una scheda in formato portrait (verticale) venga orientata orizzontale e diritta."""
        src_path = TEST_IMAGES_DIR / "scan_batch_20261024_105521_page01.jpg"
        if not src_path.exists():
            self.skipTest("Immagine di test page01 non presente")

        port_copy = TEST_IMAGES_DIR / "tmp_test_portrait.jpg"
        self.tmp_files.append(port_copy)

        with Image.open(src_path) as im:
            # Ruota di 90° per renderla portrait
            im.rotate(90, expand=True).save(str(port_copy), format="JPEG", quality=95)

        modificato = ScannerService.ensure_upright_orientation(port_copy)
        self.assertTrue(modificato)

        with Image.open(port_copy) as im:
            self.assertGreaterEqual(im.width, im.height, "Deve essere in formato orizzontale (MOD. A)")
            self.assertFalse(ScannerService.is_page_upside_down(im), "Deve essere orientata per il verso giusto")

    def test_prepare_uploaded_files_auto_rotates(self):
        """Verifica che prepare_uploaded_files corregga automaticamente i file caricati rovesciati."""
        src_path = TEST_IMAGES_DIR / "scan_batch_20261024_105521_page01.jpg"
        if not src_path.exists():
            self.skipTest("Immagine di test page01 non presente")

        inv_file = TEST_IMAGES_DIR / "tmp_test_for_upload.jpg"
        self.tmp_files.append(inv_file)

        with Image.open(src_path) as im:
            im.rotate(180, expand=True).save(str(inv_file), format="JPEG", quality=95)

        ready, warnings = ScannerService.prepare_uploaded_files([str(inv_file)])
        self.assertEqual(len(ready), 1)
        self.tmp_files.extend(ready)

        with Image.open(ready[0]) as im:
            self.assertFalse(
                ScannerService.is_page_upside_down(im),
                "Il file preparato per il caricamento deve essere stato rigirato nel verso corretto"
            )

    def test_raw_card_extraction_scheda_capovolta_field(self):
        """Verifica che il campo scheda_capovolta sia supportato nel modello e nel parser JSON."""
        raw = RawCardExtraction(scheda_capovolta=True)
        self.assertTrue(raw.scheda_capovolta)

        raw_default = RawCardExtraction()
        self.assertFalse(raw_default.scheda_capovolta)

        # Test parsing da JSON
        json_sample = '''
        {
            "supermercato": "CONAD",
            "numero_scheda": "1",
            "righe_scatole": [],
            "totali_dichiarati": {},
            "scheda_capovolta": true
        }
        '''
        parsed = BaseVisionExtractor.parse_json_response(json_sample)
        self.assertTrue(parsed.scheda_capovolta)


if __name__ == "__main__":
    unittest.main()
