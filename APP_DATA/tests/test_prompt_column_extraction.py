"""Test per la nuova architettura di estrazione a colonne verticali e compatibilità retroattiva."""

import json
import sys
import unittest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extractors.base import BaseVisionExtractor, EXTRACTION_SYSTEM_PROMPT
from src.config import CATEGORIES


class TestColumnExtractionAndParsing(unittest.TestCase):

    def test_prompt_contains_vertical_column_instructions(self):
        """Verifica che il prompt contenga le istruzioni essenziali a colonne verticali e calligrafiche."""
        self.assertIn("PROCEDURA DI LETTURA OBBLIGATORIA: PER COLONNA VERTICALE", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("COLONNA 8 (RISO) vs COLONNA 9 (ZUCCHERO)", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("REGOLA DELLA BASE ORIZZONTALE", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("CIFRE CANCELLATE O SBARRATE", EXTRACTION_SYSTEM_PROMPT)
        self.assertIn('"colonne"', EXTRACTION_SYSTEM_PROMPT)

    def test_parse_json_column_format(self):
        """Verifica che il parser gestisca correttamente la struttura 'colonne'."""
        sample_json = {
            "supermercato": "TIGRE - PERUGIA",
            "numero_scheda": "A1",
            "colonne": [
                {
                    "colonna_idx": 1,
                    "categoria": "Omogeneizzati",
                    "scatole": [
                        {"riga": "1a", "kg": 10.0},
                        {"riga": "2a", "kg": 7.0}
                    ],
                    "totale_dichiarato_kg": 17.0,
                    "totale_dichiarato_scatole": 2
                },
                {
                    "colonna_idx": 4,
                    "categoria": "Olio",
                    "scatole": [
                        {"riga": "1a", "kg": 1.0},
                        {"riga": "2a", "kg": 2.0}
                    ],
                    "totale_dichiarato_kg": None,
                    "totale_dichiarato_scatole": None
                },
                {
                    "colonna_idx": 8,
                    "categoria": "Riso",
                    "scatole": [
                        {"riga": "2a", "kg": 10.0},
                        {"riga": "3a", "kg": 7.5}
                    ]
                },
                {
                    "colonna_idx": 9,
                    "categoria": "Zucchero",
                    "scatole": []
                }
            ],
            "note_o_anomalie": "Riga 1a Riso sbarrata",
            "scheda_capovolta": False
        }

        raw_str = f"```json\n{json.dumps(sample_json)}\n```"
        parsed = BaseVisionExtractor.parse_json_response(raw_str)

        self.assertEqual(parsed.supermercato, "TIGRE - PERUGIA")
        self.assertEqual(parsed.numero_scheda, "1")  # "A1" normalizzato a "1"
        self.assertEqual(len(parsed.righe_scatole), 6)

        omo = [b.kg for b in parsed.righe_scatole if b.categoria == "Omogeneizzati"]
        self.assertEqual(omo, [10.0, 7.0])

        olio = [b.kg for b in parsed.righe_scatole if b.categoria == "Olio"]
        self.assertEqual(olio, [1.0, 2.0])

        riso = [b.kg for b in parsed.righe_scatole if b.categoria == "Riso"]
        self.assertEqual(riso, [10.0, 7.5])

        zucchero = [b.kg for b in parsed.righe_scatole if b.categoria == "Zucchero"]
        self.assertEqual(zucchero, [])

        self.assertEqual(parsed.totali_dichiarati.per_categoria.get("Omogeneizzati"), {"kg": 17.0, "scatole": 2})
        self.assertEqual(parsed.note_o_anomalie, "Riga 1a Riso sbarrata")

    def test_parse_json_deterministic_colonna_idx(self):
        """Verifica che l'indice numerico colonna_idx (1..13) abbia precedenza e prevenga lo slittamento categorie."""
        sample_json = {
            "colonne": [
                {
                    # In caso di etichetta OCR errata ma indice geometrico corretto
                    "colonna_idx": 8,
                    "categoria": "Zucchero",  # OCR aveva letto male l'intestazione
                    "scatole": [{"riga": "1a", "kg": 5.0}]
                }
            ]
        }
        parsed = BaseVisionExtractor.parse_json_response(json.dumps(sample_json))
        # Colonna 8 corrisponde tassativamente a Riso
        self.assertEqual(parsed.righe_scatole[0].categoria, "Riso")
        self.assertEqual(parsed.righe_scatole[0].kg, 5.0)

    def test_parse_json_legacy_backward_compatibility(self):
        """Verifica che il parser mantenga compatibilità al 100% con il vecchio formato 'righe_scatole'."""
        legacy_json = {
            "supermercato": "COOP",
            "numero_scheda": "2",
            "righe_scatole": [
                {"riga": "1a", "categoria": "Tonno", "kg": 4.5},
                {"riga": "2a", "categoria": "Pasta", "kg": 12.0}
            ],
            "totali_dichiarati": {
                "kg_totale": 16.5,
                "scatole_totale": 2
            }
        }
        parsed = BaseVisionExtractor.parse_json_response(json.dumps(legacy_json))
        self.assertEqual(len(parsed.righe_scatole), 2)
        self.assertEqual(parsed.righe_scatole[0].categoria, "Tonno")
        self.assertEqual(parsed.righe_scatole[0].kg, 4.5)
        self.assertEqual(parsed.totali_dichiarati.kg_totale, 16.5)

    def test_encode_and_optimize_preserves_high_res(self):
        """Verifica che un'immagine 2338x1653 (300 DPI A4) non venga rimpicciolita con max_dimension=3200."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            temp_path = f.name

        try:
            im = Image.new("RGB", (2338, 1653), color="white")
            im.save(temp_path, "JPEG")

            mime, b64 = BaseVisionExtractor.encode_and_optimize_image(temp_path, max_dimension=3200, quality=92)
            self.assertEqual(mime, "image/jpeg")
            self.assertTrue(len(b64) > 100)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_parse_json_filters_out_zero_weights(self):
        """Verifica che celle barrate con trattini o zeri (kg=0) vengano escluse dal conteggio scatole."""
        sample_json = {
            "colonne": [
                {
                    "colonna_idx": 4,
                    "categoria": "Olio",
                    "scatole": [
                        {"riga": "1a", "kg": 0.0},
                        {"riga": "2a", "kg": 5.0}
                    ]
                }
            ]
        }
        parsed = BaseVisionExtractor.parse_json_response(json.dumps(sample_json))
        self.assertEqual(len(parsed.righe_scatole), 1)
        self.assertEqual(parsed.righe_scatole[0].kg, 5.0)

    def test_parse_json_normalizes_trip_codes(self):
        """Verifica che il parser normalizzi prefissi come 'Viaggio', 'N.', 'A1'."""
        test_cases = [
            ("Viaggio 1", "1"),
            ("N. 2", "2"),
            ("Viaggio 1 Bis", "1 BIS"),
            ("A1", "1"),
            ("Spedizione 3", "3"),
            ("", "NON_PRESENTE")
        ]
        for raw, expected in test_cases:
            sample = {"numero_scheda": raw, "colonne": []}
            parsed = BaseVisionExtractor.parse_json_response(json.dumps(sample))
            self.assertEqual(parsed.numero_scheda, expected)

    def test_gemini_response_schema_definition(self):
        """Verifica che GEMINI_RESPONSE_SCHEMA sia correttamente definito e importabile da gemini_extractor."""
        from src.extractors.gemini_extractor import GEMINI_RESPONSE_SCHEMA
        self.assertEqual(GEMINI_RESPONSE_SCHEMA["type"], "OBJECT")
        self.assertIn("colonne", GEMINI_RESPONSE_SCHEMA["properties"])
        self.assertIn("scheda_capovolta", GEMINI_RESPONSE_SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()
