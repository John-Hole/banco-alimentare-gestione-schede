"""Estrattore per OpenRouter con supporto per visione, failover tra chiavi e fallback automatico."""

import base64
import mimetypes
import time
from typing import Callable, Optional, Tuple, Dict, Any
import requests

from .base import BaseVisionExtractor, EXTRACTION_SYSTEM_PROMPT
from ..models import RawCardExtraction
from ..services.quota_tracker import get_quota_tracker
from ..services.session_logger import get_session_logger


class OpenRouterVisionExtractor(BaseVisionExtractor):
    """Chiama l'API OpenRouter con modelli di visione (OpenAI-compatible) con supporto per chiave di backup e fallback."""

    def __init__(
        self,
        api_key: str,
        backup_api_key: Optional[str] = None,
        model: str = "xiaomi/mimo-v2.6-pro",
        fallback_extractor: Optional[BaseVisionExtractor] = None,
        enable_auto_fallback: bool = True,
        reasoning_effort: Optional[str] = None,
        display_name: Optional[str] = None,
    ):
        self.api_key = (api_key or "").strip()
        self.backup_api_key = (backup_api_key or "").strip()
        self.model = model
        self.fallback_extractor = fallback_extractor
        self.enable_auto_fallback = enable_auto_fallback
        self.reasoning_effort = reasoning_effort
        self.display_name = display_name
        self.tracker = get_quota_tracker()
        self.session_logger = get_session_logger()

    def _execute_openrouter_call(self, key: str, img_data: str, mime_type: str) -> Tuple[int, Dict[str, Any], str, float]:
        """Esegue una singola chiamata REST a OpenRouter API."""
        self.session_logger.log_ai_attempt("OpenRouter", "OpenRouter API", self.model)
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://bancoalimentare.it",
            "X-Title": "Banco Alimentare Colletta",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": EXTRACTION_SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analizza l'immagine della scheda corriere allegata ed estrai tutte le informazioni nel formato JSON richiesto dalle istruzioni di sistema."},
                        {
                          "type": "image_url",
                          "image_url": {
                              "url": f"data:{mime_type};base64,{img_data}"
                          }
                        }
                    ]
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 4096
        }

        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        elif "nemotron" in self.model.lower():
            payload["reasoning"] = {"effort": "none"}

        t0 = time.time()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(5, 20))
            duration = time.time() - t0
            if resp.status_code != 200:
                self.session_logger.log_ai_result("OpenRouter", "OpenRouter", self.model, resp.status_code, duration, is_success=False, error_msg=resp.text[:300])
                return resp.status_code, {}, resp.text, duration
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            cost = float(data.get("usage", {}).get("cost") or 0.0)
            self.session_logger.log_ai_result("OpenRouter", "OpenRouter", self.model, 200, duration, tokens=tokens, is_success=True, cost_usd=cost)
            return 200, data, "", duration
        except requests.exceptions.Timeout:
            duration = time.time() - t0
            self.session_logger.log_ai_result("OpenRouter", "OpenRouter", self.model, 408, duration, is_success=False, error_msg=f"Timeout ({duration:.1f}s)")
            return 408, {}, f"Timeout OpenRouter ({duration:.1f}s): il server del modello non risponde in tempo", duration
        except Exception as e:
            duration = time.time() - t0
            self.session_logger.log_ai_result("OpenRouter", "OpenRouter", self.model, 500, duration, is_success=False, error_msg=str(e))
            return 500, {}, f"Eccezione di rete OpenRouter: {e}", duration

    def extract_from_image(self, image_path: str, status_callback: Optional[Callable[[str], None]] = None) -> RawCardExtraction:
        if not self.api_key:
            if self.fallback_extractor:
                return self.fallback_extractor.extract_from_image(image_path)
            return RawCardExtraction(
                note_o_anomalie="Chiave API OpenRouter mancante. Impostare la chiave nelle impostazioni o variabile OPENROUTER_API_KEY."
            )

        try:
            mime_type, img_data = self.encode_and_optimize_image(image_path)
        except Exception as e:
            return RawCardExtraction(note_o_anomalie=f"Impossibile leggere/ottimizzare immagine: {e}")

        # 1. Tentativo con Chiave Primaria OpenRouter
        if status_callback:
            status_callback(f"📡 Invio richiesta a OpenRouter ({self.model})...")
        status_code, result_json, err_text, duration = self._execute_openrouter_call(
            self.api_key, img_data, mime_type
        )

        if status_code == 200:
            choices = result_json.get("choices", [])
            tokens = result_json.get("usage", {}).get("total_tokens", 0)
            cost = float(result_json.get("usage", {}).get("cost") or 0.0)
            self.tracker.record_call(provider="openrouter", model=self.model, status_code=200, tokens=tokens, duration_sec=duration, is_fallback=False)
            if not choices:
                if self.fallback_extractor:
                    return self.fallback_extractor.extract_from_image(image_path)
                return RawCardExtraction(note_o_anomalie="Nessuna risposta da OpenRouter")

            msg = choices[0].get("message", {})
            text_content = msg.get("content", "")
            if not text_content or not text_content.strip():
                text_content = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
            card = self.parse_json_response(text_content)
            if not self.is_valid_raw_extraction(card):
                self.session_logger.logger.warning(
                    f"[OpenRouter] Risposta o JSON non valido dal modello {self.model}: {card.note_o_anomalie}. Avvio fallback..."
                )
            else:
                card.tempo_elaborazione_sec = round(duration, 1)
                card.costo_usd = cost
                disp_name = self.display_name or f"OpenRouter ({self.model})"
                cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                badge = f"[{disp_name} | {duration:.1f}s | {cost_str}]"
                if badge not in (card.note_o_anomalie or ""):
                    card.note_o_anomalie = f"{badge} {card.note_o_anomalie or ''}".strip()
                return card

        # Se fallisce, registra errore
        self.tracker.record_call(
            provider="openrouter",
            model=self.model,
            status_code=status_code,
            tokens=0,
            duration_sec=duration,
            is_fallback=False,
            note=err_text[:100]
        )

        if not self.enable_auto_fallback:
            return RawCardExtraction(note_o_anomalie=f"Errore OpenRouter HTTP {status_code}: {err_text}")

        # 2. Failover su Seconda Chiave OpenRouter (se presente)
        if self.backup_api_key and self.backup_api_key != self.api_key:
            fb_status, fb_json, fb_err, fb_dur = self._execute_openrouter_call(
                self.backup_api_key, img_data, mime_type
            )
            if fb_status == 200:
                choices = fb_json.get("choices", [])
                tokens = fb_json.get("usage", {}).get("total_tokens", 0)
                cost = float(fb_json.get("usage", {}).get("cost") or 0.0)
                self.tracker.record_call(
                    provider="openrouter",
                    model=self.model,
                    status_code=200,
                    tokens=tokens,
                    duration_sec=fb_dur,
                    is_fallback=True,
                    note="Failover su Chiave OpenRouter Backup"
                )
                if choices:
                    msg = choices[0].get("message", {})
                    text_content = msg.get("content", "")
                    if not text_content or not text_content.strip():
                        text_content = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
                    card = self.parse_json_response(text_content)
                    if self.is_valid_raw_extraction(card):
                        card.tempo_elaborazione_sec = round(fb_dur, 1)
                        card.costo_usd = cost
                        disp_name = self.display_name or f"OpenRouter ({self.model})"
                        cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                        card.note_o_anomalie = f"[{disp_name} (Backup) | {fb_dur:.1f}s | {cost_str}]: {card.note_o_anomalie or ''}".strip()
                        return card
                    else:
                        self.session_logger.logger.warning(
                            f"[OpenRouter] Risposta o JSON non valido da backup: {card.note_o_anomalie}."
                        )
            else:
                self.tracker.record_call(
                    provider="openrouter",
                    model=self.model,
                    status_code=fb_status,
                    tokens=0,
                    duration_sec=fb_dur,
                    is_fallback=True,
                    note=f"Failover OR Backup fallito (HTTP {fb_status})"
                )

        # 3. Fallback di emergenza a catena (se configurato)
        if self.fallback_extractor:
            try:
                card = self.fallback_extractor.extract_from_image(image_path)
                disp_this = self.display_name or self.model
                fb_disp = getattr(self.fallback_extractor, "display_name", None) or getattr(self.fallback_extractor, "model", "fallback")
                fallback_msg = f"[Fallback {fb_disp}]: {disp_this} non disponibile (HTTP {status_code})."
                card.note_o_anomalie = f"{fallback_msg} {card.note_o_anomalie or ''}".strip()
                return card
            except Exception as e:
                disp_this = self.display_name or self.model
                return RawCardExtraction(
                    note_o_anomalie=f"Errore {disp_this} ({status_code}) ed eccezione fallback: {e}"
                )

        return RawCardExtraction(
            note_o_anomalie=f"Errore {self.display_name or self.model} HTTP {status_code}: {err_text}"
        )
