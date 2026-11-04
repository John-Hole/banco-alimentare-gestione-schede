"""preset_engine.py
=============================================================================
MOTORE COMPUTAZIONALE DEI 5 PRESET DOCUMENTALI E CLASSIFICATORE AUTOMATICO
=============================================================================
Progetto: Banco Alimentare - Acquisizione Schede Raccolta 'MOD. A'
Autore: Team Antigravity AI
Data: 2026-10-31

Questo modulo contiene l'implementazione matematica e algoritmica (OpenCV, NumPy, PIL)
dei 5 Preset di ottimizzazione delle scansioni, la funzione di auto-classificazione
intelligente `detect_best_preset(image)` e i servizi per la pipeline di scansione ed IA
(`prepare_scan_for_pipeline`, `create_ai_optimized_copy`, `clean_temp_ai_dir`).
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger("banco_alimentare.preset_engine")

# Import configurazioni centralizzate con fallback robusto
try:
    from .presets_config import (
        PRESETS_CONFIG,
        AUTO_CLASSIFIER_THRESHOLDS,
        EXPORT_CONFIG,
    )
except ImportError:
    try:
        from presets_config import (
            PRESETS_CONFIG,
            AUTO_CLASSIFIER_THRESHOLDS,
            EXPORT_CONFIG,
        )
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from presets_config import (
            PRESETS_CONFIG,
            AUTO_CLASSIFIER_THRESHOLDS,
            EXPORT_CONFIG,
        )

# Determinazione cartelle di base e directory temporanea IA
try:
    from ..config import BASE_DIR
except Exception:
    BASE_DIR = Path(__file__).resolve().parent.parent.parent

TEST_IMAGES_DIR = BASE_DIR / "test_images"
TEMP_AI_DIR = TEST_IMAGES_DIR / "temp_ai"
TEMP_AI_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# FUNZIONI DI UTILITÀ E CONVERSIONE IMMAGINE
# =============================================================================

def to_cv2_image(image_input: Union[np.ndarray, Image.Image, str, Path]) -> np.ndarray:
    """Converte qualsiasi input supportato (numpy, PIL Image o percorso file) in un ndarray OpenCV BGR (o Grayscale)."""
    if isinstance(image_input, (str, Path)):
        p = Path(image_input).resolve()
        if not p.exists():
            raise FileNotFoundError(f"File immagine non trovato: {p}")
        # Carica con np.fromfile + imdecode per supporto garantito a qualsiasi percorso Unicode su Windows
        img = None
        try:
            raw_bytes = np.fromfile(str(p), dtype=np.uint8)
            if raw_bytes.size > 0:
                img = cv2.imdecode(raw_bytes, cv2.IMREAD_UNCHANGED)
        except Exception:
            img = None
        if img is None:
            # Fallback sicuro via Pillow
            with Image.open(p) as pil_img:
                return to_cv2_image(pil_img)
        return img

    if isinstance(image_input, Image.Image):
        pil_img = image_input.copy()
        if pil_img.mode == "L":
            return np.array(pil_img, dtype=np.uint8)
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        rgb_arr = np.array(pil_img, dtype=np.uint8)
        return cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)

    if isinstance(image_input, np.ndarray):
        return image_input.copy()

    raise TypeError(f"Tipo di input non supportato per l'immagine: {type(image_input)}")


def to_pil_image(image_cv2: np.ndarray) -> Image.Image:
    """Converte un array numpy OpenCV (BGR o Scala di grigi) in un oggetto PIL.Image.Image."""
    if len(image_cv2.shape) == 2:
        return Image.fromarray(image_cv2, mode="L")
    if len(image_cv2.shape) == 3:
        if image_cv2.shape[2] == 3:
            rgb = cv2.cvtColor(image_cv2, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb, mode="RGB")
        if image_cv2.shape[2] == 4:
            rgba = cv2.cvtColor(image_cv2, cv2.COLOR_BGRA2RGBA)
            return Image.fromarray(rgba, mode="RGBA")
    raise ValueError(f"Formato array non supportato per conversione in PIL: shape {image_cv2.shape}")


def unsharp_mask(image: np.ndarray, sigma: float = 1.0, strength: float = 0.3) -> np.ndarray:
    """Applica un filtro Unsharp Mask (maschera di contrasto) per accentuare i bordi e la nitidezza.

    Formula: out = image * (1 + strength) - blurred * strength
    """
    if strength <= 0.0 or image.max() == 0 or image.min() == 255:
        return image.copy()
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma, sigmaY=sigma)
    sharpened = cv2.addWeighted(
        image.astype(np.float32), 1.0 + float(strength),
        blurred.astype(np.float32), -float(strength),
        0.0
    )
    return np.clip(sharpened, 0.0, 255.0).astype(np.uint8)


def apply_clahe_color_lab(image: np.ndarray, clip_limit: float = 2.0, grid_size: Tuple[int, int] = (16, 16)) -> np.ndarray:
    """Applica CLAHE (Contrast Limited Adaptive Histogram Equalization) esclusivamente
    sul canale Luminanza (L) dello spazio colore LAB, preservando cromaticità e bilanciamento del bianco.
    """
    if image.max() == 0 or image.min() == 255:
        return image.copy()

    if len(image.shape) == 2:
        clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=grid_size)
        return clahe.apply(image)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_chan, a_chan, b_chan = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=grid_size)
    l_enhanced = clahe.apply(l_chan)
    lab_merged = cv2.merge((l_enhanced, a_chan, b_chan))
    return cv2.cvtColor(lab_merged, cv2.COLOR_LAB2BGR)


def build_levels_lut(
    gamma_photoshop: float,
    in_black: int = 0,
    in_white: int = 252,
    white_clean_threshold: int = 250
) -> np.ndarray:
    """Genera una Look-Up Table (LUT) a 256 valori che replica esattamente il comportamento
    della finestra 'Livelli' (Levels) di Photoshop per la regolazione dei mezzitoni.

    Matematica:
      esponente = 1.0 / gamma_photoshop
      V_norm = clip((V_in - in_black) / (in_white - in_black), 0.0, 1.0)
      V_out = round(V_norm ** esponente * 255.0)
    """
    gamma_safe = max(float(gamma_photoshop), 0.001)
    exp = 1.0 / gamma_safe
    lut = np.zeros(256, dtype=np.uint8)
    in_b = float(in_black)
    in_w = float(in_white)
    denom = max(in_w - in_b, 1.0)

    for i in range(256):
        if i >= in_w:
            lut[i] = 255
        elif i <= in_b:
            lut[i] = 0
        else:
            norm = (float(i) - in_b) / denom
            val = (norm ** exp) * 255.0
            lut[i] = int(round(np.clip(val, 0.0, 255.0)))

    # Pulizia candore sfondo sopra la soglia
    if 0 <= white_clean_threshold <= 255:
        lut[white_clean_threshold:] = 255

    return lut


def build_scurve_lut(
    control_points: List[Tuple[int, int]],
    contrast_factor: float = 1.25,
    brightness_delta: int = -10,
    white_clean_threshold: int = 252
) -> np.ndarray:
    """Genera una Look-Up Table (LUT) a 256 valori a partire dai punti di controllo
    della Curva ad S sigmoidea (replica Screenshot 3 Photoshop) con aggiustamento
    combinato di contrasto e luminosità.
    """
    pts_x = [float(p[0]) for p in control_points]
    pts_y = [float(p[1]) for p in control_points]

    xs = np.arange(256, dtype=np.float32)
    # Interpolazione monotona lineare/polinomiale per una curva fluida senza oscillazioni Runge
    ys = np.interp(xs, pts_x, pts_y)

    # Applicazione contrasto e luminosità centrata sul punto medio (128)
    # Formula: V_mod = contrast_factor * (ys - 128) + 128 + brightness_delta
    ys_mod = float(contrast_factor) * (ys - 128.0) + 128.0 + float(brightness_delta)
    lut = np.clip(np.round(ys_mod), 0.0, 255.0).astype(np.uint8)

    if 0 <= white_clean_threshold <= 255:
        lut[white_clean_threshold:] = 255

    return lut


# =============================================================================
# IMPLEMENTAZIONE DEI 5 PRESET
# =============================================================================

def preset_1_matita_ultra_leggera(
    image: Union[np.ndarray, Image.Image, str, Path],
    params: Optional[Dict[str, Any]] = None
) -> np.ndarray:
    """Preset 1: 'Matita Ultra-Leggera'.

    Progettato appositamente per le schede compilate a matita con grafite sbiadita
    (come Scheda 1 - Tigre). Applica la curva non-lineare Photoshop Livelli Gamma ~0.14
    (esponente ~7.14) che recupera e scurisce anche i numeri più evanescenti,
    sbiancando al contempo lo sfondo cartaceo a 255 senza creare rumore grigio.
    """
    p = PRESETS_CONFIG[1].copy()
    if params:
        p.update(params)

    img = to_cv2_image(image)

    # 1. Costruzione ed applicazione della LUT della Curva Gamma Livelli
    lut = build_levels_lut(
        gamma_photoshop=p["gamma_photoshop"],
        in_black=p["in_black"],
        in_white=p["in_white"],
        white_clean_threshold=p["white_clean_threshold"]
    )
    curved = cv2.LUT(img, lut)

    # 2. Rifinitura di nitidezza morbida sui bordi del testo
    strength = float(p.get("sharpness_strength", 0.25))
    sigma = float(p.get("unsharp_sigma", 1.0))
    if strength > 0.0:
        curved = unsharp_mask(curved, sigma=sigma, strength=strength)

    return curved


def preset_2_biro_marcata(
    image: Union[np.ndarray, Image.Image, str, Path],
    params: Optional[Dict[str, Any]] = None
) -> np.ndarray:
    """Preset 2: 'Biro Marcata / Tratto Pesante'.

    Progettato per schede con calligrafia spessa a penna o pennarello (come Scheda 2 - Coop Triangolo).
    Applica un livellamento morbido (Gamma ~0.45) e un CLAHE leggero su canale LAB
    per distribuire il contrasto senza alterare la larghezza del tratto.
    NON effettua alcuna dilatazione/ingrossamento morfologico, garantendo che gli occhielli
    delle cifre '0', '6', '8', '9' rimangano perfettamente aperti e non si impastino.
    """
    p = PRESETS_CONFIG[2].copy()
    if params:
        p.update(params)

    img = to_cv2_image(image)

    # 1. CLAHE controllato sul canale Luminanza (LAB) per uniformità locale senza aloni
    clahed = apply_clahe_color_lab(
        img,
        clip_limit=p["clahe_clip_limit"],
        grid_size=p["clahe_tile_grid_size"]
    )

    # 2. Curva gamma morbida
    lut = build_levels_lut(
        gamma_photoshop=p["gamma_photoshop"],
        in_black=p["in_black"],
        in_white=p["in_white"],
        white_clean_threshold=p["white_clean_threshold"]
    )
    result = cv2.LUT(clahed, lut)

    return result


def preset_3_standard_bilanciato(
    image: Union[np.ndarray, Image.Image, str, Path],
    params: Optional[Dict[str, Any]] = None
) -> np.ndarray:
    """Preset 3: 'Standard Bilanciato'.

    Preset di riferimento universale per documenti con normale scrittura a penna a sfera
    blu o nera. Replica la Curva ad S di Photoshop (Screenshot 3) con contrasto +25%,
    luminosità -10% e unsharp mask morbido. Ottimo bilanciamento generale.
    """
    p = PRESETS_CONFIG[3].copy()
    if params:
        p.update(params)

    img = to_cv2_image(image)

    # 1. Costruzione ed applicazione LUT Curva ad S con contrasto e luminosità
    lut = build_scurve_lut(
        control_points=p["s_curve_points"],
        contrast_factor=p["contrast_boost_factor"],
        brightness_delta=p["brightness_boost_delta"],
        white_clean_threshold=p["white_clean_threshold"]
    )
    curved = cv2.LUT(img, lut)

    # 2. Maschera di nitidezza moderata
    strength = float(p.get("sharpness_strength", 0.35))
    sigma = float(p.get("unsharp_sigma", 1.0))
    if strength > 0.0:
        curved = unsharp_mask(curved, sigma=sigma, strength=strength)

    return curved


def preset_4_sbianca_fondo(
    image: Union[np.ndarray, Image.Image, str, Path],
    params: Optional[Dict[str, Any]] = None
) -> np.ndarray:
    """Preset 4: 'Sbianca Fondo / Ombre Scanner'.

    Ideale per fogli su carta riciclata scura, ingiallita o scansioni con ombre laterali
    e gradienti causati dall'ADF o dal piano dello scanner.
    Stima lo sfondo tramite Gaussian Blur ad ampio raggio ed esegue una normalizzazione
    fotometrica per divisione, seguita da CLAHE dinamico e bianco puro a 255.
    """
    p = PRESETS_CONFIG[4].copy()
    if params:
        p.update(params)

    img = to_cv2_image(image)
    if img.max() == 0 or img.min() == 255:
        return img.copy()

    is_grayscale = (len(img.shape) == 2)
    if is_grayscale:
        l_chan = img.copy()
    else:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l_chan, a_chan, b_chan = cv2.split(lab)

    ksize = int(p["bg_blur_ksize"])
    if ksize % 2 == 0:
        ksize += 1
    sigma = float(p["bg_blur_sigma"])

    # 1. Stima continua dello sfondo locale
    bg_est = cv2.GaussianBlur(l_chan, (ksize, ksize), sigmaX=sigma, sigmaY=sigma)

    # 2. Normalizzazione fotometrica per divisione: out = (l / bg) * 255
    l_float = l_chan.astype(np.float32)
    bg_float = np.maximum(bg_est.astype(np.float32), 1.0)
    l_norm = np.clip((l_float / bg_float) * 255.0, 0.0, 255.0).astype(np.uint8)

    # 3. CLAHE dinamico per recuperare contrasto elevato sul testo
    clahe = cv2.createCLAHE(
        clipLimit=float(p["clahe_clip_limit"]),
        tileGridSize=p["clahe_tile_grid_size"]
    )
    l_clahed = clahe.apply(l_norm)

    # 4. Normalizzazione del punto di bianco
    white_th = int(p["white_clean_threshold"])
    l_clahed[l_clahed >= white_th] = 255

    # 5. Piccolo boost di contrasto post-elaborazione
    post_boost = float(p.get("post_contrast_boost", 1.10))
    if post_boost != 1.0:
        l_boost = np.clip(post_boost * (l_clahed.astype(np.float32) - 128.0) + 128.0, 0.0, 255.0).astype(np.uint8)
        l_boost[l_clahed >= white_th] = 255
        l_final = l_boost
    else:
        l_final = l_clahed

    if is_grayscale:
        return l_final
    else:
        lab_out = cv2.merge((l_final, a_chan, b_chan))
        res_bgr = cv2.cvtColor(lab_out, cv2.COLOR_LAB2BGR)
        res_bgr[l_final >= white_th] = (255, 255, 255)
        return res_bgr


def preset_5_micro_dettaglio(
    image: Union[np.ndarray, Image.Image, str, Path],
    params: Optional[Dict[str, Any]] = None
) -> np.ndarray:
    """Preset 5: 'Micro-Dettaglio / Tratto Fine'.

    Ottimizzato per calligrafie tremule, tratti sottili eseguiti con penne a punta fine
    o tratti spezzati/discontinui. Combina unsharp mask aggressivo, CLAHE a micro-celle (8x8)
    e una micro-ricongiunzione controllata per saldare le interruzioni senza allargare le cifre.
    """
    p = PRESETS_CONFIG[5].copy()
    if params:
        p.update(params)

    img = to_cv2_image(image)

    # 1. CLAHE a celle microscopiche (8x8) per esaltare il micro-contrasto locale
    clahed = apply_clahe_color_lab(
        img,
        clip_limit=float(p["clahe_clip_limit"]),
        grid_size=p["clahe_tile_grid_size"]
    )

    # 2. Maschera di contrasto (Unsharp Mask) aggressiva
    amount = float(p["unsharp_amount"])
    sigma = float(p["unsharp_sigma"])
    sharp = unsharp_mask(clahed, sigma=sigma, strength=amount)

    # 3. Micro-ricongiunzione controllata del tratto
    if p.get("stroke_reconnect_enable", True):
        ksize = p.get("stroke_reconnect_kernel_size", (2, 2))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, ksize)
        eroded = cv2.erode(sharp, kernel, iterations=1)
        w = float(p.get("stroke_reconnect_blend_weight", 0.25))
        reconnected = cv2.addWeighted(sharp, 1.0 - w, eroded, w, 0.0)
    else:
        reconnected = sharp

    # 4. Pulizia sfondo
    white_th = int(p.get("white_clean_threshold", 250))
    if white_th <= 255:
        if len(reconnected.shape) == 2:
            reconnected[reconnected >= white_th] = 255
        else:
            mask = (reconnected[:, :, 0] >= white_th) & (reconnected[:, :, 1] >= white_th) & (reconnected[:, :, 2] >= white_th)
            reconnected[mask] = (255, 255, 255)

    return reconnected


# Aliases per richiamo diretto
def apply_preset_0(
    image: Union[np.ndarray, Image.Image, str, Path],
    return_pil: bool = False,
    **kwargs: Any,
) -> Union[np.ndarray, Image.Image]:
    """Preset 0: 'P0' – Nessuna modifica, immagine originale pura non alterata."""
    return apply_preset(image, 0, return_pil=return_pil, **kwargs)

apply_preset_1 = preset_1_matita_ultra_leggera
apply_preset_2 = preset_2_biro_marcata
apply_preset_3 = preset_3_standard_bilanciato
apply_preset_4 = preset_4_sbianca_fondo
apply_preset_5 = preset_5_micro_dettaglio


# =============================================================================
# DISPATCHER APPLICAZIONE PRESET
# =============================================================================

def apply_preset(
    image: Union[np.ndarray, Image.Image, str, Path],
    preset: Union[int, str],
    custom_params: Optional[Dict[str, Any]] = None,
    return_pil: bool = False,
    **overrides: Any,
) -> Union[np.ndarray, Image.Image]:
    """Applica il preset specificato (tramite ID intero 1..5 o stringa 'P1'..'P5') all'immagine.

    Se preset è 0 o 'RESET' / 'ORIGINALE', restituisce l'immagine non alterata.
    """
    params = (custom_params or {}).copy()
    if overrides:
        params.update(overrides)

    preset_id = None
    if isinstance(preset, int):
        preset_id = preset
    elif isinstance(preset, str):
        preset_clean = preset.strip().upper()
        if preset_clean in ("0", "P0", "P0_ORIGINALE", "P0_STANDARD", "RESET", "ORIGINALE", "ORIGINAL"):
            preset_id = 0
        elif preset_clean in ("1", "P1", "P1_MATITA_ULTRALEGGERA"):
            preset_id = 1
        elif preset_clean in ("2", "P2", "P2_BIRO_MARCATA"):
            preset_id = 2
        elif preset_clean in ("3", "P3", "P3_STANDARD_BILANCIATO"):
            preset_id = 3
        elif preset_clean in ("4", "P4", "P4_SBIANCA_FONDO"):
            preset_id = 4
        elif preset_clean in ("5", "P5", "P5_MICRO_DETTAGLIO"):
            preset_id = 5

    if preset_id == 0:
        cv_img = to_cv2_image(image)
        if return_pil:
            return to_pil_image(cv_img)
        return cv_img

    if preset_id not in PRESETS_CONFIG:
        raise ValueError(f"Preset non riconosciuto: '{preset}'. I preset validi sono 1, 2, 3, 4, 5 (o 0 per reset).")

    handlers = {
        1: preset_1_matita_ultra_leggera,
        2: preset_2_biro_marcata,
        3: preset_3_standard_bilanciato,
        4: preset_4_sbianca_fondo,
        5: preset_5_micro_dettaglio,
    }

    result = handlers[preset_id](image, params=params if params else None)

    if return_pil:
        return to_pil_image(result)
    return result


# =============================================================================
# ALGORITMO DI CLASSIFICAZIONE INTELLIGENTE AUTOMATICA
# =============================================================================

def detect_best_preset(
    image: Union[np.ndarray, Image.Image, str, Path],
    custom_thresholds: Optional[Dict[str, Any]] = None
) -> Tuple[int, str, Dict[str, Any]]:
    """Analizza la matrice dell'immagine e determina automaticamente il preset più adatto.

    Restituisce una tupla:
      (preset_id: int, preset_name: str, metrics: dict)
    """
    th = AUTO_CLASSIFIER_THRESHOLDS.copy()
    if custom_thresholds:
        th.update(custom_thresholds)

    img = to_cv2_image(image)
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Se l'immagine è verticale, ruotiamo temporaneamente per allineare l'analisi della tabella
    h, w = gray.shape
    if h > w:
        gray = cv2.rotate(gray, cv2.ROTATE_90_CLOCKWISE)
        h, w = gray.shape

    # 1. Metriche dello Sfondo Carta
    bg_median = float(np.median(gray))
    bg_p95 = float(np.percentile(gray, 95))

    pad_y = max(int(h * 0.04), 10)
    pad_x = max(int(w * 0.04), 10)
    top_b = float(gray[:pad_y, :].mean())
    bot_b = float(gray[-pad_y:, :].mean())
    left_b = float(gray[:, :pad_x].mean())
    right_b = float(gray[:, -pad_x:].mean())
    min_border = min(top_b, bot_b, left_b, right_b)
    border_mean = (top_b + bot_b + left_b + right_b) / 4.0

    center_region = float(gray[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)].mean())
    edge_shadow_delta = max(0.0, bg_median - min_border)
    bg_shadow_delta = max(abs(border_mean - center_region), edge_shadow_delta)

    # 2. Analisi dell'Inchiostro e Calligrafia nella Tabella Dati (Righe 1a-10a)
    y_start, y_end = int(h * 0.35), int(h * 0.82)
    x_start, x_end = int(w * 0.18), int(w * 0.92)
    table_crop = gray[y_start:y_end, x_start:x_end]

    # Rimozione linee di griglia stampate
    _, b_dark = cv2.threshold(table_crop, 215, 255, cv2.THRESH_BINARY_INV)
    h_len = max(int(w * 0.02), 15)
    v_len = max(int(h * 0.02), 15)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (h_len, 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, v_len))
    lines_h = cv2.morphologyEx(b_dark, cv2.MORPH_OPEN, h_kernel)
    lines_v = cv2.morphologyEx(b_dark, cv2.MORPH_OPEN, v_kernel)
    table_lines = cv2.bitwise_or(lines_h, lines_v)
    lines_dil = cv2.dilate(table_lines, np.ones((5, 5), np.uint8))

    # Maschera dei caratteri e numeri compilati a mano
    hw_candidates = (b_dark > 0) & (lines_dil == 0)

    num_cc, labels, stats, _ = cv2.connectedComponentsWithStats(hw_candidates.astype(np.uint8))
    faint_chars = 0
    total_chars = 0
    char_means: List[float] = []
    char_mins: List[float] = []

    char_mean_th = float(th.get("pencil_char_mean_threshold", 135.0))
    char_min_th = float(th.get("pencil_char_min_threshold", 100.0))

    for i in range(1, num_cc):
        area = stats[i, cv2.CC_STAT_AREA]
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        if 15 < area < 3000 and 5 < cw < int(w * 0.15) and 8 < ch < int(h * 0.10):
            vals = table_crop[labels == i]
            mean_val = float(vals.mean())
            min_val = float(vals.min())
            char_means.append(mean_val)
            char_mins.append(min_val)
            total_chars += 1
            if mean_val > char_mean_th and min_val > char_min_th:
                faint_chars += 1

    faint_char_ratio = float(faint_chars / max(total_chars, 1))
    char_means_max = float(max(char_means)) if char_means else 0.0
    char_means_avg = float(np.mean(char_means)) if char_means else 0.0

    # Spessore medio del tratto con Distance Transform
    if np.any(hw_candidates):
        dist = cv2.distanceTransform(hw_candidates.astype(np.uint8), cv2.DIST_L2, 3)
        dist_vals = dist[hw_candidates > 0]
        mean_stroke_r = float(dist_vals.mean()) if len(dist_vals) > 0 else 0.0
    else:
        mean_stroke_r = 0.0

    # Normalizzazione raggio rispetto a larghezza standard 2000px
    scale_to_std = 2000.0 / max(float(w), 1.0)
    norm_stroke_r = mean_stroke_r * scale_to_std

    metrics = {
        "bg_median": round(bg_median, 2),
        "bg_p95": round(bg_p95, 2),
        "bg_shadow_delta": round(bg_shadow_delta, 2),
        "total_handwriting_chars": total_chars,
        "faint_chars_count": faint_chars,
        "faint_char_ratio": round(faint_char_ratio, 4),
        "char_means_max": round(char_means_max, 2),
        "char_means_avg": round(char_means_avg, 2),
        "mean_stroke_radius": round(mean_stroke_r, 3),
        "norm_stroke_radius": round(norm_stroke_r, 3),
    }

    # 3. Albero Decisionale di Classificazione

    # Regola 1: Fondo Sporco o Forti Ombre Scanner -> P4
    if (bg_median < th["bg_dark_threshold"] or
            bg_shadow_delta > th["bg_shadow_delta_threshold"]):
        metrics["decision_rule"] = "Regola 1: Sfondo non uniforme o ombra laterale elevata"
        return 4, PRESETS_CONFIG[4]["name"], metrics

    # Regola 2: Presenza di Tratti a Matita Sbiadita -> P1
    pencil_chars_min = int(th.get("pencil_faint_chars_min", 3))
    pencil_ratio_min = float(th.get("pencil_faint_ratio_min", 0.02))
    pencil_max_th = float(th.get("pencil_char_mean_max_threshold", 150.0))

    has_faint_pencil = (
        (faint_chars >= pencil_chars_min and faint_char_ratio >= pencil_ratio_min) or
        (faint_chars >= 1 and char_means_max >= pencil_max_th and any(m >= 120.0 for m in char_mins))
    )
    if has_faint_pencil:
        metrics["decision_rule"] = (
            f"Regola 2: Rilevati tratti a matita sbiadita (cifre deboli: {faint_chars}, "
            f"ratio: {faint_char_ratio*100:.1f}%, max_gray: {char_means_max:.1f})"
        )
        return 1, PRESETS_CONFIG[1]["name"], metrics

    # Regola 3: Tratto Ultra-Fine / Penna Spezzettata -> P5
    micro_radius_max = float(th.get("micro_stroke_radius_max", 1.12))
    if total_chars > 0 and 0.0 < norm_stroke_r < micro_radius_max:
        metrics["decision_rule"] = (
            f"Regola 3: Tratto ultra-fine (raggio norm: {norm_stroke_r:.2f} px < {micro_radius_max:.2f} px)"
        )
        return 5, PRESETS_CONFIG[5]["name"], metrics

    # Regola 4: Biro Marcata / Tratto Pesante Senza Sbiaditure -> P2
    biro_mean_max = float(th.get("biro_char_mean_max_limit", 125.0))
    biro_radius_min = float(th.get("biro_stroke_radius_min", 1.28))
    if (faint_chars == 0 and
            char_means_max <= biro_mean_max and
            norm_stroke_r >= biro_radius_min):
        metrics["decision_rule"] = (
            f"Regola 4: Inchiostro scuro uniforme e tratto pesante (raggio norm: {norm_stroke_r:.2f} px >= {biro_radius_min:.2f} px)"
        )
        return 2, PRESETS_CONFIG[2]["name"], metrics

    # Regola 5: Default Bilanciato -> P3
    metrics["decision_rule"] = (
        f"Regola 5: Condizioni standard di compilazione a penna normale (raggio norm: {norm_stroke_r:.2f} px)"
    )
    return 3, PRESETS_CONFIG[3]["name"], metrics


# =============================================================================
# SERVIZI DI PIPELINE PER SCANSIONE ED INVIO ALL'IA
# =============================================================================

def clean_temp_ai_dir() -> int:
    """Rimuove tutti i file temporanei nella cartella test_images/temp_ai/.
    Restituisce il numero di file rimossi.
    """
    count = 0
    if not TEMP_AI_DIR.exists():
        return count
    for f in TEMP_AI_DIR.glob("*"):
        if f.is_file():
            try:
                f.unlink(missing_ok=True)
                count += 1
            except Exception as e:
                logger.debug(f"Impossibile rimuovere file temporaneo {f}: {e}")
    return count


def create_ai_optimized_copy(image_path: Union[str, Path], preset_id: int) -> Path:
    """Genera e salva la copia ottimizzata per l'IA con il preset specificato nella cartella temp_ai/.

    Se preset_id == 0, genera una copia JPEG di alta qualità dell'immagine originale.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Immagine sorgente non trovata per copia IA: {path}")

    TEMP_AI_DIR.mkdir(parents=True, exist_ok=True)
    temp_target = TEMP_AI_DIR / f"{path.stem}_p{preset_id}.jpg"

    if preset_id == 0:
        # Copia o risalvataggio pulito
        with Image.open(path) as img:
            rgb = img.convert("RGB") if img.mode != "RGB" else img
            rgb.save(str(temp_target), format="JPEG", quality=95)
    else:
        enhanced = apply_preset(path, preset_id, return_pil=False)
        if isinstance(enhanced, np.ndarray):
            success, enc = cv2.imencode(".jpg", enhanced, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            if success:
                temp_target.write_bytes(enc.tobytes())
            else:
                to_pil_image(enhanced).save(str(temp_target), format="JPEG", quality=95)
        else:
            enhanced.save(str(temp_target), format="JPEG", quality=95)

    return temp_target


def prepare_scan_for_pipeline(
    image_path: Union[str, Path],
    auto_detect_preset: bool = True
) -> Tuple[Path, int, str]:
    """Esegue la sequenza completa di preparazione della scansione per la pipeline:
    1. Verifica e raddrizzamento dell'orientamento su disco (~50 ms).
    2. Rilevamento automatico del preset ottimale se auto_detect_preset=True, altrimenti Preset 0 (P0).
    3. Generazione e salvataggio della copia ottimizzata per l'IA in test_images/temp_ai/.

    Restituisce: (path_ottimizzato_ia, preset_id, preset_name)
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Immagine non trovata per la pipeline: {path}")

    # 1. Raddrizzamento orientamento
    try:
        from .scanner_service import ScannerService
        ScannerService.ensure_upright_orientation(path)
    except Exception as e:
        logger.warning(f"Avviso raddrizzamento orientamento per {path}: {e}")

    # 2. Preset
    if auto_detect_preset:
        preset_id, preset_name, _metrics = detect_best_preset(path)
    else:
        preset_id = 0
        preset_name = "P0"

    # 3. Generazione copia temporanea per l'IA
    ai_copy_path = create_ai_optimized_copy(path, preset_id)

    return ai_copy_path, preset_id, preset_name


def deskew_image(img: Any, return_angle: bool = False) -> Any:
    """Raddrizza automaticamente l'inclinazione (fine deskewing) della scheda delegando a ScannerService."""
    from .scanner_service import ScannerService
    return ScannerService.deskew_image(img, return_angle=return_angle)

