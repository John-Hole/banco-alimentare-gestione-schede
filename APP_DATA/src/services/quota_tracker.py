"""Servizio per il monitoraggio e il tracciamento locale delle quote IA (Gemini & OpenRouter).

Gestisce il calcolo delle richieste giornaliere (RPD), richieste per minuto (RPM),
token utilizzati, ciclo di reset alle 09:00 italiane (00:00 Pacific Time di Google)
e storico delle ultime chiamate effettuate.
"""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional

# Limiti noti ufficiali per i modelli Google Gemini (Free Tier) e OpenRouter
MODEL_CATALOG = {
    "gemini-3.5-flash-lite": {
        "name": "Gemini 3.5 Flash Lite",
        "rpm": 15,
        "tpm": 250_000,
        "rpd": 500,
        "desc": "Modello ultra-veloce principale, quota abbondante di 500 schede/giorno."
    },
    "gemini-3.8-flash": {
        "name": "Gemini 3.8 Flash",
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 20,
        "desc": "Modello ad altissima qualità, quota limitata di 20 schede/giorno (Free)."
    },
    "gemini-3.5-flash": {
        "name": "Gemini 3.5 Flash",
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 20,
        "desc": "Modello bilanciato, quota limitata di 20 schede/giorno (Free)."
    },
    "gemini-3.6-flash": {
        "name": "Gemini 3.6 Flash",
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 20,
        "desc": "Modello Flash, quota di 20 schede/giorno."
    },
    "gemini-2.5-flash": {
        "name": "Gemini 2.5 Flash",
        "rpm": 5,
        "tpm": 250_000,
        "rpd": 20,
        "desc": "Modello rapido precedente, ottimo come fallback (20 schede/giorno)."
    },
    "gemini-2.5-flash-lite": {
        "name": "Gemini 2.5 Flash Lite",
        "rpm": 10,
        "tpm": 250_000,
        "rpd": 20,
        "desc": "Modello leggero precedente, 20 schede/giorno."
    },
    "openrouter": {
        "name": "OpenRouter (A Consumo / Crediti)",
        "rpm": 60,
        "tpm": 1_000_000,
        "rpd": 99_999,
        "desc": "Accesso a consumo tramite saldo OpenRouter (senza limite giornaliero RPD fisso)."
    }
}

DEFAULT_GEMINI_LIMIT = {"name": "Gemini Personalizzato", "rpm": 5, "tpm": 250_000, "rpd": 20, "desc": "Quota standard Free Tier."}
STATS_FILE_PATH = Path(__file__).resolve().parent.parent.parent / "usage_stats.json"


def get_current_cycle_id() -> str:
    """Restituisce la data del ciclo quota corrente (YYYY-MM-DD).
    
    Google resetta le quote alle 00:00 Pacific Time.
    In Italia (fuso orario UTC+1 / UTC+2), il reset avviene sempre alle ore 09:00 del mattino.
    Quindi, fino alle ore 08:59:59 si appartiene al ciclo del giorno precedente.
    """
    now = datetime.now()
    if now.hour < 9:
        cycle_dt = now - timedelta(days=1)
    else:
        cycle_dt = now
    return cycle_dt.strftime("%Y-%m-%d")


def get_time_until_reset() -> str:
    """Restituisce il tempo residuo in formato leggibile fino alle prossime ore 09:00."""
    now = datetime.now()
    if now.hour < 9:
        next_reset = now.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        next_reset = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    diff = next_reset - now
    total_seconds = max(0, int(diff.total_seconds()))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours}h {minutes}m (alle 09:00)"


class QuotaTracker:
    """Tracciatore persistente e thread-safe dell'utilizzo API."""

    _instance: Optional["QuotaTracker"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(QuotaTracker, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, stats_path: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self.stats_path = stats_path or STATS_FILE_PATH
        self._file_lock = threading.Lock()
        # Cronologia timestamps richieste per il calcolo RPM live (in memoria)
        self._request_timestamps: List[float] = []
        self._ensure_loaded()
        self._initialized = True

    def _get_default_data(self) -> Dict[str, Any]:
        return {
            "cycle_id": get_current_cycle_id(),
            "models": {},
            "recent_calls": []
        }

    def _ensure_loaded(self) -> Dict[str, Any]:
        current_cycle = get_current_cycle_id()
        data = self._get_default_data()

        if self.stats_path.exists():
            try:
                with open(self.stats_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        # Se il ciclo registrato è precedente alle 09:00 di oggi, resetta le quote giornaliere
                        if loaded.get("cycle_id") == current_cycle:
                            data = loaded
                        else:
                            # Manteniamo solo lo storico recente, azzerando i contatori giornalieri
                            data["recent_calls"] = loaded.get("recent_calls", [])[:15]
            except Exception:
                pass

        data["cycle_id"] = current_cycle
        return data

    def _save_data(self, data: Dict[str, Any]) -> None:
        with self._file_lock:
            try:
                temp_file = self.stats_path.with_suffix(".tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                temp_file.replace(self.stats_path)
            except Exception as e:
                print(f"[QuotaTracker] Errore nel salvataggio statistiche: {e}")

    def get_model_spec(self, model: str) -> Dict[str, Any]:
        """Restituisce le specifiche e i limiti noti del modello."""
        clean_model = model.lower().strip()
        if clean_model in MODEL_CATALOG:
            return MODEL_CATALOG[clean_model]
        # Tentativo di corrispondenza parziale
        for key, spec in MODEL_CATALOG.items():
            if key in clean_model or clean_model in key:
                return spec
        return DEFAULT_GEMINI_LIMIT

    def record_call(
        self,
        provider: str,
        model: str,
        status_code: int,
        tokens: int = 0,
        duration_sec: float = 0.0,
        is_fallback: bool = False,
        note: str = ""
    ) -> None:
        """Registra una chiamata API effettuata."""
        now_ts = time.time()
        self._request_timestamps.append(now_ts)
        # Pulisci timestamp più vecchi di 60 secondi
        cutoff = now_ts - 60.0
        self._request_timestamps = [t for t in self._request_timestamps if t >= cutoff]

        data = self._ensure_loaded()
        models = data.setdefault("models", {})
        
        # Chiave univoca per separare i contatori di Gemini 1 e Gemini 2
        stat_key = f"{provider}:{model}" if provider in ("gemini_1", "gemini_2") else model
        model_stats = models.setdefault(stat_key, {
            "requests_today": 0,
            "tokens_today": 0,
            "last_error_code": None,
            "is_exhausted": False
        })

        if status_code == 200:
            model_stats["requests_today"] += 1
            model_stats["tokens_today"] += tokens
            model_stats["last_error_code"] = None
        elif status_code == 429:
            model_stats["last_error_code"] = 429
            model_stats["is_exhausted"] = True
        else:
            model_stats["last_error_code"] = status_code

        # Aggiungi allo storico chiamate (massimo 25 voci)
        recent = data.setdefault("recent_calls", [])
        call_entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "provider": provider,
            "model": model,
            "status_code": status_code,
            "tokens": tokens,
            "duration_sec": round(duration_sec, 2),
            "is_fallback": is_fallback,
            "note": note
        }
        recent.insert(0, call_entry)
        data["recent_calls"] = recent[:25]

        self._save_data(data)

    def get_rpm(self) -> int:
        """Restituisce il numero di richieste effettuate negli ultimi 60 secondi."""
        now_ts = time.time()
        cutoff = now_ts - 60.0
        self._request_timestamps = [t for t in self._request_timestamps if t >= cutoff]
        return len(self._request_timestamps)

    def is_exhausted(self, provider: str, model: str) -> bool:
        """Restituisce True se la quota del provider/modello è esaurita (errore 429 o limite RPD superato)."""
        clean_provider = (provider or "gemini").lower().strip()
        if clean_provider == "openrouter":
            return False
        st = self.get_status(clean_provider, model)
        return bool(st.get("is_exhausted", False))

    def get_status(self, provider: str = "gemini", model: str = "gemini-3.5-flash-lite") -> Dict[str, Any]:
        """Restituisce lo stato corrente completo per il modello e provider specificato."""
        data = self._ensure_loaded()
        clean_provider = (provider or "gemini").lower().strip()

        if clean_provider == "efficiency":
            eff_model = "gemini-3.8-flash"
            gem1_st = self.get_status("gemini_1", eff_model)
            if not gem1_st.get("is_exhausted", False):
                return {
                    **gem1_st,
                    "provider": "efficiency",
                    "active_subprovider": "gemini_1",
                    "status_text": "Efficienza (Gemini 3.8 Free — 20/giorno)",
                    "provider_label": "⚡ Gemini 1 (3.8)"
                }
            gem2_st = self.get_status("gemini_2", eff_model)
            if not gem2_st.get("is_exhausted", False):
                return {
                    **gem2_st,
                    "provider": "efficiency",
                    "active_subprovider": "gemini_2",
                    "status_text": "Efficienza (Gemini 2 3.8 Free — 20/giorno)",
                    "provider_label": "⚡ Gemini 2 (3.8)"
                }
            or_st = self.get_status("openrouter", "xiaomi/mimo-v2.6-flash")
            return {
                **or_st,
                "provider": "efficiency",
                "active_subprovider": "openrouter",
                "status_text": "Efficienza (Xiaomi OpenRouter Attivo)",
                "provider_label": "⚡ OR Xiaomi"
            }

        if clean_provider == "openrouter":
            spec = MODEL_CATALOG["openrouter"]
            rpm = self.get_rpm()
            return {
                "provider": "openrouter",
                "model": model,
                "model_name": spec["name"],
                "requests_today": len(data.get("recent_calls", [])),
                "limit_rpd": 99999,
                "remaining_rpd": 99999,
                "percent_used": 0.0,
                "percent_remaining": 100.0,
                "rpm_current": rpm,
                "limit_rpm": spec["rpm"],
                "tokens_today": sum(m.get("tokens_today", 0) for m in data.get("models", {}).values()),
                "color": "#10b981",  # Sempre verde per OpenRouter free
                "status_text": "A Consumo (Crediti)",
                "is_exhausted": False,
                "time_until_reset": get_time_until_reset(),
                "recent_calls": data.get("recent_calls", [])
            }

        # Provider Gemini (Gemini 1 o Gemini 2)
        if "2" in clean_provider:
            provider_key = "gemini_2"
            provider_label = "Gemini 2"
        elif "1" in clean_provider:
            provider_key = "gemini_1"
            provider_label = "Gemini 1"
        else:
            provider_key = "gemini"
            provider_label = "Gemini"

        spec = self.get_model_spec(model)
        models_dict = data.get("models", {})
        # Cerca prima la chiave specifica del provider (es. 'gemini_1:gemini-3.5-flash-lite'), poi il modello generico
        stat_key = f"{provider_key}:{model}"
        model_stats = models_dict.get(stat_key) or models_dict.get(model, {
            "requests_today": 0,
            "tokens_today": 0,
            "last_error_code": None,
            "is_exhausted": False
        })

        req_today = model_stats["requests_today"]
        limit_rpd = spec["rpd"]
        limit_rpm = spec["rpm"]
        rpm = self.get_rpm()
        is_exhausted = model_stats.get("is_exhausted", False) or req_today >= limit_rpd

        percent_used = min(100.0, round((req_today / limit_rpd) * 100, 1)) if limit_rpd > 0 else 0.0
        percent_remaining = max(0.0, round(100.0 - percent_used, 1))
        remaining_rpd = max(0, limit_rpd - req_today)

        # Determina colore badge
        if is_exhausted or percent_used >= 90.0:
            color = "#ef4444"  # Rosso
            status_text = "Quota Quasi Esaurita" if not is_exhausted else "Quota Esaurita (429)"
        elif percent_used >= 70.0:
            color = "#f59e0b"  # Giallo
            status_text = "Attenzione Quota (>70%)"
        else:
            color = "#10b981"  # Verde
            status_text = "Quota Disponibile"

        return {
            "provider": provider_key,
            "provider_label": provider_label,
            "model": model,
            "model_name": spec["name"],
            "requests_today": req_today,
            "limit_rpd": limit_rpd,
            "remaining_rpd": remaining_rpd,
            "percent_used": percent_used,
            "percent_remaining": percent_remaining,
            "rpm_current": rpm,
            "limit_rpm": limit_rpm,
            "tokens_today": model_stats.get("tokens_today", 0),
            "color": color,
            "status_text": status_text,
            "is_exhausted": is_exhausted,
            "time_until_reset": get_time_until_reset(),
            "recent_calls": data.get("recent_calls", [])
        }

    def reset_stats(self) -> None:
        """Azzera manualmente tutti i contatori registrati."""
        data = self._get_default_data()
        self._request_timestamps.clear()
        self._save_data(data)


_global_tracker: Optional[QuotaTracker] = None


def get_quota_tracker() -> QuotaTracker:
    """Restituisce l'istanza globale del QuotaTracker (Singleton)."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = QuotaTracker()
    return _global_tracker
