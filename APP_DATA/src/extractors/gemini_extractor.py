"""Estrattore per Google Gemini API con supporto per visione, fallback automatico e tracciamento quote."""

import base64
import mimetypes
from pathlib import Path
import time
from typing import Callable, Optional, Tuple, Dict, Any
import requests

from .base import BaseVisionExtractor, EXTRACTION_SYSTEM_PROMPT
from ..models import RawCardExtraction
from ..services.quota_tracker import get_quota_tracker
from ..services.session_logger import get_session_logger


GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "propertyOrdering": [
        "supermercato",
        "numero_scheda",
        "colonne",
        "note_o_anomalie",
        "scheda_capovolta",
    ],
    "properties": {
        "supermercato": {"type": "STRING", "nullable": True},
        "numero_scheda": {"type": "STRING", "nullable": True},
        "colonne": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "propertyOrdering": [
                    "colonna_idx",
                    "categoria",
                    "scatole",
                    "totale_dichiarato_kg",
                    "totale_dichiarato_scatole",
                ],
                "properties": {
                    "colonna_idx": {"type": "INTEGER"},
                    "categoria": {"type": "STRING"},
                    "scatole": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "propertyOrdering": ["riga", "kg"],
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


def build_gemini_generation_config(
    model_name: str,
    response_mime_type: Optional[str] = "application/json",
    response_schema: Optional[Dict[str, Any]] = GEMINI_RESPONSE_SCHEMA,
    is_arbitration: bool = False,
    max_output_tokens: int = 16384,
) -> Dict[str, Any]:
    """Costruisce generationConfig ottimale per famiglia di modelli Gemini.

    - Gemini 3.x / 4.x+: thinkingLevel ('LOW' per OCR veloce, 'MEDIUM' per arbitraggio),
      omette temperature (puo' causare loop di ragionamento sui Gemini 3+), maxOutputTokens=16384.
    - Gemini 2.5.x: thinkingBudget=1024, temperature=0.0, maxOutputTokens=16384.
    - Gemini 1.5 / 2.0: omette thinkingConfig (non supportato), temperature=0.0, maxOutputTokens=8192.
    """
    clean = (model_name or "").strip().lower()
    if clean.startswith("models/"):
        clean = clean[7:]

    is_gemini_3_plus = clean.startswith(("gemini-3", "gemini-4", "gemini-5"))
    is_gemini_2_5 = clean.startswith("gemini-2.5")

    cfg: Dict[str, Any] = {}
    if response_mime_type:
        cfg["response_mime_type"] = response_mime_type
    if response_schema is not None:
        cfg["response_schema"] = response_schema

    if is_gemini_3_plus:
        cfg["maxOutputTokens"] = max_output_tokens
        cfg["thinkingConfig"] = {
            "thinkingLevel": "MEDIUM" if is_arbitration else "LOW"
        }
    elif is_gemini_2_5:
        cfg["temperature"] = 0.0
        cfg["maxOutputTokens"] = max_output_tokens
        cfg["thinkingConfig"] = {"thinkingBudget": 1024}
    else:
        cfg["temperature"] = 0.0
        cfg["maxOutputTokens"] = min(max_output_tokens, 8192)

    return cfg


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

    @staticmethod
    def _extract_finish_reason(candidates: list) -> str:
        """Restituisce il finishReason del primo candidate Gemini (se presente)."""
        if not candidates or not isinstance(candidates[0], dict):
            return ""
        return str(candidates[0].get("finishReason") or "").strip()

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
            "generationConfig": build_gemini_generation_config(model_name)
        }

        t0 = time.time()
        try:
            resp = requests.post(url, json=payload, timeout=(5, 30))
            duration = time.time() - t0
            if resp.status_code != 200:
                self.session_logger.log_ai_result("Google Gemini", "Google", model_name, resp.status_code, duration, is_success=False, error_msg=resp.text[:300])
                return resp.status_code, {}, resp.text, duration
            data = resp.json()
            usage_meta = data.get("usageMetadata", {})
            tokens = usage_meta.get("totalTokenCount", 0)
            fr = self._extract_finish_reason(data.get("candidates", []))
            if fr and fr.upper() not in self._NORMAL_FINISH_REASONS:
                self.session_logger.logger.warning(
                    f"[Gemini] {model_name} finishReason={fr} "
                    f"(thoughts={usage_meta.get('thoughtsTokenCount', 0)}, "
                    f"out={usage_meta.get('candidatesTokenCount', 0)}, tot={tokens})"
                )
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

    def extract_from_image(self, image_path: str, _is_retry_after_rotation: bool = False, status_callback: Optional[Callable[[str], None]] = None) -> RawCardExtraction:
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
                        finish_reason = self._extract_finish_reason(candidates)
                        card = self.parse_json_response(text_content, finish_reason=finish_reason)
                        if self.is_valid_raw_extraction(card):
                            card.tempo_elaborazione_sec = round(fb_dur, 1)
                            card.costo_usd = 0.0
                            if card.scheda_capovolta and not _is_retry_after_rotation:
                                try:
                                    from ..scanner.scanner_service import ScannerService
                                    ScannerService.rotate_image_file_180(image_path)
                                    retry_card = self.extract_from_image(
                                        image_path,
                                        _is_retry_after_rotation=True,
                                        status_callback=status_callback,
                                    )
                                    retry_card.was_rotated_180 = True
                                    return retry_card
                                except Exception as e:
                                    self.session_logger.logger.error(f"[Gemini Backup] Errore rotazione 180° e re-analisi: {e}")
                            card.note_o_anomalie = f"[{self.backup_label} ({self.model}) | {fb_dur:.1f}s | Gratis]: Quota {self.primary_label} esaurita. {card.note_o_anomalie or ''}".strip()
                            return card
                        self.session_logger.logger.warning(
                            f"[Gemini] Risposta o JSON incompleto da {self.backup_label}: {card.note_o_anomalie}. Avvio fallback..."
                        )
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
        if status_callback:
            status_callback(f"📡 Invio richiesta a Google Gemini ({self.model})...")
        status_code, result_json, err_text, duration = self._execute_gemini_call(
            self.api_key, self.model, img_data, mime_type
        )

        prim_recorded = False
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
            prim_recorded = True

            candidates = result_json.get("candidates", [])
            if not candidates:
                err_text = f"Nessun candidato restituito da {self.primary_label}"
                self.session_logger.logger.warning(f"[Gemini] {err_text}. Avvio failover/fallback...")
            else:
                text_content = self._extract_text_from_candidates(candidates)
                finish_reason = self._extract_finish_reason(candidates)
                card = self.parse_json_response(text_content, finish_reason=finish_reason)
                if not self.is_valid_raw_extraction(card):
                    err_text = card.note_o_anomalie or "Risposta o JSON incompleto/non valido"
                    self.session_logger.logger.warning(
                        f"[Gemini] Risposta o JSON non valido/incompleto da {self.primary_label}: {err_text}. Avvio failover/fallback..."
                    )
                else:
                    card.tempo_elaborazione_sec = round(duration, 1)
                    card.costo_usd = 0.0
                    if card.scheda_capovolta and not _is_retry_after_rotation:
                        self.session_logger.logger.warning(
                            f"[Gemini] Scheda rilevata capovolta (180°): rotazione fisica su disco e ri-analisi immediata."
                        )
                        try:
                            from ..scanner.scanner_service import ScannerService
                            ScannerService.rotate_image_file_180(image_path)
                            retry_card = self.extract_from_image(
                                image_path,
                                _is_retry_after_rotation=True,
                                status_callback=status_callback,
                            )
                            retry_card.was_rotated_180 = True
                            return retry_card
                        except Exception as e:
                            self.session_logger.logger.error(f"[Gemini] Errore rotazione 180° e re-analisi: {e}")
                    badge = f"[Google Gemini ({self.model}) | {duration:.1f}s | Gratis]"
                    if badge not in (card.note_o_anomalie or ""):
                        card.note_o_anomalie = f"{badge} {card.note_o_anomalie or ''}".strip()
                    return card

        # 2. Se fallisce con codice 429 o errore
        if not prim_recorded:
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
                note_o_anomalie=f"Errore {self.primary_label} HTTP {status_code}: {err_text}",
                is_incomplete=True,
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
                    finish_reason = self._extract_finish_reason(candidates)
                    card = self.parse_json_response(text_content, finish_reason=finish_reason)
                    if self.is_valid_raw_extraction(card):
                        card.tempo_elaborazione_sec = round(fb_dur, 1)
                        card.costo_usd = 0.0
                        if card.scheda_capovolta and not _is_retry_after_rotation:
                            self.session_logger.logger.warning(
                                f"[Gemini Backup] Scheda rilevata capovolta (180°): rotazione fisica su disco e ri-analisi immediata."
                            )
                            try:
                                from ..scanner.scanner_service import ScannerService
                                ScannerService.rotate_image_file_180(image_path)
                                retry_card = self.extract_from_image(
                                    image_path,
                                    _is_retry_after_rotation=True,
                                    status_callback=status_callback,
                                )
                                retry_card.was_rotated_180 = True
                                return retry_card
                            except Exception as e:
                                self.session_logger.logger.error(f"[Gemini Backup] Errore rotazione 180° e re-analisi: {e}")
                        fallback_msg = f"[Failover {self.backup_label}] [Google Gemini ({self.model}) | {fb_dur:.1f}s | Gratis]: Quota esaurita su {self.primary_label}."
                        card.note_o_anomalie = f"{fallback_msg} {card.note_o_anomalie or ''}".strip()
                        return card
                    else:
                        self.session_logger.logger.warning(
                            f"[Gemini] Risposta o JSON non valido da {self.backup_label}: {card.note_o_anomalie}."
                        )
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
                if not getattr(card, "is_incomplete", False) and (card.righe_scatole or not (card.note_o_anomalie and "Limite quota" in card.note_o_anomalie)):
                    card.note_o_anomalie = f"{fallback_msg} {card.note_o_anomalie or ''}".strip()
                    return card
            except Exception as e:
                self.session_logger.logger.error(f"[Gemini] Errore durante fallback extractor: {e}")

        return RawCardExtraction(
            note_o_anomalie=f"Limite quota Gemini esaurito (HTTP {status_code}): {err_text}"
        )
