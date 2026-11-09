import concurrent.futures
import time
from typing import Any, Dict, List, Tuple

import requests

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_TEST_MODEL = "nvidia/nemotron-3.5-super-vl:free"


def _gemini_error_message(resp: requests.Response) -> str:
    try:
        msg = resp.json().get("error", {}).get("message", "")
    except Exception:
        msg = ""
    return msg or resp.text[:200] or f"HTTP {resp.status_code}"


def test_gemini_api_key(api_key: str, model: str = "gemini-3.5-flash-lite", timeout: float = 10.0) -> Tuple[bool, str]:
    """Ping sintetico: legge i metadati del modello (non consuma quota di generazione).

    Ritorna (valida, messaggio).
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Chiave vuota."
    t0 = time.time()
    endpoint = f"{GEMINI_BASE_URL}/models/{model}" if model else f"{GEMINI_BASE_URL}/models"
    try:
        resp = requests.get(
            endpoint,
            params={"key": api_key},
            timeout=timeout,
        )
        latency = time.time() - t0
    except requests.exceptions.Timeout:
        return False, f"Timeout ({timeout:.0f}s): il server Google non risponde."
    except requests.exceptions.ConnectionError:
        return False, "Nessuna connessione a Internet o server Google irraggiungibile."
    except Exception as e:
        return False, f"Errore di rete: {e}"

    if resp.status_code == 200:
        return True, f"✓ Attiva · Chiave valida (risposta in {latency:.2f}s)"
    if resp.status_code == 404:
        # Se il modello specifico non esiste, verifica con l'endpoint models generale
        try:
            r2 = requests.get(f"{GEMINI_BASE_URL}/models", params={"key": api_key, "pageSize": 1}, timeout=timeout)
            if r2.status_code == 200:
                return True, f"✓ Attiva ({latency:.2f}s) · Modello non disponibile"
        except Exception:
            pass
    if resp.status_code == 429:
        return True, f"✓ Attiva · Quota esaurita al momento (429, {latency:.2f}s)"
    if resp.status_code in (400, 401, 403):
        return False, "Chiave non valida o scaduta. " + _gemini_error_message(resp)
    return False, f"Errore {resp.status_code}: {_gemini_error_message(resp)}"


def test_openrouter_api_key(api_key: str, timeout: float = 15.0) -> Tuple[bool, str]:
    """Verifica una chiave OpenRouter tramite l'endpoint /auth/key."""
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Chiave vuota."
    t0 = time.time()
    try:
        resp = requests.get(
            OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        latency = time.time() - t0
    except requests.exceptions.Timeout:
        return False, f"Timeout ({timeout:.0f}s): il server OpenRouter non risponde."
    except requests.exceptions.ConnectionError:
        return False, "Nessuna connessione a Internet o OpenRouter irraggiungibile."
    except Exception as e:
        return False, f"Errore di rete: {e}"
    if resp.status_code == 200:
        return True, f"✓ Attiva · Chiave valida (risposta in {latency:.2f}s)"
    if resp.status_code in (401, 403):
        return False, "Chiave non valida o scaduta."
    return False, f"Errore {resp.status_code}."


def test_gemini_generation(
    api_key: str,
    model: str = "gemini-3.5-flash-lite",
    timeout: float = 12.0,
) -> Tuple[bool, str]:
    """Testa una reale generazione di contenuto (POST generateContent) con payload minimale.

    Verifica se il modello risponde effettivamente o se è congestionato (503), in quota (429), ecc.
    Ritorna (successo, messaggio_dettagliato).
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Chiave vuota."
    clean_model = (model or "gemini-3.5-flash-lite").strip()
    if clean_model.startswith("models/"):
        clean_model = clean_model[7:]
    endpoint = f"{GEMINI_BASE_URL}/models/{clean_model}:generateContent"
    clean_lower = clean_model.lower()
    gen_cfg: Dict[str, Any] = {"maxOutputTokens": 64 if clean_lower.startswith(("gemini-3", "gemini-4", "gemini-5")) else 10}
    if clean_lower.startswith(("gemini-3", "gemini-4", "gemini-5")):
        gen_cfg["thinkingConfig"] = {"thinkingLevel": "LOW"}
    else:
        gen_cfg["temperature"] = 0.0
    payload = {
        "contents": [{"parts": [{"text": "ping"}]}],
        "generationConfig": gen_cfg,
    }
    t0 = time.time()
    try:
        resp = requests.post(
            endpoint,
            params={"key": api_key},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        lat = time.time() - t0
    except requests.exceptions.Timeout:
        return False, f"Timeout ({timeout:.0f}s): il modello non ha risposto in tempo."
    except requests.exceptions.ConnectionError:
        return False, "Connessione assente o server Google non raggiungibile."
    except Exception as e:
        return False, f"Errore di rete: {e}"

    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, dict):
                if "error" in data:
                    err_info = data["error"]
                    err_msg = err_info.get("message", "") if isinstance(err_info, dict) else str(err_info)
                    return False, f"Errore Google: {err_msg}"
                block_reason = data.get("promptFeedback", {}).get("blockReason")
                if block_reason:
                    return False, f"Blocco sicurezza Google: {block_reason}"
                candidates = data.get("candidates", [])
                if candidates and isinstance(candidates, list):
                    cand0 = candidates[0] if isinstance(candidates[0], dict) else {}
                    content = cand0.get("content") or {}
                    parts = content.get("parts", []) or []
                    txt = (parts[0].get("text", "") if parts and isinstance(parts[0], dict) else "").strip()
                    if not txt:
                        finish_reason = cand0.get("finishReason", "")
                        txt = f"[{finish_reason}]" if finish_reason else "OK"
                else:
                    txt = "OK"
            else:
                txt = "OK"
            txt_snippet = (txt[:25] + "...") if len(txt) > 25 else txt
            return True, f"✓ Risposta ricevuta in {lat:.2f}s: '{txt_snippet}'"
        except Exception:
            return True, f"✓ Risposta ricevuta in {lat:.2f}s (HTTP 200)"
    if resp.status_code == 503:
        return False, "⚠️ Sovraccarico server Google (HTTP 503: alta richiesta). Riprova tra poco."
    if resp.status_code == 429:
        return False, "⚠️ Quota esaurita (HTTP 429). Limite richieste superato."
    if resp.status_code in (500, 502, 504):
        return False, f"⚠️ Server Google non disponibile (HTTP {resp.status_code}). Riprova tra poco."
    if resp.status_code in (400, 401, 403):
        return False, f"Errore HTTP {resp.status_code}: {_gemini_error_message(resp)}"
    return False, f"Errore HTTP {resp.status_code}: {_gemini_error_message(resp)}"


def test_openrouter_generation(
    api_key: str,
    model: str = DEFAULT_OPENROUTER_TEST_MODEL,
    timeout: float = 15.0,
) -> Tuple[bool, str]:
    """Invia una richiesta minima di chat completion al modello OpenRouter indicato.

    Ritorna (successo, messaggio_dettagliato).
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return False, "Chiave vuota."
    model = (model or DEFAULT_OPENROUTER_TEST_MODEL).strip()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 10,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/banco-alimentare",
        "X-Title": "Banco Alimentare Colletta",
    }
    t0 = time.time()
    try:
        resp = requests.post(
            OPENROUTER_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        lat = time.time() - t0
    except requests.exceptions.Timeout:
        return False, f"Timeout ({timeout:.0f}s): OpenRouter non ha risposto in tempo."
    except requests.exceptions.ConnectionError:
        return False, "Connessione assente o server OpenRouter non raggiungibile."
    except Exception as e:
        return False, f"Errore di rete: {e}"

    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, dict):
                if "error" in data:
                    err_info = data["error"]
                    err_msg = err_info.get("message", "") if isinstance(err_info, dict) else str(err_info)
                    return False, f"⚠️ Errore OpenRouter: {err_msg or 'Errore provider'}"
                choices = data.get("choices", [])
                if choices and isinstance(choices, list):
                    c0 = choices[0] if isinstance(choices[0], dict) else {}
                    msg_obj = c0.get("message") or {}
                    txt = (msg_obj.get("content", "") or "").strip()
                else:
                    txt = "OK"
            else:
                txt = "OK"
            txt_snippet = (txt[:25] + "...") if len(txt) > 25 else txt
            return True, f"✓ Risposta ricevuta in {lat:.2f}s: '{txt_snippet}'"
        except Exception:
            return True, f"✓ Risposta ricevuta in {lat:.2f}s (HTTP 200)"
    if resp.status_code == 429:
        return False, "⚠️ Quota OpenRouter esaurita o rate limit superato (HTTP 429)."
    if resp.status_code in (401, 403):
        return False, "Chiave OpenRouter non valida o scaduta."
    if resp.status_code in (502, 503, 504):
        return False, f"⚠️ Modello OpenRouter temporaneamente non disponibile (HTTP {resp.status_code})."
    try:
        err_msg = resp.json().get("error", {}).get("message", "")
    except Exception:
        err_msg = ""
    err_msg = err_msg or resp.text[:150] or f"HTTP {resp.status_code}"
    return False, f"Errore HTTP {resp.status_code}: {err_msg}"


def check_all_servers_status(
    gemini_keys: List[str],
    openrouter_key: str = "",
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Testa in PARALLELO lo stato operativo e la latenza di TUTTE le chiavi Google Gemini e della chiave OpenRouter."""
    results: Dict[str, Any] = {
        "gemini": {
            "ok": False,
            "latency": 0.0,
            "status_text": "Nessuna chiave configurata",
            "keys": [],
        },
        "openrouter": {
            "ok": False,
            "latency": 0.0,
            "status_text": "Nessuna chiave configurata",
        },
    }

    valid_gem_keys = [k.strip() for k in gemini_keys if k and k.strip()]
    or_k = (openrouter_key or "").strip()

    def check_single_gemini_key(idx: int, key: str) -> Dict[str, Any]:
        t0 = time.time()
        ok, msg = test_gemini_api_key(key, timeout=min(timeout, 4.0))
        lat = time.time() - t0
        return {
            "index": idx,
            "key": key,
            "ok": ok,
            "latency": lat,
            "msg": msg,
        }

    def check_gemini() -> Dict[str, Any]:
        if not valid_gem_keys:
            try:
                t0 = time.time()
                r = requests.get(f"{GEMINI_BASE_URL}/models", timeout=min(timeout, 3.0))
                dur = time.time() - t0
                return {
                    "ok": True,
                    "latency": dur,
                    "status_text": f"🟡 Server raggiungibile ({dur:.2f}s), nessuna chiave inserita",
                    "keys": [],
                }
            except Exception:
                return {
                    "ok": False,
                    "latency": 0.0,
                    "status_text": "🔴 Server Google non raggiungibile",
                    "keys": [],
                }

        keys_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(valid_gem_keys))) as pool:
            futures = [pool.submit(check_single_gemini_key, idx, k) for idx, k in enumerate(valid_gem_keys, 1)]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    keys_results.append(fut.result())
                except Exception as e:
                    keys_results.append({"index": 0, "key": "", "ok": False, "latency": 0.0, "msg": str(e)})

        keys_results.sort(key=lambda x: x.get("index", 0))

        all_ok = any(kr["ok"] for kr in keys_results)
        max_lat = max((kr["latency"] for kr in keys_results), default=0.0)

        status_parts = []
        for kr in keys_results:
            idx = kr["index"]
            if kr["ok"]:
                status_parts.append(f"Google {idx}: 🟢 Operativo ({kr['latency']:.2f}s)")
            else:
                msg_low = kr.get("msg", "").lower()
                if "timeout" in msg_low:
                    status_parts.append(f"Google {idx}: 🔴 Timeout")
                elif "non valida" in msg_low or "scaduta" in msg_low or any(code in kr.get("msg", "") for code in ("400", "401", "403")):
                    status_parts.append(f"Google {idx}: 🔴 Non valida")
                else:
                    status_parts.append(f"Google {idx}: 🔴 Non risponde")

        status_text = "  •  ".join(status_parts) if status_parts else "Nessuna chiave verificata"

        return {
            "ok": all_ok,
            "latency": max_lat,
            "status_text": status_text,
            "keys": keys_results,
        }

    def check_openrouter() -> Dict[str, Any]:
        if not or_k:
            return {
                "ok": False,
                "latency": 0.0,
                "status_text": "⚪ Non configurato",
            }
        t0 = time.time()
        ok, msg = test_openrouter_api_key(or_k, timeout=min(timeout, 4.0))
        dur = time.time() - t0
        if ok:
            return {
                "ok": True,
                "latency": dur,
                "status_text": f"🟢 Operativo ({dur:.2f}s)",
            }
        return {
            "ok": False,
            "latency": dur,
            "status_text": f"🔴 Non disponibile ({msg})",
        }

    # Esecuzione concorrente in parallelo (max ~timeout secondi garantiti)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_gem = executor.submit(check_gemini)
        f_or = executor.submit(check_openrouter)
        try:
            results["gemini"] = f_gem.result(timeout=timeout + 2.0)
        except Exception as e:
            results["gemini"] = {"ok": False, "latency": 0.0, "status_text": f"🔴 Timeout Google ({e})", "keys": []}
        try:
            results["openrouter"] = f_or.result(timeout=timeout + 2.0)
        except Exception as e:
            results["openrouter"] = {"ok": False, "latency": 0.0, "status_text": f"🔴 Timeout OpenRouter ({e})"}

    return results
