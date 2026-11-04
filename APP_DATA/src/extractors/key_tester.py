"""Verifica rapida (ping) delle chiavi API: Google Gemini e OpenRouter.

Le funzioni sono bloccanti: vanno eseguite su un thread secondario dalla UI.
"""

from typing import Tuple

import requests

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"


def _gemini_error_message(resp: requests.Response) -> str:
    try:
        msg = resp.json().get("error", {}).get("message", "")
    except Exception:
        msg = ""
    return msg or resp.text[:200] or f"HTTP {resp.status_code}"


def test_gemini_api_key(api_key: str, model: str = "gemini-2.5-flash", timeout: float = 15.0) -> Tuple[bool, str]:
    """Ping sintetico: legge i metadati del modello (non consuma quota di generazione).

    Ritorna (valida, messaggio).
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Chiave vuota."
    try:
        resp = requests.get(
            f"{GEMINI_BASE_URL}/models/{model}",
            params={"key": api_key},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return False, "Timeout: il server non risponde. Controllare la connessione."
    except requests.exceptions.ConnectionError:
        return False, "Nessuna connessione a Internet."
    except Exception as e:
        return False, f"Errore di rete: {e}"

    if resp.status_code == 200:
        return True, "Chiave valida."
    if resp.status_code == 404:
        # La chiave è stata accettata ma il modello non esiste: verifica con l'elenco modelli
        try:
            r2 = requests.get(f"{GEMINI_BASE_URL}/models", params={"key": api_key, "pageSize": 1}, timeout=timeout)
            if r2.status_code == 200:
                return True, f"Chiave valida (modello '{model}' non disponibile per questo account)."
        except Exception:
            pass
    if resp.status_code == 429:
        return True, "Chiave valida, ma quota esaurita al momento (429)."
    if resp.status_code in (400, 401, 403):
        return False, "Chiave non valida o scaduta. " + _gemini_error_message(resp)
    return False, f"Errore {resp.status_code}: {_gemini_error_message(resp)}"


def test_openrouter_api_key(api_key: str, timeout: float = 15.0) -> Tuple[bool, str]:
    """Verifica una chiave OpenRouter tramite l'endpoint /auth/key."""
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Chiave vuota."
    try:
        resp = requests.get(OPENROUTER_KEY_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    except requests.exceptions.Timeout:
        return False, "Timeout: il server non risponde."
    except requests.exceptions.ConnectionError:
        return False, "Nessuna connessione a Internet."
    except Exception as e:
        return False, f"Errore di rete: {e}"
    if resp.status_code == 200:
        return True, "Chiave valida."
    if resp.status_code in (401, 403):
        return False, "Chiave non valida o scaduta."
    return False, f"Errore {resp.status_code}."
