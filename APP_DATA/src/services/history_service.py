"""Servizio per l'archiviazione storica strutturata delle scansioni e dei dati convalidati.

Consente di conservare durante la Giornata della Colletta:
1. L'immagine ad alta risoluzione della scheda scansionata.
2. I dati grezzi estratti dall'IA.
3. I dati finali convalidati dall'operatore (Golden Ground Truth) scritti su Excel.
4. Un indice master interrogabile (indice_storico.json) per benchmark e miglioramenti futuri.
5. Copie grezze preventive di sicurezza di ogni foglio acquisito dallo scanner.
"""

import os
import re
import json
import shutil
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Union

from ..config import STORICO_DIR, load_app_config
from ..models import ProcessedCard, RawCardExtraction
from .session_logger import get_session_logger


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Rimuove caratteri non ammessi nei nomi di cartelle e file su Windows."""
    if not name:
        return "SENZA_NOME"
    # Rimuove caratteri vietati: \ / : * ? " < > |
    sanitized = re.sub(r'[\\/*?:"<>|]', '_', name).strip()
    # Rimuove spazi multipli e trattini consecutivi
    sanitized = re.sub(r'\s+', ' ', sanitized)
    # Rimuove punti e spazi finali (non ammessi da Windows)
    sanitized = sanitized.rstrip('. ')
    if not sanitized:
        sanitized = "SENZA_NOME"
    return sanitized[:max_length]


class HistoryService:
    """Gestisce la cartella di storico e il salvataggio dei dataset per i prossimi anni."""

    _instance: Optional["HistoryService"] = None
    _lock = threading.Lock()

    def __init__(self, custom_storico_dir: Optional[Path] = None):
        if custom_storico_dir:
            self.storico_dir = Path(custom_storico_dir)
        else:
            cfg = load_app_config()
            cfg_path = cfg.get("storico_folder")
            self.storico_dir = Path(cfg_path) if cfg_path else STORICO_DIR

        self.storico_dir.mkdir(parents=True, exist_ok=True)
        self.raw_scans_dir = self.storico_dir / "_scansioni_grezze"
        self.raw_scans_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.storico_dir / "indice_storico.json"
        self._init_index_file()

    @classmethod
    def get_instance(cls) -> "HistoryService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_index_file(self):
        """Inizializza il file indice se non esiste."""
        if not self.index_file.exists():
            initial_data = {
                "versione_indice": "1.0",
                "descrizione": "Indice master schede Colletta Alimentare archiviate per benchmark e miglioramento IA",
                "data_creazione": datetime.now().isoformat(),
                "ultimo_aggiornamento": datetime.now().isoformat(),
                "totale_schede": 0,
                "totale_supermercati": 0,
                "totale_kg": 0.0,
                "totale_scatole": 0,
                "schede": []
            }
            try:
                with open(self.index_file, "w", encoding="utf-8") as f:
                    json.dump(initial_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                get_session_logger().logger.error(f"[STORICO] Errore inizializzazione indice: {e}")

    def archive_raw_scan(self, image_path: Union[str, Path], prefix: str = "raw") -> Optional[Path]:
        """Salva una copia preventiva del foglio grezzo acquisito dallo scanner.
        
        Garantisce che nessuna scheda scansionata vada persa anche se la sessione
        viene interrotta prima della conferma dell'operatore.
        """
        src = Path(image_path)
        if not src.exists():
            return None

        today_str = datetime.now().strftime("%Y-%m-%d")
        daily_raw_dir = self.raw_scans_dir / today_str
        daily_raw_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_filename = f"{prefix}_{timestamp}_{src.name}"
        dest_path = daily_raw_dir / dest_filename

        try:
            shutil.copy2(str(src), str(dest_path))
            return dest_path
        except Exception as e:
            get_session_logger().logger.warning(f"[STORICO] Impossibile archiviare scansione grezza: {e}")
            return None

    def archive_card(
        self,
        image_path: Optional[Union[str, Path]],
        sheet_name: str,
        transport: str,
        processed_card: ProcessedCard,
        raw_extraction: Optional[RawCardExtraction] = None,
        model_name: str = "",
        excel_target: str = "",
        row_idx: int = 0,
        preset_id: int = 0,
        session_id: str = "",
    ) -> Dict[str, Any]:
        """Archivia la scheda convalidata nella cartella del supermercato.
        
        Salva:
        1. Copia dell'immagine scansionata in alta risoluzione.
        2. File JSON strutturato (Ground Truth + dati IA).
        3. Aggiorna l'indice master 'indice_storico.json'.
        """
        logger = get_session_logger()
        clean_market = sanitize_filename(sheet_name or "SUPERMERCATO_NON_SPECIFICATO")
        clean_transport = sanitize_filename(transport or "SENZA_TRASPORTO")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Cartella del supermercato
        market_dir = self.storico_dir / clean_market
        market_dir.mkdir(parents=True, exist_ok=True)

        base_name = f"{clean_transport}_{timestamp}"
        id_scheda = f"{clean_market}_{clean_transport}_{timestamp}"

        # 1. Copia dell'immagine scansionata
        dest_img_path = None
        rel_img_path = ""
        if image_path:
            src_img = Path(image_path)
            if src_img.exists():
                ext = src_img.suffix.lower() if src_img.suffix else ".jpg"
                dest_img_name = f"{base_name}{ext}"
                dest_img_path = market_dir / dest_img_name
                try:
                    shutil.copy2(str(src_img), str(dest_img_path))
                    rel_img_path = f"{clean_market}/{dest_img_name}"
                except Exception as e:
                    logger.logger.warning(f"[STORICO] Errore copia immagine nello storico: {e}")

        # 2. Rilevamento modifiche manuali rispetto all'estrazione grezza IA
        modifiche_manuali = False
        if raw_extraction and processed_card:
            raw_boxes = len(raw_extraction.righe_scatole)
            proc_boxes = len(processed_card.righe_scatole)
            raw_kg = sum(b.kg for b in raw_extraction.righe_scatole)
            proc_kg = processed_card.totale_kg_calcolati
            if raw_boxes != proc_boxes or abs(raw_kg - proc_kg) > 0.05:
                modifiche_manuali = True

        # 3. Costruzione del dataset Ground Truth JSON
        flags_data = []
        if processed_card and processed_card.flags:
            flags_data = [f.model_dump() for f in processed_card.flags]

        categories_summary = {}
        if processed_card and processed_card.categorie:
            for c_name, c_det in processed_card.categorie.items():
                categories_summary[c_name] = {
                    "kg_calcolati": c_det.kg_calcolati,
                    "scatole_calcolate": c_det.scatole_calcolate,
                    "kg_dichiarati": c_det.kg_dichiarati,
                    "scatole_dichiarate": c_det.scatole_dichiarate,
                    "ha_discrepanza": any(f.severity.value in ("WARNING", "RED") for f in c_det.flags),
                }

        totali_dichiarati_dump = {}
        if processed_card and processed_card.totali_dichiarati:
            totali_dichiarati_dump = processed_card.totali_dichiarati.model_dump()

        boxes_dump = []
        if processed_card and processed_card.righe_scatole:
            boxes_dump = [b.model_dump() for b in processed_card.righe_scatole]

        dataset_record = {
            "id_scheda": id_scheda,
            "versione_schema": "1.0",
            "timestamp_archiviazione": datetime.now().isoformat(),
            "data_colletta": datetime.now().strftime("%Y-%m-%d"),
            "session_id": session_id,
            "supermercato": {
                "confermato": sheet_name,
                "rilevato_ia": (processed_card.supermercato_rilevato if processed_card else (raw_extraction.supermercato if raw_extraction else "")),
            },
            "trasporto": {
                "confermato": transport,
                "rilevato_ia": (processed_card.numero_scheda_rilevato if processed_card else (raw_extraction.numero_scheda if raw_extraction else "")),
                "riga_excel": row_idx,
            },
            "file_excel": Path(excel_target).name if excel_target else "",
            "foglio_excel": sheet_name,
            "modello_ia": model_name,
            "preset_id_applicato": preset_id,
            "immagine_file": dest_img_path.name if dest_img_path else "",
            "immagine_path_relativo": rel_img_path,
            "modifiche_manuali_operatore": modifiche_manuali,
            "ha_flag_rossi": processed_card.ha_flag_rossi if processed_card else False,
            "flags_validazione": flags_data,
            "totali_confermati_excel": {
                "kg_totali": processed_card.totale_kg_calcolati if processed_card else 0.0,
                "scatole_totali": processed_card.totale_scatole_calcolate if processed_card else 0,
                "totali_dichiarati": totali_dichiarati_dump,
                "categorie": categories_summary,
                "scatole_dettaglio": boxes_dump,
            },
            "dati_grezzi_ia": raw_extraction.model_dump() if raw_extraction else None,
        }

        # 4. Salvataggio del file JSON
        dest_json_name = f"{base_name}_dati.json"
        dest_json_path = market_dir / dest_json_name
        rel_json_path = f"{clean_market}/{dest_json_name}"

        try:
            with open(dest_json_path, "w", encoding="utf-8") as f:
                json.dump(dataset_record, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.logger.error(f"[STORICO] Errore scrittura JSON storico '{dest_json_path}': {e}")
            raise

        # 5. Aggiornamento atomico dell'indice master
        self._append_to_index({
            "id_scheda": id_scheda,
            "timestamp": dataset_record["timestamp_archiviazione"],
            "data": dataset_record["data_colletta"],
            "supermercato": sheet_name,
            "trasporto": transport,
            "kg_totali": dataset_record["totali_confermati_excel"]["kg_totali"],
            "scatole_totali": dataset_record["totali_confermati_excel"]["scatole_totali"],
            "modello_ia": model_name,
            "modifiche_manuali": modifiche_manuali,
            "cartella_supermercato": clean_market,
            "file_json": rel_json_path,
            "file_immagine": rel_img_path,
        })

        logger.log_archive_event(
            "Scheda archiviata con successo",
            details=f"ID: '{id_scheda}' | Cartella: '{clean_market}' | Immagine: '{dest_img_path.name if dest_img_path else 'No'}' | JSON: '{dest_json_name}'"
        )

        return {
            "success": True,
            "id_scheda": id_scheda,
            "market_dir": str(market_dir),
            "dest_img_path": str(dest_img_path) if dest_img_path else None,
            "dest_json_path": str(dest_json_path),
            "clean_market": clean_market,
        }

    def _append_to_index(self, record: Dict[str, Any]):
        """Aggiunge in modo thread-safe un nuovo elemento all'indice generale."""
        with self._lock:
            try:
                if self.index_file.exists():
                    with open(self.index_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    data = {"schede": []}

                schede = data.get("schede", [])
                schede.append(record)

                # Ricalcolo totali statistici
                unique_supermarkets = {s.get("supermercato") for s in schede if s.get("supermercato")}
                tot_kg = sum(float(s.get("kg_totali", 0.0)) for s in schede)
                tot_scatole = sum(int(s.get("scatole_totali", 0)) for s in schede)

                data["schede"] = schede
                data["totale_schede"] = len(schede)
                data["totale_supermercati"] = len(unique_supermarkets)
                data["totale_kg"] = round(tot_kg, 2)
                data["totale_scatole"] = tot_scatole
                data["ultimo_aggiornamento"] = datetime.now().isoformat()

                with open(self.index_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                get_session_logger().logger.error(f"[STORICO] Errore aggiornamento indice_storico.json: {e}")

    def get_history_stats(self) -> Dict[str, Any]:
        """Restituisce le statistiche attuali delle schede archiviate."""
        with self._lock:
            if not self.index_file.exists():
                return {"totale_schede": 0, "totale_supermercati": 0, "totale_kg": 0.0, "totale_scatole": 0}
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {
                    "totale_schede": data.get("totale_schede", 0),
                    "totale_supermercati": data.get("totale_supermercati", 0),
                    "totale_kg": data.get("totale_kg", 0.0),
                    "totale_scatole": data.get("totale_scatole", 0),
                    "ultimo_aggiornamento": data.get("ultimo_aggiornamento", ""),
                }
            except Exception:
                return {"totale_schede": 0, "totale_supermercati": 0, "totale_kg": 0.0, "totale_scatole": 0}

    def open_history_folder(self) -> bool:
        """Apre la cartella storico_scansioni in Esplora Risorse di Windows."""
        self.storico_dir.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(self.storico_dir))
                return True
            else:
                subprocess.Popen(["xdg-open", str(self.storico_dir)])
                return True
        except Exception:
            try:
                subprocess.run(["explorer", str(self.storico_dir)])
                return True
            except Exception as e:
                get_session_logger().logger.error(f"[STORICO] Impossibile aprire cartella storico: {e}")
                return False


def get_history_service() -> HistoryService:
    """Restituisce l'istanza singleton del servizio di storico."""
    return HistoryService.get_instance()
