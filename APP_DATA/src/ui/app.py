"""Applicazione desktop principale Banco Alimentare (Versione 3 - v3).

Restyling moderno con bordi arrotondati (CustomTkinter), layout side-by-side ed ergonomia da tastiera.

"""



import os

import subprocess

from dataclasses import dataclass

from pathlib import Path

import tkinter as tk

from tkinter import ttk, messagebox, filedialog

from datetime import datetime

from typing import Optional, Dict, Any, List

import customtkinter as ctk



from ..config import (

    CATEGORIES,

    TRANSPORT_ROWS,

    BACKUP_DIR,

    BASE_DIR,

    ROOT_DIR,

    load_app_config,

    save_app_config,

    has_valid_api_key,

    DEFAULT_GEMINI_MODEL,

    DEFAULT_CASCADE_GEMINI_MODEL,

    DEFAULT_CASCADE_V2_PRIMARY_MODEL,

    DEFAULT_CASCADE_LUNA_MODEL,

    DEFAULT_CASCADE_BUFFER_MODEL,

    DEFAULT_CASCADE_HAIKU_MODEL,

    DEFAULT_CASCADE_FREE_MODEL,

    DEFAULT_CASCADE_ARBITRATOR_MODEL,

    DEFAULT_CASCADE_FREE_PRIMARY_MODE,

    DEFAULT_CASCADE_PAID_MODE,

    DEFAULT_CASCADE_FALLBACK_MODE,

    SUPPORTED_PROVIDERS,

    get_pipeline_status,

    STORICO_DIR,

    DEFAULT_SHORTCUTS,

)

from ..models import (

    ProcessedCard,

    FlagSeverity,

    ValidationFlag,

    RawCardExtraction,

    BoxItem,

)

from ..validation.validator import CardValidator

from ..excel.excel_manager import (

    ExcelManager,

    ExcelNotRunningError,

    ExcelWorkbookNotOpenError,

)

from ..excel.backup_service import BackupService

from ..scanner import (

    ScannerService,

    TEST_IMAGES_DIR,

    SOURCE_FEEDER,

    FeederNotAvailableError,

    ScanBatchError,

    prepare_scan_for_pipeline,

    detect_best_preset,

    create_ai_optimized_copy,

    clean_temp_ai_dir,

    TEMP_AI_DIR,

)

from ..extractors.base import BaseVisionExtractor

from ..extractors.mock_extractor import MockVisionExtractor

from ..extractors.gemini_extractor import GeminiVisionExtractor

from ..extractors.openrouter_extractor import OpenRouterVisionExtractor

from ..extractors.cascade_extractor import CascadeVisionExtractor



from .components import (

    ImageViewerV3,

    ExcelNotOpenDialogV3,

    QuickEditRowDialogV3,

    QuickEditBoxDialogV3,

    FullScreenViewerDialogV3,

    TooltipV3,

    IAStatusDialogV3,

)

from .components_dialogs import (

    WelcomeSetupDialogV3,

    SettingsDialogV3,

    BatchStripV3,

    ScanProgressDialogV3,

    ContinueScanDialogV3,

    DuplicateRowDialogV3,

)

from .async_utils import BackgroundTask

from ..services.quota_tracker import get_quota_tracker

from ..services.session_logger import get_session_logger

from ..services.colletta_service import CollettaService

from ..services.history_service import get_history_service

from .log_viewer_dialog import LogViewerDialogV3



APP_VERSION = "v3.1"



# Righe minime/massime del riquadro avvisi (si adatta dinamicamente al contenuto)

ALERTS_MIN_LINES = 4

ALERTS_MAX_LINES = 9



ctk.set_appearance_mode("light")

ctk.set_default_color_theme("blue")





@dataclass(eq=False)

class BatchItem:

    """Singolo foglio della coda di lavoro (lotto) con il suo stato di elaborazione."""



    image_path: str

    status: str = "pending"   # pending | processing | ready | written | error

    raw: Optional[RawCardExtraction] = None

    card: Optional[ProcessedCard] = None

    error: str = ""

    preset_id: int = 0

    ai_image_path: Optional[str] = None

    force_strong: bool = False

    raw_archive_path: Optional[str] = None

    raw_assigned_market: str = ""





class BancoAlimentareApp(ctk.CTk):

    """Finestra desktop principale Banco Alimentare con bordi arrotondati ed ergonomia da tastiera."""



    def __init__(self):

        super().__init__()

        self.title("Banco Alimentare — Colletta Alimentare | Inserimento Rapido Schede")

        self.geometry("1320x880")

        self.minsize(1120, 740)

        self.configure(fg_color="#f1f5f9")



        # Controller e configurazioni

        self.config_data = load_app_config()

        # Ripristina il provider salvato dall'utente (solo fallback a 'cascade' se mancante o non supportato)

        raw_saved_prov = str(self.config_data.get("active_provider") or "").strip()

        if not raw_saved_prov:

            self.config_data["active_provider"] = "cascade"

        else:

            norm_prov = self._normalize_display_to_provider(raw_saved_prov)

            self.config_data["active_provider"] = norm_prov if norm_prov in SUPPORTED_PROVIDERS else "cascade"

        self.session_logger = get_session_logger()

        self.session_logger.log_session_start(self.config_data)

        self.colletta_service = CollettaService.get_instance()

        self.available_files = self.colletta_service.list_available_files()



        # Determina il file gestionale iniziale

        last_path = self.config_data.get("last_excel_path")

        if not last_path or not os.path.exists(last_path) or "MASTER" in last_path:

            perugia_path = None

            for f in self.available_files:

                if "perugia" in f["file_name"].lower():

                    perugia_path = f["file_path"]

                    break

            self.current_excel_path = perugia_path or (self.available_files[0]["file_path"] if self.available_files else "")

        else:

            self.current_excel_path = last_path



        self.excel_mgr = ExcelManager(self.current_excel_path)

        self.current_processed_card: Optional[ProcessedCard] = None

        self.current_image_path: Optional[str] = None

        self.active_extractor: Optional[BaseVisionExtractor] = None

        self.expanded_categories: set[str] = set()

        self.quota_tracker = get_quota_tracker()

        self._pending_suggestion_action = None

        self.session_cards_written: int = 0



        # Coda di lavoro multi-foglio

        self.batch_items: List[BatchItem] = []

        self.batch_index: int = -1

        self._strip_visible = False

        self._extracting = False

        self._active_extraction_task = None

        self._extraction_generation_id: int = 0

        self._scan_busy = False

        self._scanning_folder = False

        self._all_sheets: List[str] = []

        self._toast_job = None

        self._folder_feedback_job = None

        self.swap_panels: bool = bool(self.config_data.get("swap_panels", False))



        self._init_extractor()

        self._setup_ttk_styles()

        self._build_ui()

        self._setup_global_shortcuts()
        self._update_scan_button_mode()

        self._update_ia_status_ui()



        # Pulizia preventiva cartella temporanea IA

        try:

            clean_temp_ai_dir()

        except Exception:

            pass



        # Gestione chiusura pulita dell'applicazione con pulizia file temporanei

        self.protocol("WM_DELETE_WINDOW", self.on_closing)



        # Verifica iniziale di Excel dolce in background

        self._excel_check_job = self.after(400, self._check_excel_status_silent)

        # Primo avvio (onboarding API key) e rilevamento cartella dati

        self._startup_check_job = self.after(600, self._startup_checks)



    # Etichette del menu provider nell'header nell'ordine esatto richiesto

    PROVIDER_DISPLAY_CASCADE = "🚀 Cascata Personalizzabile (3 Sezioni — Consigliata)"

    PROVIDER_DISPLAY_CASCADE_V2 = "⚡ Cascata v2 (Gemini 3.8 Primario: 3.8 -> 3.5 -> Haiku -> Nemotron)"

    PROVIDER_DISPLAY_GEMINI_35 = "🆓 Gemini 3.5 Flash-Lite (Google API Diretta — Gratis)"

    PROVIDER_DISPLAY_GEMINI_38 = "✨ Gemini 3.8 Flash (Google API Diretta — Gratis)"

    PROVIDER_DISPLAY_LUNA_LOW = "🌙 GPT-6 Luna (Low Reasoning) (OpenRouter)"

    PROVIDER_DISPLAY_HAIKU_LOW = "🦅 Claude Haiku 5.5 (Low Reasoning) (OpenRouter)"

    PROVIDER_DISPLAY_OR_SONNET = "🧠 Claude Sonnet 5.5 (OpenRouter)"

    PROVIDER_DISPLAY_OR_NEMOTRON = "🆓 Nemotron 3.5 Super-VL (OpenRouter Free — Gratis)"



    PROVIDER_DISPLAY_VALUES = [

        PROVIDER_DISPLAY_CASCADE,

        PROVIDER_DISPLAY_CASCADE_V2,

        PROVIDER_DISPLAY_GEMINI_35,

        PROVIDER_DISPLAY_GEMINI_38,

        PROVIDER_DISPLAY_LUNA_LOW,

        PROVIDER_DISPLAY_HAIKU_LOW,

        PROVIDER_DISPLAY_OR_SONNET,

        PROVIDER_DISPLAY_OR_NEMOTRON,

    ]



    PROVIDER_KEY_MAP = {

        "cascade": PROVIDER_DISPLAY_CASCADE,

        "cascade_v2": PROVIDER_DISPLAY_CASCADE_V2,

        "gemini_35": PROVIDER_DISPLAY_GEMINI_35,

        "gemini_38": PROVIDER_DISPLAY_GEMINI_38,

        "or_luna_low": PROVIDER_DISPLAY_LUNA_LOW,

        "or_haiku_low": PROVIDER_DISPLAY_HAIKU_LOW,

        "or_sonnet": PROVIDER_DISPLAY_OR_SONNET,

        "or_nemotron": PROVIDER_DISPLAY_OR_NEMOTRON,

    }

    PROVIDER_INV_MAP = {v: k for k, v in PROVIDER_KEY_MAP.items()}



    @classmethod

    def _normalize_display_to_provider(cls, disp: str) -> str:

        """Converte un'etichetta visuale nel codice provider interno."""

        d = (disp or "").strip()

        if d in cls.PROVIDER_INV_MAP:

            return cls.PROVIDER_INV_MAP[d]

        if d in cls.PROVIDER_KEY_MAP:

            return d

        dl = d.lower()

        if dl in cls.PROVIDER_KEY_MAP:

            return dl

        if "nemotron" in dl:

            return "or_nemotron"

        if "cascata v2" in dl or "cascade_v2" in dl or "cascade v2" in dl:

            return "cascade_v2"

        if "cascade" in dl or "cascata" in dl:

            return "cascade"

        if "3.8" in dl:

            return "gemini_38"

        if "3.5" in dl:

            return "gemini_35"

        if "luna" in dl:

            return "or_luna_low"

        if "haiku" in dl:

            return "or_haiku_low"

        if "sonnet" in dl:

            return "or_sonnet"

        # Retrocompatibilità

        if "efficienza" in dl or "efficiency" in dl:

            return "efficiency"

        if "2.5" in dl:

            return "cascade"

        if "terra" in dl:

            return "or_luna_low"

        if "mimo" in dl or "xiaomi" in dl:

            return "openrouter"

        return "cascade"



    @classmethod

    def _normalize_provider_to_display(cls, raw: str) -> str:

        provider = cls._normalize_display_to_provider(raw)

        return cls.PROVIDER_KEY_MAP.get(provider, cls.PROVIDER_DISPLAY_CASCADE)



    def _init_extractor(self):

        raw_provider = self.config_data.get("active_provider", "cascade")

        provider = self._normalize_display_to_provider(raw_provider)



        key1 = (self.config_data.get("gemini_api_key", "") or "").strip()

        key2 = (self.config_data.get("gemini_api_key_2", "") or "").strip()

        if not key1 and key2:

            key1, key2 = key2, ""

        or_key1 = (self.config_data.get("openrouter_api_key", "") or "").strip()

        or_key2 = (self.config_data.get("openrouter_api_key_2", "") or "").strip()

        if not or_key1 and or_key2:

            or_key1, or_key2 = or_key2, ""

        enable_fb = self.config_data.get("enable_auto_fallback", True)



        # Costruzione modulare della rete di fallback a catena (Chain Reaction):

        # Se un modello esaurisce quote, crediti o va in errore/timeout, l'estrazione non fallisce

        # ma prosegue automaticamente lungo la catena fino ai modelli gratuiti di riserva.

        def make_nemotron(fb=None):

            return OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="nvidia/nemotron-3.5-super-vl:free",

                reasoning_effort="none",

                display_name="Nemotron Free (Fallback)",

                fallback_extractor=fb,

                enable_auto_fallback=enable_fb

            ) if or_key1 else fb



        def make_haiku(fb=None):

            return OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="anthropic/claude-haiku-5.5",

                reasoning_effort="low",

                display_name="Claude Haiku 5.5 (Fallback)",

                fallback_extractor=fb,

                enable_auto_fallback=enable_fb

            ) if or_key1 else fb



        def make_luna(fb=None):

            return OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="openai/gpt-6-luna",

                reasoning_effort="low",

                display_name="GPT-6 Luna (Fallback)",

                fallback_extractor=fb,

                enable_auto_fallback=enable_fb

            ) if or_key1 else fb



        def make_gemini_35(fb=None):

            return GeminiVisionExtractor(

                api_key=key1,

                backup_api_key=key2,

                primary_label="Google 1",

                backup_label="Google 2",

                model="gemini-3.5-flash-lite",

                fallback_extractor=fb,

                enable_auto_fallback=enable_fb

            ) if (key1 or key2) else fb



        arb_free = bool(
            self.config_data.get(
                "enable_arbitrator_on_free",
                self.config_data.get("enable_auto_arbitrator", True),
            )
        )
        arb_paid = bool(self.config_data.get("enable_arbitrator_on_paid", False))
        arb_any = bool(arb_free or arb_paid)
        free_mode = self.config_data.get("cascade_free_primary_mode", DEFAULT_CASCADE_FREE_PRIMARY_MODE)
        paid_mode = self.config_data.get("cascade_paid_mode", DEFAULT_CASCADE_PAID_MODE)
        fallback_mode = self.config_data.get("cascade_fallback_mode", DEFAULT_CASCADE_FALLBACK_MODE)
        luna_model = self.config_data.get("cascade_luna_model", DEFAULT_CASCADE_LUNA_MODEL)

        if provider == "cascade_v2":

            self.active_extractor = CascadeVisionExtractor(

                gemini_api_key_1=key1,

                gemini_api_key_2=key2,

                openrouter_api_key=or_key1,

                gemini_model=self.config_data.get("cascade_gemini_model", DEFAULT_CASCADE_GEMINI_MODEL),

                gemini_38_model=DEFAULT_CASCADE_V2_PRIMARY_MODEL,

                luna_model=luna_model,

                buffer_model=self.config_data.get("cascade_buffer_model") or self.config_data.get("openrouter_buffer_model", DEFAULT_CASCADE_BUFFER_MODEL),

                haiku_model=self.config_data.get("cascade_haiku_model", DEFAULT_CASCADE_HAIKU_MODEL),

                free_model=self.config_data.get("cascade_free_model") or self.config_data.get("openrouter_free_model", DEFAULT_CASCADE_FREE_MODEL),

                arbitrator_model=self.config_data.get("cascade_arbitrator_model", self.config_data.get("openrouter_arbitrator_model", DEFAULT_CASCADE_ARBITRATOR_MODEL)),

                enable_auto_arbitrator=arb_any,

                enable_arbitrator_on_free=arb_free,

                enable_arbitrator_on_paid=arb_paid,

                free_primary_mode="gemini_38_then_35",

                paid_mode=paid_mode,

                fallback_mode=fallback_mode,

                quota_tracker=self.quota_tracker,

                variant="v2",

            )

        elif provider == "gemini_35":

            # Catena: Gemini 3.5 Lite -> GPT-6 Luna -> Claude Haiku -> Nemotron Free

            fb_chain = make_luna(make_haiku(make_nemotron(None))) if enable_fb else None

            self.active_extractor = GeminiVisionExtractor(

                api_key=key1,

                backup_api_key=key2,

                primary_label="Google 1",

                backup_label="Google 2",

                model="gemini-3.5-flash-lite",

                fallback_extractor=fb_chain,

                enable_auto_fallback=enable_fb

            )

        elif provider == "gemini_38":

            # Catena: Gemini 3.8 Flash -> Gemini 3.5 Lite -> GPT-6 Luna -> Claude Haiku -> Nemotron Free

            fb_35 = make_gemini_35(make_luna(make_haiku(make_nemotron(None)))) if enable_fb else None

            self.active_extractor = GeminiVisionExtractor(

                api_key=key1,

                backup_api_key=key2,

                primary_label="Google 1",

                backup_label="Google 2",

                model="gemini-3.8-flash",

                fallback_extractor=fb_35,

                enable_auto_fallback=enable_fb

            )

        elif provider == "or_luna_low":

            # Catena: GPT-6 Luna -> Claude Haiku -> Nemotron Free -> Gemini 3.5 Lite

            fb_chain = make_haiku(make_nemotron(make_gemini_35(None))) if enable_fb else None

            self.active_extractor = OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="openai/gpt-6-luna",

                reasoning_effort="low",

                display_name="GPT-6 Luna (Low)",

                fallback_extractor=fb_chain,

                enable_auto_fallback=enable_fb

            )

        elif provider == "or_haiku_low":

            # Catena: Claude Haiku -> Nemotron Free -> Gemini 3.5 Lite -> GPT-6 Luna

            fb_chain = make_nemotron(make_gemini_35(make_luna(None))) if enable_fb else None

            self.active_extractor = OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="anthropic/claude-haiku-5.5",

                reasoning_effort="low",

                display_name="Claude Haiku 5.5 (Low)",

                fallback_extractor=fb_chain,

                enable_auto_fallback=enable_fb

            )

        elif provider == "or_sonnet":

            # Catena: Claude Sonnet -> Gemini 3.5 Lite -> GPT-6 Luna -> Claude Haiku -> Nemotron Free

            fb_chain = make_gemini_35(make_luna(make_haiku(make_nemotron(None)))) if enable_fb else None

            self.active_extractor = OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="anthropic/claude-sonnet-5.5",

                reasoning_effort=None,

                display_name="Claude Sonnet 5.5",

                fallback_extractor=fb_chain,

                enable_auto_fallback=enable_fb

            )

        elif provider == "or_nemotron":

            # Catena: Nemotron Free -> Gemini 3.5 Lite -> GPT-6 Luna -> Claude Haiku

            fb_chain = make_gemini_35(make_luna(make_haiku(None))) if enable_fb else None

            self.active_extractor = OpenRouterVisionExtractor(

                api_key=or_key1,

                backup_api_key=or_key2,

                model="nvidia/nemotron-3.5-super-vl:free",

                reasoning_effort="none",

                display_name="Nemotron 3.5 Super-VL",

                fallback_extractor=fb_chain,

                enable_auto_fallback=enable_fb

            )

        else:

            # Predefinito: Cascata Personalizzabile a 3 Sezioni

            self.active_extractor = CascadeVisionExtractor(

                gemini_api_key_1=key1,

                gemini_api_key_2=key2,

                openrouter_api_key=or_key1,

                gemini_model=self.config_data.get("cascade_gemini_model", DEFAULT_CASCADE_GEMINI_MODEL),

                gemini_38_model=DEFAULT_CASCADE_V2_PRIMARY_MODEL,

                luna_model=luna_model,

                buffer_model=self.config_data.get("cascade_buffer_model") or self.config_data.get("openrouter_buffer_model", DEFAULT_CASCADE_BUFFER_MODEL),

                haiku_model=self.config_data.get("cascade_haiku_model", DEFAULT_CASCADE_HAIKU_MODEL),

                free_model=self.config_data.get("cascade_free_model") or self.config_data.get("openrouter_free_model", DEFAULT_CASCADE_FREE_MODEL),

                arbitrator_model=self.config_data.get("cascade_arbitrator_model", self.config_data.get("openrouter_arbitrator_model", DEFAULT_CASCADE_ARBITRATOR_MODEL)),

                enable_auto_arbitrator=arb_any,

                enable_arbitrator_on_free=arb_free,

                enable_arbitrator_on_paid=arb_paid,

                free_primary_mode=free_mode,

                paid_mode=paid_mode,

                fallback_mode=fallback_mode,

                quota_tracker=self.quota_tracker,

            )



    def _setup_ttk_styles(self):

        """Stili per il Treeview interno con layout compatto ed evidenziazione color-coded."""

        style = ttk.Style(self)

        style.theme_use("clam")

        style.configure(

            "Treeview",

            background="#ffffff",

            foreground="#0f172a",

            fieldbackground="#ffffff",

            rowheight=24,

            font=("Segoe UI", 9),

            borderwidth=0

        )

        style.configure(

            "Treeview.Heading",

            background="#f8fafc",

            foreground="#0f172a",

            font=("Segoe UI", 9, "bold"),

            borderwidth=1,

            relief="flat",

            padding=(6, 3)

        )

        style.map(

            "Treeview",

            background=[("selected", "#e0f2fe")],

            foreground=[("selected", "#0369a1")]

        )



    def _setup_global_shortcuts(self):

        """Scorciatoie globali da tastiera per massimizzare la velocità."""

        shortcuts = self.config_data.get("shortcuts") or DEFAULT_SHORTCUTS



        if not hasattr(self, "_bound_shortcuts"):

            self._bound_shortcuts = {}



        # Disassocia scorciatoie precedentemente registrate per evitare accumulo di listener

        for action_name, old_seq in list(self._bound_shortcuts.items()):

            try:

                self.unbind(old_seq)

            except Exception:

                pass

            if hasattr(self, "tree") and self.tree:

                try:

                    self.tree.unbind(old_seq)

                except Exception:

                    pass

        self._bound_shortcuts.clear()



        def _normalize_seq(seq: str) -> str:

            if not seq:

                return ""

            s = seq.strip()

            s = s.replace("Ctrl-", "Control-").replace("Ctrl+", "Control-").replace("ctrl-", "Control-").replace("ctrl+", "Control-")

            s = s.replace("+", "-")

            if not s.startswith("<"):

                s = f"<{s}>"

            return s



        def _safe_bind_root(action: str, seq: str, handler):

            norm = _normalize_seq(seq)

            if not norm:

                return

            try:

                self.bind(norm, handler)

                self._bound_shortcuts[action] = norm

            except Exception:

                pass



        def _safe_bind_tree(action: str, seq: str, handler):

            norm = _normalize_seq(seq)

            if not norm:

                return

            if hasattr(self, "tree") and self.tree:

                try:

                    self.tree.bind(norm, handler)

                    self._bound_shortcuts[f"tree_{action}"] = norm

                except Exception:

                    pass



        _safe_bind_root("scan", shortcuts.get("scan", "F2"), lambda e: self._on_acquire_scanner())

        _safe_bind_root("scan_flatbed", shortcuts.get("scan_flatbed", "<Shift-F2>"), lambda e: self._on_acquire_alternative_scanner())

        _safe_bind_root("upload_image", shortcuts.get("upload_image", "<Control-o>"), lambda e: self._on_upload_image())

        _safe_bind_root("write_excel", shortcuts.get("write_excel", "<Control-Return>"), lambda e: self._on_write_shortcut())

        _safe_bind_root("write_excel_alt", shortcuts.get("write_excel_alt", "<Alt-Return>"), lambda e: self._on_alt_write_shortcut())

        _safe_bind_root("toggle_override", shortcuts.get("toggle_override", "<Tab>"), self._on_tab_pressed)

        _safe_bind_tree("toggle_override", shortcuts.get("toggle_override", "<Tab>"), self._on_tab_pressed)

        _safe_bind_root("reset_session", shortcuts.get("reset_session", "F5"), lambda e: self._on_reset_all_clicked())

        _safe_bind_root("settings", shortcuts.get("settings", "<Control-p>"), lambda e: self._open_settings())



        # Intercetta il tasto Tab a livello globale e su Treeview per sopprimere la navigazione ciclica

        try:

            self.bind_class("all", "<<NextWindow>>", self._on_tab_pressed)

            self.bind_class("Treeview", "<Tab>", self._on_tab_pressed)

            self.bind_class("Treeview", "<Key-Tab>", self._on_tab_pressed)

            self.bind_class("Treeview", "<<NextWindow>>", lambda e: "break")

            self.bind_class("Treeview", "<<PrevWindow>>", lambda e: "break")

            self.bind_all("<Key-Tab>", self._on_tab_pressed)

            self.bind_all("<Tab>", self._on_tab_pressed)

        except Exception:

            pass



        # Scorciatoia configurabile per Modifica Rapida (edit_mode)

        def _on_shortcut_edit_mode(e=None):

            if self.is_edit_mode:

                self._on_save_edit_mode()

            else:

                self._on_tree_edit_action()

            return "break"



        _safe_bind_root("edit_mode", shortcuts.get("edit_mode", "<Shift-Return>"), _on_shortcut_edit_mode)

        _safe_bind_tree("edit_mode", shortcuts.get("edit_mode", "<Shift-Return>"), _on_shortcut_edit_mode)



        # Scorciatoia configurabile per Conferma Riga (confirm_row)

        _safe_bind_tree("confirm_row", shortcuts.get("confirm_row", "<Return>"), self._on_tree_return_key)



        # Scorciatoie standard e fallback sempre garantite

        self.bind("<Control-O>", lambda e: self._on_upload_image())

        self.bind("<Control-Return>", lambda e: self._on_write_shortcut())

        self.bind("<Control-KP_Enter>", lambda e: self._on_write_shortcut())

        self.bind("<Alt-Return>", lambda e: self._on_alt_write_shortcut())

        self.bind("<Alt-KP_Enter>", lambda e: self._on_alt_write_shortcut())

        self.bind("<Tab>", self._on_tab_pressed)

        self.bind("<Alt-Left>", lambda e: self._on_batch_prev())

        self.bind("<Alt-Right>", lambda e: self._on_batch_next())

        self.bind("<F5>", lambda e: self._on_reset_all_clicked())

        self.bind("<Control-Delete>", lambda e: self._on_reset_all_clicked())

        self.bind("<Escape>", lambda e: self._on_escape_pressed())

        self.bind("<F11>", lambda e: self._on_open_fullscreen())

        self.bind("<Control-Shift-R>", lambda e: self._on_reprocess_sonnet())

        self.bind("<Control-Shift-r>", lambda e: self._on_reprocess_sonnet())



        if hasattr(self, "tree") and self.tree:

            self.tree.bind("<Return>", self._on_tree_return_key)

            self.tree.bind("<KP_Enter>", self._on_tree_return_key)

            self.tree.bind("<Shift-Return>", self._on_tree_shift_return_key)

            self.tree.bind("<Shift-KP_Enter>", self._on_tree_shift_return_key)

            self.tree.bind("<Tab>", self._on_tab_pressed)

            self.tree.bind("<Key-Tab>", self._on_tab_pressed)



    def _on_tab_pressed(self, event=None):

        """Shortcut globale Tab: attiva/disattiva immediatamente la spunta di autorizzazione, senza navigazione focus tra widget."""

        if getattr(self, "_inline_entry", None) is not None:

            return None



        widget = getattr(event, "widget", None) if event else None

        if widget:

            try:

                import customtkinter as ctk

                if isinstance(widget, (tk.Entry, tk.Text, ctk.CTkEntry, ctk.CTkTextbox)):

                    return None

            except Exception:

                pass



        try:

            focused = self.focus_get()

            if isinstance(focused, (tk.Entry, tk.Text)):

                return None

            import customtkinter as ctk

            if isinstance(focused, (ctk.CTkEntry, ctk.CTkTextbox)):

                return None

        except Exception:

            pass



        current_val = bool(self.override_var.get())

        new_val = not current_val

        self.override_var.set(new_val)

        self._on_override_toggle()

        if new_val and self.write_btn.cget("state") != "disabled":

            try:

                self.write_btn.focus_set()

            except Exception:

                pass



        return "break"



    def _on_alt_write_shortcut(self):

        """Scorciatoia globale Alt+Invio: scrive direttamente la scheda su Excel."""

        if not self.current_processed_card:

            return

        self._on_write_to_excel()



    def _on_write_shortcut(self):

        """Ctrl+Invio globale: scrive direttamente la scheda su Excel."""

        if not self.current_processed_card:

            return

        self._on_write_to_excel()



    def _on_escape_pressed(self):

        if self.is_edit_mode:

            self._on_save_edit_mode()

            return

        self._on_cancel_card()



    def _on_cancel_card(self):

        """Annulla la scheda corrente: in un lotto scarta solo il foglio attivo."""

        cur = self._current_item()

        if not (self.current_processed_card or self.current_image_path or cur):

            return

        if cur and len(self.batch_items) > 1:

            msg = f"Vuoi scartare il foglio #{self.batch_index + 1} dal lotto?"

        else:

            msg = "Vuoi annullare la scheda corrente e resettare il modulo?"

        if not messagebox.askyesno("Annulla Scheda", msg, parent=self):

            return

        if cur and len(self.batch_items) > 1:

            self._discard_current_item()

        else:

            self._clear_batch()



    def _cancel_active_extraction(self):

        """Cancella cooperativamente l'estrazione IA in background se in corso."""

        self._extraction_generation_id += 1

        if hasattr(self, "_active_extraction_task") and self._active_extraction_task:

            try:

                self._active_extraction_task.cancel()

            except Exception:

                pass

            self._active_extraction_task = None

        self._extracting = False



    def _on_view_session_log(self):

        """Apre il visualizzatore interattivo del log della sessione corrente."""

        LogViewerDialogV3(self)



    def _on_open_logs_folder(self):

        """Apre la cartella dei log in Esplora Risorse di Windows."""

        self.session_logger.open_logs_directory()



    def _on_reset_all_clicked(self):

        """Richiesta di conferma per pulire tutto e resettare lo stato (F5)."""

        has_data = bool(self.batch_items or self.current_processed_card or self.current_image_path)

        if has_data:

            if not messagebox.askyesno(

                "Pulisci Tutto",

                "Vuoi azzerare tutte le schede scansionate/caricate, ripulire la visualizzazione e sbloccare qualsiasi elaborazione in sospeso?\n\n(L'applicazione tornerà allo stato iniziale pulito)",

                parent=self,

            ):

                return

        self._reset_all_state()



    def _reset_all_state(self):

        """Esegue il reset totale dello stato come se l'app fosse appena avviata."""

        # 1. Annulla elaborazioni IA in background

        self._cancel_active_extraction()



        # 2. Svuota la coda batch e le immagini

        self.batch_items.clear()

        self.batch_index = -1

        self.current_processed_card = None

        self.current_image_path = None

        self.expanded_categories.clear()



        # 3. Resetta viewer immagini e filmstrip batch

        if hasattr(self, "image_viewer"):

            self.image_viewer.clear()

        if hasattr(self, "_strip_visible") and self._strip_visible:

            self._hide_batch_strip()



        # 4. Resetta campi scheda, tabella scatole, totali e alert

        self._reset_card_fields()

        self.alerts_title_lbl.configure(text="In attesa di scansione o caricamento...", text_color="#64748b")



        # 5. Ripristina il gestore IA e aggiorna indicatori

        self._init_extractor()

        self._update_ia_status_ui()

        self._check_excel_status_silent()



        self._show_toast("🧹 Applicazione e scansioni azzerate (stato iniziale)")



    # --- COSTRUZIONE INTERFACCIA (v3 Arrotondata) ---



    def _build_ui(self):

        # 1. HEADER SUPERIORE

        self._build_header()



        # 2. FOOTER FISSO (Azioni Primarie) - Ancorato in basso prima del container centrale per massimizzare lo spazio verticale utile

        self._build_footer()



        # 3. CONTAINER CENTRALE FISSO A 2 COLONNE

        content_frame = ctk.CTkFrame(self, fg_color="transparent")

        content_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 2))



        # Pannello Immagine Documento (Sinistra, espandibile)

        self.left_pane = ctk.CTkFrame(content_frame, fg_color="#f1f5f9", corner_radius=0)

        self.image_pane = self.left_pane

        self.left_pane.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))

        self._build_left_pane(self.left_pane)



        # Pannello Tabella Dati, Categorie e Validazione (Destra, fisso a 540px)

        self.right_pane = ctk.CTkFrame(content_frame, fg_color="#f1f5f9", corner_radius=0, width=540)

        self.right_pane.pack_propagate(False)

        self.data_pane = self.right_pane

        self.right_pane.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))

        self._build_right_pane(self.right_pane)



    def _build_header(self):

        header = ctk.CTkFrame(self, corner_radius=0, fg_color="#ffffff")

        header.pack(side=tk.TOP, fill=tk.X)

        ctk.CTkFrame(self, corner_radius=0, height=1, fg_color="#e2e8f0").pack(side=tk.TOP, fill=tk.X)



        # Riga unica super-compatta con zero spazio sprecato

        top_row = ctk.CTkFrame(header, fg_color="transparent")

        top_row.pack(fill=tk.X, padx=10, pady=5)



        # ==== 1. A SINISTRA: SCANSIONE DIRETTA VELOCE + MENU ALTRE OPZIONI ====

        acq_box = ctk.CTkFrame(top_row, fg_color="transparent")

        acq_box.pack(side=tk.LEFT)



        self.scan_btn = ctk.CTkButton(

            acq_box,

            text="📷  SCANSIONE   F2",

            font=("Segoe UI", 12, "bold"),

            fg_color="#16a34a",

            hover_color="#15803d",

            corner_radius=10,

            width=175,

            height=36,

            command=self._on_acquire_scanner

        )

        self.scan_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.scan_btn_tooltip = TooltipV3(self.scan_btn, self._get_scan_button_tooltip)



        self.more_options_btn = ctk.CTkButton(

            acq_box,

            text="⋯ Altre Opzioni ▾",

            font=("Segoe UI", 10, "bold"),

            fg_color="#f1f5f9",

            hover_color="#e2e8f0",

            text_color="#0f172a",

            border_width=1,

            border_color="#cbd5e1",

            corner_radius=8,

            width=110,

            height=28,

            command=self._on_more_options_clicked

        )

        self.more_options_btn.pack(side=tk.LEFT, padx=(0, 6))

        TooltipV3(

            self.more_options_btn,

            "Altre opzioni: carica foto/PDF da computer (Ctrl+O), scansione singola su piano vetro o pulisci tutto (F5)"

        )



        # Menu a tendina 'Altre Opzioni'

        self.skip_blank_pages_var = tk.BooleanVar(value=True)

        self.more_options_menu = tk.Menu(self, tearoff=0, font=("Segoe UI", 10))

        self.more_options_menu.add_command(

            label="📁  Carica foto / PDF da computer   (Ctrl+O)",

            command=self._on_upload_image

        )

        self.more_options_menu.add_command(

            label="📄  Scansione singola su Piano Vetro   (Shift+F2)",

            command=self._on_acquire_flatbed

        )

        self.more_options_menu.add_separator()

        self.more_options_menu.add_command(

            label="🔍  Seleziona / Cambia Scanner di Windows (WIA)...",

            command=self._on_select_scanner_dialog

        )

        self.more_options_menu.add_checkbutton(

            label="☑  Scarta automaticamente fogli bianchi",

            variable=self.skip_blank_pages_var,

        )

        self.more_options_menu.add_separator()

        self.more_options_menu.add_command(

            label="🧹  Pulisci tutto e azzera sessione   (F5)",

            command=self._on_reset_all_clicked

        )

        self.more_options_menu.add_separator()

        self.more_options_menu.add_command(

            label="📜  Visualizza log sessione corrente",

            command=self._on_view_session_log

        )

        self.more_options_menu.add_command(

            label="📂  Apri cartella file di log",

            command=self._on_open_logs_folder

        )

        self.more_options_menu.add_command(

            label="📁  Apri cartella storico scansioni",

            command=self._open_history_folder

        )

        self.more_options_menu.add_separator()

        self.more_options_menu.add_command(

            label="💡  Invia Consiglio / Segnalazione...",

            command=self._open_feedback_dialog

        )



        # ==== 2. SORGENTE DATI (Cartella, File Excel, Stato Excel Aperto, Supermercato) ====

        src_box = ctk.CTkFrame(top_row, fg_color="transparent")

        src_box.pack(side=tk.LEFT)



        self.choose_folder_btn = ctk.CTkButton(

            src_box,

            text="📊 Excel ▾",

            font=("Segoe UI", 10, "bold"),

            fg_color="#0284c7",

            hover_color="#0369a1",

            corner_radius=8,

            width=75,

            height=30,

            command=self._on_excel_source_menu

        )

        self.excel_source_menu = tk.Menu(self, tearoff=0, font=("Segoe UI", 10))

        self.excel_source_menu.add_command(label="📁  Cambia cartella dati Colletta…", command=self._on_choose_data_folder)

        self.excel_source_menu.add_command(label="📄  Apri un singolo file Excel…", command=self._on_browse_file)

        self.choose_folder_btn.pack(side=tk.LEFT, padx=(0, 4))

        TooltipV3(self.choose_folder_btn, "Sorgente dati: cambia cartella o apri un file Excel")



        self.folder_pill = ctk.CTkFrame(src_box, corner_radius=12, fg_color="#e0f2fe", border_width=1, border_color="#bae6fd")

        self.folder_pill_lbl = ctk.CTkLabel(

            self.folder_pill, text="", font=("Segoe UI", 9, "bold"), text_color="#0369a1"

        )

        self.folder_pill_lbl.pack(padx=8, pady=2)

        TooltipV3(self.folder_pill, self._get_folder_tooltip)

        TooltipV3(self.folder_pill_lbl, self._get_folder_tooltip)



        file_labels = [f["display_label"] for f in self.available_files] or ["Nessun file trovato"]

        curr_fname = Path(self.current_excel_path).name if self.current_excel_path else ""

        init_label = file_labels[0]

        for f in self.available_files:

            if f["file_name"].lower() == curr_fname.lower() or f["file_path"].lower() == str(self.current_excel_path).lower():

                init_label = f["display_label"]

                break



        self.selected_file_var = tk.StringVar(value=init_label)

        self.file_menu = ctk.CTkOptionMenu(

            src_box,

            variable=self.selected_file_var,

            values=file_labels,

            corner_radius=8,

            width=165,

            height=30,

            fg_color="#f8fafc",

            text_color="#0f172a",

            button_color="#cbd5e1",

            button_hover_color="#94a3b8",

            font=("Segoe UI", 10),

            command=self._on_file_selected

        )

        self.file_menu.pack(side=tk.LEFT, padx=(0, 4))

        TooltipV3(self.file_menu, "File Excel territoriale attivo")



        # Pill Stato Excel: spostato qui vicino al tasto Excel, al file e al supermercato!

        self.excel_pill = ctk.CTkFrame(

            src_box, corner_radius=12, fg_color="#f1f5f9", border_width=1, border_color="#cbd5e1", height=30

        )

        self.excel_pill.pack(side=tk.LEFT, padx=(0, 6))



        self.excel_led_canvas = tk.Canvas(self.excel_pill, width=10, height=10, bg="#f1f5f9", highlightthickness=0)

        self.excel_led_canvas.pack(side=tk.LEFT, padx=(6, 2), pady=3)

        self.excel_led = self.excel_led_canvas.create_oval(1, 1, 9, 9, fill="#ef4444", outline="")



        self.excel_status_lbl = ctk.CTkLabel(

            self.excel_pill, text="Excel", font=("Segoe UI", 9, "bold"), text_color="#0f172a"

        )

        self.excel_status_lbl.pack(side=tk.LEFT, padx=(2, 4))



        self.open_excel_btn = ctk.CTkButton(

            self.excel_pill,

            text="📂",

            font=("Segoe UI Emoji", 10),

            fg_color="#0284c7",

            hover_color="#0369a1",

            corner_radius=8,

            width=24,

            height=22,

            command=self._on_excel_action_clicked

        )

        self.open_excel_btn.pack(side=tk.LEFT, padx=(0, 4), pady=2)

        TooltipV3(self.open_excel_btn, self._get_excel_action_tooltip)



        self.browse_file_btn = ctk.CTkButton(

            src_box, text="📄", font=("Segoe UI Emoji", 11), fg_color="#e2e8f0",

            hover_color="#cbd5e1", text_color="#0f172a", corner_radius=8, width=26, height=28,

            command=self._on_browse_file

        )



        ctk.CTkLabel(src_box, text="🏪 Supermercato:", font=("Segoe UI", 10, "bold"), text_color="#475569").pack(

            side=tk.LEFT, padx=(0, 4))

        self.supermarket_var = tk.StringVar()

        self.supermarket_combo = ctk.CTkComboBox(

            src_box,

            variable=self.supermarket_var,

            values=[],

            corner_radius=8,

            width=150,

            height=30,

            fg_color="#ffffff",

            text_color="#0f172a",

            button_color="#cbd5e1",

            button_hover_color="#94a3b8",

            border_width=1,

            border_color="#cbd5e1",

            font=("Segoe UI", 10),

            command=self._on_supermarket_selected

        )

        self.supermarket_combo.pack(side=tk.LEFT)

        try:

            self.supermarket_combo._entry.bind("<KeyRelease>", self._on_sheet_typed)

        except Exception:

            pass

        TooltipV3(self.supermarket_combo, "Foglio supermercato: scrivi per cercare, Invio per confermare")



        self.folder_feedback_lbl = ctk.CTkLabel(

            src_box, text="", font=("Segoe UI", 9), text_color="#64748b"

        )

        self.folder_feedback_lbl.pack(side=tk.LEFT, padx=(6, 0))



        # ==== 4. A DESTRA: IMPOSTAZIONI, INVERTI PANNELLI, CONTATORE SCHEDE, SELETTORE TUTTI I MODELLI ====

        right_controls = ctk.CTkFrame(top_row, fg_color="transparent")

        right_controls.pack(side=tk.RIGHT)



        # Pulsante Impostazioni

        self.settings_btn = ctk.CTkButton(

            right_controls,

            text="⚙️ Impostazioni",

            font=("Segoe UI", 10, "bold"),

            fg_color="#0284c7",

            hover_color="#0369a1",

            corner_radius=8,

            width=96,

            height=30,

            command=self._open_settings

        )

        self.settings_btn.pack(side=tk.RIGHT)

        TooltipV3(self.settings_btn, "Chiavi API, modello IA, scanner, scorciatoie e consigli")







        # Contatore Schede fatte (pulito, senza 'i' brutta)

        self.ia_pill = ctk.CTkFrame(

            right_controls, corner_radius=12, fg_color="#f8fafc", border_width=1, border_color="#cbd5e1", height=30

        )

        self.ia_pill.pack(side=tk.RIGHT, padx=(6, 4))



        self.ia_led_canvas = tk.Canvas(self.ia_pill, width=10, height=10, bg="#f8fafc", highlightthickness=0)

        self.ia_led_canvas.pack(side=tk.LEFT, padx=(6, 2), pady=3)

        self.ia_led = self.ia_led_canvas.create_oval(1, 1, 9, 9, fill="#10b981", outline="")



        self.ia_status_lbl = ctk.CTkLabel(

            self.ia_pill, text="🏆 Schede fatte: 0  ·  Oggi: 0", font=("Segoe UI", 9, "bold"),

            text_color="#0f172a", cursor="hand2"

        )

        self.ia_status_lbl.pack(side=tk.LEFT, padx=(2, 8))



        self.ia_pill.configure(cursor="hand2")

        self.ia_led_canvas.configure(cursor="hand2")

        self.ia_pill.bind("<Button-1>", lambda e: self._open_ia_stats_dialog())

        self.ia_status_lbl.bind("<Button-1>", lambda e: self._open_ia_stats_dialog())

        self.ia_led_canvas.bind("<Button-1>", lambda e: self._open_ia_stats_dialog())

        TooltipV3(self.ia_pill, "Contatore schede elaborate. Clicca per aprire il Flusso IA & Modelli nelle Impostazioni", delay_ms=200)

        TooltipV3(self.ia_status_lbl, "Contatore schede elaborate. Clicca per aprire il Flusso IA & Modelli nelle Impostazioni", delay_ms=200)



        # Selettore Tutti i Modelli Disponibili

        init_disp = self._normalize_provider_to_display(self.config_data.get("active_provider", "cascade"))

        self.provider_var = tk.StringVar(value=init_disp)

        self.provider_menu = ctk.CTkOptionMenu(

            right_controls,

            variable=self.provider_var,

            values=list(self.PROVIDER_DISPLAY_VALUES),

            corner_radius=8,

            width=245,

            height=30,

            fg_color="#f8fafc",

            text_color="#0f172a",

            button_color="#cbd5e1",

            button_hover_color="#94a3b8",

            font=("Segoe UI", 10),

            command=self._on_provider_change

        )

        self.provider_menu.pack(side=tk.RIGHT, padx=(6, 2))

        TooltipV3(self.provider_menu, "Seleziona il modello IA attivo per l'analisi delle schede")



        self._update_folder_pill()

        sheets = self.colletta_service.get_supermarkets_for_file(self.current_excel_path) if self.current_excel_path else []

        self._set_sheet_values(sheets)



    def _build_left_pane(self, parent):

        # 1. Strip miniature del lotto (visibile solo con più fogli, richiudibile con toggle)

        self.batch_strip = BatchStripV3(

            parent,

            on_select=self._select_batch_item,

            on_prev=self._on_batch_prev,

            on_next=self._on_batch_next,

            initial_expanded=False

        )



        # 2. Visualizzatore Documento con Toolbar Integrata (occupa il 100% dell'altezza utile)

        self.image_viewer = ImageViewerV3(

            parent,

            on_reprocess_callback=self._on_reprocess_image,

            on_image_changed_callback=self._on_image_viewer_changed,

            on_sonnet_reprocess_callback=self._on_reprocess_sonnet,

            on_preset_changed_callback=self._on_preset_changed,

        )

        self.image_viewer.pack(fill=tk.BOTH, expand=True)



    def _on_preset_changed(self, preset_id: int):

        """Notifica che l'utente ha cliccato un preset diverso sulla toolbar del visore."""

        item = self._current_item()

        if item is not None:

            item.preset_id = preset_id



    def _on_image_viewer_changed(self, image_path: Path):

        """Notifica che l'immagine corrente è stata ruotata o modificata dall'utente."""

        item = self._current_item()

        if item is not None:

            try:

                active_preset = self.image_viewer.get_active_preset() if hasattr(self, "image_viewer") else 0

                item.ai_image_path = str(create_ai_optimized_copy(Path(item.image_path), active_preset))

                item.preset_id = active_preset

            except Exception as e:

                try:

                    self.session_logger.logger.warning(f"Avviso aggiornamento copia IA post-rotazione: {e}")

                except Exception:

                    pass

        if self._strip_visible and 0 <= self.batch_index < len(self.batch_items):

            self.batch_strip.refresh_item(self.batch_index, reload_image=True)



    def _build_right_pane(self, parent):

        # 1. Card Destinazione Excel

        dest_card = ctk.CTkFrame(parent, corner_radius=14, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        dest_card.pack(fill=tk.X, pady=(0, 8))



        ctk.CTkLabel(

            dest_card,

            text="DESTINAZIONE GESTIONALE EXCEL",

            font=("Segoe UI", 10, "bold"),

            text_color="#64748b"

        ).pack(anchor=tk.W, padx=14, pady=(10, 4))



        # Banner Suggeritore Intelligente Foglio / File (appare solo su suggerimento/anomalia)

        self.suggestion_banner = ctk.CTkFrame(

            dest_card,

            corner_radius=10,

            fg_color="#eff6ff",

            border_width=1,

            border_color="#bfdbfe"

        )

        # Non viene mostrato inizialmente



        sugg_inner = ctk.CTkFrame(self.suggestion_banner, fg_color="transparent")

        sugg_inner.pack(fill=tk.X, padx=10, pady=(8, 4))



        self.suggestion_icon_lbl = ctk.CTkLabel(

            sugg_inner,

            text="💡",

            font=("Segoe UI", 14)

        )

        self.suggestion_icon_lbl.pack(side=tk.LEFT, padx=(0, 6), anchor=tk.N)



        sugg_text_box = ctk.CTkFrame(sugg_inner, fg_color="transparent")

        sugg_text_box.pack(side=tk.LEFT, fill=tk.X, expand=True)



        self.suggestion_title_lbl = ctk.CTkLabel(

            sugg_text_box,

            text="Suggerimento Foglio",

            font=("Segoe UI", 11, "bold"),

            text_color="#1e3a8a",

            anchor=tk.W

        )

        self.suggestion_title_lbl.pack(fill=tk.X)



        self.suggestion_desc_lbl = ctk.CTkLabel(

            sugg_text_box,

            text="",

            font=("Segoe UI", 10),

            text_color="#334155",

            anchor=tk.W,

            justify=tk.LEFT,

            wraplength=340

        )

        self.suggestion_desc_lbl.pack(fill=tk.X, pady=(2, 0))



        self.suggestion_action_btn = ctk.CTkButton(

            self.suggestion_banner,

            text="✓ Applica",

            font=("Segoe UI", 11, "bold"),

            height=28,

            corner_radius=8,

            fg_color="#0284c7",

            hover_color="#0369a1",

            command=self._on_apply_suggestion

        )

        self.suggestion_action_btn.pack(anchor=tk.E, padx=10, pady=(0, 8))



        dest_grid = ctk.CTkFrame(dest_card, fg_color="transparent")

        dest_grid.pack(fill=tk.X, padx=14, pady=(0, 14))

        self.dest_grid = dest_grid



        # Dimensioni FISSE delle colonne di dest_grid per prevenire qualsiasi layout shift

        dest_grid.grid_columnconfigure(0, weight=0, minsize=115)

        dest_grid.grid_columnconfigure(1, weight=1)

        dest_grid.grid_columnconfigure(2, weight=0)



        # Punto vendita (il selettore è nell'header: qui lo stato del foglio attivo)

        ctk.CTkLabel(

            dest_grid,

            text="Punto Vendita:",

            font=("Segoe UI", 11, "bold"),

            text_color="#0f172a"

        ).grid(row=0, column=0, sticky=tk.W, pady=4)



        self.sheet_name_lbl = ctk.CTkLabel(

            dest_grid,

            text="—",

            font=("Segoe UI", 11),

            text_color="#0369a1",

            anchor=tk.W,

            wraplength=200

        )

        self.sheet_name_lbl.grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)

        self.supermarket_var.trace_add("write", self._update_sheet_name_display)



        self.sheet_status_lbl = ctk.CTkLabel(

            dest_grid,

            text="",

            font=("Segoe UI", 11, "bold"),

            text_color="#64748b"

        )

        self.sheet_status_lbl.grid(row=0, column=2, sticky=tk.E, padx=(4, 0), pady=4)



        # Trasporto / Scheda N° (A1..A10)

        ctk.CTkLabel(

            dest_grid,

            text="Scheda / Viaggio:",

            font=("Segoe UI", 11, "bold"),

            text_color="#0f172a"

        ).grid(row=1, column=0, sticky=tk.W, pady=4)



        self.transport_var = tk.StringVar()

        self.transport_combo = ctk.CTkComboBox(

            dest_grid,

            variable=self.transport_var,

            values=[f"A{i}" for i in range(1, 11)],

            corner_radius=8,

            width=100,

            height=30,

            state="readonly",

            border_width=1,

            border_color="#cbd5e1",

            command=self._on_transport_selected

        )

        self.transport_combo.grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)



        self.transport_status_lbl = ctk.CTkLabel(

            dest_grid,

            text="",

            font=("Segoe UI", 11, "bold")

        )

        self.transport_status_lbl.grid(row=1, column=2, sticky=tk.E, padx=(4, 0), pady=4)



        # Riga 2: Riepilogo stato righe Excel (A1..A10) per il supermercato selezionato

        self.excel_summary_lbl = ctk.CTkLabel(

            dest_grid,

            text="",

            font=("Segoe UI", 9, "bold"),

            text_color="#475569",

            anchor="w",

            justify="left",

            wraplength=480

        )

        # Riepilogo testuale nascosto: resta solo "Suggerito da Excel" accanto al menu Scheda/Viaggio



        # 2. Card Tabella 13 Categorie

        table_card = ctk.CTkFrame(parent, corner_radius=14, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        table_card.pack(fill=tk.BOTH, expand=True, pady=(0, 8))



        t_head = ctk.CTkFrame(table_card, fg_color="transparent")

        t_head.pack(fill=tk.X, padx=14, pady=(8, 4))



        ctk.CTkLabel(

            t_head,

            text="DETTAGLIO 13 CATEGORIE ALIMENTARI",

            font=("Segoe UI", 10, "bold"),

            text_color="#64748b"

        ).pack(side=tk.LEFT)



        self.edit_btn = ctk.CTkButton(

            t_head,

            text="✏️ Modifica",

            font=("Segoe UI", 11, "bold"),

            fg_color="#0284c7",

            hover_color="#0369a1",

            corner_radius=8,

            width=90,

            height=28,

            state="disabled",

            command=self._on_toggle_edit_mode

        )

        self.edit_btn.pack(side=tk.RIGHT)



        # Container Tabella (Ospita la Vista Treeview standard e la Vista Modifica Tabellare)

        tree_container = ctk.CTkFrame(table_card, corner_radius=8, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        tree_container.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))



        # 1. Vista Visualizzazione (Treeview con Accordion)

        self.table_view_frame = ctk.CTkFrame(tree_container, fg_color="transparent")

        self.table_view_frame.pack(fill=tk.BOTH, expand=True)



        tree_scroll = ttk.Scrollbar(self.table_view_frame)

        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)



        cols = ("cat", "kg", "scatole", "stato")

        self.tree = ttk.Treeview(

            self.table_view_frame,

            columns=cols,

            show="headings",

            yscrollcommand=tree_scroll.set,

            height=10

        )

        tree_scroll.config(command=self.tree.yview)



        self.tree.heading("cat", text="Categoria Prodotto", anchor=tk.W)

        self.tree.heading("kg", text="Totale Kg", anchor=tk.E)

        self.tree.heading("scatole", text="N° Scatole", anchor=tk.CENTER)

        self.tree.heading("stato", text="Stato", anchor=tk.CENTER)



        self.tree.column("cat", width=180, minwidth=120, anchor=tk.W)

        self.tree.column("kg", width=95, minwidth=70, anchor=tk.E)

        self.tree.column("scatole", width=75, minwidth=50, anchor=tk.CENTER)

        self.tree.column("stato", width=55, minwidth=40, anchor=tk.CENTER)



        self.tree.pack(fill=tk.BOTH, expand=True)



        self.tree.bind("<Button-1>", self._on_tree_click)

        self.tree.bind("<Double-1>", lambda e: self._on_tree_edit_action())

        self.tree.bind("<Return>", self._on_tree_return_key)

        self.tree.bind("<KP_Enter>", self._on_tree_return_key)

        self.tree.bind("<Shift-Return>", self._on_tree_shift_return_key)

        self.tree.bind("<Shift-KP_Enter>", self._on_tree_shift_return_key)

        self.tree.bind("<Control-Return>", lambda e: (self._on_write_shortcut(), "break")[1])

        self.tree.bind("<Control-KP_Enter>", lambda e: (self._on_write_shortcut(), "break")[1])

        self.tree.bind("<space>", self._on_tree_toggle_space)

        self.tree.bind("<Right>", self._on_tree_expand)

        self.tree.bind("<Left>", self._on_tree_collapse)



        self.tree.tag_configure("ok", foreground="#15803d", font=("Segoe UI", 9, "bold"))

        self.tree.tag_configure("error", foreground="#b91c1c", font=("Segoe UI", 9, "bold"), background="#fee2e2")

        self.tree.tag_configure("empty", foreground="#94a3b8", font=("Segoe UI", 9))

        self.tree.tag_configure("child_row", foreground="#334155", background="#f8fafc", font=("Segoe UI", 9))

        self.tree.tag_configure("child_error", foreground="#b91c1c", background="#fef2f2", font=("Segoe UI", 9, "italic"))

        self.tree.tag_configure("child_confirmed", foreground="#15803d", background="#f0fdf4", font=("Segoe UI", 9, "bold"))



        # Stato modalita modifica in-place

        self.is_edit_mode = False

        self._edit_mode_source: str = "button"

        self._edit_mode_backup: Optional[ProcessedCard] = None

        self._inline_entry: Optional[tk.Entry] = None

        self._editing_info: Optional[Dict[str, Any]] = None



        # Totali vs Azioni Modifica

        totals_card = ctk.CTkFrame(table_card, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e2e8f0")

        totals_card.pack(fill=tk.X, padx=12, pady=(0, 10))



        self.calc_totals_lbl = ctk.CTkLabel(

            totals_card,

            text="Totale: 0 scatole  —  0.0 Kg",

            font=("Segoe UI", 11, "bold"),

            text_color="#0f172a"

        )

        self.calc_totals_lbl.pack(side=tk.LEFT, padx=12, pady=8)



        # Box Azioni Modifica (visibile SOLO in modalità modifica)

        self.edit_actions_box = ctk.CTkFrame(totals_card, fg_color="transparent")

        self.cancel_edit_btn = ctk.CTkButton(

            self.edit_actions_box,

            text="✖️ Annulla",

            font=("Segoe UI", 11),

            fg_color="#e2e8f0",

            text_color="#0f172a",

            hover_color="#cbd5e1",

            corner_radius=8,

            width=80,

            height=28,

            command=self._on_cancel_edit_mode

        )

        self.save_edit_btn = ctk.CTkButton(

            self.edit_actions_box,

            text="💾 Salva",

            font=("Segoe UI", 11, "bold"),

            fg_color="#16a34a",

            hover_color="#15803d",

            corner_radius=8,

            width=80,

            height=28,

            command=self._on_save_edit_mode

        )

        self.cancel_edit_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self.save_edit_btn.pack(side=tk.RIGHT)

        # Nascosto per impostazione predefinita

        self.edit_actions_box.pack_forget()



        # 3. Card Allarmi e Flag Rossi

        self.alerts_card = ctk.CTkFrame(parent, corner_radius=14, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        self.alerts_card.pack(fill=tk.X)



        self.alerts_title_lbl = ctk.CTkLabel(

            self.alerts_card,

            text="STATO VALIDAZIONE",

            font=("Segoe UI", 10, "bold"),

            text_color="#64748b"

        )

        self.alerts_title_lbl.pack(anchor=tk.W, padx=14, pady=(10, 4))



        alerts_box = tk.Frame(self.alerts_card, bg="#ffffff")

        alerts_box.pack(fill=tk.X, padx=12, pady=(0, 6))

        self.alerts_scroll = ttk.Scrollbar(alerts_box, orient=tk.VERTICAL)

        self.alerts_text = tk.Text(

            alerts_box,

            width=30,

            height=ALERTS_MIN_LINES,

            wrap=tk.WORD,

            font=("Segoe UI", 10),

            bg="#f8fafc",

            relief="solid",

            borderwidth=1,

            padx=8,

            pady=5,

            yscrollcommand=self.alerts_scroll.set

        )

        self.alerts_scroll.config(command=self.alerts_text.yview)

        self.alerts_text.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.alerts_text.config(state=tk.DISABLED)



        self.override_var = tk.BooleanVar(value=False)

        self.override_check = ctk.CTkCheckBox(

            self.alerts_card,

            text="Ho verificato i dati ed autorizzo la scrittura su Excel",

            variable=self.override_var,

            corner_radius=6,

            font=("Segoe UI", 11, "bold"),

            text_color="#0f172a",

            fg_color="#16a34a",

            hover_color="#15803d",

            border_color="#94a3b8",

            state="normal",

            cursor="hand2",

            command=self._on_override_toggle

        )

        self.override_check.pack(anchor=tk.W, padx=14, pady=(0, 10))



    def _build_footer(self):

        footer = ctk.CTkFrame(self, corner_radius=0, fg_color="#f1f5f9", height=50)

        footer.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(2, 8))



        # Pulsante Primario "SCRIVI SU EXCEL E SALVA"

        self.write_btn = ctk.CTkButton(

            footer,

            text="💾  SCRIVI SU EXCEL E SALVA  (Ctrl+Invio)",

            font=("Segoe UI", 13, "bold"),

            fg_color="#cbd5e1",

            text_color="#64748b",

            hover_color="#94a3b8",

            corner_radius=10,

            height=46,

            state="disabled",

            command=self._on_write_to_excel

        )

        self.write_btn.pack(side=tk.LEFT, padx=(0, 12))



        # Pulsante Scarta Foglio / Annulla Scheda (abilitato solo quando c'è un foglio attivo)

        self.reset_btn = ctk.CTkButton(

            footer,

            text="✖️  Annulla Scheda (Esc)",

            font=("Segoe UI", 12, "bold"),

            fg_color="#f1f5f9",

            text_color="#94a3b8",

            hover_color="#cbd5e1",

            corner_radius=10,

            height=46,

            state="disabled",

            command=self._on_cancel_card

        )

        self.reset_btn.pack(side=tk.LEFT, padx=(0, 16))

        TooltipV3(

            self.reset_btn,

            "Scarta il foglio corrente e passa al successivo (Esc).\n"

            "Per azzerare l'intera sessione usa '⋯ Altre Opzioni ▾ ➔ Pulisci tutto (F5)'"

        )



        self.toast_lbl = ctk.CTkLabel(

            footer,

            text="",

            font=("Segoe UI", 11, "bold"),

            text_color="#0369a1"

        )

        self.toast_lbl.pack(side=tk.LEFT)



        # Destra: Cartella Storico, Cartella Backup e Ultimo Backup

        open_history_btn = ctk.CTkButton(

            footer,

            text="📁 Cartella Storico",

            font=("Segoe UI", 11, "bold"),

            fg_color="#0284c7",

            text_color="#ffffff",

            hover_color="#0369a1",

            corner_radius=8,

            height=36,

            command=self._open_history_folder

        )

        open_history_btn.pack(side=tk.RIGHT, padx=(8, 0))

        TooltipV3(

            open_history_btn,

            "Archivio Storico Scansioni:\n"

            "Apre la cartella con tutte le cartelle e schede scansionate,\n"

            "salvate per i benchmark e i miglioramenti dei prossimi anni."

        )



        open_backup_btn = ctk.CTkButton(

            footer,

            text="📂 Cartella Backup",

            font=("Segoe UI", 11),

            fg_color="#e2e8f0",

            text_color="#0f172a",

            hover_color="#cbd5e1",

            corner_radius=8,

            height=36,

            command=self._open_backup_folder

        )

        open_backup_btn.pack(side=tk.RIGHT, padx=(8, 0))



        self.backup_lbl = ctk.CTkLabel(

            footer,

            text="🛡️ Nessun backup recente",

            font=("Segoe UI", 10),

            text_color="#64748b"

        )

        self.backup_lbl.pack(side=tk.RIGHT)



    # --- AVVIO, ONBOARDING E IMPOSTAZIONI ---



    def _startup_checks(self):

        """Primo avvio: onboarding API key; poi allineamento della cartella dati."""

        self._startup_check_job = None

        try:

            if not self.winfo_exists():

                return

            if not has_valid_api_key(self.config_data) and self.state() != "withdrawn":

                self._show_welcome()

            svc = self.colletta_service

            if svc.data_dir and svc.needs_rescan():

                self._apply_data_folder(str(svc.data_dir), startup=True)

            elif svc.data_dir:

                n_files = len(svc.index_data.get("files", {}))

                n_markets = len(svc.index_data.get("supermarkets", []))

                if n_files > 0:

                    self._set_folder_feedback(

                        f"✓ Rilevati {n_files} file Excel e {n_markets} supermercati nella cartella selezionata",

                        "#15803d",

                        auto_hide_ms=5000,

                    )

            elif not svc.data_dir:

                self._set_folder_feedback(

                    "Nessuna cartella dati: usa '📁 Scegli Cartella Dati...'", "#b45309"

                )

        except Exception as e:

            print(f"Avviso controlli di avvio: {e}")



    def _show_welcome(self):

        WelcomeSetupDialogV3(

            self,

            on_saved=self._on_welcome_key_saved,

            model=self.config_data.get("cascade_gemini_model", DEFAULT_CASCADE_GEMINI_MODEL),

            config=self.config_data,

            on_trello_unlocked=self._on_welcome_trello_unlocked,

        )



    def _on_welcome_trello_unlocked(self, trello_cfg: Dict[str, str]) -> None:

        if not isinstance(trello_cfg, dict):

            return

        for key in ("trello_api_key", "trello_token", "trello_list_id"):

            val = str(trello_cfg.get(key) or "").strip()

            if val:

                self.config_data[key] = val



    def _on_welcome_key_saved(self, key: str, key2: str = "", or_key: str = "", colletta_folder: str = ""):

        self.config_data["gemini_api_key"] = key

        self.config_data["gemini_api_key_2"] = key2

        self.config_data["openrouter_api_key"] = or_key

        self.config_data["active_provider"] = "cascade"

        if colletta_folder and os.path.isdir(colletta_folder):

            self.config_data["last_colletta_folder"] = os.path.normpath(colletta_folder)

        save_app_config(self.config_data)

        disp = self._normalize_provider_to_display("cascade")

        self.provider_var.set(disp)

        self._init_extractor()

        self._update_ia_status_ui()

        if colletta_folder and os.path.isdir(colletta_folder):

            cur_dir = str(self.colletta_service.data_dir or "")

            same_dir = (

                bool(cur_dir)

                and os.path.normcase(os.path.abspath(colletta_folder)) == os.path.normcase(os.path.abspath(cur_dir))

            )

            if not same_dir or self.colletta_service.needs_rescan():

                self._apply_data_folder(os.path.normpath(colletta_folder))

            else:

                n_files = len(self.colletta_service.index_data.get("files", {}))

                n_markets = len(self.colletta_service.index_data.get("supermarkets", []))

                if n_files > 0:

                    self._set_folder_feedback(

                        f"✓ Rilevati {n_files} file Excel e {n_markets} supermercati nella cartella selezionata",

                        "#15803d",

                        auto_hide_ms=5000,

                    )

        self._show_toast("✓ Pipeline a cascata configurata: l'app è pronta all'uso")



    def _build_feedback_context(self) -> Dict[str, Any]:

        excel_file = ""

        if getattr(self, "excel_mgr", None) is not None:

            try:

                excel_file = self.excel_mgr.get_target_filename() or ""

            except Exception:

                excel_file = ""

        return {

            "provider": self.config_data.get("active_provider", "cascade"),

            "excel_file": excel_file,

            "supermercato": self.supermarket_var.get().strip() if hasattr(self, "supermarket_var") else "",

        }



    def _open_settings(self, initial_tab: Optional[str] = None):

        kwargs: Dict[str, Any] = {

            "feedback_context": self._build_feedback_context(),

        }

        if initial_tab is not None:

            kwargs["initial_tab"] = initial_tab

        return SettingsDialogV3(self, self.config_data, self._apply_settings, **kwargs)



    def _open_feedback_dialog(self):

        return self._open_settings(initial_tab="💡 Consigli")



    def _apply_settings(self, values: Dict[str, Any]):

        for k, v in values.items():

            if self.config_data.get(k) != v:

                self.session_logger.log_config_change(k, self.config_data.get(k), v)

        self.config_data.update(values)

        save_app_config(self.config_data)

        disp = self._normalize_provider_to_display(self.config_data.get("active_provider", "cascade"))

        self.provider_var.set(disp)

        self._init_extractor()

        self._update_ia_status_ui()

        if hasattr(self, "_update_scan_button_mode"):

            self._update_scan_button_mode()

        self._setup_global_shortcuts()

        self._show_toast("✓ Impostazioni salvate")

        # Rinfresca l'immagine corrente e tutti i fogli del batch con i nuovi parametri di taratura
        try:
            b = float(self.config_data.get("image_brightness", 1.0))
            c = float(self.config_data.get("image_contrast", 1.0))
            s = float(self.config_data.get("image_sharpness", 1.0))

            tuned_paths = set()
            cur_item = self._current_item()

            # 1. Aggiorna tutti i fogli del lotto (partendo da .raw.jpg e rigenerando la copia IA dal file tarato)
            if hasattr(self, "batch_items") and self.batch_items:
                for idx, item in enumerate(self.batch_items):
                    if item.image_path and Path(item.image_path).exists():
                        p = Path(item.image_path)
                        ScannerService.apply_tuning_to_file(p, b, c, s)
                        tuned_paths.add(str(p))
                        try:
                            new_ai = create_ai_optimized_copy(p, getattr(item, "preset_id", 0))
                            item.ai_image_path = str(new_ai)
                        except Exception:
                            pass
                        if getattr(self, "_strip_visible", False) and hasattr(self, "batch_strip"):
                            self.batch_strip.refresh_item(idx, reload_image=True)

            # 2. Aggiorna l'immagine visualizzata a video
            if hasattr(self, "current_image_path") and self.current_image_path and Path(self.current_image_path).exists():
                if str(self.current_image_path) not in tuned_paths:
                    ScannerService.apply_tuning_to_file(Path(self.current_image_path), b, c, s)
                if hasattr(self, "image_viewer") and self.image_viewer:
                    self.image_viewer._original_cache.clear()
                    self.image_viewer._processed_cache.clear()
                    self.image_viewer.load_image(
                        str(self.current_image_path),
                        initial_preset=getattr(cur_item, "preset_id", 0) if cur_item else 0,
                    )
        except Exception as ex_tune:
            self.session_logger.logger.warning(f"Errore aggiornamento taratura live su immagine aperta: {ex_tune}")



    # --- CARTELLA DATI DINAMICA ---



    def _get_folder_tooltip(self) -> str:

        folder = self.colletta_service.data_dir

        return f"Cartella dati attiva:\n{folder}" if folder else "Nessuna cartella dati selezionata"



    def _update_sheet_name_display(self, *args):

        """Aggiorna il nome del supermercato senza alterare la larghezza del layout."""

        val = self.supermarket_var.get().strip() or "—"

        if len(val) > 26:

            disp_text = val[:24] + "..."

        else:

            disp_text = val

        try:

            self.sheet_name_lbl.configure(text=disp_text)

            TooltipV3(self.sheet_name_lbl, val if val != "—" else "Nessun foglio selezionato")

        except Exception:

            pass



    def _update_folder_pill(self):

        folder = self.colletta_service.data_dir

        name = folder.name if folder and folder.name else (str(folder) if folder else "nessuna cartella")

        if len(name) > 16:

            name = name[:14] + ".."

        self.folder_pill_lbl.configure(text=f"📂 {name}")



    def _clear_folder_feedback(self):

        """Svuota la notifica verde della cartella allo scadere dei 5 secondi."""

        self._folder_feedback_job = None

        try:

            self.folder_feedback_lbl.configure(text="", text_color="#64748b")

        except tk.TclError:

            pass



    def _set_folder_feedback(self, text: str, color: str = "#64748b", auto_hide_ms: Optional[int] = None):

        if getattr(self, "_folder_feedback_job", None) is not None:

            try:

                self.after_cancel(self._folder_feedback_job)

            except Exception:

                pass

            self._folder_feedback_job = None



        try:

            self.folder_feedback_lbl.configure(text=text, text_color=color)

        except tk.TclError:

            return



        if auto_hide_ms is None:

            auto_hide_ms = 5000 if (color == "#15803d" and text) else 0



        if auto_hide_ms and auto_hide_ms > 0 and text:

            try:

                self._folder_feedback_job = self.after(auto_hide_ms, self._clear_folder_feedback)

            except Exception:

                self._folder_feedback_job = None



    def _on_choose_data_folder(self):

        current = self.config_data.get("last_colletta_folder") or ""

        initial = current if current and os.path.isdir(current) else str(ROOT_DIR)

        folder = filedialog.askdirectory(

            title="Scegli la cartella con i file Excel della Colletta",

            initialdir=initial,

            parent=self,

        )

        if folder:

            self._apply_data_folder(os.path.normpath(folder))



    def _apply_data_folder(self, folder: str, startup: bool = False):

        """Scansiona in background la cartella e aggiorna istantaneamente file e fogli."""

        if self._scanning_folder:

            return

        self._scanning_folder = True

        self.choose_folder_btn.configure(state="disabled")

        self._set_folder_feedback("⏳ Scansione cartella in corso...", "#b45309", auto_hide_ms=0)

        svc = self.colletta_service



        def work(progress):

            return svc.scan_folder(folder, lambda i, n, f: progress(i, n, f))



        def on_progress(i, n, fname):

            self._set_folder_feedback(f"⏳ Lettura {i + 1}/{n}: {fname}", "#b45309", auto_hide_ms=0)



        def on_done(summary, error):

            self._scanning_folder = False

            self.choose_folder_btn.configure(state="normal")

            if error is not None:

                self._set_folder_feedback("❌ Cartella non leggibile", "#b91c1c", auto_hide_ms=0)

                messagebox.showerror(

                    "Cartella Non Valida",

                    f"Impossibile leggere la cartella selezionata:\n{folder}\n\n{error}",

                    parent=self,

                )

                return

            self.config_data["last_colletta_folder"] = folder

            save_app_config(self.config_data)

            self._refresh_file_list(keep_current=startup)

            self._update_folder_pill()



            n_files, n_markets = summary["files"], summary["supermarkets"]

            if n_files == 0:

                self._set_folder_feedback("Nessun file Excel nella cartella", "#b45309", auto_hide_ms=0)

                messagebox.showinfo(

                    "Nessun File Excel",

                    "Nella cartella selezionata non ci sono file Excel (.xlsx / .xls).\n\n"

                    "Seleziona la cartella corretta con '📁 Scegli Cartella Dati...'.",

                    parent=self,

                )

                return

            msg = f"Rilevati {n_files} file Excel e {n_markets} supermercati nella cartella selezionata"

            if summary["errors"]:

                msg += f" ({len(summary['errors'])} non leggibili)"

            self._set_folder_feedback("✓ " + msg, "#15803d", auto_hide_ms=5000)

            if not startup:

                self._show_toast("✓ " + msg)



        BackgroundTask(self, work, on_done, on_progress).start()



    def _refresh_file_list(self, keep_current: bool = False):

        """Ricostruisce il menu File dalla cartella attiva e allinea il foglio supermercato."""

        self.available_files = self.colletta_service.list_available_files()

        labels = [f["display_label"] for f in self.available_files] or ["Nessun file trovato"]

        self.file_menu.configure(values=labels)



        if not self.available_files:

            self.selected_file_var.set("Nessun file trovato")

            self._set_sheet_values([])

            self.supermarket_var.set("")

            return



        cur_name = Path(self.current_excel_path).name.lower() if self.current_excel_path else ""

        cur_dir = str(self.colletta_service.data_dir).lower() if self.colletta_service.data_dir else ""

        match = next(

            (f for f in self.available_files

             if f["file_name"].lower() == cur_name and str(Path(f["file_path"]).parent).lower() == cur_dir

             and str(self.current_excel_path).lower() == f["file_path"].lower()),

            None,

        )

        if match and keep_current:

            self.selected_file_var.set(match["display_label"])

            self._set_sheet_values(self.colletta_service.get_supermarkets_for_file(match["file_path"]))

            return



        pick = next((f for f in self.available_files if "perugia" in f["file_name"].lower()), self.available_files[0])

        self.selected_file_var.set(pick["display_label"])

        self.supermarket_var.set("")

        self._on_file_selected(pick["display_label"])



    # --- SELETTORE FOGLI CON RICERCA RAPIDA ---



    def _set_sheet_values(self, sheets):

        self._all_sheets = list(sheets or [])

        try:

            self.supermarket_combo.configure(values=self._all_sheets)

        except tk.TclError:

            pass



    def _on_sheet_typed(self, event=None):

        """Autocompletamento: filtra i fogli in base al testo digitato; Invio conferma."""

        if event is None:

            return

        if event.keysym in ("Up", "Down", "Left", "Right", "Tab", "Shift_L", "Shift_R",

                            "Control_L", "Control_R", "Alt_L", "Alt_R", "Escape"):

            return

        text = self.supermarket_var.get().strip().lower()

        matches = [x for x in self._all_sheets if text in x.lower()] if text else list(self._all_sheets)

        try:

            self.supermarket_combo.configure(values=matches if matches else self._all_sheets)

        except tk.TclError:

            return

        if event.keysym == "Return":

            exact = next((x for x in self._all_sheets if x.lower() == text), None)

            chosen = exact or (matches[0] if len(matches) == 1 else None)

            if chosen:

                self.supermarket_var.set(chosen)

                self._on_supermarket_selected(chosen)

            elif matches:

                try:

                    self.supermarket_combo._open_dropdown_menu()

                except Exception:

                    pass



    # --- GESTIONE FILE GESTIONALE E CONTROLLO EXCEL COM ---



    def _on_file_selected(self, label: str):

        """Gestisce il cambio file dal menu a tendina in alto."""

        target_fpath = None

        for f in self.available_files:

            if f["display_label"] == label:

                target_fpath = f["file_path"]

                break



        if not target_fpath:

            return



        self.current_excel_path = target_fpath

        self.config_data["last_excel_path"] = target_fpath

        save_app_config(self.config_data)



        self.excel_mgr.set_target_file(target_fpath)



        # Popola la lista supermercati per il nuovo file

        supermarkets = self.colletta_service.get_supermarkets_for_file(target_fpath)

        if not supermarkets:

            self._set_sheet_values([])

        if supermarkets:

            self._set_sheet_values(supermarkets)

            if self.supermarket_var.get() not in supermarkets:

                self.supermarket_var.set("")

                self.sheet_status_lbl.configure(text="Seleziona foglio", text_color="#64748b")



        self._check_excel_status_silent()

        self._hide_suggestion_banner()



    def _on_browse_file(self):

        """Apre un file dialog per selezionare qualunque file Excel personalizzato."""

        chosen = filedialog.askopenfilename(

            title="Seleziona File Gestionale Excel",

            filetypes=[("File Excel", "*.xls *.xlsx"), ("Tutti i file", "*.*")],

            parent=self

        )

        if not chosen:

            return



        self.current_excel_path = chosen

        self.config_data["last_excel_path"] = chosen

        save_app_config(self.config_data)



        self.excel_mgr.set_target_file(chosen)



        fname = Path(chosen).name

        custom_label = f"Personalizzato ({fname})"



        current_vals = list(self.file_menu.cget("values"))

        if custom_label not in current_vals:

            current_vals.append(custom_label)

            self.file_menu.configure(values=current_vals)



        self.selected_file_var.set(custom_label)

        self._check_excel_status_silent()

        self._hide_suggestion_banner()



    def _on_excel_action_clicked(self):

        """Esegue l'azione rapida del pulsante Excel (Apri o Ricarica)."""

        btn_text = self.open_excel_btn.cget("text")

        if "🔄" in btn_text:

            self._manual_check_excel()

        else:

            self._on_open_target_in_excel()



    def _on_open_target_in_excel(self):

        """Apre il file selezionato direttamente in Microsoft Excel."""

        if not self.current_excel_path or not os.path.exists(self.current_excel_path):

            messagebox.showerror(

                "File Non Trovato",

                f"Il file selezionato non esiste sul disco:\n{self.current_excel_path}",

                parent=self

            )

            return



        self.config(cursor="wait")

        self.update()

        try:

            success = self.excel_mgr.open_target_file()

            if success:

                self._check_excel_status_silent()

                fname = self.excel_mgr.get_target_filename()

                messagebox.showinfo(

                    "Excel Aperto",

                    f"✓ File '{fname}' aperto correttamente in Microsoft Excel!",

                    parent=self

                )

            else:

                messagebox.showerror(

                    "Errore Apertura",

                    f"Impossibile aprire il file gestionale in Excel.",

                    parent=self

                )

        except Exception as e:

            messagebox.showerror(

                "Errore Apertura Excel",

                f"Si è verificato un errore durante l'apertura di Excel:\n{e}",

                parent=self

            )

        finally:

            self.config(cursor="")



    def _get_excel_action_tooltip(self) -> str:

        btn_text = self.open_excel_btn.cget("text")

        target_name = self.excel_mgr.get_target_filename()

        if "🔄" in btn_text:

            return f"Ricarica e verifica lo stato del file '{target_name}' in Excel"

        elif "⚡" in btn_text:

            return f"Excel è aperto su un altro file. Clicca per aprire '{target_name}'"

        else:

            return f"Clicca per avviare Excel ed aprire il file '{target_name}'"



    # --- BANNER SUGGERITORE INTELLIGENTE ---



    def _show_suggestion_banner(

        self,

        title: str,

        desc: str,

        btn_text: str,

        action_callback,

        is_warning: bool = False

    ):

        """Mostra il banner di suggerimento o avviso zona nel pannello destro."""

        if is_warning:

            self.suggestion_banner.configure(fg_color="#fffbeb", border_color="#fde68a")

            self.suggestion_icon_lbl.configure(text="⚠️")

            self.suggestion_title_lbl.configure(text=title, text_color="#92400e")

            self.suggestion_desc_lbl.configure(text=desc, text_color="#78350f")

            self.suggestion_action_btn.configure(

                text=btn_text,

                fg_color="#d97706",

                hover_color="#b45309"

            )

        else:

            self.suggestion_banner.configure(fg_color="#eff6ff", border_color="#bfdbfe")

            self.suggestion_icon_lbl.configure(text="💡")

            self.suggestion_title_lbl.configure(text=title, text_color="#1e3a8a")

            self.suggestion_desc_lbl.configure(text=desc, text_color="#334155")

            self.suggestion_action_btn.configure(

                text=btn_text,

                fg_color="#0284c7",

                hover_color="#0369a1"

            )



        self._pending_suggestion_action = action_callback

        # Posiziona il banner prima della griglia punto vendita

        self.suggestion_banner.pack(fill=tk.X, padx=14, pady=(4, 8), before=self.dest_grid)



    def _hide_suggestion_banner(self):

        """Nasconde il banner di suggerimento."""

        self._pending_suggestion_action = None

        if hasattr(self, "suggestion_banner"):

            self.suggestion_banner.pack_forget()



    def _on_apply_suggestion(self):

        """Esegue l'azione collegata al banner di suggerimento."""

        if hasattr(self, "_pending_suggestion_action") and callable(self._pending_suggestion_action):

            action = self._pending_suggestion_action

            self._hide_suggestion_banner()

            action()



    def _switch_to_sheet(self, sheet_name: str):

        """Passa al foglio suggerito nello stesso file."""

        self.supermarket_var.set(sheet_name)

        self._on_supermarket_selected(sheet_name)



    def _switch_to_other_file_and_sheet(self, target_fpath: str, target_sheet: str):

        """Passa al file della zona corretta, lo apre in Excel e seleziona il foglio."""

        self.current_excel_path = target_fpath

        self.config_data["last_excel_path"] = target_fpath

        save_app_config(self.config_data)



        target_fname = Path(target_fpath).name

        for f in self.available_files:

            if f["file_path"].lower() == target_fpath.lower() or f["file_name"].lower() == target_fname.lower():

                self.selected_file_var.set(f["display_label"])

                break



        self.excel_mgr.set_target_file(target_fpath)



        try:

            self.excel_mgr.open_target_file()

        except Exception:

            pass



        self._check_excel_status_silent()

        self.supermarket_var.set(target_sheet)

        self._on_supermarket_selected(target_sheet)



        messagebox.showinfo(

            "Zona Aggiornata",

            f"✓ Passato alla zona corretta:\n\n• File: {target_fname}\n• Foglio: {target_sheet}",

            parent=self

        )



    # --- CONTROLLO EXCEL COM ---



    def _check_excel_status_silent(self) -> bool:

        """Verifica lo stato di Excel con gestione a 3 stati: Connesso, Altro file, Chiuso."""

        try:

            if self.excel_mgr.is_connected():

                sheets = self.excel_mgr.get_data_sheet_names()

                self.excel_led_canvas.itemconfig(self.excel_led, fill="#16a34a")

                self.excel_status_lbl.configure(

                    text=f"Aperto ({len(sheets)} fogli)",

                    text_color="#0f172a"

                )

                self.open_excel_btn.configure(

                    text="🔄",

                    width=26,

                    fg_color="#334155",

                    hover_color="#475569"

                )

                if sheets:

                    self._set_sheet_values(sheets)

                return True



            # Tenta la connessione

            self.excel_mgr.connect()

            sheets = self.excel_mgr.get_data_sheet_names()

            self.excel_led_canvas.itemconfig(self.excel_led, fill="#16a34a")

            self.excel_status_lbl.configure(

                text=f"Aperto ({len(sheets)} fogli)",

                text_color="#0f172a"

            )

            self.open_excel_btn.configure(

                text="🔄",

                width=26,

                fg_color="#334155",

                hover_color="#475569"

            )

            if sheets:

                self._set_sheet_values(sheets)

            return True



        except ExcelWorkbookNotOpenError:

            # Excel è aperto ma questo specifico file non è caricato

            self.excel_led_canvas.itemconfig(self.excel_led, fill="#eab308")

            self.excel_status_lbl.configure(

                text="Excel: Altro file aperto",

                text_color="#a16207"

            )

            self.open_excel_btn.configure(

                text="⚡ Apri questo",

                width=88,

                fg_color="#d97706",

                hover_color="#b45309"

            )

            indexed_sheets = self.colletta_service.get_supermarkets_for_file(self.current_excel_path)

            if indexed_sheets:

                self._set_sheet_values(indexed_sheets)

            return False



        except ExcelNotRunningError:

            # Excel non è in esecuzione

            self.excel_led_canvas.itemconfig(self.excel_led, fill="#ef4444")

            self.excel_status_lbl.configure(

                text="File non aperto",

                text_color="#b91c1c"

            )

            self.open_excel_btn.configure(

                text="📂 Apri in Excel",

                width=100,

                fg_color="#0284c7",

                hover_color="#0369a1"

            )

            indexed_sheets = self.colletta_service.get_supermarkets_for_file(self.current_excel_path)

            if indexed_sheets:

                self._set_sheet_values(indexed_sheets)

            return False



        except Exception as e:

            self.excel_led_canvas.itemconfig(self.excel_led, fill="#ef4444")

            self.excel_status_lbl.configure(text=f"Excel: Errore", text_color="#b91c1c")

            self.open_excel_btn.configure(

                text="📂 Apri in Excel",

                width=100,

                fg_color="#0284c7",

                hover_color="#0369a1"

            )

            return False



    def _manual_check_excel(self):

        if self._check_excel_status_silent():

            messagebox.showinfo(

                "Excel Connesso",

                f"Microsoft Excel è connesso correttamente!\n\n"

                f"• File aperto: {self.excel_mgr.workbook.Name}\n"

                f"• Fogli supermercato: {len(self.excel_mgr.get_data_sheet_names())}",

                parent=self

            )

        else:

            self._show_excel_not_open_dialog()



    def _show_excel_not_open_dialog(self) -> bool:

        dialog = ExcelNotOpenDialogV3(

            self,

            retry_callback=self._check_excel_status_silent,

            cancel_callback=None

        )

        self.wait_window(dialog)

        return dialog.result



    # --- PROVIDER E ACQUISIZIONE ---



    def _on_provider_change(self, value=None):

        disp = (value or self.provider_var.get() or "").strip()

        provider = self._normalize_display_to_provider(disp)

        self.config_data["active_provider"] = provider

        save_app_config(self.config_data)

        self._extraction_generation_id += 1

        self._cancel_active_extraction()

        self._init_extractor()

        self._update_ia_status_ui()

        cur = self._current_item()
        if cur and cur.status == "ready":
            self._show_toast(f"🤖 Modello attivo: {disp} — Premi 'Rianalizza con l'IA' per aggiornare la scheda aperta")
        else:
            self._show_toast(f"🤖 Modello attivo: {disp}")



    def _efficiency_state(self) -> Dict[str, Any]:

        """Stato della modalità Efficienza: quale fase è attiva (Gemini 3.8 gratuito o Xiaomi)."""

        model = "gemini-3.8-flash"

        has_k1 = bool(str(self.config_data.get("gemini_api_key") or "").strip())

        has_k2 = bool(str(self.config_data.get("gemini_api_key_2") or "").strip())

        g1 = self.quota_tracker.get_status("gemini_1", model)

        g2 = self.quota_tracker.get_status("gemini_2", model) if (has_k1 and has_k2) else None

        limit_total = g1["limit_rpd"] * (2 if g2 is not None else 1)

        used_total = g1["requests_today"] + (g2["requests_today"] if g2 is not None else 0)

        if (has_k1 or has_k2) and not g1["is_exhausted"]:

            phase = "gemini_1"

        elif g2 is not None and not g2["is_exhausted"]:

            phase = "gemini_2"

        else:

            phase = "openrouter"

        return {

            "phase": phase,

            "used": min(used_total, limit_total),

            "limit": limit_total,

            "status": g1 if phase != "gemini_2" else g2,

        }



    def _get_ia_tooltip_text(self) -> str:

        disp = self.provider_var.get()

        provider = self._normalize_display_to_provider(disp)



        if provider in ("cascade", "cascade_v2"):

            st = get_pipeline_status(self.config_data)

            cap = st["daily_free_capacity"]

            title = "🚀 Cascata Personalizzabile (3 Sezioni)" if provider == "cascade" else "⚡ Cascata v2 (Gemini 3.8 Primario)"

            return (

                f"{title}:\n"

                f"• Sezione 1 (Google Free): {st['phase_a_desc']}\n"

                f"• Sezione 2 (Pagamento OR): {st['phase_b_desc']}\n"

                f"• Sezione 3 (Fallback Free): {st['phase_c_desc']}\n"

                f"• Arbitro Matematico: {st['phase_d_desc']}\n"

                f"⚡ Capacità Gratuita: {cap} schede/giorno ({st['rpm_capacity']} RPM) · 0,00 €\n"

                f"👉 Clicca per aprire le Impostazioni e la Mappa dei Piani"

            )



        if provider == "efficiency":

            st = self._efficiency_state()

            if st["phase"] == "openrouter":

                sub_desc = "Xiaomi MiMo 2.6 Flash (OpenRouter) — chiamate gratuite Gemini 3.8 esaurite"

            else:

                lbl = "Gemini 1" if st["phase"] == "gemini_1" else "Gemini 2"

                sub_desc = f"{lbl} (Gemini 3.8 Flash) — {st['used']}/{st['limit']} chiamate gratuite usate oggi"

            return (

                f"⚡ Modalità: Efficienza (Gemini 3.8 Flash gratuito ➔ Xiaomi)\n"

                f"📊 Fase Attiva: {sub_desc}\n"

                f"🪙 Strategia: prima le 20 chiamate gratuite di Gemini 3.8 Flash, poi Xiaomi MiMo in automatico.\n"

                f"🕒 Reset Giornaliero: {st['status']['time_until_reset']}\n"

                f"👉 Clicca per aprire il pannello di dettaglio e quote"

            )



        if provider == "gemini_35":

            st = self.quota_tracker.get_status("gemini_1", "gemini-3.5-flash-lite")

            return (

                f"🆓 Modalità Free: Google Gemini 3.5 Flash-Lite (solo chiamate gratuite)\n"

                f"📊 Quota Giornaliera: {st['requests_today']} / {st['limit_rpd']} ({st['percent_remaining']}% residua)\n"

                f"⚡ Frequenza Live: {st['rpm_current']} / {st['limit_rpm']} RPM\n"

                f"🕒 Reset Giornaliero: {st['time_until_reset']}\n"

                f"👉 Clicca per aprire statistiche e quote"

            )



        if provider == "gemini_38":

            st = self.quota_tracker.get_status("gemini_1", "gemini-3.8-flash")

            return (

                f"✨ Modalità Google API: Google Gemini 3.8 Flash\n"

                f"📊 Quota Giornaliera: {st['requests_today']} / {st['limit_rpd']} ({st['percent_remaining']}% residua)\n"

                f"⚡ Frequenza Live: {st['rpm_current']} / {st['limit_rpm']} RPM\n"

                f"🕒 Reset Giornaliero: {st['time_until_reset']}\n"

                f"👉 Clicca per aprire statistiche e quote"

            )



        # Modelli OpenRouter diretti

        disp_name = self.PROVIDER_KEY_MAP.get(provider, disp)

        return (

            f"🤖 Modello: {disp_name}\n"

            f"⚡ Chiamata diretta tramite OpenRouter API\n"

            f"💰 A consumo sul credito OpenRouter (tempo e costo tracciati nella scheda)\n"

            f"👉 Clicca per aprire statistiche e impostazioni"

        )



    def _set_ia_pill(self, text: str, led_color: str, text_color: str = "#0f172a"):

        if hasattr(self, "ia_led_canvas") and hasattr(self, "ia_led"):

            self.ia_led_canvas.itemconfig(self.ia_led, fill=led_color)

        if hasattr(self, "ia_status_lbl"):

            self.ia_status_lbl.configure(text=text, text_color=text_color)



    def _update_ia_status_ui(self):

        """Aggiorna il contatore pulito delle schede fatte nella sessione e oggi."""

        today_total = 0

        try:

            st1 = self.quota_tracker.get_status("gemini_1", "gemini-3.5-flash-lite")

            st2 = self.quota_tracker.get_status("gemini_2", "gemini-3.5-flash-lite")

            today_total = st1.get("requests_today", 0) + st2.get("requests_today", 0)

        except Exception:

            today_total = self.session_cards_written

        today_total = max(today_total, self.session_cards_written)



        counter_text = f"🏆 Schede fatte: {self.session_cards_written}  ·  Oggi: {today_total}"

        self._set_ia_pill(counter_text, "#10b981")



    def _open_ia_stats_dialog(self, event=None):

        return self._open_settings(initial_tab="🚀 Flusso IA & Modelli")



    # --- ACQUISIZIONE: SCANNER SINGOLO, ADF, FILE DA DISCO ---



    def _scan_options(self):

        cfg = self.config_data

        try:

            dpi = int(cfg.get("scanner_dpi", 200))

        except (TypeError, ValueError):

            dpi = 200

        return dpi, False, cfg.get("scanner_default_source", "flatbed")



    def _on_more_options_clicked(self):

        """Apre il menu 'Altre Opzioni' (Carica file da computer, Scansione Piano Vetro, Pulisci tutto F5)."""

        if self._scan_busy:

            return

        try:

            x = self.more_options_btn.winfo_rootx()

            y = self.more_options_btn.winfo_rooty() + self.more_options_btn.winfo_height() + 2

            self.more_options_menu.tk_popup(x, y)

        finally:

            try:

                self.more_options_menu.grab_release()

            except tk.TclError:

                pass



    def _on_select_scanner_dialog(self):

        """Apre il dialog nativo di Windows per selezionare o cambiare scanner."""

        try:

            chosen = ScannerService.select_device_dialog()

            if chosen:

                self._show_toast("✓ Scanner di Windows selezionato")

        except ConnectionError as ce:

            messagebox.showwarning("Nessuno Scanner", str(ce), parent=self)

        except Exception as e:

            messagebox.showerror("Errore Selezione Scanner", str(e), parent=self)



    def _on_acquire_flatbed(self):

        """Scansione da piano vetro (uno o più fogli in sequenza)."""

        if self._scan_busy:

            return

        self._flatbed_sequence()



    def _get_scan_button_tooltip(self) -> str:
        _dpi, _opt, source = self._scan_options()
        if source == "adf":
            return "Scansione veloce (F2): Cassetto superiore ADF (impostazioni)"
        return "Scansione veloce (F2): Piano Vetro (impostazioni)"

    def _update_scan_button_mode(self):
        """Aggiorna il tooltip del pulsante scansione in base alla sorgente predefinita."""
        if hasattr(self, "scan_btn_tooltip") and self.scan_btn_tooltip:
            self.scan_btn_tooltip.text_func_or_text = self._get_scan_button_tooltip

    def _on_acquire_alternative_scanner(self):
        """Scansione alternativa (Shift+F2): se la sorgente predefinita è ADF usa Piano Vetro, e viceversa."""
        if self._scan_busy:
            return
        _dpi, _opt, source = self._scan_options()
        if source == "adf":
            self._on_acquire_flatbed()
        else:
            self._on_acquire_adf()

    def _on_acquire_scanner(self):

        """Scansione veloce (F2). Con sorgente predefinita ADF acquisisce tutti i fogli nel caricatore."""

        if self._scan_busy:

            return

        _dpi, _opt, source = self._scan_options()

        if source == "adf":

            self._start_adf_scan(max_pages=30)

        else:

            self._flatbed_sequence()



    def _flatbed_sequence(self):

        """Scansione sequenziale su piano di vetro: dopo ogni foglio chiede se aggiungerne un altro."""

        _dpi, optimize, _src = self._scan_options()

        acquired: List[str] = []

        self._scan_busy = True

        try:

            while True:

                try:

                    out = ScannerService.acquire_from_scanner(optimize=optimize)

                except ConnectionError as ce:

                    messagebox.showwarning("Scanner non rilevato", str(ce), parent=self)

                    break

                except Exception as e:

                    messagebox.showerror("Errore Scanner", str(e), parent=self)

                    break

                if not out:

                    break  # l'utente ha annullato il dialog dello scanner

                if self.skip_blank_pages_var.get() and ScannerService.is_blank_page(Path(out)):

                    self._show_toast("Foglio vuoto scartato")

                    dlg = ContinueScanDialogV3(self, len(acquired), blank=True)

                    self.wait_window(dlg)

                    if dlg.result != "more":

                        break

                    continue

                acquired.append(str(out))

                dlg = ContinueScanDialogV3(self, len(acquired))

                self.wait_window(dlg)

                if dlg.result != "more":

                    break

        finally:

            self._scan_busy = False

        if acquired:

            self._load_batch(acquired)



    def _on_acquire_adf(self):

        """Scansione multi-foglio dal caricatore automatico (Shift+F2)."""

        self._start_adf_scan(max_pages=30)



    def _start_adf_scan(self, max_pages: int):

        if self._scan_busy:

            return

        try:

            device_id = ScannerService.resolve_device_id()

        except ConnectionError as ce:

            messagebox.showwarning("Scanner non rilevato", str(ce), parent=self)

            return

        except Exception as e:

            messagebox.showerror("Errore Scanner", str(e), parent=self)

            return

        if not device_id:

            return  # selezione annullata



        dpi, optimize, _src = self._scan_options()

        self._scan_busy = True

        self.scan_btn.configure(state="disabled")

        self.more_options_btn.configure(state="disabled")

        holder: Dict[str, Any] = {}



        dlg = ScanProgressDialogV3(

            self, "Scansione ADF in corso", on_cancel=lambda: holder["task"].cancel()

        )



        def work(progress):

            return ScannerService.acquire_pages(

                device_id, SOURCE_FEEDER, max_pages, dpi, optimize,

                skip_blank_pages=self.skip_blank_pages_var.get(),

                progress_cb=lambda n, msg: progress(msg),

                cancel_event=holder["task"].cancel_event,

                on_page_acquired=lambda p: self.after(0, lambda p=p: self._on_streamed_page_acquired(p)),

            )



        def on_progress(msg):

            dlg.set_message(msg)



        def on_done(pages, error):

            dlg.close()

            self._scan_busy = False

            self.scan_btn.configure(state="normal")

            self.more_options_btn.configure(state="normal")

            skipped = ScannerService.last_blank_skipped

            if error is None:

                if pages:

                    loaded_paths = {i.image_path for i in self.batch_items}
                    missing = [str(x) for x in pages if str(x) not in loaded_paths]
                    if missing:
                        self._load_batch(missing)

                    extra = f" ({skipped} bianchi scartati)" if skipped else ""

                    self._show_toast(f"✓ {len(pages)} fogli in coda{extra}")

                elif skipped:

                    messagebox.showinfo(

                        "Solo fogli bianchi",

                        f"Tutti i {skipped} fogli scansionati erano vuoti e sono stati scartati.",

                        parent=self,

                    )

                else:

                    messagebox.showinfo(

                        "Cassetto vuoto",

                        "Nessun foglio rilevato nel cassetto superiore (ADF).\n\n"

                        "Inserisci le schede nel caricatore automatico e ripremi SCANSIONE (F2).",

                        parent=self,

                    )

            elif isinstance(error, FeederNotAvailableError):

                messagebox.showinfo(

                    "ADF non disponibile",

                    f"{error}\n\nPasso automaticamente alla scansione su piano singolo.",

                    parent=self,

                )

                self.after(50, self._flatbed_sequence)

            elif isinstance(error, ScanBatchError):

                if error.pages:

                    self._load_batch([str(x) for x in error.pages])

                    msg = f"{error}\n\nFogli acquisiti e conservati: {len(error.pages)}"

                else:

                    msg = str(error)

                messagebox.showwarning(

                    "Scansione interrotta",

                    msg,

                    parent=self,

                )

            elif isinstance(error, ConnectionError):

                messagebox.showwarning("Scanner non raggiungibile", str(error), parent=self)

            else:

                messagebox.showerror("Errore Scanner", f"Errore durante la scansione:\n{error}", parent=self)



        holder["task"] = BackgroundTask(self, work, on_done, on_progress)

        holder["task"].start()



    def _on_streamed_page_acquired(self, page_path: Path):
        """Streaming pipeline: appena lo scanner salva un foglio, lo inserisce subito in coda e avvia l'IA in background."""
        p_str = str(page_path)
        raw_arch = ScannerService.get_raw_archive(page_path)
        auto_preset = bool(self.config_data.get("auto_preset_assignment", False))
        try:
            ai_path, pid, pname = prepare_scan_for_pipeline(page_path, auto_detect_preset=auto_preset)
            item = BatchItem(
                image_path=p_str,
                preset_id=pid,
                ai_image_path=str(ai_path),
                raw_archive_path=raw_arch,
            )
            self.session_logger.logger.info(
                f"[STREAMING SCAN] Foglio '{page_path.name}': Preparato con Preset {pid} ({pname}) -> {ai_path.name}"
            )
        except Exception as e:
            self.session_logger.logger.warning(f"[STREAMING SCAN] Errore preparazione scansione '{p_str}': {e}")
            item = BatchItem(image_path=p_str, preset_id=0, ai_image_path=p_str, raw_archive_path=raw_arch)

        if all(i.status == "written" for i in self.batch_items):
            self.batch_items = []
            self.batch_index = -1

        is_first_page = (len(self.batch_items) == 0)
        self.batch_items.append(item)

        if is_first_page:
            self.batch_index = 0
            self._refresh_batch_strip(0)
            self._select_batch_item(0)
        else:
            self._refresh_batch_strip(self.batch_index if self.batch_index >= 0 else 0)

        if not self._extracting:
            self._process_next_pending()



    def _on_upload_image(self):

        """Carica una o più immagini / PDF dal disco (selezione multipla)."""

        filetypes = [

            ("Immagini e PDF", "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff *.pdf"),

            ("Tutti i file", "*.*")

        ]

        chosen = filedialog.askopenfilenames(

            title="Seleziona foto o PDF delle schede corriere", filetypes=filetypes, parent=self

        )

        if not chosen:

            return

        self.config(cursor="wait")



        def on_done(result, error):

            self.config(cursor="")

            if error is not None:

                messagebox.showerror("Errore Caricamento", f"Impossibile caricare i file:\n{error}", parent=self)

                return

            ready, warnings = result

            if ScannerService.last_blank_skipped:

                self._show_toast(f"{ScannerService.last_blank_skipped} pagine bianche scartate")

            if warnings:

                messagebox.showwarning(

                    "Alcuni file non sono stati caricati",

                    "\n".join(warnings[:8]) + ("\n..." if len(warnings) > 8 else ""),

                    parent=self,

                )

            if ready:

                self._load_batch([str(x) for x in ready])



        BackgroundTask(self, lambda progress: ScannerService.prepare_uploaded_files(list(chosen)), on_done).start()



    def _on_open_fullscreen(self):

        if self.current_image_path and Path(self.current_image_path).exists():

            if hasattr(self, "image_viewer") and self.image_viewer and self.image_viewer.current_image_path:

                self.image_viewer.open_fullscreen()

                return

            FullScreenViewerDialogV3(self, str(self.current_image_path), initial_image=None)

        else:

            messagebox.showinfo("Nessuna Immagine", "Acquisisci o carica prima una scheda.", parent=self)



    # --- CODA DI LAVORO MULTI-FOGLIO ---



    def _current_item(self) -> Optional[BatchItem]:

        if 0 <= self.batch_index < len(self.batch_items):

            return self.batch_items[self.batch_index]

        return None



    def _assign_item_raw_to_market(self, item: Optional[BatchItem], market_name: Optional[str]) -> Optional[str]:

        """Sposta la copia grezza dello storico in SCANSIONI/<YYYY-MM-DD>/<Supermercato>/ (o _non_riconosciuto/)."""

        if item is None:

            return None

        raw_arch = getattr(item, "raw_archive_path", None)

        if not raw_arch and getattr(item, "image_path", None):

            raw_arch = ScannerService.get_raw_archive(item.image_path)

        if not raw_arch:

            return None

        try:

            clean_target = (market_name or "").strip()

            moved = get_history_service().assign_raw_scan_to_supermarket(raw_arch, clean_target)

            if moved:

                moved_str = str(moved)

                item.raw_archive_path = moved_str

                item.raw_assigned_market = clean_target or "_non_riconosciuto"

                if getattr(item, "image_path", None):

                    ScannerService.register_raw_archive(item.image_path, moved)

                return moved_str

        except Exception as e:

            try:

                self.session_logger.logger.warning(f"[STORICO] Errore spostamento copia grezza '{raw_arch}': {e}")

            except Exception:

                pass

        return None



    def _load_batch(self, paths: List[str]):

        """Aggiunge i fogli alla coda (o la sostituisce se tutti i fogli precedenti sono già scritti)."""

        if not paths:

            return

        # Prepara ciascuna scheda: raddrizzamento orientamento, auto-rilevamento preset, e copia ottimizzata IA

        new_items: List[BatchItem] = []

        auto_preset = bool(self.config_data.get("auto_preset_assignment", False))

        for p in paths:

            path_obj = Path(p)

            raw_arch = ScannerService.get_raw_archive(path_obj)

            try:

                ai_path, pid, pname = prepare_scan_for_pipeline(path_obj, auto_detect_preset=auto_preset)

                new_items.append(

                    BatchItem(

                        image_path=str(p),

                        preset_id=pid,

                        ai_image_path=str(ai_path),

                        raw_archive_path=raw_arch,

                    )

                )

                self.session_logger.logger.info(

                    f"[PRESET] Scheda '{path_obj.name}': Raddrizzata e preparata con Preset {pid} ({pname}) -> {ai_path.name}"

                )

            except Exception as e:

                self.session_logger.logger.warning(

                    f"[PRESET] Errore preparazione scansione '{p}': {e}"

                )

                new_items.append(BatchItem(image_path=str(p), preset_id=0, ai_image_path=str(p), raw_archive_path=raw_arch))



        self._commit_current_item()

        if all(i.status == "written" for i in self.batch_items):

            self.batch_items = []

        first_new = len(self.batch_items)

        self.batch_items.extend(new_items)

        self.batch_index = -1

        self._refresh_batch_strip(first_new)

        self._select_batch_item(first_new)

        self._process_next_pending()



    def _process_image_file(self, image_path: str):

        """Compatibilità: carica un singolo file come lotto di un foglio."""

        self._load_batch([image_path])



    def _hide_batch_strip(self):

        if hasattr(self, "batch_strip") and getattr(self, "_strip_visible", False):

            try:

                self.batch_strip.pack_forget()

            except Exception:

                pass

            self._strip_visible = False



    def _refresh_batch_strip(self, selected: int = 0):

        # Mostra la strip di coda solo se ci sono almeno 2 fogli nel lotto (massimizza spazio)

        has_items = len(self.batch_items) > 1

        if has_items:

            self.batch_strip.set_items(self.batch_items, selected)

            if not self._strip_visible:

                self.batch_strip.pack(fill=tk.X, pady=(0, 4), before=self.image_viewer)

                self._strip_visible = True

        elif self._strip_visible:

            self._hide_batch_strip()



    def _refresh_item_badge(self, item: BatchItem):

        if self._strip_visible and item in self.batch_items:

            self.batch_strip.refresh_item(self.batch_items.index(item))



    def _commit_current_item(self):

        """Salva nello slot corrente lo stato della scheda (dati modificati, foglio, viaggio)."""

        item = self._current_item()

        if item is None:

            return

        if hasattr(self, "image_viewer"):

            item.preset_id = self.image_viewer.get_active_preset()

        if self._inline_entry:

            self._commit_inline_cell_edit()

        if self.is_edit_mode:

            self._on_save_edit_mode()

        if self.current_processed_card is not None:

            item.card = self.current_processed_card



    def _select_batch_item(self, idx: int):

        if not (0 <= idx < len(self.batch_items)):

            return

        if idx != self.batch_index:

            self._commit_current_item()

        self.batch_index = idx

        item = self.batch_items[idx]

        # Se la scheda non è ancora pronta (pending/processing), svuota la selezione

        if item.card is None:

            try:

                self.tree.selection_remove(*self.tree.selection())

                self.tree.focus("")

            except Exception:

                pass

        self._reset_card_fields()

        self.current_image_path = item.image_path

        self.image_viewer.load_image(item.image_path, initial_preset=getattr(item, "preset_id", 0))

        if self._strip_visible:

            self.batch_strip.set_selected(idx)



        if item.card is not None:

            self.current_processed_card = item.card

            self._populate_ui_with_card(item.card, preserve_selection=False)

        elif item.raw is not None:

            try:

                self._apply_raw_extraction(item.raw)

                item.card = self.current_processed_card

            except Exception as e:

                self.alerts_title_lbl.configure(text=f"⚠️ ERRORE DATI: {str(e)[:70]}", text_color="#b91c1c")

        elif item.status == "error":

            self.alerts_title_lbl.configure(text=f"⚠️ ERRORE IA: {item.error[:70]}", text_color="#b91c1c")

        elif item.status == "processing":
            self.alerts_title_lbl.configure(text="⏳ ELABORAZIONE IA IN CORSO...", text_color="#b45309")
            self.alerts_text.config(state=tk.NORMAL)
            self.alerts_text.delete("1.0", tk.END)
            from datetime import datetime
            now_str = datetime.now().strftime("%H:%M:%S")
            self.alerts_text.insert(tk.END, f"[{now_str}] 🚀 Elaborazione del foglio #{idx+1} in corso...\n")
            self.alerts_text.config(state=tk.DISABLED)
        else:
            self.alerts_title_lbl.configure(text=f"⏳ In coda per elaborazione (foglio {idx+1} di {len(self.batch_items)})", text_color="#64748b")
            self.alerts_text.config(state=tk.NORMAL)
            self.alerts_text.delete("1.0", tk.END)
            self.alerts_text.insert(tk.END, f"Il foglio #{idx+1} è in coda di elaborazione.\nVerrà analizzato automaticamente dall'IA appena il precedente sarà completato.\n")
            self.alerts_text.config(state=tk.DISABLED)

        self._update_write_button_state()



    def _on_batch_prev(self):

        if self.batch_index > 0:

            self._select_batch_item(self.batch_index - 1)



    def _on_batch_next(self):

        if self.batch_index < len(self.batch_items) - 1:

            self._select_batch_item(self.batch_index + 1)



    def _on_extraction_progress(self, msg: str):
        """Aggiorna in tempo reale il box e l'etichetta di stato durante l'elaborazione IA."""
        if not msg:
            return
        try:
            self.alerts_title_lbl.configure(text=f"⏳ {msg[:60]}", text_color="#b45309")
            self.alerts_text.config(state=tk.NORMAL)
            self.alerts_text.config(bg="#f8fafc")
            from datetime import datetime
            now_str = datetime.now().strftime("%H:%M:%S")
            self.alerts_text.insert(tk.END, f"[{now_str}] {msg}\n")
            self.alerts_text.see(tk.END)
            self.alerts_text.config(state=tk.DISABLED)
        except Exception:
            pass

    def _process_next_pending(self):

        """Elabora in background (un foglio alla volta) i fogli in attesa, partendo da quello visualizzato."""

        if self._extracting:

            return

        cur = self._current_item()

        target = cur if (cur and cur.status == "pending") else next(

            (i for i in self.batch_items if i.status == "pending"), None

        )

        if target is None:

            return

        if not self.active_extractor:

            self._init_extractor()

        extractor = self.active_extractor

        path = target.image_path

        use_strong = bool(getattr(target, "force_strong", False))

        target.force_strong = False

        target.status = "processing"

        self._refresh_item_badge(target)

        self._extracting = True

        self._extraction_generation_id += 1

        current_gen = self._extraction_generation_id

        sent_path_holder = {"path": str(path)}

        def work(progress):

            try:

                rotated = ScannerService.ensure_upright_orientation(Path(path))

                need_prep = (

                    rotated

                    or not getattr(target, "ai_image_path", None)

                    or not Path(target.ai_image_path).exists()

                    or target.ai_image_path == str(path)

                )

                if need_prep:

                    auto_preset = bool(self.config_data.get("auto_preset_assignment", False))

                    ai_path, pid, pname = prepare_scan_for_pipeline(Path(path), auto_detect_preset=auto_preset)

                    target.ai_image_path = str(ai_path)

                    target.preset_id = pid

                    if target is self._current_item():

                        self.after(0, lambda: self.image_viewer.load_image(path, initial_preset=pid))

                    if self._strip_visible and target in self.batch_items:

                        idx = self.batch_items.index(target)

                        self.after(0, lambda i=idx: self.batch_strip.refresh_item(i, reload_image=True))

            except Exception as e:

                try:

                    self.session_logger.logger.warning(f"Avviso auto-orientamento pre-IA: {e}")

                except Exception:

                    pass



            image_to_send = target.ai_image_path or path
            sent_path_holder["path"] = str(image_to_send)
            progress("📡 Invio immagine al motore IA...")

            if use_strong and hasattr(extractor, "extract_with_strong_model"):

                return extractor.extract_with_strong_model(image_to_send, status_callback=progress)

            return extractor.extract_from_image(image_to_send, status_callback=progress)



        def on_done(raw, error):

            if current_gen != self._extraction_generation_id:

                return

            try:

                self._extracting = False

                self._active_extraction_task = None

                self._update_ia_status_ui()

                if target in self.batch_items:

                    raw_err = getattr(raw, "note_o_anomalie", "") or ""

                    is_extractor_err = bool(

                        raw_err and any(k in raw_err.lower() for k in ("errore openrouter", "errore gemini", "errore chiamata", "timeout"))

                        and not getattr(raw, "righe_scatole", None)

                        and not getattr(raw, "supermercato", "")

                    )



                    if error is not None or raw is None or is_extractor_err:

                        target.status = "error"

                        target.error = str(error or raw_err or "risposta vuota")

                    else:

                        # Se l'IA ha rilevato che la scheda era capovolta o se l'estrattore ha ruotato la copia AI su disco, allinea work image, .raw.jpg e cache visore

                        was_rotated_by_ext = bool(getattr(raw, "was_rotated_180", False))
                        detected_upside_down = bool(getattr(raw, "scheda_capovolta", False))

                        if was_rotated_by_ext or detected_upside_down:

                            try:

                                work_p = Path(target.image_path)
                                sent_p = Path(sent_path_holder["path"])
                                try:
                                    same_file = (work_p.resolve() == sent_p.resolve())
                                except Exception:
                                    same_file = (os.path.abspath(str(work_p)) == os.path.abspath(str(sent_p)))

                                if (was_rotated_by_ext and not same_file) or (detected_upside_down and not was_rotated_by_ext):

                                    ScannerService.rotate_image_file_180(work_p)

                                if target.preset_id is not None:

                                    target.ai_image_path = str(create_ai_optimized_copy(work_p, target.preset_id))

                                if hasattr(self, "image_viewer") and self.image_viewer:
                                    for cache_key in {str(work_p), str(target.image_path)}:
                                        self.image_viewer._original_cache.pop(cache_key, None)
                                        self.image_viewer._processed_cache.pop(cache_key, None)
                                        self.image_viewer._preset_state_cache.pop(cache_key, None)

                                if target is self._current_item():

                                    self.image_viewer.load_image(target.image_path, initial_preset=target.preset_id)

                                if self._strip_visible and target in self.batch_items:

                                    self.batch_strip.refresh_item(self.batch_items.index(target), reload_image=True)

                            except Exception as e:

                                try:

                                    self.session_logger.logger.warning(f"Avviso rotazione 180 da IA: {e}")

                                except Exception:

                                    pass

                        target.raw = raw

                        target.status = "ready"

                        # Sposta subito la scansione grezza da _da_assegnare alla cartella del supermercato rilevato
                        try:
                            raw_mkt = (getattr(raw, "supermercato", "") or "").strip()
                            resolved_mkt = ""
                            if raw_mkt:
                                cur_fn = self.excel_mgr.get_target_filename() if hasattr(self, "excel_mgr") else None
                                m_list = self.colletta_service.find_matches(raw_mkt, current_file=cur_fn, top_n=1)
                                resolved_mkt = m_list[0]["sheet_name"] if m_list else raw_mkt
                            self._assign_item_raw_to_market(target, resolved_mkt)
                        except Exception:
                            pass

                    self._refresh_item_badge(target)

                    if target is self._current_item():

                        if target.status == "ready":

                            try:

                                self._apply_raw_extraction(target.raw)

                                target.card = self.current_processed_card

                            except Exception as ex:

                                self.alerts_title_lbl.configure(

                                    text=f"⚠️ ERRORE DATI: {str(ex)[:70]}", text_color="#b91c1c"

                                )

                        else:

                            self.alerts_title_lbl.configure(

                                text=f"⚠️ ERRORE IA: {target.error[:70]}", text_color="#b91c1c"

                            )

                            messagebox.showerror(

                                "Errore Elaborazione",

                                f"Errore durante l'elaborazione dell'immagine:\n{target.error}\n\n"

                                f"Usa 'Rielabora' o premi F5 per resettare.",

                                parent=self,

                            )

            finally:

                self._extracting = False

                self._active_extraction_task = None

                self._process_next_pending()



        if target is self._current_item():
            try:
                self.alerts_title_lbl.configure(text="⏳ ELABORAZIONE IA IN CORSO...", text_color="#b45309")
                self.alerts_text.config(state=tk.NORMAL)
                self.alerts_text.delete("1.0", tk.END)
                from datetime import datetime
                now_str = datetime.now().strftime("%H:%M:%S")
                self.alerts_text.insert(tk.END, f"[{now_str}] 🚀 Avvio analisi del foglio...\n")
                self.alerts_text.config(state=tk.DISABLED)
            except Exception:
                pass

        task = BackgroundTask(self, work, on_done, on_progress=self._on_extraction_progress)

        self._active_extraction_task = task

        task.start()



    def _on_reprocess_image(self):

        item = self._current_item()

        if item is None:

            return

        # Annulla eventuale estrazione in sospeso/bloccata

        self._cancel_active_extraction()



        # Rileva il preset attivo attualmente nel visore e genera la nuova copia temporanea per l'IA

        active_preset = self.image_viewer.get_active_preset()

        new_ai_path = self.image_viewer.save_temp_ai_copy()

        if not new_ai_path:

            try:

                new_ai_path = create_ai_optimized_copy(Path(item.image_path), active_preset)

            except Exception:

                new_ai_path = Path(item.image_path)

        item.ai_image_path = str(new_ai_path)

        item.preset_id = active_preset



        self.session_logger.logger.info(

            f"[REPROCESS] Rianalisi scheda '{Path(item.image_path).name}' con Preset {active_preset} -> {new_ai_path}"

        )



        item.raw = None

        item.card = None

        item.error = ""

        item.status = "pending"

        self._refresh_item_badge(item)

        self._reset_card_fields()

        self.alerts_title_lbl.configure(text="⏳ ELABORAZIONE IA IN CORSO...", text_color="#b45309")

        self._process_next_pending()



    def _on_reprocess_sonnet(self):

        """Rianalizza la scheda corrente con il modello forte (Claude Sonnet), previa conferma."""

        item = self._current_item()

        if item is None:

            return

        if not self.active_extractor:

            self._init_extractor()

        if not hasattr(self.active_extractor, "extract_with_strong_model") or not getattr(self.active_extractor, "or_key", ""):

            messagebox.showwarning(

                "Modello forte non disponibile",

                "Per usare Claude Sonnet serve la modalità 🚀 Cascata con una chiave OpenRouter configurata.",

                parent=self,

            )

            return

        if not messagebox.askyesno(

            "Rianalisi con Claude Sonnet",

            "Vuoi rianalizzare questa scheda con Claude Sonnet (massima precisione)?\n\n"

            "Costo stimato: circa 0,03–0,05 € dal credito OpenRouter.",

            parent=self,

        ):

            return

        item.force_strong = True

        self._on_reprocess_image()



    def _on_excel_source_menu(self):

        try:

            x = self.choose_folder_btn.winfo_rootx()

            y = self.choose_folder_btn.winfo_rooty() + self.choose_folder_btn.winfo_height() + 2

            self.excel_source_menu.tk_popup(x, y)

        finally:

            try:

                self.excel_source_menu.grab_release()

            except Exception:

                pass



    def _discard_current_item(self):

        idx = self.batch_index

        if not (0 <= idx < len(self.batch_items)):

            return

        self.batch_items.pop(idx)

        self.current_processed_card = None

        self.batch_index = -1

        if not self.batch_items:

            self._clear_batch()

            return

        new_idx = min(idx, len(self.batch_items) - 1)

        self._refresh_batch_strip(new_idx)

        self._select_batch_item(new_idx)



    def _clear_batch(self):

        self.batch_items = []

        self.batch_index = -1

        self._refresh_batch_strip()

        self._on_reset_form()



    def _after_write_success(self, sheet_name: str, transport: str, result: Dict[str, Any]):

        """Gestisce il dopo-scrittura: nel lotto passa al foglio successivo senza dialog bloccanti."""

        self.session_cards_written += 1

        self._update_ia_status_ui()

        item = self._current_item()

        if item is not None:

            item.status = "written"

            item.card = self.current_processed_card

            self._refresh_item_badge(item)



        if len(self.batch_items) <= 1:

            cells_cnt = result.get('written_cells_count', 0)

            self._show_toast(f"📗 Scheda registrata in Excel: {sheet_name} → {transport} ({cells_cnt} celle) · File salvato")

            self._clear_batch()

            return



        remaining = [i for i, it in enumerate(self.batch_items) if it.status != "written"]

        self._show_toast(f"📗 Scritto & Archiviato nello Storico: {sheet_name} → {transport}  ({len(self.batch_items) - len(remaining)}/{len(self.batch_items)})")

        if remaining:

            after = [i for i in remaining if i > self.batch_index]

            self._select_batch_item(after[0] if after else remaining[0])

        else:

            messagebox.showinfo(

                "Lotto completato",

                f"✓ Tutti i {len(self.batch_items)} fogli del lotto sono stati scritti su Excel.",

                parent=self

            )

            self._clear_batch()



    def _show_toast(self, text: str, ms: int = 5000):

        """Messaggio non bloccante nel footer."""

        try:

            self.toast_lbl.configure(text=text)

            if self._toast_job:

                self.after_cancel(self._toast_job)

            self._toast_job = self.after(ms, lambda: self.toast_lbl.configure(text=""))

        except tk.TclError:

            pass



    def _apply_raw_extraction(self, raw: RawCardExtraction):

        self._hide_suggestion_banner()

        detected_supermarket = (raw.supermercato or "").strip()

        current_fname = self.excel_mgr.get_target_filename()



        # 1. Ricerca corrispondenze tramite CollettaService

        matches = self.colletta_service.find_matches(

            detected_supermarket,

            current_file=current_fname,

            top_n=3

        )



        matched_sheet = None

        next_slot = "A1"

        filled_transports = []

        best_match = matches[0] if matches else None



        # Verifica stato Excel silenzioso

        excel_ready = self._check_excel_status_silent()



        if best_match:

            if best_match["is_current_file"]:

                matched_sheet = best_match["sheet_name"]

                current_selected = self.supermarket_var.get()

                if current_selected and current_selected.lower() != matched_sheet.lower():

                    self._show_suggestion_banner(

                        title=f"Foglio Rilevato: {matched_sheet}",

                        desc=f"La scheda rilevata ('{detected_supermarket}') appartiene al foglio '{matched_sheet}' ({int(best_match['score']*100)}%). Vuoi cambiare foglio?",

                        btn_text=f"Passa a '{matched_sheet}'",

                        action_callback=lambda s=matched_sheet: self._switch_to_sheet(s),

                        is_warning=False

                    )

                else:

                    self.supermarket_var.set(matched_sheet)

            else:

                other_file = best_match["file_name"]

                other_zone = best_match["zone"]

                other_sheet = best_match["sheet_name"]

                score_pct = int(best_match["score"] * 100)



                self._show_suggestion_banner(

                    title=f"Scheda di un'altra zona: {other_zone}",

                    desc=(

                        f"La scheda rilevata ('{detected_supermarket}') appartiene alla zona '{other_zone}'!\n"

                        f"File consigliato: {other_file}\n"

                        f"Foglio: '{other_sheet}' (Affidabilità: {score_pct}%)\n"

                        f"Attualmente stai lavorando su '{current_fname}'."

                    ),

                    btn_text=f"🔄 Passa a {other_zone} ed apri",

                    action_callback=lambda f=best_match["file_path"], s=other_sheet: self._switch_to_other_file_and_sheet(f, s),

                    is_warning=True

                )

        elif detected_supermarket:

            self._show_suggestion_banner(

                title="Punto Vendita non riconosciuto",

                desc=f"Insegna rilevata: '{detected_supermarket}'. Nessuna corrispondenza esatta trovata: verificare o selezionare manualmente il foglio.",

                btn_text="Nascondi",

                action_callback=self._hide_suggestion_banner,

                is_warning=True

            )



        active_sheet = matched_sheet or self.supermarket_var.get()

        if active_sheet and excel_ready:

            try:

                status = self.excel_mgr.get_sheet_status(active_sheet, force_refresh=True)

                next_slot = status.get("next_available_slot") or "A1"

                filled_transports = status.get("filled_transports", [])

            except Exception:

                pass



        processed = CardValidator.validate(

            raw,

            next_available_slot=next_slot,

            existing_filled_rows=filled_transports

        )



        if active_sheet:

            processed.supermercato_confermato = active_sheet

        try:

            arch_market = active_sheet or (best_match["sheet_name"] if best_match else detected_supermarket)

            self._assign_item_raw_to_market(self._current_item(), arch_market)

        except Exception:

            pass

        self.current_processed_card = processed

        self.override_var.set(False)

        self._populate_ui_with_card(processed)



    def _populate_ui_with_card(self, card: ProcessedCard, preserve_selection: bool = False):

        # 1. Supermercato

        if card.supermercato_confermato:

            self.supermarket_var.set(card.supermercato_confermato)

            self.sheet_status_lbl.configure(text="✓ Foglio Agganciato", text_color="#16a34a")

        else:

            self.supermarket_var.set(card.supermercato_rilevato)

            self.sheet_status_lbl.configure(text="⚠️ Verificare Foglio", text_color="#dc2626")



        # 2. Numero Scheda / Trasporto & Segnalazione Intelligente da Excel

        active_sheet = card.supermercato_confermato or self.supermarket_var.get().strip()

        rec = None

        if active_sheet and self._check_excel_status_silent():

            try:

                rec = self.excel_mgr.analyze_transport_recommendation(

                    sheet_name=active_sheet,

                    current_card_kg=card.totale_kg_calcolati,

                    current_card_boxes=card.totale_scatole_calcolate,

                    detected_transport=card.numero_scheda_rilevato

                )

            except Exception as e:

                print(f"Avviso analisi trasporto Excel: {e}")



        if rec:

            self.excel_summary_lbl.configure(text=rec["excel_summary_text"])

            slot = rec["recommended_slot"]

            self.transport_var.set(slot)

            card.numero_scheda_risolto = slot



            if rec.get("is_collision"):

                self.transport_status_lbl.configure(text=rec["status_message"], text_color="#dc2626")

            elif "suggerit" in rec.get("status_message", "").lower():

                self.transport_status_lbl.configure(text=rec["status_message"], text_color="#0284c7")

            else:

                self.transport_status_lbl.configure(text=rec["status_message"], text_color="#16a34a")



            if rec.get("duplicate_warning"):

                dup_msg = rec["duplicate_warning"]

                if not any(f.field == "numero_scheda" and f.message == dup_msg for f in card.flags):

                    card.flags.append(

                        ValidationFlag(

                            severity=FlagSeverity.WARNING,

                            field="numero_scheda",

                            message=dup_msg

                        )

                    )

        else:

            self.excel_summary_lbl.configure(text="")

            resolved = card.numero_scheda_risolto

            if resolved:

                self.transport_var.set(resolved)

                self.transport_status_lbl.configure(text=f"Assegnato: {resolved}", text_color="#16a34a")

            elif card.numero_scheda_rilevato == "ILLEGGIBILE":

                self.transport_var.set("")

                self.transport_status_lbl.configure(text="❌ ILLEGGIBILE: Scegli manuale", text_color="#dc2626")

            else:

                self.transport_var.set("")

                self.transport_status_lbl.configure(text="Selezionare riga", text_color="#64748b")



        # 3. Tabella 13 Categorie ad Accordion con Feedback Inline

        self._populate_categories_table(card, preserve_selection=preserve_selection)



        # 4. Totale Complessivo

        self.calc_totals_lbl.configure(

            text=f"Totale: {card.totale_scatole_calcolate} scatole  —  {card.totale_kg_calcolati:.1f} Kg"

        )

        self.edit_btn.configure(state="normal")



        # 5. Box Allarmi

        self._update_alerts_box(card)

        self.override_var.set(card.autorizzato_da_operatore)

        self._update_write_button_state()



    def _update_alerts_box(self, card: ProcessedCard):

        self.alerts_text.config(state=tk.NORMAL)

        self.alerts_text.delete("1.0", tk.END)



        has_red = card.ha_flag_rossi

        has_warning = any(f.severity == FlagSeverity.WARNING for f in card.flags)



        if has_red:

            self.alerts_title_lbl.configure(text="STATO: ANOMALIE RILEVATE (FLAG ROSSO)", text_color="#dc2626")

            self.alerts_text.config(bg="#fef2f2")

        elif has_warning:

            self.alerts_title_lbl.configure(text="STATO: AVVISI (VERIFICARE)", text_color="#d97706")

            self.alerts_text.config(bg="#fffbeb")

        else:

            self.alerts_title_lbl.configure(text="STATO: DATI COERENTI (VERDE)", text_color="#16a34a")

            self.alerts_text.config(bg="#f0fdf4")



        # Se non ci sono anomalie rosse o avvisi gialli da verificare:

        if not has_red and not has_warning:

            self.alerts_text.insert(tk.END, "✓ Nessuna discrepanza. Dati coerenti e pronti per la scrittura su Excel.\n")

            for f in card.flags:

                if f.severity == FlagSeverity.GREEN:

                    self.alerts_text.insert(tk.END, f"🟢 {f.message}\n")

        else:

            for f in card.flags:

                prefix = "🔴 " if f.severity == FlagSeverity.RED else ("🟡 " if f.severity == FlagSeverity.WARNING else "🟢 ")

                self.alerts_text.insert(tk.END, f"{prefix}{f.message}\n")



        self.alerts_text.config(state=tk.DISABLED)

        self._fit_alerts_height()



    def _fit_alerts_height(self):

        """Adatta dinamicamente l'altezza del riquadro avvisi al testo (tra min e max righe)."""

        try:

            # Misura le righe visualizzate (con a capo automatico) alla massima altezza consentita

            self.alerts_text.config(height=ALERTS_MAX_LINES)

            self.alerts_text.update_idletasks()

            res = self.alerts_text.count("1.0", "end-1c", "displaylines")

            n_lines = (res[0] if isinstance(res, tuple) else res) or 0

            n_lines = max(1, int(n_lines))

        except Exception:

            n_lines = int(self.alerts_text.index("end-1c").split(".")[0])

        height = max(ALERTS_MIN_LINES, min(ALERTS_MAX_LINES, n_lines))

        self.alerts_text.config(height=height)

        # Scrollbar visibile solo se il testo eccede l'altezza massima

        if n_lines > ALERTS_MAX_LINES:

            if not self.alerts_scroll.winfo_ismapped():

                self.alerts_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        elif self.alerts_scroll.winfo_ismapped():

            self.alerts_scroll.pack_forget()



    def _update_cancel_button_state(self):

        """Aggiorna visibilità/testo del pulsante Scarta Foglio / Annulla Scheda nel footer."""

        if not hasattr(self, "reset_btn"):

            return

        has_active = bool(self.current_processed_card or self.current_image_path or self.batch_items)

        if not has_active:

            self.reset_btn.configure(

                state="disabled",

                text="✖️  Annulla Scheda (Esc)",

                fg_color="#f1f5f9",

                text_color="#94a3b8",

            )

            return

        lbl = "✖️  Scarta Foglio (Esc)" if len(self.batch_items) > 1 else "✖️  Annulla Scheda (Esc)"

        self.reset_btn.configure(

            state="normal",

            text=lbl,

            fg_color="#e2e8f0",

            text_color="#0f172a",

            hover_color="#cbd5e1",

        )



    def _update_write_button_state(self):

        self._update_cancel_button_state()

        if not self.current_processed_card:

            self.write_btn.configure(

                state="disabled",

                fg_color="#cbd5e1",

                text_color="#64748b"

            )

            self.override_check.configure(

                state="normal",

                cursor="hand2",

                text_color="#0f172a",

                text="Ho verificato i dati ed autorizzo la scrittura su Excel"

            )

            return



        card = self.current_processed_card

        has_red = card.ha_flag_rossi

        has_warning = any(f.severity == FlagSeverity.WARNING for f in card.flags)

        has_alerts = has_red or has_warning

        has_slot = bool(self.transport_var.get())



        cur_item = self._current_item()

        if cur_item is not None and cur_item.status == "written":

            self.write_btn.configure(state="disabled", fg_color="#cbd5e1", text_color="#64748b")

            self.override_check.configure(

                state="disabled", text="📗 Foglio già scritto su Excel", text_color="#0369a1"

            )

            return



        if has_alerts:

            # Presenza di anomalie o avvisi: la casella evidenzia che l'autorizzazione è richiesta

            self.override_check.configure(

                state="normal",

                cursor="hand2",

                text_color="#dc2626",

                text="Ho verificato le discrepanze segnalate ed autorizzo la scrittura su Excel"

            )

        else:

            # Stato verde perfetto: nessuna anomalia o avviso

            self.override_check.configure(

                state="normal",

                cursor="hand2",

                text_color="#0f172a",

                text="Ho verificato i dati ed autorizzo la scrittura su Excel"

            )



        # Il pulsante SCRIVI SU EXCEL E SALVA si attiva SOLO se l'operatore ha spuntato la casella ed è assegnato lo slot!

        if self.override_var.get() and has_slot:

            self.write_btn.configure(

                state="normal",

                fg_color="#16a34a",

                text_color="#ffffff",

                hover_color="#15803d"

            )

        else:

            self.write_btn.configure(

                state="disabled",

                fg_color="#cbd5e1",

                text_color="#64748b"

            )



    def _on_override_toggle(self):

        if self.current_processed_card:

            self.current_processed_card.autorizzato_da_operatore = self.override_var.get()

        self._update_write_button_state()



    def _on_supermarket_selected(self, value=None):

        sheet = self.supermarket_var.get().strip()

        if not sheet:

            self.sheet_status_lbl.configure(text="", text_color="#64748b")

            self.excel_summary_lbl.configure(text="")

            return



        if self.current_processed_card:

            self.current_processed_card.supermercato_confermato = sheet

        self._assign_item_raw_to_market(self._current_item(), sheet)

        if self._check_excel_status_silent():

            try:

                card = self.current_processed_card

                c_kg = card.totale_kg_calcolati if card else None

                c_bx = card.totale_scatole_calcolate if card else None

                c_det = card.numero_scheda_rilevato if card else None

                rec = self.excel_mgr.analyze_transport_recommendation(

                    sheet_name=sheet,

                    current_card_kg=c_kg,

                    current_card_boxes=c_bx,

                    detected_transport=c_det

                )

                filled = rec.get("filled_transports", [])

                self.sheet_status_lbl.configure(

                    text=f"✓ Agganciato ({len(filled)} viaggi)",

                    text_color="#16a34a"

                )

                self.excel_summary_lbl.configure(text=rec.get("excel_summary_text", ""))

                slot = rec.get("recommended_slot")

                if slot:

                    self.transport_var.set(slot)

                    if card:

                        card.numero_scheda_risolto = slot

                if rec.get("is_collision"):

                    self.transport_status_lbl.configure(text=rec["status_message"], text_color="#dc2626")

                elif "suggerit" in rec.get("status_message", "").lower():

                    self.transport_status_lbl.configure(text=rec["status_message"], text_color="#0284c7")

                else:

                    self.transport_status_lbl.configure(text=rec["status_message"], text_color="#16a34a")

            except Exception as e:

                self.sheet_status_lbl.configure(text="⚠️ Errore lettura", text_color="#dc2626")

                print(f"Errore lettura stato Excel supermercato: {e}")

        else:

            self.sheet_status_lbl.configure(text="✓ Selezionato (Excel non connesso)", text_color="#d97706")

            self.excel_summary_lbl.configure(text="")



        self._update_write_button_state()



    def _on_transport_selected(self, value=None):

        slot = self.transport_var.get()

        active_sheet = self.supermarket_var.get().strip()



        # Verifica in tempo reale con Excel se lo slot selezionato è già occupato

        if active_sheet and self._check_excel_status_silent():

            try:

                st = self.excel_mgr.get_sheet_status(active_sheet)

                filled = st.get("filled_transports", [])

                details = st.get("transports_detail", {})

                if slot in filled:

                    d = details.get(slot, {})

                    self.transport_status_lbl.configure(

                        text=f"⚠️ {slot} GIÀ COMPILATO ({d.get('summary', '')})!",

                        text_color="#dc2626"

                    )

                else:

                    self.transport_status_lbl.configure(

                        text=f"✓ {slot} Libero",

                        text_color="#16a34a"

                    )

            except Exception:

                pass



        if self.current_processed_card:

            self.current_processed_card.numero_scheda_risolto = slot

            self.current_processed_card.flags = [

                f for f in self.current_processed_card.flags if f.field != "numero_scheda"

            ]

            self.current_processed_card.ha_flag_rossi = any(

                f.severity == FlagSeverity.RED for f in self.current_processed_card.flags

            )

            self._update_alerts_box(self.current_processed_card)

        self._update_write_button_state()



    # --- GESTIONE ACCORDION TABELLA 13 CATEGORIE (v3) ---



    def _populate_categories_table(self, card: ProcessedCard, preserve_selection: bool = True):

        """Popola la tabella delle 13 categorie con layout compatto, frecce accordion e validazione inline."""

        selected_item = (self.tree.focus() or (self.tree.selection()[0] if self.tree.selection() else None)) if preserve_selection else None



        self.tree.delete(*self.tree.get_children())

        for cat_def in CATEGORIES:

            cat_name = cat_def["name"]

            cat_data = card.categorie.get(cat_name)

            cat_boxes = [b for b in card.righe_scatole if b.categoria == cat_name]

            boxes_count = len(cat_boxes)

            boxes_sum = round(sum(b.kg for b in cat_boxes), 2)



            scatole_calc = cat_data.scatole_calcolate if cat_data else 0

            kg_calc = cat_data.kg_calcolati if cat_data else 0.0



            has_red = cat_data and any(f.severity == FlagSeverity.RED for f in cat_data.flags)



            all_boxes_confirmed = (boxes_count > 0 and all(getattr(b, "confermata", False) for b in cat_boxes))

            if all_boxes_confirmed and cat_data:

                if cat_data.kg_dichiarati is not None and abs(cat_data.kg_calcolati - cat_data.kg_dichiarati) > 0.05:
                    cat_data.confermata = False
                else:
                    cat_data.confermata = True



            is_cat_confirmed = getattr(cat_data, "confermata", False) if cat_data else False



            # Requisito: Rilevamento discrepanza/errore

            is_empty = (scatole_calc == 0 and kg_calc == 0 and boxes_count == 0)

            is_mismatch = False

            if not is_empty and not is_cat_confirmed:

                if has_red:

                    is_mismatch = True

                elif boxes_count > 0 and boxes_count != scatole_calc:

                    is_mismatch = True

                elif boxes_count > 0 and abs(boxes_sum - kg_calc) > 0.05:

                    is_mismatch = True

                elif scatole_calc > 0 and boxes_count == 0:

                    is_mismatch = True



            if is_empty:

                tag = "empty"

                chevron = "   "

                cat_display = f"{chevron}{cat_name}"

                kg_display = "—"

                scatole_display = "—"

                stato_display = "—"

            elif is_cat_confirmed:

                # Categoria confermata (tutte le scatole confermate o approvata direttamente)

                tag = "ok"

                is_expanded = cat_name in self.expanded_categories

                chevron = "▼ " if is_expanded else "▶ "

                cat_display = f"{chevron}{cat_name}"

                kg_display = f"{kg_calc:.1f} Kg"

                scatole_display = str(scatole_calc)

                stato_display = "✓"

            elif is_mismatch:

                tag = "error"

                is_expanded = cat_name in self.expanded_categories

                chevron = "▼ " if is_expanded else "▶ "

                cat_display = f"{chevron}{cat_name}"

                kg_display = f"{kg_calc:.1f} Kg"

                scatole_display = str(scatole_calc)

                stato_display = "✗"

            else:

                tag = "ok"

                is_expanded = cat_name in self.expanded_categories

                chevron = "▼ " if is_expanded else "▶ "

                cat_display = f"{chevron}{cat_name}"

                kg_display = f"{kg_calc:.1f} Kg"

                scatole_display = str(scatole_calc)

                stato_display = "✓"



            self.tree.insert("", tk.END, iid=cat_name, values=(cat_display, kg_display, scatole_display, stato_display), tags=(tag,))



            # Se la categoria è espansa e ci sono colli singoli, inserisci le sotto-righe indentate

            if not is_empty and (cat_name in self.expanded_categories):

                if boxes_count > 0:

                    for idx, box in enumerate(cat_boxes):

                        child_iid = f"{cat_name}__box__{idx}"

                        child_name = f"      ▪ {idx + 1}ª Scatola: {box.kg:.1f} kg"

                        child_kg = f"{box.kg:.1f} Kg"

                        is_box_confirmed = getattr(box, "confermata", False)

                        child_scatole = "—"

                        if is_box_confirmed:

                            child_stato = "✓"

                            child_tag = "child_confirmed"

                        elif is_mismatch and not is_cat_confirmed:

                            child_stato = "✗"

                            child_tag = "child_error"

                        else:

                            child_stato = "✓"

                            child_tag = "child_row"

                        self.tree.insert("", tk.END, iid=child_iid, values=(child_name, child_kg, child_scatole, child_stato), tags=(child_tag,))

                else:

                    child_iid = f"{cat_name}__nobox"

                    self.tree.insert("", tk.END, iid=child_iid, values=("      ▪ (Nessun dettaglio colli presente)", "—", "—", "—"), tags=("child_row",))



        # Ripristina selezione o posiziona il cursore/focus sulla prima categoria per navigazione immediata da tastiera

        all_children = self.tree.get_children()

        if all_children:

            target_item = None

            if preserve_selection and selected_item and selected_item in all_children:

                target_item = selected_item

            else:

                target_item = all_children[0]



            if target_item:

                self.tree.selection_set(target_item)

                self.tree.focus(target_item)

                self.tree.see(target_item)

                self.tree.focus_set()

                try:

                    self.after_idle(self.tree.focus_set)

                except Exception:

                    pass



    def _on_tree_click(self, event):

        """Gestisce il clic con il mouse:

        - Se su colonna '#4' ('stato'): commuta la conferma della riga (categoria o singola scatola).

        - Se su chevron ▶/▼ su colonna 'cat': apre/chiude l'accordion.

        - Se in modalità modifica (✏️ Modifica) e su colonna 'kg' o 'scatole': avvia editing inline in-place.

        """

        region = self.tree.identify_region(event.x, event.y)

        if region != "cell":

            if self._inline_entry:

                self._commit_inline_cell_edit()

            return



        item_id = self.tree.identify_row(event.y)

        col = self.tree.identify_column(event.x)

        if not item_id or not self.current_processed_card:

            return



        # 0. Clic diretto sulla colonna #4 ('stato' ✓ / ✗)

        if col == "#4":

            if self._inline_entry:

                self._commit_inline_cell_edit()



            # Caso A: Riga Categoria madre

            if item_id in [c["name"] for c in CATEGORIES]:

                cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == item_id]

                cat_data = self.current_processed_card.categorie.get(item_id)

                if cat_boxes:

                    all_confirmed = all(getattr(b, "confermata", False) for b in cat_boxes)

                    if all_confirmed:

                        # Se già tutte confermate, togli la conferma a tutte le scatole

                        for b in cat_boxes:

                            b.confermata = False

                        if cat_data:

                            cat_data.confermata = False

                        self._populate_categories_table(self.current_processed_card, preserve_selection=True)

                    else:

                        # Conferma tutte le scatole e approva la discrepanza

                        for b in cat_boxes:

                            b.confermata = True

                        if cat_data:

                            cat_data.confermata = True

                        self._approve_category_discrepancy(item_id, keep_item_id=item_id)

                else:

                    # Categoria senza dettaglio scatole

                    if cat_data:

                        cat_data.confermata = not getattr(cat_data, "confermata", False)

                        if cat_data.confermata:

                            self._approve_category_discrepancy(item_id, keep_item_id=item_id)

                        else:

                            self._populate_categories_table(self.current_processed_card, preserve_selection=True)



                if item_id in self.tree.get_children():

                    self.tree.selection_set(item_id)

                    self.tree.focus(item_id)

                    self.tree.focus_set()

                return "break"



            # Caso B: Singola scatola figlia

            if "__box__" in item_id:

                parts = item_id.split("__box__")

                cat_name = parts[0]

                try:

                    box_idx = int(parts[1])

                    cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == cat_name]

                    if box_idx < len(cat_boxes):

                        target_box = cat_boxes[box_idx]

                        target_box.confermata = not getattr(target_box, "confermata", False)

                        cat_data = self.current_processed_card.categorie.get(cat_name)

                        if cat_data:

                            cat_data.confermata = all(getattr(b, "confermata", False) for b in cat_boxes)

                        self._populate_categories_table(self.current_processed_card, preserve_selection=True)

                        if item_id in self.tree.get_children():

                            self.tree.selection_set(item_id)

                            self.tree.focus(item_id)

                            self.tree.focus_set()

                except (ValueError, IndexError):

                    pass

                return "break"



            return "break"



        # 1. Se siamo in modalità modifica esplicita (pulsante ✏️ Modifica): editing in-place

        if self.is_edit_mode:

            if col in ("#2", "#3"):

                self._start_inline_cell_edit(item_id, col)

                return "break"



        # 2. Clic su riga Categoria madre (colonne #1, #2, #3)

        if item_id in [c["name"] for c in CATEGORIES]:

            cat_data = self.current_processed_card.categorie.get(item_id)

            cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == item_id]

            if not cat_data or (cat_data.scatole_calcolate == 0 and cat_data.kg_calcolati == 0 and len(cat_boxes) == 0):

                return  # Categoria vuota: nessuna scatola da aprire



            if self._inline_entry:

                self._commit_inline_cell_edit()



            # Caso A: Clic su colonna #1 (Categoria) o colonna #3 (N° Scatole) -> Toggle apertura/chiusura

            if col in ("#1", "#3"):

                self._toggle_category_expansion(item_id)

                return "break"



            # Caso B: Clic singolo su colonna #2 (Totale Kg) -> Apre l'accordion se chiuso

            # (se già aperto, non lo richiude per consentire al doppio clic di avviare l'editing in-place)

            if col == "#2":

                if item_id not in self.expanded_categories:

                    self._toggle_category_expansion(item_id)

                    return "break"



    def _toggle_category_expansion(self, cat_name: str):

        if not self.current_processed_card:

            return

        cat_data = self.current_processed_card.categorie.get(cat_name)

        cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == cat_name]

        if not cat_data or (cat_data.scatole_calcolate == 0 and cat_data.kg_calcolati == 0 and len(cat_boxes) == 0):

            return



        if cat_name in self.expanded_categories:

            self.expanded_categories.remove(cat_name)

        else:

            self.expanded_categories.add(cat_name)



        self._populate_categories_table(self.current_processed_card)

        if cat_name in self.tree.get_children():

            self.tree.selection_set(cat_name)

            self.tree.focus(cat_name)



    def _on_tree_toggle_space(self, event=None):

        item_id = self.tree.focus()

        if item_id and item_id in [c["name"] for c in CATEGORIES]:

            self._toggle_category_expansion(item_id)

            return "break"



    def _on_tree_expand(self, event=None):

        item_id = self.tree.focus()

        if item_id and item_id in [c["name"] for c in CATEGORIES]:

            if item_id not in self.expanded_categories:

                self._toggle_category_expansion(item_id)

            else:

                cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == item_id] if self.current_processed_card else []

                if cat_boxes:

                    child_iid = f"{item_id}__box__0"

                    if child_iid in self.tree.get_children():

                        self.tree.selection_set(child_iid)

                        self.tree.focus(child_iid)

                        self.tree.see(child_iid)

            return "break"



    def _on_tree_collapse(self, event=None):

        item_id = self.tree.focus()

        if item_id and item_id in [c["name"] for c in CATEGORIES]:

            if item_id in self.expanded_categories:

                self._toggle_category_expansion(item_id)

            return "break"

        elif item_id and "__box__" in item_id:

            parent_cat = item_id.split("__box__")[0]

            if parent_cat in self.tree.get_children():

                self.tree.selection_set(parent_cat)

                self.tree.focus(parent_cat)

            return "break"



    def _on_tree_return_key(self, event=None):

        """Tasto Invio su tabella:

        - Su Categoria: conferma l'intera categoria madre e imposta tutte le sue scatole figlie a confermata = True (senza aprire/chiudere l'accordion).

        - Su Scatola: commuta lo stato confermata di QUELLA SPECIFICA SCATOLA, mantenendo il focus sulla scatola.

        """

        item_id = self.tree.focus() or (self.tree.selection()[0] if self.tree.selection() else None)

        if not item_id or not self.current_processed_card:

            return "break"



        if self._inline_entry:

            self._commit_inline_cell_edit()

            return "break"



        # Caso 1: Siamo su una sotto-riga scatola (es. Pasta__box__0) -> Commuta stato confermata

        if "__box__" in item_id:

            parts = item_id.split("__box__")

            cat_name = parts[0]

            try:

                box_idx = int(parts[1])

                cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == cat_name]

                if box_idx < len(cat_boxes):

                    target_box = cat_boxes[box_idx]

                    target_box.confermata = not getattr(target_box, "confermata", False)

                    cat_data = self.current_processed_card.categorie.get(cat_name)

                    if cat_data:

                        cat_data.confermata = all(getattr(b, "confermata", False) for b in cat_boxes)

                    # Aggiorna la tabella preservando la selezione attiva sulla scatola

                    self._populate_categories_table(self.current_processed_card, preserve_selection=True)

                    if item_id in self.tree.get_children():

                        self.tree.selection_set(item_id)

                        self.tree.focus(item_id)

                        self.tree.focus_set()

            except (ValueError, IndexError):

                pass

            return "break"



        # Caso 2: Siamo su una riga categoria madre

        if item_id in [c["name"] for c in CATEGORIES]:

            cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == item_id]

            cat_data = self.current_processed_card.categorie.get(item_id)

            for b in cat_boxes:

                b.confermata = True

            if cat_data:

                cat_data.confermata = True

            self._approve_category_discrepancy(item_id, keep_item_id=item_id)

            if item_id in self.tree.get_children():

                self.tree.selection_set(item_id)

                self.tree.focus(item_id)

            self.tree.focus_set()

            return "break"



        return "break"



    def _on_tree_shift_return_key(self, event=None):

        """Shift + Invio: se già in modifica salva ed esce, altrimenti avvia modifica rapida peso inline (senza popup)."""

        if self._inline_entry:

            self._commit_inline_cell_edit()

            self._exit_edit_mode_ui()

            self.tree.focus_set()

            self.after_idle(self.tree.focus_set)

            return "break"

        if self.is_edit_mode:

            self._on_save_edit_mode()

            self.tree.focus_set()

            self.after_idle(self.tree.focus_set)

            return "break"

        return self._on_tree_edit_action(event)



    def _on_tree_ctrl_return_key(self, event=None):

        """Ctrl + Invio (o doppio clic) su riga tabella: apre la finestra di modifica dati."""

        self._on_tree_edit_action()

        return "break"



    def _approve_category_discrepancy(self, cat_name: str, keep_item_id: Optional[str] = None):

        """Approva immediatamente la categoria: allinea dichiarati a calcolati e trasforma ✕ rossa in ✓ verde."""

        card = self.current_processed_card

        if not card:

            return

        cat_data = card.categorie.get(cat_name)

        if not cat_data:

            return



        cat_boxes = [b for b in card.righe_scatole if b.categoria == cat_name]

        boxes_sum = round(sum(b.kg for b in cat_boxes), 2)

        boxes_count = len(cat_boxes)



        # Allinea i valori dichiarati ai valori effettivi calcolati

        if boxes_count > 0:

            cat_data.scatole_calcolate = boxes_count

            cat_data.kg_calcolati = boxes_sum

            cat_data.scatole_dichiarate = boxes_count

            cat_data.kg_dichiarati = boxes_sum

            for b in cat_boxes:

                b.confermata = True

        else:

            cat_data.scatole_dichiarate = cat_data.scatole_calcolate

            cat_data.kg_dichiarati = cat_data.kg_calcolati



        cat_data.confermata = True



        # Rimuove flag di discrepanza su questa categoria

        cat_data.flags = [

            f for f in cat_data.flags

            if f.severity != FlagSeverity.RED and not f.field.startswith(f"categoria:{cat_name}")

        ]



        # Ricalcola la scheda

        recomputed = CardValidator.recompute_from_categories(card)

        self.current_processed_card = recomputed



        # Ridisegna tabella preservando l'espansione e la riga attiva

        self._populate_categories_table(recomputed, preserve_selection=True)



        target_sel = keep_item_id if (keep_item_id and keep_item_id in self.tree.get_children()) else cat_name

        if target_sel in self.tree.get_children():

            self.tree.selection_set(target_sel)

            self.tree.focus(target_sel)

            self.tree.focus_set()

            self.after_idle(self.tree.focus_set)



        # Aggiorna totali in calce, avvisi e pulsante scrittura

        self.calc_totals_lbl.configure(

            text=f"Totale: {recomputed.totale_scatole_calcolate} scatole  —  {recomputed.totale_kg_calcolati:.1f} Kg"

        )

        self._update_alerts_box(recomputed)

        self._update_write_button_state()

        self._show_toast(f"✓ Categoria '{cat_name}' approvata ({cat_data.kg_calcolati:.1f} Kg)")



    # --- MODIFICA RAPIDA VALORI (RIGHE AGGREGATE E SINGOLI COLLI) ---



    def _on_tree_edit_action(self, event=None):

        """Doppio clic o Shift+Invio su riga: attiva la modalita modifica rapida ed avvia l'editing inline del peso (#2)."""

        item_id = self.tree.focus() or (self.tree.selection()[0] if self.tree.selection() else None)

        if not item_id or not self.current_processed_card:

            return "break"



        if not self.is_edit_mode:

            self._on_enter_edit_mode(source="quick")



        self._start_inline_cell_edit(item_id, "#2")

        return "break"



    # --- MODALITÀ MODIFICA DIRETTA IN-PLACE (v3) ---



    def _on_toggle_edit_mode(self):

        if self.is_edit_mode:

            self._on_save_edit_mode()

        else:

            self._on_enter_edit_mode(source="button")



    def _on_enter_edit_mode(self, source: str = "button"):

        if not self.current_processed_card:

            return

        self.is_edit_mode = True

        self._edit_mode_source = source

        self._edit_mode_backup = self.current_processed_card.model_copy(deep=True)



        # Evidenzia pulsante modifica attivo

        self.edit_btn.configure(

            text="✏️ Modifica Attiva",

            fg_color="#e0f2fe",

            text_color="#0284c7",

            hover_color="#bae6fd"

        )



        # Niente tasti Salva / Annulla in calce (auto-salvataggio automatico all'uscita dal campo)

        self.edit_actions_box.pack_forget()



        # Imposta cursore a I-beam per indicare che i campi numerici sono sbloccati

        self.tree.configure(cursor="xterm")





    def _on_save_edit_mode(self):

        """Conferma la modalità modifica, salva lo stato corrente e torna in visualizzazione."""

        if self._inline_entry:

            self._commit_inline_cell_edit()



        self._edit_mode_backup = None

        self._exit_edit_mode_ui()



    def _on_cancel_edit_mode(self):

        """Annulla tutte le modifiche fatte durante la sessione di modifica, ripristinando il backup."""

        self._cancel_inline_cell_edit()



        if self._edit_mode_backup:

            self.current_processed_card = self._edit_mode_backup

            self._edit_mode_backup = None



        self._exit_edit_mode_ui()



        if self.current_processed_card:

            self._populate_ui_with_card(self.current_processed_card)



    def _exit_edit_mode_ui(self):

        self.is_edit_mode = False

        self._edit_mode_source = "button"

        self.tree.configure(cursor="")

        self.edit_actions_box.pack_forget()

        self.edit_btn.configure(

            text="✏️ Modifica",

            fg_color="#0284c7",

            text_color="#ffffff",

            hover_color="#0369a1",

            state="normal" if self.current_processed_card else "disabled"

        )

        self.tree.focus_set()

        self.after_idle(self.tree.focus_set)



    def _start_inline_cell_edit(self, item_id: str, col: str):

        """Apre una casella di input tk.Entry esattamente sovrapposta alla cella selezionata."""

        if not self.current_processed_card:

            return



        # Se c'era già un'altra cella in modifica

        if self._inline_entry:

            if self._editing_info and self._editing_info.get("item_id") == item_id and self._editing_info.get("col") == col:

                return

            self._commit_inline_cell_edit()



        card = self.current_processed_card

        current_val_str = ""

        justify = "right" if col == "#2" else "center"



        is_cat_row = item_id in [c["name"] for c in CATEGORIES]

        is_box_row = "__box__" in item_id



        if not is_cat_row and not is_box_row:

            return



        if is_cat_row:

            cat_data = card.categorie.get(item_id)

            if col == "#2":  # Totale Kg

                val = cat_data.kg_calcolati if cat_data else 0.0

                current_val_str = f"{val:.1f}" if val > 0 else ""

                justify = "right"

            elif col == "#3":  # N° Scatole

                val = cat_data.scatole_calcolate if cat_data else 0

                current_val_str = str(val) if val > 0 else ""

                justify = "center"

        elif is_box_row:

            if col == "#2":  # Kg del singolo collo

                parts = item_id.split("__box__")

                cat_name = parts[0]

                try:

                    box_idx = int(parts[1])

                    cat_boxes = [b for b in card.righe_scatole if b.categoria == cat_name]

                    if box_idx < len(cat_boxes):

                        current_val_str = f"{cat_boxes[box_idx].kg:.1f}"

                except (ValueError, IndexError):

                    return

                justify = "right"

            else:

                return



        # Assicura visibilità riga

        self.tree.see(item_id)

        self.tree.update_idletasks()



        bbox = self.tree.bbox(item_id, col)

        if not bbox:

            self.update_idletasks()

            bbox = self.tree.bbox(item_id, col)



        if not bbox:

            try:

                children = list(self.tree.get_children())

                row_idx = children.index(item_id) if item_id in children else 0

                y = 26 + row_idx * 24

                w_cat = self.tree.column("cat", "width") or 180

                w_kg = self.tree.column("kg", "width") or 95

                w_scat = self.tree.column("scatole", "width") or 75

                w_stato = self.tree.column("stato", "width") or 55

                if col == "#2":

                    bbox = (w_cat, y, w_kg, 24)

                elif col == "#3":

                    bbox = (w_cat + w_kg, y, w_scat, 24)

                else:

                    bbox = (0, y, w_cat, 24)

            except Exception:

                return



        x, y, width, height = bbox



        entry_h = 20

        entry_y = y + max(0, (height - entry_h) // 2)



        if col == "#3":

            entry_w = 40

            entry_x = x + max(0, (width - entry_w) // 2)

        else:

            entry_w = 60

            right_pad = 18 if is_cat_row else 6

            entry_x = max(x + 2, x + width - entry_w - right_pad)



        entry = tk.Entry(

            self.tree,

            font=("Segoe UI", 9, "bold"),

            justify=justify,

            relief="solid",

            borderwidth=1,

            highlightthickness=1,

            highlightcolor="#0284c7",

            highlightbackground="#0284c7",

            bg="#ffffff",

            fg="#0f172a"

        )

        entry.place(x=entry_x, y=entry_y, width=entry_w, height=entry_h)

        if current_val_str:

            entry.insert(0, current_val_str)

            entry.select_range(0, tk.END)



        self._inline_entry = entry

        self._editing_info = {

            "item_id": item_id,

            "col": col,

            "orig_val": current_val_str,

            "is_cat_row": is_cat_row,

            "is_box_row": is_box_row

        }



        entry.focus_set()



        entry.bind("<Return>", lambda e: (self._on_inline_return(), "break")[1])

        entry.bind("<KP_Enter>", lambda e: (self._on_inline_return(), "break")[1])

        entry.bind("<Shift-Return>", lambda e: (self._on_inline_shift_return(), "break")[1])

        entry.bind("<Shift-KP_Enter>", lambda e: (self._on_inline_shift_return(), "break")[1])

        entry.bind("<Up>", lambda e: (self._on_inline_arrow_key("up"), "break")[1])

        entry.bind("<Down>", lambda e: (self._on_inline_arrow_key("down"), "break")[1])

        entry.bind("<Tab>", self._on_inline_tab)

        entry.bind("<Escape>", lambda e: (self._cancel_inline_cell_edit(), "break")[1])

        entry.bind("<FocusOut>", self._on_inline_focus_out)



    def _commit_inline_cell_edit(self):

        """Convalida e applica il valore inserito nella cella attiva, ricalcolando totali e allarmi."""

        if not self._inline_entry or not self._editing_info or not self.current_processed_card:

            return



        is_quick_mode = (getattr(self, "_edit_mode_source", "button") == "quick")

        entry = self._inline_entry

        info = self._editing_info

        self._inline_entry = None

        self._editing_info = None



        raw_val = entry.get().strip().replace(",", ".")

        try:

            entry.destroy()

        except Exception:

            pass



        card = self.current_processed_card

        item_id = info["item_id"]

        col = info["col"]



        if info["is_cat_row"]:

            cat_name = item_id

            cat_data = card.categorie.get(cat_name)

            if not cat_data:

                return



            if col == "#2":  # Totale Kg

                try:

                    new_kg = round(float(raw_val), 2) if raw_val else 0.0

                    if new_kg < 0:

                        new_kg = 0.0

                except ValueError:

                    new_kg = cat_data.kg_calcolati



                if new_kg <= 0.0:

                    cat_data.scatole_calcolate = 0

                    cat_data.kg_calcolati = 0.0

                    cat_data.scatole_dichiarate = None

                    cat_data.kg_dichiarati = None

                    self._sync_boxes_to_category(cat_name, 0, 0.0)

                    cat_data.flags = [

                        f for f in cat_data.flags

                        if f.severity != FlagSeverity.RED and not f.field.startswith(f"categoria:{cat_name}")

                    ]

                    if cat_name in self.expanded_categories:

                        self.expanded_categories.remove(cat_name)

                else:

                    cat_data.kg_calcolati = new_kg

                    cat_data.scatole_dichiarate = None

                    cat_data.kg_dichiarati = None

                    self._sync_boxes_to_category(cat_name, cat_data.scatole_calcolate, new_kg)



            elif col == "#3":  # N° Scatole

                try:

                    new_scat = int(raw_val) if raw_val else 0

                    if new_scat < 0:

                        new_scat = 0

                except ValueError:

                    new_scat = cat_data.scatole_calcolate



                if new_scat <= 0:

                    cat_data.scatole_calcolate = 0

                    cat_data.kg_calcolati = 0.0

                    cat_data.scatole_dichiarate = None

                    cat_data.kg_dichiarati = None

                    self._sync_boxes_to_category(cat_name, 0, 0.0)

                    cat_data.flags = [

                        f for f in cat_data.flags

                        if f.severity != FlagSeverity.RED and not f.field.startswith(f"categoria:{cat_name}")

                    ]

                    if cat_name in self.expanded_categories:

                        self.expanded_categories.remove(cat_name)

                else:

                    cat_data.scatole_calcolate = new_scat

                    cat_data.scatole_dichiarate = None

                    cat_data.kg_dichiarati = None

                    self._sync_boxes_to_category(cat_name, new_scat, cat_data.kg_calcolati)



        elif info["is_box_row"]:

            parts = item_id.split("__box__")

            cat_name = parts[0]

            try:

                box_idx = int(parts[1])

                cat_boxes = [b for b in card.righe_scatole if b.categoria == cat_name]

                if box_idx < len(cat_boxes):

                    target_box = cat_boxes[box_idx]

                    try:

                        new_box_kg = round(float(raw_val), 2) if raw_val else 0.0

                        if new_box_kg < 0:

                            new_box_kg = 0.0

                    except ValueError:

                        new_box_kg = target_box.kg



                    if new_box_kg <= 0.0:

                        # Auto-eliminazione della scatola

                        if target_box in card.righe_scatole:

                            card.righe_scatole.remove(target_box)

                    else:

                        target_box.kg = new_box_kg
                        target_box.confermata = True



                    cur_cat_boxes = [b for b in card.righe_scatole if b.categoria == cat_name]

                    cat_data = card.categorie.get(cat_name)

                    if cat_data:

                        cat_data.scatole_calcolate = len(cur_cat_boxes)

                        cat_data.kg_calcolati = round(sum(b.kg for b in cur_cat_boxes), 2)

                        if len(cur_cat_boxes) == 0:

                            cat_data.scatole_calcolate = 0

                            cat_data.kg_calcolati = 0.0

                            if cat_name in self.expanded_categories:

                                self.expanded_categories.remove(cat_name)
                        elif cat_data.kg_dichiarati is not None:
                            diff_kg = abs(cat_data.kg_calcolati - cat_data.kg_dichiarati)
                            scat_match = (cat_data.scatole_dichiarate is None or cat_data.scatole_calcolate == cat_data.scatole_dichiarate)
                            if diff_kg <= 0.05 and scat_match:
                                cat_data.confermata = True
                                for b in cur_cat_boxes:
                                    b.confermata = True
                                cat_data.flags = [
                                    f for f in cat_data.flags
                                    if f.severity != FlagSeverity.RED and not f.field.startswith(f"categoria:{cat_name}")
                                ]
                            else:
                                cat_data.confermata = False

            except (ValueError, IndexError):

                pass



        # Ricalcola la scheda

        recomputed = CardValidator.recompute_from_categories(card)

        self.current_processed_card = recomputed



        # Ridisegna tabella preservando l'elemento attivo e l'espansione

        self._populate_categories_table(recomputed)

        cat_root = item_id.split("__box__")[0] if "__box__" in item_id else item_id

        target_focus = item_id if item_id in self.tree.get_children() else (cat_root if cat_root in self.tree.get_children() else None)

        if target_focus:

            self.tree.selection_set(target_focus)

            self.tree.focus(target_focus)

            self.tree.focus_set()

            self.after_idle(self.tree.focus_set)



        # Aggiorna totale in calce

        self.calc_totals_lbl.configure(

            text=f"Totale: {recomputed.totale_scatole_calcolate} scatole  —  {recomputed.totale_kg_calcolati:.1f} Kg"

        )

        self._update_alerts_box(recomputed)

        self._update_write_button_state()



        if is_quick_mode:

            self._exit_edit_mode_ui()



    def _on_inline_shift_return(self):

        """Shift+Invio durante l'editing: salva ed esce dalla modalità modifica."""

        self._commit_inline_cell_edit()

        self._exit_edit_mode_ui()

        self.tree.focus_set()

        self.after_idle(self.tree.focus_set)



    def _on_inline_arrow_key(self, direction: str = "down"):

        """Frecce Su/Giù durante l'editing: salvano il valore ed escono dalla modifica tornando alle frecce."""

        info = self._editing_info

        target_item = info.get("item_id") if info else None

        self._commit_inline_cell_edit()

        self._exit_edit_mode_ui()

        children = list(self.tree.get_children())

        if target_item and target_item in children:

            idx = children.index(target_item)

            new_idx = max(0, idx - 1) if direction == "up" else min(len(children) - 1, idx + 1)

            new_item = children[new_idx]

            self.tree.selection_set(new_item)

            self.tree.focus(new_item)

            self.tree.see(new_item)

        elif target_item:

            cat_root = target_item.split("__box__")[0]

            if cat_root in children:

                self.tree.selection_set(cat_root)

                self.tree.focus(cat_root)

                self.tree.see(cat_root)

        self.tree.focus_set()

        self.after_idle(self.tree.focus_set)



    def _cancel_inline_cell_edit(self):

        """Annulla la modifica della cella attiva senza applicarla."""

        is_quick = (getattr(self, "_edit_mode_source", "button") == "quick")

        if self._inline_entry:

            entry = self._inline_entry

            self._inline_entry = None

            self._editing_info = None

            try:

                entry.destroy()

            except Exception:

                pass

        if is_quick:

            self._exit_edit_mode_ui()

        self.tree.focus_set()

        self.after_idle(self.tree.focus_set)



    def _on_inline_return(self):

        self._commit_inline_cell_edit()



    def _on_inline_focus_out(self, event):

        self.after(50, self._check_focus_out_commit)



    def _check_focus_out_commit(self):

        if self._inline_entry:

            try:

                focused = self.focus_get()

                if focused != self._inline_entry:

                    self._commit_inline_cell_edit()

            except Exception:

                self._commit_inline_cell_edit()



    def _on_inline_tab(self, event=None):

        """Conferma la cella corrente e sposta il cursore di modifica alla cella successiva."""

        if not self._editing_info or not self.current_processed_card:

            return "break"



        cur_item = self._editing_info["item_id"]

        cur_col = self._editing_info["col"]



        self._commit_inline_cell_edit()



        # Elenco delle celle modificabili

        editable_cells = []

        for cat_def in CATEGORIES:

            cat_name = cat_def["name"]

            editable_cells.append((cat_name, "#2"))

            editable_cells.append((cat_name, "#3"))

            if cat_name in self.expanded_categories:

                cat_boxes = [b for b in self.current_processed_card.righe_scatole if b.categoria == cat_name]

                for idx in range(len(cat_boxes)):

                    child_iid = f"{cat_name}__box__{idx}"

                    editable_cells.append((child_iid, "#2"))



        try:

            if (cur_item, cur_col) in editable_cells:

                cur_idx = editable_cells.index((cur_item, cur_col))

                next_idx = (cur_idx + 1) % len(editable_cells)

            else:

                cat_name = cur_item.split("__box__")[0]

                candidates = [i for i, (iid, _) in enumerate(editable_cells) if iid == cat_name]

                last_cat_idx = candidates[-1] if candidates else 0

                next_idx = (last_cat_idx + 1) % len(editable_cells)

            next_item, next_col = editable_cells[next_idx]

            self.after(20, lambda: self._start_inline_cell_edit(next_item, next_col))

        except (ValueError, IndexError):

            pass



        return "break"



    def _sync_boxes_to_category(self, cat_name: str, new_scat: int, new_kg: float):

        """Sincronizza le singole scatole in card.righe_scatole per mantenere coerenza con i totali di categoria."""

        card = self.current_processed_card

        if not card:

            return



        existing_boxes = [b for b in card.righe_scatole if b.categoria == cat_name]

        if new_scat == 0:

            for b in existing_boxes:

                if b in card.righe_scatole:

                    card.righe_scatole.remove(b)

        elif new_scat > 0:

            if len(existing_boxes) == new_scat:

                if len(existing_boxes) == 1:

                    existing_boxes[0].kg = new_kg

                else:

                    cur_sum = sum(b.kg for b in existing_boxes)

                    if cur_sum > 0:

                        ratio = new_kg / cur_sum

                        for b in existing_boxes:

                            b.kg = round(b.kg * ratio, 2)

                    else:

                        avg_kg = round(new_kg / new_scat, 2)

                        for b in existing_boxes:

                            b.kg = avg_kg

            elif len(existing_boxes) < new_scat:

                cur_sum = sum(b.kg for b in existing_boxes)

                diff_kg = max(0.0, new_kg - cur_sum)

                missing_count = new_scat - len(existing_boxes)

                avg_diff = round(diff_kg / missing_count, 2) if missing_count > 0 else 0.0

                for _ in range(missing_count):

                    card.righe_scatole.append(BoxItem(categoria=cat_name, kg=avg_diff))

            else:

                excess = len(existing_boxes) - new_scat

                for b in existing_boxes[-excess:]:

                    if b in card.righe_scatole:

                        card.righe_scatole.remove(b)

                rem = [b for b in card.righe_scatole if b.categoria == cat_name]

                if len(rem) == 1:

                    rem[0].kg = new_kg

                elif len(rem) > 1:

                    cur_sum = sum(b.kg for b in rem)

                    if cur_sum > 0:

                        ratio = new_kg / cur_sum

                        for b in rem:

                            b.kg = round(b.kg * ratio, 2)



    def _on_edit_declared_totals(self):

        if not self.current_processed_card:

            return



        card = self.current_processed_card

        edit_win = ctk.CTkToplevel(self)

        edit_win.title("Modifica Totale Dichiarato a Penna")

        edit_win.resizable(False, False)

        edit_win.configure(fg_color="#f1f5f9")

        edit_win.transient(self)

        edit_win.grab_set()



        from .components_dialogs import _center_modal

        _center_modal(edit_win, self, 460, 310)



        container = ctk.CTkFrame(

            edit_win, corner_radius=14, fg_color="#ffffff", border_width=1, border_color="#e2e8f0"

        )

        container.pack(fill=tk.BOTH, expand=True, padx=18, pady=18)



        # Barra pulsanti ancorata in basso per non venire mai schiacciata

        btn_bar = ctk.CTkFrame(container, fg_color="transparent")

        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 4))



        ctk.CTkLabel(

            container,

            text="Totale a Penna (in Calce)",

            font=("Segoe UI", 15, "bold"),

            text_color="#0f172a"

        ).pack(anchor=tk.W, pady=(0, 2))



        ctk.CTkLabel(

            container,

            text="Modifica i totali se l'OCR li ha letti male, o rimuovili se non presenti.",

            font=("Segoe UI", 11),

            text_color="#64748b"

        ).pack(anchor=tk.W, pady=(0, 14))



        form = ctk.CTkFrame(container, fg_color="transparent")

        form.pack(fill=tk.X)



        ctk.CTkLabel(form, text="Scatole Dichiarate:", font=("Segoe UI", 11, "bold"), text_color="#0f172a").grid(row=0, column=0, sticky=tk.W, pady=8)

        scat_entry = ctk.CTkEntry(form, width=130, height=34, corner_radius=8)

        if card.totali_dichiarati.scatole_totale is not None:

            scat_entry.insert(0, str(card.totali_dichiarati.scatole_totale))

        scat_entry.grid(row=0, column=1, sticky=tk.W, padx=14, pady=8)



        ctk.CTkLabel(form, text="Kg Totali Dichiarati:", font=("Segoe UI", 11, "bold"), text_color="#0f172a").grid(row=1, column=0, sticky=tk.W, pady=8)

        kg_entry = ctk.CTkEntry(form, width=130, height=34, corner_radius=8)

        if card.totali_dichiarati.kg_totale is not None:

            kg_entry.insert(0, str(card.totali_dichiarati.kg_totale))

        kg_entry.grid(row=1, column=1, sticky=tk.W, padx=14, pady=8)



        def clear_action():

            card.totali_dichiarati.scatole_totale = None

            card.totali_dichiarati.kg_totale = None

            recomputed = CardValidator.recompute_from_categories(card)

            self.current_processed_card = recomputed

            self._populate_categories_table(recomputed, preserve_selection=True)

            self.calc_totals_lbl.configure(

                text=f"Totale: {recomputed.totale_scatole_calcolate} scatole  —  {recomputed.totale_kg_calcolati:.1f} Kg"

            )

            self._update_alerts_box(recomputed)

            self._update_write_button_state()

            edit_win.destroy()



        def save_action():

            raw_s = scat_entry.get().strip()

            raw_k = kg_entry.get().strip().replace(",", ".")

            new_s = int(raw_s) if raw_s else None

            new_k = round(float(raw_k), 2) if raw_k else None

            card.totali_dichiarati.scatole_totale = new_s

            card.totali_dichiarati.kg_totale = new_k

            recomputed = CardValidator.recompute_from_categories(card)

            self.current_processed_card = recomputed

            self._populate_categories_table(recomputed, preserve_selection=True)

            self.calc_totals_lbl.configure(

                text=f"Totale: {recomputed.totale_scatole_calcolate} scatole  —  {recomputed.totale_kg_calcolati:.1f} Kg"

            )

            self._update_alerts_box(recomputed)

            self._update_write_button_state()

            edit_win.destroy()



        ctk.CTkButton(

            btn_bar, text="🗑️ Rimuovi Totale", font=("Segoe UI", 10, "bold"),

            fg_color="#fee2e2", text_color="#dc2626", hover_color="#fecaca",

            corner_radius=8, height=36, command=clear_action

        ).pack(side=tk.LEFT)



        ctk.CTkButton(

            btn_bar, text="💾 Salva", font=("Segoe UI", 11, "bold"),

            fg_color="#16a34a", hover_color="#15803d",

            corner_radius=8, width=110, height=36, command=save_action

        ).pack(side=tk.RIGHT)



        ctk.CTkButton(

            btn_bar, text="Annulla", font=("Segoe UI", 11),

            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",

            corner_radius=8, width=85, height=36, command=edit_win.destroy

        ).pack(side=tk.RIGHT, padx=(0, 8))



    # --- SCRITTURA EXCEL E RESET ---



    def _on_write_to_excel(self):

        if not self.current_processed_card:

            return

        cur_item = self._current_item()

        if cur_item is not None and cur_item.status == "written":

            return



        sheet_name = self.supermarket_var.get().strip()

        transport = self.transport_var.get().strip()



        if not sheet_name:

            messagebox.showwarning("Punto Vendita Mancante", "Selezionare il foglio Excel del supermercato.", parent=self)

            return



        if not transport:

            messagebox.showwarning("Trasporto Mancante", "Selezionare il numero di scheda/trasporto (A1..A10).", parent=self)

            return



        if not self.override_var.get():

            messagebox.showwarning(

                "Verifica Richiesta",

                "Spuntare la casella 'Ho verificato ed autorizzo la scrittura su Excel' per procedere.",

                parent=self

            )

            return



        # 1. Verifica Excel aperto

        if not self.excel_mgr.is_connected():

            try:

                self.excel_mgr.connect()

            except (ExcelNotRunningError, ExcelWorkbookNotOpenError):

                fname = self.excel_mgr.get_target_filename()

                confirm = messagebox.askyesno(

                    "File Non Aperto in Excel",

                    f"Il file gestionale '{fname}' non è aperto o connesso in Excel.\n\n"

                    f"Vuoi aprirlo adesso in Microsoft Excel per procedere alla scrittura?",

                    parent=self

                )

                if confirm:

                    if not self.excel_mgr.open_target_file():

                        messagebox.showerror(

                            "Errore Apertura",

                            f"Impossibile aprire il file in Excel:\n{self.excel_mgr.target_path}",

                            parent=self

                        )

                        return

                    self._check_excel_status_silent()

                else:

                    return



        # 2. Verifica esistenza foglio

        available_sheets = self.excel_mgr.get_sheet_names()

        if sheet_name not in available_sheets:

            matched, score = self.excel_mgr.find_matching_sheet(sheet_name)

            if matched and score >= 0.6:

                confirm = messagebox.askyesno(

                    "Conferma Foglio",

                    f"Il foglio '{sheet_name}' non è esatto. Usare il foglio più vicino:\n'{matched}'?",

                    parent=self

                )

                if confirm:

                    sheet_name = matched

                    self.supermarket_var.set(matched)

                else:

                    return

            else:

                messagebox.showerror(

                    "Foglio Non Trovato",

                    f"Il foglio '{sheet_name}' non esiste nel gestionale aperto.",

                    parent=self

                )

                return



        # 3. Verifica sovrascrittura se riga già compilata (propone sovrascrittura oppure la prima riga vuota, es. A5)

        status = self.excel_mgr.get_sheet_status(sheet_name)

        if transport in status.get("filled_transports", []):

            next_free = status.get("next_available_slot")

            dup_dlg = DuplicateRowDialogV3(

                self,

                sheet_name=sheet_name,

                transport=transport,

                next_available_slot=next_free,

            )

            self.wait_window(dup_dlg)

            if dup_dlg.result == "cancel" or not dup_dlg.target_transport:

                return

            if dup_dlg.result == "next" and dup_dlg.target_transport:

                transport = dup_dlg.target_transport

                self.transport_var.set(transport)

                if self.current_processed_card:

                    self.current_processed_card.trasporto_assegnato = transport

                self._on_transport_selected()



        # 4. Scrittura sicura con backup preventivo

        self.config(cursor="wait")

        self.update()



        try:

            result = self.excel_mgr.write_card_data(

                sheet_name=sheet_name,

                transport=transport,

                processed_card=self.current_processed_card,

                create_backup_first=True

            )



            backup_path = result.get("backup_path")

            if backup_path:

                self.backup_lbl.configure(text=f"🛡️ Backup: {Path(backup_path).name}")



            row_idx = result.get("row", 0)

            target_fname = self.excel_mgr.get_target_filename() if hasattr(self.excel_mgr, "get_target_filename") else ""

            self.session_logger.log_excel_event(

                "Salvataggio scheda completato",

                sheet_name=sheet_name,

                row=row_idx,

                details=f"Viaggio: {transport} | File: {target_fname} | Backup: {Path(backup_path).name if backup_path else 'No'}"

            )



            # 5. Archiviazione nello storico scansioni (immagine + dataset ground truth per miglioramento anni futuri)

            try:

                cur_item = self._current_item()

                source_img = (cur_item.image_path if cur_item else None) or getattr(self, "current_image_path", None)

                raw_arch = getattr(cur_item, "raw_archive_path", None) if cur_item else None

                if not raw_arch and source_img:

                    raw_arch = ScannerService.get_raw_archive(source_img)

                ext = getattr(self, "active_extractor", None)

                model_used = (

                    getattr(ext, "last_winner_model", "")

                    or getattr(ext, "model", "")

                    or self.config_data.get("active_provider", "")

                )

                raw_notes = (

                    getattr(cur_item.raw, "note_o_anomalie", "")

                    if cur_item and getattr(cur_item, "raw", None)

                    else ""

                ) or ""

                if raw_notes.startswith("[") and "]" in raw_notes:

                    badge = raw_notes[1:raw_notes.index("]")].strip()

                    if badge:

                        if model_used and model_used not in badge:

                            model_used = f"{model_used} ({badge})"

                        elif not model_used:

                            model_used = badge

                arch_res = get_history_service().archive_card(

                    image_path=source_img,

                    sheet_name=sheet_name,

                    transport=transport,

                    processed_card=self.current_processed_card,

                    raw_extraction=cur_item.raw if cur_item else None,

                    model_name=model_used,

                    excel_target=target_fname,

                    row_idx=row_idx,

                    preset_id=cur_item.preset_id if cur_item else 0,

                    session_id=getattr(self.session_logger, "session_id", ""),

                    raw_archive_path=raw_arch,

                )

                if cur_item and arch_res and arch_res.get("raw_archive_path"):

                    cur_item.raw_archive_path = arch_res["raw_archive_path"]

                    cur_item.raw_assigned_market = sheet_name

                    if source_img:

                        ScannerService.register_raw_archive(source_img, arch_res["raw_archive_path"])

            except Exception as e_hist:

                self.session_logger.logger.warning(f"[STORICO] Errore archiviazione scheda nello storico: {e_hist}")



            self._after_write_success(sheet_name, transport, result)



        except Exception as e:

            self.session_logger.logger.error(f"[EXCEL] Errore scrittura su foglio '{sheet_name}': {e}")

            messagebox.showerror("Errore di Scrittura", f"Errore durante la scrittura su Excel:\n{e}", parent=self)

        finally:

            self.config(cursor="")



    def _on_reset_form(self):

        """Resetta l'interfaccia rendendola immediatamente pronta per la scheda successiva."""

        self._reset_card_fields()

        self.current_image_path = None

        self.image_viewer.clear()



    def _reset_card_fields(self):

        """Svuota i campi della scheda (tabella, foglio, viaggio, allarmi) senza toccare l'immagine."""

        self._hide_suggestion_banner()

        self.current_processed_card = None

        self.supermarket_var.set("")

        self.transport_var.set("")

        self.sheet_status_lbl.configure(text="")

        self.transport_status_lbl.configure(text="")

        self.expanded_categories.clear()

        self.tree.delete(*self.tree.get_children())

        if self.is_edit_mode:

            self._exit_edit_mode_ui()

        self.edit_btn.configure(state="disabled")

        self.alerts_text.config(state=tk.NORMAL)

        self.alerts_text.delete("1.0", tk.END)

        self.alerts_text.config(state=tk.DISABLED, bg="#f8fafc", height=ALERTS_MIN_LINES)

        if self.alerts_scroll.winfo_ismapped():

            self.alerts_scroll.pack_forget()

        self.alerts_title_lbl.configure(text="STATO VALIDAZIONE", text_color="#64748b")

        self.override_var.set(False)

        self.override_check.configure(

            state="normal",

            cursor="hand2",

            text_color="#0f172a",

            text="Ho verificato i dati ed autorizzo la scrittura su Excel"

        )

        self.write_btn.configure(state="disabled")



    def _open_history_folder(self):

        """Apre la cartella dello storico delle scansioni e dei dataset."""

        get_history_service().open_history_folder()



    def _open_backup_folder(self):

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        try:

            os.startfile(str(BACKUP_DIR))

        except Exception:

            subprocess.run(["explorer", str(BACKUP_DIR)])



    def destroy(self):

        for attr in ("_excel_check_job", "_startup_check_job", "_toast_job", "_folder_feedback_job"):

            job = getattr(self, attr, None)

            if job is not None:

                try:

                    self.after_cancel(job)

                except Exception:

                    pass

                setattr(self, attr, None)

        super().destroy()



    def on_closing(self):

        """Pulisce le risorse, cancella i file temporanei IA e chiude l'applicazione in modo pulito."""

        try:

            clean_temp_ai_dir()

        except Exception as e:

            try:

                self.session_logger.logger.warning(f"Errore pulizia temp_ai alla chiusura: {e}")

            except Exception:

                pass

        try:

            self.destroy()

        except Exception:

            pass





# Alias di retrocompatibilità

BancoAlimentareAppV3 = BancoAlimentareApp


