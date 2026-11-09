"""Componenti grafici modernizzati con bordi arrotondati (CustomTkinter) per la Versione 3 (v3)."""

import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Optional, Callable
from PIL import Image, ImageTk, ImageEnhance, ImageOps, ImageFilter, ImageChops
import customtkinter as ctk
import webbrowser

from ..services.quota_tracker import get_quota_tracker

from .theme import (
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_HEADER_BG,
    COLOR_SUCCESS,
    COLOR_DANGER,
    COLOR_DANGER_LIGHT,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    FONT_MAIN,
    FONT_BOLD,
    FONT_SUBTITLE,
    FONT_TITLE,
)


class ExcelNotOpenDialogV3(ctk.CTkToplevel):
    """Dialog modale anti-crash mostrata quando Excel non è aperto (Look v3 moderno arrotondato)."""

    def __init__(self, parent, retry_callback: Callable[[], bool], cancel_callback: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.title("Excel Non Rilevato")
        self.geometry("490x250")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.retry_callback = retry_callback
        self.cancel_callback = cancel_callback
        self.result = False

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 490) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 250) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self._build_ui()

    def _build_ui(self):
        container = ctk.CTkFrame(self, corner_radius=14, fg_color="#ffffff")
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # Header con icona e avviso
        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill=tk.X, pady=(0, 10))

        icon_lbl = ctk.CTkLabel(header, text="⚠️", font=("Segoe UI", 26))
        icon_lbl.pack(side=tk.LEFT, padx=(0, 12))

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ctk.CTkLabel(
            titles,
            text="Microsoft Excel non è aperto",
            font=("Segoe UI", 15, "bold"),
            text_color="#dc2626"
        ).pack(anchor=tk.W)

        ctk.CTkLabel(
            titles,
            text="Il file gestionale deve essere aperto durante la registrazione.",
            font=("Segoe UI", 11),
            text_color="#64748b"
        ).pack(anchor=tk.W)

        # Box informativo con bordi arrotondati
        info_box = ctk.CTkFrame(container, corner_radius=10, fg_color="#fef2f2", border_width=1, border_color="#fecaca")
        info_box.pack(fill=tk.X, pady=(4, 16))

        ctk.CTkLabel(
            info_box,
            text="Aprire il file gestionale in Microsoft Excel e cliccare 'Riprova'.\nI dati della scheda attuale non andranno persi.",
            font=("Segoe UI", 11, "bold"),
            text_color="#dc2626",
            justify=tk.LEFT
        ).pack(anchor=tk.W, padx=12, pady=10)

        # Barra pulsanti arrotondati
        btn_bar = ctk.CTkFrame(container, fg_color="transparent")
        btn_bar.pack(fill=tk.X)

        cancel_btn = ctk.CTkButton(
            btn_bar,
            text="Cancella",
            font=("Segoe UI", 11),
            fg_color="#e2e8f0",
            text_color="#0f172a",
            hover_color="#cbd5e1",
            corner_radius=8,
            width=100,
            height=34,
            command=self._on_cancel
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        retry_btn = ctk.CTkButton(
            btn_bar,
            text="🔄 Riprova Connessione",
            font=("Segoe UI", 11, "bold"),
            fg_color="#16a34a",
            hover_color="#15803d",
            corner_radius=8,
            height=34,
            command=self._on_retry
        )
        retry_btn.pack(side=tk.RIGHT)
        retry_btn.focus_set()

    def _on_retry(self):
        if self.retry_callback():
            self.result = True
            self.destroy()
        else:
            messagebox.showwarning(
                "Ancora non rilevato",
                "Excel non risulta ancora aperto. Apri il file gestionale in Excel prima di riprovare.",
                parent=self
            )

    def _on_cancel(self):
        if self.cancel_callback:
            self.cancel_callback()
        self.result = False
        self.destroy()


class ImageViewerV3(ctk.CTkFrame):
    """Pannello anteprima documento con bordi arrotondati, auto-adattamento, zoom e drag-pan."""

    BRIGHTNESS_STEP = 0.10
    CONTRAST_STEP = 0.12
    MAX_STEPS = 30

    def __init__(
        self,
        parent,
        on_reprocess_callback: Optional[Callable[[], None]] = None,
        on_image_changed_callback: Optional[Callable[[Path], None]] = None,
        on_sonnet_reprocess_callback: Optional[Callable[[], None]] = None,
        on_preset_changed_callback: Optional[Callable[[int], None]] = None,
        **kwargs,
    ):
        super().__init__(parent, corner_radius=12, fg_color="#ffffff", border_width=1, border_color="#e2e8f0", **kwargs)
        self.on_reprocess_callback = on_reprocess_callback
        self.on_image_changed_callback = on_image_changed_callback
        self.on_sonnet_reprocess_callback = on_sonnet_reprocess_callback
        self.on_preset_changed_callback = on_preset_changed_callback

        self.current_image_path: Optional[Path] = None
        self.pil_image: Optional[Image.Image] = None
        self._original_pil_image: Optional[Image.Image] = None
        self._base_processed_image: Optional[Image.Image] = None
        self._original_cache: dict[str, Image.Image] = {}
        self._processed_cache: dict[str, Image.Image] = {}
        self._preset_state_cache: dict[str, int] = {}
        self._active_preset: int = 0
        self._brightness_steps: int = 0
        self._contrast_steps: int = 0
        self.tk_image: Optional[ImageTk.PhotoImage] = None
        self.zoom_factor: float = 1.0

        self._pan_start_x = 0
        self._pan_start_y = 0
        self._offset_x = 0
        self._offset_y = 0

        self._build_ui()

    def _build_ui(self):
        # Sezione superiore compatta: Sinistra (Toolbar strumenti + Schermo Intero), Destra (solo Rianalizza IA)
        top_section = ctk.CTkFrame(self, fg_color="transparent")
        top_section.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(6, 4))

        # Barra strumenti per le modifiche dell'immagine (include anche Schermo Intero F11)
        toolbar = ctk.CTkFrame(top_section, corner_radius=8, fg_color="#f8fafc", border_width=1, border_color="#e2e8f0")
        toolbar.pack(side=tk.LEFT, anchor=tk.W)

        inner_tb = ctk.CTkFrame(toolbar, fg_color="transparent")
        inner_tb.pack(padx=6, pady=4)

        # 1. Gruppo Rotazione
        rot_l_btn = ctk.CTkButton(
            inner_tb, text="⟲ 90°", font=("Segoe UI", 10, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=42, height=30,
            command=lambda: self.rotate_image(-90)
        )
        rot_l_btn.pack(side=tk.LEFT, padx=1)

        rot_r_btn = ctk.CTkButton(
            inner_tb, text="⟳ 90°", font=("Segoe UI", 10, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=42, height=30,
            command=lambda: self.rotate_image(90)
        )
        rot_r_btn.pack(side=tk.LEFT, padx=1)

        # Separatore 1
        sep1 = ctk.CTkFrame(inner_tb, width=1, height=18, fg_color="#cbd5e1")
        sep1.pack(side=tk.LEFT, padx=4, pady=5)

        # 2. Gruppo Preset per Scansioni (P0 standard + P1-P5) + Reset
        self.p0_btn = ctk.CTkButton(
            inner_tb, text="P0", font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7", text_color="#ffffff", hover_color="#0369a1",
            corner_radius=6, width=38, height=30,
            command=self.apply_preset_0
        )
        self.p0_btn.pack(side=tk.LEFT, padx=1)

        self.p1_btn = ctk.CTkButton(
            inner_tb, text="P1: Matita", font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7", text_color="#ffffff", hover_color="#0369a1",
            corner_radius=6, width=76, height=30,
            command=self.apply_preset_1
        )
        self.p1_btn.pack(side=tk.LEFT, padx=1)

        self.p2_btn = ctk.CTkButton(
            inner_tb, text="P2: Biro Forte", font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7", text_color="#ffffff", hover_color="#0369a1",
            corner_radius=6, width=86, height=30,
            command=self.apply_preset_2
        )
        self.p2_btn.pack(side=tk.LEFT, padx=1)

        self.p3_btn = ctk.CTkButton(
            inner_tb, text="P3: Standard", font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7", text_color="#ffffff", hover_color="#0369a1",
            corner_radius=6, width=80, height=30,
            command=self.apply_preset_3
        )
        self.p3_btn.pack(side=tk.LEFT, padx=1)

        self.p4_btn = ctk.CTkButton(
            inner_tb, text="P4: Sbianca Fondo", font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7", text_color="#ffffff", hover_color="#0369a1",
            corner_radius=6, width=108, height=30,
            command=self.apply_preset_4
        )
        self.p4_btn.pack(side=tk.LEFT, padx=1)

        self.p5_btn = ctk.CTkButton(
            inner_tb, text="P5: Micro Tratto", font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7", text_color="#ffffff", hover_color="#0369a1",
            corner_radius=6, width=96, height=30,
            command=self.apply_preset_5
        )
        self.p5_btn.pack(side=tk.LEFT, padx=1)

        self.reset_btn = ctk.CTkButton(
            inner_tb, text="↺ Reset", font=("Segoe UI", 10, "bold"),
            fg_color="#f1f5f9", text_color="#64748b", hover_color="#e2e8f0",
            corner_radius=6, width=54, height=30,
            command=self.reset_image
        )
        self.reset_btn.pack(side=tk.LEFT, padx=1)

        # Separatore 2
        sep2 = ctk.CTkFrame(inner_tb, width=1, height=18, fg_color="#cbd5e1")
        sep2.pack(side=tk.LEFT, padx=4, pady=5)

        # 3. Gruppo Rifiniture Manuali (Luminosità e Contrasto non distruttivi rispetto al preset)
        ctk.CTkLabel(inner_tb, text="Lum:", font=("Segoe UI", 9, "bold"), text_color="#64748b").pack(side=tk.LEFT, padx=(2, 1))
        lum_minus_btn = ctk.CTkButton(
            inner_tb, text="−", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=26, height=30,
            command=lambda: self.adjust_brightness(0.90)
        )
        lum_minus_btn.pack(side=tk.LEFT, padx=1)

        lum_plus_btn = ctk.CTkButton(
            inner_tb, text="+", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=26, height=30,
            command=lambda: self.adjust_brightness(1.10)
        )
        lum_plus_btn.pack(side=tk.LEFT, padx=1)

        ctk.CTkLabel(inner_tb, text="Contr:", font=("Segoe UI", 9, "bold"), text_color="#64748b").pack(side=tk.LEFT, padx=(4, 1))
        cont_minus_btn = ctk.CTkButton(
            inner_tb, text="−", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=26, height=30,
            command=lambda: self.adjust_contrast(0.88)
        )
        cont_minus_btn.pack(side=tk.LEFT, padx=1)

        cont_plus_btn = ctk.CTkButton(
            inner_tb, text="+", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=26, height=30,
            command=lambda: self.adjust_contrast(1.15)
        )
        cont_plus_btn.pack(side=tk.LEFT, padx=1)

        # Separatore 3
        sep3 = ctk.CTkFrame(inner_tb, width=1, height=18, fg_color="#cbd5e1")
        sep3.pack(side=tk.LEFT, padx=4, pady=5)

        # 4. Gruppo Zoom + Adatta + Schermo Intero (F11)
        zoom_out_btn = ctk.CTkButton(
            inner_tb, text="➖", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=26, height=30,
            command=self.zoom_out
        )
        zoom_out_btn.pack(side=tk.LEFT, padx=1)

        self.zoom_lbl = ctk.CTkLabel(
            inner_tb, text="100%", font=("Segoe UI", 10, "bold"),
            text_color="#0f172a", width=36
        )
        self.zoom_lbl.pack(side=tk.LEFT, padx=1)

        zoom_in_btn = ctk.CTkButton(
            inner_tb, text="➕", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=26, height=30,
            command=self.zoom_in
        )
        zoom_in_btn.pack(side=tk.LEFT, padx=1)

        fit_btn = ctk.CTkButton(
            inner_tb, text="↔ Adatta", font=("Segoe UI", 10, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=6, width=54, height=30,
            command=self.fit_to_screen
        )
        fit_btn.pack(side=tk.LEFT, padx=1)

        sep4 = ctk.CTkFrame(inner_tb, width=1, height=18, fg_color="#cbd5e1")
        sep4.pack(side=tk.LEFT, padx=4, pady=5)

        fullscreen_btn = ctk.CTkButton(
            inner_tb,
            text="⛶ F11",
            font=("Segoe UI", 10, "bold"),
            fg_color="#334155",
            text_color="#ffffff",
            hover_color="#1e293b",
            corner_radius=6,
            width=50,
            height=30,
            command=self.open_fullscreen
        )
        fullscreen_btn.pack(side=tk.LEFT, padx=(1, 2))
        self.fullscreen_btn = fullscreen_btn

        # Azione a destra: solo Rianalizza IA, compatto e allineato alla barra strumenti
        top_actions = ctk.CTkFrame(top_section, fg_color="transparent")
        top_actions.pack(side=tk.RIGHT, anchor=tk.E)

        if self.on_reprocess_callback:
            reprocess_btn = ctk.CTkButton(
                top_actions,
                text="⚡ Rianalizza IA",
                font=("Segoe UI", 11, "bold"),
                fg_color="#0284c7",
                hover_color="#0369a1",
                corner_radius=8,
                width=128,
                height=38,
            )
            reprocess_btn.pack(side=tk.RIGHT)
            # Bind diretto per intercettare lo Shift (Shift+Click = Sonnet on-demand)
            reprocess_btn.bind("<Button-1>", self._on_reprocess_click)
            for child in reprocess_btn.winfo_children():
                child.bind("<Button-1>", self._on_reprocess_click)
            self.reprocess_btn = reprocess_btn
        else:
            self.reprocess_btn = None

        # Canvas con contenitore arrotondato
        canvas_frame = ctk.CTkFrame(self, corner_radius=8, fg_color="#0f172a")
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        self.canvas = tk.Canvas(canvas_frame, bg="#0f172a", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_drag)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

        self._render_placeholder()

    def _render_placeholder(self):
        self.canvas.delete("all")
        w = max(self.canvas.winfo_width(), 300)
        h = max(self.canvas.winfo_height(), 350)
        self.canvas.create_text(
            w // 2, h // 2,
            text="📷 Nessuna scheda acquisita\n\nPremi F2 per Scansione da Scanner\noppure carica una foto dal computer",
            fill="#94a3b8",
            font=("Segoe UI", 11, "italic"),
            justify=tk.CENTER
        )

    def load_image(self, image_path: str, initial_preset: Optional[int] = None):
        """Carica l'immagine usando una cache in memoria per rendere istantaneo lo switch tra schede."""
        self.current_image_path = Path(image_path)
        key = str(self.current_image_path)
        try:
            try:
                mtime = self.current_image_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            cached = self._original_cache.get(key)
            if cached is not None and getattr(cached, "_ba_mtime", None) == mtime:
                self._original_pil_image = cached
                self.pil_image = self._processed_cache.get(key, cached)
                self._active_preset = self._preset_state_cache.get(key, 0)
            else:
                with Image.open(self.current_image_path) as img:
                    img.load()
                    base = img.convert("RGB") if img.mode != "RGB" else img.copy()
                base._ba_mtime = mtime
                self._original_cache[key] = base
                self._processed_cache.pop(key, None)
                self._preset_state_cache.pop(key, None)
                self._original_pil_image = base
                self.pil_image = base
                self._active_preset = 0
            self._base_processed_image = self.pil_image
            self._brightness_steps = 0
            self._contrast_steps = 0
            self._offset_x = 0
            self._offset_y = 0
            self.zoom_factor = 1.0
            if initial_preset is not None:
                if key in self._processed_cache and self._preset_state_cache.get(key) == initial_preset:
                    self.pil_image = self._processed_cache[key]
                    self._base_processed_image = self.pil_image
                    self._active_preset = initial_preset
                    self._update_preset_buttons()
                    self._display_image()
                else:
                    self.set_active_preset(initial_preset)
            else:
                self._update_preset_buttons()
                self._display_image()
        except Exception as e:
            self.canvas.delete("all")
            self.canvas.create_text(
                150, 150,
                text=f"Errore caricamento immagine:\n{e}",
                fill="#ef4444",
                font=("Segoe UI", 10)
            )

    def _save_and_refresh(self):
        if not self.pil_image or not self.current_image_path:
            return
        to_save = self.pil_image if self.pil_image.mode == "RGB" else self.pil_image.convert("RGB")
        to_save.save(str(self.current_image_path), format="JPEG", quality=95)
        self._display_image()
        if self.on_image_changed_callback and self.current_image_path:
            try:
                self.on_image_changed_callback(self.current_image_path)
            except Exception:
                pass

    def _persist_rotation(self, sync_raw_rotation_ccw: Optional[int] = None):
        """Salva su disco l'originale ruotato (la rotazione è permanente, i preset no) e aggiorna le cache."""
        if not self._original_pil_image or not self.current_image_path:
            return
        self._original_pil_image.save(str(self.current_image_path), format="JPEG", quality=95)
        if sync_raw_rotation_ccw is not None:
            try:
                from ..scanner.scanner_service import ScannerService
                ScannerService.rotate_sibling_raw_backup(self.current_image_path, sync_raw_rotation_ccw)
            except Exception:
                pass
        key = str(self.current_image_path)
        try:
            self._original_pil_image._ba_mtime = self.current_image_path.stat().st_mtime
        except OSError:
            pass
        self._original_cache[key] = self._original_pil_image
        if self._active_preset:
            self._processed_cache[key] = self.pil_image
        else:
            self._processed_cache.pop(key, None)
        self._display_image()
        if self.on_image_changed_callback:
            try:
                self.on_image_changed_callback(self.current_image_path)
            except Exception:
                pass

    def _on_reprocess_click(self, event=None):
        """Click = rianalisi normale; Shift+Click = rianalisi con il modello forte (Sonnet)."""
        shift = bool(event is not None and (event.state & 0x0001))
        if shift and self.on_sonnet_reprocess_callback:
            self.on_sonnet_reprocess_callback()
        elif self.on_reprocess_callback:
            self.on_reprocess_callback()
        return "break"

    def _recompute_from_base(self):
        """Ricalcola l'immagine partendo dal preset/base attivo applicando gli step netti di luminosità e contrasto in memoria."""
        src = self._base_processed_image or self._original_pil_image or self.pil_image
        if src is None:
            return
        img = src.copy()
        if img.mode != "RGB":
            img = img.convert("RGB")
        b_factor = max(0.15, round(1.0 + (self._brightness_steps * self.BRIGHTNESS_STEP), 4))
        c_factor = max(0.15, round(1.0 + (self._contrast_steps * self.CONTRAST_STEP), 4))
        if self._brightness_steps != 0:
            img = ImageEnhance.Brightness(img).enhance(b_factor)
        if self._contrast_steps != 0:
            img = ImageEnhance.Contrast(img).enhance(c_factor)
        self.pil_image = img
        self._display_image()

    def rotate_image(self, degrees: int = 90) -> bool:
        if not self.pil_image or not self.current_image_path:
            return False
        try:
            self.pil_image = self.pil_image.rotate(-degrees, expand=True)
            if getattr(self, "_original_pil_image", None) is not None:
                self._original_pil_image = self._original_pil_image.rotate(-degrees, expand=True)
            if getattr(self, "_base_processed_image", None) is not None:
                self._base_processed_image = self._base_processed_image.rotate(-degrees, expand=True)
            self._offset_x = 0
            self._offset_y = 0
            self._persist_rotation(sync_raw_rotation_ccw=-degrees)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore rotazione: {e}")
            except Exception:
                pass
            return False

    def adjust_brightness(self, factor: float = 1.10) -> bool:
        if not self.pil_image or not self.current_image_path:
            return False
        try:
            if self._base_processed_image is None:
                self._base_processed_image = (self._original_pil_image or self.pil_image).copy()
            delta = 1 if factor >= 1.0 else -1
            self._brightness_steps = max(-self.MAX_STEPS, min(self.MAX_STEPS, self._brightness_steps + delta))
            self._recompute_from_base()
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore luminosità: {e}")
            except Exception:
                pass
            return False

    def adjust_contrast(self, factor: float = 1.15) -> bool:
        if not self.pil_image or not self.current_image_path:
            return False
        try:
            if self._base_processed_image is None:
                self._base_processed_image = (self._original_pil_image or self.pil_image).copy()
            delta = 1 if factor >= 1.0 else -1
            self._contrast_steps = max(-self.MAX_STEPS, min(self.MAX_STEPS, self._contrast_steps + delta))
            self._recompute_from_base()
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore contrasto: {e}")
            except Exception:
                pass
            return False

    def _get_base_image(self) -> Optional[Image.Image]:
        """Restituisce una copia dell'immagine originale non alterata se disponibile."""
        if getattr(self, "_original_pil_image", None) is not None:
            base = self._original_pil_image.copy()
        elif getattr(self, "pil_image", None) is not None:
            base = self.pil_image.copy()
            self._original_pil_image = self.pil_image.copy()
        elif getattr(self, "current_image_path", None) is not None and Path(self.current_image_path).exists():
            try:
                with Image.open(self.current_image_path) as img:
                    img.load()
                    base = img.convert("RGB") if img.mode != "RGB" else img.copy()
                self._original_pil_image = base.copy()
                if getattr(self, "pil_image", None) is None:
                    self.pil_image = base.copy()
            except Exception:
                return None
        else:
            return None
        if base.mode != "RGB":
            base = base.convert("RGB")
        return base

    # ------------------------------------------------------------------ PRESET
    # I preset NON sovrascrivono il file originale su disco: lavorano solo sull'anteprima
    # (l'IA continua a ricevere la scansione originale). Ogni preset parte sempre
    # dall'originale, quindi sono alternativi e non cumulativi.

    _PRESET_ACTIVE_COLOR = "#0c4a6e"
    _PRESET_IDLE_COLOR = "#0284c7"

    def _update_preset_buttons(self):
        buttons = (
            (0, getattr(self, "p0_btn", None)),
            (1, getattr(self, "p1_btn", None)),
            (2, getattr(self, "p2_btn", None)),
            (3, getattr(self, "p3_btn", None)),
            (4, getattr(self, "p4_btn", None)),
            (5, getattr(self, "p5_btn", None)),
        )
        for idx, btn in buttons:
            if btn is None:
                continue
            active = (idx == self._active_preset)
            btn.configure(
                fg_color=self._PRESET_ACTIVE_COLOR if active else self._PRESET_IDLE_COLOR,
                border_width=2 if active else 0,
                border_color="#fbbf24",
            )

    def _apply_preset_result(self, img: Image.Image, preset_id: int):
        if img.mode != "RGB":
            img = img.convert("RGB")
        self.pil_image = img
        self._base_processed_image = img
        self._brightness_steps = 0
        self._contrast_steps = 0
        self._active_preset = preset_id
        if self.current_image_path:
            key = str(self.current_image_path)
            self._processed_cache[key] = img
            self._preset_state_cache[key] = preset_id
        self._update_preset_buttons()
        self._display_image()
        if getattr(self, "on_preset_changed_callback", None):
            try:
                self.on_preset_changed_callback(preset_id)
            except Exception:
                pass

    def apply_preset_0(self) -> bool:
        """Preset 0: 'P0' – scansione originale standard pura senza filtri né modifiche."""
        base = self._get_base_image()
        if base is None:
            return False
        try:
            self._apply_preset_result(base, 0)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore applicazione Preset 0: {e}")
            except Exception:
                pass
            return False

    def set_active_preset(self, preset_id: int) -> bool:
        """Imposta programmaticamente il preset attivo (0..5)."""
        if preset_id == 0:
            return self.apply_preset_0()
        elif preset_id == 1:
            return self.apply_preset_1()
        elif preset_id == 2:
            return self.apply_preset_2()
        elif preset_id == 3:
            return self.apply_preset_3()
        elif preset_id == 4:
            return self.apply_preset_4()
        elif preset_id == 5:
            return self.apply_preset_5()
        return False

    def get_active_preset(self) -> int:
        """Restituisce l'ID del preset attualmente attivo (0..5)."""
        return self._active_preset

    def save_temp_ai_copy(self) -> Optional[Path]:
        """Salva una copia dell'immagine attualmente visualizzata nel visore in test_images/temp_ai/."""
        if not self.pil_image or not self.current_image_path:
            return None
        try:
            from ..scanner.preset_engine import TEMP_AI_DIR
            TEMP_AI_DIR.mkdir(parents=True, exist_ok=True)
            temp_path = TEMP_AI_DIR / f"{self.current_image_path.stem}_p{self._active_preset}.jpg"
            to_save = self.pil_image if self.pil_image.mode == "RGB" else self.pil_image.convert("RGB")
            to_save.save(str(temp_path), format="JPEG", quality=95)
            return temp_path
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore salvataggio temp_ai: {e}")
            except Exception:
                pass
            return None

    def _gray_base(self):
        base = self._get_base_image()
        if base is None:
            return None
        import numpy as np
        return np.array(base.convert("L"))

    def apply_preset_1(self) -> bool:
        """Preset 1: 'Matita' – amplificazione grafite chiara con curva non-lineare Photoshop Gamma 0.14."""
        base = self._get_base_image()
        if base is None:
            return False
        try:
            try:
                from ..scanner.preset_engine import apply_preset
                out = apply_preset(base, 1, return_pil=True)
            except Exception:
                # Fallback puro Pillow
                from PIL import ImageChops, ImageFilter, ImageOps
                gray = base.convert("L")
                bg = gray.filter(ImageFilter.GaussianBlur(radius=25))
                diff = ImageChops.subtract(bg, gray)
                ink = diff.point(lambda p: min(255, int((p - 1.5) * 25.5)) if p > 1 else 0)
                dark = gray.point(lambda p: min(255, int((200 - p) * 3.2)) if p < 200 else 0)
                comb = ImageChops.lighter(ink, dark)
                thick = comb.filter(ImageFilter.MaxFilter(3))
                out = ImageOps.invert(thick)

            self._apply_preset_result(out, 1)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore Preset 1: {e}")
            except Exception:
                pass
            return False

    def apply_preset_2(self) -> bool:
        """Preset 2: 'Biro Forte' – contrasto controllato e CLAHE morbido per non impastare occhielli."""
        base = self._get_base_image()
        if base is None:
            return False
        try:
            try:
                from ..scanner.preset_engine import apply_preset
                out = apply_preset(base, 2, return_pil=True)
            except Exception:
                # Fallback puro Pillow
                from PIL import ImageChops, ImageFilter, ImageOps
                gray = base.convert("L")
                bg = gray.filter(ImageFilter.GaussianBlur(radius=15))
                diff = ImageChops.subtract(bg, gray)
                ink = diff.point(lambda p: 255 if p > 2 else 0)
                thick = ink.filter(ImageFilter.MaxFilter(3))
                out = ImageOps.invert(thick)

            self._apply_preset_result(out, 2)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore Preset 2: {e}")
            except Exception:
                pass
            return False

    def apply_preset_3(self) -> bool:
        """Preset 3: 'Standard' – replica Curva ad S Photoshop con contrasto e nitidezza bilanciati."""
        base = self._get_base_image()
        if base is None:
            return False
        try:
            try:
                from ..scanner.preset_engine import apply_preset
                out = apply_preset(base, 3, return_pil=True)
            except Exception:
                # Fallback puro Pillow
                from PIL import ImageFilter, ImageOps
                gray = base.convert("L")
                ac = ImageOps.autocontrast(gray, cutoff=1)
                lut = [0 if p <= 120 else (255 if p >= 235 else int((p - 120) * 255 / 115)) for p in range(256)]
                res = ac.point(lut)
                out = res.filter(ImageFilter.MinFilter(3))

            self._apply_preset_result(out, 3)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore Preset 3: {e}")
            except Exception:
                pass
            return False

    def apply_preset_4(self) -> bool:
        """Preset 4: 'Sbianca Fondo' – normalizzazione fotometrica per carte scure o ombre ADF."""
        base = self._get_base_image()
        if base is None:
            return False
        try:
            try:
                from ..scanner.preset_engine import apply_preset
                out = apply_preset(base, 4, return_pil=True)
            except Exception:
                # Fallback puro Pillow
                from PIL import ImageFilter, ImageOps, ImageChops
                gray = base.convert("L")
                bg = gray.filter(ImageFilter.GaussianBlur(radius=35))
                diff = ImageChops.difference(gray, bg)
                inv = ImageOps.invert(diff)
                out = ImageOps.autocontrast(inv, cutoff=2)

            self._apply_preset_result(out, 4)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore Preset 4: {e}")
            except Exception:
                pass
            return False

    def apply_preset_5(self) -> bool:
        """Preset 5: 'Micro Tratto' – CLAHE micro-celle, unsharp marcato e micro-ricongiunzione."""
        base = self._get_base_image()
        if base is None:
            return False
        try:
            try:
                from ..scanner.preset_engine import apply_preset
                out = apply_preset(base, 5, return_pil=True)
            except Exception:
                # Fallback puro Pillow
                from PIL import ImageFilter, ImageEnhance
                gray = base.convert("L")
                sharp = ImageEnhance.Sharpness(gray).enhance(2.5)
                contr = ImageEnhance.Contrast(sharp).enhance(1.4)
                out = contr.filter(ImageFilter.SHARPEN)

            self._apply_preset_result(out, 5)
            return True
        except Exception as e:
            try:
                from ..services.session_logger import get_session_logger
                get_session_logger().logger.warning(f"Errore Preset 5: {e}")
            except Exception:
                pass
            return False

    def auto_enhance_document(self) -> bool:
        """Applica Preset 1 come ottimizzazione automatica predefinita."""
        return self.apply_preset_1()

    def reset_image(self) -> bool:
        """Ripristina l'anteprima allo stato originale della scansione (P0)."""
        return self.apply_preset_0()

    def open_fullscreen(self):
        if self.current_image_path and self.current_image_path.exists():
            target_path = self.current_image_path
            def _after_fs_rotate():
                if not target_path:
                    return
                key = str(target_path)
                self._original_cache.pop(key, None)
                self._processed_cache.pop(key, None)
                self._preset_state_cache.pop(key, None)
                if self.current_image_path and str(self.current_image_path) == key:
                    self.load_image(target_path, initial_preset=self._active_preset)
                if self.on_image_changed_callback:
                    try:
                        self.on_image_changed_callback(target_path)
                    except Exception:
                        pass
            FullScreenViewerDialogV3(
                self.winfo_toplevel(),
                str(self.current_image_path),
                initial_image=self.pil_image,
                persist_rotation=True,
                on_rotated=_after_fs_rotate,
            )
        else:
            messagebox.showinfo("Nessuna Immagine", "Acquisisci o carica prima una scheda.", parent=self)

    def clear(self):
        self.current_image_path = None
        self.pil_image = None
        self._original_pil_image = None
        self._base_processed_image = None
        self._active_preset = 0
        self._brightness_steps = 0
        self._contrast_steps = 0
        self.tk_image = None
        self._offset_x = 0
        self._offset_y = 0
        self.zoom_factor = 1.0
        self._render_placeholder()

    def _on_resize(self, event):
        if self.pil_image:
            self._display_image()
        else:
            self._render_placeholder()

    def _on_pan_start(self, event):
        self._pan_start_x = event.x
        self._pan_start_y = event.y

    def _on_pan_drag(self, event):
        if not self.pil_image:
            return
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        self._offset_x += dx
        self._offset_y += dy
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._display_image()

    def _on_mousewheel(self, event):
        if not self.pil_image:
            return
        if event.delta > 0:
            self.zoom_factor = min(self.zoom_factor * 1.15, 3.5)
        else:
            self.zoom_factor = max(self.zoom_factor / 1.15, 0.4)
        self._display_image()

    def zoom_in(self):
        if not self.pil_image:
            return
        self.zoom_factor = min(self.zoom_factor * 1.25, 4.0)
        self._display_image()

    def zoom_out(self):
        if not self.pil_image:
            return
        self.zoom_factor = max(self.zoom_factor / 1.25, 0.35)
        self._display_image()

    def fit_to_screen(self):
        if not self.pil_image:
            return
        self.zoom_factor = 1.0
        self._offset_x = 0
        self._offset_y = 0
        self._display_image()

    def _display_image(self):
        if not self.pil_image:
            return
        c_w = self.canvas.winfo_width()
        c_h = self.canvas.winfo_height()
        if c_w < 10 or c_h < 10:
            try:
                self.canvas.update_idletasks()
                c_w = self.canvas.winfo_width()
                c_h = self.canvas.winfo_height()
            except Exception:
                pass
        if c_w < 10 or c_h < 10:
            return

        img_w, img_h = self.pil_image.size
        ratio = min(c_w / img_w, c_h / img_h) * self.zoom_factor
        new_w = max(10, int(img_w * ratio))
        new_h = max(10, int(img_h * ratio))

        resized = self.pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(resized)

        self.canvas.delete("all")
        pos_x = (c_w // 2) + self._offset_x

        # Se l'immagine è adattata o più bassa del canvas, ancorala in alto
        # con un margine sottile (eliminando la banda blu vuota sopra)
        if new_h <= c_h and self.zoom_factor <= 1.05:
            pos_y = (new_h // 2) + 6 + self._offset_y
        else:
            pos_y = (c_h // 2) + self._offset_y

        self.canvas.create_image(pos_x, pos_y, image=self.tk_image, anchor=tk.CENTER)
        self.canvas.image = self.tk_image

        if hasattr(self, "zoom_lbl"):
            self.zoom_lbl.configure(text=f"{int(self.zoom_factor * 100)}%")


class QuickEditRowDialogV3(ctk.CTkToplevel):
    """Finestra rapida ed ergonomica per modificare al volo Scatole e Kg (Look arrotondato)."""

    def __init__(self, parent, cat_name: str, current_scatole: int, current_kg: float, on_save: Callable[[int, float], None]):
        super().__init__(parent)
        self.title(f"Modifica Categoria: {cat_name}")
        self.geometry("440x310")
        self.resizable(False, False)
        self.configure(fg_color="#f1f5f9")
        self.transient(parent)
        self.grab_set()

        self.cat_name = cat_name
        self.on_save = on_save

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 440) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 310) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self._build_ui(current_scatole, current_kg)

    def _build_ui(self, current_scatole: int, current_kg: float):
        container = ctk.CTkFrame(
            self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color="#e2e8f0"
        )
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # Barra pulsanti ancorata in fondo con margini generosi
        btn_bar = ctk.CTkFrame(container, fg_color="transparent")
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=22, pady=(10, 18))

        cancel_btn = ctk.CTkButton(
            btn_bar, text="Annulla (Esc)", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=8, width=110, height=36,
            command=self.destroy
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        save_btn = ctk.CTkButton(
            btn_bar, text="💾 Salva (Invio)", font=("Segoe UI", 11, "bold"),
            fg_color="#16a34a", hover_color="#15803d",
            corner_radius=8, width=130, height=36,
            command=self._do_save
        )
        save_btn.pack(side=tk.RIGHT)

        # Header e Form con padding ampio
        ctk.CTkLabel(
            container,
            text=f"📦 Categoria: {self.cat_name}",
            font=("Segoe UI", 15, "bold"),
            text_color="#0f172a"
        ).pack(anchor=tk.W, padx=22, pady=(18, 2))

        ctk.CTkLabel(
            container,
            text="Digita i nuovi valori e premi Invio per salvare.",
            font=("Segoe UI", 11),
            text_color="#64748b"
        ).pack(anchor=tk.W, padx=22, pady=(0, 14))

        form = ctk.CTkFrame(container, fg_color="#f8fafc", corner_radius=10, border_width=1, border_color="#e2e8f0")
        form.pack(fill=tk.X, padx=22, pady=4)

        # Scatole
        ctk.CTkLabel(
            form, text="N° Scatole:", font=("Segoe UI", 12, "bold"), text_color="#0f172a"
        ).grid(row=0, column=0, sticky=tk.W, padx=(16, 10), pady=(12, 6))
        self.scatole_entry = ctk.CTkEntry(
            form, width=140, height=34, corner_radius=8, font=("Segoe UI", 12, "bold"),
            border_width=1, border_color="#94a3b8"
        )
        self.scatole_entry.insert(0, str(current_scatole))
        self.scatole_entry.grid(row=0, column=1, sticky=tk.W, padx=8, pady=(12, 6))

        # Kg
        ctk.CTkLabel(
            form, text="Totale Kg:", font=("Segoe UI", 12, "bold"), text_color="#0f172a"
        ).grid(row=1, column=0, sticky=tk.W, padx=(16, 10), pady=(6, 12))
        self.kg_entry = ctk.CTkEntry(
            form, width=140, height=34, corner_radius=8, font=("Segoe UI", 12, "bold"),
            border_width=1, border_color="#94a3b8"
        )
        self.kg_entry.insert(0, str(current_kg))
        self.kg_entry.grid(row=1, column=1, sticky=tk.W, padx=8, pady=(6, 12))

        self.scatole_entry.focus_set()
        self.scatole_entry.select_range(0, tk.END)

        self.bind("<Return>", lambda e: self._do_save())
        self.bind("<KP_Enter>", lambda e: self._do_save())
        self.bind("<Escape>", lambda e: self.destroy())

    def _do_save(self):
        raw_scat = self.scatole_entry.get().strip()
        raw_kg = self.kg_entry.get().strip().replace(",", ".")
        try:
            new_scat = int(raw_scat) if raw_scat else 0
            new_kg = float(raw_kg) if raw_kg else 0.0
            if new_scat < 0 or new_kg < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Valori non validi",
                "Inserisci un numero intero positivo per le scatole e un numero decimale per i Kg.",
                parent=self
            )
            return

        if new_scat <= 0 or new_kg <= 0:
            self.on_save(0, 0.0)
            self.destroy()
            return

        self.on_save(new_scat, round(new_kg, 2))
        self.destroy()


class QuickEditBoxDialogV3(ctk.CTkToplevel):
    """Finestra rapida per modificare al volo o eliminare il peso di una singola scatola (Look arrotondato v3)."""

    def __init__(
        self,
        parent,
        cat_name: str,
        box_index: int,
        current_kg: float,
        on_save: Callable[[float], None],
        on_delete: Optional[Callable[[], None]] = None
    ):
        super().__init__(parent)
        self.title(f"Modifica Singola Scatola: {cat_name}")
        self.geometry("450x275")
        self.resizable(False, False)
        self.configure(fg_color="#f1f5f9")
        self.transient(parent)
        self.grab_set()

        self.cat_name = cat_name
        self.box_index = box_index
        self.on_save = on_save
        self.on_delete = on_delete

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 450) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 275) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self._build_ui(current_kg)

    def _build_ui(self, current_kg: float):
        container = ctk.CTkFrame(
            self, corner_radius=14, fg_color="#ffffff", border_width=1, border_color="#e2e8f0"
        )
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # Barra pulsanti ancorata in fondo con margini generosi
        btn_bar = ctk.CTkFrame(container, fg_color="transparent")
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=22, pady=(10, 18))

        if self.on_delete:
            del_btn = ctk.CTkButton(
                btn_bar, text="🗑️ Elimina", font=("Segoe UI", 11, "bold"),
                fg_color="#fee2e2", text_color="#b91c1c", hover_color="#fecaca",
                corner_radius=8, width=96, height=36,
                command=self._do_delete
            )
            del_btn.pack(side=tk.LEFT)

        cancel_btn = ctk.CTkButton(
            btn_bar, text="Annulla (Esc)", font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0", text_color="#0f172a", hover_color="#cbd5e1",
            corner_radius=8, width=105, height=36,
            command=self.destroy
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        save_btn = ctk.CTkButton(
            btn_bar, text="💾 Salva (Invio)", font=("Segoe UI", 11, "bold"),
            fg_color="#16a34a", hover_color="#15803d",
            corner_radius=8, width=125, height=36,
            command=self._do_save
        )
        save_btn.pack(side=tk.RIGHT)

        # Header e Form con padding ampio
        ctk.CTkLabel(
            container,
            text=f"📦 {self.box_index + 1}ª Scatola — {self.cat_name}",
            font=("Segoe UI", 15, "bold"),
            text_color="#0f172a"
        ).pack(anchor=tk.W, padx=22, pady=(18, 2))

        ctk.CTkLabel(
            container,
            text="Modifica il peso rilevato per questo singolo collo.",
            font=("Segoe UI", 11),
            text_color="#64748b"
        ).pack(anchor=tk.W, padx=22, pady=(0, 14))

        form = ctk.CTkFrame(container, fg_color="#f8fafc", corner_radius=10, border_width=1, border_color="#e2e8f0")
        form.pack(fill=tk.X, padx=22, pady=4)

        ctk.CTkLabel(
            form, text="Peso Collo (Kg):", font=("Segoe UI", 12, "bold"), text_color="#0f172a"
        ).grid(row=0, column=0, sticky=tk.W, padx=(16, 12), pady=14)
        self.kg_entry = ctk.CTkEntry(
            form, width=150, height=36, corner_radius=8, font=("Segoe UI", 13, "bold"),
            border_width=1, border_color="#94a3b8"
        )
        self.kg_entry.insert(0, str(current_kg))
        self.kg_entry.grid(row=0, column=1, sticky=tk.W, padx=8, pady=14)

        self.kg_entry.focus_set()
        self.kg_entry.select_range(0, tk.END)

        self.bind("<Return>", lambda e: self._do_save())
        self.bind("<KP_Enter>", lambda e: self._do_save())
        self.bind("<Escape>", lambda e: self.destroy())

    def _do_save(self):
        raw_kg = self.kg_entry.get().strip().replace(",", ".")
        try:
            new_kg = float(raw_kg) if raw_kg else 0.0
            if new_kg < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Valore non valido",
                "Inserisci un peso valido in Kg (numero positivo o zero).",
                parent=self
            )
            return

        if new_kg <= 0:
            if self.on_delete:
                self.on_delete()
            self.destroy()
            return

        self.on_save(round(new_kg, 2))
        self.destroy()

    def _do_delete(self):
        if messagebox.askyesno("Conferma Eliminazione", f"Vuoi eliminare la {self.box_index + 1}ª scatola di {self.cat_name}?", parent=self):
            if self.on_delete:
                self.on_delete()
            self.destroy()


class FullScreenViewerDialogV3(ctk.CTkToplevel):
    """Ispezione a tutto schermo del documento con controlli zoom e tastiera."""

    def __init__(
        self,
        parent,
        image_path: str,
        initial_image: Optional[Image.Image] = None,
        persist_rotation: bool = True,
        on_rotated: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.title("Ispezione Documento — (Premi Esc per uscire)")
        self.geometry("1100x800")
        try:
            self.state("zoomed")
        except Exception:
            pass

        self.transient(parent)
        self.grab_set()

        self.image_path = Path(image_path)
        self.persist_rotation = bool(persist_rotation)
        self._on_rotated = on_rotated
        self.pil_image: Optional[Image.Image] = None
        self.tk_image: Optional[ImageTk.PhotoImage] = None
        self.zoom_level = 1.0

        if initial_image is not None:
            self.pil_image = initial_image.copy()
        elif self.image_path.exists():
            try:
                self.pil_image = Image.open(self.image_path)
            except Exception as e:
                print(f"Errore fullscreen: {e}")

        self._build_ui()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("r", lambda e: self._rotate())
        self.bind("R", lambda e: self._rotate())

    def _build_ui(self):
        top_bar = ctk.CTkFrame(self, corner_radius=0, fg_color="#0f172a", height=46)
        top_bar.pack(side=tk.TOP, fill=tk.X)

        ctk.CTkLabel(
            top_bar,
            text=f"Ispezione Scheda: {self.image_path.name}  |  Esc: Chiudi  |  R: Ruota  |  Rotella: Zoom",
            font=("Segoe UI", 11, "bold"),
            text_color="#ffffff"
        ).pack(side=tk.LEFT, padx=14, pady=8)

        close_btn = ctk.CTkButton(
            top_bar, text="✕ Chiudi (Esc)", font=("Segoe UI", 10, "bold"),
            fg_color="#dc2626", hover_color="#b91c1c", corner_radius=6,
            width=90, height=28, command=self.destroy
        )
        close_btn.pack(side=tk.RIGHT, padx=12, pady=8)

        fit_btn = ctk.CTkButton(
            top_bar, text="↔️ Adatta", font=("Segoe UI", 10),
            fg_color="#334155", hover_color="#1e293b", corner_radius=6,
            width=70, height=28, command=self._fit_to_screen
        )
        fit_btn.pack(side=tk.RIGHT, padx=6, pady=8)

        rot_btn = ctk.CTkButton(
            top_bar, text="⟳ Ruota (R)", font=("Segoe UI", 10),
            fg_color="#334155", hover_color="#1e293b", corner_radius=6,
            width=80, height=28, command=self._rotate
        )
        rot_btn.pack(side=tk.RIGHT, padx=4, pady=8)

        # Canvas
        frame = tk.Frame(self, bg="#0f172a")
        frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(frame, bg="#0f172a", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.after(100, self._fit_to_screen)

    def _rotate(self):
        if not self.pil_image:
            return
        self.pil_image = self.pil_image.rotate(-90, expand=True)
        if self.persist_rotation:
            try:
                if self.image_path.exists() and self.image_path.is_file():
                    with Image.open(self.image_path) as disk_im:
                        disk_im.load()
                        rot_disk = disk_im.rotate(-90, expand=True)
                        if rot_disk.mode != "RGB":
                            rot_disk = rot_disk.convert("RGB")
                        rot_disk.save(str(self.image_path), format="JPEG", quality=95)
                else:
                    to_save = self.pil_image if self.pil_image.mode == "RGB" else self.pil_image.convert("RGB")
                    to_save.save(str(self.image_path), format="JPEG", quality=95)
                from ..scanner.scanner_service import ScannerService
                ScannerService.rotate_sibling_raw_backup(self.image_path, -90)
                if self._on_rotated:
                    self._on_rotated()
            except Exception as e:
                print(f"Errore rotazione fullscreen: {e}")
        self._fit_to_screen()

    def _on_mousewheel(self, event):
        if not self.pil_image:
            return
        if event.delta > 0:
            self.zoom_level = min(self.zoom_level * 1.25, 4.0)
        else:
            self.zoom_level = max(self.zoom_level / 1.25, 0.25)
        self._render_image()

    def _fit_to_screen(self):
        if not self.pil_image:
            return
        c_w = max(self.canvas.winfo_width(), 600)
        c_h = max(self.canvas.winfo_height(), 500)
        img_w, img_h = self.pil_image.size
        ratio = min((c_w - 40) / img_w, (c_h - 40) / img_h)
        self.zoom_level = max(0.2, min(ratio, 3.0))
        self._render_image()

    def _render_image(self):
        if not self.pil_image:
            return
        img_w, img_h = self.pil_image.size
        new_w = max(10, int(img_w * self.zoom_level))
        new_h = max(10, int(img_h * self.zoom_level))

        resized = self.pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.tk_image = ImageTk.PhotoImage(resized)

        self.canvas.delete("all")
        c_w = self.canvas.winfo_width()
        c_h = self.canvas.winfo_height()

        pos_x = c_w // 2
        pos_y = c_h // 2

        self.canvas.create_image(pos_x, pos_y, image=self.tk_image, anchor=tk.CENTER)
        self.canvas.image = self.tk_image


class TooltipV3:
    """Tooltip leggero ed elegante al passaggio del mouse per widget CustomTkinter / Tkinter."""

    def __init__(self, widget, text_func_or_text, delay_ms: int = 250):
        self.widget = widget
        self.text_func_or_text = text_func_or_text
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip_window = None

        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")
        self.widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, event=None):
        self._schedule()

    def _on_leave(self, event=None):
        self._unschedule()
        self._hide()

    def _schedule(self):
        self._unschedule()
        try:
            self._after_id = self.widget.after(self.delay_ms, self._show)
        except Exception:
            pass

    def _unschedule(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip_window:
            return
        text = self.text_func_or_text() if callable(self.text_func_or_text) else self.text_func_or_text
        if not text:
            return

        try:
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return

        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass

        frame = tk.Frame(
            tw,
            background="#0f172a",
            borderwidth=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground="#38bdf8"
        )
        frame.pack()
        label = tk.Label(
            frame,
            text=text,
            justify=tk.LEFT,
            background="#0f172a",
            foreground="#f8fafc",
            font=("Segoe UI", 9),
            padx=10,
            pady=8
        )
        label.pack()

    def _hide(self):
        if self._tip_window:
            try:
                self._tip_window.destroy()
            except Exception:
                pass
            self._tip_window = None


class IAStatusDialogV3(ctk.CTkToplevel):
    """Finestra di monitoraggio per le quote IA, limiti Free Tier, frequenza e catena di fallback."""

    def __init__(
        self,
        parent,
        provider: str,
        model: str,
        fallback_model: Optional[str] = None,
        enable_fallback: bool = True,
        on_reset_callback: Optional[Callable[[], None]] = None
    ):
        super().__init__(parent)
        self.title("Stato & Limiti Quota IA")
        self.geometry("640x700")
        self.minsize(580, 580)
        self.transient(parent)
        self.grab_set()

        self.provider = provider
        self.model = model
        self.fallback_model = fallback_model
        self.enable_fallback = enable_fallback
        self.on_reset_callback = on_reset_callback
        self.tracker = get_quota_tracker()

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 640) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 700) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

        self._build_ui()

    def _build_ui(self):
        container = ctk.CTkFrame(self, corner_radius=14, fg_color="#f8fafc")
        container.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        # Header superiore
        header = ctk.CTkFrame(container, corner_radius=10, fg_color="#0f172a", height=58)
        header.pack(fill=tk.X, pady=(0, 10))

        head_content = ctk.CTkFrame(header, fg_color="transparent")
        head_content.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        ctk.CTkLabel(
            head_content,
            text="🤖  STATO & MONITORAGGIO QUOTE IA",
            font=("Segoe UI", 13, "bold"),
            text_color="#38bdf8"
        ).pack(side=tk.LEFT)

        status_info = self.tracker.get_status(self.provider, self.model)
        badge_color = status_info["color"]

        self.badge_lbl = ctk.CTkLabel(
            head_content,
            text=f"● {status_info['status_text']}",
            font=("Segoe UI", 11, "bold"),
            text_color=badge_color
        )
        self.badge_lbl.pack(side=tk.RIGHT)

        # Area Scorrevole per i contenuti
        scroll_area = ctk.CTkScrollableFrame(container, corner_radius=12, fg_color="transparent")
        scroll_area.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # CARD 1: Modello Attivo e Barre di Progresso
        card1 = ctk.CTkFrame(scroll_area, corner_radius=12, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")
        card1.pack(fill=tk.X, pady=(0, 10))

        ctk.CTkLabel(
            card1,
            text="METRICHE LIVE & QUOTA GIORNALIERA",
            font=("Segoe UI", 10, "bold"),
            text_color="#64748b"
        ).pack(anchor=tk.W, padx=14, pady=(10, 4))

        m_info_box = ctk.CTkFrame(card1, fg_color="#f1f5f9", corner_radius=8)
        m_info_box.pack(fill=tk.X, padx=14, pady=(0, 10))

        p_disp = status_info.get("provider_label", str(self.provider)).upper()
        ctk.CTkLabel(
            m_info_box,
            text=f"Provider: {p_disp}  │  Modello: {self.model}",
            font=("Segoe UI", 11, "bold"),
            text_color="#0f172a"
        ).pack(anchor=tk.W, padx=10, pady=(6, 2))

        ctk.CTkLabel(
            m_info_box,
            text=f"Nome Ufficiale: {status_info['model_name']}",
            font=("Segoe UI", 10),
            text_color="#475569"
        ).pack(anchor=tk.W, padx=10, pady=(0, 6))

        # Barra Richieste Giornaliere (RPD)
        rpd_box = ctk.CTkFrame(card1, fg_color="transparent")
        rpd_box.pack(fill=tk.X, padx=14, pady=(0, 8))

        rpd_top = ctk.CTkFrame(rpd_box, fg_color="transparent")
        rpd_top.pack(fill=tk.X)
        ctk.CTkLabel(
            rpd_top,
            text="Richieste Oggi (RPD):",
            font=("Segoe UI", 11, "bold"),
            text_color="#0f172a"
        ).pack(side=tk.LEFT)

        rpd_val_txt = f"{status_info['requests_today']} / {status_info['limit_rpd']}  ({status_info['percent_remaining']}% residue)"
        if self.provider == "openrouter":
            rpd_val_txt = f"{status_info['requests_today']} schede oggi (A Consumo / Crediti)"
        elif self.provider == "efficiency":
            if status_info.get("active_subprovider") == "openrouter":
                rpd_val_txt = f"{status_info['requests_today']} schede oggi (OpenRouter attivo a consumo)"
            else:
                rpd_val_txt = f"{status_info['requests_today']} / {status_info['limit_rpd']}  ({status_info['percent_remaining']}% residue Gemini Free)"
        self.rpd_lbl = ctk.CTkLabel(
            rpd_top,
            text=rpd_val_txt,
            font=("Segoe UI", 11, "bold"),
            text_color=badge_color
        )
        self.rpd_lbl.pack(side=tk.RIGHT)

        rpd_fraction = min(1.0, status_info["percent_used"] / 100.0) if self.provider != "openrouter" else 0.05
        self.rpd_progress = ctk.CTkProgressBar(
            rpd_box,
            height=12,
            corner_radius=6,
            progress_color=badge_color,
            fg_color="#e2e8f0"
        )
        self.rpd_progress.set(rpd_fraction)
        self.rpd_progress.pack(fill=tk.X, pady=(4, 2))

        # Barra Frequenza per Minuto (RPM)
        rpm_box = ctk.CTkFrame(card1, fg_color="transparent")
        rpm_box.pack(fill=tk.X, padx=14, pady=(0, 8))

        rpm_top = ctk.CTkFrame(rpm_box, fg_color="transparent")
        rpm_top.pack(fill=tk.X)
        ctk.CTkLabel(
            rpm_top,
            text="Frequenza Ultimo Minuto (RPM):",
            font=("Segoe UI", 11, "bold"),
            text_color="#0f172a"
        ).pack(side=tk.LEFT)

        rpm_val_txt = f"{status_info['rpm_current']} / {status_info['limit_rpm']} RPM"
        self.rpm_lbl = ctk.CTkLabel(
            rpm_top,
            text=rpm_val_txt,
            font=("Segoe UI", 11),
            text_color="#0f172a"
        )
        self.rpm_lbl.pack(side=tk.RIGHT)

        rpm_fraction = min(1.0, status_info["rpm_current"] / max(1, status_info["limit_rpm"]))
        self.rpm_progress = ctk.CTkProgressBar(
            rpm_box,
            height=8,
            corner_radius=4,
            progress_color="#0284c7",
            fg_color="#e2e8f0"
        )
        self.rpm_progress.set(rpm_fraction)
        self.rpm_progress.pack(fill=tk.X, pady=(4, 2))

        # Statistiche Token e Reset
        stats_sub = ctk.CTkFrame(card1, fg_color="#f8fafc", corner_radius=8, border_width=1, border_color="#e2e8f0")
        stats_sub.pack(fill=tk.X, padx=14, pady=(2, 12))

        ctk.CTkLabel(
            stats_sub,
            text=f"🪙  Token consumati oggi: {status_info['tokens_today']:,}".replace(",", "."),
            font=("Segoe UI", 10),
            text_color="#334155"
        ).pack(anchor=tk.W, padx=10, pady=(6, 2))

        ctk.CTkLabel(
            stats_sub,
            text=f"🕒  Prossimo reset giornaliero: {status_info['time_until_reset']}",
            font=("Segoe UI", 10, "bold"),
            text_color="#0369a1"
        ).pack(anchor=tk.W, padx=10, pady=(0, 6))

        # CARD 2: Sistema di Fallback Automatico
        card2 = ctk.CTkFrame(scroll_area, corner_radius=12, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")
        card2.pack(fill=tk.X, pady=(0, 10))

        ctk.CTkLabel(
            card2,
            text="SISTEMA DI SICUREZZA & FALLBACK AUTOMATICO",
            font=("Segoe UI", 10, "bold"),
            text_color="#64748b"
        ).pack(anchor=tk.W, padx=14, pady=(10, 4))

        fb_status_txt = "🟢 ATTIVO — Nessun blocco durante il lavoro" if self.enable_fallback else "⚪ DISATTIVATO"
        ctk.CTkLabel(
            card2,
            text=f"Stato Protezione: {fb_status_txt}",
            font=("Segoe UI", 11, "bold"),
            text_color="#0f172a"
        ).pack(anchor=tk.W, padx=14, pady=(0, 4))

        p_str = str(self.provider).lower()
        active_lbl = "Gemini 2" if "2" in p_str else ("OpenRouter" if "openrouter" in p_str else "Gemini 1")
        backup_lbl = "Gemini 1" if "2" in p_str else "Gemini 2"

        if "efficien" in p_str:
            chain_desc = (
                "⚡ 1° Livello: Gemini 1 (Gratuito — 20 schede/giorno con 3.8 Flash, 500 con Flash-Lite)\n"
                "⚡ 2° Livello: Gemini 2 (Raddoppia le richieste gratuite se presente)\n"
                "⚡ 3° Livello: OpenRouter a Consumo (Subentro automatico non appena Gemini termina la quota!)\n\n"
                "In modalità Efficienza ottieni il massimo risparmio: 0 spesa finché hai richieste gratuite su Gemini; "
                "quando le richieste gratuite giornaliere finiscono, le schede successive vengono elaborate "
                "immediatamente tramite OpenRouter senza alcuna interruzione."
            )
        elif "openrouter" in p_str:
            chain_desc = (
                "1° Livello: OpenRouter (A Consumo su Credito - Nessun limite RPD giornaliero)\n\n"
                "In questa modalità le richieste scalano frazioni minime di centesimo dal tuo saldo OpenRouter ($9.79).\n"
                "Se abilitato il failover, subentra la seconda chiave OpenRouter o il fallback Gemini."
            )
        else:
            chain_desc = (
                f"1° Livello: {active_lbl} ({self.model} — Fino a 500 schede/giorno)\n"
                f"2° Livello: Failover su {backup_lbl} (STESSO modello {self.model} — Altre 500 schede/giorno)\n"
                f"3° Livello: Fallback OpenRouter Free (Schede residue illimitate)\n\n"
                f"Se la quota di {active_lbl} si esaurisce (errore 429), l'app passa automaticamente "
                f"a {backup_lbl} mantenendo identica qualità e velocità.\n"
                "Se anche la seconda chiave è esaurita, subentra OpenRouter senza bloccare il lavoro."
            )

        ctk.CTkLabel(
            card2,
            text=chain_desc,
            font=("Segoe UI", 10),
            text_color="#475569",
            justify=tk.LEFT
        ).pack(anchor=tk.W, padx=14, pady=(0, 12))

        # CARD 3: Tabella Limiti Ufficiali Free Tier
        card3 = ctk.CTkFrame(scroll_area, corner_radius=12, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")
        card3.pack(fill=tk.X, pady=(0, 10))

        ctk.CTkLabel(
            card3,
            text="RIEPILOGO LIMITI UFFICIALI (FREE TIER)",
            font=("Segoe UI", 10, "bold"),
            text_color="#64748b"
        ).pack(anchor=tk.W, padx=14, pady=(10, 4))

        limits_text = (
            "• Gemini 3.5 Flash Lite: 500 richieste/giorno gratuite per account │ 15 al minuto (1.000 con 2 account)\n"
            "• Gemini 3.8 Flash: 20 richieste/giorno gratuite per account │ 5 al minuto\n"
            "• OpenRouter (A Consumo): Illimitate richieste/giorno (scalate da saldo prepagato $9.79)"
        )
        ctk.CTkLabel(
            card3,
            text=limits_text,
            font=("Segoe UI", 10),
            text_color="#334155",
            justify=tk.LEFT
        ).pack(anchor=tk.W, padx=14, pady=(0, 12))

        # CARD 4: Storico Ultime Chiamate
        card4 = ctk.CTkFrame(scroll_area, corner_radius=12, fg_color="#ffffff", border_width=1, border_color="#e2e8f0")
        card4.pack(fill=tk.X, pady=(0, 6))

        ctk.CTkLabel(
            card4,
            text="CRONOLOGIA ULTIME CHIAMATE",
            font=("Segoe UI", 10, "bold"),
            text_color="#64748b"
        ).pack(anchor=tk.W, padx=14, pady=(10, 4))

        calls = status_info.get("recent_calls", [])
        if not calls:
            ctk.CTkLabel(
                card4,
                text="Nessuna chiamata registrata nella sessione corrente.",
                font=("Segoe UI", 10, "italic"),
                text_color="#94a3b8"
            ).pack(anchor=tk.W, padx=14, pady=(0, 12))
        else:
            hist_box = ctk.CTkFrame(card4, fg_color="#f8fafc", corner_radius=8)
            hist_box.pack(fill=tk.X, padx=14, pady=(0, 12))
            for call in calls[:6]:
                status_icon = "🟢" if call["status_code"] == 200 else "🔴"
                fb_tag = " [FALLBACK]" if call.get("is_fallback") else ""
                txt = f"{status_icon} [{call['timestamp']}] {call['model']}{fb_tag} • {call['status_code']} • {call['duration_sec']}s • {call['tokens']} tk"
                ctk.CTkLabel(
                    hist_box,
                    text=txt,
                    font=("Segoe UI", 9),
                    text_color="#334155"
                ).pack(anchor=tk.W, padx=8, pady=2)

        # Barra Inferiore con Pulsanti di Azione
        btn_bar = ctk.CTkFrame(container, fg_color="transparent")
        btn_bar.pack(fill=tk.X, pady=(6, 0))

        studio_btn = ctk.CTkButton(
            btn_bar,
            text="🌐 Apri Google AI Studio",
            font=("Segoe UI", 11, "bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            corner_radius=8,
            height=34,
            command=self._open_ai_studio
        )
        studio_btn.pack(side=tk.LEFT, padx=(0, 8))

        reset_btn = ctk.CTkButton(
            btn_bar,
            text="🔄 Azzera Conteggio Locale",
            font=("Segoe UI", 11),
            fg_color="#475569",
            hover_color="#334155",
            corner_radius=8,
            height=34,
            command=self._on_reset_stats
        )
        reset_btn.pack(side=tk.LEFT)

        close_btn = ctk.CTkButton(
            btn_bar,
            text="Chiudi",
            font=("Segoe UI", 11, "bold"),
            fg_color="#e2e8f0",
            text_color="#0f172a",
            hover_color="#cbd5e1",
            corner_radius=8,
            width=90,
            height=34,
            command=self.destroy
        )
        close_btn.pack(side=tk.RIGHT)

    def _open_ai_studio(self):
        try:
            webbrowser.open("https://aistudio.google.com/rate-limit")
        except Exception as e:
            messagebox.showerror("Errore Browser", f"Impossibile aprire il browser: {e}", parent=self)

    def _on_reset_stats(self):
        if messagebox.askyesno("Conferma Reset", "Vuoi azzerare i contatori delle statistiche locali?", parent=self):
            self.tracker.reset_stats()
            if self.on_reset_callback:
                self.on_reset_callback()
            self.destroy()



from .components_dialogs import (  # noqa: E402,F401  (re-export componenti dialogs)
    WelcomeSetupDialogV3,
    SettingsDialogV3,
    BatchStripV3,
    ScanProgressDialogV3,
    ContinueScanDialogV3,
)
