"""Modulo di automazione COM per Microsoft Excel con protezione anti-crash e tolleranza OCR.

Supporta sia il layout reale della Colletta Alimentare 2025 (righe 5..14, col C..AB)
sia il layout master legacy (righe 6..15, col B..AA).
"""

import os
import difflib
import re
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

from ..config import (
    CATEGORIES,
    TRANSPORT_ROWS,
    PROTECTED_ROWS,
    FORMULA_ROWS,
    DEFAULT_EXCEL_FILENAME,
)
from ..models import ProcessedCard
from .backup_service import BackupService


class ExcelNotRunningError(Exception):
    """Sollevata quando Microsoft Excel non è in esecuzione."""
    pass


class ExcelWorkbookNotOpenError(Exception):
    """Sollevata quando il file gestionale specificato non è aperto in Excel."""
    pass


class ExcelManager:
    """Gestisce la connessione all'istanza di Excel, l'apertura file e le operazioni di I/O sicure."""

    # Mappatura per il formato REALE della Colletta 2025
    REAL_COLLETTA_ROWS = {
        "A1": 5, "A2": 6, "A3": 7, "A4": 8, "A5": 9,
        "A6": 10, "A7": 11, "A8": 12, "A9": 13, "A10": 14
    }
    REAL_PROTECTED_ROWS = {1, 2, 3, 4, 15, 16, 17, 18, 19, 20, 21}
    REAL_FORMULA_ROWS = {16}

    # Categorie nel formato reale (sfasate di +1 colonna: C=3, D=4 ...)
    REAL_CATEGORIES = [
        {"name": "Omogeneizzati", "kg_col": 3, "box_col": 4, "kg_letter": "C", "box_letter": "D"},
        {"name": "Alimenti per bambini", "kg_col": 5, "box_col": 6, "kg_letter": "E", "box_letter": "F"},
        {"name": "Tonno", "kg_col": 7, "box_col": 8, "kg_letter": "G", "box_letter": "H"},
        {"name": "Olio", "kg_col": 9, "box_col": 10, "kg_letter": "I", "box_letter": "J"},
        {"name": "Pelati", "kg_col": 11, "box_col": 12, "kg_letter": "K", "box_letter": "L"},
        {"name": "Legumi", "kg_col": 13, "box_col": 14, "kg_letter": "M", "box_letter": "N"},
        {"name": "Pasta", "kg_col": 15, "box_col": 16, "kg_letter": "O", "box_letter": "P"},
        {"name": "Riso", "kg_col": 17, "box_col": 18, "kg_letter": "Q", "box_letter": "R"},
        {"name": "Zucchero", "kg_col": 19, "box_col": 20, "kg_letter": "S", "box_letter": "T"},
        {"name": "Latte", "kg_col": 21, "box_col": 22, "kg_letter": "U", "box_letter": "V"},
        {"name": "Biscotti", "kg_col": 23, "box_col": 24, "kg_letter": "W", "box_letter": "X"},
        {"name": "Carne in scatola", "kg_col": 25, "box_col": 26, "kg_letter": "Y", "box_letter": "Z"},
        {"name": "Varie", "kg_col": 27, "box_col": 28, "kg_letter": "AA", "box_letter": "AB"},
    ]

    def __init__(self, target_filepath_or_name: Optional[str] = None):
        self.target_path: Optional[str] = str(target_filepath_or_name) if target_filepath_or_name else None
        self.excel_app = None
        self.workbook = None
        self._cached_sheet_names: Optional[List[str]] = None
        self._sheet_status_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def clear_cache(self):
        """Invalida la cache dei nomi dei fogli e degli stati dei fogli."""
        self._cached_sheet_names = None
        self._sheet_status_cache.clear()

    def set_target_file(self, target_filepath_or_name: str):
        """Imposta il file Excel di destinazione corrente."""
        self.target_path = str(target_filepath_or_name)
        self.clear_cache()
        # Se siamo già connessi ma a un file diverso, resettiamo il puntatore al workbook
        if self.workbook:
            wb_name = ""
            try:
                wb_name = self.workbook.Name.lower()
            except Exception:
                pass
            target_name = Path(self.target_path).name.lower()
            if target_name not in wb_name and wb_name not in target_name:
                self.workbook = None

    def get_target_filename(self) -> str:
        """Restituisce il nome file pulito del target."""
        if self.target_path:
            return Path(self.target_path).name
        return DEFAULT_EXCEL_FILENAME

    def connect(self) -> Tuple[Any, Any]:
        """Tenta la connessione all'istanza di Excel già aperta e al file corretto."""
        try:
            import win32com.client
            self.excel_app = win32com.client.GetActiveObject("Excel.Application")
        except Exception as e:
            self.excel_app = None
            self.workbook = None
            raise ExcelNotRunningError("Microsoft Excel non è in esecuzione. Aprire Excel e riprovare.") from e

        try:
            workbooks_count = self.excel_app.Workbooks.Count
            if workbooks_count == 0:
                self.workbook = None
                raise ExcelWorkbookNotOpenError("Nessun file aperto in Excel. Aprire il gestionale in Excel e riprovare.")

            target_filename = self.get_target_filename()
            clean_target = target_filename.lower().replace(".xlsx", "").replace(".xls", "").strip()

            found_wb = None
            for i in range(1, workbooks_count + 1):
                wb = self.excel_app.Workbooks(i)
                wb_name = wb.Name.lower()
                if clean_target in wb_name or wb_name in clean_target:
                    found_wb = wb
                    break

            if found_wb is None:
                if workbooks_count == 1 and not self.target_path:
                    found_wb = self.excel_app.ActiveWorkbook
                else:
                    raise ExcelWorkbookNotOpenError(
                        f"Il file '{target_filename}' non è tra i file aperti in Excel. "
                        "Aprire il file in Excel e riprovare."
                    )

            self.workbook = found_wb
            self.clear_cache()
            return self.excel_app, self.workbook

        except (ExcelNotRunningError, ExcelWorkbookNotOpenError):
            raise
        except Exception as e:
            self.excel_app = None
            self.workbook = None
            self.clear_cache()
            raise ExcelNotRunningError(f"Errore di comunicazione con Excel: {e}") from e

    def is_connected(self) -> bool:
        """Verifica rapidamente se la connessione è ancora valida e il workbook corretto è aperto."""
        try:
            if self.excel_app and self.workbook:
                target_name = self.get_target_filename().lower().replace(".xlsx", "").replace(".xls", "").strip()
                wb_name = self.workbook.Name.lower()
                if target_name in wb_name or wb_name in target_name or not self.target_path:
                    return True
        except Exception:
            pass
        self.workbook = None
        return False

    def open_target_file(self) -> bool:
        """Apre il file target in Excel (avviando Excel se non è ancora in esecuzione)."""
        if not self.target_path or not os.path.exists(self.target_path):
            return False

        import win32com.client
        abs_path = os.path.abspath(self.target_path)

        try:
            self.excel_app = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            self.excel_app = win32com.client.Dispatch("Excel.Application")

        self.excel_app.Visible = True

        target_name = Path(self.target_path).name.lower()
        for i in range(1, self.excel_app.Workbooks.Count + 1):
            wb = self.excel_app.Workbooks(i)
            if wb.Name.lower() == target_name:
                self.workbook = wb
                wb.Activate()
                self.clear_cache()
                return True

        self.workbook = self.excel_app.Workbooks.Open(abs_path)
        self.workbook.Activate()
        self.clear_cache()
        return True

    def get_sheet_names(self, force_refresh: bool = False) -> List[str]:
        """Restituisce l'elenco dei nomi di tutti i fogli del workbook aperto (con caching in-memory)."""
        if not force_refresh and self._cached_sheet_names is not None:
            return list(self._cached_sheet_names)

        if not self.is_connected():
            self.connect()
        names = []
        for i in range(1, self.workbook.Sheets.Count + 1):
            sheet = self.workbook.Sheets(i)
            names.append(sheet.Name)
        self._cached_sheet_names = list(names)
        return list(names)

    def get_data_sheet_names(self, force_refresh: bool = False) -> List[str]:
        """Restituisce solo i fogli supermercato/dati, escludendo fogli di servizio."""
        all_sheets = self.get_sheet_names(force_refresh=force_refresh)
        non_data = {
            "enti", "note", "volontari", "distribuzione", 
            "schema distribuzione colletta", "prospetto distribuzione", 
            "divisione distribuzione", "file per la distribuzione", 
            "prospetto per distribuzione", "foglio1", "totale", "appoggio totale"
        }
        return [s for s in all_sheets if s.lower().strip() not in non_data]

    def detect_sheet_layout(self, sheet) -> Dict[str, Any]:
        """Rileva automaticamente se il foglio segue il formato reale della Colletta 2025 o il formato master."""
        try:
            val_r4_c1 = str(sheet.Cells(4, 1).Value or "").strip().upper()
            val_r3_c3 = str(sheet.Cells(3, 3).Value or "").strip().upper()
            
            if "TRASP" in val_r4_c1 or "OMOGENEIZZATI" in val_r3_c3:
                return {
                    "is_real_format": True,
                    "transport_rows": self.REAL_COLLETTA_ROWS,
                    "categories": self.REAL_CATEGORIES,
                    "protected_rows": self.REAL_PROTECTED_ROWS,
                    "formula_rows": self.REAL_FORMULA_ROWS,
                    "first_col": 3,
                    "last_col": 28
                }
        except Exception:
            pass

        return {
            "is_real_format": False,
            "transport_rows": TRANSPORT_ROWS,
            "categories": CATEGORIES,
            "protected_rows": PROTECTED_ROWS,
            "formula_rows": FORMULA_ROWS,
            "first_col": 2,
            "last_col": 27
        }

    @staticmethod
    def _normalize_string(s: str) -> str:
        """Normalizza una stringa rimuovendo accenti, punteggiatura, toponomastica e spazi multipli."""
        if not s:
            return ""
        s = s.lower().strip()
        s = s.replace("pontedoddi", "ponte d oddi")
        s = s.replace("otto", "8").replace("uno", "1").replace("due", "2")
        s = s.replace("c.c. ", " ").replace("circ. ", "circondariale ")
        s = re.sub(r"\b(via|strada|viale|piazza|loc|loc\.|corso|citta|città|supermercato|superstore|ipermercato|snc)\b", " ", s)
        s = re.sub(r"[^\w\s]", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip()

    def find_matching_sheet(self, supermarket_name: str) -> Tuple[Optional[str], float]:
        """Cerca il foglio con il nome del supermercato con matching tollerante OCR."""
        if not supermarket_name:
            return None, 0.0

        sheets = self.get_sheet_names()
        if not sheets:
            return None, 0.0

        clean_query = supermarket_name.strip().lower()
        for sheet in sheets:
            if sheet.strip().lower() == clean_query:
                return sheet, 1.0

        norm_query = self._normalize_string(supermarket_name)
        tokens_query = set(norm_query.split())

        for sheet in sheets:
            norm_sheet = self._normalize_string(sheet)
            if norm_sheet and norm_sheet == norm_query:
                return sheet, 0.98

        best_sheet = None
        best_score = 0.0

        for sheet in sheets:
            norm_sheet = self._normalize_string(sheet)
            if not norm_sheet:
                continue

            if norm_query in norm_sheet or norm_sheet in norm_query:
                if 0.90 > best_score:
                    best_score = 0.90
                    best_sheet = sheet

            tokens_sheet = set(norm_sheet.split())
            if tokens_sheet and tokens_sheet.issubset(tokens_query):
                if 0.92 > best_score:
                    best_score = 0.92
                    best_sheet = sheet

            ratio = difflib.SequenceMatcher(None, norm_query, norm_sheet).ratio()
            if ratio > best_score:
                best_score = ratio
                best_sheet = sheet

        if best_score >= 0.55:
            return best_sheet, round(best_score, 2)

        return None, round(best_score, 2)

    def get_sheet_status(self, sheet_name: str, force_refresh: bool = False, max_age_seconds: float = 15.0) -> Dict[str, Any]:
        """Ispeziona il foglio per determinare quali trasporti (A1..A10) sono già compilati, i dettagli e la prossima riga libera."""
        import time
        if not force_refresh and sheet_name in self._sheet_status_cache:
            cache_time, cached_val = self._sheet_status_cache[sheet_name]
            if time.time() - cache_time < max_age_seconds:
                return cached_val

        if not self.is_connected():
            self.connect()

        sheet = self.workbook.Sheets(sheet_name)
        layout = self.detect_sheet_layout(sheet)
        transport_map = layout["transport_rows"]
        categories = layout["categories"]
        first_col = layout["first_col"]
        last_col = layout["last_col"]

        filled_transports = []
        row_values_map = {}
        transports_detail = {}

        for transport, row_idx in sorted(transport_map.items(), key=lambda x: x[1]):
            row_has_data = False
            vals = []
            kg_sum = 0.0
            box_sum = 0
            cat_entries = []

            for cat in categories:
                kg_val = sheet.Cells(row_idx, cat["kg_col"]).Value
                box_val = sheet.Cells(row_idx, cat["box_col"]).Value
                k_num = 0.0
                b_num = 0
                if kg_val not in (None, "", " "):
                    try:
                        k_num = float(kg_val)
                    except (ValueError, TypeError):
                        pass
                if box_val not in (None, "", " "):
                    try:
                        b_num = int(float(box_val))
                    except (ValueError, TypeError):
                        pass

                if k_num > 0 or b_num > 0:
                    row_has_data = True
                    kg_sum += k_num
                    box_sum += b_num
                    cat_entries.append(f"{cat['name']}: {k_num:.1f}kg ({b_num}sc)")

            for col_idx in range(first_col, last_col + 1):
                cell_val = sheet.Cells(row_idx, col_idx).Value
                if cell_val not in (None, "", 0, 0.0):
                    row_has_data = True
                vals.append(cell_val)

            row_values_map[transport] = vals
            if row_has_data:
                filled_transports.append(transport)
                transports_detail[transport] = {
                    "is_filled": True,
                    "row_idx": row_idx,
                    "totale_kg": round(kg_sum, 2),
                    "totale_scatole": box_sum,
                    "summary": f"{round(kg_sum, 1)} kg, {box_sum} scatole",
                    "categories_detail": ", ".join(cat_entries[:4]) + ("..." if len(cat_entries) > 4 else "")
                }
            else:
                transports_detail[transport] = {
                    "is_filled": False,
                    "row_idx": row_idx,
                    "totale_kg": 0.0,
                    "totale_scatole": 0,
                    "summary": "Libero",
                    "categories_detail": ""
                }

        next_available = None
        for transport in sorted(transport_map.keys(), key=lambda t: int(t.replace("A", ""))):
            if transport not in filled_transports:
                next_available = transport
                break

        res = {
            "filled_transports": filled_transports,
            "next_available_slot": next_available,
            "row_values_map": row_values_map,
            "transports_detail": transports_detail,
            "layout": layout
        }
        self._sheet_status_cache[sheet_name] = (time.time(), res)
        return res

    def analyze_transport_recommendation(
        self,
        sheet_name: str,
        current_card_kg: Optional[float] = None,
        current_card_boxes: Optional[int] = None,
        detected_transport: Optional[str] = None
    ) -> Dict[str, Any]:
        """Analizza lo stato delle righe in Excel e restituisce la raccomandazione intelligente per il trasporto."""
        st = self.get_sheet_status(sheet_name)
        filled = st.get("filled_transports", [])
        next_slot = st.get("next_available_slot") or "A1"
        details = st.get("transports_detail", {})

        # 1. Costruisci il testo di riepilogo generale delle righe presenti in Excel
        if not filled:
            excel_summary = f"📋 Excel [{sheet_name}]: Tutte le righe sono libere (A1..A10 vuote)"
        else:
            filled_desc = []
            for t in filled:
                d = details.get(t, {})
                kg = d.get("totale_kg", 0.0)
                bx = d.get("totale_scatole", 0)
                filled_desc.append(f"{t} ({kg:.1f}kg, {bx}sc)")
            excel_summary = f"📋 Excel [{sheet_name}]: {', '.join(filled_desc)} | Prossimo libero: {next_slot}"

        # 2. Controllo duplicato con righe già presenti
        duplicate_slot = None
        if current_card_kg is not None and current_card_boxes is not None:
            for t in filled:
                d = details.get(t, {})
                t_kg = d.get("totale_kg", 0.0)
                t_bx = d.get("totale_scatole", 0)
                if abs(t_kg - current_card_kg) < 0.2 and t_bx == current_card_boxes:
                    duplicate_slot = t
                    break

        duplicate_warning = None
        if duplicate_slot:
            duplicate_warning = (
                f"⚠️ POSSIBILE DUPLICATO: Il viaggio {duplicate_slot} ha già {details[duplicate_slot]['totale_kg']:.1f} kg "
                f"e {details[duplicate_slot]['totale_scatole']} scatole uguali a questa scheda!"
            )

        # 3. Determina lo slot consigliato
        clean_det = (detected_transport or "").strip().upper()
        if clean_det.isdigit():
            clean_det = f"A{clean_det}"

        recommended = next_slot
        status_msg = ""
        is_collision = False

        if clean_det in details:
            if clean_det in filled:
                # Il numero indicato sulla carta è già occupato!
                is_collision = True
                recommended = next_slot
                status_msg = f"⚠️ Scheda N° indica {clean_det}, ma in Excel è GIÀ COMPILATO! Consigliato: {next_slot}"
            else:
                recommended = clean_det
                status_msg = f"✓ Assegnato: {clean_det} (confermato libero in Excel)"
        else:
            # Numero illeggibile o assente sulla carta
            if filled:
                status_msg = f"💡 Suggerito da Excel: {next_slot} ({len(filled)} già inseriti)"
            else:
                status_msg = f"💡 Suggerito da Excel: {next_slot} (Prima riga libera)"

        return {
            "recommended_slot": recommended,
            "status_message": status_msg,
            "excel_summary_text": excel_summary,
            "duplicate_warning": duplicate_warning,
            "is_collision": is_collision,
            "next_available_slot": next_slot,
            "filled_transports": filled,
            "transports_detail": details,
        }

    def write_card_data(
        self,
        sheet_name: str,
        transport: str,
        processed_card: ProcessedCard,
        create_backup_first: bool = True
    ) -> Dict[str, Any]:
        """Scrive i dati della scheda corriere nel foglio e nella riga specificati rispettando il layout reale."""
        if not self.is_connected():
            self.connect()

        if sheet_name not in self.get_sheet_names():
            raise ValueError(f"Foglio '{sheet_name}' non trovato nel file Excel aperto.")

        transport = transport.upper().strip()
        sheet = self.workbook.Sheets(sheet_name)
        layout = self.detect_sheet_layout(sheet)
        transport_map = layout["transport_rows"]
        categories = layout["categories"]
        protected_rows = layout["protected_rows"]
        formula_rows = layout["formula_rows"]

        if transport not in transport_map:
            raise ValueError(f"Identificativo trasporto '{transport}' non valido. Ammessi solo da A1 a A10.")

        row_idx = transport_map[transport]
        if row_idx in protected_rows or row_idx in formula_rows:
            raise PermissionError(f"Tentativo vietato di scrittura nella riga protetta {row_idx}.")

        # 1. Backup preventivo
        backup_path = None
        if create_backup_first:
            backup_path = BackupService.create_backup(workbook_com=self.workbook)

        # Ottimizzazione COM: disattiva rendering, eventi e avvisi durante la scrittura celle per abbattere i tempi di I/O
        if self.excel_app:
            try:
                self.excel_app.ScreenUpdating = False
                self.excel_app.DisplayAlerts = False
                self.excel_app.EnableEvents = False
            except Exception:
                pass

        written_cells = []
        try:
            # 2. Scrittura celle per le 13 categorie
            for cat_def in categories:
                cat_name = cat_def["name"]
                cat_data = processed_card.categorie.get(cat_name)

                kg_val = cat_data.kg_calcolati if cat_data else 0.0
                box_val = cat_data.scatole_calcolate if cat_data else 0

                kg_cell = sheet.Cells(row_idx, cat_def["kg_col"])
                box_cell = sheet.Cells(row_idx, cat_def["box_col"])

                if kg_val > 0 or box_val > 0:
                    kg_cell.Value = kg_val
                    box_cell.Value = box_val
                    written_cells.append(f"{cat_def['kg_letter']}{row_idx}={kg_val}")
                    written_cells.append(f"{cat_def['box_letter']}{row_idx}={box_val}")
                else:
                    kg_cell.Value = None
                    box_cell.Value = None

            # 3. Attiva il foglio e posiziona la selezione
            try:
                sheet.Activate()
                sheet.Cells(row_idx, layout["first_col"]).Select()
            except Exception:
                pass

            # 4. Salva il file senza chiuderlo
            self.workbook.Save()
        finally:
            if self.excel_app:
                try:
                    self.excel_app.EnableEvents = True
                    self.excel_app.ScreenUpdating = True
                    self.excel_app.DisplayAlerts = True
                except Exception:
                    pass

        self.clear_cache()

        return {
            "success": True,
            "sheet": sheet_name,
            "transport": transport,
            "row": row_idx,
            "written_cells_count": len(written_cells),
            "backup_path": str(backup_path) if backup_path else None,
            "message": f"Dati scritti con successo nel foglio '{sheet_name}', riga {transport} ({len(written_cells)} celle compilate). File salvato."
        }
