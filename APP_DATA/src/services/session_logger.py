"""Modulo di logging centralizzato per sessione per l'applicazione Banco Alimentare.

Fornisce tracciamento completo e trasparente di:
- Avvio e configurazione della sessione (modelli, chiavi, provider, quote).
- Modello IA esatto chiamato in ciascun tentativo (Gemini 3.5, Gemini 2.5, Nemotron, Sonnet, MiMo).
- Eventi di failover/fallback (HTTP 429 quota esaurita, rotazione chiavi).
- Durata in secondi, token consumati e codici di stato HTTP.
- Operazioni di scansione e correzione orientamento immagini.
- Validazione matematica delle somme ed eventuale intervento dell'arbitro Claude Sonnet 5.5.
- Scrittura e salvataggio delle schede sul file Excel.
- Cattura automatica dei crash ed eccezioni non gestite con traceback completo.
"""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional

from ..config import BASE_DIR, LOGS_DIR

MAX_RETENTION_LOGS = 30  # Mantiene gli ultimi 30 log di sessione per non occupare spazio


def mask_api_key(key: Optional[str]) -> str:
    """Maschera una chiave API per registrarla nei log in sicurezza."""
    if not key:
        return "[NON CONFIGURATA]"
    k = key.strip()
    if len(k) <= 8:
        return "***"
    return f"{k[:6]}...{k[-4:]}"


class SessionLogger:
    """Gestore singleton dei log di sessione per Banco Alimentare."""

    _instance: Optional["SessionLogger"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.logs_dir = LOGS_DIR
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_file_path = self.logs_dir / f"session_{self.session_id}.log"
        self.latest_link_path = self.logs_dir / "latest_session.log"
        self._items_processed = 0
        self._errors_count = 0
        self._start_time = datetime.now()

        # Inizializza il logger di Python
        self.logger = logging.getLogger("banco_alimentare")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        # Rimuove handler precedenti se presenti
        for h in list(self.logger.handlers):
            self.logger.removeHandler(h)

        # File Handler (UTF-8 con flush immediato)
        formatter = logging.Formatter(
            fmt="[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.file_handler = logging.FileHandler(self.log_file_path, encoding="utf-8", mode="a")
        self.file_handler.setLevel(logging.DEBUG)
        self.file_handler.setFormatter(formatter)
        self.logger.addHandler(self.file_handler)

        # Stream Handler per console se stdout è disponibile (e non sotto pythonw con stdout=None)
        if sys.stdout is not None:
            try:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(logging.INFO)
                console_handler.setFormatter(formatter)
                self.logger.addHandler(console_handler)
            except Exception:
                pass

        # Crea o aggiorna copia latest_session.log
        self._update_latest_pointer()

        # Pulisci file più vecchi se eccedono MAX_RETENTION_LOGS
        self._clean_old_logs()

        # Installa hook per catturare eccezioni non gestite
        self._install_excepthook()

    @classmethod
    def get_instance(cls) -> "SessionLogger":
        with cls._lock:
            if cls._instance is None:
                cls._instance = SessionLogger()
            return cls._instance

    def get_logger(self, component: str = "") -> logging.Logger:
        if component:
            return self.logger.getChild(component)
        return self.logger

    def _update_latest_pointer(self):
        """Mantiene una copia o link all'ultima sessione per rapida consultazione."""
        try:
            # Su Windows usiamo una copia del file iniziale o scriviamo un puntatore
            shutil.copy2(self.log_file_path, self.latest_link_path)
        except Exception:
            pass

    def sync_latest_pointer(self):
        """Aggiorna il file latest_session.log con i contenuti attuali."""
        try:
            self.file_handler.flush()
            shutil.copy2(self.log_file_path, self.latest_link_path)
        except Exception:
            pass

    def _clean_old_logs(self):
        """Elimina i file di log più vecchi oltre il limite configurato."""
        try:
            log_files = sorted(
                [f for f in self.logs_dir.glob("session_*.log") if f.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for old_file in log_files[MAX_RETENTION_LOGS:]:
                try:
                    old_file.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def _install_excepthook(self):
        """Intercetta eccezioni non catturate registrandole nel file log prima della chiusura."""
        old_excepthook = sys.excepthook

        def custom_excepthook(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                if old_excepthook:
                    old_excepthook(exc_type, exc_value, exc_traceback)
                return
            self.logger.critical(
                "CRASH NON GESTITO DELL'APPLICAZIONE:",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
            self.sync_latest_pointer()
            if old_excepthook:
                old_excepthook(exc_type, exc_value, exc_traceback)

        sys.excepthook = custom_excepthook

    # -------------------------------------------------------------------------
    # BANNER DI SESSIONE
    # -------------------------------------------------------------------------
    def log_session_start(self, config_data: Dict[str, Any]):
        """Scrive l'intestazione iniziale formattata con tutti i modelli e le configurazioni."""
        from ..config import (
            CASCADE_FREE_MODES,
            CASCADE_PAID_MODES,
            CASCADE_FALLBACK_MODES,
            DEFAULT_CASCADE_FREE_PRIMARY_MODE,
            DEFAULT_CASCADE_PAID_MODE,
            DEFAULT_CASCADE_FALLBACK_MODE,
            DEFAULT_CASCADE_ARBITRATOR_MODEL,
            DEFAULT_CASCADE_FREE_MODEL,
        )

        prov = str(config_data.get("active_provider", "cascade") or "cascade")
        k1 = config_data.get("gemini_api_key", "")
        k2 = config_data.get("gemini_api_key_2", "")
        or_key = config_data.get("openrouter_api_key", "")
        excel_path = config_data.get("last_excel_path", "")
        dpi = config_data.get("scanner_dpi", 200)
        source = str(config_data.get("scanner_default_source", "adf") or "adf")

        free_mode = config_data.get("cascade_free_primary_mode", DEFAULT_CASCADE_FREE_PRIMARY_MODE)
        paid_mode = config_data.get("cascade_paid_mode", DEFAULT_CASCADE_PAID_MODE)
        fallback_mode = config_data.get("cascade_fallback_mode", DEFAULT_CASCADE_FALLBACK_MODE)
        arb_model = (
            config_data.get("cascade_arbitrator_model")
            or config_data.get("openrouter_arbitrator_model")
            or DEFAULT_CASCADE_ARBITRATOR_MODEL
        )
        arb_free = bool(config_data.get("enable_arbitrator_on_free", config_data.get("enable_auto_arbitrator", True)))
        arb_paid = bool(config_data.get("enable_arbitrator_on_paid", False))

        free_label = CASCADE_FREE_MODES.get(free_mode, free_mode)
        paid_label = CASCADE_PAID_MODES.get(paid_mode, paid_mode)
        fb_label = CASCADE_FALLBACK_MODES.get(fallback_mode, config_data.get("openrouter_free_model", DEFAULT_CASCADE_FREE_MODEL))

        bri = float(config_data.get("image_brightness", 1.0))
        con = float(config_data.get("image_contrast", 1.0))
        sha = float(config_data.get("image_sharpness", 1.0))

        banner = [
            "=" * 82,
            "   BANCO ALIMENTARE — LOG DI SESSIONE",
            f"   Inizio Sessione : {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"   ID Sessione     : {self.session_id}",
            f"   File Log        : {self.log_file_path}",
            f"   Piattaforma     : Python {platform.python_version()} su {platform.system()} {platform.release()}",
            "-" * 82,
            f"   MODALITÀ PROVIDER ATTIVA: {prov.upper()}",
            "   CASCATA IA PERSONALIZZABILE (3 SEZIONI):",
            f"     • [SEZIONE 1 - Google Free]         : {free_label}",
            f"                                 Chiave 1: {mask_api_key(k1)} | Chiave 2: {mask_api_key(k2)}",
            f"     • [SEZIONE 2 - OpenRouter Pagamento]: {paid_label}",
            f"     • [SEZIONE 3 - Fallback Free OR]    : {fb_label} ({config_data.get('openrouter_free_model', DEFAULT_CASCADE_FREE_MODEL)})",
            f"     • [ARBITRO MATEMATICO]              : {arb_model}",
            f"       -> Su modelli Free: {'ATTIVO' if arb_free else 'DISATTIVATO'} | Su modelli a Pagamento: {'ATTIVO' if arb_paid else 'DISATTIVATO'}",
            f"                                 Chiave OR: {mask_api_key(or_key)}",
            f"     • [MODALITÀ SINGOLA]                : Gemini={config_data.get('gemini_model', 'gemini-3.5-flash-lite')} | OR={config_data.get('openrouter_model', 'anthropic/claude-haiku-5.5')}",
            "-" * 82,
            f"   SORGENTE DATI EXCEL : {Path(excel_path).name if excel_path else '[Nessun file aperto]'}",
            f"   OPZIONI SCANNER     : Sorgente={source.upper()} | Risoluzione={dpi} DPI | Luminosità={bri:.2f} | Contrasto={con:.2f} | Nitidezza={sha:.2f}",
            "=" * 82,
        ]
        text_banner = "\n" + "\n".join(banner) + "\n"
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write(text_banner)
        self.sync_latest_pointer()

    def log_session_end(self, reason: str = "Chiusura ordinaria dell'applicazione"):
        """Scrive il resoconto finale prima di terminare la sessione."""
        elapsed = datetime.now() - self._start_time
        banner = [
            "\n" + "=" * 82,
            "   BANCO ALIMENTARE — FINE SESSIONE",
            f"   Fine Sessione      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (Durata: {str(elapsed).split('.')[0]})",
            f"   Motivo             : {reason}",
            f"   Schede Elaborate   : {self._items_processed}",
            f"   Errori Rilevati    : {self._errors_count}",
            f"   File Log Completo  : {self.log_file_path}",
            "=" * 82 + "\n",
        ]
        with open(self.log_file_path, "a", encoding="utf-8") as f:
            f.write("\n".join(banner))
        self.sync_latest_pointer()

    # -------------------------------------------------------------------------
    # TRACCIAMENTO OPERAZIONI IA (MODELLI ESATTI E RISPOSTE)
    # -------------------------------------------------------------------------
    def log_ai_attempt(self, tier_label: str, provider: str, model_name: str, key_label: str = "", details: str = ""):
        """Registra l'avvio della chiamata verso un modello specifico."""
        key_info = f" [Chiave: {key_label}]" if key_label else ""
        detail_info = f" | {details}" if details else ""
        msg = f"[{tier_label}] Invio richiesta a {provider} con modello='{model_name}'{key_info}{detail_info}"
        self.logger.info(msg)
        self.sync_latest_pointer()

    def log_ai_result(
        self,
        tier_label: str,
        provider: str,
        model_name: str,
        status_code: int,
        duration_sec: float,
        tokens: int = 0,
        is_success: bool = True,
        error_msg: str = "",
        cost_usd: Optional[float] = None,
    ):
        """Registra l'esito della chiamata IA con modello esatto, latenza, token, costo e stato."""
        cost_str = ""
        if cost_usd is not None:
            cost_str = f" | Costo: ${cost_usd:.4f}" if cost_usd > 0 else " | Costo: Gratis"
        if is_success:
            msg = (
                f"[{tier_label}] Risposta positiva da '{model_name}': HTTP {status_code} in {duration_sec:.2f}s "
                f"| Token totali: {tokens}{cost_str}"
            )
            self.logger.info(msg)
        else:
            self._errors_count += 1
            if status_code == 429:
                msg = (
                    f"[{tier_label}] QUOTA ESAURITA (HTTP 429) per modello '{model_name}' in {duration_sec:.2f}s! "
                    f"Avvio failover/fallback al livello successivo... Dettaglio: {error_msg}"
                )
                self.logger.warning(msg)
            else:
                msg = (
                    f"[{tier_label}] ERRORE HTTP {status_code} per modello '{model_name}' in {duration_sec:.2f}s: {error_msg}"
                )
                self.logger.error(msg)
        self.sync_latest_pointer()

    def log_ai_fallback(self, from_tier: str, to_tier: str, next_model: str, reason: str = ""):
        """Registra il passaggio di fallback ad un altro modello."""
        self.logger.warning(
            f"[FALLBACK IA] Passaggio da [{from_tier}] a [{to_tier}] (Nuovo Modello: '{next_model}') | Motivo: {reason}"
        )
        self.sync_latest_pointer()

    def log_math_validation(self, discrepancies: List[Dict[str, Any]], will_arbitrate: bool = False, arbitrator_model: str = ""):
        """Registra l'esito del controllo matematico tra somme delle scatole e totali scritti a mano."""
        if not discrepancies:
            self.logger.info("[CONTROLLO SOMME] Tutte le somme delle categorie corrispondono perfettamente ai totali dichiarati.")
        else:
            disc_str = "; ".join([
                f"{d['categoria']}: calcolato={d['calcolato']} kg vs dichiarato={d['dichiarato']} kg"
                for d in discrepancies
            ])
            self.logger.warning(f"[CONTROLLO SOMME] Discrepanza matematica rilevata su: {disc_str}")
            if will_arbitrate and arbitrator_model:
                self.logger.info(f"[PIANO D - ARBITRO] Invocazione arbitro di precisione con modello='{arbitrator_model}' per correzione celle...")
        self.sync_latest_pointer()

    def log_arbitration_result(self, arbitrator_model: str, success: bool, duration_sec: float, details: str = ""):
        """Registra l'esito dell'intervento dell'arbitro Claude Sonnet 5.5."""
        if success:
            self.logger.info(
                f"[PIANO D - ARBITRO] Arbitraggio completato con successo con '{arbitrator_model}' in {duration_sec:.2f}s. {details}"
            )
        else:
            self.logger.warning(
                f"[PIANO D - ARBITRO] Arbitro '{arbitrator_model}' non riuscito ({details}). Mantenuti i dati originali con avviso."
            )
        self.sync_latest_pointer()

    def log_extraction_complete(self, tier_winner: str, supermarket: str, scheda_num: str, box_count: int, total_kg: float, upside_down: bool = False, notes: str = ""):
        """Registra il riassunto dell'estrazione completata per un foglio."""
        self._items_processed += 1
        flip_str = " | Scheda rilevata capovolta dall'IA: RUOTATA 180°" if upside_down else ""
        note_str = f" | Note: {notes}" if notes else ""
        self.logger.info(
            f"[ESTRAZIONE COMPLETATA] Modello Vincente: '{tier_winner}' | Supermercato: '{supermarket or 'N/D'}' "
            f"| Scheda N°: {scheda_num or 'N/D'} | Scatole: {box_count} (Tot: {total_kg:.1f} kg){flip_str}{note_str}"
        )
        self.sync_latest_pointer()

    # -------------------------------------------------------------------------
    # TRACCIAMENTO SCANNER & IMMAGINI
    # -------------------------------------------------------------------------
    def log_scan_event(self, action: str, source: str = "", dpi: int = 0, details: str = ""):
        """Registra eventi legati allo scanner hardware WIA."""
        src_str = f" [{source.upper()}]" if source else ""
        dpi_str = f" @ {dpi} DPI" if dpi else ""
        self.logger.info(f"[SCANNER]{src_str}{dpi_str} {action} {details}".strip())
        self.sync_latest_pointer()

    def log_orientation_event(self, filepath: str, angle: int, action: str):
        """Registra le operazioni di rilevamento e rotazione dell'orientamento."""
        fname = Path(filepath).name
        self.logger.info(f"[ORIENTAMENTO] '{fname}': angolo rilevato={angle}° -> Azione: {action}")
        self.sync_latest_pointer()

    # -------------------------------------------------------------------------
    # TRACCIAMENTO EXCEL & CONFIGURAZIONE
    # -------------------------------------------------------------------------
    def log_excel_event(self, action: str, sheet_name: str = "", row: int = 0, details: str = ""):
        """Registra le modifiche apportate ai file Excel territoriali."""
        target = f" su foglio '{sheet_name}' riga {row}" if sheet_name and row else (f" foglio '{sheet_name}'" if sheet_name else "")
        self.logger.info(f"[EXCEL] {action}{target} {details}".strip())
        self.sync_latest_pointer()

    def log_config_change(self, key: str, old_val: Any, new_val: Any):
        """Registra modifiche di configurazione apportate dall'utente durante l'esecuzione."""
        lk = str(key or "").lower()
        if any(s in lk for s in ("key", "token", "secret", "password", "pwd", "auth")):
            safe_old = mask_api_key(str(old_val)) if old_val else "[NON CONFIGURATA]"
            safe_new = mask_api_key(str(new_val)) if new_val else "[NON CONFIGURATA]"
            self.logger.info(f"[CONFIG] Aggiornato parametro '{key}': '{safe_old}' -> '{safe_new}'")
        else:
            self.logger.info(f"[CONFIG] Aggiornato parametro '{key}': '{old_val}' -> '{new_val}'")
        self.sync_latest_pointer()

    def log_archive_event(self, action: str, details: str = ""):
        """Registra eventi di archiviazione nello storico scansioni e dataset."""
        self.logger.info(f"[STORICO] {action} {details}".strip())
        self.sync_latest_pointer()

    # -------------------------------------------------------------------------
    # UTILITY ACCESSO AI FILE DI LOG
    # -------------------------------------------------------------------------
    def get_current_log_path(self) -> Path:
        return self.log_file_path

    def read_current_log(self, max_lines: Optional[int] = None) -> str:
        """Legge il contenuto del log di sessione corrente."""
        try:
            self.file_handler.flush()
            if not self.log_file_path.exists():
                return "File di log non ancora creato."
            with open(self.log_file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                if max_lines and len(lines) > max_lines:
                    return "".join(lines[-max_lines:])
                return "".join(lines)
        except Exception as e:
            return f"Errore durante la lettura del file log: {e}"

    def open_in_notepad(self) -> bool:
        """Apre il file di log corrente nel Blocco Note nativo o programma predefinito."""
        try:
            self.sync_latest_pointer()
            if os.name == "nt":
                try:
                    os.startfile(str(self.log_file_path))
                    return True
                except Exception:
                    subprocess.Popen(["notepad.exe", str(self.log_file_path)])
                    return True
            else:
                subprocess.Popen(["xdg-open", str(self.log_file_path)])
                return True
        except Exception as e:
            self.logger.error(f"Impossibile aprire il file di log nel visualizzatore esterno: {e}")
            return False

    def open_logs_directory(self) -> bool:
        """Apre la cartella logs/ in Esplora Risorse di Windows."""
        try:
            if os.name == "nt":
                os.startfile(str(self.logs_dir))
                return True
            else:
                subprocess.Popen(["xdg-open", str(self.logs_dir)])
                return True
        except Exception as e:
            self.logger.error(f"Impossibile aprire la cartella logs: {e}")
            return False


def get_session_logger() -> SessionLogger:
    """Restituisce l'istanza globale del SessionLogger."""
    return SessionLogger.get_instance()
