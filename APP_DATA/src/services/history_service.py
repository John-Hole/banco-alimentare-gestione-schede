"""Servizio per l'archiviazione storica strutturata delle scansioni e dei dati convalidati.

Organizzazione gerarchica:
    SCANSIONI/
      <YYYY-MM-DD>/
        _da_assegnare/         (copie grezze appena acquisite prima del riconoscimento IA)
        _non_riconosciuto/     (copie grezze con supermercato non rilevato dall'IA)
        <Supermercato>/        (copie grezze spostate + immagine finale + _dati.json)
      indice_storico.json      (indice master interrogabile)
"""

import os
import re
import json
import shutil
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union

from ..config import STORICO_DIR, load_app_config
from ..models import ProcessedCard, RawCardExtraction
from .session_logger import get_session_logger


PENDING_RAW_DIR_NAME = "_da_assegnare"
UNRECOGNIZED_MARKET_DIR_NAME = "_non_riconosciuto"


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Rimuove caratteri non ammessi nei nomi di cartelle e file su Windows."""
    if not name:
        return "SENZA_NOME"
    # Rimuove caratteri vietati: \ / : * ? " < > |
    sanitized = re.sub(r'[\\/*?:"<>|]', '_', name).strip()
    # Rimuove spazi multipli
    sanitized = re.sub(r'\s+', ' ', sanitized)
    # Rimuove punti e spazi finali (non ammessi da Windows)
    sanitized = sanitized.rstrip('. ')
    if not sanitized:
        sanitized = "SENZA_NOME"
    return sanitized[:max_length]


class HistoryService:
    """Gestisce la cartella storico organizzata per Data -> Supermercato."""

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
        self.raw_scans_dir = self.get_pending_raw_dir(create=False)
        self.index_file = self.storico_dir / "indice_storico.json"
        self._init_index_file()

    @classmethod
    def get_instance(cls) -> "HistoryService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @staticmethod
    def _resolve_date_str(date_str: Optional[str] = None) -> str:
        """Restituisce una stringa data sicura YYYY-MM-DD."""
        if date_str:
            cleaned = str(date_str).strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
                return cleaned
            sanitized = sanitize_filename(cleaned, max_length=20)
            if sanitized and sanitized != "SENZA_NOME":
                return sanitized
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _unique_dest_path(target_dir: Path, filename: str) -> Path:
        """Genera un percorso univoco dentro target_dir evitando sovrascritture."""
        candidate = target_dir / filename
        if not candidate.exists():
            return candidate
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 2
        while True:
            cand = target_dir / f"{stem}_{counter}{suffix}"
            if not cand.exists():
                return cand
            counter += 1

    def get_day_dir(self, date_str: Optional[str] = None, create: bool = True) -> Path:
        """Restituisce SCANSIONI/<YYYY-MM-DD>/."""
        day_str = self._resolve_date_str(date_str)
        day_dir = self.storico_dir / day_str
        if create:
            day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir

    def get_pending_raw_dir(self, date_str: Optional[str] = None, create: bool = True) -> Path:
        """Restituisce SCANSIONI/<YYYY-MM-DD>/_da_assegnare/."""
        pending_dir = self.get_day_dir(date_str, create=create) / PENDING_RAW_DIR_NAME
        if create:
            pending_dir.mkdir(parents=True, exist_ok=True)
        return pending_dir

    def get_market_dir(self, sheet_name: str, date_str: Optional[str] = None, create: bool = True) -> Path:
        """Restituisce SCANSIONI/<YYYY-MM-DD>/<Supermercato>/."""
        clean_market = sanitize_filename(sheet_name or UNRECOGNIZED_MARKET_DIR_NAME)
        market_dir = self.get_day_dir(date_str, create=create) / clean_market
        if create:
            market_dir.mkdir(parents=True, exist_ok=True)
        return market_dir

    def _init_index_file(self):
        """Inizializza il file indice se non esiste."""
        if not self.index_file.exists():
            initial_data = {
                "versione_indice": "2.0",
                "descrizione": "Indice master schede Colletta Alimentare archiviate (Data/Supermercato) per benchmark e miglioramento IA",
                "data_creazione": datetime.now().isoformat(),
                "ultimo_aggiornamento": datetime.now().isoformat(),
                "totale_schede": 0,
                "totale_supermercati": 0,
                "totale_kg": 0.0,
                "totale_scatole": 0,
                "schede": [],
            }
            try:
                with open(self.index_file, "w", encoding="utf-8") as f:
                    json.dump(initial_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                get_session_logger().logger.error(f"[STORICO] Errore inizializzazione indice: {e}")

    def archive_raw_scan(
        self,
        image_path: Union[str, Path],
        prefix: str = "scanner",
        supermarket_name: Optional[str] = None,
        date_str: Optional[str] = None,
    ) -> Optional[Path]:
        """Salva una copia preventiva intatta della scansione in SCANSIONI/<YYYY-MM-DD>/...

        Se supermarket_name è noto subito, archivia in <Supermercato>/;
        altrimenti in _da_assegnare/ in attesa del riconoscimento IA.
        """
        src = Path(image_path)
        if not src.exists() or not src.is_file():
            return None

        day_str = self._resolve_date_str(date_str)
        if supermarket_name and str(supermarket_name).strip():
            target_dir = self.get_market_dir(str(supermarket_name).strip(), date_str=day_str, create=True)
        else:
            target_dir = self.get_pending_raw_dir(date_str=day_str, create=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = src.suffix.lower() if src.suffix else ".jpg"
        clean_prefix = sanitize_filename(prefix or "scanner", max_length=20)
        clean_stem = sanitize_filename(src.stem, max_length=60)
        dest_filename = f"{clean_prefix}_{timestamp}_{clean_stem}_grezza{ext}"
        dest_path = self._unique_dest_path(target_dir, dest_filename)

        try:
            shutil.copy2(str(src), str(dest_path))
            return dest_path
        except Exception as e:
            get_session_logger().logger.warning(f"[STORICO] Impossibile archiviare scansione grezza: {e}")
            return None

    def assign_raw_scan_to_supermarket(
        self,
        raw_archive_path: Optional[Union[str, Path]],
        supermarket_name: Optional[str],
        date_str: Optional[str] = None,
    ) -> Optional[Path]:
        """Sposta una copia grezza archiviata nella cartella del supermercato del giorno.

        Se il supermercato è vuoto/sconosciuto, sposta in SCANSIONI/<YYYY-MM-DD>/_non_riconosciuto/.
        È idempotente e thread-safe.
        """
        if not raw_archive_path:
            return None
        src = Path(raw_archive_path)

        with self._lock:
            if not src.exists() or not src.is_file():
                return None

            # Preserva la cartella giorno originale della scansione se riconosciuta
            inferred_day = None
            try:
                parent_day = src.parent.parent.name
                if re.match(r"^\d{4}-\d{2}-\d{2}$", parent_day):
                    inferred_day = parent_day
            except Exception:
                inferred_day = None

            day_str = self._resolve_date_str(date_str or inferred_day)
            raw_name = str(supermarket_name or "").strip()
            clean_market = sanitize_filename(raw_name) if raw_name else UNRECOGNIZED_MARKET_DIR_NAME

            day_dir = self.get_day_dir(day_str, create=True)
            target_dir = day_dir / clean_market
            target_dir.mkdir(parents=True, exist_ok=True)

            try:
                if src.parent.resolve() == target_dir.resolve():
                    return src
            except Exception:
                pass

            dest_path = self._unique_dest_path(target_dir, src.name)
            old_parent = src.parent

            try:
                shutil.move(str(src), str(dest_path))
            except Exception as e:
                get_session_logger().logger.warning(
                    f"[STORICO] Impossibile spostare scansione grezza '{src.name}' in '{clean_market}': {e}"
                )
                return src

            # Rimuovi eventuale vecchia cartella vuota (_da_assegnare / precedente supermercato)
            try:
                if old_parent.exists() and old_parent.is_dir():
                    if old_parent.parent.resolve() == day_dir.resolve() and old_parent.resolve() != target_dir.resolve():
                        if not any(old_parent.iterdir()):
                            old_parent.rmdir()
            except Exception:
                pass

            try:
                get_session_logger().log_archive_event(
                    "Scansione grezza assegnata",
                    details=f"'{dest_path.name}' -> '{day_str}/{clean_market}/'",
                )
            except Exception:
                pass

            return dest_path

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
        raw_archive_path: Optional[Union[str, Path]] = None,
        date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Archivia la scheda convalidata in SCANSIONI/<YYYY-MM-DD>/<Supermercato>/.

        Salva:
        1. Spostamento eventuale della copia grezza in <YYYY-MM-DD>/<Supermercato>/.
        2. Copia dell'immagine elaborata finale.
        3. File JSON strutturato (Ground Truth + dati IA).
        4. Aggiorna l'indice master 'indice_storico.json'.
        """
        logger = get_session_logger()
        inferred_day = None
        if not date_str and raw_archive_path:
            try:
                cand_day = Path(raw_archive_path).parent.parent.name
                if re.match(r"^\d{4}-\d{2}-\d{2}$", cand_day):
                    inferred_day = cand_day
            except Exception:
                inferred_day = None
        day_str = self._resolve_date_str(date_str or inferred_day)
        clean_market = sanitize_filename(sheet_name or "SUPERMERCATO_NON_SPECIFICATO")
        clean_transport = sanitize_filename(transport or "SENZA_TRASPORTO")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Cartella gerarchica Data -> Supermercato
        market_dir = self.get_market_dir(clean_market, date_str=day_str, create=True)

        # Assicura che anche la scansione grezza sia nella stessa cartella del supermercato confermato
        moved_raw_path: Optional[Path] = None
        if raw_archive_path:
            moved_raw_path = self.assign_raw_scan_to_supermarket(
                raw_archive_path,
                sheet_name or clean_market,
                date_str=day_str,
            )

        # Calcolo stem univoco per evitare collisioni nello stesso secondo
        base_name = f"{clean_transport}_{timestamp}"
        suffix_idx = 2
        while (market_dir / f"{base_name}_dati.json").exists():
            base_name = f"{clean_transport}_{timestamp}_{suffix_idx}"
            suffix_idx += 1

        id_scheda = f"{day_str}_{clean_market}_{base_name}"

        # 1. Copia dell'immagine scansionata elaborata
        dest_img_path = None
        rel_img_path = ""
        if image_path:
            src_img = Path(image_path)
            if src_img.exists() and src_img.is_file():
                ext = src_img.suffix.lower() if src_img.suffix else ".jpg"
                dest_img_name = f"{base_name}{ext}"
                dest_img_path = market_dir / dest_img_name
                if dest_img_path.exists():
                    dest_img_path = self._unique_dest_path(market_dir, dest_img_name)
                    dest_img_name = dest_img_path.name
                try:
                    shutil.copy2(str(src_img), str(dest_img_path))
                    rel_img_path = f"{day_str}/{clean_market}/{dest_img_name}"
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

        rel_market_folder = f"{day_str}/{clean_market}"
        dataset_record = {
            "id_scheda": id_scheda,
            "versione_schema": "2.0",
            "timestamp_archiviazione": datetime.now().isoformat(),
            "data_colletta": day_str,
            "data_sessione": day_str,
            "percorso_relativo": rel_market_folder,
            "session_id": session_id,
            "supermercato": {
                "confermato": sheet_name,
                "rilevato_ia": (
                    processed_card.supermercato_rilevato
                    if processed_card
                    else (raw_extraction.supermercato if raw_extraction else "")
                ),
            },
            "trasporto": {
                "confermato": transport,
                "rilevato_ia": (
                    processed_card.numero_scheda_rilevato
                    if processed_card
                    else (raw_extraction.numero_scheda if raw_extraction else "")
                ),
                "riga_excel": row_idx,
            },
            "file_excel": Path(excel_target).name if excel_target else "",
            "foglio_excel": sheet_name,
            "modello_ia": model_name,
            "preset_id_applicato": preset_id,
            "immagine_file": dest_img_path.name if dest_img_path else "",
            "immagine_path_relativo": rel_img_path,
            "immagine_grezza": moved_raw_path.name if moved_raw_path else "",
            "immagine_grezza_relativo": f"{rel_market_folder}/{moved_raw_path.name}" if moved_raw_path else "",
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
        rel_json_path = f"{rel_market_folder}/{dest_json_name}"

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
            "data": day_str,
            "supermercato": sheet_name,
            "trasporto": transport,
            "kg_totali": dataset_record["totali_confermati_excel"]["kg_totali"],
            "scatole_totali": dataset_record["totali_confermati_excel"]["scatole_totali"],
            "modello_ia": model_name,
            "modifiche_manuali": modifiche_manuali,
            "percorso_relativo": rel_market_folder,
            "cartella_supermercato": rel_market_folder,
            "file_json": rel_json_path,
            "file_immagine": rel_img_path,
            "file_grezza": dataset_record["immagine_grezza_relativo"],
        })

        logger.log_archive_event(
            "Scheda archiviata con successo",
            details=(
                f"ID: '{id_scheda}' | Cartella: '{rel_market_folder}' | "
                f"Immagine: '{dest_img_path.name if dest_img_path else 'No'}' | JSON: '{dest_json_name}'"
            ),
        )

        return {
            "success": True,
            "id_scheda": id_scheda,
            "date_str": day_str,
            "market_dir": str(market_dir),
            "dest_img_path": str(dest_img_path) if dest_img_path else None,
            "dest_json_path": str(dest_json_path),
            "raw_archive_path": str(moved_raw_path) if moved_raw_path else None,
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

    def open_history_folder(self, date_str: Optional[str] = None) -> bool:
        """Apre la cartella storico (del giorno specificato/odierno se esiste, altrimenti radice SCANSIONI)."""
        self.storico_dir.mkdir(parents=True, exist_ok=True)
        target_dir = self.storico_dir
        if date_str:
            target_dir = self.get_day_dir(date_str, create=True)
        else:
            today_dir = self.get_day_dir(create=False)
            if today_dir.exists():
                target_dir = today_dir

        try:
            if os.name == "nt":
                os.startfile(str(target_dir))
                return True
            else:
                subprocess.Popen(["xdg-open", str(target_dir)])
                return True
        except Exception:
            try:
                subprocess.run(["explorer", str(target_dir)])
                return True
            except Exception as e:
                get_session_logger().logger.error(f"[STORICO] Impossibile aprire cartella storico: {e}")
                return False


def get_history_service() -> HistoryService:
    """Restituisce l'istanza singleton del servizio di storico."""
    return HistoryService.get_instance()
