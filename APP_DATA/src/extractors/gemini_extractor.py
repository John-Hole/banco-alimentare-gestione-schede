"""Estrattore per Google Gemini API con supporto per visione, fallback automatico e tracciamento quote."""

import base64
import mimetypes
from pathlib import Path
import time
from typing import Optional, Tuple, Dict, Any
import requests

from .base import BaseVisionExtractor, EXTRACTION_SYSTEM_PROMPT
from ..models import RawCardExtraction
from ..services.quota_tracker import get_quota_tracker
from ..services.session_logger import get_session_logger


GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "supermercato": {"type": "STRING", "nullable": True},
        "numero_scheda": {"type": "STRING", "nullable": True},
        "colonne": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "colonna_idx": {"type": "INTEGER"},
                    "categoria": {"type": "STRING"},
                    "scatole": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "riga": {"type": "STRING"},
                                "kg": {"type": "NUMBER"}
                            },
                            "required": ["riga", "kg"]
                        }
                    },
                    "totale_dichiarato_kg": {"type": "NUMBER", "nullable": True},
                    "totale_dichiarato_scatole": {"type": "INTEGER", "nullable": True}
                },
                "required": ["colonna_idx", "categoria", "scatole"]
            }
        },
        "note_o_anomalie": {"type": "STRING", "nullable": True},
        "scheda_capovolta": {"type": "BOOLEAN"}
    },
    "required": ["colonne", "scheda_capovolta"]
}


class GeminiVisionExtractor(BaseVisionExtractor):
    """Chiama le API di Google Gemini con capacità di visione multimodale, failover tra due chiavi API e fallback OpenRouter."""

    def __init__(
        self,
        api_key: str,
        backup_api_key: Optional[str] = None,
        primary_label: str = "Gemini 1",
        backup_label: str = "Gemini 2",
        model: str = "gemini-3.5-flash-lite",
        fallback_extractor: Optional[BaseVisionExtractor] = None,
        enable_auto_fallback: bool = True
    ):
        self.api_key = api_key
        self.backup_api_key = backup_api_key
        self.primary_label = primary_label
        self.backup_label = backup_label
        self.model = model
        self.fallback_extractor = fallback_extractor
        self.enable_auto_fallback = enable_auto_fallback
        self.tracker = get_quota_tracker()
        self.session_logger = get_session_logger()

    @staticmethod
    def _extract_text_from_candidates(candidates: list) -> str:
        """Estrae il testo JSON dall'ultimo part non-thought dei candidates Gemini."""
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return ""
        non_thought = [
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and not p.get("thought") and p.get("text")
        ]
        if non_thought:
            return non_thought[-1]
        last = parts[-1]
        return last.get("text", "") if isinstance(last, dict) else ""

    def _execute_gemini_call(self, api_key: str, model_name: str, img_data: str, mime_type: str) -> Tuple[int, Dict[str, Any], str, float]:
        """Esegue una singola chiamata REST a Google Generative Language API e calcola tempo e token."""
        self.session_logger.log_ai_attempt("Google Gemini", "Google", model_name)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "system_instruction": {
                "parts": [{"text": EXTRACTION_SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Analizza la scheda allegata."},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": img_data
                            }
                        }
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": GEMINI_RESPONSE_SCHEMA,
                "temperature": 0.0,
                "maxOutputTokens": 4096,
                "thinkingConfig": {"thinkingBudget": 512}
            }
        }

        t0 = time.time()
        try:
            resp = requests.post(url, json=payload, timeout=(8, 32))
            duration = time.time() - t0
            if resp.status_code != 200:
                self.session_logger.log_ai_result("Google Gemini", "Google", model_name, resp.status_code, duration, is_success=False, error_msg=resp.text[:300])
                return resp.status_code, {}, resp.text, duration
            data = resp.json()
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
            self.session_logger.log_ai_result("Google Gemini", "Google", model_name, 200, duration, tokens=tokens, is_success=True, cost_usd=0.0)
            return 200, data, "", duration
        except requests.exceptions.Timeout:
            duration = time.time() - t0
            self.session_logger.log_ai_result("Google Gemini", "Google", model_name, 408, duration, is_success=False, error_msg=f"Timeout ({duration:.1f}s)")
            return 408, {}, f"Timeout connessione Gemini ({duration:.1f}s)", duration
        except Exception as e:
            duration = time.time() - t0
            self.session_logger.log_ai_result("Google Gemini", "Google", model_name, 500, duration, is_success=False, error_msg=str(e))
            return 500, {}, f"Eccezione di rete Gemini: {e}", duration

    def extract_from_image(self, image_path: str, _is_retry_after_rotation: bool = False) -> RawCardExtraction:
        if not self.api_key:
            if self.fallback_extractor:
                return self.fallback_extractor.extract_from_image(image_path)
            return RawCardExtraction(
                note_o_anomalie=f"Chiave API {self.primary_label} mancante. Impostare la chiave nel file di configurazione o variabili d'ambiente."
            )

        try:
            mime_type, img_data = self.encode_and_optimize_image(image_path)
        except Exception as e:
            return RawCardExtraction(note_o_anomalie=f"Impossibile leggere/ottimizzare immagine: {e}")

        prim_provider = "gemini_1" if "1" in self.primary_label else ("gemini_2" if "2" in self.primary_label else "gemini")
        back_provider = "gemini_2" if "2" in self.backup_label else ("gemini_1" if "1" in self.backup_label else "gemini")

        # Controllo preventivo quote (Modalità Efficienza / Failover Rapido):
        # Se la chiave primaria ha già esaurito le chiamate giornaliere (es. 20 RPD con 3.8 Flash o 429),
        # passa direttamente alla chiave secondaria o a OpenRouter senza attendere un errore preannunciato.
        prim_exhausted = self.tracker.is_exhausted(prim_provider, self.model)
        has_backup = bool(self.backup_api_key and self.backup_api_key.strip() and self.backup_api_key != self.api_key)
        back_exhausted = has_backup and self.tracker.is_exhausted(back_provider, self.model)

        if prim_exhausted:
            if has_backup and not back_exhausted:
                fb_status, fb_json, fb_err, fb_dur = self._execute_gemini_call(
                    self.backup_api_key, self.model, img_data, mime_type
                )
                if fb_status == 200:
                    fb_tokens = fb_json.get("usageMetadata", {}).get("totalTokenCount", 0)
                    self.tracker.record_call(
                        provider=back_provider,
                        model=self.model,
                        status_code=200,
                        tokens=fb_tokens,
                        duration_sec=fb_dur,
                        is_fallback=True,
                        note=f"Chiamata diretta {self.backup_label} ({self.primary_label} esaurita)"
                    )
                    candidates = fb_json.get("candidates", [])
                    if candidates:
                        text_content = self._extract_text_from_candidates(candidates)
                        card = self.parse_json_response(text_content)
                        card.tempo_elaborazione_sec = round(fb_dur, 1)
                        card.costo_usd = 0.0
                        card.note_o_anomalie = f"[{self.backup_label} ({self.model}) | {fb_dur:.1f}s | Gratis]: Quota {self.primary_label} esaurita. {card.note_o_anomalie or ''}".strip()
                        return card
                else:
                    self.tracker.record_call(
                        provider=back_provider,
                        model=self.model,
                        status_code=fb_status,
                        tokens=0,
                        duration_sec=fb_dur,
                        is_fallback=True,
                        note=f"{self.backup_label} fallito (HTTP {fb_status})"
                    )

            if self.fallback_extractor:
                try:
                    card = self.fallback_extractor.extract_from_image(image_path)
                    fallback_msg = f"[Fallback OpenRouter]: Quote Gemini esaurite. Estrazione completata tramite OpenRouter ({getattr(self.fallback_extractor, 'model', 'default')})."
                    card.note_o_anomalie = f"{fallback_msg} {card.note_o_anomalie or ''}".strip()
                    return card
                except Exception as e:
                    return RawCardExtraction(
                        note_o_anomalie=f"Quote Gemini esaurite ed eccezione fallback OpenRouter: {e}"
                    )

        # 1. Tentativo con il modello primario sulla chiave principale
        status_code, result_json, err_text, duration = self._execute_gemini_call(
            self.api_key, self.model, img_data, mime_type
        )

        if status_code == 200:
            usage_meta = result_json.get("usageMetadata", {})
            tokens = usage_meta.get("totalTokenCount", 0)
            self.tracker.record_call(
                provider=prim_provider,
                model=self.model,
                status_code=200,
                tokens=tokens,
                duration_sec=duration,
                is_fallback=False
            )

            candidates = result_json.get("candidates", [])
            if not candidates:
                return RawCardExtraction(note_o_anomalie=f"Nessun candidato restituito da {self.primary_label}")

            text_content = self._extract_text_from_candidates(candidates)
            card = self.parse_json_response(text_content)
            card.tempo_elaborazione_sec = round(duration, 1)
            card.costo_usd = 0.0
            if card.scheda_capovolta and not _is_retry_after_rotation:
                self.session_logger.logger.warning(
                    f"[Gemini] Scheda rilevata capovolta (180°): rotazione fisica su disco e ri-analisi immediata."
                )
                try:
                    from ..scanner.scanner_service import ScannerService
                    ScannerService.rotate_image_file_180(image_path)
                    return self.extract_from_image(image_path, _is_retry_after_rotation=True)
                except Exception as e:
                    self.session_logger.logger.error(f"[Gemini] Errore rotazione 180° e re-analisi: {e}")
            badge = f"[Google Gemini ({self.model}) | {duration:.1f}s | Gratis]"
            if badge not in (card.note_o_anomalie or ""):
                card.note_o_anomalie = f"{badge} {card.note_o_anomalie or ''}".strip()
            return card

        # 2. Se fallisce con codice 429 o errore
        self.tracker.record_call(
            provider=prim_provider,
            model=self.model,
            status_code=status_code,
            tokens=0,
            duration_sec=duration,
            is_fallback=False,
            note=f"Errore HTTP {status_code}"
        )

        # Se il fallback automatico non è abilitato, restituisci errore diretto
        if not self.enable_auto_fallback:
            return RawCardExtraction(
                note_o_anomalie=f"Errore {self.primary_label} HTTP {status_code}: {err_text}"
            )

        # 3. Failover su Seconda Chiave Gemini (stesso identico modello primario)
        if self.backup_api_key and self.backup_api_key.strip() and self.backup_api_key != self.api_key:
            fb_status, fb_json, fb_err, fb_dur = self._execute_gemini_call(
                self.backup_api_key, self.model, img_data, mime_type
            )
            if fb_status == 200:
                fb_tokens = fb_json.get("usageMetadata", {}).get("totalTokenCount", 0)
                self.tracker.record_call(
                    provider=back_provider,
                    model=self.model,
                    status_code=200,
                    tokens=fb_tokens,
                    duration_sec=fb_dur,
                    is_fallback=True,
                    note=f"Failover da {self.primary_label}"
                )
                candidates = fb_json.get("candidates", [])
                if candidates:
                    text_content = self._extract_text_from_candidates(candidates)
                    card = self.parse_json_response(text_content)
                    card.tempo_elaborazione_sec = round(fb_dur, 1)
                    card.costo_usd = 0.0
                    fallback_msg = f"[Failover {self.backup_label}] [Google Gemini ({self.model}) | {fb_dur:.1f}s | Gratis]: Quota esaurita su {self.primary_label}."
                    card.note_o_anomalie = f"{fallback_msg} {card.note_o_anomalie or ''}".strip()
                    return card
            else:
                self.tracker.record_call(
                    provider=back_provider,
                    model=self.model,
                    status_code=fb_status,
                    tokens=0,
                    duration_sec=fb_dur,
                    is_fallback=True,
                    note=f"Failover {self.backup_label} fallito (HTTP {fb_status})"
                )

        # 4. Fallback di emergenza (es. da Gemini 3.8 Flash a 3.5 Flash-Lite o su OpenRouter)
        if self.fallback_extractor:
            try:
                card = self.fallback_extractor.extract_from_image(image_path)
                fb_model = getattr(self.fallback_extractor, "model", "default")
                if isinstance(self.fallback_extractor, GeminiVisionExtractor):
                    fallback_msg = f"[Fallback Google API ({fb_model})]: {self.model} non disponibile (HTTP {status_code})."
                else:
                    fb_disp = getattr(self.fallback_extractor, "display_name", fb_model)
                    fallback_msg = f"[Fallback OpenRouter ({fb_disp})]: Google non disponibile (HTTP {status_code})."
                if card.righe_scatole or not (card.note_o_anomalie and "Limite quota" in card.note_o_anomalie):
                    card.note_o_anomalie = f"{fallback_msg} {card.note_o_anomalie or ''}".strip()
                    return card
            except Exception as e:
                self.session_logger.logger.error(f"[Gemini] Errore durante fallback extractor: {e}")

        return RawCardExtraction(
            note_o_anomalie=f"Limite quota Gemini esaurito (HTTP {status_code}): {err_text}"
        )
