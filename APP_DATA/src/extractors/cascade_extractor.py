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

from typing import Callable, Optional, Dict, Any, Tuple, List



from .base import BaseVisionExtractor, EXTRACTION_SYSTEM_PROMPT

from .gemini_extractor import GEMINI_RESPONSE_SCHEMA, GeminiVisionExtractor

from ..models import RawCardExtraction, BoxItem

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

        self.gemini_key_1 = self.k1

        self.gemini_key_2 = self.k2

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

        tier_label: str = "PIANO A - Google Gemini",

        custom_prompt: Optional[str] = None,

    ) -> Tuple[int, Dict[str, Any], str, float]:

        """Esegue una chiamata a Google Generative Language API."""

        m_name = model_name or self.gemini_model

        self.session_logger.log_ai_attempt(tier_label, "Google", m_name, key_label=key_label)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={api_key}"

        sys_instruction = EXTRACTION_SYSTEM_PROMPT

        if custom_prompt:

            sys_instruction += f"\n\nISTRUZIONI SPECIFICHE DI CONTROLLO MATEMATICO:\n{custom_prompt}"

        payload = {

            "system_instruction": {"parts": [{"text": sys_instruction}]},

            "contents": [

                {

                    "role": "user",

                    "parts": [

                        {"text": custom_prompt or "Analizza la scheda allegata."},

                        {"inline_data": {"mime_type": mime_type, "data": img_data}}

                    ]

                }

            ],

            "generationConfig": {

                "response_mime_type": "application/json",

                "response_schema": GEMINI_RESPONSE_SCHEMA,

                "temperature": 0.0,

                "maxOutputTokens": 4096,

                "thinkingConfig": {"thinkingBudget": 1024}

            }

        }

        t0 = time.time()

        try:

            resp = requests.post(url, json=payload, timeout=(5, 15))

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

            resp = requests.post(url, headers=headers, json=payload, timeout=(5, 20))

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

            declared_boxes = None

            if isinstance(val, dict):

                declared_kg = val.get("kg")

                declared_boxes = val.get("scatole")

            elif isinstance(val, (int, float)):

                declared_kg = float(val)



            calc_kg = calculated_kg.get(cat, 0.0)

            calc_boxes = calculated_box_count.get(cat, 0)



            kg_diff = False

            dec_kg_val = None

            if declared_kg is not None:

                try:

                    dec_kg_val = float(declared_kg)

                    if abs(calc_kg - dec_kg_val) > 0.01:

                        kg_diff = True

                except (ValueError, TypeError):

                    pass



            box_diff = False

            dec_box_val = None

            if declared_boxes is not None:

                try:

                    dec_box_val = int(declared_boxes)

                    if calc_boxes != dec_box_val:

                        box_diff = True

                except (ValueError, TypeError):

                    pass



            if kg_diff or box_diff:

                delta = round(dec_kg_val - calc_kg, 2) if dec_kg_val is not None else 0.0

                discrepancies.append({

                    "categoria": cat,

                    "tipo": "kg_mismatch" if kg_diff else "box_mismatch",

                    "calcolato": calc_kg,

                    "dichiarato": dec_kg_val if dec_kg_val is not None else calc_kg,

                    "scatole_trovate": calc_boxes,

                    "scatole_dichiarate": dec_box_val,

                    "delta_kg": delta

                })



        is_gemini_arb = bool(self.arbitrator_model and (self.arbitrator_model.startswith("gemini") or "gemini" in self.arbitrator_model.lower()))

        has_arb_key = bool(self.gemini_key_1 or self.gemini_key_2) if is_gemini_arb else bool(self.or_key)



        self.session_logger.log_math_validation(

            discrepancies,

            will_arbitrate=bool(has_arb_key and self.arbitrator_model and discrepancies and self.enable_auto_arbitrator),

            arbitrator_model=self.arbitrator_model,

        )

        return discrepancies



    @staticmethod
    def _is_plausible_box_reconciliation(old_boxes: List[BoxItem], new_boxes: List[BoxItem]) -> bool:
        """Verifica che la riconciliazione dell'arbitro non sia una fabbricazione forzata (es. 7 -> 12).
        
        Ritorna True se le modifiche sono calligraficamente plausibili, False se c'è un'alterazione non plausibile.
        """
        if not new_boxes:
            return False
        # Se il numero di scatole trovate è aumentato (ha recuperato una riga omessa), è plausibile
        if len(new_boxes) > len(old_boxes):
            return True
        
        # Confronta per posizione ordinale (1a riga, 2a riga, ecc.)
        for i in range(min(len(old_boxes), len(new_boxes))):
            old_val = old_boxes[i].kg or 0.0
            new_val = new_boxes[i].kg or 0.0
            if abs(new_val - old_val) < 0.01:
                continue
            
            # Se un valore cambia:
            # 1. Non è ammesso che cambi il numero di cifre intere (es. '7' (1 cifra) -> '12' (2 cifre)) quando old_val > 0
            if old_val > 0 and new_val > 0:
                old_digits = len(str(int(round(old_val))))
                new_digits = len(str(int(round(new_val))))
                if old_digits != new_digits:
                    return False
            
            # 2. Per numeri a cifra singola (< 10), una variazione assoluta superiore a 6 kg (es. 1 -> 8, 2 -> 9)
            # è quasi certamente un'allucinazione aritmetica per forzare la somma, a meno che non sia 1 vs 7
            if old_val < 10 and abs(new_val - old_val) > 6.0:
                return False
                
        return True

    def _arbitrate_with_gemini_flash(

        self,

        discrepancies: List[Dict[str, Any]],

        img_data: str,

        mime_type: str,

        raw: RawCardExtraction

    ) -> RawCardExtraction:

        """Esegue un secondo passaggio rapido e chirurgico con Google Gemini Flash (100% gratuito)

        sulle sole colonne che presentano discrepanze matematiche.

        Applica la sostituzione condizionale solo se la quadratura torna esatta (100%).

        """

        gem_key = self.gemini_key_1 or self.gemini_key_2 or self.k1 or self.k2

        if not gem_key or not discrepancies:

            return raw



        disc_details = []

        for d in discrepancies:

            cat = d["categoria"]

            calc_kg = d["calcolato"]

            dec_kg = d["dichiarato"]

            calc_sc = d["scatole_trovate"]

            dec_sc = d.get("scatole_dichiarate")

            delta = d.get("delta_kg", round(dec_kg - calc_kg, 2))



            info = f"- {cat}: somma attuale = {calc_kg} kg ({calc_sc} scatole) vs DICHIARATO = {dec_kg} kg"

            if dec_sc is not None:

                info += f" ({dec_sc} scatole dichiarate)"

            info += f" -> DELTA DA COLMARE = {delta:+.2f} kg."

            disc_details.append(info)



        prompt_arbitro = (
            "CONTROLLO ERRORI RAPIDO MOD. A:\n"
            "La prima scansione presenta discrepanze sui totali di queste categorie:\n"
            + "\n".join(disc_details) + "\n\n"
            "ISTRUZIONI CHIRURGICHE DI ANALISI DEI DUBBI CALLIGRAFICI:\n"
            "1. Esamina con attenzione SOLO le colonne indicate sopra.\n"
            "2. VERIFICA DUBBI E SCENARI CALLIGRAFICI:\n"
            "   - Cerca se in una casella c'è un'effettiva incertezza visiva o calligrafia ambigua/sporca con più letture possibili "
            "(es. 3 vs 4 aperto; 1 vs 7 con/senza trattino; 2 vs 4; 5 vs 6; 0 vs 8; cifre corrette o scarabocchiate).\n"
            "   - Confronta i diversi scenari di lettura: se tra le interpretazioni visivamente plausibili ce n'è una che fa "
            "coincidere esattamente la somma con il TOTALE KG (o n° scatole) dichiarato in calce dal volontario, "
            "adotta quella lettura più probabile.\n"
            "3. REGOLA FERREA DI CERTEZZA (ANTI-FORZATURA):\n"
            "   - Correggi una casella SOLO se esiste un'autentica ambiguità o incertezza grafica!\n"
            "   - Se un numero è scritto in modo chiaro e inequivocabile (es. un '10' netto, un '7' nitido, un '2' chiaro), "
            "NON ALTERARLO MAI (non trasformare un '10' in '9' o un '7' in '6') solo per far quadrare i conti. "
            "Se i numeri sono chiaramente quelli e il totale in calce è un'addizione errata del volontario, mantieni i numeri scritti fedelmente.\n"
            "4. NOTA ESPLICITA NEL JSON:\n"
            "   - Nel campo 'note_o_anomalie' del JSON spiega sinteticamente la tua deduzione, indicando la categoria, la riga, "
            "il dubbio riscontrato e perché è stata scelta quella cifra (es. 'Pasta riga 1a: cifra ambigua tra 3 e 4, adottato 4 per quadratura con totale 15 kg').\n"
            "5. Restituisci l'intero JSON con i valori corretti e la spiegazione in note_o_anomalie."
        )

        label = "Google 1" if (self.k1 or self.gemini_key_1) else "Google 2"

        model_to_use = self.gemini_model



        self.session_logger.log_ai_attempt("ARBITRO FLASH GRATUITO", "Google", model_to_use, key_label=label)

        t0 = time.time()

        status, res_json, err, dur = self._call_gemini(

            gem_key, img_data, mime_type, key_label=label,

            model_name=model_to_use, tier_label="ARBITRO FLASH GRATUITO",

            custom_prompt=prompt_arbitro

        )

        if status != 200:

            return raw



        candidates = res_json.get("candidates", [])

        txt = GeminiVisionExtractor._extract_text_from_candidates(candidates)

        arb_raw = self.parse_json_response(txt)

        if not arb_raw.righe_scatole:

            return raw



        # Validazione condizionale a quadratura esatta (100%)

        arb_calculated_kg: Dict[str, float] = {}

        arb_calculated_boxes: Dict[str, List[BoxItem]] = {}

        for item in arb_raw.righe_scatole:

            cat = normalize_category_name(item.categoria)

            arb_calculated_kg[cat] = round(arb_calculated_kg.get(cat, 0.0) + (item.kg or 0.0), 2)

            arb_calculated_boxes.setdefault(cat, []).append(item)



        resolved_categories = []

        current_boxes_by_cat: Dict[str, List[BoxItem]] = {}

        for item in raw.righe_scatole:

            c = normalize_category_name(item.categoria)

            current_boxes_by_cat.setdefault(c, []).append(item)



        for d in discrepancies:

            cat = d["categoria"]

            dec_kg = d["dichiarato"]

            dec_sc = d.get("scatole_dichiarate")



            new_sum = arb_calculated_kg.get(cat, 0.0)

            new_boxes = arb_calculated_boxes.get(cat, [])

            new_count = len(new_boxes)



            kg_matched = abs(new_sum - dec_kg) <= 0.01 if dec_kg is not None else False

            boxes_matched = (new_count == dec_sc) if dec_sc is not None else True



            if kg_matched and boxes_matched and new_boxes:
                old_boxes = current_boxes_by_cat.get(cat, [])
                if not self._is_plausible_box_reconciliation(old_boxes, new_boxes):
                    self.session_logger.logger.warning(
                        f"[ARBITRO FLASH] Riconciliazione forzata respinta per '{cat}': rilevata alterazione calligrafica non plausibile "
                        f"(es. cifre alterate per far coincidere la somma con un errore di calcolo del compilatore). Mantenuto valore reale scritto dal volontario."
                    )
                    continue

                current_boxes_by_cat[cat] = new_boxes

                resolved_categories.append(f"{cat} ({new_sum} kg, {new_count} sc)")

                self.session_logger.logger.info(

                    f"[ARBITRO FLASH] Quadratura esatta 100% per '{cat}': somma={new_sum} kg, scatole={new_count}."

                )



        if resolved_categories:

            updated_righe = []

            for cat_name, boxes in current_boxes_by_cat.items():

                updated_righe.extend(boxes)

            raw.righe_scatole = updated_righe



            if not raw.supermercato and arb_raw.supermercato:

                raw.supermercato = arb_raw.supermercato



            res_str = ", ".join(resolved_categories)

            gemini_notes = (arb_raw.note_o_anomalie or "").strip()
            res_str = ", ".join(resolved_categories)
            raw.note_o_anomalie = (raw.note_o_anomalie or "").strip()
            if gemini_notes and gemini_notes not in raw.note_o_anomalie:
                raw.note_o_anomalie = (raw.note_o_anomalie + f" [{gemini_notes}]").strip()
            raw.note_o_anomalie = (raw.note_o_anomalie + f" [Riconciliato da Arbitro Gemini Flash: {res_str}]").strip()

            self.session_logger.log_ai_result(

                "ARBITRO FLASH GRATUITO", "Google", model_to_use, 200, dur,

                tokens=res_json.get("usageMetadata", {}).get("totalTokenCount", 0),

                is_success=True, cost_usd=0.0

            )



        return raw



    def _arbitrate_discrepancies(self, discrepancies: List[Dict[str, Any]], img_data: str, mime_type: str, raw: RawCardExtraction) -> RawCardExtraction:

        """Invoca il modello arbitro configurato (Haiku, Luna, Sonnet, Gemini, ecc.) per risolvere le celle con somme incoerenti."""

        cat_names = [d["categoria"] for d in discrepancies]

        prompt = (
            f"ATTENZIONE: Si è riscontrata una discrepanza matematica sulle seguenti categorie: {', '.join(cat_names)}.\n"
            "Guarda con estrema precisione i numeri scritti a mano sia nelle righe delle scatole (1a-10a) sia nella riga TOTALE KG e TOT. N° SCATOLE.\n\n"
            "ISTRUZIONI PER L'ANALISI DEI DUBBI CALLIGRAFICI:\n"
            "1. ANALISI DEGLI SCENARI: Cerca se in una casella c'è un'effettiva incertezza visiva o calligrafia ambigua "
            "(es. 3 vs 4 aperto; 1 vs 7; 2 vs 4; 5 vs 6; 0 vs 8). Se tra le interpretazioni visivamente plausibili ce n'è una "
            "che fa quadrare esattamente la somma con il TOTALE KG dichiarato dal volontario, adotta quella lettura più probabile.\n"
            "2. REGOLA FERREA DI CERTEZZA (ANTI-FORZATURA): Correggi SOLO in caso di autentica ambiguità o incertezza calligrafica. "
            "Se un numero è scritto in modo chiaro e inequivocabile (es. un '10' netto, un '7' nitido), NON inventare MAI numeri né alterare cifre chiare "
            "(es. non trasformare un '7' in '12' o un '10' in '9') per forzare la somma. Se il volontario ha sbagliato il calcolo del totale a mente, "
            "mantieni i numeri scritti fedelmente.\n"
            "3. NOTA DEL RAGIONAMENTO: Nel campo 'note_o_anomalie' del JSON spiega brevemente quale dubbio calligrafico è stato risolto e perché.\n"
            "4. Restituisci l'intero JSON corretto e validato."
        )

        is_gemini_arb = bool(self.arbitrator_model and (self.arbitrator_model.startswith("gemini") or "gemini" in self.arbitrator_model.lower()))

        

        if is_gemini_arb:

            gem_key = self.gemini_key_1 or self.gemini_key_2

            if not gem_key:

                self.session_logger.log_arbitration_result(self.arbitrator_model, success=False, duration_sec=0.0, details="Chiave Google mancante per arbitro Gemini")

                return raw

            status, res_json, err, dur = self._call_gemini(

                gem_key, img_data, mime_type, key_label="Google Arbitro",

                model_name=self.arbitrator_model, tier_label="ARBITRO MATEMATICO",

                custom_prompt=prompt

            )

            if status == 200:

                candidates = res_json.get("candidates", [])

                if candidates:

                    parts = candidates[0].get("content", {}).get("parts", [{}])

                    txt = parts[0].get("text", "")

                    parsed_raw = self.parse_json_response(txt)

                    if parsed_raw.righe_scatole:

                        parsed_raw.tempo_elaborazione_sec = round(dur, 1)

                        parsed_raw.costo_usd = 0.0

                        arb_name = self.arbitrator_model.split("/")[-1].replace(":free", "").replace("-", " ").title()

                        parsed_raw.note_o_anomalie = (parsed_raw.note_o_anomalie or "") + f" [Arbitrato con successo da {arb_name} in {dur:.1f}s | Gratis]"

                        self.session_logger.log_arbitration_result(

                            self.arbitrator_model, success=True, duration_sec=dur, details="Discrepanze risolte matematicamente da Gemini"

                        )

                        return parsed_raw

                    else:

                        err_detail = parsed_raw.note_o_anomalie or "nessuna scatola estratta"

                        self.session_logger.log_arbitration_result(

                            self.arbitrator_model, success=False, duration_sec=dur, details=f"Parsing arbitro fallito: {err_detail}"

                        )

                        raw.note_o_anomalie = (raw.note_o_anomalie or "") + f" [Arbitro Gemini non valido ({err_detail})]"

                        return raw

            self.session_logger.log_arbitration_result(self.arbitrator_model, success=False, duration_sec=dur, details=err)

            return raw

        else:
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

                        arb_name = self.arbitrator_model.split("/")[-1].replace(":free", "").replace("-", " ").title()

                        haiku_raw.note_o_anomalie = (haiku_raw.note_o_anomalie or "") + f" [Arbitrato con successo da {arb_name} in {dur:.1f}s | {cost_str}]"

                        self.session_logger.log_arbitration_result(

                            self.arbitrator_model, success=True, duration_sec=dur, details="Discrepanze risolte matematicamente"

                        )

                        return haiku_raw

                    else:

                        err_detail = haiku_raw.note_o_anomalie or "nessuna scatola estratta"

                        self.session_logger.log_arbitration_result(

                            self.arbitrator_model, success=False, duration_sec=dur, details=f"Parsing arbitro fallito: {err_detail}"

                        )

                        raw.note_o_anomalie = (raw.note_o_anomalie or "") + f" [Arbitro non valido ({err_detail})]"

                        return raw

            self.session_logger.log_arbitration_result(self.arbitrator_model, success=False, duration_sec=dur, details=err)

            return raw



    # -------------------------------------------------------------------------

    # ANALISI ON-DEMAND CON MODELLO FORTE (Claude Sonnet 5.5) - solo su richiesta esplicita

    # -------------------------------------------------------------------------

    def extract_with_strong_model(self, image_path: str, model_name: str = "anthropic/claude-sonnet-5.5", status_callback: Optional[Callable[[str], None]] = None) -> RawCardExtraction:

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

        if status_callback:
            status_callback(f"🧠 Analisi con modello forte '{model_name}' in corso...")
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
    @staticmethod
    def _is_valid_raw_extraction(raw: Optional[RawCardExtraction]) -> bool:
        """Verifica se l'estrazione ottenuta dal tier e' valida o se contiene un errore fatale di parsing JSON."""
        if raw is None:
            return False
        # Se ha estratto righe di scatole, l'estrazione ha avuto successo
        if raw.righe_scatole and len(raw.righe_scatole) > 0:
            return True
        # Se non ci sono scatole, verifichiamo che non sia dovuto a un errore di parsing, sintassi o risposta vuota
        err_note = (raw.note_o_anomalie or "").lower()
        fatal_terms = (
            "impossibile interpretare json",
            "nessun oggetto json",
            "risposta vuota",
            "formato inatteso",
            "expecting",
            "jsondecodeerror",
            "delimiter",
        )
        if any(term in err_note for term in fatal_terms):
            return False
        # Se non c'e' errore di sintassi, la scheda e' lecitamente vuota
        return True





    # -------------------------------------------------------------------------

    # FLUSSO PRINCIPALE A CASCATA

    # -------------------------------------------------------------------------

    def extract_from_image(self, image_path: str, _is_retry_after_rotation: bool = False, status_callback: Optional[Callable[[str], None]] = None) -> RawCardExtraction:

        from pathlib import Path

        img_name = Path(image_path).name

        variant_label = "v2 (Gemini 3.8 Primario)" if self.variant == "v2" else "v1 (Standard)"

        self.session_logger.logger.info(f"[CASCADE] === Inizio elaborazione Cascata {variant_label}: '{img_name}' (retry_rotazione={_is_retry_after_rotation}) ===")
        if status_callback:
            status_callback(f"📡 Invio immagine a Cascata {variant_label}...")

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
                        parsed = self.parse_json_response(txt)
                        if not self._is_valid_raw_extraction(parsed):
                            self.session_logger.logger.warning(
                                f"[CASCADE v2] Risposta o JSON non valido da Gemini 3.8 ({label}): {parsed.note_o_anomalie}. Fallback in corso..."
                            )
                            continue
                        raw_result = parsed
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
                            parsed = self.parse_json_response(txt)
                            if not self._is_valid_raw_extraction(parsed):
                                self.session_logger.logger.warning(
                                    f"[CASCADE v2] Risposta o JSON non valido da Gemini 3.5 ({label}): {parsed.note_o_anomalie}. Fallback in corso..."
                                )
                                continue
                            raw_result = parsed
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
                        parsed = self.parse_json_response(txt)
                        if self._is_valid_raw_extraction(parsed):
                            raw_result = parsed
                            cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = cost
                            cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                            tier_used = f"Piano C: GPT-6 Luna (Low) | {dur:.1f}s | {cost_str}"
                            tokens = res_json.get("usage", {}).get("total_tokens", 0)
                            self.tracker.record_call("openrouter", self.buffer_model, 200, tokens, dur, is_fallback=True)
                        else:
                            self.session_logger.logger.warning(
                                f"[CASCADE v2] Risposta o JSON non valido da GPT-6 Luna: {parsed.note_o_anomalie}. Fallback a Haiku..."
                            )

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
                        parsed = self.parse_json_response(txt)
                        if self._is_valid_raw_extraction(parsed):
                            raw_result = parsed
                            cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = cost
                            cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                            tier_used = f"Piano D: Claude Haiku 5.5 (Low) | {dur:.1f}s | {cost_str}"
                            tokens = res_json.get("usage", {}).get("total_tokens", 0)
                            self.tracker.record_call("openrouter_haiku", self.haiku_model, 200, tokens, dur, is_fallback=True)
                        else:
                            self.session_logger.logger.warning(
                                f"[CASCADE v2] Risposta o JSON non valido da Claude Haiku 5.5: {parsed.note_o_anomalie}. Fallback a Nemotron..."
                            )

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
                        parsed = self.parse_json_response(txt)
                        if self._is_valid_raw_extraction(parsed):
                            raw_result = parsed
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = 0.0
                            tier_used = f"Piano E: Nemotron Free | {dur:.1f}s | Gratis"
                            tokens = res_json.get("usage", {}).get("total_tokens", 0)
                            self.tracker.record_call("openrouter_free", self.free_model, 200, tokens, dur, is_fallback=True)
                        else:
                            self.session_logger.logger.warning(
                                f"[CASCADE v2] Risposta o JSON non valido da Nemotron Free: {parsed.note_o_anomalie}"
                            )

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

                if status_callback:
                    status_callback(f"⏳ Analisi IA con Google {label} ({self.gemini_model})...")

                status, res_json, err, dur = self._call_gemini(

                    key, img_data, mime_type, key_label=label, model_name=self.gemini_model, tier_label="PIANO A - Google Gemini"

                )

                if status == 200:

                    try:

                        cands = res_json.get("candidates", [])

                        txt = GeminiVisionExtractor._extract_text_from_candidates(cands)
                        parsed = self.parse_json_response(txt)
                        if not self._is_valid_raw_extraction(parsed):
                            self.session_logger.logger.warning(
                                f"[CASCADE] Risposta o JSON non valido da Gemini 3.5 ({label}): {parsed.note_o_anomalie}. Fallback in corso..."
                            )
                            continue
                        raw_result = parsed
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
                if status_callback:
                    status_callback("🔄 Passaggio automatico a Piano B: GPT-6 Luna...")

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
                        parsed = self.parse_json_response(txt)
                        if self._is_valid_raw_extraction(parsed):
                            raw_result = parsed
                            cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = cost
                            cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                            tier_used = f"Piano B: GPT-6 Luna (Low) | {dur:.1f}s | {cost_str}"
                            tokens = res_json.get("usage", {}).get("total_tokens", 0)
                            self.tracker.record_call("openrouter", self.buffer_model, 200, tokens, dur, is_fallback=True)
                        else:
                            self.session_logger.logger.warning(
                                f"[CASCADE] Risposta o JSON non valido da GPT-6 Luna: {parsed.note_o_anomalie}. Fallback a Haiku..."
                            )

                    except Exception as ex:

                        self.session_logger.logger.error(f"[CASCADE] Errore parsing GPT-6 Luna: {ex}")



            # 3. PIANO C: OpenRouter Claude Haiku 5.5 (Low Reasoning)

            if raw_result is None and self.or_key:
                if status_callback:
                    status_callback("🔄 Passaggio automatico a Piano C: Claude Haiku...")

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
                        parsed = self.parse_json_response(txt)
                        if self._is_valid_raw_extraction(parsed):
                            raw_result = parsed
                            cost = float(res_json.get("usage", {}).get("cost") or 0.0)
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = cost
                            cost_str = f"${cost:.4f}" if cost > 0 else "Gratis"
                            tier_used = f"Piano C: Claude Haiku 5.5 (Low) | {dur:.1f}s | {cost_str}"
                            tokens = res_json.get("usage", {}).get("total_tokens", 0)
                            self.tracker.record_call("openrouter_haiku", self.haiku_model, 200, tokens, dur, is_fallback=True)
                        else:
                            self.session_logger.logger.warning(
                                f"[CASCADE] Risposta o JSON non valido da Claude Haiku 5.5: {parsed.note_o_anomalie}. Fallback a Nemotron..."
                            )

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
                        parsed = self.parse_json_response(txt)
                        if self._is_valid_raw_extraction(parsed):
                            raw_result = parsed
                            raw_result.tempo_elaborazione_sec = round(dur, 1)
                            raw_result.costo_usd = 0.0
                            tier_used = f"Piano D: Nemotron Free | {dur:.1f}s | Gratis"
                            tokens = res_json.get("usage", {}).get("total_tokens", 0)
                            self.tracker.record_call("openrouter_free", self.free_model, 200, tokens, dur, is_fallback=True)
                        else:
                            self.session_logger.logger.warning(
                                f"[CASCADE] Risposta o JSON non valido da Nemotron Free: {parsed.note_o_anomalie}"
                            )

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



        # Controllo matematico & Arbitraggio Rapido / Haiku
        try:
            discrepancies = self._validate_math(raw_result)
            if discrepancies:
                is_gemini_source = bool(tier_used and ("Gemini" in tier_used or "Google" in tier_used))
                gem_key = getattr(self, "gemini_key_1", None) or getattr(self, "gemini_key_2", None) or self.k1 or self.k2

                # 1. Se la scansione iniziale è di Gemini: facciamo un secondo passaggio al volo ultra-rapido
                # con Gemini SOLO sulle colonne con errori
                if is_gemini_source and gem_key:
                    if status_callback:
                        status_callback(f"⚡ Controllo rapido Gemini su {len(discrepancies)} discrepanze...")
                    raw_result = self._arbitrate_with_gemini_flash(discrepancies, img_data, mime_type, raw_result)
                    discrepancies = self._validate_math(raw_result)

                # 2. Se rimangono discrepanze (oppure se Gemini era fallito ed è stata usata Luna/OpenRouter):
                # passiamo DIRETTAMENTE all'Arbitro configurato (es. Haiku).
                # Niente rimbalzi o ulteriori tentativi con Gemini.
                if discrepancies:
                    disc_text = "; ".join([f"{d['categoria']} (somma={d['calcolato']} kg, dichiarato={d['dichiarato']} kg)" for d in discrepancies])
                    is_gemini_arb = bool(self.arbitrator_model and (self.arbitrator_model.startswith("gemini") or "gemini" in self.arbitrator_model.lower()))
                    has_arb_key = bool(gem_key) if is_gemini_arb else bool(self.or_key)

                    if has_arb_key and self.arbitrator_model and self.enable_auto_arbitrator:
                        if status_callback:
                            arb_display = self.arbitrator_model.split("/")[-1].replace(":free", "").replace("-", " ").title()
                            status_callback(f"🦅 Arbitraggio con {arb_display}...")
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

