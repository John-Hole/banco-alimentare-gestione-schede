"""Design system, palette colori e stili grafici moderni per la Versione 2 (v2)."""

import tkinter as tk
from tkinter import ttk

# --- DESIGN TOKENS ---

# Palette Colori
COLOR_BG = "#f1f5f9"           # Slate-100 (sfondo principale riposante)
COLOR_CARD = "#ffffff"         # Bianco puro per card e container
COLOR_BORDER = "#cbd5e1"       # Slate-300 (bordi sottili e precisi)
COLOR_BORDER_LIGHT = "#e2e8f0" # Slate-200 (divisori leggeri)

# Colori Brand e Header
COLOR_HEADER_BG = "#0f172a"    # Slate-900 (header scuro professionale ad alto contrasto)
COLOR_HEADER_FG = "#f8fafc"    # Slate-50
COLOR_BRAND_ACCENT = "#38bdf8" # Sky-400 (dettagli e accenti)

# Colori di Azione e Stato (Massima visibilità)
COLOR_SUCCESS = "#16a34a"      # Verde smeraldo (OK / Scrittura confermata)
COLOR_SUCCESS_HOVER = "#15803d"# Verde scuro hover
COLOR_DANGER = "#dc2626"       # Rosso vivido (Flag rosso / Discrepanza)
COLOR_DANGER_LIGHT = "#fef2f2" # Rosso chiarissimo per sfondo alert
COLOR_WARNING = "#d97706"      # Ambra / Arancio (Avvisi)
COLOR_INFO = "#0284c7"         # Azzurro / Primario secondario
COLOR_INFO_HOVER = "#0369a1"

# Tipografia
COLOR_TEXT = "#0f172a"         # Slate-900 (testo principale ad alto contrasto)
COLOR_TEXT_MUTED = "#64748b"   # Slate-500 (testo secondario/note)
COLOR_TEXT_LIGHT = "#94a3b8"   # Slate-400

# Font Families
FONT_MAIN = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 12, "bold")
FONT_SUBTITLE = ("Segoe UI", 10, "bold")
FONT_BIG_BTN = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 9)
FONT_BADGE = ("Segoe UI", 8, "bold")


def apply_modern_theme(root: tk.Tk) -> ttk.Style:
    """Configura il tema TTK e i widget nativi per un look moderno e pulito."""
    style = ttk.Style(root)
    style.theme_use("clam")

    # Sfondo generale finestra
    root.configure(bg=COLOR_BG)

    # Stili Frame di base e Card
    style.configure("TFrame", background=COLOR_BG)
    style.configure("Card.TFrame", background=COLOR_CARD, relief="solid", borderwidth=1, bordercolor=COLOR_BORDER)
    style.configure("FlatCard.TFrame", background=COLOR_CARD)
    style.configure("Header.TFrame", background=COLOR_HEADER_BG)

    # Label generiche e Card Label
    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT, font=FONT_MAIN)
    style.configure("Card.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT, font=FONT_MAIN)
    style.configure("CardMuted.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT_MUTED, font=("Segoe UI", 8))
    style.configure("CardTitle.TLabel", background=COLOR_CARD, foreground=COLOR_HEADER_BG, font=FONT_SUBTITLE)
    style.configure("Header.TLabel", background=COLOR_HEADER_BG, foreground=COLOR_HEADER_FG, font=FONT_TITLE)

    # Bottoni Primari (Scrivi su Excel)
    style.configure(
        "Primary.TButton",
        background=COLOR_SUCCESS,
        foreground="#ffffff",
        font=FONT_BIG_BTN,
        borderwidth=0,
        focusthickness=0,
        padding=(14, 8)
    )
    style.map(
        "Primary.TButton",
        background=[("active", COLOR_SUCCESS_HOVER), ("disabled", "#cbd5e1")],
        foreground=[("disabled", "#94a3b8")]
    )

    # Bottoni Azione (Scanner, Carica, Strumenti)
    style.configure(
        "Action.TButton",
        background=COLOR_INFO,
        foreground="#ffffff",
        font=FONT_BOLD,
        borderwidth=0,
        focusthickness=0,
        padding=(10, 5)
    )
    style.map(
        "Action.TButton",
        background=[("active", COLOR_INFO_HOVER), ("disabled", "#cbd5e1")]
    )

    # Bottoni Neutri / Secondari
    style.configure(
        "Secondary.TButton",
        background="#e2e8f0",
        foreground=COLOR_TEXT,
        font=FONT_BOLD,
        borderwidth=0,
        focusthickness=0,
        padding=(10, 6)
    )
    style.map(
        "Secondary.TButton",
        background=[("active", "#cbd5e1")]
    )

    # Bottoni Tool Piccoli (Ruota, Contrasto)
    style.configure(
        "Tool.TButton",
        background="#f8fafc",
        foreground=COLOR_TEXT,
        font=("Segoe UI", 8, "bold"),
        borderwidth=1,
        bordercolor=COLOR_BORDER,
        focusthickness=0,
        padding=(6, 3)
    )
    style.map(
        "Tool.TButton",
        background=[("active", "#e2e8f0")]
    )

    # Treeview Moderna
    style.configure(
        "Treeview",
        background="#ffffff",
        foreground=COLOR_TEXT,
        fieldbackground="#ffffff",
        rowheight=26,
        font=FONT_MAIN,
        borderwidth=0
    )
    style.configure(
        "Treeview.Heading",
        background="#f8fafc",
        foreground=COLOR_TEXT,
        font=FONT_BOLD,
        borderwidth=1,
        relief="flat",
        padding=(6, 4)
    )
    style.map(
        "Treeview",
        background=[("selected", "#e0f2fe")],
        foreground=[("selected", "#0369a1")]
    )

    # Combobox Moderna
    style.configure(
        "TCombobox",
        padding=4,
        font=FONT_MAIN
    )

    # Checkbutton
    style.configure(
        "Card.TCheckbutton",
        background=COLOR_CARD,
        foreground=COLOR_DANGER,
        font=FONT_BOLD
    )

    return style
