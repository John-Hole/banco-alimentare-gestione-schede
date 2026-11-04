"""Dialog di visualizzazione del log di sessione per Banco Alimentare v3."""

import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from ..services.session_logger import get_session_logger


class LogViewerDialogV3(ctk.CTkToplevel):
    """Finestra interattiva per visualizzare, consultare e aprire il log della sessione corrente."""

    def __init__(self, parent: ctk.CTk):
        super().__init__(parent)
        self.session_logger = get_session_logger()
        self.log_path = self.session_logger.get_current_log_path()

        self.title("Banco Alimentare — Log di Sessione")
        self.geometry("980x640")
        self.minsize(700, 450)
        self.configure(fg_color="#f8fafc")

        # Rendi modale/transient
        self.transient(parent)
        self.after(100, lambda: self.focus_force())

        self._build_ui()
        self._load_log_content()

        # Scorciatoie da tastiera
        self.bind("<F5>", lambda e: self._on_refresh())
        self.bind("<Escape>", lambda e: self.destroy())

    def _build_ui(self):
        # 1. Header superiore
        header = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=0, height=54)
        header.pack(fill=tk.X, side=tk.TOP)
        ctk.CTkFrame(self, height=1, fg_color="#e2e8f0").pack(fill=tk.X, side=tk.TOP)

        header_content = ctk.CTkFrame(header, fg_color="transparent")
        header_content.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)

        title_box = ctk.CTkFrame(header_content, fg_color="transparent")
        title_box.pack(side=tk.LEFT)

        ctk.CTkLabel(
            title_box,
            text="📜 Log della Sessione Corrente",
            font=("Segoe UI", 14, "bold"),
            text_color="#0f172a"
        ).pack(anchor="w")

        file_size_kb = (self.log_path.stat().st_size / 1024) if self.log_path.exists() else 0.0
        self.subtitle_lbl = ctk.CTkLabel(
            title_box,
            text=f"File: {self.log_path.name}  ({file_size_kb:.1f} KB) — {str(self.log_path)}",
            font=("Segoe UI", 9),
            text_color="#64748b"
        )
        self.subtitle_lbl.pack(anchor="w")

        # Pulsanti rapidi a destra nell'header
        top_btn_box = ctk.CTkFrame(header_content, fg_color="transparent")
        top_btn_box.pack(side=tk.RIGHT)

        ctk.CTkButton(
            top_btn_box,
            text="🔄 Aggiorna (F5)",
            font=("Segoe UI", 10, "bold"),
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color="#0f172a",
            border_width=1,
            border_color="#cbd5e1",
            corner_radius=6,
            height=28,
            width=110,
            command=self._on_refresh
        ).pack(side=tk.LEFT, padx=(0, 6))

        ctk.CTkButton(
            top_btn_box,
            text="📝 Apri in Blocco Note",
            font=("Segoe UI", 10, "bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            corner_radius=6,
            height=28,
            width=140,
            command=self._on_open_notepad
        ).pack(side=tk.LEFT, padx=(0, 6))

        ctk.CTkButton(
            top_btn_box,
            text="📂 Cartella Log",
            font=("Segoe UI", 10, "bold"),
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color="#0f172a",
            border_width=1,
            border_color="#cbd5e1",
            corner_radius=6,
            height=28,
            width=110,
            command=self._on_open_folder
        ).pack(side=tk.LEFT)

        # 2. Area Centrale di Testo con Monospace Font
        body_frame = ctk.CTkFrame(self, fg_color="transparent")
        body_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        self.textbox = ctk.CTkTextbox(
            body_frame,
            font=("Consolas", 10),
            fg_color="#0f172a",
            text_color="#f8fafc",
            wrap="none",
            corner_radius=8,
            border_width=1,
            border_color="#334155"
        )
        self.textbox.pack(fill=tk.BOTH, expand=True)

        # 3. Footer inferiore
        footer = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=0, height=44)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        ctk.CTkFrame(self, height=1, fg_color="#e2e8f0").pack(fill=tk.X, side=tk.BOTTOM)

        footer_content = ctk.CTkFrame(footer, fg_color="transparent")
        footer_content.pack(fill=tk.BOTH, expand=True, padx=16, pady=6)

        self.status_lbl = ctk.CTkLabel(
            footer_content,
            text="Tutti i log della sessione corrente registrati in tempo reale.",
            font=("Segoe UI", 9),
            text_color="#64748b"
        )
        self.status_lbl.pack(side=tk.LEFT)

        ctk.CTkButton(
            footer_content,
            text="Chiudi",
            font=("Segoe UI", 10),
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color="#0f172a",
            border_width=1,
            border_color="#cbd5e1",
            corner_radius=6,
            width=80,
            height=26,
            command=self.destroy
        ).pack(side=tk.RIGHT)

        ctk.CTkButton(
            footer_content,
            text="📋 Copia negli Appunti",
            font=("Segoe UI", 10),
            fg_color="#f1f5f9",
            hover_color="#e2e8f0",
            text_color="#0f172a",
            border_width=1,
            border_color="#cbd5e1",
            corner_radius=6,
            width=140,
            height=26,
            command=self._on_copy_clipboard
        ).pack(side=tk.RIGHT, padx=(0, 8))

    def _load_log_content(self):
        """Legge il log dal disco e aggiorna la casella di testo scorrendo in fondo."""
        content = self.session_logger.read_current_log()
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", tk.END)
        self.textbox.insert(tk.END, content)
        self.textbox.see(tk.END)
        self.textbox.configure(state="disabled")

        if self.log_path.exists():
            kb = self.log_path.stat().st_size / 1024
            self.subtitle_lbl.configure(
                text=f"File: {self.log_path.name}  ({kb:.1f} KB) — {str(self.log_path)}"
            )

    def _on_refresh(self):
        self._load_log_content()
        self.status_lbl.configure(text="✅ Log aggiornato.")
        self.after(2000, lambda: self.status_lbl.configure(
            text="Tutti i log della sessione corrente registrati in tempo reale."
        ))

    def _on_open_notepad(self):
        ok = self.session_logger.open_in_notepad()
        if not ok:
            messagebox.showwarning("Avviso", "Impossibile aprire il file con l'editor predefinito.", parent=self)

    def _on_open_folder(self):
        ok = self.session_logger.open_logs_directory()
        if not ok:
            messagebox.showwarning("Avviso", "Impossibile aprire la cartella logs.", parent=self)

    def _on_copy_clipboard(self):
        try:
            content = self.session_logger.read_current_log()
            self.clipboard_clear()
            self.clipboard_append(content)
            self.status_lbl.configure(text="✅ Log copiato negli appunti!")
            self.after(2500, lambda: self.status_lbl.configure(
                text="Tutti i log della sessione corrente registrati in tempo reale."
            ))
        except Exception as e:
            messagebox.showerror("Errore", f"Impossibile copiare negli appunti: {e}", parent=self)
