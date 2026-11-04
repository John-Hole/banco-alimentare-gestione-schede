"""Servizio per la gestione e indicizzazione dei file Excel della Colletta Alimentare.

Gestisce una cartella dati selezionabile dall'utente (di default 'fogli_colletta_2025' se presente),
la scansione in tempo reale dei file Excel e dei fogli supermercato e il matching intelligente
per suggerire il file e foglio corretto.
"""

import os
import json
import re
import difflib
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any

from ..config import BASE_DIR, ROOT_DIR, LEGACY_COLLETTA_DIR, load_app_config

# Mantenuto per retrocompatibilità: cartella storica di default
COLLETTA_DIR = LEGACY_COLLETTA_DIR
INDEX_FILE = BASE_DIR / "colletta_index.json"

EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}

NON_SUPERMARKET_SHEETS = {
    "enti", "note", "volontari", "distribuzione", 
    "schema distribuzione colletta", "prospetto distribuzione", 
    "divisione distribuzione", "file per la distribuzione", 
    "prospetto per distribuzione", "foglio1", "totale", "appoggio totale"
}


class _ExcelSheetReader:
    """Legge i nomi dei fogli dei file Excel (.xlsx via openpyxl, .xls via xlrd o Excel COM).

    Per i file .xls, in assenza di xlrd, avvia UNA SOLA istanza nascosta e separata di Excel
    (non interferisce con l'istanza dell'utente) riutilizzata per tutti i file della scansione.
    """

    def __init__(self):
        self._excel = None
        self._com_inited = False
        self._com_failed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._excel is not None:
            try:
                self._excel.Quit()
            except Exception:
                pass
            self._excel = None
        if self._com_inited:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
            self._com_inited = False

    def sheet_names(self, path: Path) -> List[str]:
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(str(path), read_only=True, data_only=True)
            try:
                return list(wb.sheetnames)
            finally:
                wb.close()

        # .xls (formato binario legacy)
        try:
            import xlrd  # type: ignore
            book = xlrd.open_workbook(str(path), on_demand=True)
            try:
                return list(book.sheet_names())
            finally:
                book.release_resources()
        except ImportError:
            pass
        return self._sheet_names_via_com(path)

    def _sheet_names_via_com(self, path: Path) -> List[str]:
        if self._com_failed:
            raise RuntimeError("Lettura file .xls non disponibile (Excel non installato).")
        try:
            if self._excel is None:
                import pythoncom
                import win32com.client
                pythoncom.CoInitialize()
                self._com_inited = True
                self._excel = win32com.client.DispatchEx("Excel.Application")
                self._excel.Visible = False
                self._excel.DisplayAlerts = False
            wb = self._excel.Workbooks.Open(str(path), UpdateLinks=0, ReadOnly=True)
            try:
                return [wb.Worksheets(i).Name for i in range(1, wb.Worksheets.Count + 1)]
            finally:
                wb.Close(False)
        except Exception as e:
            if self._excel is None:
                self._com_failed = True
            raise RuntimeError(f"Impossibile leggere {path.name}: {e}")


class CollettaService:
    """Gestisce la scoperta dei file Excel della Colletta, la lista dei fogli e il matching."""

    _instance: Optional["CollettaService"] = None

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            folder = str(load_app_config().get("last_colletta_folder") or "").strip()
            if folder and Path(folder).is_dir():
                data_dir = Path(folder)
            elif LEGACY_COLLETTA_DIR.is_dir():
                data_dir = LEGACY_COLLETTA_DIR
        self.data_dir: Optional[Path] = Path(data_dir) if data_dir else None
        self.index_data: Dict[str, Any] = {"files": {}, "supermarkets": []}
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._indexed_dir: Optional[Path] = None
        self.load_or_build_index()

    @classmethod
    def get_instance(cls) -> "CollettaService":
        if cls._instance is None:
            cls._instance = CollettaService()
        return cls._instance

    # ------------------------------------------------------------------ indice

    def load_or_build_index(self, force_rebuild: bool = False):
        """Carica l'indice da disco se disponibile, altrimenti lo carica vuoto o lo rigenera."""
        if not force_rebuild and INDEX_FILE.exists():
            try:
                with open(INDEX_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.index_data = {
                    "files": data.get("files", {}),
                    "supermarkets": data.get("supermarkets", []),
                }
                self._cache = data.get("cache", {}) or {}
                self._indexed_dir = self._infer_indexed_dir(data)
                if self._indexed_dir and self._indexed_dir.is_dir():
                    for fname, finfo in self.index_data["files"].items():
                        candidate = self._indexed_dir / fname
                        if candidate.exists():
                            finfo["file_path"] = str(candidate)
                    for sm in self.index_data["supermarkets"]:
                        fname = sm.get("file_name", "")
                        if fname:
                            candidate = self._indexed_dir / fname
                            if candidate.exists():
                                sm["file_path"] = str(candidate)
                return
            except Exception as e:
                print(f"Errore caricamento {INDEX_FILE}: {e}")

        # Se non esiste, indicizza i file trovati nella cartella
        self.index_data = {"files": {}, "supermarkets": []}
        self._indexed_dir = self.data_dir
        if self.data_dir and self.data_dir.exists():
            self._scan_folder_basic()

    @staticmethod
    def _infer_indexed_dir(data: Dict[str, Any]) -> Optional[Path]:
        """Ricava la cartella a cui si riferisce l'indice salvato (supporta percorsi relativi e il formato legacy)."""
        legacy_names = {LEGACY_COLLETTA_DIR.name.lower(), "fogli_colletta_2025", "fogli_colletta"}
        raw_dir = data.get("data_dir")
        if raw_dir:
            p = Path(raw_dir)
            if not p.is_absolute():
                for base in (ROOT_DIR, BASE_DIR):
                    cand = base / p
                    if cand.exists():
                        return cand
                p = ROOT_DIR / p
            if not p.exists() and p.name.lower() in legacy_names and LEGACY_COLLETTA_DIR.exists():
                p = LEGACY_COLLETTA_DIR
            return p
        for info in data.get("files", {}).values():
            if info.get("file_path"):
                fp = Path(info["file_path"])
                p = fp.parent if fp.is_absolute() else (ROOT_DIR / fp.parent)
                if not p.exists() and not fp.is_absolute():
                    p = BASE_DIR / fp.parent
                if not p.exists() and p.name.lower() in legacy_names and LEGACY_COLLETTA_DIR.exists():
                    p = LEGACY_COLLETTA_DIR
                return p
        return None

    @staticmethod
    def _path_key(p: Path) -> str:
        return os.path.normcase(os.path.abspath(str(p)))

    def needs_rescan(self) -> bool:
        """True se l'indice caricato non corrisponde alla cartella dati attiva."""
        if not self.data_dir or not self.data_dir.exists():
            return False
        if self._indexed_dir is None:
            return True
        return self._path_key(self._indexed_dir) != self._path_key(self.data_dir)

    @staticmethod
    def _to_portable_path(p_str: str) -> str:
        if not p_str:
            return ""
        resolved = Path(p_str).resolve()
        for base in (ROOT_DIR, BASE_DIR):
            try:
                rel = resolved.relative_to(base.resolve())
                return rel.as_posix()
            except Exception:
                pass
        return str(p_str)

    @classmethod
    def _cache_key(cls, p: Path) -> str:
        portable = cls._to_portable_path(str(p))
        if portable and not Path(portable).is_absolute():
            return portable.lower()
        return cls._path_key(p)

    def _save_index(self):
        try:
            rel_dir = self._to_portable_path(str(self.data_dir)) if self.data_dir else ""
            portable_files = {}
            for k, v in (self.index_data.get("files", {}) or {}).items():
                item = dict(v)
                if "file_path" in item:
                    item["file_path"] = self._to_portable_path(str(item["file_path"]))
                portable_files[k] = item
            portable_supermarkets = []
            for s in (self.index_data.get("supermarkets", []) or []):
                item = dict(s)
                if "file_path" in item:
                    item["file_path"] = self._to_portable_path(str(item["file_path"]))
                portable_supermarkets.append(item)
            portable_cache = {
                self._cache_key(Path(ck)): cv
                for ck, cv in (self._cache or {}).items()
            }
            payload = {
                "data_dir": rel_dir,
                "files": portable_files,
                "supermarkets": portable_supermarkets,
                "cache": portable_cache,
            }
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1, ensure_ascii=False)
        except Exception as e:
            print(f"Errore salvataggio {INDEX_FILE}: {e}")

    @staticmethod
    def _zone_from_filename(fname: str) -> str:
        stem = Path(fname).stem
        zone = re.sub(r"^\s*colletta\s*\d{4}\s*", "", stem, flags=re.IGNORECASE).strip()
        return zone or stem

    def _iter_excel_files(self, folder: Optional[Path] = None) -> List[Path]:
        folder = folder or self.data_dir
        if not folder or not Path(folder).is_dir():
            return []
        try:
            return sorted(
                p for p in Path(folder).iterdir()
                if p.is_file() and p.suffix.lower() in EXCEL_EXTENSIONS and not p.name.startswith("~$")
            )
        except OSError:
            return []

    def _scan_folder_basic(self):
        """Scansione leggera basata sui file presenti nella directory (senza aprire i file)."""
        for p in self._iter_excel_files():
            fname = p.name
            self.index_data["files"][fname] = {
                "file_name": fname,
                "file_path": str(p),
                "zone": self._zone_from_filename(fname),
                "total_sheets": 0,
                "supermarket_sheets": [],
                "sheets": []
            }

    def set_data_dir(self, folder: Optional[str]):
        """Imposta la cartella dati attiva senza eseguire la scansione dei fogli."""
        self.data_dir = Path(folder) if folder else None

    def scan_folder(
        self,
        folder: Optional[str] = None,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """Scansiona la cartella indicata (o quella attiva) rilevando tutti i .xlsx/.xls e i loro fogli.

        Adatto ad essere eseguito su un thread secondario. Non solleva eccezioni per file illeggibili:
        li riporta nel campo 'errors' del riepilogo restituito.
        """
        target = Path(folder) if folder else self.data_dir
        if not target or not target.is_dir():
            raise NotADirectoryError(f"Cartella non valida: {target}")

        legacy_addr = {
            (s.get("file_name", "").lower(), s.get("sheet_name", "").lower()): s.get("full_address", "")
            for s in self.index_data.get("supermarkets", [])
        }
        legacy_files = {k.lower(): v for k, v in self.index_data.get("files", {}).items()}

        paths = self._iter_excel_files(target)
        files: Dict[str, Any] = {}
        supermarkets: List[Dict[str, Any]] = []
        errors: List[str] = []
        new_cache = dict(self._cache)

        with _ExcelSheetReader() as reader:
            for idx, p in enumerate(paths):
                if progress_cb:
                    try:
                        progress_cb(idx, len(paths), p.name)
                    except Exception:
                        pass
                try:
                    st = p.stat()
                    stamp = {"mtime": int(st.st_mtime), "size": st.st_size}
                except OSError as e:
                    errors.append(f"{p.name}: {e}")
                    continue

                key = self._cache_key(p)
                cached = new_cache.get(key) or new_cache.get(self._path_key(p))
                sheet_names: Optional[List[str]] = None
                if cached and cached.get("mtime") == stamp["mtime"] and cached.get("size") == stamp["size"]:
                    sheet_names = cached.get("sheet_names")

                if sheet_names is None:
                    legacy = legacy_files.get(p.name.lower())
                    leg_fp = Path(legacy.get("file_path", "")) if legacy and legacy.get("file_path") else None
                    if leg_fp and not leg_fp.is_absolute():
                        cand_fp = ROOT_DIR / leg_fp
                        leg_fp = cand_fp if cand_fp.exists() else (BASE_DIR / leg_fp)
                    same_dir = bool(
                        leg_fp and (
                            leg_fp.resolve().parent == p.resolve().parent
                            or (p.resolve().parent == LEGACY_COLLETTA_DIR.resolve() and leg_fp.parent.name.lower() in ("fogli_colletta_2025", "fogli_colletta"))
                        )
                    )
                    if legacy and same_dir and legacy.get("sheets"):
                        sheet_names = [s["name"] for s in legacy["sheets"]]
                    else:
                        try:
                            sheet_names = reader.sheet_names(p)
                            new_cache[key] = {**stamp, "sheet_names": sheet_names}
                        except Exception as e:
                            errors.append(f"{p.name}: {e}")
                            sheet_names = []

                market_sheets = [
                    n for n in sheet_names
                    if n.strip().lower() not in NON_SUPERMARKET_SHEETS
                ]
                zone = self._zone_from_filename(p.name)
                files[p.name] = {
                    "file_name": p.name,
                    "file_path": str(p),
                    "zone": zone,
                    "total_sheets": len(sheet_names),
                    "supermarket_sheets": market_sheets,
                    "sheets": [
                        {
                            "name": n,
                            "address": legacy_addr.get((p.name.lower(), n.lower()), ""),
                            "is_data_sheet": n in market_sheets,
                            "is_supermarket": n in market_sheets,
                            "is_totale": n.strip().lower() in ("totale", "appoggio totale"),
                        }
                        for n in sheet_names
                    ],
                }
                for n in market_sheets:
                    supermarkets.append({
                        "file_name": p.name,
                        "file_path": str(p),
                        "zone": zone,
                        "sheet_name": n,
                        "full_address": legacy_addr.get((p.name.lower(), n.lower()), ""),
                    })

        self.data_dir = target
        self._indexed_dir = target
        self._cache = new_cache
        self.index_data = {"files": files, "supermarkets": supermarkets}
        self._save_index()
        return {
            "folder": str(target),
            "files": len(files),
            "supermarkets": len(supermarkets),
            "errors": errors,
        }

    # ------------------------------------------------------------------ query

    def list_available_files(self) -> List[Dict[str, Any]]:
        """Restituisce l'elenco ordinato di tutti i file Excel della Colletta disponibili."""
        files = []
        disk_files = {p.name: p for p in self._iter_excel_files()}

        for fname, fpath in disk_files.items():
            info = self.index_data.get("files", {}).get(fname, {})
            zone = info.get("zone") or self._zone_from_filename(fname)
            supermarkets_count = len(info.get("supermarket_sheets", []))
            
            display_label = f"{zone} ({fname})"
            if supermarkets_count > 0:
                display_label = f"{zone} — {supermarkets_count} supermercati"
                
            files.append({
                "file_name": fname,
                "file_path": str(fpath),
                "zone": zone,
                "display_label": display_label,
                "supermarkets_count": supermarkets_count,
                "supermarkets": info.get("supermarket_sheets", [])
            })
            
        # Ordina per zona
        files.sort(key=lambda x: x["zone"])
        # Etichette duplicate (es. stessa zona in .xls e .xlsx): disambigua con il nome file
        seen: Dict[str, int] = {}
        for f in files:
            seen[f["display_label"]] = seen.get(f["display_label"], 0) + 1
        for f in files:
            if seen[f["display_label"]] > 1:
                f["display_label"] = f"{f['display_label']} [{f['file_name']}]"
        return files

    def get_supermarkets_for_file(self, filename_or_path: str) -> List[str]:
        """Restituisce l'elenco dei soli fogli supermercato (esclusi Note, Totale, ecc.) per un dato file."""
        fname = Path(filename_or_path).name
        file_info = self.index_data.get("files", {}).get(fname)
        if file_info and file_info.get("supermarket_sheets"):
            return file_info["supermarket_sheets"]
        return []

    @staticmethod
    def normalize_text(s: str) -> str:
        """Normalizza una stringa per confronto OCR tollerante."""
        if not s:
            return ""
        s = s.lower().strip()
        # Abbreviazioni e toponimi comuni
        s = s.replace("circ.", "circondariale").replace("circ ", "circondariale ")
        s = s.replace("pontedoddi", "ponte d oddi")
        s = s.replace("c.c. ", " ").replace("c.c.", " ")
        s = s.replace("s.m.angeli", "santa maria degli angeli")
        s = s.replace("s.m.", "santa maria ")
        s = s.replace("s. ", "san ")
        # Rimozione parole di arresto e caratteri speciali
        s = re.sub(r"\b(via|strada|viale|piazza|loc|loc\.|corso|citta|città|supermercato|superstore|ipermercato|snc|punto|vendita)\b", " ", s)
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    def find_matches(self, query: str, current_file: Optional[str] = None, top_n: int = 3) -> List[Dict[str, Any]]:
        """Cerca il supermercato/foglio corrispondente in tutti i file o nel file corrente.
        
        Restituisce una lista ordinata di corrispondenze con punteggio e dettagli.
        """
        if not query or not self.index_data.get("supermarkets"):
            return []

        norm_q = self.normalize_text(query)
        if not norm_q:
            return []
            
        tokens_q = set(norm_q.split())
        current_fname = Path(current_file).name if current_file else None

        results = []
        for item in self.index_data["supermarkets"]:
            target_name = item["sheet_name"]
            target_addr = item.get("full_address", "")
            target_zone = item.get("zone", "")
            file_name = item["file_name"]
            
            norm_name = self.normalize_text(target_name)
            norm_addr = self.normalize_text(target_addr)
            full_text = f"{target_name} {target_zone} {target_addr}"
            norm_full = self.normalize_text(full_text)
            tokens_full = set(norm_full.split())
            
            score = 0.0
            
            # Corrispondenza perfetta sul nome
            if norm_q == norm_name or norm_q == self.normalize_text(f"{target_name} {target_zone}"):
                score = 1.0
            elif norm_q in norm_full or norm_full in norm_q:
                score = 0.95
            else:
                # Sovrapposizione token con peso
                common = tokens_q.intersection(tokens_full)
                if common:
                    recall = len(common) / len(tokens_q)
                    prec = len(common) / len(tokens_full)
                    score = recall * 0.7 + prec * 0.3
                    
                ratio_name = difflib.SequenceMatcher(None, norm_q, norm_name).ratio()
                ratio_addr = difflib.SequenceMatcher(None, norm_q, norm_addr).ratio() if norm_addr else 0
                ratio_full = difflib.SequenceMatcher(None, norm_q, norm_full).ratio()
                score = max(score, ratio_name, ratio_addr * 0.85, ratio_full * 0.9)
                
            # Bonus se appartiene al file attualmente selezionato dall'utente
            is_current_file = bool(current_fname and file_name.lower() == current_fname.lower())
            if is_current_file:
                score = min(1.0, score + 0.08)
                
            if score >= 0.40:
                results.append({
                    "score": round(score, 3),
                    "file_name": file_name,
                    "file_path": item["file_path"],
                    "zone": target_zone,
                    "sheet_name": target_name,
                    "full_address": target_addr,
                    "is_current_file": is_current_file
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_n]
