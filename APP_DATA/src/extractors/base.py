"""Interfaccia base per i provider di estrazione dati da immagine con prompt standardizzato."""

import json
import re
from abc import ABC, abstractmethod
from typing import Callable, Optional

from ..models import RawCardExtraction, DeclaredTotals, BoxItem
from ..config import CATEGORIES, normalize_category_name


EXTRACTION_SYSTEM_PROMPT = """Sei un assistente OCR di altissima precisione per l'analisi visiva delle schede cartacee "MOD. A - SCHEDA ANALITICA DONAZIONI E DI TRASPORTO BENI DONATI" della Giornata Nazionale della Colletta Alimentare (Banco Alimentare).

Il documento è una tabella a griglia stampata relativa al carico di un singolo furgone/trasporto.

STRUTTURA DEL DOCUMENTO:

1. INTESTAZIONE:
   - Supermercato: nome, città e indirizzo del punto vendita (stampato o timbrato, es. "TIGRE - PERUGIA STRADA PONTE D'ODDI, 8/A").
     * REGOLA TASSATIVA: Riporta SEMPRE il testo del supermercato ESCLUSIVAMENTE nella chiave JSON "supermercato" (MAI e POI MAI in "note_o_anomalie" o altrove)! Se il riquadro è leggibile, "supermercato" DEVE contenere la stringa non-nulla. Se non presente o illeggibile: null.
   - Scheda N°: casella in alto a destra. Riporta il codice sintetico pulito scritto a mano (es. "1", "2", "3", "1-BIS").
     * Estrai unicamente il codice sintetico pulito:
       - "Viaggio 1" o "N. 1" o "1°" -> "1"
       - "Viaggio 2" o "N. 2" -> "2"
       - "1 Bis" o "Viaggio 1 Bis" -> "1-BIS"
       - "A1" -> "1"
     * Se la casella è vuota o non compilata: "NON_PRESENTE".
     * Se è coperta, cancellata, macchiata o indecifrabile: "ILLEGGIBILE".

2. PROCEDURA DI LETTURA OBBLIGATORIA: PER COLONNA VERTICALE (DA COLONNA 1 A 13)
   La prima colonna a sinistra contiene le etichette delle righe ("SCATOLE": 1a, 2a, 3a, 4a, 5a, 6a, 7a, 8a, 9a, 10a, TOTALE KG, TOT. N° SCATOLE). Questa colonna NON è un prodotto e non ha colonna_idx!
   Subito a destra si susseguono ESATTAMENTE 13 COLONNE VERTICALI FISSE DI PRODOTTI, numerate da colonna_idx: 1 a 13 da sinistra a destra:

   - [Colonna 1]  OMOGENEIZZATI -> "Omogeneizzati"
   - [Colonna 2]  ALIMENTARI INFANZIA -> "Alimenti per bambini"
   - [Colonna 3]  TONNO -> "Tonno"
   - [Colonna 4]  OLIO -> "Olio"
   - [Colonna 5]  PELATI E SUGHI -> "Pelati"
   - [Colonna 6]  LEGUMI -> "Legumi"
   - [Colonna 7]  PASTA -> "Pasta"
   - [Colonna 8]  RISO -> "Riso"
   - [Colonna 9]  ZUCCHERO -> "Zucchero"
   - [Colonna 10] LATTE -> "Latte"
   - [Colonna 11] BISCOTTI -> "Biscotti"
   - [Colonna 12] CARNE IN SCATOLA -> "Carne in scatola"
   - [Colonna 13] VARIE -> "Varie"

3. METODO OBBLIGATORIO DI ASSEGNAZIONE DELLE CASELLE:
   Ciascuna colonna è rigorosamente delimitata dalle sue due linee verticali continue che partono dal riquadro del titolo stampato in alto e scendono dritte fino in fondo al foglio.
   Per OGNI numero manoscritto, prima di assegnarlo a una categoria, RISALI VERTICALMENTE DRITTO VERSO L'ALTO restando tra le stesse due linee verticali continue fino a leggere l'intestazione stampata in testa alla colonna:

   - COLONNA 8 (RISO) vs COLONNA 9 (ZUCCHERO):
     Verifica geometricamente in quale corridoio verticale si trovano le cifre rispetto a PASTA (Colonna 7):
     * CASO A (Riso compilato, Zucchero vuoto): Se i numeri manoscritti si trovano nella colonna SUBITO A DESTRA di PASTA (cioè nel corridoio verticale n. 8 sotto l'intestazione stampata 'RISO KG') e il corridoio successivo a destra ('ZUCCHERO KG', Colonna 9) è bianco, TUTTI quei numeri appartengono TASSATIVAMENTE a RISO (colonna_idx: 8, "categoria": "Riso") e ZUCCHERO (colonna_idx: 9) è RIGOROSAMENTE VUOTO ("scatole": [])! È severamente vietato spostare i numeri di Riso dentro Zucchero!
     * CASO B (Riso vuoto, Zucchero compilato): Se invece il corridoio verticale n. 8 subito a destra di Pasta (sotto 'RISO KG') è completamente bianco per tutte le righe da 1a a 10a, e i numeri si trovano un corridoio più a destra (sotto 'ZUCCHERO KG', Colonna 9), allora Riso (colonna_idx: 8) è vuoto ("scatole": []) e i numeri appartengono a Zucchero (colonna_idx: 9).

   - COLONNA 2 (ALIMENTARI INFANZIA):
     Controlla attentamente la riga 1a: quando vedi una cifra con un occhiello tondo chiuso nella parte superiore da cui scende un'asta obliqua a destra senza base orizzontale (es. subito sopra il '10' della riga 2a), è TASSATIVAMENTE un **'9' (`9.0` kg)**, MAI un '2'!

   - COLONNA 4 (OLIO) vs COLONNA 5 (PELATI):
     OLIO (Colonna 4) è subito a destra di TONNO (Colonna 3); PELATI E SUGHI (Colonna 5) è tra OLIO (Colonna 4) e LEGUMI (Colonna 6).
     Controlla tutte le righe compilate da 1a a 10a mantenendo ciascun valore rigorosamente nel proprio corridoio verticale delimitato dalle linee continue. Non fermarti alle prime righe: se ci sono scatole scritte anche nelle righe 3a, 4a o successive, estraile tutte accuratamente.

   - COLONNA 7 (PASTA):
     Verifica attentamente tutte le righe compilate da 1a a 10a. Non scambiare un '9' iniziale per un '3', né un '8' per un '5'.

   - REGOLA GENERALE PER LE COLONNE VUOTE:
     Se una colonna non ha numeri scritti dal compilatore nelle righe da 1a a 10a, restituisci sempre "scatole": []. È severamente vietato spostare o duplicare cifre verso colonne adiacenti vuote.

4. REGOLE RIGIDE SUI VALORI NELLE CELLE ED ERRORI CALLIGRAFICI:
   - CIFRE CANCELLATE O SBARRATE A PENNA E CORREZIONI NELLA STESSA CELLA:
     * REGOLA PRIORITARIA (CORREZIONE ACCANTO A SCARABOCCHIO): Se in una casella c'è una macchia/scarabocchio a penna a destra ma nella stessa casella a sinistra (o sotto) si vede una cifra valida (es. `7`), la casella NON è annullata: DEVI SEMPRE ESTRARRE quella cifra valida nell'array `scatole` e annotare la correzione in `note_o_anomalie`!
     * Escludi una casella SOLO quando contiene unicamente uno scarabocchio indecifrabile senza alcuna cifra leggibile accanto.
   - TRATTINI, BARRE, ZERI E CROCI IN CELLE VUOTE:
     Trattini isolati ("-"), barre ("/"), zeri ("0") o croci isolate ("X") usati per indicare assenza di scatole indicano cella VUOTA: NON inserire alcuna scatola con peso 0.0 kg.
     ATTENZIONE: NON confondere la cifra '7' barrata con una croce di cella vuota (vedi sotto la regola del 7).
   - ESPRESSIONI MATEMATICHE NELLE CASELLE:
     Se un volontario scrive un'operazione (es. "2x5", "5+5", "3*4"), risolvi l'operazione calcolando il valore in kg (es. 10.0, 12.0) e annotala in "note_o_anomalie".
   - UNITÀ DI MISURA ESPLICITE (KG / GRAMMI):
     Se i pesi sono indicati in grammi (es. "500 gr", "250g"), convertili in kg (0.5, 0.25).
   - FRECCE E CORREZIONI A MARGINE:
     Se un numero è scritto a margine con freccia verso una casella, assegnalo alla casella bersaglio e segnalalo nelle note.

   - MANUALE CALLIGRAFICO ITALIANO (CONFRONTO INTRA-SCHEDA):
     Prima di classificare una cifra dubbia, confrontala sempre con le altre cifre scritte dalla stessa mano nella stessa scheda!
     * DISTINZIONE TRA '7' MANOSCRITTO ITALIANO E CANCELLATURE/CROCI (REGOLA DEL '7' BARRATO):
       - ATTENZIONE CRITICA: In Italia la cifra '7' manoscritta presenta quasi SEMPRE una barra orizzontale in cima, un'asta discendente obliqua e un TRATTINO ORIZZONTALE A METÀ ASTA (spesso simile a una crocetta con uncino superiore).
       - TASSATIVO: Un segno con barra superiore e trattino mediano NON È UNA CROCETTA, NON È UNA BARRA DI CANCELLATURA, NON È UN TRATTINO VUOTO! È al 100% la cifra '7' (`7.0` kg)!
       - È severamente vietato escludere o saltare una casella che contiene un '7' con trattino mediano: deve SEMPRE essere estratta nell'array 'scatole'!
     * DISTINZIONE TRA '1' e '2' (REGOLA DELLA BASE ORIZZONTALE E DEL VERTICE SUPERIORE):
       - ATTENZIONE: La cifra '1' isolata (1.0 kg) viene spesso scritta a mano con grazia/uncino iniziale in alto a sinistra, asta centrale e una base orizzontale piatta in fondo.
       - È un '2' (2.0 kg) solo quando presenta la tipica curvatura/arco superiore da cui scende il tratto verso la base orizzontale.
       - Se la cifra è un'asta semplice senza base orizzontale, è sempre '1' (1.0 kg).
     * DISTINZIONE TRA '9', '5' E '3' (REGOLA DELL'OCCHIELLO CHIUSO VS ARCHI APERTI):
       - Il '9' presenta SEMPRE un OCCHIELLO SUPERIORE CHIUSO circolare o ovoidale ('o') nella parte alta, da cui scende a destra un'asta verticale o curva.
       - Il '5' presenta una barra orizzontale netta in cima, un breve stelo verticale a sinistra, e una pancia inferiore aperta verso sinistra.
       - Il '3' è composto da due archi aperti verso sinistra, senza mai anello chiuso in cima.
       - Se la somma di colonna risulta inferiore al totale dichiarato di 6 kg esatti (es. calcolato 29 vs dichiarato 35), verifica se una cifra interpretata come '3' ha in realtà l'occhiello superiore tipico di un '9' (poiché 9 - 3 = 6 kg)!
     * VIRGOLA DECIMALE: converti numeri con virgola decimale (es. 7,5 o 2,5) nel float standard (es. 7.5, 2.5).

   - PROCEDURA OBBLIGATORIA DI RICONCILIAZIONE A VINCOLI MATEMATICI (RESIDUO E CONTEGGIO SCATOLE):
     Prima di emettere il JSON finale, per CIASCUNA colonna che ha 'TOTALE KG' e/o 'TOT. N° SCATOLE' compilati a fondo tabella, esegui mentalmente questa verifica a vincoli:
     1. VINCOLO SCATOLE (CONTEGGIO):
        Se 'TOT. N° SCATOLE' dichiara N scatole (es. 4 scatole) ma hai identificato solo M scatole (es. 3 scatole con M < N), SIGNIFICA CHE HAI SCARTATO PER ERRORE UNA CASELLA COMPILATA!
        Riesamina subito le caselle della colonna scartate (in particolare cerca cifre come il '7' con trattino mediano o cifre a matita/tratto sottile) per recuperare la scatola mancante.
     2. VINCOLO KG (CALCOLO DEL RESIDUO DELTA):
        Calcola: Delta = (TOTALE KG DICHIARATO) - (SOMMA KG CASELLE IDENTIFICATE).
        Se Delta > 0:
        - Se manca 1 scatola e Delta = 7.0 kg, cerca la casella con il '7' barrato che avevi omesso e inseriscila!
        - Se le scatole coincidono ma Delta = 6.0 kg, verifica se una cifra letta come '3' è in realtà un '9' (9 - 3 = 6).
     3. QUADRATURA PERFETTA:
        Quando la somma delle singole caselle coincide esattamente con 'TOTALE KG' e il conteggio con 'TOT. N° SCATOLE', convalida l'estrazione: la verità calligrafica e matematica è perfettamente ricostruita.
     4. Se invece anche dopo l'attenta verifica la discrepanza resta oggettiva ed evidente, estrai i valori effettivi e segnala la discrepanza nelle 'note_o_anomalie'.

   - TOTALI DI CALCE:
     Se in fondo alla colonna sono compilate le righe 'TOTALE KG' o 'TOT. N° SCATOLE', estraile in 'totale_dichiarato_kg' e 'totale_dichiarato_scatole'. Non metterle nell'array 'scatole'.
   - ORIENTAMENTO SCHEDA:
     Imposta "scheda_capovolta": true solo se l'immagine è ruotata di 180°, altrimenti false.

RISPONDI ESCLUSIVAMENTE CON UN OGGETTO JSON VALIDO CON QUESTA STRUTTURA:
{
  "supermercato": "stringa o null",
  "numero_scheda": "stringa (es. '1', 'NON_PRESENTE', 'ILLEGGIBILE')",
  "colonne": [
    {
      "colonna_idx": 1,
      "categoria": "Omogeneizzati",
      "scatole": [
        {"riga": "1a", "kg": 10.0}
      ],
      "totale_dichiarato_kg": null,
      "totale_dichiarato_scatole": null
    }
  ],
  "note_o_anomalie": "stringa o null",
  "scheda_capovolta": false
}
"""


class BaseVisionExtractor(ABC):
    """Classe base astratta per tutti i provider di estrazione visiva."""

    @abstractmethod
    def extract_from_image(self, image_path: str, status_callback: Optional[Callable[[str], None]] = None) -> RawCardExtraction:
        """Estrae i dati dalla foto o scansione della scheda corriere."""
        pass

    @staticmethod
    def encode_and_optimize_image(image_path: str, max_dimension: int = 3200, quality: int = 92) -> tuple[str, str]:
        """Carica l'immagine, corregge l'orientamento EXIF, comprime in JPEG ad alta risoluzione ed esporta in base64."""
        from PIL import Image, ImageOps
        import io
        import base64
        from pathlib import Path

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"File immagine non trovato: {image_path}")

        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Ridimensiona solo se supera la dimensione massima per non degradare il tratto a penna
            max_dim = max(img.width, img.height)
            if max_dim > max_dimension:
                scale = max_dimension / max_dim
                new_size = (int(img.width * scale), int(img.height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return "image/jpeg", b64_data

    @staticmethod
    def _is_bottom_total_reference(text: str) -> bool:
        """Rileva se un'etichetta o categoria fa riferimento alle righe di totale in calce alla scheda."""
        if not text:
            return False
        t = text.lower().strip()
        if any(kw in t for kw in (
            "totale", "tot.", "tot kg", "tot.kg", "totale kg", "tot scatole",
            "totale scatole", "tot n", "tot. n", "tot colli", "totale colli"
        )):
            return True
        if re.search(r"\btot\b", t) or re.search(r"\btotale\b", t):
            return True
        return False

    @staticmethod
    def _repair_json(text: str) -> str:
        """Applica euristiche di recupero per errori comuni di sintassi JSON generati dagli LLM."""
        if not text:
            return text
        s = text.replace("\r\n", "\n")
        # 1. Rimuovi commenti // o /* */
        s = re.sub(r"//.*?\n", "\n", s)
        s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
        # 2. Python literals -> JSON standard
        s = re.sub(r"\bNone\b", "null", s)
        s = re.sub(r"\bTrue\b", "true", s)
        s = re.sub(r"\bFalse\b", "false", s)
        # 3. Rimuovi trailing commas prima di } o ]
        s = re.sub(r",\s*([}\]])", r"\1", s)
        # 4. Inserisci virgole mancanti tra elementi separati da a capo
        s = re.sub(r'("|\d|true|false|null|\]|\})\s*\n\s*("|\{|\[)', r"\1,\n\2", s)
        # 5. Oggetti o liste adiacenti senza virgola: }{ -> },{ e ][ -> ],[
        s = re.sub(r"\}\s*\{", "},{", s)
        s = re.sub(r"\]\s*\[", "],[", s)
        # 6. Rimuovi eventuali trailing commas create dalle sostituzioni precedenti
        s = re.sub(r",\s*([}\]])", r"\1", s)
        # 7. Chiusura automatica di parentesi non bilanciate se l'output e' stato troncato
        open_curlies = s.count("{") - s.count("}")
        open_brackets = s.count("[") - s.count("]")
        if open_brackets > 0:
            s += "]" * open_brackets
        if open_curlies > 0:
            s += "}" * open_curlies
        return s

    @classmethod
    def _extract_via_regex(cls, text: str) -> Optional[RawCardExtraction]:
        """Parser euristico di emergenza: estrae scatole e totali anche da JSON gravemente malformato."""
        if not text:
            return None
        supermercato = None
        m_sup = re.search(r'"supermercato"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
        if m_sup:
            supermercato = m_sup.group(1).strip()
        num_scheda = None
        m_num = re.search(r'"numero_scheda"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
        if m_num:
            num_scheda = m_num.group(1).strip()

        righe: List[BoxItem] = []
        totali_per_cat: Dict[str, Dict[str, Any]] = {}

        col_blocks = re.split(r'\{\s*"colonna_idx"', text)
        if len(col_blocks) > 1:
            for block in col_blocks[1:]:
                cat_m = re.search(r'"categoria"\s*:\s*"([^"]+)"', block)
                idx_m = re.search(r'^[\s:]*(\d+)', block)
                cat = "Varie"
                if idx_m:
                    idx_val = int(idx_m.group(1))
                    if 1 <= idx_val <= len(CATEGORIES):
                        cat = CATEGORIES[idx_val - 1]["name"]
                elif cat_m:
                    cat = normalize_category_name(cat_m.group(1))

                for sc_match in re.finditer(r'\{[^{}]*"kg"\s*:\s*([0-9.]+)[^{}]*\}', block):
                    try:
                        kg_val = float(sc_match.group(1))
                        if kg_val > 0:
                            righe.append(BoxItem(categoria=cat, kg=kg_val))
                    except ValueError:
                        pass
                tot_kg_m = re.search(r'"totale_dichiarato_kg"\s*:\s*([0-9.]+)', block)
                tot_sc_m = re.search(r'"totale_dichiarato_scatole"\s*:\s*(\d+)', block)
                if tot_kg_m or tot_sc_m:
                    t_kg = float(tot_kg_m.group(1)) if tot_kg_m else None
                    t_sc = int(tot_sc_m.group(1)) if tot_sc_m else None
                    totali_per_cat[cat] = {"kg": t_kg, "scatole": t_sc}

        if not righe:
            for sc_match in re.finditer(r'\{[^{}]*"categoria"\s*:\s*"([^"]+)"[^{}]*"kg"\s*:\s*([0-9.]+)[^{}]*\}', text):
                c = normalize_category_name(sc_match.group(1))
                try:
                    k = float(sc_match.group(2))
                    if k > 0:
                        righe.append(BoxItem(categoria=c, kg=k))
                except ValueError:
                    pass

        if righe or supermercato or num_scheda:
            totali = DeclaredTotals(per_categoria=totali_per_cat)
            return RawCardExtraction(
                supermercato=supermercato,
                numero_scheda=num_scheda,
                righe_scatole=righe,
                totali_dichiarati=totali,
                note_o_anomalie="[Recupero euristico da sintassi JSON imperfetta]"
            )
        return None

    @classmethod
    def is_valid_raw_extraction(cls, raw: Optional[RawCardExtraction]) -> bool:
        """Verifica se l'estrazione ottenuta e' valida o se contiene un errore fatale."""
        if raw is None:
            return False
        if raw.righe_scatole and len(raw.righe_scatole) > 0:
            return True
        err_note = (raw.note_o_anomalie or "").lower()
        fatal_terms = (
            "impossibile interpretare json",
            "nessun oggetto json",
            "risposta vuota",
            "formato inatteso",
            "expecting",
            "jsondecodeerror",
            "delimiter",
            "falliti",
        )
        if any(term in err_note for term in fatal_terms):
            return False
        return True

    @classmethod
    def parse_json_response(cls, raw_text: str) -> RawCardExtraction:
        """Pulisce eventuale markdown e converte il testo JSON in RawCardExtraction con riparazione automatica e fallback euristico."""
        if not raw_text:
            return RawCardExtraction(note_o_anomalie="Risposta vuota dal modello")

        # Rimuovi blocchi di codice markdown ```json ... ```
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE).strip()

        data = None
        parse_err = None

        # 1. Tentativo parsing diretto
        try:
            data = json.loads(cleaned)
        except Exception as e:
            parse_err = e

        # 2. Tentativo blocco {...}
        if data is None:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except Exception as e:
                    parse_err = e

        # 3. Tentativo con riparazione automatica della sintassi (virgole mancanti / trailing / parentesi)
        if data is None:
            try:
                data = json.loads(cls._repair_json(cleaned))
            except Exception:
                pass

        # 4. Tentativo riparazione sul blocco regex {...}
        if data is None:
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                try:
                    data = json.loads(cls._repair_json(match.group(0)))
                except Exception as e:
                    parse_err = e

        # 5. Fallback euristico di emergenza (recupera comunque scatole, totali e supermercato)
        if data is None:
            recovered = cls._extract_via_regex(cleaned)
            if recovered and recovered.righe_scatole:
                return recovered
            return RawCardExtraction(note_o_anomalie=f"Impossibile interpretare JSON: {parse_err}")
        if not isinstance(data, dict):
            return RawCardExtraction(note_o_anomalie="Risposta del modello in formato inatteso")

        righe = []
        totali_per_cat = {}

        # 1. Parsing Formato a Colonne Verticali ("colonne")
        if "colonne" in data and isinstance(data["colonne"], list):
            for col in data["colonne"]:
                if not isinstance(col, dict):
                    continue
                col_idx = col.get("colonna_idx")
                cat_name = col.get("categoria")
                if isinstance(col_idx, int) and 1 <= col_idx <= len(CATEGORIES):
                    cat = CATEGORIES[col_idx - 1]["name"]
                else:
                    cat = normalize_category_name(str(cat_name or ""))

                for sc in (col.get("scatole") or []):
                    if not isinstance(sc, dict):
                        continue
                    riga_val = str(sc.get("riga") or "").strip().lower()
                    if cls._is_bottom_total_reference(riga_val):
                        continue
                    try:
                        kg = max(float(sc.get("kg") or 0.0), 0.0)
                    except (ValueError, TypeError):
                        kg = 0.0
                    if kg <= 0.0:
                        continue
                    righe.append(BoxItem(categoria=cat, kg=kg))

                tot_kg = col.get("totale_dichiarato_kg")
                tot_sc = col.get("totale_dichiarato_scatole")
                if tot_kg is not None or tot_sc is not None:
                    try:
                        parsed_kg = float(tot_kg) if tot_kg is not None else None
                    except (ValueError, TypeError):
                        parsed_kg = None
                    try:
                        parsed_sc = int(tot_sc) if tot_sc is not None else None
                    except (ValueError, TypeError):
                        parsed_sc = None
                    if parsed_kg is not None or parsed_sc is not None:
                        totali_per_cat[cat] = {"kg": parsed_kg, "scatole": parsed_sc}

        # 2. Parsing Formato Storico / Fallback ad Array Piatto ("righe_scatole")
        elif "righe_scatole" in data and isinstance(data["righe_scatole"], list):
            for item in data["righe_scatole"]:
                if not isinstance(item, dict):
                    continue
                riga_val = str(item.get("riga") or "").strip().lower()
                cat_raw = str(item.get("categoria") or "").strip().lower()
                col_idx = item.get("colonna_idx")
                if cls._is_bottom_total_reference(riga_val) or cls._is_bottom_total_reference(cat_raw):
                    continue
                if isinstance(col_idx, int) and 1 <= col_idx <= len(CATEGORIES):
                    cat = CATEGORIES[col_idx - 1]["name"]
                else:
                    cat = normalize_category_name(str(item.get("categoria") or ""))
                try:
                    kg = max(float(item.get("kg") or 0.0), 0.0)
                except (ValueError, TypeError):
                    kg = 0.0
                if kg <= 0.0:
                    continue
                righe.append(BoxItem(categoria=cat, kg=kg))

        # Gestione totali dichiarati globali / per categoria (eventualmente sovrascritti o integrati)
        totali_dict = data.get("totali_dichiarati")
        if not isinstance(totali_dict, dict):
            totali_dict = {}
        per_cat = totali_dict.get("per_categoria")
        if isinstance(per_cat, dict):
            for k, v in per_cat.items():
                cat_k = normalize_category_name(k)
                if isinstance(v, dict):
                    totali_per_cat[cat_k] = v
                elif isinstance(v, (int, float)):
                    totali_per_cat[cat_k] = {"kg": float(v)}
                elif isinstance(v, str):
                    try:
                        totali_per_cat[cat_k] = {"kg": float(v.replace(",", "."))}
                    except (ValueError, TypeError):
                        pass

        try:
            scatole_tot = totali_dict.get("scatole_totale")
            scatole_tot = int(scatole_tot) if scatole_tot is not None else None
        except (ValueError, TypeError):
            scatole_tot = None
        try:
            kg_tot = totali_dict.get("kg_totale")
            kg_tot = float(kg_tot) if kg_tot is not None else None
        except (ValueError, TypeError):
            kg_tot = None

        totali = DeclaredTotals(
            kg_totale=kg_tot,
            scatole_totale=scatole_tot,
            per_categoria=totali_per_cat
        )

        num_scheda = data.get("numero_scheda")
        if num_scheda:
            num_scheda = str(num_scheda).strip().upper()
            num_scheda = re.sub(r"^(?:VIAGGIO|SCHEDA|FURGONE|SPEDIZIONE|NUMERO|NUM|N)[.\s°]+\s*", "", num_scheda, flags=re.IGNORECASE).strip()
            if num_scheda.startswith("A") and num_scheda[1:].isdigit():
                num_scheda = num_scheda[1:]
        if not num_scheda:
            num_scheda = "NON_PRESENTE"

        supermercato_val = (data.get("supermercato") or "").strip()
        note_val = data.get("note_o_anomalie") or ""
        # Defensive fallback: se il supermercato è vuoto, recuperalo se il modello lo ha scritto per errore nelle note
        if not supermercato_val and note_val:
            m_sup = re.search(r"supermercato[:\s\-]+([^.\n\[]+)", note_val, flags=re.IGNORECASE)
            if m_sup:
                candidate_sup = m_sup.group(1).strip()
                if len(candidate_sup) >= 3:
                    supermercato_val = candidate_sup

        return RawCardExtraction(
            supermercato=supermercato_val,
            numero_scheda=num_scheda,
            righe_scatole=righe,
            totali_dichiarati=totali,
            note_o_anomalie=note_val or None,
            scheda_capovolta=bool(data.get("scheda_capovolta", False))
        )