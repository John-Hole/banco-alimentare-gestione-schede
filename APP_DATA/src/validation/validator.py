"""Motore di validazione e applicazione regole per schede corriere Banco Alimentare."""

import re
from typing import Dict, List, Optional
from ..models import (
    RawCardExtraction,
    ProcessedCard,
    CategoryDetail,
    ValidationFlag,
    FlagSeverity,
    BoxItem,
)
from ..config import CATEGORIES, CATEGORY_NAMES, TRANSPORT_ROWS


class CardValidator:
    """Valida i dati grezzi estratti dalla scheda corriere secondo le specifiche del Banco Alimentare."""

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

    @classmethod
    def validate(
        cls,
        raw: RawCardExtraction,
        next_available_slot: Optional[str] = "A1",
        existing_filled_rows: Optional[List[str]] = None,
    ) -> ProcessedCard:
        """Esegue l'intero ciclo di validazione e genera il ProcessedCard."""
        flags: List[ValidationFlag] = []
        if existing_filled_rows is None:
            existing_filled_rows = []

        # 1. Inizializzazione delle 13 categorie canoniche
        cat_details: Dict[str, CategoryDetail] = {}
        for c in CATEGORIES:
            cat_details[c["name"]] = CategoryDetail(
                categoria=c["name"],
                kg_calcolati=0.0,
                scatole_calcolate=0,
                kg_dichiarati=None,
                scatole_dichiarate=None,
                kg_col=c["kg_col"],
                box_col=c["box_col"],
                flags=[],
            )

        # 2. Aggregazione scatole per categoria (Regola 6.1)
        # DIVIETO ASSOLUTO: Scarta qualsiasi riga_scatola che faccia riferimento ai totali di fondo
        filtered_boxes: List[BoxItem] = []
        for box in raw.righe_scatole:
            cat_lower = (box.categoria or "").strip().lower()
            riga_attr = getattr(box, "riga", None)
            riga_lower = (str(riga_attr) if riga_attr else "").strip().lower()
            if cls._is_bottom_total_reference(cat_lower) or cls._is_bottom_total_reference(riga_lower):
                continue
            filtered_boxes.append(box)

        raw.righe_scatole = filtered_boxes

        totale_kg_calc = 0.0
        totale_scatole_calc = 0
        for box in filtered_boxes:
            if box.categoria in cat_details:
                cat_details[box.categoria].scatole_calcolate += 1
                cat_details[box.categoria].kg_calcolati += round(box.kg, 2)
            else:
                # Categoria non standard: assegnata a "Varie" con avviso
                cat_details["Varie"].scatole_calcolate += 1
                cat_details["Varie"].kg_calcolati += round(box.kg, 2)
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.WARNING,
                        field=f"scatola:{box.categoria}",
                        message=f"Categoria non riconosciuta '{box.categoria}', conteggiata in 'Varie'.",
                    )
                )

        for cat in cat_details.values():
            cat.kg_calcolati = round(cat.kg_calcolati, 2)
            totale_kg_calc += cat.kg_calcolati
            totale_scatole_calc += cat.scatole_calcolate
        totale_kg_calc = round(totale_kg_calc, 2)

        # Inserimento totali dichiarati per categoria se presenti
        if raw.totali_dichiarati and raw.totali_dichiarati.per_categoria:
            for cat_name, val in raw.totali_dichiarati.per_categoria.items():
                if cat_name in cat_details:
                    declared_kg = None
                    declared_scat = None
                    if isinstance(val, dict):
                        declared_kg = val.get("kg")
                        declared_scat = val.get("scatole")
                    elif isinstance(val, (int, float)):
                        declared_kg = float(val)
                    elif isinstance(val, str):
                        try:
                            declared_kg = float(val.replace(",", "."))
                        except (ValueError, TypeError):
                            pass
                    if declared_kg is not None:
                        try:
                            cat_details[cat_name].kg_dichiarati = round(float(declared_kg), 2)
                        except (ValueError, TypeError):
                            pass
                    if declared_scat is not None:
                        try:
                            cat_details[cat_name].scatole_dichiarate = int(declared_scat)
                        except (ValueError, TypeError):
                            pass

        # 3. Validazione Numero Scheda (Regola 6.2)
        numero_risolto: Optional[str] = None
        num_raw = (raw.numero_scheda or "").strip().upper()

        if not num_raw or num_raw in ("NON_PRESENTE", "VUOTO", "NESSUNO"):
            # Scheda non scritta: assegnazione automatica
            if next_available_slot:
                numero_risolto = next_available_slot
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.WARNING,
                        field="numero_scheda",
                        message=f"Numero scheda non presente sul foglio: assegnato automaticamente '{numero_risolto}'.",
                    )
                )
            else:
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.RED,
                        field="numero_scheda",
                        message="Numero scheda non presente e nessun trasporto libero rimasto nel foglio Excel (A1-A10 completati).",
                    )
                )
        elif num_raw in ("ILLEGGIBILE", "DUBBIO", "SCARABOCCHIO"):
            numero_risolto = None
            flags.append(
                ValidationFlag(
                    severity=FlagSeverity.RED,
                    field="numero_scheda",
                    message="Numero scheda scritto ma illeggibile o ambiguo. Richiesta selezione manuale da parte dell'operatore.",
                )
            )
        else:
            # Numero specificato (es. "1", "A1", "2"...)
            if not num_raw.startswith("A") and num_raw.isdigit():
                candidate = f"A{num_raw}"
            else:
                candidate = num_raw

            if candidate in TRANSPORT_ROWS:
                numero_risolto = candidate
                # Verifica se la riga è già compilata in Excel
                if candidate in existing_filled_rows:
                    flags.append(
                        ValidationFlag(
                            severity=FlagSeverity.RED,
                            field="numero_scheda",
                            message=f"Attenzione: il trasporto '{candidate}' risulta già compilato nel foglio Excel gestionale!",
                        )
                    )
                else:
                    flags.append(
                        ValidationFlag(
                            severity=FlagSeverity.GREEN,
                            field="numero_scheda",
                            message=f"Numero scheda valido: {candidate}.",
                        )
                    )
            else:
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.RED,
                        field="numero_scheda",
                        message=f"Numero scheda '{num_raw}' non valido (deve essere compreso tra A1 e A10).",
                    )
                )

        # 4. Validazione Supermercato
        if not raw.supermercato or not raw.supermercato.strip():
            flags.append(
                ValidationFlag(
                    severity=FlagSeverity.RED,
                    field="supermercato",
                    message="Nome del supermercato non rilevato sul modulo cartaceo.",
                )
            )

        # 5. Regola 6.4: Mancanza dati riga per riga con solo totale presente
        ha_scatole = len(raw.righe_scatole) > 0
        ha_totale_dichiarato = (
            raw.totali_dichiarati.kg_totale is not None
            or raw.totali_dichiarati.scatole_totale is not None
        )

        if not ha_scatole and ha_totale_dichiarato:
            flags.append(
                ValidationFlag(
                    severity=FlagSeverity.RED,
                    field="dati_scatole",
                    message="Mancano i dati delle singole scatole nella tabella; presente solo un totale complessivo.",
                )
            )
        elif not ha_scatole and not ha_totale_dichiarato:
            flags.append(
                ValidationFlag(
                    severity=FlagSeverity.RED,
                    field="dati_scatole",
                    message="Scheda completamente vuota o non compilata.",
                )
            )

        # 6. Regola 6.3: Confronto Totale Scritto vs Dati Calcolati
        # 6.3a: Totale complessivo Kg
        if raw.totali_dichiarati.kg_totale is not None:
            dich_kg = round(float(raw.totali_dichiarati.kg_totale), 2)
            diff_kg = abs(dich_kg - totale_kg_calc)
            if diff_kg <= 0.05:
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.GREEN,
                        field="totale_kg",
                        message=f"Totale Kg dichiarato ({dich_kg} kg) coincide con la somma calcolata.",
                    )
                )
            else:
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.RED,
                        field="totale_kg",
                        message=f"Discrepanza Totale Kg: dichiarato {dich_kg} kg vs calcolato {totale_kg_calc} kg (diff: {round(totale_kg_calc - dich_kg, 2)} kg).",
                    )
                )

        # 6.3b: Totale complessivo Scatole
        if raw.totali_dichiarati.scatole_totale is not None:
            dich_scat = int(raw.totali_dichiarati.scatole_totale)
            if dich_scat == totale_scatole_calc:
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.GREEN,
                        field="totale_scatole",
                        message=f"Totale scatole dichiarato ({dich_scat}) coincide con il conteggio.",
                    )
                )
            else:
                flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.RED,
                        field="totale_scatole",
                        message=f"Discrepanza Totale Scatole: dichiarato {dich_scat} vs calcolato {totale_scatole_calc}.",
                    )
                )

        # 6.3c & 6.4: Confronto e consistenza per singola categoria
        for cat_name, cat in cat_details.items():
            # Caso 1: Totale dichiarato presente ma nessuna scatola riga per riga
            if (cat.kg_dichiarati is not None and cat.kg_dichiarati > 0) and cat.scatole_calcolate == 0:
                flag = ValidationFlag(
                    severity=FlagSeverity.RED,
                    field=f"categoria:{cat_name}",
                    message=f"Categoria '{cat_name}': dichiarato totale di {cat.kg_dichiarati} kg ma nessuna scatola segnata nel dettaglio.",
                )
                cat.flags.append(flag)
                flags.append(flag)

            # Caso 2: Scatole compilate ma totale dichiarato pari a 0
            elif (cat.kg_dichiarati is not None and cat.kg_dichiarati == 0) and cat.scatole_calcolate > 0:
                flag = ValidationFlag(
                    severity=FlagSeverity.RED,
                    field=f"categoria:{cat_name}",
                    message=f"Categoria '{cat_name}': scatole presenti ma totale dichiarato è 0.",
                )
                cat.flags.append(flag)
                flags.append(flag)

            # Caso 3: Totale Kg dichiarato per categoria non coincidente
            elif cat.kg_dichiarati is not None and cat.scatole_calcolate > 0:
                diff = abs(cat.kg_dichiarati - cat.kg_calcolati)
                if diff > 0.05:
                    flag = ValidationFlag(
                        severity=FlagSeverity.RED,
                        field=f"categoria:{cat_name}",
                        message=f"Categoria '{cat_name}': discrepanza kg (dichiarato {cat.kg_dichiarati} vs calcolato {cat.kg_calcolati}).",
                    )
                    cat.flags.append(flag)
                    flags.append(flag)
                else:
                    flag = ValidationFlag(
                        severity=FlagSeverity.GREEN,
                        field=f"categoria:{cat_name}",
                        message=f"Categoria '{cat_name}': totale verificato ({cat.kg_calcolati} kg).",
                    )
                    cat.flags.append(flag)

            # Caso 4: Totale Scatole dichiarato per categoria non coincidente
            if cat.scatole_dichiarate is not None and cat.scatole_dichiarate != cat.scatole_calcolate:
                flag = ValidationFlag(
                    severity=FlagSeverity.RED,
                    field=f"categoria:{cat_name}:scatole",
                    message=f"Categoria '{cat_name}': discrepanza scatole (dichiarate {cat.scatole_dichiarate} vs calcolate {cat.scatole_calcolate}).",
                )
                cat.flags.append(flag)
                flags.append(flag)

        # 6.5 Rilevamento automatico di slittamento tra colonne adiacenti (Column Drift)
        # Se due colonne adiacenti mostrano discrepanze bilanciate ed opposte (es. Olio a -3kg e Pelati a +3kg)
        cat_order = [c["name"] for c in CATEGORIES]
        for i in range(len(cat_order) - 1):
            n1 = cat_order[i]
            n2 = cat_order[i + 1]
            cd1 = cat_details[n1]
            cd2 = cat_details[n2]
            if cd1.kg_dichiarati is not None and cd2.kg_dichiarati is not None:
                d1 = round(cd1.kg_calcolati - cd1.kg_dichiarati, 2)
                d2 = round(cd2.kg_calcolati - cd2.kg_dichiarati, 2)
                if abs(d1 + d2) <= 0.05 and abs(d1) > 0.05:
                    flags.append(
                        ValidationFlag(
                            severity=FlagSeverity.WARNING,
                            field=f"drift:{n1}_{n2}",
                            message=f"Possibile slittamento tra colonne adiacenti '{n1}' e '{n2}': discrepanza bilanciata di {abs(d1)} kg.",
                        )
                    )

        # 7. Note o anomalie aggiuntive
        if raw.note_o_anomalie and raw.note_o_anomalie.strip():
            flags.append(
                ValidationFlag(
                    severity=FlagSeverity.WARNING,
                    field="note",
                    message=f"Nota rilevata: {raw.note_o_anomalie}",
                )
            )

        ha_rossi = any(f.severity == FlagSeverity.RED for f in flags)

        return ProcessedCard(
            supermercato_rilevato=raw.supermercato or "",
            supermercato_confermato=raw.supermercato or "",
            numero_scheda_rilevato=raw.numero_scheda,
            numero_scheda_risolto=numero_risolto,
            righe_scatole=raw.righe_scatole,
            totali_dichiarati=raw.totali_dichiarati,
            categorie=cat_details,
            totale_kg_calcolati=totale_kg_calc,
            totale_scatole_calcolate=totale_scatole_calc,
            flags=flags,
            ha_flag_rossi=ha_rossi,
            autorizzato_da_operatore=False,
            pronto_per_scrittura=(not ha_rossi and numero_risolto is not None),
        )

    @classmethod
    def recompute_from_categories(cls, card: ProcessedCard) -> ProcessedCard:
        """Ricalcola totali e flag quando l'operatore modifica i valori in tabella."""
        totale_kg = 0.0
        totale_scatole = 0
        new_flags: List[ValidationFlag] = []

        for cat in card.categorie.values():
            cat.flags = []
            totale_kg += cat.kg_calcolati
            totale_scatole += cat.scatole_calcolate
        card.totale_kg_calcolati = round(totale_kg, 2)
        card.totale_scatole_calcolate = totale_scatole

        # Verifica totali generali dichiarati
        if card.totali_dichiarati.kg_totale is not None:
            dich_kg = round(float(card.totali_dichiarati.kg_totale), 2)
            if abs(dich_kg - card.totale_kg_calcolati) <= 0.05:
                new_flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.GREEN,
                        field="totale_kg",
                        message=f"Totale Kg dichiarato ({dich_kg} kg) coincide con la somma calcolata.",
                    )
                )
            else:
                new_flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.RED,
                        field="totale_kg",
                        message=f"Discrepanza Totale Kg: dichiarato {dich_kg} kg vs calcolato {card.totale_kg_calcolati} kg.",
                    )
                )

        if card.totali_dichiarati.scatole_totale is not None:
            dich_scat = int(card.totali_dichiarati.scatole_totale)
            if dich_scat == card.totale_scatole_calcolate:
                new_flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.GREEN,
                        field="totale_scatole",
                        message=f"Totale scatole dichiarato ({dich_scat}) coincide con il conteggio.",
                    )
                )
            else:
                new_flags.append(
                    ValidationFlag(
                        severity=FlagSeverity.RED,
                        field="totale_scatole",
                        message=f"Discrepanza Totale Scatole: dichiarato {dich_scat} vs calcolato {card.totale_scatole_calcolate}.",
                    )
                )

        # Verifica per categoria
        for cat_name, cat in card.categorie.items():
            if (cat.kg_dichiarati is not None and cat.kg_dichiarati > 0) and cat.scatole_calcolate == 0:
                flag = ValidationFlag(
                    severity=FlagSeverity.RED,
                    field=f"categoria:{cat_name}",
                    message=f"Categoria '{cat_name}': dichiarato totale di {cat.kg_dichiarati} kg ma nessuna scatola inserita.",
                )
                cat.flags.append(flag)
                new_flags.append(flag)
            elif cat.kg_dichiarati is not None and cat.scatole_calcolate > 0:
                diff = abs(cat.kg_dichiarati - cat.kg_calcolati)
                if diff > 0.05:
                    flag = ValidationFlag(
                        severity=FlagSeverity.RED,
                        field=f"categoria:{cat_name}",
                        message=f"Categoria '{cat_name}': discrepanza kg (dichiarato {cat.kg_dichiarati} vs calcolato {cat.kg_calcolati}).",
                    )
                    cat.flags.append(flag)
                    new_flags.append(flag)
                else:
                    flag = ValidationFlag(
                        severity=FlagSeverity.GREEN,
                        field=f"categoria:{cat_name}",
                        message=f"Categoria '{cat_name}': totale verificato ({cat.kg_calcolati} kg).",
                    )
                    cat.flags.append(flag)

            # Caso 4: Totale Scatole dichiarato per categoria non coincidente
            if cat.scatole_dichiarate is not None and cat.scatole_dichiarate != cat.scatole_calcolate:
                flag = ValidationFlag(
                    severity=FlagSeverity.RED,
                    field=f"categoria:{cat_name}:scatole",
                    message=f"Categoria '{cat_name}': discrepanza scatole (dichiarate {cat.scatole_dichiarate} vs calcolate {cat.scatole_calcolate}).",
                )
                cat.flags.append(flag)
                new_flags.append(flag)

        # Aggiungi eventuali flag di numero scheda preesistenti
        for f in card.flags:
            if f.field in ("numero_scheda", "supermercato", "note"):
                new_flags.append(f)

        card.flags = new_flags
        card.ha_flag_rossi = any(f.severity == FlagSeverity.RED for f in new_flags)
        card.pronto_per_scrittura = (not card.ha_flag_rossi or card.autorizzato_da_operatore) and (card.numero_scheda_risolto is not None)
        return card
