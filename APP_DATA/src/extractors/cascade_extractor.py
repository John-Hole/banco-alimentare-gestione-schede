"""Estrattore a Cascata Multi-Tier (CascadeVisionExtractor) per Banco Alimentare.

Supporta sia la modalità v1 che la modalità v2:
- CASCATA v1 (Standard Consigliata):
  * Piano A (100% Gratis): Google Gemini 3.5 Flash-Lite (load-balanced su 2 chiavi API).
  * Piano B (Buffer Economico): OpenRouter con openai/gpt-6-luna (Low Reasoning, ~$0.0016).
  * Piano C (Buffer Sicurezza): OpenRouter con anthropic/claude-haiku-5.5 (Low Reasoning, ~$0.0025).
  * Piano D (Buffer Emergenza 100% Gratis): OpenRouter con nvidia/nemotron-3.5-super-vl:free.
  * Arbitro Matematico: anthropic/claude-haiku-5.5 (Low Reasoning, ~$0.0025) — interviene SOLO su discrepanze.

- CASCATA v2 (Gemini 3.8 Primario):
  * Piano A (100% Gratis): Google Gemini 3.8 Flash (Chiave 1 ➔ Chiave 2).
  * Piano B (100% Gratis): Google Gemini 3.5 Flash-Lite (Chiave 1 ➔ Chiave 2).
  * Piano C (Buffer Economico): OpenRouter con openai/gpt-6-luna (Low Reasoning, ~$0.0016).
  * Piano D (Buffer Sicurezza): OpenRouter con anthropic/claude-haiku-5.5 (Low Reasoning, ~$0.0025).
  * Piano E (Buffer Emergenza 100% Gratis): OpenRouter con nvidia/nemotron-3.5-super-vl:free.
  * Arbitro Matematico: anthropic/claude-haiku-5.5 (Low Reasoning, ~$0.0025) — interviene SOLO su discrepanze.

Claude Sonnet 5.5 viene invocato SOLO su selezione manuale esplicita dell'utente.
"""

import json
import time
import requests
from typing import Optional, Dict, Any, Tuple, List

from .base import BaseVisionExtractor, EXTRACTION_SYSTEM_PROMPT
from .gemini_extractor import GEMINI_RESPONSE_SCHEMA, GeminiVisionExtractor
from ..models import RawCardExtraction
from ..config import (
    DEFAULT_CASCADE_GEMINI_MODEL,
    DEFAULT_CASCADE_V2_PRIMARY_MODEL,
    DEFAULT_CASCADE_BUFFER_MODEL,
    DEFAULT_CASCADE_HAIKU_MODEL,
    DEFAULT_CASCADE_FREE_MODEL,
    DEFAULT_CASCADE_ARBITRATOR_MODEL,
    normalize_category_name,
)
from ..services.quota_tracker import get_quota_tracker
from ..services.session_logger import get_session_logger


class CascadeVisionExtractor(BaseVisionExtractor):
    """Motore di estrazione a cascata multi-tier con failover automatico e arbitraggio matematico."""

    def __init__(
        self,
        gemini_api_key_1: str = "",
        gemini_api_key_2: str = "",
        openrouter_api_key: str = "",
        gemini_model: str = DEFAULT_CASCADE_GEMINI_MODEL,
        gemini_38_model: str = DEFAULT_CASCADE_V2_PRIMARY_MODEL,
        buffer_model: str = DEFAULT_CASCADE_BUFFER_MODEL,
        haiku_model: str = DEFAULT_CASCADE_HAIKU_MODEL,
        free_model: str = DEFAULT_CASCADE_FREE_MODEL,
        arbitrator_model: str = DEFAULT_CASCADE_ARBITRATOR_MODEL,
        enable_auto_arbitrator: bool = True,
        quota_tracker: Optional[Any] = None,
        free_fallback_model: Optional[str] = None,
        variant: str = "v1",
        **kwargs,
    ):
        self.k1 = (gemini_api_key_1 or "").strip()
        self.k2 = (gemini_api_key_2 or "").strip()
        self.or_key = (openrouter_api_key or "").strip()

        self.variant = (variant or "v1").lower().strip()
        self.gemini_model = gemini_model
        self.gemini_38_model = gemini_38_model
        self.buffer_model = buffer_model

        # Supporto retrocompatibile per haiku_model e free_model (Nemotron Free)
        passed_free = free_fallback_model or free_model
        if "haiku" in (passed_free or "").lower():
            self.haiku_model = passed_free
            self.free_model = DEFAULT_CASCADE_FREE_MODEL
        else:
            self.haiku_model = haiku_model or DEFAULT_CASCADE_HAIKU_MODEL
            self.free_model = passed_free or DEFAULT_CASCADE_FREE_MODEL

        self.arbitrator_model = arbitrator_model
        self.enable_auto_arbitrator = bool(enable_auto_arbitrator or kwargs.get("enable_paid_arbitration", False))

        self.tracker = quota_tracker if quota_tracker is not None else get_quota_tracker()
        self.session_logger = get_session_logger()
        self._turn = 0  # Per round-robin tra chiave 1 e chiave 2

    # -------------------------------------------------------------------------
    # CHIAMATE DI RETE AI VARI PIANI
    # -------------------------------------------------------------------------
    def _call_gemini(
        self,
        api_key: str,
        img_data: str,
        mime_type: str,
        key_label: str = "Google",
        model_name: Optional[str] = None,
        tier_label: str = "PIANO A - Google Gemini"
    ) -> Tuple[int, Dict[str, Any], str, float]:
        """Esegue una chiamata a Google Generative Language API."""
        m_name = model_name or self.gemini_model
        self.session_logger.log_ai_attempt(tier_label, "Google", m_name, key_label=key_label)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": EXTRACTION_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Analizza la scheda allegata."},
                        {"inline_data": {"mime_type": mime_type, "data": img_data}}
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
                self.session_logger.log_ai_result(
                    tier_label, "Google", m_name, resp.status_code, duration,
                    is_success=False, error_msg=resp.text[:300]
                )
                return resp.status_code, {}, resp.text, duration
            data = resp.json()
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
            self.session_logger.log_ai_result(
                tier_label, "Google", m_name, 200, duration,
                tokens=tokens, is_success=True, cost_usd=0.0
            )
            return 200, data, "", duration
        except requests.exceptions.Timeout:
            dur = time.time() - t0
            self.session_logger.log_ai_result(
                tier_label, "Google", m_name, 408, dur,
                is_success=False, error_msg=f"Timeout Google Gemini {m_name}"
            )
            return 408, {}, f"Timeout Google Gemini {m_name}", dur
        except Exception as e:
            dur = time.time() - t0
            self.session_logger.log_ai_result(
                tier_label, "Google", m_name, 500, dur,
                is_success=False, error_msg=str(e)
            )
            return 500, {}, str(e), dur

    def _call_openrouter(
        self,
        model: str,
        img_data: str,
        mime_type: str,
        tier_label: str = "OpenRouter",
        custom_prompt: Optional[str] = None
    ) -> Tuple[int, Dict[str, Any], str, float]:
        """Esegue una chiamata a OpenRouter API (Piano B, C o Arbitro)."""
        if not self.or_key:
            self.session_logger.log_ai_result(tier_label, "OpenRouter", model, 401, 0.0, is_success=False, error_msg="Chiave OpenRouter mancante")
            return 401, {}, "Chiave OpenRouter mancante", 0.0

        self.session_logger.log_ai_attempt(tier_label, "OpenRouter", model)
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.or_key}",
            "HTTP-Referer": "https://bancoalimentare.it",
            "X-Title": "Banco Alimentare Colletta",
            "Content-Type": "application/json"
        }
        user_prompt = custom_prompt or "Analizza l'immagine della scheda allegata ed estrai tutte le informazioni nel formato JSON specificato."
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{img_data}"}}
                    ]
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "max_tokens": 4096
        }
        if "nemotron" in model.lower():
            payload["reasoning"] = {"effort": "none"}
        elif "luna" in model.lower() or "haiku" in model.lower():
            payload["reasoning"] = {"effort": "low"}

        t0 = time.time()
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(8, 30))
            duration = time.time() - t0
            if resp.status_code != 200:
                self.session_logger.log_ai_result(
                    tier_label, "OpenRouter", model, resp.status_code, duration,
                    is_success=False, error_msg=resp.text[:300]
                )
                return resp.status_code, {}, resp.text, duration
            data = resp.json()
            tokens = data.get("usage", {}).get("total_tokens", 0)
            cost = float(data.get("usage", {}).get("cost") or 0.0)
            self.session_logger.log_ai_result(
                tier_label, "OpenRouter", model, 200, duration,
                tokens=tokens, is_success=True, cost_usd=cost
            )
            return 200, data, "", duration
        except requests.exceptions.Timeout:
            dur = time.time() - t0
            self.session_logger.log_ai_result(
                tier_label, "OpenRouter", model, 408, dur,
                is_success=False, error_msg=f"Timeout OpenRouter {model}"
            )
            return 408, {}, f"Timeout OpenRouter {model}", dur
        except Exception as e:
            dur = time.time() - t0
            self.session_logger.log_ai_result(
                tier_label, "OpenRouter", model, 500, dur,
                is_success=False, error_msg=str(e)
            )
            return 500, {}, str(e), dur

    # -------------------------------------------------------------------------
    # VALIDAZIONE MATEMATICA E ARBITRAGGIO
    # -------------------------------------------------------------------------
    def _validate_math(self, raw: RawCardExtraction) -> List[Dict[str, Any]]:
        """Verifica la coerenza tra somma dei kg delle scatole e totale dichiarato per ciascuna colonna."""
        discrepancies = []
        if not raw.righe_scatole:
            return discrepancies

        calculated_kg: Dict[str, float] = {}
        calculated_box_count: Dict[str, int] = {}

        for item in raw.righe_scatole:
            cat = normalize_category_name(item.categoria)
            calculated_kg[cat] = round(calculated_kg.get(cat, 0.0) + (item.kg or 0.0), 2)
            calculated_box_count[cat] = calculated_box_count.get(cat, 0) + 1

        per_cat = raw.totali_dichiarati.per_categoria if raw.totali_dichiarati else {}
        for cat_raw, val in per_cat.items():
            cat = normalize_category_name(cat_raw)
            declared_kg = None
            if isinstance(val, dict):
                declared_kg = val.get("kg")
            elif isinstance(val, (int, float)):
                declared_kg = float(val)

            calc_kg = calculated_kg.get(cat, 0.0)
            if declared_kg is not None:
                try:
                    dec_val = float(declared_kg)
                    if abs(calc_kg - dec_val) > 0.01:
                        discrepancies.append({
                            "categoria": cat,
                            "tipo": "kg_mismatch",
                            "calcolato": calc_kg,
                            "dichiarato": dec_val,
                            "scatole_trovate": calculated_box_count.get(cat, 0),
                        })
                except (ValueError, TypeError):
                    pass

        self.session_logger.log_math_validation(
            discrepancies,
            will_arbitrate=bool(self.or_key and self.arbitrator_model and discrepancies and self.enable_auto_arbitrator),
            arbitrator_model=self.arbitrator_model,
        )
        return discrepancies

    def _arbitrate_discrepancies(self, discrepancies: List[Dict[str, Any]], img_data: str, mime_type: str, raw: RawCardExtraction) -> RawCardExtraction:
        """Invoca Claude Haiku 5.5 per risolvere in modo chirurgico ed economico le celle con somme incoerenti."""
        cat_names = [d["categoria"] for d in discrepancies]
        prompt = (
            f"ATTENZIONE: Si è riscontrata una discrepanza matematica sulle seguenti categorie: {', '.join(cat_names)}.\n"
            "Guarda con estrema precisione i numeri scritti a mano sia nelle righe delle scatole (1a-10a) sia nella riga TOTALE KG e TOT. N° SCATOLE.\n"
            "Restituisci l'intero JSON corretto e validato matematicamente."
        )
        status, res_json, err, dur = self._call_openrouter(
            self.arbitrator_model, img_data, mime_type, tier_label="ARBITRO MATEMATICO", custom_prompt=prompt
        )
        if status == 200:
            choices = res_json.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                haiku_raw = self.parse_json_response(txt)
                if haiku_raw.righe_scatole:
                    cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                    haiku_raw.tempo_elaborazione_sec = round(dur, 1)
                    haiku_raw.costo_usd = cost
                    cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                    haiku_raw.note_o_anomalie = (haiku_raw.note_o_anomalie or "") + f" [Arbitrato con successo da Claude Haiku 5.5 in {dur:.1f}s | {cost_str}]"
                    self.session_logger.log_arbitration_result(
                        self.arbitrator_model, success=True, duration_sec=dur, details="Discrepanze risolte matematicamente"
                    )
                    return haiku_raw
                else:
                    err_detail = haiku_raw.note_o_anomalie or "nessuna scatola estratta"
                    self.session_logger.log_arbitration_result(
                        self.arbitrator_model, success=False, duration_sec=dur, details=f"Parsing arbitro fallito: {err_detail}"
                    )
                    raw.note_o_anomalie = (raw.note_o_anomalie or "") + f" [Arbitro Haiku non valido ({err_detail})]"
                    return raw
        self.session_logger.log_arbitration_result(self.arbitrator_model, success=False, duration_sec=dur, details=err)
        return raw

    # -------------------------------------------------------------------------
    # ANALISI ON-DEMAND CON MODELLO FORTE (Claude Sonnet 5.5) - solo su richiesta esplicita
    # -------------------------------------------------------------------------
    def extract_with_strong_model(self, image_path: str, model_name: str = "anthropic/claude-sonnet-5.5") -> RawCardExtraction:
        """Esegue una chiamata esclusiva a Claude Sonnet 5.5 su richiesta manuale dell'utente."""
        from pathlib import Path
        img_name = Path(image_path).name
        self.session_logger.logger.info(f"[CASCADE] === Analisi ON-DEMAND manuale con modello forte '{model_name}': '{img_name}' ===")
        if not self.or_key:
            return RawCardExtraction(note_o_anomalie="Errore chiamata: chiave OpenRouter non configurata")
        try:
            mime_type, img_data = self.encode_and_optimize_image(image_path)
        except Exception as e:
            return RawCardExtraction(note_o_anomalie=f"Impossibile leggere/ottimizzare immagine: {e}")
        status, res_json, err, dur = self._call_openrouter(
            model_name, img_data, mime_type, tier_label="ON-DEMAND - Claude Sonnet 5.5"
        )
        if status != 200:
            return RawCardExtraction(note_o_anomalie=f"Errore OpenRouter modello forte: {err[:200]}")
        try:
            msg = res_json.get("choices", [{}])[0].get("message", {})
            txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
            raw = self.parse_json_response(txt)
        except Exception as e:
            return RawCardExtraction(note_o_anomalie=f"Errore parsing modello forte: {e}")
        cost = float(res_json.get("usage", {}).get("cost") or 0.0)
        raw.tempo_elaborazione_sec = round(dur, 1)
        raw.costo_usd = cost
        cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
        try:
            discrepancies = self._validate_math(raw)
            if discrepancies:
                disc_text = "; ".join([f"{d['categoria']} (somma={d['calcolato']} kg, dichiarato={d['dichiarato']} kg)" for d in discrepancies])
                raw.note_o_anomalie = (raw.note_o_anomalie or "") + f" [Discrepanza matematica: {disc_text}]"
        except Exception:
            pass
        raw.note_o_anomalie = f"[On-demand: Claude Sonnet 5.5 | {dur:.1f}s | {cost_str}] {raw.note_o_anomalie or ''}".strip()
        return raw

    # -------------------------------------------------------------------------
    # FLUSSO PRINCIPALE A CASCATA
    # -------------------------------------------------------------------------
    def extract_from_image(self, image_path: str, _is_retry_after_rotation: bool = False) -> RawCardExtraction:
        from pathlib import Path
        img_name = Path(image_path).name
        variant_label = "v2 (Gemini 3.8 Primario)" if self.variant == "v2" else "v1 (Standard)"
        self.session_logger.logger.info(f"[CASCADE] === Inizio elaborazione Cascata {variant_label}: '{img_name}' (retry_rotazione={_is_retry_after_rotation}) ===")
        try:
            mime_type, img_data = self.encode_and_optimize_image(image_path)
        except Exception as e:
            err_msg = f"Impossibile leggere/ottimizzare immagine: {e}"
            self.session_logger.logger.error(f"[CASCADE] {err_msg}")
            return RawCardExtraction(note_o_anomalie=err_msg)

        tier_used = "Nessuno"
        raw_result: Optional[RawCardExtraction] = None

        keys_to_try = []
        if self.k1 and self.k2:
            if self._turn % 2 == 0:
                keys_to_try = [(self.k1, "Google 1"), (self.k2, "Google 2")]
            else:
                keys_to_try = [(self.k2, "Google 2"), (self.k1, "Google 1")]
            self._turn += 1
        elif self.k1:
            keys_to_try = [(self.k1, "Google 1")]
        elif self.k2:
            keys_to_try = [(self.k2, "Google 2")]

        if self.variant == "v2":
            # =========================================================
            # CASCATA V2: Gemini 3.8 ➔ Gemini 3.5 ➔ GPT-6 Luna ➔ Haiku
            # =========================================================

            # 1. PIANO A (v2): Google Gemini 3.8 Flash
            for key, label in keys_to_try:
                prov_name = "gemini_1" if "1" in label else "gemini_2"
                if self.tracker.is_exhausted(prov_name, self.gemini_38_model):
                    continue
                status, res_json, err, dur = self._call_gemini(
                    key, img_data, mime_type, key_label=label, model_name=self.gemini_38_model, tier_label="PIANO A (v2) - Gemini 3.8 Flash"
                )
                if status == 200:
                    try:
                        cands = res_json.get("candidates", [])
                        txt = GeminiVisionExtractor._extract_text_from_candidates(cands)
                        raw_result = self.parse_json_response(txt)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = 0.0
                        tier_used = f"Piano A: {label} (Gemini 3.8 Flash | {dur:.1f}s | Gratis)"
                        tokens = res_json.get("usageMetadata", {}).get("totalTokenCount", 0)
                        self.tracker.record_call(prov_name, self.gemini_38_model, 200, tokens, dur, is_fallback=False)
                        break
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE v2] Errore parsing Gemini 3.8: {ex}")
                if status == 429:
                    self.tracker.record_call(prov_name, self.gemini_38_model, 429, 0, dur, is_fallback=False)

            # 2. PIANO B (v2): Google Gemini 3.5 Flash-Lite
            if raw_result is None:
                self.session_logger.log_ai_fallback(
                    "Piano A (Gemini 3.8 Flash)", "Piano B (Gemini 3.5 Flash-Lite)",
                    self.gemini_model, reason="Gemini 3.8 non disponibile (503/408/quota)"
                )
                for key, label in keys_to_try:
                    prov_name = "gemini_1" if "1" in label else "gemini_2"
                    if self.tracker.is_exhausted(prov_name, self.gemini_model):
                        continue
                    status, res_json, err, dur = self._call_gemini(
                        key, img_data, mime_type, key_label=label, model_name=self.gemini_model, tier_label="PIANO B (v2) - Gemini 3.5 Flash-Lite"
                    )
                    if status == 200:
                        try:
                            cands = res_json.get("candidates", [])
                            txt = GeminiVisionExtractor._extract_text_from_candidates(cands)
                            raw_result = self.parse_json_response(txt)
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = 0.0
                            tier_used = f"Piano B: {label} (Gemini 3.5 Lite | {dur:.1f}s | Gratis)"
                            tokens = res_json.get("usageMetadata", {}).get("totalTokenCount", 0)
                            self.tracker.record_call(prov_name, self.gemini_model, 200, tokens, dur, is_fallback=True)
                            break
                        except Exception as ex:
                            self.session_logger.logger.error(f"[CASCADE v2] Errore parsing Gemini 3.5: {ex}")
                    if status == 429:
                        self.tracker.record_call(prov_name, self.gemini_model, 429, 0, dur, is_fallback=True)

            # 3. PIANO C (v2): OpenRouter GPT-6 Luna (Low Reasoning)
            if raw_result is None and self.or_key:
                self.session_logger.log_ai_fallback(
                    "Piano B (Gemini 3.5 Flash-Lite)", "Piano C (GPT-6 Luna)",
                    self.buffer_model, reason="Server o quote Google non disponibili"
                )
                status, res_json, err, dur = self._call_openrouter(
                    self.buffer_model, img_data, mime_type, tier_label="PIANO C (v2) - GPT-6 Luna"
                )
                if status == 200:
                    try:
                        msg = res_json.get("choices", [{}])[0].get("message", {})
                        txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                        raw_result = self.parse_json_response(txt)
                        cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = cost
                        cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                        tier_used = f"Piano C: GPT-6 Luna (Low) | {dur:.1f}s | {cost_str}"
                        tokens = res_json.get("usage", {}).get("total_tokens", 0)
                        self.tracker.record_call("openrouter", self.buffer_model, 200, tokens, dur, is_fallback=True)
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE v2] Errore parsing GPT-6 Luna: {ex}")

            # 4. PIANO D (v2): OpenRouter Claude Haiku 5.5 (Low Reasoning)
            if raw_result is None and self.or_key:
                self.session_logger.log_ai_fallback(
                    "Piano C (GPT-6 Luna)", "Piano D (Claude Haiku 5.5)",
                    self.haiku_model, reason="Piano C non riuscito o timeout"
                )
                status, res_json, err, dur = self._call_openrouter(
                    self.haiku_model, img_data, mime_type, tier_label="PIANO D (v2) - Claude Haiku 5.5"
                )
                if status == 200:
                    try:
                        msg = res_json.get("choices", [{}])[0].get("message", {})
                        txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                        raw_result = self.parse_json_response(txt)
                        cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = cost
                        cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                        tier_used = f"Piano D: Claude Haiku 5.5 (Low) | {dur:.1f}s | {cost_str}"
                        tokens = res_json.get("usage", {}).get("total_tokens", 0)
                        self.tracker.record_call("openrouter_haiku", self.haiku_model, 200, tokens, dur, is_fallback=True)
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE v2] Errore parsing Claude Haiku: {ex}")

            # 5. PIANO E (v2): OpenRouter Nemotron 3.5 Super-VL (100% Gratis di emergenza)
            if raw_result is None and self.or_key:
                self.session_logger.log_ai_fallback(
                    "Piano D (Claude Haiku 5.5)", "Piano E (Nemotron Free)",
                    self.free_model, reason="Piano D non riuscito o crediti OpenRouter esauriti"
                )
                status, res_json, err, dur = self._call_openrouter(
                    self.free_model, img_data, mime_type, tier_label="PIANO E (v2) - Nemotron Free"
                )
                if status == 200:
                    try:
                        msg = res_json.get("choices", [{}])[0].get("message", {})
                        txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                        raw_result = self.parse_json_response(txt)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = 0.0
                        tier_used = f"Piano E: Nemotron Free | {dur:.1f}s | Gratis"
                        tokens = res_json.get("usage", {}).get("total_tokens", 0)
                        self.tracker.record_call("openrouter_free", self.free_model, 200, tokens, dur, is_fallback=True)
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE v2] Errore parsing Nemotron Free: {ex}")

        else:
            # =========================================================
            # CASCATA V1 (Standard): Gemini 3.5 ➔ GPT-6 Luna ➔ Haiku ➔ Nemotron Free
            # =========================================================

            # 1. PIANO A: Google Gemini 3.5 Flash-Lite (Round-robin Chiave 1 e Chiave 2)
            for key, label in keys_to_try:
                prov_name = "gemini_1" if "1" in label else "gemini_2"
                if self.tracker.is_exhausted(prov_name, self.gemini_model):
                    continue
                status, res_json, err, dur = self._call_gemini(
                    key, img_data, mime_type, key_label=label, model_name=self.gemini_model, tier_label="PIANO A - Google Gemini"
                )
                if status == 200:
                    try:
                        cands = res_json.get("candidates", [])
                        txt = GeminiVisionExtractor._extract_text_from_candidates(cands)
                        raw_result = self.parse_json_response(txt)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = 0.0
                        tier_used = f"Piano A: {label} (Gemini 3.5 Lite | {dur:.1f}s | Gratis)"
                        tokens = res_json.get("usageMetadata", {}).get("totalTokenCount", 0)
                        self.tracker.record_call(prov_name, self.gemini_model, 200, tokens, dur, is_fallback=False)
                        break
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE] Errore parsing Gemini 3.5: {ex}")
                if status == 429:
                    self.tracker.record_call(prov_name, self.gemini_model, 429, 0, dur, is_fallback=False)

            # 2. PIANO B: OpenRouter GPT-6 Luna (Low Reasoning)
            if raw_result is None and self.or_key:
                self.session_logger.log_ai_fallback(
                    "Piano A (Google Gemini)", "Piano B (GPT-6 Luna)",
                    self.buffer_model, reason="Quote o server Google non disponibili (503/408/429)"
                )
                status, res_json, err, dur = self._call_openrouter(
                    self.buffer_model, img_data, mime_type, tier_label="PIANO B - GPT-6 Luna"
                )
                if status == 200:
                    try:
                        msg = res_json.get("choices", [{}])[0].get("message", {})
                        txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                        raw_result = self.parse_json_response(txt)
                        cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = cost
                        cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                        tier_used = f"Piano B: GPT-6 Luna (Low) | {dur:.1f}s | {cost_str}"
                        tokens = res_json.get("usage", {}).get("total_tokens", 0)
                        self.tracker.record_call("openrouter", self.buffer_model, 200, tokens, dur, is_fallback=True)
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE] Errore parsing GPT-6 Luna: {ex}")

            # 3. PIANO C: OpenRouter Claude Haiku 5.5 (Low Reasoning)
            if raw_result is None and self.or_key:
                self.session_logger.log_ai_fallback(
                    "Piano B (GPT-6 Luna)", "Piano C (Claude Haiku 5.5)",
                    self.haiku_model, reason="Piano B non riuscito o timeout"
                )
                status, res_json, err, dur = self._call_openrouter(
                    self.haiku_model, img_data, mime_type, tier_label="PIANO C - Claude Haiku 5.5"
                )
                if status == 200:
                    try:
                        msg = res_json.get("choices", [{}])[0].get("message", {})
                        txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                        raw_result = self.parse_json_response(txt)
                        cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = cost
                        cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                        tier_used = f"Piano C: Claude Haiku 5.5 (Low) | {dur:.1f}s | {cost_str}"
                        tokens = res_json.get("usage", {}).get("total_tokens", 0)
                        self.tracker.record_call("openrouter_haiku", self.haiku_model, 200, tokens, dur, is_fallback=True)
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE] Errore parsing Claude Haiku: {ex}")

            # 4. PIANO D: OpenRouter Nemotron 3.5 Super-VL (100% Gratis di emergenza)
            if raw_result is None and self.or_key:
                self.session_logger.log_ai_fallback(
                    "Piano C (Claude Haiku 5.5)", "Piano D (Nemotron Free)",
                    self.free_model, reason="Piano C non riuscito o crediti OpenRouter esauriti"
                )
                status, res_json, err, dur = self._call_openrouter(
                    self.free_model, img_data, mime_type, tier_label="PIANO D - Nemotron Free"
                )
                if status == 200:
                    try:
                        msg = res_json.get("choices", [{}])[0].get("message", {})
                        txt = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or ""
                        raw_result = self.parse_json_response(txt)
                        raw_result.tempo_elaborazione_sec = round(dur, 1)
                        raw_result.costo_usd = 0.0
                        tier_used = f"Piano D: Nemotron Free | {dur:.1f}s | Gratis"
                        tokens = res_json.get("usage", {}).get("total_tokens", 0)
                        self.tracker.record_call("openrouter_free", self.free_model, 200, tokens, dur, is_fallback=True)
                    except Exception as ex:
                        self.session_logger.logger.error(f"[CASCADE] Errore parsing Nemotron Free: {ex}")

        # Se nessun livello ha risposto
        if raw_result is None:
            self.session_logger.logger.error("[CASCADE] Tutti i piani della cascata sono falliti o le chiavi sono mancanti/esaurite.")
            return RawCardExtraction(
                note_o_anomalie="Tutti i piani della cascata sono falliti o le chiavi sono mancanti/esaurite."
            )

        # Gestione orientamento a 180°
        if raw_result.scheda_capovolta and not _is_retry_after_rotation:
            self.session_logger.logger.warning(
                f"[CASCADE] Scheda '{img_name}' rilevata capovolta (180°) dall'IA: "
                "applico rotazione fisica di 180° su disco e ri-eseguo l'analisi a orientamento corretto."
            )
            try:
                from ..scanner.scanner_service import ScannerService
                ScannerService.rotate_image_file_180(image_path)
                retry_result = self.extract_from_image(image_path, _is_retry_after_rotation=True)
                retry_result.was_rotated_180 = True
                return retry_result
            except Exception as e:
                self.session_logger.logger.error(f"[CASCADE] Errore durante rotazione 180° e re-analisi: {e}")

        # Controllo matematico & arbitraggio con Claude Haiku 5.5
        try:
            discrepancies = self._validate_math(raw_result)
            if discrepancies:
                disc_text = "; ".join([f"{d['categoria']} (somma={d['calcolato']} kg, dichiarato={d['dichiarato']} kg)" for d in discrepancies])
                if self.or_key and self.arbitrator_model and self.enable_auto_arbitrator:
                    raw_result = self._arbitrate_discrepancies(discrepancies, img_data, mime_type, raw_result)
                else:
                    raw_result.note_o_anomalie = (raw_result.note_o_anomalie or "") + f" [Discrepanza matematica: {disc_text}]"
                    self.session_logger.logger.info(
                        f"[CASCADE] Discrepanza matematica rilevata su {len(discrepancies)} categorie: delegata all'operatore."
                    )
        except Exception as e:
            self.session_logger.logger.warning(f"[CASCADE] Avviso validazione matematica cascata: {e}")

        # Aggiunta badge vincitore
        note_prefix = f"[{tier_used}]"
        if note_prefix not in (raw_result.note_o_anomalie or ""):
            raw_result.note_o_anomalie = f"{note_prefix} {raw_result.note_o_anomalie or ''}".strip()

        tot_kg = sum(b.kg for b in raw_result.righe_scatole)
        self.session_logger.log_extraction_complete(
            tier_winner=tier_used,
            supermarket=raw_result.supermercato,
            scheda_num=raw_result.numero_scheda,
            box_count=len(raw_result.righe_scatole),
            total_kg=tot_kg,
            upside_down=raw_result.scheda_capovolta,
            notes=raw_result.note_o_anomalie,
        )
        return raw_result
