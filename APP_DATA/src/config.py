"""Modulo di configurazione e costanti dell'applicazione."""

import os
import json
from pathlib import Path
from typing import Dict, List, Any

APP_DATA_DIR = Path(__file__).resolve().parent.parent
BASE_DIR = APP_DATA_DIR
ROOT_DIR = APP_DATA_DIR.parent if APP_DATA_DIR.name.upper() == "APP_DATA" else APP_DATA_DIR

BACKUP_DIR = ROOT_DIR / "BACKUP"
BACKUP_DIR.mkdir(exist_ok=True)
LOGS_DIR = APP_DATA_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
STORICO_DIR = ROOT_DIR / "SCANSIONI"
STORICO_DIR.mkdir(exist_ok=True)
SUGGERIMENTI_DIR = ROOT_DIR / "SUGGERIMENTI"

CONFIG_FILE = APP_DATA_DIR / "config.json"

# File gestionale di default se non specificato
DEFAULT_EXCEL_FILENAME = "gestionale_banco_alimentare_MASTER.xlsx"
DEFAULT_EXCEL_PATH = APP_DATA_DIR / DEFAULT_EXCEL_FILENAME

# 13 Categorie Canoniche con rispettive colonne Kg e Scatole
# Riferimento: Riga 4 (Nome) e Riga 5 (Kg / Scat.)
CATEGORIES = [
    {"name": "Omogeneizzati", "kg_col": 2, "box_col": 3, "kg_letter": "B", "box_letter": "C"},
    {"name": "Alimenti per bambini", "kg_col": 4, "box_col": 5, "kg_letter": "D", "box_letter": "E"},
    {"name": "Tonno", "kg_col": 6, "box_col": 7, "kg_letter": "F", "box_letter": "G"},
    {"name": "Olio", "kg_col": 8, "box_col": 9, "kg_letter": "H", "box_letter": "I"},
    {"name": "Pelati", "kg_col": 10, "box_col": 11, "kg_letter": "J", "box_letter": "K"},
    {"name": "Legumi", "kg_col": 12, "box_col": 13, "kg_letter": "L", "box_letter": "M"},
    {"name": "Pasta", "kg_col": 14, "box_col": 15, "kg_letter": "N", "box_letter": "O"},
    {"name": "Riso", "kg_col": 16, "box_col": 17, "kg_letter": "P", "box_letter": "Q"},
    {"name": "Zucchero", "kg_col": 18, "box_col": 19, "kg_letter": "R", "box_letter": "S"},
    {"name": "Latte", "kg_col": 20, "box_col": 21, "kg_letter": "T", "box_letter": "U"},
    {"name": "Biscotti", "kg_col": 22, "box_col": 23, "kg_letter": "V", "box_letter": "W"},
    {"name": "Carne in scatola", "kg_col": 24, "box_col": 25, "kg_letter": "X", "box_letter": "Y"},
    {"name": "Varie", "kg_col": 26, "box_col": 27, "kg_letter": "Z", "box_letter": "AA"},
]

CATEGORY_NAMES = [c["name"] for c in CATEGORIES]
CATEGORY_BY_NAME = {c["name"].lower(): c for c in CATEGORIES}

# Alias frequenti per normalizzazione da testo scritto a mano o OCR
CATEGORY_ALIASES: Dict[str, str] = {
    "omogeneizzati": "Omogeneizzati",
    "omogeneizzato": "Omogeneizzati",
    "omog": "Omogeneizzati",
    "omog.": "Omogeneizzati",
    "alimenti per bambini": "Alimenti per bambini",
    "alimenti bambini": "Alimenti per bambini",
    "bambini": "Alimenti per bambini",
    "alim. bambini": "Alimenti per bambini",
    "baby food": "Alimenti per bambini",
    "infanzia": "Alimenti per bambini",
    "alimenti infanzia": "Alimenti per bambini",
    "alimenti infanzia / omogeneizzati": "Alimenti per bambini",
    "tonno": "Tonno",
    "tonno in scatola": "Tonno",
    "olio": "Olio",
    "olio d'oliva": "Olio",
    "pelati": "Pelati",
    "pelati e sughi": "Pelati",
    "pomodori pelati": "Pelati",
    "pomodoro": "Pelati",
    "legumi": "Legumi",
    "fagioli": "Legumi",
    "ceci": "Legumi",
    "lenticchie": "Legumi",
    "pasta": "Pasta",
    "riso": "Riso",
    "zucchero": "Zucchero",
    "latte": "Latte",
    "biscotti": "Biscotti",
    "carne in scatola": "Carne in scatola",
    "carne": "Carne in scatola",
    "carne scatola": "Carne in scatola",
    "simmenthal": "Carne in scatola",
    "varie": "Varie",
    "altro": "Varie",
}

# Mappatura righe Trasporto (da A1 a A10)
TRANSPORT_ROWS: Dict[str, int] = {
    "A1": 6,
    "A2": 7,
    "A3": 8,
    "A4": 9,
    "A5": 10,
    "A6": 11,
    "A7": 12,
    "A8": 13,
    "A9": 14,
    "A10": 15,
}

ROW_TO_TRANSPORT = {v: k for k, v in TRANSPORT_ROWS.items()}

# Righe protette con formule o intestazioni
PROTECTED_ROWS = {1, 2, 3, 4, 5, 16, 18, 19, 21}
FORMULA_ROWS = {16, 18, 19}

def normalize_category_name(name: str) -> str:
    """Normalizza il nome di una categoria verso uno dei 13 nomi canonici."""
    if not name:
        return "Varie"
    cleaned = name.strip().lower()
    if cleaned in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[cleaned]
    for alias, canonical in CATEGORY_ALIASES.items():
        if alias in cleaned or cleaned in alias:
            return canonical
    return "Varie"


# Cartella dati Colletta di default (usata solo se presente accanto all'applicazione)
LEGACY_COLLETTA_DIR = ROOT_DIR / "FOGLI_COLLETTA"

# Modelli Gemini selezionabili dalle Impostazioni
SUPPORTED_GEMINI_MODELS: List[str] = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

# Modelli OpenRouter selezionabili dalle Impostazioni
SUPPORTED_OPENROUTER_MODELS: List[str] = [
    "meta/muse-ember",
    "anthropic/claude-haiku-5.5",
    "anthropic/claude-sonnet-5.5",
    "nvidia/nemotron-3.5-super-vl:free",
]
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-5.5"

# Modelli specifici dell'Architettura a Cascata Personalizzabile Banco Alimentare
DEFAULT_CASCADE_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_CASCADE_V2_PRIMARY_MODEL = "gemini-3.8-flash"
DEFAULT_CASCADE_EMBER_MODEL = "meta/muse-ember"
DEFAULT_CASCADE_LUNA_MODEL = DEFAULT_CASCADE_EMBER_MODEL  # Alias retrocompatibilità per codice/test legacy
DEFAULT_CASCADE_HAIKU_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_CASCADE_BUFFER_MODEL = DEFAULT_CASCADE_EMBER_MODEL
DEFAULT_CASCADE_FREE_MODEL = "nvidia/nemotron-3.5-super-vl:free"
DEFAULT_CASCADE_ARBITRATOR_MODEL = "anthropic/claude-haiku-5.5"

# Opzioni delle 3 Sezioni della Cascata Personalizzabile
CASCADE_FREE_MODES: Dict[str, str] = {
    "gemini_35": "🆓 Solo Gemini 3.5 Flash-Lite (Gratis · 2 Chiavi)",
    "none": "⚪ Disattivato (Nessun modello Google gratuito)",
}
CASCADE_FREE_INV_MAP: Dict[str, str] = {v: k for k, v in CASCADE_FREE_MODES.items()}
DEFAULT_CASCADE_FREE_PRIMARY_MODE = "gemini_35"

CASCADE_PAID_MODES: Dict[str, str] = {
    "ember": "🔥 Meta Muse Ember Minimal (~$0.0010 — Default)",
    "haiku": "🦅 Claude Haiku 5.5 Low (~$0.0028)",
    "ember_then_haiku": "🔥➔🦅 Prima Muse Ember Minimal, poi Haiku 5.5",
    "haiku_then_ember": "🦅➔🔥 Prima Haiku 5.5, poi Muse Ember Minimal",
    "none": "⚪ Disattivato (Nessun modello a pagamento)",
}
CASCADE_PAID_INV_MAP: Dict[str, str] = {v: k for k, v in CASCADE_PAID_MODES.items()}
DEFAULT_CASCADE_PAID_MODE = "ember"

CASCADE_FALLBACK_MODES: Dict[str, str] = {
    "nemotron_free": "🆓 Nemotron 3.5 Super-VL Free ($0.00 — Default)",
    "none": "⚪ Disattivato",
}
CASCADE_FALLBACK_INV_MAP: Dict[str, str] = {v: k for k, v in CASCADE_FALLBACK_MODES.items()}
DEFAULT_CASCADE_FALLBACK_MODE = "nemotron_free"

# Modelli selezionabili per l'Arbitro Matematico (controllo discrepanze quadrature)
SUPPORTED_ARBITRATOR_MODELS: List[str] = [
    "anthropic/claude-haiku-5.5",
    "meta/muse-ember:minimal",
    "meta/muse-ember:low",
    "anthropic/claude-sonnet-5.5",
    "gemini-3.5-flash-lite",
    "nvidia/nemotron-3.5-super-vl:free",
]

ARBITRATOR_DISPLAY_MAP: Dict[str, str] = {
    "anthropic/claude-haiku-5.5": "🦅 Claude Haiku 5.5 (Low Reasoning) (~$0.0028 — Consigliato)",
    "meta/muse-ember:minimal": "🔥 Meta Muse Ember (Minimal Reasoning) (~$0.0010)",
    "meta/muse-ember:low": "🔥 Meta Muse Ember (Low Reasoning) (~$0.0014)",
    "anthropic/claude-sonnet-5.5": "🧠 Claude Sonnet 5.5 (~$0.0350 — Massima Precisione)",
    "gemini-3.5-flash-lite": "🆓 Gemini 3.5 Flash-Lite (Google Diretto — Gratis)",
    "nvidia/nemotron-3.5-super-vl:free": "🆓 Nemotron 3.5 Super-VL (OpenRouter Free — Gratis)",
}
ARBITRATOR_INV_MAP: Dict[str, str] = {v: k for k, v in ARBITRATOR_DISPLAY_MAP.items()}

# Provider selezionabili (Menu a tendina ordinato)
SUPPORTED_PROVIDERS: List[str] = [
    "cascade",
    "gemini_35",
    "gemini_38",
    "or_muse_minimal",
    "or_muse_low",
    "or_haiku_low",
    "or_sonnet",
    "or_nemotron",
]
DEFAULT_PROVIDER = "cascade"

# Opzioni scanner di default
SCANNER_DPI_CHOICES: List[int] = [150, 200, 300, 400, 600]
DEFAULT_SCANNER_OPTIONS: Dict[str, Any] = {
    "scanner_default_source": "adf",   # 'adf' (Alimentatore Automatico) | 'flatbed' (Piano Vetro)
    "scanner_dpi": 200,
    "scanner_auto_optimize": False,    # Disattivato: nessun aumento gamma/luminosità nascosto
    "auto_preset_assignment": False,   # Default False: usa P0 (standard senza modifiche); True: auto-detect P1..P5
    "image_brightness": 1.0,           # Luminosità taratura (0.50 - 1.50, default 1.0 = 0%)
    "image_contrast": 1.0,             # Contrasto taratura (0.50 - 3.00, default 1.0)
    "image_sharpness": 1.0,            # Nitidezza taratura (0.50 - 3.00, default 1.0)
}


def default_colletta_folder() -> str:
    """Cartella dati di default: 'FOGLI_COLLETTA' se presente nella radice del progetto, altrimenti vuota."""
    if LEGACY_COLLETTA_DIR.is_dir():
        return str(LEGACY_COLLETTA_DIR)
    alt_dir = ROOT_DIR / "fogli_colletta_2025"
    if alt_dir.is_dir():
        return str(alt_dir)
    return ""


def has_valid_api_key(cfg: Dict[str, Any]) -> bool:
    """True se in configurazione è presente almeno una chiave primaria valida (OpenRouter o Gemini)."""
    return bool(str(cfg.get("openrouter_api_key") or "").strip()) or bool(str(cfg.get("gemini_api_key") or "").strip()) or bool(str(cfg.get("gemini_api_key_2") or "").strip())


# Scorciatoie da tastiera predefinite e configurabili
DEFAULT_SHORTCUTS: Dict[str, str] = {
    "scan": "F2",
    "scan_flatbed": "<Shift-F2>",
    "upload_image": "<Control-o>",
    "write_excel": "<Control-Return>",
    "write_excel_alt": "<Alt-Return>",
    "edit_mode": "<Shift-Return>",
    "confirm_row": "<Return>",
    "toggle_override": "<Tab>",
    "reset_session": "F5",
    "settings": "<Control-p>",
}

SHORTCUT_LABELS: Dict[str, str] = {
    "scan": "Scansione ADF (Alimentatore)",
    "scan_flatbed": "Scansione Piano Vetro",
    "upload_image": "Carica Immagini / PDF",
    "write_excel": "Salva su Excel (Principale)",
    "write_excel_alt": "Salva su Excel (Alternativo)",
    "edit_mode": "Modifica Rapida Cella",
    "confirm_row": "Conferma Riga / Categoria",
    "toggle_override": "Spunta Autorizzazione",
    "reset_session": "Azzera Sessione",
    "settings": "Apri Impostazioni",
}

SHORTCUT_DESCRIPTIONS: Dict[str, str] = {
    "scan": "Scansione rapida multi-foglio da caricatore ADF",
    "scan_flatbed": "Scansione foglio singolo da piano vetro",
    "upload_image": "Carica immagini o PDF dal computer",
    "write_excel": "Scrive la scheda corrente su Excel e salva il file",
    "write_excel_alt": "Scorciatoia alternativa per il salvataggio su Excel",
    "edit_mode": "Attiva modifica inline del peso nella cella selezionata",
    "confirm_row": "Conferma la riga selezionata (scatola o categoria madre)",
    "toggle_override": "Attiva o disattiva la spunta di autorizzazione forzata",
    "reset_session": "Pulisce la sessione e azzera tutti i campi",
    "settings": "Apre il pannello delle impostazioni dell'applicazione",
}


def load_app_config() -> Dict[str, Any]:
    """Carica la configurazione da config.json se presente, altrimenti valori di default."""
    defaults = {
        "active_provider": "cascade",
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "gemini_api_key_2": os.getenv("GEMINI_API_KEY_2", ""),
        "gemini_model": DEFAULT_GEMINI_MODEL,
        "enable_auto_fallback": True,
        "fallback_to_openrouter": True,
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        "openrouter_api_key_2": os.getenv("OPENROUTER_API_KEY_2", ""),
        "openrouter_model": DEFAULT_OPENROUTER_MODEL,
        "last_excel_path": str(DEFAULT_EXCEL_PATH) if DEFAULT_EXCEL_PATH.exists() else "",
        "last_colletta_folder": default_colletta_folder(),
        "storico_folder": str(STORICO_DIR),
        "shortcuts": dict(DEFAULT_SHORTCUTS),
        **DEFAULT_SCANNER_OPTIONS,
        "cascade_free_primary_mode": DEFAULT_CASCADE_FREE_PRIMARY_MODE,
        "cascade_paid_mode": DEFAULT_CASCADE_PAID_MODE,
        "cascade_fallback_mode": DEFAULT_CASCADE_FALLBACK_MODE,
        "openrouter_buffer_model": DEFAULT_CASCADE_BUFFER_MODEL,
        "openrouter_free_model": DEFAULT_CASCADE_FREE_MODEL,
        "openrouter_arbitrator_model": DEFAULT_CASCADE_ARBITRATOR_MODEL,
        "cascade_arbitrator_model": DEFAULT_CASCADE_ARBITRATOR_MODEL,
        "enable_auto_arbitrator": True,
        "enable_arbitrator_on_free": True,
        "enable_arbitrator_on_paid": False,
        "trello_api_key": os.getenv("TRELLO_API_KEY", ""),
        "trello_token": os.getenv("TRELLO_TOKEN", ""),
        "trello_list_id": os.getenv("TRELLO_LIST_ID", ""),
        "swap_panels": False,
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "shortcuts" in data and isinstance(data["shortcuts"], dict):
                    merged_sc = dict(DEFAULT_SHORTCUTS)
                    merged_sc.update(data["shortcuts"])
                    data["shortcuts"] = merged_sc
                for path_key, fallback_val in (
                    ("last_excel_path", defaults["last_excel_path"]),
                    ("last_colletta_folder", defaults["last_colletta_folder"]),
                    ("storico_folder", defaults["storico_folder"]),
                ):
                    val = str(data.get(path_key) or "").strip()
                    if not val or not Path(val).exists():
                        data[path_key] = fallback_val
                if "enable_arbitrator_on_free" not in data and "enable_auto_arbitrator" in data:
                    data["enable_arbitrator_on_free"] = bool(data["enable_auto_arbitrator"])

                # Normalizzazione retrocompatibile per migrazione da GPT-6 Luna a Meta Muse Ember
                if data.get("active_provider") == "or_luna_low":
                    data["active_provider"] = "or_muse_minimal"
                elif data.get("active_provider") in ("or_muse_medium", "or_muse_max"):
                    data["active_provider"] = "or_muse_low"
                elif data.get("active_provider") == "cascade_v2":
                    data["active_provider"] = "cascade"

                paid_m = str(data.get("cascade_paid_mode") or "").strip().lower()
                if paid_m == "luna":
                    data["cascade_paid_mode"] = "ember"
                elif paid_m == "luna_then_haiku":
                    data["cascade_paid_mode"] = "ember_then_haiku"
                elif paid_m == "haiku_then_luna":
                    data["cascade_paid_mode"] = "haiku_then_ember"

                if data.get("cascade_free_primary_mode") in ("gemini_38", "gemini_38_then_35", "gemini_35_then_38"):
                    data["cascade_free_primary_mode"] = "gemini_35"

                if data.get("cascade_arbitrator_model") in ("openai/gpt-6-luna", "gemini-3.8-flash"):
                    data["cascade_arbitrator_model"] = "meta/muse-ember:minimal" if data.get("cascade_arbitrator_model") == "openai/gpt-6-luna" else "gemini-3.5-flash-lite"
                if data.get("openrouter_arbitrator_model") in ("openai/gpt-6-luna", "gemini-3.8-flash"):
                    data["openrouter_arbitrator_model"] = "meta/muse-ember:minimal" if data.get("openrouter_arbitrator_model") == "openai/gpt-6-luna" else "gemini-3.5-flash-lite"

                defaults.update(data)
        except Exception:
            pass
    return defaults


def save_app_config(cfg: Dict[str, Any]) -> None:
    """Salva la configurazione su config.json."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Errore nel salvataggio config.json: {e}")


def get_pipeline_status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Calcola lo stato delle 3 sezioni della cascata personalizzabile e degli arbitri."""
    k1 = bool(str(cfg.get("gemini_api_key") or "").strip())
    k2 = bool(str(cfg.get("gemini_api_key_2") or "").strip())
    or_key = bool(str(cfg.get("openrouter_api_key") or "").strip())

    google_count = (1 if k1 else 0) + (1 if k2 else 0)
    free_mode = cfg.get("cascade_free_primary_mode", DEFAULT_CASCADE_FREE_PRIMARY_MODE)
    paid_mode = cfg.get("cascade_paid_mode", DEFAULT_CASCADE_PAID_MODE)
    fallback_mode = cfg.get("cascade_fallback_mode", DEFAULT_CASCADE_FALLBACK_MODE)
    arb_on_free = bool(cfg.get("enable_arbitrator_on_free", cfg.get("enable_auto_arbitrator", True)))
    arb_on_paid = bool(cfg.get("enable_arbitrator_on_paid", False))
    arb_model = cfg.get("cascade_arbitrator_model") or cfg.get("openrouter_arbitrator_model") or DEFAULT_CASCADE_ARBITRATOR_MODEL

    # Sezione 1: Modelli Google Free
    free_label = CASCADE_FREE_MODES.get(free_mode, free_mode)
    if google_count == 2:
        phase_a_status = "green"
        phase_a_desc = f"2/2 Chiavi Google Attive · 1.000 RPD · 30 RPM (0,00 €) · {free_label}"
        daily_cap = 1000
        rpm_cap = 30
    elif google_count == 1:
        phase_a_status = "amber"
        phase_a_desc = f"1/2 Chiave Google Attiva · 500 RPD · 15 RPM (0,00 €) · {free_label}"
        daily_cap = 500
        rpm_cap = 15
    else:
        phase_a_status = "red"
        phase_a_desc = "Nessuna chiave Google inserita (Sezione 1 non disponibile)"
        daily_cap = 0
        rpm_cap = 0

    # Sezione 2: Modelli a Pagamento OpenRouter
    paid_label = CASCADE_PAID_MODES.get(paid_mode, paid_mode)
    if paid_mode == "none":
        phase_b_status = "amber"
        phase_b_desc = "Disattivato dall'utente (nessuna spesa OpenRouter)"
    elif or_key:
        phase_b_status = "green"
        phase_b_desc = f"Attivo · {paid_label}"
    else:
        phase_b_status = "red"
        phase_b_desc = "Disattivato · Richiede OpenRouter API Key"

    # Sezione 3: Fallback Gratuito OpenRouter
    fb_label = CASCADE_FALLBACK_MODES.get(fallback_mode, fallback_mode)
    if fallback_mode == "none":
        phase_c_status = "amber"
        phase_c_desc = "Disattivato dall'utente"
    elif or_key:
        phase_c_status = "green"
        phase_c_desc = f"Attivo · {fb_label}"
    else:
        phase_c_status = "red"
        phase_c_desc = "Disattivato · Richiede OpenRouter API Key"

    # Arbitro Matematico (2 switch indipendenti: su Free / su Pagamento)
    if not (arb_on_free or arb_on_paid):
        phase_d_status = "amber"
        phase_d_desc = "Arbitro disattivato su Free e su Pagamento (Shift+Click sempre manuale)"
    elif or_key or (arb_model.startswith("gemini") and google_count > 0):
        phase_d_status = "amber"
        phase_d_desc = (
            f"Arbitro {arb_model} · Free: {'ON' if arb_on_free else 'OFF'} | "
            f"Pagamento: {'ON' if arb_on_paid else 'OFF'}"
        )
    else:
        phase_d_status = "red"
        phase_d_desc = "Disattivato · Richiede OpenRouter API Key"

    return {
        "google_count": google_count,
        "has_openrouter": or_key,
        "phase_a": phase_a_status,
        "phase_a_desc": phase_a_desc,
        "phase_b": phase_b_status,
        "phase_b_desc": phase_b_desc,
        "phase_c": phase_c_status,
        "phase_c_desc": phase_c_desc,
        "phase_d": phase_d_status,
        "phase_d_desc": phase_d_desc,
        "daily_free_capacity": daily_cap,
        "rpm_capacity": rpm_cap,
        "overall_ready": (google_count > 0 or or_key),
    }
