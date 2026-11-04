"""presets_config.py
=============================================================================
CONFIGURAZIONE DEI 5 PRESET DOCUMENTALI E CLASSIFICATORE AUTOMATICO
=============================================================================
Progetto: Banco Alimentare - Acquisizione Schede Raccolta 'MOD. A'
Autore: Team Antigravity AI
Data: 2026-10-31

Questo file centralizza tutti i parametri di elaborazione visiva per i 5 Preset
ottimizzati sulle casistiche reali delle schede MOD. A (dalla matita chiarissima
alla biro spessa, fino a sfondi scuri e tratti spezzati), oltre alle soglie
dell'algoritmo di classificazione intelligente automatica.

È stato strutturato in modo leggibile e ampiamente commentato in italiano affinché
qualsiasi operatore o sviluppatore possa tarare finemente i singoli parametri
senza dover modificare il codice del motore computazionale (preset_engine.py).
"""

from typing import Dict, Any, List, Tuple


# =============================================================================
# 1. DIZIONARIO CONFIGURAZIONE DEI PRESET DOCUMENTALI (PRESETS_CONFIG)
# =============================================================================

PRESETS_CONFIG: Dict[int, Dict[str, Any]] = {
    # -------------------------------------------------------------------------
    # PRESET 0: STANDARD ORIGINALE (P0 - Nessuna modifica)
    # -------------------------------------------------------------------------
    0: {
        "id": 0,
        "code": "P0",
        "name": "P0",
        "titolo": "P0",
        "target_scenario": (
            "Nessuna modifica: scansione originale standard pura senza filtri o alterazioni."
        ),
        "descrizione": (
            "Mantiene l'immagine nativa acquisita dallo scanner al 100% senza alcun ritocco "
            "o alterazione di contrasto, luminosità o curve."
        ),
    },

    # -------------------------------------------------------------------------
    # PRESET 1: MATITA ULTRA-LEGGERA (Scheda 1 Tigre Benchmark)
    # -------------------------------------------------------------------------
    1: {
        "id": 1,
        "code": "P1",
        "name": "P1_Matita_UltraLeggera",
        "titolo": "Matita Ultra-Leggera / Tratti Sbiaditi",
        "target_scenario": (
            "Schede compilate a matita con grafite chiara (HB/2H), tratti sbiaditi, "
            "cancellature o scrittura quasi trasparente (es. Scheda 1 - Tigre)."
        ),
        "descrizione": (
            "Applica una curva di trasferimento non lineare ad altissima pendenza "
            "sui toni medio-chiari (equivalente a Photoshop Livelli Gamma 0.14) "
            "per convertire anche il tratto a matita più debole in un nero solido e leggibile, "
            "pulendo al contempo lo sfondo a bianco puro (255) senza aloni grigi."
        ),
        # Parametri Curva Livelli / Gamma
        # gamma_photoshop: Valore cursore mezzitoni di Photoshop (default: 0.14).
        # Nella matematica standard, esponente = 1.0 / gamma_photoshop (~7.14).
        # Valori più bassi (es. 0.11 - 0.14) scuriscono molto di più la matita sbiadita;
        # valori più alti (es. 0.20 - 0.25) lasciano la sfumatura più morbida.
        "gamma_photoshop": 0.14,

        # Punto di nero e punto di bianco per il remapping dei livelli
        "in_black": 0,       # Livello di ingresso mappato a 0
        "in_white": 250,     # Livello di ingresso oltre il quale il segnale è bianco puro (allineato a white_clean_threshold)

        # Soglia di azzeramento rumore carta: qualsiasi livello sopra questa soglia viene forzato a 255
        "white_clean_threshold": 250,

        # Nitidezza / Unsharp Mask di rifinitura bordi cifre
        "sharpness_strength": 0.25,   # Intensità maschera di contrasto (0.0 = disattivata, 0.25 = morbida)
        "unsharp_sigma": 1.0,         # Raggio Gaussiano in pixel per il calcolo dei bordi
    },

    # -------------------------------------------------------------------------
    # PRESET 2: BIRO MARCATA / TRATTO PESANTE (Scheda 2 Coop Triangolo Benchmark)
    # -------------------------------------------------------------------------
    2: {
        "id": 2,
        "code": "P2",
        "name": "P2_Biro_Marcata",
        "titolo": "Biro Marcata / Tratto Pesante",
        "target_scenario": (
            "Schede compilate a penna a sfera spessa, biro nera molto carica o pennarello "
            "dove le cifre chiuse ('0', '6', '8', '9') rischiano di impastarsi o fondersi "
            "con le linee della griglia (es. Scheda 2 - Coop Triangolo)."
        ),
        "descrizione": (
            "Applica un contrasto controllato e delicato (Gamma 0.45) unito a un CLAHE "
            "a basso clipLimit per distribuire i toni locali senza alterare lo spessore nativo del tratto. "
            "È CATEGORICAMENTE ESCLUSA qualsiasi dilatazione/ispessimento morfologico "
            "in modo da mantenere aperti e nitidi gli occhielli interni delle cifre numeriche."
        ),
        # Curva tonale morbida (Gamma Photoshop ~0.45, esponente ~2.22)
        "gamma_photoshop": 0.45,
        "in_black": 0,
        "in_white": 252,

        # CLAHE (Contrast Limited Adaptive Histogram Equalization) su canale Luminanza LAB
        "clahe_clip_limit": 1.6,          # Taglio massimo del contrasto locale (basso per evitare aloni)
        "clahe_tile_grid_size": (16, 16), # Dimensione delle celle locali di equalizzazione

        # Pulizia sfondo
        "white_clean_threshold": 252,

        # Flag di sicurezza: impedisce categoricamente l'uso di filtri morfologici dilatanti
        "allow_morphology_dilation": False,
    },

    # -------------------------------------------------------------------------
    # PRESET 3: STANDARD BILANCIATO (Tuttofare per normale penna a sfera)
    # -------------------------------------------------------------------------
    3: {
        "id": 3,
        "code": "P3",
        "name": "P3_Standard_Bilanciato",
        "titolo": "Standard Bilanciato / Penna Normale",
        "target_scenario": (
            "Condizioni ideali e standard di compilazione: penna a sfera blu o nera comune, "
            "pressione media sul foglio, carta bianca normale."
        ),
        "descrizione": (
            "Replica esatta della Curva ad S sigmoidea (Screenshot 3 Photoshop) unita a un "
            "incremento bilanciato del contrasto (+25%), leggera riduzione della luminosità (-10%) "
            "e filtro di nitidezza morbido. Produce neri profondi ma naturali e carta candida."
        ),
        # Punti di controllo della Curva ad S (Input, Output) calibrati su Photoshop Screenshot 3
        # I mezzitoni e i toni scuri vengono compressi verso il basso, mentre le alte luci restano alte.
        "s_curve_points": [
            (0, 0),
            (50, 5),
            (100, 15),
            (128, 26),
            (150, 35),
            (180, 65),
            (200, 85),
            (220, 135),
            (230, 160),
            (240, 195),
            (250, 231),
            (255, 255)
        ],

        # Correzioni aggiuntive post-curva
        "contrast_boost_factor": 1.25,   # Moltiplicatore di contrasto (+25%)
        "brightness_boost_delta": -10,   # Variazione di luminosità (-10 su scala 255)
        "white_clean_threshold": 252,    # Punto di bianco garantito

        # Nitidezza
        "sharpness_strength": 0.35,      # Unsharp mask moderato
        "unsharp_sigma": 1.0,
    },

    # -------------------------------------------------------------------------
    # PRESET 4: SBIANCA FONDO / OMBRE SCANNER (Carte scure o gradienti ADF)
    # -------------------------------------------------------------------------
    4: {
        "id": 4,
        "code": "P4",
        "name": "P4_Sbianca_Fondo",
        "titolo": "Sbianca Fondo / Ombre Scanner",
        "target_scenario": (
            "Fogli stampati su carta riciclata grigia o ingiallita, oppure scansioni ADF "
            "con ombre scure laterali, pieghe d'ombra o illuminazione marcatamente non uniforme."
        ),
        "descrizione": (
            "Stima la superficie continua dello sfondo cartaceo locale tramite un filtro "
            "Gaussiano ad ampio raggio ed esegue una normalizzazione per divisione fotometrica. "
            "Successivamente applica un CLAHE dinamico per massimizzare il contrasto dell'inchiostro, "
            "riportando l'intera pagina a bianco assoluto (255) uniforme."
        ),
        # Parametri per la stima dello sfondo locale
        "bg_blur_ksize": 51,             # Dimensione del kernel Gaussiano dispari (es. 51x51)
        "bg_blur_sigma": 25.0,           # Deviazione standard per sfocare completamente il testo

        # CLAHE dinamico per esaltare il testo dopo la divisione dello sfondo
        "clahe_clip_limit": 3.0,
        "clahe_tile_grid_size": (16, 16),

        # Normalizzazione del punto di bianco
        "white_clean_threshold": 245,    # I pixel normalizzati >= 245 diventano 255
        "post_contrast_boost": 1.10,     # Piccolo boost di contrasto finale (+10%)
    },

    # -------------------------------------------------------------------------
    # PRESET 5: MICRO-DETTAGLIO / TRATTO FINE (Tratti sottili o penne fini spezzate)
    # -------------------------------------------------------------------------
    5: {
        "id": 5,
        "code": "P5",
        "name": "P5_Micro_Dettaglio",
        "titolo": "Micro-Dettaglio / Tratto Fine",
        "target_scenario": (
            "Calligrafie tremule, tratti sottili eseguiti con penne a sfera a punta ultra-fine (0.3/0.5 mm) "
            "o tratti spezzettati/interrotti per scarso afflusso di inchiostro."
        ),
        "descrizione": (
            "Combina una maschera di contrasto (Unsharp Mask) aggressiva per marcare i micro-bordi "
            "con un CLAHE ad alta risoluzione locale (griglia 8x8) e un algoritmo di micro-ricongiunzione "
            "delicato (kernel 2x2 sfumato) che salda le interruzioni senza allargare le cifre."
        ),
        # Equalizzazione locale a micro-celle per piccoli dettagli
        "clahe_clip_limit": 2.2,
        "clahe_tile_grid_size": (8, 8),  # Celle piccole per cogliere micro-variazioni del tratto

        # Maschera di contrasto unsharp marcata
        "unsharp_amount": 1.8,           # Intensità elevata per far risaltare bordi e salti di gradiente
        "unsharp_sigma": 1.2,

        # Micro-ricongiunzione controllata del tratto
        "stroke_reconnect_enable": True,
        "stroke_reconnect_kernel_size": (2, 2),  # Kernel minimale 2x2 per evitare sovra-ispessimenti
        "stroke_reconnect_blend_weight": 0.25,   # Peso della fusione (25% ricongiunto, 75% unsharp puro)

        "white_clean_threshold": 250,
    }
}


# =============================================================================
# 2. SOGLIE PER IL CLASSIFICATORE AUTOMATICO (AUTO_CLASSIFIER_THRESHOLDS)
# =============================================================================

AUTO_CLASSIFIER_THRESHOLDS: Dict[str, Any] = {
    # -------------------------------------------------------------------------
    # Regola 1: Valutazione Fondo Carta e Ombre Scansione (Trigger per Preset 4)
    # -------------------------------------------------------------------------
    # Se la mediana dell'intera pagina è inferiore a questa soglia, la carta è grigia/scura
    "bg_dark_threshold": 225.0,

    # Se la differenza tra la luminosità dei bordi esterni (primi 4% del foglio) e il centro
    # supera questo valore, c'è un forte gradiente di ombra dovuto allo scanner o all'ADF
    "bg_shadow_delta_threshold": 26.0,

    # -------------------------------------------------------------------------
    # Regola 2: Valutazione Grafite / Matita Sbiadita (Trigger per Preset 1)
    # -------------------------------------------------------------------------
    # Nel riquadro centrale della tabella (righe 1a-10a), contiamo i caratteri/cifre
    # che presentano luminosità media elevata E densità minima (darkest pixel) priva
    # di un nucleo nero di inchiostro (min > pencil_char_min_threshold).
    # Questo previene falsi positivi dovuti all'anti-aliasing dei bordi su inchiostro scuro.
    "pencil_char_mean_threshold": 135.0,      # Soglia minima di luminosità media per il carattere
    "pencil_char_min_threshold": 100.0,       # Il pixel più scuro del carattere deve essere > 100
    "pencil_faint_chars_min": 3,              # Numero minimo assoluto di cifre a matita sbiadita
    "pencil_faint_ratio_min": 0.02,           # Percentuale minima di cifre sbiadite rispetto al totale cifre
    "pencil_char_mean_max_threshold": 150.0,  # Se una cifra isolata supera 150 con min > 120 -> scatta P1

    # -------------------------------------------------------------------------
    # Regola 3: Valutazione Micro-Tratto / Dettaglio Fine (Trigger per Preset 5)
    # -------------------------------------------------------------------------
    # Raggio medio del tratto calcolato tramite Distance Transform e normalizzato
    # rispetto a una larghezza standard di riferimento (2000 px) per indipendenza dal DPI.
    # Un tratto a penna ultra-fine (0.3/0.5 mm) ha raggio normalizzato < 1.12 px.
    "micro_stroke_radius_max": 1.12,

    # -------------------------------------------------------------------------
    # Regola 4: Valutazione Biro Marcata / Tratto Pesante (Trigger per Preset 2)
    # -------------------------------------------------------------------------
    # Se non vi sono tracce di matita sbiadita, l'inchiostro è saturo e il tratto normalizzato
    # è corposo/pesante (raggio normalizzato >= 1.28 px, tipico di biro cariche o pennarelli).
    "biro_char_mean_max_limit": 125.0,        # Tutti i caratteri hanno media grigio scura/densa
    "biro_stroke_radius_min": 1.28,           # Raggio normalizzato del tratto tipico di biro marcata

    # -------------------------------------------------------------------------
    # Regola 5: Default Standard Bilanciato (Preset 3)
    # -------------------------------------------------------------------------
    # Se nessuna delle condizioni estreme precedenti si verifica (tratto medio ~1.12-1.27 px
    # con normale penna a sfera blu o nera), viene applicato P3.
}


# =============================================================================
# 3. OPZIONI GENERALI DI ESPORTAZIONE E SALVATAGGIO (EXPORT_CONFIG)
# =============================================================================

EXPORT_CONFIG: Dict[str, Any] = {
    "jpeg_quality": 95,                        # Qualità di compressione JPEG per i test (1-100)
    "output_dir_name": "risultati_5_preset",   # Nome della cartella di output
    "add_text_watermark": False,               # Se True aggiunge la label del preset nell'angolo
}
