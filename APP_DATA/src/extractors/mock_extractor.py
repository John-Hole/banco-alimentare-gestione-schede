"""Estrattore Mock con scenari realistici per test e collaudo immediato."""

import os
from typing import Dict, Any, List
from .base import BaseVisionExtractor
from ..models import RawCardExtraction, BoxItem, DeclaredTotals


class MockVisionExtractor(BaseVisionExtractor):
    """Fornisce risposte simulate realistiche per collaudare l'app."""

    def __init__(self, default_scenario: str = "scheda_perfetta"):
        self.scenario = default_scenario

    def set_scenario(self, scenario_name: str) -> None:
        self.scenario = scenario_name

    @staticmethod
    def get_available_scenarios() -> List[Dict[str, str]]:
        return [
            {
                "id": "scheda_perfetta",
                "title": "1. Scheda Perfetta (Tutto Verde)",
                "desc": "Casa Circ. Capanne, Scheda N°1, scatole e totale coincidente (48.0 Kg, 4 scatole)."
            },
            {
                "id": "totale_discordante",
                "title": "2. Totale Discordante (Flag Rosso)",
                "desc": "Somma scatole calcolata = 48.0 Kg, ma totale scritto a mano = 65.0 Kg."
            },
            {
                "id": "numero_mancante",
                "title": "3. Numero Scheda Mancante (Auto-assegnazione)",
                "desc": "Casella Scheda N° vuota: il sistema assegna in automatico la prima riga libera."
            },
            {
                "id": "numero_illeggibile",
                "title": "4. Numero Scheda Illeggibile (Flag Rosso Bloccante)",
                "desc": "Casella scarabocchiata: flag rosso, richiede selezione manuale obbligatoria."
            },
            {
                "id": "solo_totale",
                "title": "5. Solo Totale Senza Dettaglio Scatole (Flag Rosso)",
                "desc": "Righe scatola non compilate, presente solo un totale complessivo di 75.0 Kg."
            },
            {
                "id": "discrepanza_categoria",
                "title": "6. Discrepanza Singola Categoria (Flag Rosso)",
                "desc": "Dichiarato totale Tonno di 25.0 Kg ma c'è solo una scatola da 12.0 Kg."
            },
            {
                "id": "scheda_reale_tigre",
                "title": "7. Scheda Reale Tigre Ponte d'Oddi (11 scatole, 49.0 Kg)",
                "desc": "Scansione reale compilata a mano: 11 scatole per 49.0 Kg (con discrepanza compilatore su Biscotti: 2 scatole dichiarate vs 1 presente)."
            }
        ]

    def extract_from_image(self, image_path: str) -> RawCardExtraction:
        # Se nel nome del file c'è un indizio di scenario, possiamo anche rilevarlo
        fname = os.path.basename(image_path).lower()
        active = self.scenario
        for s in self.get_available_scenarios():
            if s["id"] in fname:
                active = s["id"]
                break

        if active == "scheda_perfetta":
            return RawCardExtraction(
                supermercato="Casa Circ. Capanne",
                numero_scheda="1",
                righe_scatole=[
                    BoxItem(categoria="Pasta", kg=12.5),
                    BoxItem(categoria="Pasta", kg=10.5),
                    BoxItem(categoria="Tonno", kg=15.0),
                    BoxItem(categoria="Pelati", kg=10.0),
                ],
                totali_dichiarati=DeclaredTotals(
                    kg_totale=48.0,
                    scatole_totale=4,
                    per_categoria={
                        "Pasta": {"kg": 23.0, "scatole": 2},
                        "Tonno": {"kg": 15.0, "scatole": 1},
                        "Pelati": {"kg": 10.0, "scatole": 1},
                    }
                ),
                note_o_anomalie="Scheda compilata in modo chiaro e leggibile"
            )

        elif active == "totale_discordante":
            return RawCardExtraction(
                supermercato="Casa Circ. Capanne",
                numero_scheda="2",
                righe_scatole=[
                    BoxItem(categoria="Pasta", kg=12.5),
                    BoxItem(categoria="Pasta", kg=10.5),
                    BoxItem(categoria="Tonno", kg=15.0),
                    BoxItem(categoria="Pelati", kg=10.0),
                ],
                totali_dichiarati=DeclaredTotals(
                    kg_totale=65.0,  # Discordante: calcolato è 48.0
                    scatole_totale=4,
                ),
                note_o_anomalie="Totale a penna corretto a mano con cancellatura"
            )

        elif active == "numero_mancante":
            return RawCardExtraction(
                supermercato="Casa Circ. Capanne",
                numero_scheda="NON_PRESENTE",
                righe_scatole=[
                    BoxItem(categoria="Riso", kg=14.0),
                    BoxItem(categoria="Legumi", kg=11.2),
                    BoxItem(categoria="Olio", kg=8.5),
                ],
                totali_dichiarati=DeclaredTotals(
                    kg_totale=33.7,
                    scatole_totale=3
                ),
                note_o_anomalie="Casella Scheda N° lasciata in bianco"
            )

        elif active == "numero_illeggibile":
            return RawCardExtraction(
                supermercato="Casa Circ. Capanne",
                numero_scheda="ILLEGGIBILE",
                righe_scatole=[
                    BoxItem(categoria="Latte", kg=12.0),
                    BoxItem(categoria="Biscotti", kg=9.5),
                ],
                totali_dichiarati=DeclaredTotals(
                    kg_totale=21.5,
                    scatole_totale=2
                ),
                note_o_anomalie="Numero scheda coperto da macchia d'inchiostro o scarabocchio"
            )

        elif active == "solo_totale":
            return RawCardExtraction(
                supermercato="Casa Circ. Capanne",
                numero_scheda="3",
                righe_scatole=[],  # Nessuna scatola riga per riga
                totali_dichiarati=DeclaredTotals(
                    kg_totale=75.0,
                    scatole_totale=6
                ),
                note_o_anomalie="Tabella scatole completamente vuota, presente solo totale complessivo"
            )

        elif active == "discrepanza_categoria":
            return RawCardExtraction(
                supermercato="Casa Circ. Capanne",
                numero_scheda="1",
                righe_scatole=[
                    BoxItem(categoria="Tonno", kg=12.0),
                    BoxItem(categoria="Pasta", kg=15.0),
                ],
                totali_dichiarati=DeclaredTotals(
                    kg_totale=27.0,
                    scatole_totale=2,
                    per_categoria={
                        "Tonno": {"kg": 25.0, "scatole": 2},  # Discordante! C'è solo 1 scatola da 12 kg
                        "Pasta": {"kg": 15.0, "scatole": 1},
                    }
                ),
                note_o_anomalie="Discrepanza evidente su categoria Tonno"
            )

        elif active == "scheda_reale_tigre":
            return RawCardExtraction(
                supermercato="TIGRE - PERUGIA STRADA PONTE D'ODDI, 8/A",
                numero_scheda="NON_PRESENTE",
                righe_scatole=[
                    BoxItem(categoria="Tonno", kg=3.0),
                    BoxItem(categoria="Olio", kg=1.0),
                    BoxItem(categoria="Pelati", kg=4.0),
                    BoxItem(categoria="Legumi", kg=3.0),
                    BoxItem(categoria="Pasta", kg=10.0),
                    BoxItem(categoria="Zucchero", kg=10.0),
                    BoxItem(categoria="Latte", kg=7.0),
                    BoxItem(categoria="Biscotti", kg=4.0),
                    BoxItem(categoria="Olio", kg=2.0),
                    BoxItem(categoria="Legumi", kg=4.0),
                    BoxItem(categoria="Latte", kg=1.0),
                ],
                totali_dichiarati=DeclaredTotals(
                    kg_totale=None,
                    scatole_totale=None,
                    per_categoria={
                        "Tonno": {"kg": 3.0, "scatole": 1},
                        "Olio": {"kg": 3.0, "scatole": 2},
                        "Pelati": {"kg": 4.0, "scatole": 1},
                        "Legumi": {"kg": 7.0, "scatole": 2},
                        "Pasta": {"kg": 10.0, "scatole": 1},
                        "Zucchero": {"kg": 10.0, "scatole": 1},
                        "Latte": {"kg": 8.0, "scatole": 2},
                        "Biscotti": {"kg": 4.0, "scatole": 2},
                    }
                ),
                note_o_anomalie="Scheda reale acquisita via scanner. Numero scheda non compilato (assegnato A1). Discrepanza compilatore: 2 scatole dichiarate su Biscotti ma solo 1 riga compilata (4.0 Kg)."
            )

        # Default fallback
        return RawCardExtraction(
            supermercato="Casa Circ. Capanne",
            numero_scheda="1",
            righe_scatole=[BoxItem(categoria="Pasta", kg=10.0)],
            totali_dichiarati=DeclaredTotals(kg_totale=10.0, scatole_totale=1)
        )
