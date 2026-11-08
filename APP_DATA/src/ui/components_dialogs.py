"""Dialog e componenti v3: onboarding API key, impostazioni, carosello lotto, progress e continuazione scansione."""



import os
from pathlib import Path

import webbrowser

import tkinter as tk

from tkinter import messagebox, filedialog

from typing import Any, Callable, Dict, List, Optional



import customtkinter as ctk

from PIL import Image



from ..config import (

    ROOT_DIR,

    default_colletta_folder,

    DEFAULT_GEMINI_MODEL,

    DEFAULT_OPENROUTER_MODEL,

    DEFAULT_SCANNER_OPTIONS,

    SCANNER_DPI_CHOICES,

    SUPPORTED_GEMINI_MODELS,

    SUPPORTED_OPENROUTER_MODELS,

    DEFAULT_CASCADE_GEMINI_MODEL,

    DEFAULT_CASCADE_BUFFER_MODEL,

    DEFAULT_CASCADE_FREE_MODEL,

    DEFAULT_CASCADE_ARBITRATOR_MODEL,

    SUPPORTED_ARBITRATOR_MODELS,

    ARBITRATOR_DISPLAY_MAP,

    ARBITRATOR_INV_MAP,

    get_pipeline_status,

    DEFAULT_SHORTCUTS,

    SHORTCUT_LABELS,

    SHORTCUT_DESCRIPTIONS,

)

from ..crypto_keys import unlock_giovanni_keys
from ..extractors.key_tester import (
    test_gemini_api_key,
    test_openrouter_api_key,
    check_all_servers_status,
    test_gemini_generation,
    test_openrouter_generation,
)
from .async_utils import BackgroundTask

from ..scanner.scanner_service import ScannerService, TEST_IMAGES_DIR



AI_STUDIO_URL = "https://aistudio.google.com/app/apikey"

OPENROUTER_KEYS_URL = "https://openrouter.ai/keys"



PRIMARY = "#0284c7"

PRIMARY_HOVER = "#0369a1"

BORDER = "#e2e8f0"

TEXT = "#0f172a"

MUTED = "#64748b"





def _center_modal(win, parent, w: int, h: int):

    win.update_idletasks()

    try:

        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2

        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2

    except Exception:

        x, y = 100, 100

    win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")





def _make_modal(win, parent):

    win.transient(parent)



    def _grab():

        try:

            if win.winfo_exists():

                win.grab_set()

                win.focus_force()

        except Exception:

            pass

    win.after(120, _grab)





class _KeyRow(ctk.CTkFrame):

    """Card di una chiave API configurata: tipo chiave, modelli associati, campo mascherato, test semplificato ed eliminazione."""



    def __init__(

        self,

        parent,

        title: str,

        value: str,

        tester: Callable[[str], tuple],

        provider_type: str = "gemini",

        models_info: str = "",

        on_delete: Optional[Callable[["_KeyRow"], None]] = None,

    ):

        super().__init__(parent, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color=BORDER)

        self._tester = tester

        self._task: Optional[BackgroundTask] = None

        self._shown = False

        self.provider_type = provider_type

        self._on_delete = on_delete



        # Riga 0: Badge Tipo Chiave + Titolo + Modelli associati

        head = ctk.CTkFrame(self, fg_color="transparent")

        head.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(8, 2))



        if provider_type == "openrouter":

            badge_txt = "🟠 OpenRouter"

            badge_fg = "#9a3412"

            badge_bg = "#ffedd5"

            default_models = "Buffer Cascata (GPT-6 Luna · Haiku 5.5 · Nemotron) + Sonnet (Shift+Click)"

        else:

            badge_txt = "🔵 Google Gemini"

            badge_fg = "#0369a1"

            badge_bg = "#e0f2fe"

            default_models = "Piano Primario Gratuito: Gemini 3.5 Flash-Lite / 3.8 Flash (Gratis)"



        ctk.CTkLabel(

            head, text=f" {badge_txt} ", font=("Segoe UI", 10, "bold"),

            text_color=badge_fg, fg_color=badge_bg, corner_radius=6

        ).pack(side=tk.LEFT, padx=(0, 8))



        ctk.CTkLabel(head, text=title, font=("Segoe UI", 11, "bold"), text_color=TEXT).pack(side=tk.LEFT)



        ctk.CTkLabel(

            head, text=models_info or default_models,

            font=("Segoe UI", 9), text_color=MUTED

        ).pack(side=tk.RIGHT)



        self.var = tk.StringVar(value=value or "")

        self.entry = ctk.CTkEntry(self, textvariable=self.var, show="•", height=32, corner_radius=8,

                                  placeholder_text="Incolla qui la chiave API...")

        self.entry.grid(row=1, column=0, sticky="ew", padx=(12, 4), pady=(2, 6))

        self.columnconfigure(0, weight=1)



        self.eye_btn = ctk.CTkButton(self, text="👁️", width=34, height=32, corner_radius=8,

                                     fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT,

                                     command=self._toggle)

        self.eye_btn.grid(row=1, column=1, padx=2, pady=(2, 6))

        self.test_btn = ctk.CTkButton(self, text="Testa Chiave", width=110, height=32, corner_radius=8,

                                      fg_color=PRIMARY, hover_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

                                      command=self._test)

        self.test_btn.grid(row=1, column=2, padx=2, pady=(2, 6))

        self.del_btn = ctk.CTkButton(self, text="🗑", width=34, height=32, corner_radius=8,
                                     fg_color="#fee2e2", hover_color="#fecaca", text_color="#b91c1c",
                                     command=self._clear)
        self.del_btn.grid(row=1, column=3, padx=(2, 12), pady=(2, 6))

        # Riga 2: Pulsanti dedicati di richiesta reale / generazione token
        action_row = ctk.CTkFrame(self, fg_color="transparent")
        action_row.grid(row=2, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 4))

        if self.provider_type == "gemini":
            self.test_35_btn = ctk.CTkButton(
                action_row,
                text="⚡ Richiesta Gemini 3.5",
                width=165,
                height=26,
                corner_radius=6,
                fg_color="#0891b2",
                hover_color="#0e7490",
                text_color="#ffffff",
                font=("Segoe UI", 10, "bold"),
                command=lambda: self._test_gemini_gen("gemini-3.5-flash-lite", "Gemini 3.5"),
            )
            self.test_35_btn.pack(side=tk.LEFT, padx=(0, 8))

            self.test_38_btn = ctk.CTkButton(
                action_row,
                text="⚡ Richiesta Gemini 3.8",
                width=165,
                height=26,
                corner_radius=6,
                fg_color=PRIMARY,
                hover_color=PRIMARY_HOVER,
                text_color="#ffffff",
                font=("Segoe UI", 10, "bold"),
                command=lambda: self._test_gemini_gen("gemini-3.8-flash", "Gemini 3.8"),
            )
            self.test_38_btn.pack(side=tk.LEFT)

        elif self.provider_type == "openrouter":
            self.test_free_btn = ctk.CTkButton(
                action_row,
                text="⚡ Richiesta Modello Free",
                width=180,
                height=26,
                corner_radius=6,
                fg_color="#d97706",
                hover_color="#b45309",
                text_color="#ffffff",
                font=("Segoe UI", 10, "bold"),
                command=self._test_openrouter_gen,
            )
            self.test_free_btn.pack(side=tk.LEFT)

        self.badge = ctk.CTkLabel(self, text="● Non verificata",
                                  font=("Segoe UI", 10, "bold"), text_color=MUTED,
                                  anchor="w", justify="left", wraplength=520)
        self.badge.grid(row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 8))
        self.var.trace_add("write", lambda *a: self._set_badge("● Non testata", MUTED))

    def get(self) -> str:
        return self.var.get().strip()

    def _set_badge(self, text: str, color: str):
        try:
            self.badge.configure(text=text, text_color=color)
        except tk.TclError:
            pass

    def _toggle(self):
        self._shown = not self._shown
        self.entry.configure(show="" if self._shown else "•")

    def _clear(self):
        if self._on_delete is not None:
            self._on_delete(self)
        else:
            self.var.set("")
            self._set_badge("● Chiave rimossa (salva per confermare)", "#b45309")

    def _set_buttons_busy(self, busy_btn_name: str, busy_text: str):
        """Disabilita tutti i pulsanti di test della riga durante un'operazione per evitare corse e click multipli."""
        for name in ("test_btn", "test_35_btn", "test_38_btn", "test_free_btn"):
            btn = getattr(self, name, None)
            if btn:
                try:
                    if name == busy_btn_name:
                        btn.configure(state="disabled", text=busy_text)
                    else:
                        btn.configure(state="disabled")
                except Exception:
                    pass

    def _reset_buttons_state(self):
        """Ripristina lo stato normale e i testi originali di tutti i pulsanti di test della riga."""
        orig_labels = {
            "test_btn": "Testa Chiave",
            "test_35_btn": "⚡ Richiesta Gemini 3.5",
            "test_38_btn": "⚡ Richiesta Gemini 3.8",
            "test_free_btn": "⚡ Richiesta Modello Free",
        }
        for name, orig_text in orig_labels.items():
            btn = getattr(self, name, None)
            if btn:
                try:
                    btn.configure(state="normal", text=orig_text)
                except Exception:
                    pass

    def _test(self):
        key = self.get()
        if not key:
            self._set_badge("✗ Errore / Non valida", "#dc2626")
            return
        self._set_buttons_busy("test_btn", "Verifica...")
        self._set_badge("● Verifica in corso...", MUTED)

        def done(result, error):
            self._reset_buttons_state()
            if not self.winfo_exists():
                return
            if error is not None:
                self._set_badge("✗ Errore / Non valida", "#dc2626")
                return
            ok, msg = result
            if ok:
                self._set_badge(msg if "attiva" in msg.lower() else f"✓ {msg}", "#16a34a")
            else:
                self._set_badge(msg if ("errore" in msg.lower() or "timeout" in msg.lower()) else f"✗ {msg}", "#dc2626")

        self._task = BackgroundTask(self, lambda progress: self._tester(key), done).start()

    def _test_gemini_gen(self, model: str, display_name: str):
        key = self.get()
        if not key:
            self._set_badge("✗ Inserisci prima una chiave API valida", "#dc2626")
            return

        busy_btn = "test_35_btn" if "3.5" in model else "test_38_btn"
        self._set_buttons_busy(busy_btn, f"Attesa {display_name}...")
        self._set_badge(f"● Invio richiesta a {display_name} in corso...", MUTED)

        def done(result, error):
            self._reset_buttons_state()
            if not self.winfo_exists():
                return
            if error is not None:
                self._set_badge(f"✗ Errore: {error}", "#dc2626")
                return
            ok, msg = result
            if ok:
                self._set_badge(f"{display_name}: {msg}", "#16a34a")
            else:
                col = "#d97706" if ("503" in msg or "429" in msg or "⚠️" in msg) else "#dc2626"
                self._set_badge(f"{display_name}: {msg}", col)

        self._task = BackgroundTask(
            self,
            lambda progress: test_gemini_generation(key, model=model, timeout=12.0),
            done
        ).start()

    def _test_openrouter_gen(self):
        key = self.get()
        if not key:
            self._set_badge("✗ Inserisci prima una chiave API valida", "#dc2626")
            return

        self._set_buttons_busy("test_free_btn", "Attesa Free...")
        self._set_badge("● Invio richiesta a OpenRouter (Modello Free) in corso...", MUTED)

        def done(result, error):
            self._reset_buttons_state()
            if not self.winfo_exists():
                return
            if error is not None:
                self._set_badge(f"✗ Errore: {error}", "#dc2626")
                return
            ok, msg = result
            if ok:
                self._set_badge(f"Free: {msg}", "#16a34a")
            else:
                col = "#d97706" if ("503" in msg or "429" in msg or "⚠️" in msg) else "#dc2626"
                self._set_badge(f"Free: {msg}", col)

        self._task = BackgroundTask(
            self,
            lambda progress: test_openrouter_generation(key, timeout=15.0),
            done
        ).start()





class AddApiKeyDialog(ctk.CTkToplevel):

    """Finestra modale semplice e chiara per aggiungere una nuova chiave API scegliendo il tipo e vedendo i modelli."""



    TYPE_GEMINI = "Google Gemini (Gratuito)"

    TYPE_OPENROUTER = "OpenRouter (Backup & Sonnet)"



    def __init__(self, parent, on_added: Callable[[str, str], None], gemini_model: str = DEFAULT_GEMINI_MODEL):

        super().__init__(parent)

        self.title("➕ Aggiungi Chiave API")

        self.resizable(False, False)

        self.configure(fg_color="#f1f5f9")

        self._on_added = on_added

        self._gemini_model = gemini_model

        self._shown = False

        _center_modal(self, parent, 540, 420)

        _make_modal(self, parent)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()



    def _build(self):

        card = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color=BORDER)

        card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)



        ctk.CTkLabel(

            card, text="🔑 Aggiungi una nuova Chiave API",

            font=("Segoe UI", 16, "bold"), text_color=TEXT

        ).pack(anchor="w", padx=18, pady=(16, 4))



        ctk.CTkLabel(

            card, text="1. Seleziona che tipo di chiave vuoi aggiungere:",

            font=("Segoe UI", 11, "bold"), text_color=TEXT

        ).pack(anchor="w", padx=18, pady=(8, 4))



        self.type_seg = ctk.CTkSegmentedButton(

            card,

            values=[self.TYPE_GEMINI, self.TYPE_OPENROUTER],

            height=36,

            font=("Segoe UI", 11, "bold"),

            selected_color=PRIMARY,

            selected_hover_color=PRIMARY_HOVER,

            command=self._on_type_changed

        )

        self.type_seg.pack(fill=tk.X, padx=18, pady=(0, 8))

        self.type_seg.set(self.TYPE_GEMINI)



        # Box informativo dinamico sui modelli abilitati da questa chiave

        self.models_box = ctk.CTkFrame(card, corner_radius=10, fg_color="#f0f9ff", border_width=1, border_color="#bae6fd")

        self.models_box.pack(fill=tk.X, padx=18, pady=(2, 10))

        self.models_lbl = ctk.CTkLabel(

            self.models_box, text="", font=("Segoe UI", 10),

            text_color="#0f172a", justify="left", anchor="w", wraplength=460

        )

        self.models_lbl.pack(fill=tk.X, padx=12, pady=8)



        ctk.CTkLabel(

            card, text="2. Incolla la Chiave API:",

            font=("Segoe UI", 11, "bold"), text_color=TEXT

        ).pack(anchor="w", padx=18, pady=(4, 4))



        entry_row = ctk.CTkFrame(card, fg_color="transparent")

        entry_row.pack(fill=tk.X, padx=18, pady=(0, 4))



        self.key_var = tk.StringVar()

        self.entry = ctk.CTkEntry(

            entry_row, textvariable=self.key_var, show="•", height=36, corner_radius=8,

            placeholder_text="Incolla qui la chiave (es. AIza... oppure sk-or-v1-...)"

        )

        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)



        ctk.CTkButton(

            entry_row, text="📋 Incolla", width=76, height=36, corner_radius=8,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT,

            font=("Segoe UI", 10, "bold"), command=self._paste_clip

        ).pack(side=tk.LEFT, padx=(6, 0))



        ctk.CTkButton(

            entry_row, text="👁️", width=38, height=36, corner_radius=8,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT,

            command=self._toggle_show

        ).pack(side=tk.LEFT, padx=(6, 0))



        self.status_lbl = ctk.CTkLabel(

            card, text="", font=("Segoe UI", 10, "bold"),

            text_color=MUTED, anchor="w", justify="left", wraplength=470

        )

        self.status_lbl.pack(fill=tk.X, padx=18, pady=(4, 4))



        btn_bar = ctk.CTkFrame(card, fg_color="transparent")

        btn_bar.pack(fill=tk.X, padx=18, pady=(6, 14), side=tk.BOTTOM)



        ctk.CTkButton(

            btn_bar, text="Annulla", width=95, height=38, corner_radius=10,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT,

            command=self.destroy

        ).pack(side=tk.LEFT)



        self.add_direct_btn = ctk.CTkButton(

            btn_bar, text="Aggiungi direttamente", width=150, height=38, corner_radius=10,

            fg_color="#e0f2fe", hover_color="#bae6fd", text_color=PRIMARY_HOVER,

            font=("Segoe UI", 11, "bold"), command=self._save_direct

        )

        self.add_direct_btn.pack(side=tk.RIGHT, padx=(8, 0))



        self.test_add_btn = ctk.CTkButton(

            btn_bar, text="✓ Verifica e Aggiungi", height=38, corner_radius=10,

            fg_color="#16a34a", hover_color="#15803d",

            font=("Segoe UI", 12, "bold"), command=self._test_and_add

        )

        self.test_add_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))



        self._on_type_changed(self.TYPE_GEMINI)

        self.after(180, self.entry.focus_set)



    def _current_provider_type(self) -> str:

        return "openrouter" if self.type_seg.get() == self.TYPE_OPENROUTER else "gemini"



    def _on_type_changed(self, value: str):

        if value == self.TYPE_OPENROUTER:

            self.models_box.configure(fg_color="#fff7ed", border_color="#fed7aa")

            self.models_lbl.configure(

                text="🟠 Tipo: OpenRouter (Consigliata come paracadute e arbitro)\n"

                     "• Abilita GPT-6 Luna, Claude Haiku 5.5 e Nemotron Gratis in caso di picchi.\n"

                     "• Abilita l'arbitro matematico economico (~$0.0025) e Claude Sonnet 5.5 on-demand."

            )

        else:

            self.models_box.configure(fg_color="#f0f9ff", border_color="#bae6fd")

            self.models_lbl.configure(

                text="🔵 Tipo: Google Gemini (AI Studio — 100% Gratuita)\n"

                     "• Abilita Gemini 3.5 Flash-Lite / 3.8 Flash: 500 schede gratuite al giorno per chiave.\n"

                     "• Inserendo 2 chiavi di 2 account Google diversi ottieni 1.000 schede/giorno a costo zero."

            )



    def _paste_clip(self):

        try:

            clip = self.clipboard_get().strip()

            if clip:

                self.key_var.set(clip)

                # Riconoscimento automatico del tipo di chiave dal prefisso

                if clip.startswith("sk-or-"):

                    self.type_seg.set(self.TYPE_OPENROUTER)

                    self._on_type_changed(self.TYPE_OPENROUTER)

                elif clip.startswith("AIza") or clip.startswith("AQ."):

                    self.type_seg.set(self.TYPE_GEMINI)

                    self._on_type_changed(self.TYPE_GEMINI)

        except Exception:

            pass



    def _toggle_show(self):

        self._shown = not self._shown

        self.entry.configure(show="" if self._shown else "•")



    def _save_direct(self):

        key = self.key_var.get().strip()

        if not key:

            self.status_lbl.configure(text="● Inserisci prima la chiave API.", text_color="#dc2626")

            return

        self._on_added(self._current_provider_type(), key)

        self.destroy()



    def _test_and_add(self):

        key = self.key_var.get().strip()

        if not key:

            self.status_lbl.configure(text="● Inserisci prima la chiave API.", text_color="#dc2626")

            return

        ptype = self._current_provider_type()

        self.test_add_btn.configure(state="disabled", text="Verifica in corso...")

        self.status_lbl.configure(text="● Verifica della chiave in corso...", text_color=MUTED)



        tester = (lambda: test_openrouter_api_key(key)) if ptype == "openrouter" else (

            lambda: test_gemini_api_key(key, "gemini-3.5-flash-lite")

        )



        def done(result, error):

            try:

                self.test_add_btn.configure(state="normal", text="✓ Verifica e Aggiungi")

            except tk.TclError:

                return

            ok, msg = (False, str(error)) if error is not None else result

            if ok:

                self._on_added(ptype, key)

                self.destroy()

            else:

                self.status_lbl.configure(

                    text=f"● Verifica fallita: {msg} (puoi correggerla o cliccare 'Aggiungi direttamente')",

                    text_color="#dc2626"

                )



        BackgroundTask(self, lambda p: tester(), done).start()





AddApiKeyDialogV3 = AddApiKeyDialog





class CascadeVisualMap(ctk.CTkFrame):

    """Mappa visuale interattiva dinamica dell'architettura di elaborazione IA.

    

    Si riconfigura in tempo reale quando l'utente cambia opzione nel dropdown dei provider

    (Cascata v1, Cascata v2 o Modello Singolo), evidenziando Claude Haiku 5.5 come arbitro

    matematico economico (~$0.0025) e Claude Sonnet 5.5 come modalità protetta on-demand.

    """



    COLOR_GREEN_BG = "#dcfce7"

    COLOR_GREEN_BORDER = "#86efac"

    COLOR_GREEN_TEXT = "#166534"



    COLOR_AMBER_BG = "#fef3c7"

    COLOR_AMBER_BORDER = "#fde68a"

    COLOR_AMBER_TEXT = "#92400e"



    COLOR_RED_BG = "#fee2e2"

    COLOR_RED_BORDER = "#fca5a5"

    COLOR_RED_TEXT = "#991b1b"



    COLOR_BLUE_BG = "#e0f2fe"

    COLOR_BLUE_BORDER = "#bae6fd"

    COLOR_BLUE_TEXT = "#0369a1"



    def __init__(self, parent, config: Optional[Dict[str, Any]] = None, **kwargs):

        super().__init__(parent, corner_radius=12, fg_color="#f8fafc", border_width=1, border_color="#e2e8f0", **kwargs)

        self._cfg = config or {}

        self._current_provider = self._cfg.get("active_provider", "cascade")

        self._build_ui()

        self.update_status(

            self._cfg.get("gemini_api_key", ""),

            self._cfg.get("gemini_api_key_2", ""),

            self._cfg.get("openrouter_api_key", ""),

            provider=self._current_provider,

        )



    def _build_ui(self):

        # Header banner con capacità totale / modalità

        self.banner = ctk.CTkFrame(self, corner_radius=10, fg_color="#f1f5f9", border_width=1, border_color="#cbd5e1")

        self.banner.pack(fill=tk.X, padx=10, pady=(10, 8))



        self.capacity_lbl = ctk.CTkLabel(

            self.banner,

            text="⚡ Flusso IA: —",

            font=("Segoe UI", 12, "bold"),

            text_color="#0f172a"

        )

        self.capacity_lbl.pack(side=tk.LEFT, padx=12, pady=8)



        self.overall_badge = ctk.CTkLabel(

            self.banner,

            text="VERIFICA",

            font=("Segoe UI", 10, "bold"),

            corner_radius=6,

            fg_color="#e2e8f0",

            text_color="#475569",

            padx=8,

            pady=3

        )

        self.overall_badge.pack(side=tk.RIGHT, padx=12, pady=8)



        # Contenitore dinamico delle schede dei piani

        self.cards_frame = ctk.CTkFrame(self, fg_color="transparent")

        self.cards_frame.pack(fill=tk.X, padx=10, pady=(0, 10))



    def update_mode(self, provider: str, key1: Optional[str] = None, key2: Optional[str] = None, or_key: Optional[str] = None, arbitrator_model: Optional[str] = None, enable_arbitrator: Optional[bool] = None):

        """Cambia la modalità visualizzata (v1, v2 o singolo modello) e aggiorna i grafici."""

        self._current_provider = provider

        self.update_status(key1, key2, or_key, provider=provider, arbitrator_model=arbitrator_model, enable_arbitrator=enable_arbitrator)



    def update_status(self, key1: Optional[str] = None, key2: Optional[str] = None, or_key: Optional[str] = None, provider: Optional[str] = None, arbitrator_model: Optional[str] = None, enable_arbitrator: Optional[bool] = None):

        if provider is not None:

            self._current_provider = provider

        prov = self._current_provider or "cascade"



        # Se una chiave non viene passata, mantieni quella memorizzata nella configurazione

        k1 = self._cfg.get("gemini_api_key", "") if key1 is None else key1

        k2 = self._cfg.get("gemini_api_key_2", "") if key2 is None else key2

        ok = self._cfg.get("openrouter_api_key", "") if or_key is None else or_key



        self._cfg["gemini_api_key"] = k1

        self._cfg["gemini_api_key_2"] = k2

        self._cfg["openrouter_api_key"] = ok



        if arbitrator_model is not None:

            self._cfg["cascade_arbitrator_model"] = arbitrator_model

        if enable_arbitrator is not None:

            self._cfg["enable_auto_arbitrator"] = enable_arbitrator



        # Pulisci le card esistenti

        for w in self.cards_frame.winfo_children():

            try:

                w.destroy()

            except Exception:

                pass



        cfg = {"gemini_api_key": k1, "gemini_api_key_2": k2, "openrouter_api_key": ok}

        st = get_pipeline_status(cfg)

        cap = st["daily_free_capacity"]

        rpm = st["rpm_capacity"]

        has_google = (st["google_count"] > 0)

        has_or = st["has_openrouter"]



        arb_m = self._cfg.get("cascade_arbitrator_model") or self._cfg.get("openrouter_arbitrator_model", DEFAULT_CASCADE_ARBITRATOR_MODEL)

        arb_en = self._cfg.get("enable_auto_arbitrator", True)

        is_gem_arb = arb_m.startswith("gemini") or "gemini" in arb_m.lower()

        arb_has_key = has_google if is_gem_arb else has_or

        arb_name = ARBITRATOR_DISPLAY_MAP.get(arb_m, arb_m).split(" (")[0]



        if not arb_en:

            arb_title = f"ARBITRO MATEMATICO — {arb_name} (Disattivato)"

            arb_desc = "Controllo automatico discrepanze disattivato. Eventuali errori delegati alla revisione manuale."

            arb_st = "red"

            arb_bdg = "⚪ DISATTIVATO"

        elif arb_has_key:

            arb_title = f"ARBITRO MATEMATICO — {arb_name}"

            arb_desc = f"Interviene in automatico solo se le somme non tornano · Risoluzione chirurgica con {arb_name}"

            arb_st = "green" if is_gem_arb else "amber"

            arb_bdg = "⚖️ ARBITRO PRONTO"

        else:

            arb_title = f"ARBITRO MATEMATICO — {arb_name} (Richiede Chiave)"

            arb_desc = f"Configura la chiave {'Google Gemini' if is_gem_arb else 'OpenRouter'} per abilitare l'arbitro."

            arb_st = "red"

            arb_bdg = "🔴 CHIAVE MANCANTE" 



        if prov == "cascade":

            # === CASCATA V1 (Standard Consigliata) ===

            if cap >= 1000:

                self.capacity_lbl.configure(text=f"⚡ Cascata v1 Consigliata · {cap:,} schede/giorno ({rpm} RPM) · 0,00 €", text_color="#166534")

                self.banner.configure(fg_color="#dcfce7", border_color="#86efac")

                self.overall_badge.configure(text="MASSIMA CAPACITÀ 🟢", fg_color="#bbf7d0", text_color="#166534")

            elif cap == 500:

                self.capacity_lbl.configure(text=f"⚡ Cascata v1 Consigliata · {cap:,} schede/giorno ({rpm} RPM) · 0,00 €", text_color="#92400e")

                self.banner.configure(fg_color="#fef3c7", border_color="#fde68a")

                self.overall_badge.configure(text="BUONA CAPACITÀ 🟡", fg_color="#fde68a", text_color="#92400e")

            else:

                self.capacity_lbl.configure(text="⚠️ Cascata v1 · Inserisci chiave Google Gemini per schede gratis", text_color="#991b1b")

                self.banner.configure(fg_color="#fee2e2", border_color="#fca5a5")

                self.overall_badge.configure(text="INCOMPLETA 🔴", fg_color="#fecaca", text_color="#991b1b")



            b_txt_1 = "🟢 2/2 ATTIVE (1.000 RPD)" if st["google_count"] == 2 else ("🟡 1/2 ATTIVA (500 RPD)" if st["google_count"] == 1 else "🔴 NON ATTIVO")

            self._add_tier_card(

                "PIANO 1 — Google Gemini 3.5 Flash-Lite (Primario Gratuito)",

                "500-1.000 schede/giorno gratis · Risposta ultra-rapida <1s (0,00 €)",

                status="green" if has_google else "red",

                badge_text=b_txt_1

            )

            self._add_tier_card(

                "PIANO 2 — GPT-6 Luna Low Reasoning (Buffer OpenRouter)",

                "Subentra solo su rate-limit 429 o picchi · Costo ridotto ~$0.0016/scheda (0,16 c€)",

                status="green" if has_or else "red",

                badge_text="🟢 BUFFER ATTIVO" if has_or else "🔴 DISATTIVATO (Richiede OpenRouter)"

            )

            self._add_tier_card(

                "PIANO 3 — Claude Haiku 5.5 Low Reasoning (Fallback Alta Affidabilità)",

                "Subentra se Luna non risponde o errore server · ~$0.0025/scheda",

                status="green" if has_or else "red",

                badge_text="🟢 FALLBACK PRONTO" if has_or else "🔴 DISATTIVATO"

            )

            self._add_tier_card(

                arb_title,

                arb_desc,

                status=arb_st,

                badge_text=arb_bdg

            )

            self._add_tier_card(

                "MASSIMA PRECISIONE — Claude Sonnet 5.5 (Solo Manuale Protetto)",

                "NON parte mai da solo: attivabile solo con Shift + Click su 'Rianalizza IA' o Ctrl+Shift+R",

                status="amber" if has_or else "red",

                badge_text="🔒 PROTETTO (Shift+Click)" if has_or else "🔴 NON CONFIGURATO"

            )



        elif prov == "cascade_v2":

            # === CASCATA V2 (Gemini 3.8 Primario) ===

            b_txt_1 = "🟢 2/2 ATTIVE" if st["google_count"] == 2 else ("🟡 1/2 ATTIVA" if st["google_count"] == 1 else "🔴 NON ATTIVO")

            if cap >= 1000:

                self.capacity_lbl.configure(text=f"⚡ Cascata v2 (Gemini 3.8 Primario) · Ultra-Velocità · {cap:,} schede/giorno gratis", text_color="#0369a1")

                self.banner.configure(fg_color="#e0f2fe", border_color="#bae6fd")

                self.overall_badge.configure(text="ALTA VELOCITÀ ⚡", fg_color="#bae6fd", text_color="#0369a1")

            elif cap == 500:

                self.capacity_lbl.configure(text=f"⚡ Cascata v2 (Gemini 3.8 Primario) · Ultra-Velocità · {cap:,} schede/giorno gratis", text_color="#0369a1")

                self.banner.configure(fg_color="#e0f2fe", border_color="#bae6fd")

                self.overall_badge.configure(text="BUONA CAPACITÀ 🟡", fg_color="#fde68a", text_color="#92400e")

            else:

                self.capacity_lbl.configure(text="⚠️ Cascata v2 · Inserisci chiave Google Gemini per schede gratis", text_color="#991b1b")

                self.banner.configure(fg_color="#fee2e2", border_color="#fca5a5")

                self.overall_badge.configure(text="INCOMPLETA 🔴", fg_color="#fecaca", text_color="#991b1b")



            self._add_tier_card(

                "PIANO 1 — Google Gemini 3.8 Flash (Primario Velocità Estrema)",

                "Google API Diretta · 0,00 € · Comprensione visiva avanzata e massima velocità",

                status="green" if has_google else "red",

                badge_text=b_txt_1

            )

            self._add_tier_card(

                "PIANO 2 — Google Gemini 3.5 Flash-Lite (Riserva Google Gratuita)",

                "Subentra immediatamente se Gemini 3.8 è saturo (503/429) · 0,00 €",

                status="green" if has_google else "red",

                badge_text="🟢 RISERVA GRATIS" if has_google else "🔴 NON ATTIVO"

            )

            self._add_tier_card(

                "PIANO 3 — GPT-6 Luna Low Reasoning (Buffer OpenRouter)",

                "Subentra se entrambe le chiavi Google sono sature · ~$0.0016/scheda (0,16 c€)",

                status="green" if has_or else "red",

                badge_text="🟢 BUFFER ATTIVO" if has_or else "🔴 DISATTIVATO"

            )

            self._add_tier_card(

                "PIANO 4 — Claude Haiku 5.5 Low Reasoning (Fallback Finale)",

                "Ultimo fallback ad alta affidabilità · ~$0.0025/scheda",

                status="green" if has_or else "red",

                badge_text="🟢 FALLBACK PRONTO" if has_or else "🔴 DISATTIVATO"

            )

            self._add_tier_card(

                arb_title,

                arb_desc,

                status=arb_st,

                badge_text=arb_bdg

            )

            self._add_tier_card(

                "MASSIMA PRECISIONE — Claude Sonnet 5.5 (Solo Manuale Protetto)",

                "NON parte mai da solo: attivabile solo con Shift + Click su 'Rianalizza IA' o Ctrl+Shift+R",

                status="amber" if has_or else "red",

                badge_text="🔒 PROTETTO (Shift+Click)" if has_or else "🔴 NON CONFIGURATO"

            )



        else:

            # === MODELLO SINGOLO DIRETTO ===

            prov_name = SettingsDialog.PROVIDER_DISPLAY_MAP.get(prov, prov) if hasattr(SettingsDialog, "PROVIDER_DISPLAY_MAP") else prov

            self.capacity_lbl.configure(text=f"🎯 Modalità Modello Diretto: {prov_name}", text_color="#0f172a")

            self.banner.configure(fg_color="#f1f5f9", border_color="#cbd5e1")

            self.overall_badge.configure(text="MODELLO SINGOLO 🎯", fg_color="#e2e8f0", text_color="#334155")



            is_google_model = ("gemini" in prov)

            active_key_ready = has_google if is_google_model else has_or



            self._add_tier_card(

                f"🎯 MODELLO DIRETTO SELEZIONATO: {prov_name}",

                "Tutte le schede vengono elaborate direttamente con questo modello. Nessun passaggio automatico.",

                status="green" if active_key_ready else "red",

                badge_text="🟢 ATTIVO AL 100%" if active_key_ready else "🔴 CHIAVE MANCANTE"

            )

            self._add_tier_card(

                "⚡ Cascate e Fallback Disattivati",

                "In modalità singola, in caso di errore o esaurimento crediti la lettura non tenterà altri modelli.",

                status="amber",

                badge_text="⚪ DISATTIVATI"

            )

            if prov == "or_sonnet":

                self._add_tier_card(

                    "⚠️ AVVISO COSTO — Claude Sonnet 5.5 Selezionato Diretto",

                    "Attenzione: ogni scheda scansionata consumerà crediti OpenRouter (~$0.04/scheda).",

                    status="amber",

                    badge_text="⚠️ COSTI ATTIVI"

                )

            else:

                self._add_tier_card(

                    arb_title,

                    arb_desc,

                    status=arb_st,

                    badge_text=arb_bdg

                )

                self._add_tier_card(

                    "MASSIMA PRECISIONE — Claude Sonnet 5.5 (Solo Manuale Protetto)",

                    "Disponibile on-demand con Shift + Click su 'Rianalizza IA' o Ctrl+Shift+R",

                    status="amber" if has_or else "red",

                    badge_text="🔒 PROTETTO (Shift+Click)" if has_or else "🔴 NON CONFIGURATO"

                )



    def _add_tier_card(self, title: str, subtitle: str, status: str, badge_text: str):

        if status == "green":

            bg, border, text_color = self.COLOR_GREEN_BG, self.COLOR_GREEN_BORDER, self.COLOR_GREEN_TEXT

        elif status == "amber":

            bg, border, text_color = self.COLOR_AMBER_BG, self.COLOR_AMBER_BORDER, self.COLOR_AMBER_TEXT

        elif status == "blue":

            bg, border, text_color = self.COLOR_BLUE_BG, self.COLOR_BLUE_BORDER, self.COLOR_BLUE_TEXT

        else:

            bg, border, text_color = self.COLOR_RED_BG, self.COLOR_RED_BORDER, self.COLOR_RED_TEXT



        card = ctk.CTkFrame(self.cards_frame, corner_radius=8, fg_color="#ffffff", border_width=1, border_color=border)

        card.pack(fill=tk.X, pady=3)



        top_row = ctk.CTkFrame(card, fg_color="transparent")

        top_row.pack(fill=tk.X, padx=10, pady=(6, 2))



        ctk.CTkLabel(top_row, text=title, font=("Segoe UI", 11, "bold"), text_color="#0f172a").pack(side=tk.LEFT)

        ctk.CTkLabel(

            top_row, text=badge_text, font=("Segoe UI", 9, "bold"),

            corner_radius=6, fg_color=bg, text_color=text_color, padx=6, pady=2

        ).pack(side=tk.RIGHT)



        ctk.CTkLabel(

            card, text=subtitle, font=("Segoe UI", 9),

            text_color="#64748b", justify="left", anchor="w"

        ).pack(fill=tk.X, padx=10, pady=(0, 6))





CascadeVisualMapV3 = CascadeVisualMap





class WelcomeSetupDialogV3(ctk.CTkToplevel):

    """Dialog modale di benvenuto e onboarding per configurare l'architettura a cascata (4 Piani)."""



    def __init__(

        self,

        parent,

        on_saved: Callable[..., None],

        model: str = DEFAULT_CASCADE_GEMINI_MODEL,

        config: Optional[Dict[str, Any]] = None,

    ):

        super().__init__(parent)

        self.title("Configurazione Pipeline a Cascata — Banco Alimentare Scanner")

        self.resizable(False, False)

        self.configure(fg_color="#f1f5f9")

        self._on_saved = on_saved

        self._model = model

        self._cfg = config or {}

        

        self.k1_show = False

        self.k2_show = False

        self.or_show = False



        _center_modal(self, parent, 640, 720)

        _make_modal(self, parent)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build()



    def _build(self):

        # Card container

        main_card = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color=BORDER)

        main_card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)



        # Bottom actions bar (fixed at bottom)

        btns_bar = ctk.CTkFrame(main_card, fg_color="#f8fafc", corner_radius=10, border_width=1, border_color=BORDER)

        btns_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(6, 14))



        self.later_btn = ctk.CTkButton(

            btns_bar, text="Più tardi", width=100, height=38, corner_radius=8,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT,

            command=self.destroy

        )

        self.later_btn.pack(side=tk.LEFT, padx=10, pady=8)



        self.save_btn = ctk.CTkButton(

            btns_bar, text="✓  Salva e Attiva Cascata", height=38, corner_radius=8,

            fg_color="#16a34a", hover_color="#15803d", font=("Segoe UI", 12, "bold"),

            command=self._save_and_activate

        )

        self.save_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 10), pady=8)



        # Scrollable content area

        scroll = ctk.CTkScrollableFrame(main_card, fg_color="transparent", corner_radius=0)

        scroll.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(10, 4))



        # Header

        hdr_frame = ctk.CTkFrame(scroll, fg_color="transparent")

        hdr_frame.pack(fill=tk.X, padx=12, pady=(4, 6))



        top_title_row = ctk.CTkFrame(hdr_frame, fg_color="transparent")

        top_title_row.pack(fill=tk.X)

        ctk.CTkLabel(top_title_row, text="🥫", font=("Segoe UI Emoji", 26)).pack(side=tk.LEFT, padx=(0, 8))

        ctk.CTkLabel(

            top_title_row,

            text="Pipeline Intelligente a Cascata (4 Piani)",

            font=("Segoe UI", 16, "bold"),

            text_color=TEXT

        ).pack(side=tk.LEFT)



        ctk.CTkLabel(

            hdr_frame,

            text="Per garantire una precisione ottimale (95-100%) a costo zero (1.000 schede gratis/giorno),\n"

                 "inserisci qui le tue chiavi. Lo schema in basso si adatta dinamicamente in tempo reale.",

            font=("Segoe UI", 10),

            text_color=MUTED,

            justify="left",

            wraplength=570

        ).pack(anchor="w", pady=(4, 8))



        # Box 0: Sblocco Rapido Profilo Giovanni con Password

        unlock_box = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#f0f9ff", border_width=1, border_color="#bae6fd")

        unlock_box.pack(fill=tk.X, padx=12, pady=(0, 10))



        ctk.CTkLabel(

            unlock_box,

            text="🔐 Sblocco Rapido Profilo Giovanni (con Password)",

            font=("Segoe UI", 12, "bold"),

            text_color="#0369a1"

        ).pack(anchor="w", padx=14, pady=(10, 2))



        ctk.CTkLabel(

            unlock_box,

            text="Se possiedi la password di sblocco, inseriscila qui per caricare automaticamente tutte le 3 chiavi API:",

            font=("Segoe UI", 9),

            text_color="#334155"

        ).pack(anchor="w", padx=14, pady=(0, 6))



        pwd_row = ctk.CTkFrame(unlock_box, fg_color="transparent")

        pwd_row.pack(fill=tk.X, padx=14, pady=(0, 4))



        self.pwd_var = tk.StringVar()

        self.pwd_entry = ctk.CTkEntry(

            pwd_row,

            textvariable=self.pwd_var,

            show="•",

            height=34,

            corner_radius=8,

            placeholder_text="Inserisci la password di sblocco..."

        )

        self.pwd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.pwd_entry.bind("<Return>", lambda e: self._unlock_with_password())

        self.pwd_entry.bind("<KP_Enter>", lambda e: self._unlock_with_password())



        self.unlock_btn = ctk.CTkButton(

            pwd_row,

            text="🔓 Sblocca Chiavi",

            width=140,

            height=34,

            corner_radius=8,

            fg_color=PRIMARY,

            hover_color=PRIMARY_HOVER,

            font=("Segoe UI", 11, "bold"),

            command=self._unlock_with_password

        )

        self.unlock_btn.pack(side=tk.RIGHT)



        self.unlock_status_lbl = ctk.CTkLabel(

            unlock_box, text="", font=("Segoe UI", 10, "bold"), text_color=MUTED, anchor="w"

        )

        self.unlock_status_lbl.pack(fill=tk.X, padx=14, pady=(0, 8))



        # Box Cartella Fogli Excel Colletta (opzionale)

        folder_box = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color=BORDER)

        folder_box.pack(fill=tk.X, padx=12, pady=(0, 10))



        ctk.CTkLabel(

            folder_box,

            text="📁 Cartella Fogli Excel Colletta (opzionale)",

            font=("Segoe UI", 11, "bold"),

            text_color=TEXT

        ).pack(anchor="w", padx=14, pady=(8, 2))



        folder_row = ctk.CTkFrame(folder_box, fg_color="transparent")

        folder_row.pack(fill=tk.X, padx=14, pady=(2, 10))



        init_folder = str(self._cfg.get("last_colletta_folder") or default_colletta_folder()).strip()

        self.folder_var = tk.StringVar(value=init_folder)

        self.folder_entry = ctk.CTkEntry(

            folder_row,

            textvariable=self.folder_var,

            height=32,

            corner_radius=8,

            placeholder_text="Percorso cartella file Excel della Colletta..."

        )

        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))



        self.browse_folder_btn = ctk.CTkButton(

            folder_row,

            text="📁 Sfoglia...",

            width=110,

            height=32,

            corner_radius=8,

            fg_color="#e2e8f0",

            hover_color="#cbd5e1",

            text_color=TEXT,

            font=("Segoe UI", 10, "bold"),

            command=self._choose_colletta_folder

        )

        self.browse_folder_btn.pack(side=tk.RIGHT)



        # Visual Map Widget

        self.cascade_map = CascadeVisualMapV3(scroll, config=self._cfg)

        self.cascade_map.pack(fill=tk.X, padx=12, pady=(0, 12))



        # Form Inputs Section

        inputs_box = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color=BORDER)

        inputs_box.pack(fill=tk.X, padx=12, pady=(0, 8))



        ctk.CTkLabel(

            inputs_box, text="Inserimento Chiavi API",

            font=("Segoe UI", 12, "bold"), text_color=TEXT

        ).pack(anchor="w", padx=14, pady=(10, 4))



        # Key 1: Google Gemini 1

        self.k1_var = tk.StringVar(value=self._cfg.get("gemini_api_key", ""))

        self.k1_entry = self._build_key_input_row(

            inputs_box,

            title="1. Google Gemini Key #1 (Account Principale — 500 gratis/giorno)",

            subtitle="Account Google AI Studio principale · 0,00 € · 500 RPD / 15 RPM",

            var=self.k1_var,

            toggle_cmd=self._toggle_k1,

            paste_cmd=lambda: self._paste_to_var(self.k1_var)

        )



        # Key 2: Google Gemini 2

        self.k2_var = tk.StringVar(value=self._cfg.get("gemini_api_key_2", ""))

        self.k2_entry = self._build_key_input_row(

            inputs_box,

            title="2. Google Gemini Key #2 (Secondo Account — Raddoppia a 1.000)",

            subtitle="Crea un 2° account Google per raddoppiare a 1.000 schede/giorno (0,00 €)",

            var=self.k2_var,

            toggle_cmd=self._toggle_k2,

            paste_cmd=lambda: self._paste_to_var(self.k2_var)

        )



        # Key 3: OpenRouter

        self.or_var = tk.StringVar(value=self._cfg.get("openrouter_api_key", ""))

        self.or_entry = self._build_key_input_row(

            inputs_box,

            title="3. OpenRouter API Key (Buffer di Sicurezza & Arbitro)",

            subtitle="Buffer anti-blocco (~0,09 c€/scheda), paracadute free e arbitro Claude Sonnet",

            var=self.or_var,

            toggle_cmd=self._toggle_or,

            paste_cmd=lambda: self._paste_to_var(self.or_var)

        )



        # Quick Links

        links_box = ctk.CTkFrame(inputs_box, fg_color="transparent")

        links_box.pack(fill=tk.X, padx=14, pady=(4, 12))



        ctk.CTkButton(

            links_box, text="🌐 Ottieni chiave Google AI Studio (Gratuita)", height=28, corner_radius=6,

            fg_color="#e0f2fe", hover_color="#bae6fd", text_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

            command=lambda: webbrowser.open(AI_STUDIO_URL)

        ).pack(side=tk.LEFT, padx=(0, 8))



        ctk.CTkButton(

            links_box, text="🌐 Dashboard & Chiavi OpenRouter", height=28, corner_radius=6,

            fg_color="#fef3c7", hover_color="#fde68a", text_color="#b45309", font=("Segoe UI", 10, "bold"),

            command=lambda: webbrowser.open(OPENROUTER_KEYS_URL)

        ).pack(side=tk.LEFT)



        # Status / Feedback label

        self.status_lbl = ctk.CTkLabel(

            scroll, text="", font=("Segoe UI", 10), text_color=MUTED,

            wraplength=570, justify="left"

        )

        self.status_lbl.pack(padx=14, pady=(2, 6), anchor="w")



        # Dynamic trace listeners

        self.k1_var.trace_add("write", lambda *_: self._refresh_map())

        self.k2_var.trace_add("write", lambda *_: self._refresh_map())

        self.or_var.trace_add("write", lambda *_: self._refresh_map())



        self._refresh_map()



    def _unlock_with_password(self) -> bool:

        if (

            not self.pwd_var.get().strip()

            and getattr(self, "_keys_unlocked_by_pwd", False)

            and (self.k1_var.get().strip() or self.k2_var.get().strip() or self.or_var.get().strip())

        ):

            self._save_and_activate()

            return True

        keys = unlock_giovanni_keys(self.pwd_var.get())

        if not keys:

            self.unlock_status_lbl.configure(

                text="❌ Password non valida. Riprova o inserisci le chiavi manualmente.",

                text_color="#dc2626"

            )

            return False

        self._keys_unlocked_by_pwd = True

        self.k1_var.set(keys.get("gemini_api_key", ""))

        self.k2_var.set(keys.get("gemini_api_key_2", ""))

        self.or_var.set(keys.get("openrouter_api_key", ""))

        self.pwd_var.set("")

        self._refresh_map()

        self.unlock_status_lbl.configure(

            text="✓ Chiavi API di Giovanni sbloccate e caricate!",

            text_color="#16a34a"

        )

        self.status_lbl.configure(

            text="✓ Tutte le 3 chiavi API sono pronte! Clicca 'Salva e Attiva Cascata' (o premi Invio) per iniziare.",

            text_color="#16a34a"

        )

        try:

            self.save_btn.bind("<Return>", lambda e: self._save_and_activate())

            self.save_btn.bind("<KP_Enter>", lambda e: self._save_and_activate())

            self.save_btn.focus_set()

        except Exception:

            pass

        return True



    def _choose_colletta_folder(self):

        cur = self.folder_var.get().strip()

        initial = cur if cur and os.path.isdir(cur) else str(ROOT_DIR)

        chosen = filedialog.askdirectory(

            title="Scegli la cartella con i file Excel della Colletta",

            initialdir=initial,

            parent=self,

        )

        if chosen:

            self.folder_var.set(os.path.normpath(chosen))



    def _build_key_input_row(self, parent, title: str, subtitle: str, var: tk.StringVar, toggle_cmd, paste_cmd):

        frame = ctk.CTkFrame(parent, fg_color="transparent")

        frame.pack(fill=tk.X, padx=14, pady=(4, 6))



        hdr = ctk.CTkFrame(frame, fg_color="transparent")

        hdr.pack(fill=tk.X)

        ctk.CTkLabel(hdr, text=title, font=("Segoe UI", 10, "bold"), text_color=TEXT).pack(side=tk.LEFT)

        ctk.CTkLabel(hdr, text=f"— {subtitle}", font=("Segoe UI", 9), text_color=MUTED).pack(side=tk.LEFT, padx=6)



        row = ctk.CTkFrame(frame, fg_color="transparent")

        row.pack(fill=tk.X, pady=(2, 0))



        entry = ctk.CTkEntry(row, textvariable=var, show="•", height=34, corner_radius=6, placeholder_text="Incolla chiave qui...")

        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)



        eye_btn = ctk.CTkButton(row, text="👁️", width=36, height=34, corner_radius=6, fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT, command=toggle_cmd)

        eye_btn.pack(side=tk.LEFT, padx=(4, 0))



        paste_btn = ctk.CTkButton(row, text="📋 Incolla", width=68, height=34, corner_radius=6, fg_color="#e0f2fe", hover_color="#bae6fd", text_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"), command=paste_cmd)

        paste_btn.pack(side=tk.LEFT, padx=(4, 0))



        return entry



    def _paste_to_var(self, var: tk.StringVar):

        try:

            txt = self.clipboard_get().strip()

            if txt:

                var.set(txt)

        except Exception:

            pass



    def _toggle_k1(self):

        self.k1_show = not self.k1_show

        self.k1_entry.configure(show="" if self.k1_show else "•")



    def _toggle_k2(self):

        self.k2_show = not self.k2_show

        self.k2_entry.configure(show="" if self.k2_show else "•")



    def _toggle_or(self):

        self.or_show = not self.or_show

        self.or_entry.configure(show="" if self.or_show else "•")



    def _refresh_map(self):

        k1 = self.k1_var.get().strip()

        k2 = self.k2_var.get().strip()

        or_key = self.or_var.get().strip()

        self.cascade_map.update_status(k1, k2, or_key)



    def _save_and_activate(self):

        k1 = self.k1_var.get().strip()

        k2 = self.k2_var.get().strip()

        or_key = self.or_var.get().strip()



        if not k1 and not k2 and not or_key:

            self.status_lbl.configure(

                text="⚠️ Inserisci almeno una chiave (Google Gemini o OpenRouter) per attivare l'elaborazione.",

                text_color="#dc2626"

            )

            return



        folder = self.folder_var.get().strip() if hasattr(self, "folder_var") else ""

        try:

            try:

                self._on_saved(k1, k2, or_key, folder)

            except TypeError:

                self._on_saved(k1, k2, or_key)

        finally:

            self.destroy()





WelcomeSetupDialog = WelcomeSetupDialogV3





class SettingsDialog(ctk.CTkToplevel):

    """Pannello Impostazioni: Chiavi API, Flusso IA & Modelli, Scanner e Laboratorio Taratura, Shortcut da Tastiera."""



    SOURCE_LABELS = {"flatbed": "Piano Vetro", "adf": "Alimentatore ADF"}

    PROVIDER_DISPLAY_MAP = {

        "cascade": "🚀 Cascata (Consigliata) (v1: 3.5 -> Luna -> Haiku)",

        "cascade_v2": "⚡ Cascata v2 (Gemini 3.8 Primario: 3.8 -> 3.5 -> Luna -> Haiku)",

        "gemini_35": "🆓 Gemini 3.5 Flash-Lite (Google API Diretta — Gratis)",

        "gemini_38": "✨ Gemini 3.8 Flash (Google API Diretta — Gratis)",

        "or_luna_low": "🌙 GPT-6 Luna (Low Reasoning) (OpenRouter)",

        "or_haiku_low": "🦅 Claude Haiku 5.5 (Low Reasoning) (OpenRouter)",

        "or_sonnet": "🧠 Claude Sonnet 5.5 (OpenRouter)",

        "or_nemotron": "🆓 Nemotron 3.5 Super-VL (OpenRouter Free — Gratis)",

    }

    PROVIDER_INV_MAP = {v: k for k, v in PROVIDER_DISPLAY_MAP.items()}



    def __init__(

        self,

        parent,

        config: Dict[str, Any],

        on_save: Callable[[Dict[str, Any]], None],

        initial_tab: Optional[str] = None,

    ):

        super().__init__(parent)

        self.title("⚙️ Impostazioni — Banco Alimentare")

        self.resizable(True, True)
        self.minsize(840, 640)

        self.configure(fg_color="#f1f5f9")

        self._cfg = config

        self._on_save = on_save

        self._initial_tab = initial_tab

        self._key_rows: List[_KeyRow] = []

        self._shortcut_vars: Dict[str, tk.StringVar] = {}

        curr_shortcuts = self._cfg.get("shortcuts") or {}

        for k, def_val in DEFAULT_SHORTCUTS.items():

            self._shortcut_vars[k] = tk.StringVar(value=str(curr_shortcuts.get(k, def_val)))



        # Variabili di taratura immagine laboratorio

        self.brightness_var = tk.DoubleVar(value=float(self._cfg.get("image_brightness", 1.0)))

        self.contrast_var = tk.DoubleVar(value=float(self._cfg.get("image_contrast", 1.0)))

        self.sharpness_var = tk.DoubleVar(value=float(self._cfg.get("image_sharpness", 1.0)))

        self._test_pil_orig: Optional[Image.Image] = None

        self._test_thumb_orig: Optional[Image.Image] = None

        self._ctk_thumb_orig = None

        self._ctk_thumb_tuned = None

        self._last_test_image_path: str = ""

        self._resize_job: Optional[str] = None

        self._is_maximized: bool = False

        self._scheduled_jobs: List[str] = []

        self._tuning_preview_job: Optional[str] = None



        _center_modal(self, parent, 860, 760)

        _make_modal(self, parent)

        self.bind("<F11>", lambda e: self._toggle_fullscreen_or_maximize())
        self.bind("<Configure>", self._on_window_configure)

        self._build()



    def _models(self) -> List[str]:

        models = list(SUPPORTED_GEMINI_MODELS)

        cur = self._cfg.get("gemini_model")

        if cur and cur not in models:

            models.insert(0, cur)

        return models



    def _or_models(self) -> List[str]:

        models = list(SUPPORTED_OPENROUTER_MODELS)

        cur = self._cfg.get("openrouter_model")

        if cur and cur not in models:

            models.insert(0, cur)

        return models



    def _build(self):

        # 1. BARRA PULSANTI INFERIORE (ancorata in basso PRIMA dei tab per non venire mai schiacciata!)

        bar = ctk.CTkFrame(self, fg_color="transparent", height=48)

        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(6, 14))

        ctk.CTkButton(

            bar, text="↺ Ripristina Default", width=148, height=38, corner_radius=10,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT, font=("Segoe UI", 11, "bold"),

            command=self._reset_defaults

        ).pack(side=tk.LEFT)

        ctk.CTkButton(

            bar, text="📜 Log Sessione", width=128, height=38, corner_radius=10,

            fg_color="#ffffff", hover_color="#e2e8f0", text_color=TEXT, border_width=1, border_color="#cbd5e1",

            font=("Segoe UI", 11, "bold"), command=self._open_session_log

        ).pack(side=tk.LEFT, padx=8)

        self._btn_toggle_fs = ctk.CTkButton(

            bar, text="⛶ Schermo Intero (F11)", width=175, height=38, corner_radius=10,

            fg_color="#ffffff", hover_color="#e2e8f0", text_color=TEXT, border_width=1, border_color="#cbd5e1",

            font=("Segoe UI", 11, "bold"), command=self._toggle_fullscreen_or_maximize

        )
        self._btn_toggle_fs.pack(side=tk.LEFT, padx=4)

        ctk.CTkButton(

            bar, text="💾 Salva Modifiche", width=160, height=38, corner_radius=10,

            fg_color="#16a34a", hover_color="#15803d", font=("Segoe UI", 12, "bold"),

            command=self._save

        ).pack(side=tk.RIGHT)

        ctk.CTkButton(

            bar, text="Annulla", width=95, height=38, corner_radius=10,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT, font=("Segoe UI", 11, "bold"),

            command=self.destroy

        ).pack(side=tk.RIGHT, padx=8)



        # 2. TABVIEW PRINCIPALE (occupa lo spazio rimanente sopra la barra pulsanti)

        tabs = ctk.CTkTabview(self, corner_radius=12, fg_color="#ffffff", border_width=1, border_color=BORDER)

        tabs.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=14, pady=(10, 4))

        self.tabs = tabs

        t_keys = tabs.add("🔑 Chiavi API")

        t_model = tabs.add("🚀 Flusso IA & Modelli")

        t_scan = tabs.add("🖨️ Scanner & Taratura")

        t_short = tabs.add("⌨️ Scorciatoie da Tastiera")



        # =====================================================================

        # TAB 1: CHIAVI API

        # =====================================================================

        self._build_keys_tab(t_keys)



        # =====================================================================

        # TAB 2: FLUSSO IA & MODELLI

        # =====================================================================

        self._build_model_tab(t_model)



        # =====================================================================

        # TAB 3: SCANNER & TARATURA

        # =====================================================================

        self._build_scanner_tab(t_scan)



        # =====================================================================

        # TAB 4: SCORCIATOIE DA TASTIERA

        # =====================================================================

        self._build_shortcuts_tab(t_short)



        if self._initial_tab:

            try:

                init_tab = self._initial_tab

                if "scanner" in init_tab.lower() and init_tab not in tabs._tab_dict:

                    init_tab = "🖨️ Scanner & Taratura"

                tabs.set(init_tab)

            except Exception:

                pass



    def _build_keys_tab(self, t_keys):

        top_info = ctk.CTkFrame(t_keys, fg_color="transparent")

        top_info.pack(fill=tk.X, padx=8, pady=(6, 4))

        ctk.CTkLabel(

            top_info,

            text="🔑 Le tue Chiavi API",

            font=("Segoe UI", 14, "bold"),

            text_color=TEXT,

        ).pack(anchor="w")

        ctk.CTkLabel(

            top_info,

            text="Inserisci qui le chiavi Google Gemini (1 o 2 account gratuiti) e la chiave OpenRouter di backup.\n"

                 "Il test verifica istantaneamente la connessione (✓ Attiva / ✗ Errore) senza bloccare l'app.",

            font=("Segoe UI", 10),

            text_color=MUTED,

            justify="left",

            wraplength=760,

        ).pack(anchor="w", pady=(2, 4))



        self.keys_summary_banner = ctk.CTkFrame(

            t_keys, corner_radius=8, fg_color="#f0fdf4", border_width=1, border_color="#86efac"

        )

        self.keys_summary_banner.pack(fill=tk.X, padx=8, pady=(0, 6))

        self.keys_summary_lbl = ctk.CTkLabel(

            self.keys_summary_banner,

            text="Verifica chiavi...",

            font=("Segoe UI", 11, "bold"),

            text_color="#166534",

            anchor="w",

        )

        self.keys_summary_lbl.pack(fill=tk.X, padx=12, pady=6)



        # Box Sblocco Rapido Chiavi con Password

        pwd_unlock_box = ctk.CTkFrame(

            t_keys, corner_radius=8, fg_color="#f0f9ff", border_width=1, border_color="#bae6fd"

        )

        pwd_unlock_box.pack(fill=tk.X, padx=8, pady=(0, 6))



        pwd_row = ctk.CTkFrame(pwd_unlock_box, fg_color="transparent")

        pwd_row.pack(fill=tk.X, padx=10, pady=(6, 4))



        ctk.CTkLabel(

            pwd_row,

            text="🔐 Sblocco Rapido Giovanni:",

            font=("Segoe UI", 10, "bold"),

            text_color="#0369a1",

        ).pack(side=tk.LEFT, padx=(0, 8))



        self.pwd_var = tk.StringVar()

        self.pwd_entry = ctk.CTkEntry(

            pwd_row,

            textvariable=self.pwd_var,

            show="•",

            height=30,

            corner_radius=6,

            placeholder_text="Inserisci password per caricare le chiavi...",

        )

        self.pwd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.pwd_entry.bind("<Return>", lambda e: self._unlock_with_password())

        self.pwd_entry.bind("<KP_Enter>", lambda e: self._unlock_with_password())



        self.unlock_keys_btn = ctk.CTkButton(

            pwd_row,

            text="🔐 Carica Chiavi con Password",

            width=200,

            height=30,

            corner_radius=6,

            fg_color=PRIMARY,

            hover_color=PRIMARY_HOVER,

            font=("Segoe UI", 10, "bold"),

            command=self._unlock_with_password,

        )

        self.unlock_keys_btn.pack(side=tk.RIGHT)



        self.unlock_status_lbl = ctk.CTkLabel(

            pwd_unlock_box, text="", font=("Segoe UI", 9, "bold"), text_color=MUTED, anchor="w"

        )

        self.unlock_status_lbl.pack(fill=tk.X, padx=10, pady=(0, 4))



        # Barra inferiore tab Chiavi ancorata prima della scroll area

        bottom_keys = ctk.CTkFrame(t_keys, fg_color="transparent")

        bottom_keys.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(4, 6))



        self.add_key_btn = ctk.CTkButton(

            bottom_keys,

            text="➕  Aggiungi Nuova Chiave API (Google Gemini o OpenRouter)",

            height=38,

            corner_radius=10,

            fg_color=PRIMARY,

            hover_color=PRIMARY_HOVER,

            font=("Segoe UI", 12, "bold"),

            command=self._open_add_key_dialog,

        )

        self.add_key_btn.pack(fill=tk.X, pady=(0, 6))



        links_row = ctk.CTkFrame(bottom_keys, fg_color="transparent")

        links_row.pack(fill=tk.X)



        ctk.CTkButton(

            links_row, text="🌐 Ottieni chiave Google AI Studio (Gratuita)", height=28, corner_radius=8,

            fg_color="#e0f2fe", hover_color="#bae6fd", text_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

            command=lambda: webbrowser.open(AI_STUDIO_URL)

        ).pack(side=tk.LEFT, padx=(0, 8))



        ctk.CTkButton(

            links_row, text="🌐 Dashboard & Crediti OpenRouter", height=28, corner_radius=8,

            fg_color="#fef3c7", hover_color="#fde68a", text_color="#b45309", font=("Segoe UI", 10, "bold"),

            command=lambda: webbrowser.open(OPENROUTER_KEYS_URL)

        ).pack(side=tk.LEFT)

        self._server_check_btn = ctk.CTkButton(
            links_row, text="⚡ Verifica Stato Server IA (Ping)", height=28, corner_radius=8,
            fg_color="#0284c7", hover_color="#0369a1", text_color="#ffffff", font=("Segoe UI", 10, "bold"),
            command=self._check_servers_status
        )
        self._server_check_btn.pack(side=tk.RIGHT)



        self.keys_scroll = ctk.CTkScrollableFrame(

            t_keys, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color=BORDER, height=280

        )

        self.keys_scroll.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))



        self.empty_keys_lbl = ctk.CTkLabel(

            self.keys_scroll,

            text="🔑 Nessuna chiave API configurata.\n\n"

                 "Clicca '➕ Aggiungi Nuova Chiave API' qui in basso per scegliere il tipo di chiave\n"

                 "(Google Gemini oppure OpenRouter) e incollare la tua chiave.",

            font=("Segoe UI", 11),

            text_color=MUTED,

            justify="center",

        )



        self._load_initial_keys()



    def _build_model_tab(self, t_model):

        mode_header = ctk.CTkFrame(t_model, fg_color="transparent")

        mode_header.pack(fill=tk.X, padx=10, pady=(6, 4))

        ctk.CTkLabel(

            mode_header, text="Modello / Modalità IA Attiva:", font=("Segoe UI", 12, "bold"), text_color=TEXT

        ).pack(side=tk.LEFT, padx=(0, 10))



        cur_prov = self._cfg.get("active_provider", "cascade")

        if cur_prov not in self.PROVIDER_DISPLAY_MAP:

            cur_prov = "cascade"

        init_prov_disp = self.PROVIDER_DISPLAY_MAP[cur_prov]

        self.provider_select_var = tk.StringVar(value=init_prov_disp)

        self.provider_option_menu = ctk.CTkOptionMenu(

            mode_header,

            variable=self.provider_select_var,

            values=list(self.PROVIDER_DISPLAY_MAP.values()),

            width=500,

            height=34,

            corner_radius=8,

            fg_color=PRIMARY,

            button_color=PRIMARY_HOVER,

            font=("Segoe UI", 11, "bold"),

            command=lambda val: self._on_provider_change(),

        )

        self.provider_option_menu.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.provider_select_var.trace_add("write", lambda *_: self._on_provider_change())



        # Mappa visuale interattiva del Workflow a Piani

        self.cascade_map = CascadeVisualMap(t_model, config=self._cfg)

        self.cascade_map.pack(fill=tk.X, padx=8, pady=(4, 6))



        # Card Configurazione Arbitro Matematico & Modalità Protetta Sonnet

        arbitrator_card = ctk.CTkFrame(

            t_model, corner_radius=10, fg_color="#fffbeb", border_width=1, border_color="#fde68a"

        )

        arbitrator_card.pack(fill=tk.X, padx=8, pady=(4, 6))



        arb_header = ctk.CTkFrame(arbitrator_card, fg_color="transparent")

        arb_header.pack(fill=tk.X, padx=12, pady=(10, 4))

        ctk.CTkLabel(

            arb_header,

            text="⚖️ Modello per l'Arbitro Matematico (Controllo Somme):",

            font=("Segoe UI", 11, "bold"),

            text_color="#92400e",

        ).pack(side=tk.LEFT)



        cur_arb = self._cfg.get("cascade_arbitrator_model") or self._cfg.get("openrouter_arbitrator_model", DEFAULT_CASCADE_ARBITRATOR_MODEL)

        if cur_arb not in ARBITRATOR_DISPLAY_MAP:

            cur_arb = DEFAULT_CASCADE_ARBITRATOR_MODEL

        init_arb_disp = ARBITRATOR_DISPLAY_MAP[cur_arb]

        self.arbitrator_model_var = tk.StringVar(value=init_arb_disp)



        self.arbitrator_option_menu = ctk.CTkOptionMenu(

            arb_header,

            variable=self.arbitrator_model_var,

            values=list(ARBITRATOR_DISPLAY_MAP.values()),

            width=430,

            height=32,

            corner_radius=8,

            fg_color="#b45309",

            button_color="#92400e",

            font=("Segoe UI", 10, "bold"),

            command=lambda val: self._on_arbitrator_change(),

        )

        self.arbitrator_option_menu.pack(side=tk.RIGHT)



        self.enable_arbitrator_var = tk.BooleanVar(value=bool(self._cfg.get("enable_auto_arbitrator", True)))

        arb_sw_frame = ctk.CTkFrame(arbitrator_card, fg_color="transparent")

        arb_sw_frame.pack(fill=tk.X, padx=12, pady=(2, 4))

        ctk.CTkSwitch(

            arb_sw_frame,

            text="Attiva Arbitro Automatico quando le somme non tornano (Scatole vs Totale Dichiarato a fondo foglio)",

            variable=self.enable_arbitrator_var,

            font=("Segoe UI", 10, "bold"),

            text_color="#78350f",

            command=self._on_arbitrator_change,

        ).pack(anchor="w")



        ctk.CTkLabel(

            arbitrator_card,

            text="• Chi controlla: il modello scelto interviene solo ed esclusivamente se c'è un'incongruenza sulle somme (non consuma chiamate sulle schede perfette).\n"

                 "• Claude Sonnet 5.5 protetto: attivabile manualmente per l'intera scheda con Shift + Click su '⚡ Rianalizza IA' o Ctrl+Shift+R.",

            font=("Segoe UI", 10),

            text_color="#78350f",

            justify="left",

            wraplength=760,

        ).pack(anchor="w", padx=12, pady=(0, 6))



        # Variabili mantenute per compatibilità salvataggio config

        self.or_model_var = tk.StringVar(value=self._cfg.get("openrouter_model", DEFAULT_OPENROUTER_MODEL))

        self.model_var = tk.StringVar(value=self._cfg.get("gemini_model", DEFAULT_GEMINI_MODEL))

        self.fallback_var = tk.BooleanVar(value=bool(self._cfg.get("enable_auto_fallback", True)))

        self.cross_fallback_var = tk.BooleanVar(value=bool(self._cfg.get("fallback_to_openrouter", True)))



        adv_frame = ctk.CTkFrame(t_model, fg_color="transparent")

        adv_frame.pack(fill=tk.X, padx=10, pady=(2, 2))

        ctk.CTkSwitch(

            adv_frame,

            text="Rotazione automatica tra Chiave Google #1 e #2 (fino a 1.000 schede/giorno gratis)",

            variable=self.fallback_var,

            font=("Segoe UI", 10),

        ).pack(anchor="w", pady=2)

        ctk.CTkSwitch(

            adv_frame,

            text="Usa il paracadute OpenRouter (Luna / Haiku / Nemotron) se le chiavi Google sono temporaneamente sature",

            variable=self.cross_fallback_var,

            font=("Segoe UI", 10),

        ).pack(anchor="w", pady=2)



    def _on_arbitrator_change(self, *args):

        self._on_provider_change()



    def _on_provider_change(self, *args):

        disp = self.provider_select_var.get()

        prov_key = self.PROVIDER_INV_MAP.get(disp, "cascade")

        grouped = self._collect_keys_by_type()

        gem_keys = grouped.get("gemini", [])

        or_keys = grouped.get("openrouter", [])

        k1 = gem_keys[0] if len(gem_keys) > 0 else ""

        k2 = gem_keys[1] if len(gem_keys) > 1 else ""

        or_k = or_keys[0] if len(or_keys) > 0 else ""

        arb_disp = getattr(self, "arbitrator_model_var", None)

        arb_key = ARBITRATOR_INV_MAP.get(arb_disp.get(), DEFAULT_CASCADE_ARBITRATOR_MODEL) if arb_disp else None

        arb_enabled = self.enable_arbitrator_var.get() if hasattr(self, "enable_arbitrator_var") else None

        if hasattr(self, "cascade_map"):

            self.cascade_map.update_mode(

                prov_key, k1, k2, or_k,

                arbitrator_model=arb_key,

                enable_arbitrator=arb_enabled

            )



    def _build_scanner_tab(self, parent):

        scroll = ctk.CTkScrollableFrame(parent, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color=BORDER)

        scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)



        # Card 1: Scanner Hardware (WIA)

        hw_card = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        hw_card.pack(fill=tk.X, padx=6, pady=(4, 6))



        top_hw = ctk.CTkFrame(hw_card, fg_color="transparent")

        top_hw.pack(fill=tk.X, padx=12, pady=(10, 4))

        ctk.CTkLabel(top_hw, text="🖨️ Scanner Hardware (Driver Windows WIA)", font=("Segoe UI", 12, "bold"), text_color="#0f172a").pack(side=tk.LEFT)



        btn_detect = ctk.CTkButton(

            top_hw, text="🔍 Rileva Scanner", width=140, height=28, corner_radius=6,

            fg_color=PRIMARY, hover_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

            command=self._detect_scanner_hardware

        )

        btn_detect.pack(side=tk.RIGHT, padx=(6, 0))



        btn_sel = ctk.CTkButton(

            top_hw, text="⚙️ Scegli Altro", width=110, height=28, corner_radius=6,

            fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT, font=("Segoe UI", 10, "bold"),

            command=self._select_other_scanner

        )

        btn_sel.pack(side=tk.RIGHT)



        # Badge stato scanner

        self.scanner_badge_box = ctk.CTkFrame(hw_card, corner_radius=8, fg_color="#f8fafc", border_width=1, border_color="#cbd5e1")

        self.scanner_badge_box.pack(fill=tk.X, padx=12, pady=(2, 8))

        self.scanner_status_lbl = ctk.CTkLabel(

            self.scanner_badge_box, text="Rilevamento periferica in corso...",

            font=("Segoe UI", 10, "bold"), text_color=MUTED, anchor="w"

        )

        self.scanner_status_lbl.pack(fill=tk.X, padx=10, pady=6)



        # Opzioni di scansione

        opt_grid = ctk.CTkFrame(hw_card, fg_color="transparent")

        opt_grid.pack(fill=tk.X, padx=12, pady=(0, 8))



        ctk.CTkLabel(opt_grid, text="Sorgente predefinita:", font=("Segoe UI", 10, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)

        self.source_seg = ctk.CTkSegmentedButton(opt_grid, values=list(self.SOURCE_LABELS.values()), height=32)

        self.source_seg.grid(row=0, column=1, sticky="w", pady=4)

        self.source_seg.set(self.SOURCE_LABELS.get(self._cfg.get("scanner_default_source", "flatbed"), "Piano Vetro"))



        ctk.CTkLabel(opt_grid, text="Risoluzione (DPI):", font=("Segoe UI", 10, "bold"), text_color=TEXT).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)

        self.dpi_var = tk.StringVar(value=str(self._cfg.get("scanner_dpi", 200)))

        ctk.CTkOptionMenu(

            opt_grid, variable=self.dpi_var, values=[str(d) for d in SCANNER_DPI_CHOICES],

            width=140, height=30, corner_radius=6, fg_color=PRIMARY, button_color=PRIMARY_HOVER

        ).grid(row=1, column=1, sticky="w", pady=4)



        switches_f = ctk.CTkFrame(hw_card, fg_color="transparent")

        switches_f.pack(fill=tk.X, padx=12, pady=(0, 10))



        self.opt_var = tk.BooleanVar(value=bool(self._cfg.get("scanner_auto_optimize", True)))

        ctk.CTkSwitch(switches_f, text="Auto-ottimizzazione scansione (raddrizzamento e nitidezza matita)", variable=self.opt_var, font=("Segoe UI", 10)).pack(anchor="w", pady=2)



        self.auto_preset_var = tk.BooleanVar(value=bool(self._cfg.get("auto_preset_assignment", False)))

        ctk.CTkSwitch(switches_f, text="Assegna automaticamente preset migliore (P1–P5) all'acquisizione (Default P0)", variable=self.auto_preset_var, font=("Segoe UI", 10)).pack(anchor="w", pady=2)



        # Card 2: Laboratorio di Taratura Immagine Integrato

        lab_card = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        lab_card.pack(fill=tk.X, padx=6, pady=(4, 8))



        top_lab = ctk.CTkFrame(lab_card, fg_color="transparent")

        top_lab.pack(fill=tk.X, padx=12, pady=(10, 2))

        ctk.CTkLabel(top_lab, text="🧪 Laboratorio di Taratura Immagine (Anteprima Live)", font=("Segoe UI", 12, "bold"), text_color="#0f172a").pack(side=tk.LEFT)

        self._btn_lab_fs = ctk.CTkButton(
            top_lab, text="⛶ Ingrandisci Finestra", width=150, height=26, corner_radius=6,
            fg_color="#f1f5f9", hover_color="#e2e8f0", text_color="#334155", font=("Segoe UI", 9, "bold"),
            command=self._toggle_fullscreen_or_maximize
        )
        self._btn_lab_fs.pack(side=tk.RIGHT)



        ctk.CTkLabel(

            lab_card,

            text="Regola i parametri di scansione per matite sbiadite o fogli scuri. L'anteprima si aggiorna all'istante.",

            font=("Segoe UI", 9), text_color="#64748b"

        ).pack(anchor="w", padx=12, pady=(0, 6))



        # Barra azioni laboratorio

        act_row = ctk.CTkFrame(lab_card, fg_color="transparent")

        act_row.pack(fill=tk.X, padx=12, pady=(0, 8))



        self.btn_test_scan = ctk.CTkButton(

            act_row, text="🧪 Scansione di Test", width=155, height=30, corner_radius=6,

            fg_color=PRIMARY, hover_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

            command=self._test_scan

        )

        self.btn_test_scan.pack(side=tk.LEFT, padx=(0, 8))



        self.btn_load_test_img = ctk.CTkButton(

            act_row, text="📁 Carica Immagine di Test", width=180, height=30, corner_radius=6,

            fg_color="#e0f2fe", hover_color="#bae6fd", text_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

            command=self._load_test_image_from_file

        )

        self.btn_load_test_img.pack(side=tk.LEFT, padx=(0, 8))



        ctk.CTkButton(

            act_row, text="↺ Ripristina Valori", width=140, height=30, corner_radius=6,

            fg_color="#f1f5f9", hover_color="#e2e8f0", text_color=TEXT, font=("Segoe UI", 10, "bold"),

            command=self._reset_tuning_values

        ).pack(side=tk.RIGHT)



        # Slider di Regolazione

        sliders_box = ctk.CTkFrame(lab_card, corner_radius=8, fg_color="#f8fafc", border_width=1, border_color="#e2e8f0")

        sliders_box.pack(fill=tk.X, padx=12, pady=(0, 8))



        # Slider 1: Luminosità (-50% a +50%, factor 0.5 a 1.5, default 1.0)

        s1_row = ctk.CTkFrame(sliders_box, fg_color="transparent")

        s1_row.pack(fill=tk.X, padx=10, pady=(6, 2))

        ctk.CTkLabel(s1_row, text="Luminosità:", width=100, anchor="w", font=("Segoe UI", 10, "bold"), text_color=TEXT).pack(side=tk.LEFT)

        self.brightness_slider = ctk.CTkSlider(

            s1_row, from_=0.5, to=1.5, number_of_steps=100, variable=self.brightness_var,

            command=lambda v: self._on_tuning_slider_change()

        )

        self.brightness_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        self.brightness_val_lbl = ctk.CTkLabel(s1_row, text="0%", width=60, font=("Consolas", 10, "bold"), text_color=PRIMARY)

        self.brightness_val_lbl.pack(side=tk.RIGHT)



        # Slider 2: Contrasto (0.5x a 2.5x, default 1.0x)

        s2_row = ctk.CTkFrame(sliders_box, fg_color="transparent")

        s2_row.pack(fill=tk.X, padx=10, pady=2)

        ctk.CTkLabel(s2_row, text="Contrasto:", width=100, anchor="w", font=("Segoe UI", 10, "bold"), text_color=TEXT).pack(side=tk.LEFT)

        self.contrast_slider = ctk.CTkSlider(

            s2_row, from_=0.5, to=2.5, number_of_steps=100, variable=self.contrast_var,

            command=lambda v: self._on_tuning_slider_change()

        )

        self.contrast_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        self.contrast_val_lbl = ctk.CTkLabel(s2_row, text="1.00x", width=60, font=("Consolas", 10, "bold"), text_color=PRIMARY)

        self.contrast_val_lbl.pack(side=tk.RIGHT)



        # Slider 3: Nitidezza (0.5x a 3.0x, default 1.0x)

        s3_row = ctk.CTkFrame(sliders_box, fg_color="transparent")

        s3_row.pack(fill=tk.X, padx=10, pady=(2, 6))

        ctk.CTkLabel(s3_row, text="Nitidezza:", width=100, anchor="w", font=("Segoe UI", 10, "bold"), text_color=TEXT).pack(side=tk.LEFT)

        self.sharpness_slider = ctk.CTkSlider(

            s3_row, from_=0.5, to=3.0, number_of_steps=100, variable=self.sharpness_var,

            command=lambda v: self._on_tuning_slider_change()

        )

        self.sharpness_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        self.sharpness_val_lbl = ctk.CTkLabel(s3_row, text="1.00x", width=60, font=("Consolas", 10, "bold"), text_color=PRIMARY)

        self.sharpness_val_lbl.pack(side=tk.RIGHT)



        # Riquadro Anteprima Live (Due miniature affiancate: Originale vs Filtrata)

        prev_card = ctk.CTkFrame(lab_card, corner_radius=8, fg_color="#f1f5f9", border_width=1, border_color="#cbd5e1")

        prev_card.pack(fill=tk.X, padx=12, pady=(0, 10))



        prev_cols = ctk.CTkFrame(prev_card, fg_color="transparent")

        prev_cols.pack(fill=tk.X, padx=8, pady=8)

        prev_cols.columnconfigure(0, weight=1)

        prev_cols.columnconfigure(1, weight=1)

        self.prev_cols = prev_cols
        self.prev_cols.bind("<Configure>", lambda e: self._on_prev_container_resize(e))



        # Colonna Sinistra: Originale

        col_orig = ctk.CTkFrame(prev_cols, corner_radius=6, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        col_orig.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        hdr_orig = ctk.CTkFrame(col_orig, fg_color="transparent")
        hdr_orig.pack(fill=tk.X, padx=8, pady=(6, 2))
        ctk.CTkLabel(hdr_orig, text="📷 Scansione Originale", font=("Segoe UI", 10, "bold"), text_color="#475569").pack(side=tk.LEFT)
        self.btn_zoom_orig = ctk.CTkButton(
            hdr_orig, text="🔍 Ingrandisci", width=95, height=24, corner_radius=5,
            fg_color="#f1f5f9", hover_color="#e2e8f0", text_color="#334155", font=("Segoe UI", 9, "bold"),
            command=self._open_orig_fullscreen
        )
        self.btn_zoom_orig.pack(side=tk.RIGHT)

        self.preview_orig_lbl = ctk.CTkLabel(

            col_orig, text="Nessuna immagine\ndi test caricata", font=("Segoe UI", 9),

            text_color=MUTED, height=200, cursor="hand2"

        )

        self.preview_orig_lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.preview_orig_lbl.bind("<Button-1>", lambda e: self._open_orig_fullscreen())



        # Colonna Destra: Filtrata

        col_tuned = ctk.CTkFrame(prev_cols, corner_radius=6, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        col_tuned.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        hdr_tuned = ctk.CTkFrame(col_tuned, fg_color="transparent")
        hdr_tuned.pack(fill=tk.X, padx=8, pady=(6, 2))
        ctk.CTkLabel(hdr_tuned, text="✨ Elaborata con Slider", font=("Segoe UI", 10, "bold"), text_color=PRIMARY_HOVER).pack(side=tk.LEFT)
        self.btn_zoom_tuned = ctk.CTkButton(
            hdr_tuned, text="🔍 Ingrandisci", width=95, height=24, corner_radius=5,
            fg_color="#e0f2fe", hover_color="#bae6fd", text_color=PRIMARY_HOVER, font=("Segoe UI", 9, "bold"),
            command=self._open_tuned_fullscreen
        )
        self.btn_zoom_tuned.pack(side=tk.RIGHT)

        self.preview_tuned_lbl = ctk.CTkLabel(

            col_tuned, text="Nessuna immagine\ndi test caricata", font=("Segoe UI", 9),

            text_color=MUTED, height=200, cursor="hand2"

        )

        self.preview_tuned_lbl.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.preview_tuned_lbl.bind("<Button-1>", lambda e: self._open_tuned_fullscreen())

        ctk.CTkLabel(
            prev_card,
            text="💡 Clicca su un'immagine o premi Ingrandisci per ispezionarla a tutto schermo (con zoom rotella, pan e rotazione)",
            font=("Segoe UI", 9, "italic"), text_color="#64748b"
        ).pack(pady=(0, 6))



        # Aggiorna subito le etichette con i valori iniziali

        self._update_tuning_preview(immediate=True)



        # Inizializzazione automatica sicura con tracciamento job

        self._schedule_after(50, self._detect_scanner_hardware)

        self._schedule_after(100, self._auto_load_sample_test_image)



    def _schedule_after(self, delay_ms: int, callback):

        def _wrapper():

            if not self.winfo_exists():

                return

            callback()

        try:

            job_id = self.after(delay_ms, _wrapper)

            self._scheduled_jobs.append(job_id)

            return job_id

        except Exception:

            return None



    def destroy(self):

        if getattr(self, "_tuning_preview_job", None):

            try:

                self.after_cancel(self._tuning_preview_job)

            except Exception:

                pass

            self._tuning_preview_job = None

        for job_id in list(getattr(self, "_scheduled_jobs", [])):

            try:

                self.after_cancel(job_id)

            except Exception:

                pass

        self._scheduled_jobs.clear()

        super().destroy()



    def _detect_scanner_hardware(self):

        if not self.winfo_exists():

            return

        self.scanner_status_lbl.configure(text="🔍 Rilevamento scanner in corso...", text_color=MUTED)

        self.scanner_badge_box.configure(fg_color="#f8fafc", border_color="#cbd5e1")



        def run_detect():

            return ScannerService.get_scanner_status()



        def on_done(status, error):

            if not self.winfo_exists():

                return

            if error is not None:

                self.scanner_status_lbl.configure(text=f"🔴 Errore WIA: {error}", text_color="#991b1b")

                self.scanner_badge_box.configure(fg_color="#fee2e2", border_color="#fca5a5")

                return

            self.scanner_status_lbl.configure(text=status.get("text", ""))

            if status.get("status") == "ready":

                self.scanner_status_lbl.configure(text_color="#166534")

                self.scanner_badge_box.configure(fg_color="#f0fdf4", border_color="#86efac")

            elif status.get("status") == "busy":

                self.scanner_status_lbl.configure(text_color="#92400e")

                self.scanner_badge_box.configure(fg_color="#fef3c7", border_color="#fde68a")

            else:

                self.scanner_status_lbl.configure(text_color="#991b1b")

                self.scanner_badge_box.configure(fg_color="#fee2e2", border_color="#fca5a5")



        BackgroundTask(self, lambda p: run_detect(), on_done).start()



    def _select_other_scanner(self):

        try:

            dev_id = ScannerService.select_device_dialog()

            if dev_id:

                self._detect_scanner_hardware()

        except Exception as e:

            messagebox.showwarning("Selezione Scanner", str(e), parent=self)



    def _test_scan(self):

        source_inv = {v: k for k, v in self.SOURCE_LABELS.items()}

        src = source_inv.get(self.source_seg.get(), "flatbed")

        try:

            dpi = int(self.dpi_var.get())

        except Exception:

            dpi = 200



        dev_id = ScannerService._device_id

        if not dev_id:

            try:

                scanners = ScannerService.detect_scanners()

                if scanners:

                    dev_id = scanners[0]["id"]

                    ScannerService._device_id = dev_id

            except Exception:

                pass



        if not dev_id:

            messagebox.showwarning(

                "Nessuno Scanner Rilevato",

                "Nessun scanner WIA rilevato nel sistema.\n"

                "Verificare che lo scanner sia acceso e collegato al computer.",

                parent=self,

            )

            return



        self.btn_test_scan.configure(state="disabled", text="Scansione...")



        def run_scan():

            return ScannerService.acquire_pages(

                device_id=dev_id,

                source=src,

                max_pages=1,

                dpi=dpi,

                optimize=False,

                skip_blank_pages=False,

                prefix="test_calibration",

                archive_raw=False,

            )



        def on_done(result, error):

            try:

                if self.winfo_exists():

                    self.btn_test_scan.configure(state="normal", text="🧪 Scansione di Test")

            except Exception:

                pass

            if error is not None:

                messagebox.showerror("Errore Scansione", f"Impossibile completare la scansione di test:\n{error}", parent=self)

                return

            if result and len(result) > 0:
                raw_file = Path(result[0]).with_suffix(".raw.jpg")
                to_load = raw_file if raw_file.exists() else result[0]
                self._load_tuning_test_image(to_load)



        BackgroundTask(self, lambda p: run_scan(), on_done).start()



    def _load_test_image_from_file(self):

        f = filedialog.askopenfilename(

            title="Seleziona Immagine di Test per la Taratura",

            filetypes=[

                ("File Immagine", "*.jpg;*.jpeg;*.png;*.bmp;*.webp;*.tif;*.tiff"),

                ("Tutti i File", "*.*")

            ],

            parent=self

        )

        if f:

            self._load_tuning_test_image(f)



    def _auto_load_sample_test_image(self):

        if not self.winfo_exists():

            return

        try:

            candidates = [

                TEST_IMAGES_DIR / "00_Originale_Nessuna_Modifica.jpg",

                TEST_IMAGES_DIR / "00_Originale_Nuova_Scansione.jpg",

            ]

            for p in candidates:

                if p.exists():

                    self._load_tuning_test_image(p)

                    return

            jpgs = list(TEST_IMAGES_DIR.glob("*.jpg"))

            if jpgs:

                self._load_tuning_test_image(jpgs[0])

        except Exception:

            pass



    def _toggle_fullscreen_or_maximize(self):
        """Alterna la finestra delle Impostazioni tra dimensione normale e massimizzata/schermo intero."""
        try:
            is_zoomed = False
            try:
                is_zoomed = (self.state() == "zoomed")
            except Exception:
                pass

            is_fs = False
            try:
                is_fs = bool(self.attributes("-fullscreen"))
            except Exception:
                pass

            if is_zoomed or is_fs:
                try:
                    self.attributes("-fullscreen", False)
                except Exception:
                    pass
                self.state("normal")
                self._is_maximized = False
            else:
                try:
                    self.state("zoomed")
                    self._is_maximized = True
                except Exception:
                    try:
                        self.attributes("-fullscreen", True)
                        self._is_maximized = True
                    except Exception:
                        pass
            self._update_fullscreen_btn_text(self._is_maximized)
            self._on_prev_container_resize()
        except Exception as e:
            print(f"[SETTINGS] Errore toggle fullscreen: {e}")

    def _on_window_configure(self, event):
        """Gestisce il ridimensionamento della finestra e sincronizza i pulsanti di massimizzazione."""
        if event.widget == self:
            is_z = False
            try:
                is_z = (self.state() == "zoomed") or bool(self.attributes("-fullscreen"))
            except Exception:
                pass
            if is_z != getattr(self, "_is_maximized", False):
                self._is_maximized = is_z
                self._update_fullscreen_btn_text(is_z)
                self._on_prev_container_resize()

    def _update_fullscreen_btn_text(self, is_fullscreen: bool):
        fs_bar_text = "🗗 Riduci Finestra (F11)" if is_fullscreen else "⛶ Schermo Intero (F11)"
        fs_lab_text = "🗗 Riduci Finestra" if is_fullscreen else "⛶ Ingrandisci Finestra"
        if hasattr(self, "_btn_toggle_fs") and self._btn_toggle_fs.winfo_exists():
            self._btn_toggle_fs.configure(text=fs_bar_text)
        if hasattr(self, "_btn_lab_fs") and self._btn_lab_fs.winfo_exists():
            self._btn_lab_fs.configure(text=fs_lab_text)

    def _get_preview_target_size(self) -> tuple:
        """Calcola la dimensione ottimale dei riquadri di anteprima in base alla larghezza disponibile."""
        default_w, default_h = 440, 300
        if not hasattr(self, "prev_cols") or not self.prev_cols.winfo_exists():
            return default_w, default_h

        container_w = self.prev_cols.winfo_width()
        if container_w <= 100:
            return default_w, default_h

        col_w = (container_w - 28) // 2
        col_w = max(380, min(850, col_w))

        if getattr(self, "_test_pil_orig", None):
            ow, oh = self._test_pil_orig.size
            ratio = oh / max(1, ow)
            target_h = int(col_w * ratio)
            target_h = max(240, min(520, target_h))
            target_w = int(target_h / ratio) if ratio > 0 else col_w
            return target_w, target_h
        return col_w, int(col_w * 0.68)

    def _on_prev_container_resize(self, event=None):
        if not getattr(self, "_test_pil_orig", None):
            return
        if getattr(self, "_resize_job", None):
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.after(100, lambda: self._update_tuning_preview(immediate=True))

    def _open_orig_fullscreen(self):
        """Apre l'immagine di test originale a schermo intero con zoom con rotella, pan e rotazione."""
        if not getattr(self, "_test_pil_orig", None):
            return
        from .components import FullScreenViewerDialogV3
        img_name = getattr(self, "_last_test_image_path", "Scansione_Originale.jpg")
        viewer = FullScreenViewerDialogV3(self, image_path=str(img_name), initial_image=self._test_pil_orig)
        self.wait_window(viewer)
        self._safe_restore_modal()

    def _open_tuned_fullscreen(self):
        """Apre l'immagine elaborata con i parametri slider a schermo intero ad alta risoluzione."""
        if not getattr(self, "_test_pil_orig", None):
            return
        from .components import FullScreenViewerDialogV3
        b = float(self.brightness_var.get())
        c = float(self.contrast_var.get())
        s = float(self.sharpness_var.get())
        tuned_highres = ScannerService.apply_image_tuning(self._test_pil_orig, brightness=b, contrast=c, sharpness=s)
        img_name = "Elaborata_con_Slider.jpg"
        viewer = FullScreenViewerDialogV3(self, image_path=img_name, initial_image=tuned_highres)
        self.wait_window(viewer)
        self._safe_restore_modal()

    def _safe_restore_modal(self):
        try:
            if self.winfo_exists():
                self.grab_set()
                self.focus_force()
        except Exception:
            pass

    def _load_tuning_test_image(self, path_or_pil):

        try:
            from pathlib import Path
            from PIL import ImageOps

            if isinstance(path_or_pil, (str, Path)):

                with Image.open(path_or_pil) as img:

                    img = ImageOps.exif_transpose(img)

                    rgb = img.convert("RGB")
                self._last_test_image_path = str(path_or_pil)

            else:

                img = ImageOps.exif_transpose(path_or_pil)

                rgb = img.convert("RGB")
                self._last_test_image_path = "Immagine_Test_Scansionata.jpg"

            self._test_pil_orig = rgb

            target_w, target_h = self._get_preview_target_size()
            thumb = rgb.copy()
            thumb.thumbnail((target_w, target_h), Image.Resampling.BILINEAR)

            self._test_thumb_orig = thumb

            self._update_tuning_preview(immediate=True)

        except Exception as e:
            try:
                if str(path_or_pil) not in str(getattr(self, "_last_auto_preloaded", "")):
                    messagebox.showerror("Errore Immagine", f"Impossibile caricare l'immagine di test:\n{e}", parent=self)
            except Exception:
                pass



    def _on_tuning_slider_change(self):

        self._update_tuning_preview(immediate=False)



    def _update_tuning_preview(self, immediate: bool = False):

        b = float(self.brightness_var.get())

        c = float(self.contrast_var.get())

        s = float(self.sharpness_var.get())



        b_pct = round((b - 1.0) * 100)

        try:

            self.brightness_val_lbl.configure(text=f"{b_pct:+d}%" if b_pct != 0 else "0%")

            self.contrast_val_lbl.configure(text=f"{c:.2f}x")

            self.sharpness_val_lbl.configure(text=f"{s:.2f}x")

        except Exception:

            pass



        if getattr(self, "_test_pil_orig", None) is None:

            return



        if immediate:

            if getattr(self, "_tuning_preview_job", None):

                try:

                    self.after_cancel(self._tuning_preview_job)

                except Exception:

                    pass

                self._tuning_preview_job = None

            self._render_tuning_preview()

        else:

            if getattr(self, "_tuning_preview_job", None):

                try:

                    self.after_cancel(self._tuning_preview_job)

                except Exception:

                    pass

            self._tuning_preview_job = self.after(50, self._render_tuning_preview)



    def _render_tuning_preview(self):

        self._tuning_preview_job = None

        if not self.winfo_exists() or getattr(self, "_test_pil_orig", None) is None:

            return

        b = float(self.brightness_var.get())

        c = float(self.contrast_var.get())

        s = float(self.sharpness_var.get())

        target_w, target_h = self._get_preview_target_size()

        try:

            preview_base = self._test_pil_orig.copy()
            preview_base.thumbnail((target_w, target_h), Image.Resampling.BILINEAR)
            w, h = preview_base.size

            self._ctk_thumb_orig = ctk.CTkImage(light_image=preview_base, dark_image=preview_base, size=(w, h))

            self.preview_orig_lbl.configure(image=self._ctk_thumb_orig, text="")



            tuned = ScannerService.apply_image_tuning(preview_base, brightness=b, contrast=c, sharpness=s)

            self._ctk_thumb_tuned = ctk.CTkImage(light_image=tuned, dark_image=tuned, size=(w, h))

            self.preview_tuned_lbl.configure(image=self._ctk_thumb_tuned, text="")

            self._test_thumb_orig = preview_base

        except Exception as e:

            print(f"[TARATURA] Errore render preview: {e}")



    def _reset_tuning_values(self):

        self.brightness_var.set(1.0)

        self.contrast_var.set(1.0)

        self.sharpness_var.set(1.0)

        if hasattr(self, "brightness_slider"):

            self.brightness_slider.set(1.0)

            self.contrast_slider.set(1.0)

            self.sharpness_slider.set(1.0)

        self._update_tuning_preview(immediate=True)



    def _build_shortcuts_tab(self, parent):

        scroll = ctk.CTkScrollableFrame(

            parent, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color=BORDER

        )

        scroll.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)



        # Card configurabile

        config_card = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

        config_card.pack(fill=tk.X, padx=6, pady=(4, 8))



        top_row = ctk.CTkFrame(config_card, fg_color="transparent")

        top_row.pack(fill=tk.X, padx=12, pady=(10, 4))



        ctk.CTkLabel(

            top_row, text="⌨️ Scorciatoie da Tastiera Personalizzabili",

            font=("Segoe UI", 12, "bold"), text_color="#0f172a"

        ).pack(side=tk.LEFT)



        reset_btn = ctk.CTkButton(

            top_row, text="↺ Reimposta Predefinite", width=180, height=28, corner_radius=6,

            fg_color="#e0f2fe", hover_color="#bae6fd", text_color="#0369a1", font=("Segoe UI", 10, "bold"),

            command=self._reset_shortcuts

        )

        reset_btn.pack(side=tk.RIGHT)



        ctk.CTkLabel(

            config_card,

            text="Modifica i tasti associati a ciascuna azione. Le modifiche verranno salvate in config.json.",

            font=("Segoe UI", 9), text_color="#64748b", justify="left"

        ).pack(anchor="w", padx=12, pady=(0, 8))



        for sc_key in DEFAULT_SHORTCUTS.keys():

            label = SHORTCUT_LABELS.get(sc_key, sc_key)

            desc = SHORTCUT_DESCRIPTIONS.get(sc_key, "")

            var = self._shortcut_vars[sc_key]



            r_frame = ctk.CTkFrame(config_card, fg_color="transparent")

            r_frame.pack(fill=tk.X, padx=12, pady=3)



            info_f = ctk.CTkFrame(r_frame, fg_color="transparent")

            info_f.pack(side=tk.LEFT, fill=tk.X, expand=True)



            ctk.CTkLabel(info_f, text=label, font=("Segoe UI", 10, "bold"), text_color="#0f172a", anchor="w").pack(anchor="w")

            ctk.CTkLabel(info_f, text=desc, font=("Segoe UI", 9), text_color="#64748b", anchor="w").pack(anchor="w")



            entry = ctk.CTkEntry(

                r_frame, textvariable=var, width=150, height=28, corner_radius=6,

                font=("Consolas", 10, "bold"), justify="center"

            )

            entry.pack(side=tk.RIGHT, padx=(8, 0))



        ctk.CTkFrame(config_card, height=6, fg_color="transparent").pack()



        # Sezioni informative aggiuntive (mouse e navigazione fissa)

        info_sections = [

            (

                "🖱️ Interazione con il Mouse & Tabella",

                [

                    ("Clic su colonna Stato (✓/✗)", "Approva/conferma istantaneamente la riga senza usare la tastiera"),

                    ("Doppio Clic su cella peso", "Attiva la modifica rapida del peso (si disattiva dopo il salvataggio)"),

                    ("Pulsante '✏️ Modifica'", "Attiva la modalità modifica continua per più celle consecutive"),

                    ("Frecce ← / →", "Espandono (→) o comprimono (←) l'elenco delle scatole della categoria"),

                    ("Frecce ↑ / ↓ in modifica", "Salvano il valore ed escono dalla modifica tornando alle frecce"),

                ]

            ),

            (

                "⚡ Visore Immagine & Azioni Avanzate",

                [

                    ("Shift + Clic su Rianalizza", "Rianalizza la scheda con Claude Sonnet 5.5 (previa conferma)"),

                    ("Ctrl + Shift + R", "Scorciatoia per rianalisi ad altissima precisione con Sonnet 5.5"),

                    ("F11", "Apre il foglio a schermo intero per ispezionare i dettagli"),

                    ("Rotella Mouse", "Zoom avanti / indietro sulla scheda scansionata"),

                    ("Esc", "Esce dalla modalità modifica inline o chiude la finestra attiva"),

                ]

            )

        ]



        for sec_title, rows in info_sections:

            card = ctk.CTkFrame(scroll, corner_radius=10, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")

            card.pack(fill=tk.X, padx=6, pady=5)

            ctk.CTkLabel(

                card, text=sec_title, font=("Segoe UI", 12, "bold"), text_color="#0f172a"

            ).pack(anchor="w", padx=12, pady=(8, 4))



            for key_txt, desc_txt in rows:

                r_frame = ctk.CTkFrame(card, fg_color="transparent")

                r_frame.pack(fill=tk.X, padx=12, pady=3)

                badge = ctk.CTkLabel(

                    r_frame,

                    text=f"  {key_txt}  ",

                    font=("Consolas", 10, "bold"),

                    fg_color="#e0f2fe",

                    text_color="#0369a1",

                    corner_radius=6,

                    width=210,

                    height=24,

                )

                badge.pack(side=tk.LEFT, padx=(0, 10))

                ctk.CTkLabel(

                    r_frame,

                    text=desc_txt,

                    font=("Segoe UI", 10),

                    text_color="#334155",

                    anchor="w",

                    justify="left",

                ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            ctk.CTkFrame(card, height=4, fg_color="transparent").pack()



    def _reset_shortcuts(self):

        for k, v in DEFAULT_SHORTCUTS.items():

            if k in self._shortcut_vars:

                self._shortcut_vars[k].set(v)



    def _open_session_log(self):

        from .log_viewer_dialog import LogViewerDialogV3

        LogViewerDialogV3(self)



    def _unlock_with_password(self) -> bool:

        if (

            not self.pwd_var.get().strip()

            and getattr(self, "_keys_unlocked_by_pwd", False)

            and len(self._key_rows) > 0

        ):

            self._save()

            return True

        keys = unlock_giovanni_keys(self.pwd_var.get())

        if not keys:

            if hasattr(self, "unlock_status_lbl"):

                self.unlock_status_lbl.configure(

                    text="❌ Password non valida.",

                    text_color="#dc2626",

                )

            return False

        self._keys_unlocked_by_pwd = True

        for r in list(self._key_rows):

            try:

                r.destroy()

            except Exception:

                pass

        self._key_rows.clear()

        for ptype, field in (

            ("gemini", "gemini_api_key"),

            ("gemini", "gemini_api_key_2"),

            ("openrouter", "openrouter_api_key"),

        ):

            val = str(keys.get(field) or "").strip()

            if val:

                self._append_key_row(ptype, val)

        self._refresh_keys_ui()

        self.pwd_var.set("")

        if hasattr(self, "unlock_status_lbl"):

            self.unlock_status_lbl.configure(

                text="✓ Chiavi di Giovanni sbloccate! Clicca '💾 Salva Modifiche' (o premi Invio) per confermare.",

                text_color="#16a34a",

            )

        return True



    def _load_initial_keys(self):

        """Carica nella lista solo le chiavi già valorizzate (nessun box vuoto preimpostato)."""

        for ptype, cfg_field in (

            ("gemini", "gemini_api_key"),

            ("gemini", "gemini_api_key_2"),

            ("openrouter", "openrouter_api_key"),

            ("openrouter", "openrouter_api_key_2"),

        ):

            val = str(self._cfg.get(cfg_field) or "").strip()

            if val:

                self._append_key_row(ptype, val)

        self._refresh_keys_ui()



    def _append_key_row(self, provider_type: str, key_value: str):

        same_type_count = sum(1 for r in self._key_rows if r.provider_type == provider_type)

        role_label = "Chiave Primaria" if same_type_count == 0 else f"Chiave Backup #{same_type_count + 1}"

        tester = (

            test_openrouter_api_key

            if provider_type == "openrouter"

            else (lambda k: test_gemini_api_key(k, "gemini-3.5-flash-lite"))

        )

        row = _KeyRow(

            self.keys_scroll,

            title=role_label,

            value=key_value,

            tester=tester,

            provider_type=provider_type,

            on_delete=self._remove_key_row,

        )

        self._key_rows.append(row)



    def _remove_key_row(self, row: _KeyRow):

        if row in self._key_rows:

            self._key_rows.remove(row)

        try:

            row.destroy()

        except tk.TclError:

            pass

        self._refresh_keys_ui()



    def _refresh_keys_ui(self):

        for r in self._key_rows:

            r.pack_forget()

        if not self._key_rows:

            self.empty_keys_lbl.pack(fill=tk.BOTH, expand=True, padx=16, pady=48)

        else:

            self.empty_keys_lbl.pack_forget()

            for r in self._key_rows:

                r.pack(fill=tk.X, padx=4, pady=4)

        grouped = self._collect_keys_by_type()

        gem_keys = grouped.get("gemini", [])

        or_keys = grouped.get("openrouter", [])

        k1 = gem_keys[0] if len(gem_keys) > 0 else ""

        k2 = gem_keys[1] if len(gem_keys) > 1 else ""

        or_k = or_keys[0] if len(or_keys) > 0 else ""

        if hasattr(self, "keys_summary_lbl"):

            n_gem = len(gem_keys)

            n_or = len(or_keys)

            cap = n_gem * 500

            if n_gem > 0 or n_or > 0:

                self.keys_summary_banner.configure(fg_color="#f0fdf4", border_color="#86efac")

                self.keys_summary_lbl.configure(

                    text=f"✅ Chiavi attive: {n_gem} Google Gemini ({cap:,} schede/giorno gratis) · {n_or} OpenRouter",

                    text_color="#166534",

                )

            else:

                self.keys_summary_banner.configure(fg_color="#fef2f2", border_color="#fca5a5")

                self.keys_summary_lbl.configure(

                    text="⚠️ Nessuna chiave configurata — Aggiungi almeno 1 chiave Google Gemini gratuita",

                    text_color="#991b1b",

                )

        if hasattr(self, "cascade_map"):

            raw_prov = self.PROVIDER_INV_MAP.get(self.provider_select_var.get(), "cascade")

            self.cascade_map.update_mode(raw_prov, k1, k2, or_k)



    def _check_servers_status(self):
        """Esegue un ping di test su Google Gemini e OpenRouter per verificare se i server rispondono."""
        if hasattr(self, "_server_check_btn"):
            self._server_check_btn.configure(state="disabled", text="Ping in corso...")
        self.keys_summary_lbl.configure(text="🔍 Verifica connettività e latenza server Google e OpenRouter in corso...", text_color="#0369a1")
        self.keys_summary_banner.configure(fg_color="#f0f9ff", border_color="#bae6fd")

        try:
            grouped = self._collect_keys_by_type()
            gem_keys = list(grouped.get("gemini", []))
            or_keys = list(grouped.get("openrouter", []))
            or_key = or_keys[0] if or_keys else ""

            def work(progress):
                return check_all_servers_status(gem_keys, or_key, timeout=5.0)

            def done(results, error):
                try:
                    if hasattr(self, "_server_check_btn") and self._server_check_btn.winfo_exists():
                        self._server_check_btn.configure(state="normal", text="⚡ Verifica Stato Server IA (Ping)")
                except Exception:
                    pass
                if not self.winfo_exists():
                    return
                if error or not results:
                    err_msg = str(error) if error else "Nessuna risposta dai server"
                    self.keys_summary_lbl.configure(text=f"⚠️ Errore durante la verifica server: {err_msg}", text_color="#dc2626")
                    self.keys_summary_banner.configure(fg_color="#fef2f2", border_color="#fca5a5")
                    return

                gem = results.get("gemini", {})
                opn = results.get("openrouter", {})

                gem_txt = gem.get("status_text", "")
                or_txt = opn.get("status_text", "")

                banner_txt = f"{gem_txt}  •  OpenRouter: {or_txt}" if gem_txt.startswith("Google") else f"Google: {gem_txt}  •  OpenRouter: {or_txt}"

                all_gem_ok = all(k.get("ok", False) for k in gem.get("keys", [])) if gem.get("keys") else gem.get("ok", False)
                all_ok = all_gem_ok and (opn.get("ok") or not or_key)

                if all_ok:
                    self.keys_summary_lbl.configure(text=banner_txt, text_color="#166534")
                    self.keys_summary_banner.configure(fg_color="#f0fdf4", border_color="#86efac")
                elif gem.get("ok") or opn.get("ok"):
                    self.keys_summary_lbl.configure(text=banner_txt, text_color="#b45309")
                    self.keys_summary_banner.configure(fg_color="#fffbeb", border_color="#fcd34d")
                else:
                    self.keys_summary_lbl.configure(text=banner_txt, text_color="#b91c1c")
                    self.keys_summary_banner.configure(fg_color="#fef2f2", border_color="#fca5a5")

                # Aggiorna anche i badge individuali delle _key_rows se visibili
                if hasattr(self, "_key_rows") and self._key_rows:
                    gem_keys_results = gem.get("keys", [])
                    gem_i = 0
                    for r in self._key_rows:
                        if r.provider_type == "gemini" and r.get():
                            if gem_i < len(gem_keys_results):
                                info = gem_keys_results[gem_i]
                                if info.get("ok"):
                                    r._set_badge(f"✓ Operativo ({info.get('latency', 0.0):.2f}s)", "#16a34a")
                                else:
                                    r._set_badge(f"✗ {info.get('msg', 'Non risponde')}", "#dc2626")
                                gem_i += 1
                        elif r.provider_type == "openrouter" and r.get():
                            if opn.get("ok"):
                                r._set_badge(f"✓ Operativo ({opn.get('latency', 0.0):.2f}s)", "#16a34a")
                            else:
                                raw_err = opn.get("status_text", "Non risponde").replace("🔴", "").strip()
                                r._set_badge(f"✗ {raw_err}", "#dc2626")

            BackgroundTask(self, work, done).start()
        except Exception as e:
            if hasattr(self, "_server_check_btn"):
                self._server_check_btn.configure(state="normal", text="⚡ Verifica Stato Server IA (Ping)")
            self.keys_summary_lbl.configure(text=f"⚠️ Errore durante la verifica server: {e}", text_color="#dc2626")
            self.keys_summary_banner.configure(fg_color="#fef2f2", border_color="#fca5a5")

    def _open_add_key_dialog(self):

        def on_added(ptype: str, key_val: str):

            self._append_key_row(ptype, key_val)

            self._refresh_keys_ui()



        AddApiKeyDialog(self, on_added=on_added, gemini_model=self.model_var.get())



    def _collect_keys_by_type(self) -> Dict[str, List[str]]:

        gem_keys = [r.get() for r in self._key_rows if r.provider_type == "gemini" and r.get()]

        or_keys = [r.get() for r in self._key_rows if r.provider_type == "openrouter" and r.get()]

        return {"gemini": gem_keys, "openrouter": or_keys}



    def _reset_defaults(self):

        if not messagebox.askyesno(

            "Ripristina Default",

            "Ripristinare modello e opzioni ai valori predefiniti?\n(Le chiavi API NON verranno toccate.)",

            parent=self,

        ):

            return

        self.provider_select_var.set(self.PROVIDER_DISPLAY_MAP["cascade"])

        self.or_model_var.set(DEFAULT_OPENROUTER_MODEL)

        self.model_var.set(DEFAULT_GEMINI_MODEL)

        self.fallback_var.set(True)

        self.cross_fallback_var.set(True)

        self.source_seg.set(self.SOURCE_LABELS[DEFAULT_SCANNER_OPTIONS["scanner_default_source"]])

        self.dpi_var.set(str(DEFAULT_SCANNER_OPTIONS["scanner_dpi"]))

        self.opt_var.set(DEFAULT_SCANNER_OPTIONS["scanner_auto_optimize"])

        self.auto_preset_var.set(DEFAULT_SCANNER_OPTIONS.get("auto_preset_assignment", False))

        self._reset_tuning_values()

        if hasattr(self, "arbitrator_model_var"):

            self.arbitrator_model_var.set(ARBITRATOR_DISPLAY_MAP[DEFAULT_CASCADE_ARBITRATOR_MODEL])

        if hasattr(self, "enable_arbitrator_var"):

            self.enable_arbitrator_var.set(True)

        self._reset_shortcuts()

        self._on_provider_change()



    def _save(self):

        shortcuts = {}

        for k, var in self._shortcut_vars.items():

            val = var.get().strip()

            if not val:

                val = DEFAULT_SHORTCUTS.get(k, "")

            # Normalizzazione e verifica di validità con Tkinter

            norm_test = val

            norm_test = norm_test.replace("Ctrl-", "Control-").replace("Ctrl+", "Control-").replace("ctrl-", "Control-").replace("ctrl+", "Control-")

            norm_test = norm_test.replace("+", "-")

            if not norm_test.startswith("<"):

                norm_test = f"<{norm_test}>"

            try:

                self.bind(norm_test, lambda e: None)

                self.unbind(norm_test)

            except Exception:

                messagebox.showerror(

                    "Scorciatoia Non Valida",

                    f"La combinazione di tasti '{val}' per '{SHORTCUT_LABELS.get(k, k)}' non è valida o non è supportata da Tkinter.\n\n"

                    f"Esempi di combinazioni corrette:\n"

                    f"• F2, <Shift-F2>, F5\n"

                    f"• <Control-o>, <Control-Return>, <Alt-Return>\n"

                    f"• <Return>, <Shift-Return>, <Tab>, <space>",

                    parent=self

                )

                return

            shortcuts[k] = val



        grouped = self._collect_keys_by_type()

        gem_keys = grouped["gemini"]

        or_keys = grouped["openrouter"]

        if not gem_keys and not or_keys:

            if not messagebox.askyesno(

                "Chiavi API Mancanti",

                "Nessuna chiave API inserita (né OpenRouter né Gemini).\nSenza una chiave la lettura automatica non funzionerà.\nSalvare comunque?",

                parent=self,

            ):

                return



        inv = {v: k for k, v in self.SOURCE_LABELS.items()}

        try:

            dpi = int(self.dpi_var.get())

        except ValueError:

            dpi = DEFAULT_SCANNER_OPTIONS["scanner_dpi"]



        raw_prov = self.PROVIDER_INV_MAP.get(self.provider_select_var.get(), "cascade")



        values = {

            "active_provider": raw_prov,

            "openrouter_api_key": or_keys[0] if len(or_keys) > 0 else "",

            "openrouter_api_key_2": or_keys[1] if len(or_keys) > 1 else "",

            "openrouter_model": self.or_model_var.get().strip(),

            "gemini_api_key": gem_keys[0] if len(gem_keys) > 0 else "",

            "gemini_api_key_2": gem_keys[1] if len(gem_keys) > 1 else "",

            "gemini_model": self.model_var.get().strip(),

            "enable_auto_fallback": bool(self.fallback_var.get()),

            "fallback_to_openrouter": bool(self.cross_fallback_var.get()),

            "scanner_default_source": inv.get(self.source_seg.get(), "flatbed"),

            "scanner_dpi": dpi,

            "scanner_auto_optimize": bool(self.opt_var.get()),

            "auto_preset_assignment": bool(self.auto_preset_var.get()),

            "image_brightness": round(float(self.brightness_var.get()), 2),

            "image_contrast": round(float(self.contrast_var.get()), 2),

            "image_sharpness": round(float(self.sharpness_var.get()), 2),

            "cascade_arbitrator_model": ARBITRATOR_INV_MAP.get(self.arbitrator_model_var.get(), DEFAULT_CASCADE_ARBITRATOR_MODEL) if hasattr(self, "arbitrator_model_var") else DEFAULT_CASCADE_ARBITRATOR_MODEL,

            "openrouter_arbitrator_model": ARBITRATOR_INV_MAP.get(self.arbitrator_model_var.get(), DEFAULT_CASCADE_ARBITRATOR_MODEL) if hasattr(self, "arbitrator_model_var") else DEFAULT_CASCADE_ARBITRATOR_MODEL,

            "enable_auto_arbitrator": bool(self.enable_arbitrator_var.get()) if hasattr(self, "enable_arbitrator_var") else True,

            "shortcuts": shortcuts,

        }

        try:

            self._on_save(values)

        except Exception as e:

            messagebox.showerror("Errore Salvataggio", f"Impossibile salvare le impostazioni:\n{e}", parent=self)

            return

        self.destroy()





SettingsDialogV3 = SettingsDialog





# --- Carosello lotto ---------------------------------------------------------



STATUS_STYLES = {

    "pending": ("In attesa ⏳", "#475569", "#f1f5f9"),

    "processing": ("Elaborazione IA 🔄", "#b45309", "#fef3c7"),

    "ready": ("Pronto ✓", "#15803d", "#dcfce7"),

    "written": ("Scritto su Excel 📗", "#0369a1", "#e0f2fe"),

    "error": ("Errore ⚠️", "#b91c1c", "#fee2e2"),

}





class BatchStripV3(ctk.CTkFrame):

    """Strip di miniature numerate con badge di stato, navigazione Precedente/Successivo e menu apribile/chiudibile."""



    def __init__(self, parent, on_select: Callable[[int], None],

                 on_prev: Callable[[], None], on_next: Callable[[], None],

                 initial_expanded: bool = False):

        super().__init__(parent, corner_radius=12, fg_color="#ffffff", border_width=1, border_color=BORDER)

        self._on_select = on_select

        self._cells: List[Dict[str, Any]] = []

        self._items: List[Any] = []

        self._selected = -1

        self._expanded = initial_expanded



        head = ctk.CTkFrame(self, fg_color="transparent")

        head.pack(fill=tk.X, padx=10, pady=4)



        self.toggle_btn = ctk.CTkButton(

            head,

            text="▼" if self._expanded else "▶",

            width=26,

            height=24,

            corner_radius=6,

            fg_color="#e2e8f0",

            hover_color="#cbd5e1",

            text_color=TEXT,

            font=("Segoe UI", 10, "bold"),

            command=self.toggle_expanded,

        )

        self.toggle_btn.pack(side=tk.LEFT, padx=(0, 6))



        self.title_lbl = ctk.CTkLabel(

            head, text="📋 FOGLI IN CODA", font=("Segoe UI", 10, "bold"), text_color=TEXT, cursor="hand2"

        )

        self.title_lbl.pack(side=tk.LEFT)

        self.title_lbl.bind("<Button-1>", lambda e: self.toggle_expanded())



        self.current_badge = ctk.CTkLabel(

            head, text="", font=("Segoe UI", 9, "bold"), corner_radius=8, height=20

        )

        self.current_badge.pack(side=tk.LEFT, padx=(8, 0))

        self.current_badge.bind("<Button-1>", lambda e: self.toggle_expanded())



        self.next_btn = ctk.CTkButton(head, text="Successivo ▶", width=94, height=24, corner_radius=8,

                                      fg_color=PRIMARY, hover_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

                                      command=on_next)

        self.next_btn.pack(side=tk.RIGHT)

        self.prev_btn = ctk.CTkButton(head, text="◀ Precedente", width=94, height=24, corner_radius=8,

                                      fg_color=PRIMARY, hover_color=PRIMARY_HOVER, font=("Segoe UI", 10, "bold"),

                                      command=on_prev)

        self.prev_btn.pack(side=tk.RIGHT, padx=(0, 6))



        self.scroll = ctk.CTkScrollableFrame(self, orientation="horizontal", height=106,

                                             fg_color="transparent", corner_radius=0)

        if self._expanded:

            self.scroll.pack(fill=tk.X, padx=6, pady=(0, 4))



    def toggle_expanded(self):

        self.set_expanded(not self._expanded)



    def set_expanded(self, expanded: bool):

        self._expanded = bool(expanded)

        self.toggle_btn.configure(text="▼" if self._expanded else "▶")

        if self._expanded:

            self.scroll.pack(fill=tk.X, padx=6, pady=(0, 4))

        else:

            self.scroll.pack_forget()



    def set_items(self, items: List[Any], selected: int = 0):

        for c in self._cells:

            c["frame"].destroy()

        self._cells.clear()

        self._items = list(items)

        for i, item in enumerate(self._items):

            self._cells.append(self._make_cell(i, item))

        self._selected = -1

        if self._items:

            self.set_selected(max(0, min(selected, len(self._items) - 1)))

        else:

            self.title_lbl.configure(text="📋 FOGLI IN CODA (0)")

            self.current_badge.configure(text="", fg_color="transparent")



    def _make_cell(self, i: int, item: Any) -> Dict[str, Any]:

        frame = ctk.CTkFrame(self.scroll, corner_radius=10, fg_color="#f8fafc", border_width=2, border_color=BORDER)

        frame.pack(side=tk.LEFT, padx=4, pady=2)

        img_lbl = ctk.CTkLabel(frame, text="")

        try:

            with Image.open(item.image_path) as im:

                im.draft("RGB", (320, 240))

                im = im.convert("RGB")

                im.thumbnail((104, 64))

                ctk_img = ctk.CTkImage(light_image=im.copy(), size=im.size)

            img_lbl.configure(image=ctk_img)

            img_lbl._thumb_ref = ctk_img

        except Exception:

            img_lbl.configure(text="🖼️", font=("Segoe UI Emoji", 22))

        img_lbl.pack(padx=6, pady=(6, 0))

        num_lbl = ctk.CTkLabel(frame, text=f"#{i + 1}", font=("Segoe UI", 10, "bold"), text_color=TEXT)

        num_lbl.pack()

        badge = ctk.CTkLabel(frame, text="", font=("Segoe UI", 9, "bold"), corner_radius=8, height=18)

        badge.pack(padx=6, pady=(0, 6))

        for w in (frame, img_lbl, num_lbl, badge):

            w.bind("<Button-1>", lambda e, idx=i: self._on_select(idx))

            w.configure(cursor="hand2")

        cell = {"frame": frame, "badge": badge, "img_lbl": img_lbl}

        self._apply_badge(cell, item)

        return cell



    @staticmethod

    def _apply_badge(cell: Dict[str, Any], item: Any):

        text, fg, bg = STATUS_STYLES.get(getattr(item, "status", "pending"), STATUS_STYLES["pending"])

        cell["badge"].configure(text=f" {text} ", text_color=fg, fg_color=bg)



    def _update_header_summary(self):

        if 0 <= self._selected < len(self._items):

            total = len(self._items)

            self.title_lbl.configure(text=f"📋 FOGLI IN CODA ({self._selected + 1} di {total})")

            text, fg, bg = STATUS_STYLES.get(

                getattr(self._items[self._selected], "status", "pending"),

                STATUS_STYLES["pending"],

            )

            self.current_badge.configure(text=f" #{self._selected + 1} · {text} ", text_color=fg, fg_color=bg)



    def refresh_item(self, i: int, reload_image: bool = False):

        if 0 <= i < len(self._cells):

            cell = self._cells[i]

            item = self._items[i]

            self._apply_badge(cell, item)

            if reload_image and "img_lbl" in cell:

                try:

                    with Image.open(item.image_path) as im:

                        im.draft("RGB", (320, 240))

                        im = im.convert("RGB")

                        im.thumbnail((104, 64))

                        ctk_img = ctk.CTkImage(light_image=im.copy(), size=im.size)

                    cell["img_lbl"].configure(image=ctk_img)

                    cell["img_lbl"]._thumb_ref = ctk_img

                except Exception:

                    pass

        if i == self._selected:

            self._update_header_summary()



    def set_selected(self, idx: int):

        self._selected = idx

        for i, c in enumerate(self._cells):

            c["frame"].configure(border_color=PRIMARY if i == idx else BORDER,

                                 fg_color="#f0f9ff" if i == idx else "#f8fafc")

        self.prev_btn.configure(state="normal" if idx > 0 else "disabled")

        self.next_btn.configure(state="normal" if idx < len(self._cells) - 1 else "disabled")

        self._update_header_summary()

        try:

            if len(self._cells) > 1 and self._expanded:

                self.scroll._parent_canvas.xview_moveto(max(0.0, (idx - 1) / len(self._cells)))

        except Exception:

            pass





BatchStrip = BatchStripV3





class ScanProgressDialogV3(ctk.CTkToplevel):

    """Finestra di avanzamento per la scansione batch ADF, con annullamento cooperativo."""



    def __init__(self, parent, title: str, on_cancel: Callable[[], None]):

        super().__init__(parent)

        self.title(title)

        self.resizable(False, False)

        self.configure(fg_color="#f1f5f9")

        self._on_cancel = on_cancel

        _center_modal(self, parent, 440, 190)

        _make_modal(self, parent)

        self.protocol("WM_DELETE_WINDOW", self._cancel)



        card = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color=BORDER)

        card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        self.msg = ctk.CTkLabel(card, text="Avvio scansione...", font=("Segoe UI", 13, "bold"), text_color=TEXT)

        self.msg.pack(pady=(20, 10), padx=16)

        self.bar = ctk.CTkProgressBar(card, mode="indeterminate", width=360, progress_color=PRIMARY)

        self.bar.pack(pady=4)

        self.bar.start()

        self.cancel_btn = ctk.CTkButton(card, text="Interrompi lotto", width=140, height=32, corner_radius=8,

                                        fg_color="#e2e8f0", hover_color="#cbd5e1", text_color=TEXT,

                                        command=self._cancel)

        self.cancel_btn.pack(pady=(14, 10))



    def set_message(self, text: str):

        try:

            self.msg.configure(text=text)

        except tk.TclError:

            pass



    def _cancel(self):

        self.cancel_btn.configure(state="disabled", text="Interruzione...")

        self._on_cancel()



    def close(self):

        try:

            self.bar.stop()

            self.grab_release()

            self.destroy()

        except tk.TclError:

            pass





ScanProgressDialog = ScanProgressDialogV3





class ContinueScanDialogV3(ctk.CTkToplevel):

    """Dialog di continuazione per la scansione sequenziale su vetro. `result` = 'more' | 'done'."""



    def __init__(self, parent, count: int, blank: bool = False):

        super().__init__(parent)

        self.title("Foglio acquisito")

        self.resizable(False, False)

        self.configure(fg_color="#f1f5f9")

        self.result = "done"

        _center_modal(self, parent, 460, 210)

        _make_modal(self, parent)

        self.protocol("WM_DELETE_WINDOW", self._done)



        card = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color=BORDER)

        card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        ctk.CTkLabel(card, text=("⚪  Foglio vuoto: scartato" if blank else "✅  Foglio acquisito con successo!"),

                     font=("Segoe UI", 14, "bold"),

                     text_color=("#b45309" if blank else "#15803d")).pack(pady=(16, 2), padx=16, anchor="w")

        ctk.CTkLabel(card, text="Vuoi inserire un altro foglio nello scanner?",

                     font=("Segoe UI", 12), text_color=TEXT).pack(padx=16, anchor="w")

        row = ctk.CTkFrame(card, fg_color="transparent")

        row.pack(fill=tk.X, padx=16, pady=(18, 12), side=tk.BOTTOM)

        more = ctk.CTkButton(row, text="➕ Scansiona Altro Foglio", height=38, corner_radius=10,

                             fg_color=PRIMARY, hover_color=PRIMARY_HOVER, font=("Segoe UI", 12, "bold"),

                             command=self._more)

        more.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ctk.CTkButton(row, text=f"✓ Concludi Lotto ({count} fogli)", height=38, corner_radius=10,

                      fg_color="#16a34a", hover_color="#15803d", font=("Segoe UI", 12, "bold"),

                      command=self._done).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        self.bind("<Return>", lambda e: self._more())

        self.bind("<Escape>", lambda e: self._done())

        self.after(200, more.focus_set)



    def _more(self):

        self.result = "more"

        self.destroy()



    def _done(self):

        self.result = "done"

        self.destroy()





ContinueScanDialog = ContinueScanDialogV3





class DuplicateRowDialogV3(ctk.CTkToplevel):

    """Dialog per la gestione di una riga Excel già compilata: propone sovrascrittura oppure la prima riga libera (es. A5).



    Attributi dopo la chiusura:

      - `result`: 'next' | 'overwrite' | 'cancel'

      - `target_transport`: codice riga scelto (es. 'A5' o 'A1') oppure None se annullato

    """



    def __init__(self, parent, sheet_name: str, transport: str, next_available_slot: Optional[str] = None):

        super().__init__(parent)

        self.title("Riga Già Compilata")

        self.resizable(False, False)

        self.configure(fg_color="#f1f5f9")

        self.result: str = "cancel"

        self.target_transport: Optional[str] = None

        self._transport = transport

        self._next_slot = next_available_slot



        _center_modal(self, parent, 520, 265 if next_available_slot else 235)

        _make_modal(self, parent)

        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._build(sheet_name, transport, next_available_slot)



    def _build(self, sheet_name: str, transport: str, next_slot: Optional[str]):

        card = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color=BORDER)

        card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)



        head = ctk.CTkFrame(card, fg_color="transparent")

        head.pack(fill=tk.X, padx=18, pady=(16, 6))



        ctk.CTkLabel(head, text="⚠️", font=("Segoe UI Emoji", 26)).pack(side=tk.LEFT, padx=(0, 10))

        ctk.CTkLabel(

            head,

            text=f"ATTENZIONE: La riga {transport} di '{sheet_name}'\ncontiene già dei dati!",

            font=("Segoe UI", 13, "bold"),

            text_color="#b45309",

            justify="left",

            anchor="w",

        ).pack(side=tk.LEFT, fill=tk.X, expand=True)



        if next_slot:

            info_text = (

                f"La prima riga vuota disponibile su questo foglio è {next_slot}.\n"

                f"Vuoi metterla su {next_slot} oppure sovrascrivere {transport}?"

            )

        else:

            info_text = (

                "Tutte le righe da A1 a A10 risultano già compilate.\n"

                f"Vuoi sovrascrivere la riga {transport}?"

            )



        ctk.CTkLabel(

            card,

            text=info_text,

            font=("Segoe UI", 11),

            text_color=TEXT,

            justify="left",

            anchor="w",

            wraplength=460,

        ).pack(fill=tk.X, padx=18, pady=(4, 12))



        btn_row = ctk.CTkFrame(card, fg_color="transparent")

        btn_row.pack(fill=tk.X, padx=18, pady=(4, 14), side=tk.BOTTOM)



        ctk.CTkButton(

            btn_row,

            text="Annulla",

            width=90,

            height=38,

            corner_radius=10,

            fg_color="#e2e8f0",

            hover_color="#cbd5e1",

            text_color=TEXT,

            font=("Segoe UI", 11, "bold"),

            command=self._cancel,

        ).pack(side=tk.RIGHT, padx=(6, 0))



        overwrite_btn = ctk.CTkButton(

            btn_row,

            text=f"✏️ Sovrascrivi {transport}",

            width=145,

            height=38,

            corner_radius=10,

            fg_color="#f59e0b",

            hover_color="#d97706",

            text_color="#ffffff",

            font=("Segoe UI", 11, "bold"),

            command=self._choose_overwrite,

        )

        overwrite_btn.pack(side=tk.RIGHT, padx=(6, 0))



        if next_slot:

            next_btn = ctk.CTkButton(

                btn_row,

                text=f"➕ Metti su {next_slot} (Vuota)",

                height=38,

                corner_radius=10,

                fg_color="#16a34a",

                hover_color="#15803d",

                text_color="#ffffff",

                font=("Segoe UI", 12, "bold"),

                command=self._choose_next,

            )

            next_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

            self.bind("<Return>", lambda e: self._choose_next())

            self.after(180, next_btn.focus_set)

        else:

            self.bind("<Return>", lambda e: self._choose_overwrite())

            self.after(180, overwrite_btn.focus_set)



        self.bind("<Escape>", lambda e: self._cancel())



    def _choose_next(self):

        if self._next_slot:

            self.result = "next"

            self.target_transport = self._next_slot

        self.destroy()



    def _choose_overwrite(self):

        self.result = "overwrite"

        self.target_transport = self._transport

        self.destroy()



    def _cancel(self):

        self.result = "cancel"

        self.target_transport = None

        self.destroy()





DuplicateRowDialog = DuplicateRowDialogV3

