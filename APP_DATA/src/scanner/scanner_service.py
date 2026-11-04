"""Servizio per l'acquisizione di immagini da scanner WIA o file locale."""

import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple, Union
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps

from ..config import BASE_DIR
from ..services.session_logger import get_session_logger

try:
    import win32com.client
except ImportError:
    win32com = None

TEST_IMAGES_DIR = BASE_DIR / "test_images"
TEST_IMAGES_DIR.mkdir(exist_ok=True)


# --- Costanti WIA ---
WIA_FORMAT_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
WIA_DEVICE_TYPE_SCANNER = 1
WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES = 3086
WIA_DPS_DOCUMENT_HANDLING_STATUS = 3087
WIA_DPS_DOCUMENT_HANDLING_SELECT = 3088
WIA_DPS_PAGES = 3096
WIA_DPS_SHEET_FEEDER_REGISTRATION = 3078
WIA_DPS_PAGE_SIZE = 3097
WIA_DPS_PAGE_WIDTH = 3098
WIA_DPS_PAGE_HEIGHT = 3099
WIA_PAGE_A4 = 0
WIA_IPS_XRES = 6147
WIA_IPS_YRES = 6148
WIA_IPS_XPOS = 6149
WIA_IPS_YPOS = 6150
WIA_IPS_XEXTENT = 6151
WIA_IPS_YEXTENT = 6152
WIA_IPS_PAGE_SIZE = 6164

# Valori di WIA_DPS_DOCUMENT_HANDLING_SELECT e WIA_DPS_DOCUMENT_HANDLING_STATUS
WIA_HANDLING_FEEDER = 1
WIA_HANDLING_FLATBED = 2
FEED_READY = 0x01
WIA_FEED_READY = 0x01

# Codici COM WIA
WIA_ERROR_PAPER_EMPTY = 0x80210003
WIA_ERROR_PAPER_JAM = 0x80210002
WIA_ERROR_PAPER_PROBLEM = 0x80210004
WIA_ERROR_OFFLINE = 0x80210005
WIA_ERROR_BUSY = 0x80210006
WIA_ERROR_NO_DEVICE = 0x80210015
WIA_ERROR_COVER_OPEN = 0x80210016
# Codice COM WIA / eSCL: HTTP 409 Conflict (0x80190000 | 409)
# Sollevato dal driver eSCL su Windows quando l'ADF è vuoto o ha terminato i fogli
HTTP_STATUS_CONFLICT = 0x80190199

SOURCE_FEEDER = "adf"
SOURCE_FLATBED = "flatbed"

_FRIENDLY_WIA_ERRORS = {
    WIA_ERROR_PAPER_JAM: "Inceppamento carta nello scanner. Rimuovere il foglio incastrato e riprovare.",
    WIA_ERROR_PAPER_PROBLEM: "Problema con la carta nello scanner. Controllare i fogli e riprovare.",
    WIA_ERROR_BUSY: "Lo scanner è occupato. Attendere qualche secondo e riprovare.",
    WIA_ERROR_COVER_OPEN: "Il coperchio dello scanner è aperto. Chiuderlo e riprovare.",
}


class FeederNotAvailableError(RuntimeError):
    """Lo scanner non dispone (o non espone) del modulo alimentatore automatico (ADF)."""


class ScanBatchError(RuntimeError):
    """Errore durante un lotto: porta con sé le pagine già acquisite con successo."""

    def __init__(self, message: str, pages: Optional[List[Path]] = None):
        super().__init__(message)
        self.pages: List[Path] = pages or []


class ScannerService:
    """Gestisce l'acquisizione da scanner Windows (WIA) o selezione file."""

    # Ultimo scanner selezionato dall'utente (evita di richiedere il dispositivo ad ogni lotto)
    _device_id: Optional[str] = None
    _last_known_ip: Optional[str] = None

    # Numero di pagine bianche scartate nell'ultima acquisizione / caricamento
    last_blank_skipped: int = 0

    @staticmethod
    def is_blank_page(image_path: Path, ink_ratio_threshold: float = 0.0025) -> bool:
        """True se la pagina è praticamente bianca (nessuna scrittura / stampa significativa).

        Ignora i bordi (ombre dello scanner), stima il colore della carta con la mediana e conta i
        pixel sensibilmente più scuri. In caso di errore di lettura NON scarta la pagina.
        """
        try:
            from PIL import ImageChops, ImageFilter
            with Image.open(image_path) as img:
                img.draft("L", (1200, 1200))
                gray = img.convert("L")
                gray.thumbnail((900, 900))
            w, h = gray.size
            mx, my = int(w * 0.06), int(h * 0.06)
            core = gray.crop((mx, my, w - mx, h - my))
            # Filtro passa-alto: isola scrittura/linee/stampa rispetto allo sfondo locale. In questo modo
            # una scansione chiara/sbiadita ma con contenuto NON viene scambiata per bianca, mentre
            # sfumature, ombre e rumore della carta restano sotto soglia.
            background = core.filter(ImageFilter.GaussianBlur(10))
            diff = ImageChops.difference(core, background)
            hist = diff.histogram()
            total = sum(hist)
            if total == 0:
                return False
            ink = sum(hist[10:])
            return (ink / total) < ink_ratio_threshold
        except Exception:
            return False

    @staticmethod
    def is_page_upside_down(img: Any) -> bool:
        """Determina se la scheda 'MOD. A' è orientata capovolta / sottosopra (180°).

        Accetta sia un oggetto PIL.Image.Image che un Path/str del file.
        Combina l'analisi della fascia di testata superiore (15%) rispetto alla coda bianca (15%)
        e il baricentro dell'inchiostro nei terzi superiore/inferiore (35%).
        """
        try:
            from PIL import Image
            import numpy as np

            if isinstance(img, (str, Path)):
                with Image.open(img) as opened_img:
                    opened_img.load()
                    return ScannerService.is_page_upside_down(opened_img)

            work_img = img
            if work_img.height > work_img.width:
                work_img = work_img.rotate(90, expand=True)
            work_img = ScannerService._trim_overscan_padding(work_img)

            gray = work_img.convert("L")
            if gray.width > 1200:
                h_new = int(1200 * gray.height / gray.width)
                gray = gray.resize((1200, h_new), Image.Resampling.BILINEAR)

            arr = np.array(gray)
            h, w = arr.shape
            pad_y = max(int(h * 0.04), 8)
            pad_x = max(int(w * 0.04), 8)
            core = arr[pad_y:h - pad_y, pad_x:w - pad_x]
            ch, cw = core.shape

            ink = (core < 195) & (core > 20)
            y_coords = np.where(ink)[0]
            if len(y_coords) < 800:
                return False

            y_mean = np.mean(y_coords) / ch
            y_median = np.median(y_coords) / ch

            top_margin = np.sum(ink[:int(ch * 0.15), :])
            bot_margin = np.sum(ink[int(ch * 0.85):, :])
            margin_ratio = (top_margin + 1.0) / (bot_margin + 1.0)

            top_third = np.sum(ink[:int(ch * 0.35), :])
            bot_third = np.sum(ink[int(ch * 0.65):, :])
            core_ratio = (top_third + 1.0) / (bot_third + 1.0)

            # Rilevamento netto tramite fascia di testata/margine (15%)
            if margin_ratio < 0.70:
                return True
            if margin_ratio > 1.40:
                return False

            # Valutazione congiunta multi-segnale
            votes = 0
            if y_mean > 0.49:
                votes += 1
            if y_median > 0.49:
                votes += 1
            if core_ratio < 0.95:
                votes += 1
            if margin_ratio < 0.90:
                votes += 1

            return votes >= 2
        except Exception as e:
            print(f"Avviso rilevamento orientamento: {e}")
            return False

    @staticmethod
    def ensure_upright_orientation(image_path: Path | str) -> bool:
        """Verifica l'orientamento della scheda su disco e la ruota automaticamente se rovesciata.

        1. Se è in formato verticale (portrait), la ruota a 90° in orizzontale.
        2. Se è rovesciata/capovolta (180°), la rigira di 180° per renderla diritta.
        Restituisce True se l'immagine è stata modificata/ruotata, False altrimenti.
        """
        path = Path(image_path)
        logger = get_session_logger()
        if not path.exists():
            return False
        try:
            with Image.open(path) as img:
                img.load()
                changed = False
                orig_dim = f"{img.width}x{img.height}"
                if img.height > img.width:
                    img = img.rotate(90, expand=True)
                    changed = True
                    logger.log_orientation_event(str(path), 90, f"Ruotata a orizzontale (da {orig_dim} a {img.width}x{img.height})")
                if ScannerService.is_page_upside_down(img):
                    img = img.rotate(180, expand=True)
                    changed = True
                    logger.log_orientation_event(str(path), 180, "Rilevata capovolta: ruotata di 180°")

                # 3. Fine Deskewing automatico (inclinazione -5.0° .. +5.0°)
                deskewed_img, deskew_ang = ScannerService.deskew_image(img, return_angle=True)
                if abs(deskew_ang) > 0.2 and abs(deskew_ang) <= 5.0:
                    img = deskewed_img
                    changed = True
                    logger.log_orientation_event(str(path), round(deskew_ang, 2), f"Fine Deskewing: raddrizzata di {deskew_ang:+.2f}°")

                if changed:
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    img.save(str(path), format="JPEG", quality=95)
                    return True
                else:
                    logger.log_orientation_event(str(path), 0, "Orientamento corretto (orizzontale dritto)")
        except Exception as e:
            logger.logger.warning(f"[ORIENTAMENTO] Avviso verifica orientamento {path}: {e}")
        return False

    @staticmethod
    def detect_skew_angle(img: Any) -> float:
        """Rileva l'angolo mediano di inclinazione (skew) in gradi usando OpenCV Canny + HoughLinesP.

        Cerca i tratti orizzontali della griglia stampata della tabella.
        Restituisce l'angolo theta in gradi se compreso tra -5.0° e +5.0°, altrimenti 0.0°.
        """
        try:
            import cv2
            import numpy as np
            import math
            from PIL import Image

            if isinstance(img, (str, Path)):
                p = Path(img)
                if not p.exists():
                    return 0.0
                raw = np.fromfile(str(p), dtype=np.uint8)
                if raw.size == 0:
                    return 0.0
                cv_img = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            elif isinstance(img, Image.Image):
                cv_img = np.array(img.convert("L"))
            elif isinstance(img, np.ndarray):
                cv_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
            else:
                return 0.0

            if cv_img is None or cv_img.size == 0:
                return 0.0

            h, w = cv_img.shape
            if h > w:
                cv_img = cv2.rotate(cv_img, cv2.ROTATE_90_CLOCKWISE)
                h, w = cv_img.shape

            edges = cv2.Canny(cv_img, 50, 150, apertureSize=3)
            min_line_len = max(int(w * 0.08), 60)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=min_line_len, maxLineGap=20)
            angles = []
            if lines is not None:
                for l in lines:
                    x1, y1, x2, y2 = l.ravel()
                    dx = x2 - x1
                    dy = y2 - y1
                    if abs(dx) > abs(dy) * 2:  # Prevalentemente orizzontale
                        ang = math.degrees(math.atan2(dy, dx))
                        if ang > 90:
                            ang -= 180
                        elif ang < -90:
                            ang += 180
                        if -5.0 <= ang <= 5.0:
                            angles.append(ang)

            if len(angles) >= 5:
                return float(np.median(angles))
            return 0.0
        except Exception:
            return 0.0

    @staticmethod
    def deskew_image(img: Any, return_angle: bool = False) -> Any:
        """Raddrizza automaticamente l'inclinazione (fine deskewing) della scheda.

        - Rileva l'angolo mediano theta con detect_skew_angle.
        - Se -5.0° <= theta <= 5.0° e |theta| > 0.2°, ruota l'immagine di theta gradi con sfondo bianco puro.
        - Se img è Path o str, salva l'immagine corretta su disco e restituisce il Path.
        - Se img è PIL.Image.Image, restituisce l'immagine corretta.
        """
        try:
            from PIL import Image

            if isinstance(img, (str, Path)):
                p = Path(img)
                if not p.exists():
                    return (p, 0.0) if return_angle else p
                angle = 0.0
                with Image.open(p) as pil_img:
                    pil_img.load()
                    corrected, angle = ScannerService.deskew_image(pil_img, return_angle=True)
                    if abs(angle) > 0.2 and abs(angle) <= 5.0:
                        if corrected.mode != "RGB":
                            corrected = corrected.convert("RGB")
                        corrected.save(str(p), format="JPEG", quality=95)
                        logger = get_session_logger()
                        logger.log_orientation_event(str(p), round(angle, 2), f"Fine Deskewing: raddrizzata di {angle:+.2f}°")
                return (p, angle) if return_angle else p

            if isinstance(img, Image.Image):
                angle = ScannerService.detect_skew_angle(img)
                if abs(angle) > 0.2 and abs(angle) <= 5.0:
                    fill = (255, 255, 255) if img.mode in ("RGB", "RGBA") else 255
                    corrected = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=fill)
                    return (corrected, angle) if return_angle else corrected
                return (img, angle) if return_angle else img

            import cv2
            import numpy as np
            if isinstance(img, np.ndarray):
                angle = ScannerService.detect_skew_angle(img)
                if abs(angle) > 0.2 and abs(angle) <= 5.0:
                    h, w = img.shape[:2]
                    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
                    border = (255, 255, 255) if len(img.shape) == 3 else 255
                    corrected = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=border)
                    return (corrected, angle) if return_angle else corrected
                return (img, angle) if return_angle else img

        except Exception as e:
            get_session_logger().logger.warning(f"[DESKEW] Errore raddrizzamento scansione: {e}")

        return (img, 0.0) if return_angle else img

    @staticmethod
    def rotate_image_file_180(image_path: Path | str) -> Path:
        """Ruota fisicamente di 180° l'immagine salvata su disco."""
        path = Path(image_path)
        logger = get_session_logger()
        if not path.exists():
            return path
        try:
            with Image.open(path) as img:
                img.load()
                rot = img.rotate(180, expand=True)
                if rot.mode != "RGB":
                    rot = rot.convert("RGB")
                rot.save(str(path), format="JPEG", quality=95)
                logger.log_orientation_event(str(path), 180, "Rotazione manuale/IA di 180° applicata")
        except Exception as e:
            logger.logger.error(f"[ORIENTAMENTO] Errore rotazione 180 file {path}: {e}")
        return path

    # ------------------------------------------------------------------ helper WIA

    @staticmethod
    def _com_codes(exc: BaseException) -> set:
        """Estrae i codici HRESULT (anche quelli annidati in excepinfo) da una com_error."""
        codes = set()
        args = getattr(exc, "args", ()) or ()
        if args and isinstance(args[0], int):
            codes.add(args[0] & 0xFFFFFFFF)
        if len(args) > 2 and isinstance(args[2], (tuple, list)) and len(args[2]) > 5:
            scode = args[2][5]
            if isinstance(scode, int):
                codes.add(scode & 0xFFFFFFFF)
        for m in re.findall(r"0x[0-9a-fA-F]{8}", str(exc)):
            codes.add(int(m, 16))
        return codes

    @staticmethod
    def _is_paper_empty(exc: BaseException, device: Any = None, is_feeder: bool = True) -> bool:
        """Verifica se l'eccezione indica vassoio vuoto o fine carta naturale.

        Riconosce:
        - Codice nativo WIA WIA_ERROR_PAPER_EMPTY (0x80210003)
        - Codice HTTP 409 Conflict (0x80190199) tipico del driver Windows eSCL / AirPrint
        - Stringhe descrittive in italiano o inglese ("carta esaurita", "nessun foglio", "conflitto", ecc.)
        - Codici WIA_ERROR_BUSY (0x80210006) o WIA_ERROR_PAPER_PROBLEM (0x80210004) durante scansione ADF
          quando il caricatore ha esaurito i fogli.
        """
        codes = ScannerService._com_codes(exc)
        if WIA_ERROR_PAPER_EMPTY in codes:
            return True

        if is_feeder and HTTP_STATUS_CONFLICT in codes:
            return True

        msg = str(exc).lower()
        empty_keywords = (
            "paper_empty",
            "paper empty",
            "carta esaurita",
            "nessun foglio",
            "caricatore vuoto",
            "cassetto vuoto",
            "vassoio vuoto",
            "feeder empty",
            "out of paper",
            "no paper",
        )
        if any(kw in msg for kw in empty_keywords):
            return True

        # Riconoscimento del messaggio eSCL (italiano / inglese)
        if is_feeder and any(kw in msg for kw in ("409", "conflitto", "conflict")):
            return True

        # Alcuni driver ADF segnalano PAPER_PROBLEM a fine carta
        if is_feeder and WIA_ERROR_PAPER_PROBLEM in codes:
            return True

        return False

    @staticmethod
    def _set_prop(props, prop_id: int, value) -> bool:
        """Imposta (best effort) una proprietà WIA tramite il suo ID. True se riuscito."""
        try:
            for p in props:
                if p.PropertyID == prop_id:
                    p.Value = value
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _get_prop(props, prop_id: int):
        try:
            for p in props:
                if p.PropertyID == prop_id:
                    return p.Value
        except Exception:
            pass
        return None

    @staticmethod
    def is_feeder_paper_present(device: Any = None) -> bool:
        """Compatibilità: ritorna sempre True per non bloccare mai la scansione con controlli preventivi."""
        return True

    is_feeder_ready = is_feeder_paper_present

    @staticmethod
    def resolve_device_id(force_select: bool = False) -> Optional[str]:
        """Restituisce l'ID dello scanner Windows (WIA) da usare. DA CHIAMARE SUL THREAD PRINCIPALE.

        Funzionamento universale per qualsiasi scanner Windows (USB, Wi-Fi, Ethernet, WSD, eSCL):
        - Se non ci sono scanner rilevati da Windows, solleva ConnectionError descrittivo.
        - Se force_select=True o se ci sono più scanner e nessuna selezione memorizzata:
          apre la finestra di dialogo nativa di Windows (WIA.CommonDialog) per permettere all'utente di scegliere.
        - Se esiste un solo scanner e force_select=False, lo seleziona automaticamente.
        - Se la periferica precedentemente selezionata è ancora presente e force_select=False, la riutilizza.
        Ritorna l'ID dello scanner scelto, oppure None se l'utente annulla il dialog di sistema.
        """
        try:
            import win32com.client
            mgr = win32com.client.Dispatch("WIA.DeviceManager")
            count = len(mgr.DeviceInfos)
            if count == 0:
                raise ConnectionError(
                    "Nessuno scanner rilevato da Windows. Verificare che lo scanner sia acceso, "
                    "collegato (USB, Wi-Fi o Ethernet) e visibile in 'Stampanti e scanner' di Windows."
                )

            # Se force_select=False e abbiamo già un device_id salvato e valido
            if not force_select and ScannerService._device_id:
                if any(str(i.DeviceID) == ScannerService._device_id for i in mgr.DeviceInfos):
                    return ScannerService._device_id
                ScannerService._device_id = None

            # Se c'è un solo scanner e non è forzata la selezione, selezionalo direttamente
            if not force_select and count == 1:
                ScannerService._device_id = str(mgr.DeviceInfos[0].DeviceID)
                return ScannerService._device_id

            # Finestra di dialogo nativa di sistema di Windows per la selezione dello scanner
            dialog = win32com.client.Dispatch("WIA.CommonDialog")
            device = dialog.ShowSelectDevice(WIA_DEVICE_TYPE_SCANNER, True, False)
            if device is None:
                return None
            ScannerService._device_id = str(device.DeviceID)
            return ScannerService._device_id
        except ConnectionError:
            raise
        except Exception as e:
            if WIA_ERROR_NO_DEVICE in ScannerService._com_codes(e) or "device" in str(e).lower():
                raise ConnectionError(
                    "Nessuno scanner rilevato da Windows. Verificare che lo scanner sia acceso e collegato."
                )
            raise RuntimeError(f"Errore nella selezione dello scanner: {e}")

    @staticmethod
    def select_device_dialog() -> Optional[str]:
        """Apre espressamente il dialog nativo di Windows per cambiare o selezionare lo scanner desiderato."""
        return ScannerService.resolve_device_id(force_select=True)

    @staticmethod
    def _connect(device_id: str):
        import win32com.client
        mgr = win32com.client.Dispatch("WIA.DeviceManager")
        for info in mgr.DeviceInfos:
            if str(info.DeviceID) == device_id:
                return info.Connect()
        ScannerService._device_id = None
        raise ConnectionError("Lo scanner selezionato non è più disponibile. Verificare che sia acceso e collegato.")

    @staticmethod
    def _finalize_scan(path: Path, optimize: bool) -> Path:
        """Pipeline post-acquisizione: ottimizzazione completa oppure semplice normalizzazione JPEG."""
        if optimize:
            return ScannerService.auto_optimize_scan(path)
        try:
            with Image.open(path) as img:
                img.load()
                img = ScannerService._trim_overscan_padding(img)
                if img.height > img.width:
                    img = img.rotate(90, expand=True)
                img = ScannerService._trim_overscan_padding(img)
                if ScannerService.is_page_upside_down(img):
                    img = img.rotate(180, expand=True)
                    img = ScannerService._trim_overscan_padding(img)
                deskewed_img, deskew_ang = ScannerService.deskew_image(img, return_angle=True)
                if abs(deskew_ang) > 0.2 and abs(deskew_ang) <= 5.0:
                    img = deskewed_img
                rgb = img.convert("RGB")
            rgb.save(str(path), format="JPEG", quality=95)
        except Exception as e:
            print(f"Avviso normalizzazione scansione: {e}")
        return path

    # ------------------------------------------------------------------ acquisizione multi-pagina

    @staticmethod
    def acquire_pages(
        device_id: str,
        source: str = SOURCE_FEEDER,
        max_pages: int = 30,
        dpi: int = 200,
        optimize: bool = True,
        skip_blank_pages: bool = True,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        prefix: str = "scan_batch",
    ) -> List[Path]:
        """Acquisisce una o più pagine dallo scanner indicato. Pensato per un THREAD SECONDARIO.

        - ADF: cicla fino a vassoio vuoto (WIA_ERROR_PAPER_EMPTY / HTTP 409 = fine naturale del lotto).
        - Piano: acquisisce una singola pagina.
        progress_cb(n, messaggio) viene chiamato prima e dopo ogni pagina.
        Solleva FeederNotAvailableError se l'ADF non è disponibile, ScanBatchError (con le pagine
        già acquisite) se l'errore avviene a lotto avviato.
        """
        com_inited = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_inited = True
        except Exception:
            pass

        pages: List[Path] = []
        ScannerService.last_blank_skipped = 0
        logger = get_session_logger()
        logger.log_scan_event(
            "Avvio acquisizione pagine", source=source, dpi=dpi,
            details=f"device='{device_id}' | max_pages={max_pages} | optimize={optimize} | skip_blank_pages={skip_blank_pages}"
        )
        try:
            device = ScannerService._connect(device_id)
            use_feeder = (source == SOURCE_FEEDER)

            if use_feeder:
                caps = ScannerService._get_prop(device.Properties, WIA_DPS_DOCUMENT_HANDLING_CAPABILITIES)
                if isinstance(caps, int) and not (caps & WIA_HANDLING_FEEDER):
                    raise FeederNotAvailableError("Lo scanner non dispone dell'alimentatore automatico (ADF).")
                ok = ScannerService._set_prop(device.Properties, WIA_DPS_DOCUMENT_HANDLING_SELECT, WIA_HANDLING_FEEDER)
                if not ok and caps is None:
                    raise FeederNotAvailableError("Lo scanner non espone la modalità alimentatore automatico (ADF).")
                ScannerService._set_prop(device.Properties, WIA_DPS_PAGE_SIZE, WIA_PAGE_A4)
            else:
                ScannerService._set_prop(device.Properties, WIA_DPS_DOCUMENT_HANDLING_SELECT, WIA_HANDLING_FLATBED)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            limit = max_pages if use_feeder else 1

            for n in range(1, limit + 1):
                if cancel_event is not None and cancel_event.is_set():
                    break
                if progress_cb:
                    progress_cb(n, f"Scansione foglio {n} in corso...")
                try:
                    if use_feeder:
                        # Una pagina per Transfer: il ciclo prosegue finché il vassoio non è vuoto
                        ScannerService._set_prop(device.Properties, WIA_DPS_PAGES, 1)
                        ScannerService._set_prop(device.Properties, WIA_DPS_PAGE_SIZE, WIA_PAGE_A4)
                    item = device.Items.Item(1)
                    ScannerService._set_prop(item.Properties, WIA_IPS_XRES, int(dpi))
                    ScannerService._set_prop(item.Properties, WIA_IPS_YRES, int(dpi))
                    if use_feeder:
                        # Imposta area di scansione A4 standard (~8.27 x 11.69 pollici)
                        ScannerService._set_prop(item.Properties, WIA_IPS_PAGE_SIZE, WIA_PAGE_A4)
                        ScannerService._set_prop(item.Properties, WIA_IPS_XPOS, 0)
                        ScannerService._set_prop(item.Properties, WIA_IPS_YPOS, 0)
                        a4_w_px = int(round(dpi * 8.27))
                        a4_h_px = int(round(dpi * 11.69))
                        cur_xext = ScannerService._get_prop(item.Properties, WIA_IPS_XEXTENT)
                        cur_yext = ScannerService._get_prop(item.Properties, WIA_IPS_YEXTENT)
                        if isinstance(cur_xext, int) and cur_xext > a4_w_px * 1.15:
                            ScannerService._set_prop(item.Properties, WIA_IPS_XEXTENT, a4_w_px)
                        if isinstance(cur_yext, int) and cur_yext > a4_h_px * 1.15:
                            ScannerService._set_prop(item.Properties, WIA_IPS_YEXTENT, a4_h_px)
                    image = item.Transfer(WIA_FORMAT_JPEG)
                except Exception as e:
                    if ScannerService._is_paper_empty(e, device=device, is_feeder=use_feeder):
                        break  # Fine naturale del lotto (vassoio vuoto)
                    codes = ScannerService._com_codes(e)
                    for code, text in _FRIENDLY_WIA_ERRORS.items():
                        if code in codes:
                            raise ScanBatchError(text, pages)
                    if WIA_ERROR_OFFLINE in codes or WIA_ERROR_NO_DEVICE in codes:
                        raise ConnectionError("Lo scanner è spento o non raggiungibile.")
                    if use_feeder and not pages:
                        raise FeederNotAvailableError(f"Alimentatore ADF non utilizzabile: {e}")
                    raise ScanBatchError(f"Errore durante la scansione del foglio {n}: {e}", pages)

                if image is None:
                    break
                out = TEST_IMAGES_DIR / f"{prefix}_{timestamp}_page{len(pages) + 1:02d}.jpg"
                try:
                    if out.exists():
                        out.unlink()
                    image.SaveFile(str(out))
                    # Controllo pagina bianca opzionale: se abilitato, archivia in _scartate_bianche e prosegue il lotto (continue)
                    if skip_blank_pages and ScannerService.is_blank_page(out):
                        discard_dir = TEST_IMAGES_DIR / "_scartate_bianche"
                        discard_dir.mkdir(parents=True, exist_ok=True)
                        discard_path = discard_dir / out.name
                        try:
                            if discard_path.exists():
                                discard_path.unlink()
                            out.rename(discard_path)
                        except Exception:
                            try:
                                out.unlink()
                            except Exception:
                                pass
                        ScannerService.last_blank_skipped += 1
                        if progress_cb:
                            progress_cb(n, f"Foglio {n} vuoto: archiviato in _scartate_bianche")
                        logger.log_scan_event(
                            f"Foglio {n} identificato come bianco: saltato e archiviato in _scartate_bianche",
                            details=f"file='{out.name}'"
                        )
                        continue
                    ScannerService._finalize_scan(out, optimize)
                    try:
                        from ..services.history_service import get_history_service
                        get_history_service().archive_raw_scan(out, prefix="scanner")
                    except Exception:
                        pass
                except Exception as e:
                    raise ScanBatchError(f"Impossibile salvare il foglio {n}: {e}", pages)
                pages.append(out)
                if progress_cb:
                    progress_cb(n, f"Foglio {n} acquisito")
            logger.log_scan_event(
                "Acquisizione completata", source=source, dpi=dpi,
                details=f"Pagine valide acquisite: {len(pages)} | Pagine bianche scartate: {ScannerService.last_blank_skipped}"
            )
            return pages
        finally:
            if com_inited:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    @staticmethod
    def acquire_batch_from_feeder(
        max_pages: int = 30,
        device_id: Optional[str] = None,
        dpi: int = 200,
        optimize: bool = True,
        progress_cb: Optional[Callable[[int, str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> List[Path]:
        """Acquisisce un lotto continuo dall'ADF (Alimentatore Automatico Fogli).

        Salva ogni pagina come `scan_batch_YYYYMMDD_HHMMSS_pageNN.jpg` applicando la pipeline di
        sbiancamento e contrasto matita. Il vassoio vuoto (0x80210003) termina il lotto senza errore.
        Se `device_id` è None viene usato lo scanner già selezionato (o l'unico presente).
        """
        dev = device_id or ScannerService._device_id or ScannerService.resolve_device_id()
        if not dev:
            return []
        return ScannerService.acquire_pages(
            dev, SOURCE_FEEDER, max_pages, dpi, optimize, progress_cb, cancel_event, "scan_batch"
        )

    # ------------------------------------------------------------------ file da disco

    @staticmethod
    def _extract_pdf_jpegs(pdf_path: Path) -> List[bytes]:
        """Estrae le immagini JPEG (DCTDecode) incorporate in un PDF, tipico dei PDF da scanner."""
        data = pdf_path.read_bytes()
        found: List[bytes] = []
        pos = 0
        while True:
            idx = data.find(b"/DCTDecode", pos)
            if idx < 0:
                break
            start = data.find(b"stream", idx)
            if start < 0:
                break
            start += len(b"stream")
            if data[start:start + 2] == b"\r\n":
                start += 2
            elif data[start:start + 1] in (b"\n", b"\r"):
                start += 1
            end = data.find(b"endstream", start)
            if end < 0:
                break
            blob = data[start:end].rstrip(b"\r\n")
            if blob[:2] == b"\xff\xd8":
                found.append(blob)
            pos = end
        return found

    @staticmethod
    def pdf_to_images(pdf_path: Path) -> List[Path]:
        """Converte un PDF in immagini JPEG (una per pagina). Usa PyMuPDF se installato, altrimenti
        estrae le immagini JPEG incorporate. Solleva RuntimeError se non è possibile."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = re.sub(r"[^\w\-]+", "_", pdf_path.stem)[:40]
        outputs: List[Path] = []
        try:
            import fitz  # PyMuPDF, opzionale
            with fitz.open(str(pdf_path)) as doc:
                for i, page in enumerate(doc, start=1):
                    pix = page.get_pixmap(dpi=200)
                    out = TEST_IMAGES_DIR / f"pdf_{stem}_{timestamp}_p{i:02d}.jpg"
                    pix.save(str(out))
                    outputs.append(out)
            if outputs:
                get_session_logger().log_scan_event("Conversione PDF completata", details=f"PDF '{pdf_path.name}' -> {len(outputs)} pagine estratte (PyMuPDF)")
                return outputs
        except ImportError:
            pass

        import io
        for i, blob in enumerate(ScannerService._extract_pdf_jpegs(pdf_path), start=1):
            try:
                with Image.open(io.BytesIO(blob)) as img:
                    img.load()
                    out = TEST_IMAGES_DIR / f"pdf_{stem}_{timestamp}_p{i:02d}.jpg"
                    img.convert("RGB").save(str(out), format="JPEG", quality=95)
                    outputs.append(out)
            except Exception:
                continue
        if not outputs:
            raise RuntimeError(
                f"Impossibile leggere il PDF '{pdf_path.name}'. Converti il PDF in immagini (JPG/PNG) "
                f"oppure installa il pacchetto opzionale 'pymupdf'."
            )
        get_session_logger().log_scan_event("Conversione PDF completata", details=f"PDF '{pdf_path.name}' -> {len(outputs)} pagine estratte (estrazione JPEG)")
        return outputs

    @staticmethod
    def prepare_uploaded_files(paths: List[str]) -> Tuple[List[Path], List[str]]:
        """Prepara file scelti da disco (immagini e PDF): orientamento orizzontale e leggero contrasto.

        Ritorna (immagini_pronte, avvisi). Non solleva eccezioni: i file problematici finiscono negli avvisi.
        """
        ready: List[Path] = []
        warnings: List[str] = []
        ScannerService.last_blank_skipped = 0
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        counter = 0
        for raw in paths:
            src = Path(raw)
            try:
                sources = ScannerService.pdf_to_images(src) if src.suffix.lower() == ".pdf" else [src]
            except Exception as e:
                warnings.append(str(e))
                continue
            for item in sources:
                counter += 1
                if len(paths) + len(sources) > 1 and ScannerService.is_blank_page(item):
                    ScannerService.last_blank_skipped += 1
                    continue
                try:
                    with Image.open(item) as img:
                        img.load()
                        if img.height > img.width:
                            img = img.rotate(90, expand=True)
                        if ScannerService.is_page_upside_down(img):
                            img = img.rotate(180, expand=True)
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        dest = TEST_IMAGES_DIR / f"caricata_v3_{timestamp}_{counter:02d}.jpg"
                        img.save(dest, format="JPEG", quality=95)
                        ready.append(dest)
                except Exception as e:
                    warnings.append(f"{item.name}: {e}")
        get_session_logger().log_scan_event("Preparazione file caricati", details=f"{len(ready)} fogli preparati, {len(warnings)} avvisi")
        return ready, warnings

    @staticmethod
    def acquire_from_scanner(optimize: bool = True) -> Optional[Path]:
        """Tenta di acquisire una pagina da scanner tramite WIA (Windows Image Acquisition)."""
        try:
            import win32com.client
            dialog = win32com.client.Dispatch("WIA.CommonDialog")
            # 1 = wiaDeviceTypeScanner
            # FormatID: wiaFormatJPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
            image = dialog.ShowAcquireImage(1, 1, 0, "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}", False, True, False)
            if image is None:
                return None

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = TEST_IMAGES_DIR / f"scansione_{timestamp}.jpg"
            if output_file.exists():
                output_file.unlink()
            image.SaveFile(str(output_file))

            # Applica subito tutte le migliorie: orientamento orizzontale, sbiancamento sfondo e ricalco matita
            ScannerService._finalize_scan(output_file, optimize)

            return output_file

        except Exception as e:
            err_msg = str(e)
            if "0x80210015" in err_msg or "WIA_ERROR_OFFLINE" in err_msg or "device" in err_msg.lower():
                raise ConnectionError("Nessuno scanner collegato o acceso. Collegare uno scanner o usare 'Carica Immagine'.")
            raise RuntimeError(f"Errore durante l'acquisizione da scanner: {e}")

    @staticmethod
    def _trim_overscan_padding(img: Image.Image) -> Image.Image:
        """Ritaglia automaticamente il fondo bianco in eccesso generato da scanner ADF che leggono
        un'area più lunga del foglio reale (es. 1700x5000 invece di A4 1700x2338).

        Restituisce l'immagine ritagliata alla sola area del foglio A4 con un piccolo margine.
        """
        try:
            from PIL import ImageChops, ImageFilter
            w, h = img.size
            # Caso 1: immagine già ruotata in orizzontale ma sproporzionatamente larga (es. 5000x1700)
            if w > h * 1.55:
                gray = img.convert("L")
                # Escludiamo il 4% superiore/inferiore per evitare ombre sui bordi
                my = max(int(h * 0.04), 4)
                bg = gray.filter(ImageFilter.GaussianBlur(8))
                diff = ImageChops.difference(gray, bg)
                step = 50
                last_content_x = int(h * 1.38)  # minimo A4 landscape (~1.414 * h)
                for x0 in range(0, w, step):
                    x1 = min(x0 + step, w)
                    strip = diff.crop((x0, my, x1, h - my))
                    hist = strip.histogram()
                    total = sum(hist)
                    if total > 0 and (sum(hist[14:]) / total) > 0.002:
                        last_content_x = max(last_content_x, x1)
                target_w = min(w, max(int(h * 1.38), last_content_x + int(h * 0.04)))
                if target_w < w * 0.92:
                    return img.crop((0, 0, target_w, h))

            # Caso 2: immagine ancora verticale ma sproporzionatamente alta (es. 1700x5000)
            if h > w * 1.55:
                gray = img.convert("L")
                mx = max(int(w * 0.04), 4)
                bg = gray.filter(ImageFilter.GaussianBlur(8))
                diff = ImageChops.difference(gray, bg)
                step = 50
                last_content_y = int(w * 1.38)
                for y0 in range(0, h, step):
                    y1 = min(y0 + step, h)
                    strip = diff.crop((mx, y0, w - mx, y1))
                    hist = strip.histogram()
                    total = sum(hist)
                    if total > 0 and (sum(hist[14:]) / total) > 0.002:
                        last_content_y = max(last_content_y, y1)
                target_h = min(h, max(int(w * 1.38), last_content_y + int(w * 0.04)))
                if target_h < h * 0.92:
                    return img.crop((0, 0, w, target_h))
        except Exception as e:
            print(f"Avviso ritaglio overscan: {e}")
        return img

    @staticmethod
    def auto_optimize_scan(image_path: Path) -> Path:
        """Esegue automaticamente l'intera pipeline di ottimizzazione documentale:
        1. Auto-ritaglio dell'eventuale coda bianca in eccesso (overscan ADF).
        2. Auto-rotazione in orizzontale se l'immagine è verticale (formato MOD. A).
        3. Auto-orientamento/raddrizzamento se la scheda è stata scansionata capovolta (180°).
        4. Normalizzazione dello sfondo cartaceo a bianco puro.
        5. Potenziamento specifico della grafite/matita e penne sbiadite (curva non-lineare sui mezzitoni).
        6. Incremento della nitidezza sui contorni numerici per la massima leggibilità.
        Salva l'immagine ottimizzata pronta sia per l'anteprima a video sia per l'invio all'IA.
        """
        try:
            from PIL import Image, ImageEnhance
            with Image.open(image_path) as img:
                img.load()
                # 0. Rimuove eventuale mezza pagina bianca se il driver ADF ha scansionato oltre A4
                img = ScannerService._trim_overscan_padding(img)
                # 1. Rotazione automatica in orizzontale se verticale
                if img.height > img.width:
                    img = img.rotate(90, expand=True)
                # Verifica anche dopo rotazione se resta coda bianca a destra
                img = ScannerService._trim_overscan_padding(img)

                # 2. Auto-raddrizzamento se la scheda è sottosopra / capovolta (180°)
                if ScannerService.is_page_upside_down(img):
                    img = img.rotate(180, expand=True)
                    img = ScannerService._trim_overscan_padding(img)

                # 3. Fine Deskewing automatico (inclinazione -5.0° .. +5.0°)
                deskewed_img, deskew_ang = ScannerService.deskew_image(img, return_angle=True)
                if abs(deskew_ang) > 0.2 and abs(deskew_ang) <= 5.0:
                    img = deskewed_img

                # Salva la scansione preservando tutti i livelli di grigio della matita chiara
                # (senza Brightness/Contrast distruttivi che brucerebbero i tratti leggeri a 255)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                import numpy as np
                arr = np.array(img, dtype=np.float32) / 255.0
                # Leggera curva gamma (>1 scurisce i mezzitoni senza bruciare i grigi 235..254 a 255)
                gamma_arr = np.power(arr, 1.35)
                p1 = Image.fromarray((gamma_arr * 255.0).astype(np.uint8))
                enhanced = ImageEnhance.Sharpness(p1).enhance(1.15)
                enhanced.save(str(image_path), format="JPEG", quality=95)
        except Exception as e:
            print(f"Avviso ottimizzazione automatica: {e}")
        return image_path

    @staticmethod
    def enhance_contrast(image_path: Path, factor: float = 1.30) -> Path:
        """Aumenta il contrasto dell'immagine specificata ed esalta la scrittura a matita e biro sbiadita."""
        try:
            with Image.open(image_path) as img:
                gray = img.convert("L")
                lut = []
                for i in range(256):
                    if i >= 225:
                        val = 255
                    elif i <= 50:
                        val = int(i * 0.3)
                    else:
                        norm = (i - 50) / 175.0
                        val = int(15 + 240 * (norm ** 2.2))
                    lut.append(val)
                boosted = gray.point(lut, "L").convert("RGB")
                sharp = ImageEnhance.Sharpness(boosted).enhance(2.0)
                sharp.save(str(image_path), quality=95)
        except Exception as e:
            print(f"Avviso contrasto: {e}")
        return image_path

    @staticmethod
    def generate_sample_slip_image(
        scenario: str = "scheda_perfetta",
        supermarket: str = "Casa Circ. Capanne",
        card_num: str = "1",
        rows_text: Optional[list] = None,
        total_text: str = "TOTALE: 4 scatole - 48.0 Kg"
    ) -> Path:
        """Genera un'immagine sintetica della scheda corriere per test visivi."""
        TEST_IMAGES_DIR.mkdir(exist_ok=True)
        filename = f"sample_{scenario}.png"
        file_path = TEST_IMAGES_DIR / filename
        if file_path.exists():
            return file_path

        # Dimensioni tipo A5 / modulo cartaceo
        width, height = 700, 950
        img = Image.new("RGB", (width, height), color=(250, 249, 245))
        draw = ImageDraw.Draw(img)

        # Intestazione modulo
        draw.rectangle([(20, 20), (width - 20, 110)], outline=(30, 70, 130), width=3)
        draw.text((40, 30), "BANCO ALIMENTARE - COLLETTA ALIMENTARE", fill=(30, 70, 130))
        draw.text((40, 55), "SCHEDA TRASPORTO CORRIERE", fill=(20, 20, 20))
        draw.text((40, 80), f"PUNTO RACCOLTA: {supermarket}", fill=(0, 50, 120))

        # Riquadro Numero Scheda
        draw.rectangle([(width - 210, 35), (width - 35, 95)], outline=(180, 50, 50), width=2)
        draw.text((width - 200, 42), "SCHEDA N°:", fill=(180, 50, 50))
        if card_num == "ILLEGGIBILE":
            # Disegna scarabocchio
            draw.line([(width - 100, 50), (width - 50, 75), (width - 80, 85), (width - 45, 60)], fill=(40, 40, 40), width=3)
        elif card_num != "NON_PRESENTE":
            draw.text((width - 100, 55), card_num, fill=(20, 20, 20))

        # Tabella Scatole
        y_start = 140
        draw.rectangle([(20, y_start), (width - 20, 780)], outline=(100, 100, 100), width=2)
        draw.line([(80, y_start), (80, 780)], fill=(120, 120, 120), width=1)
        draw.line([(450, y_start), (450, 780)], fill=(120, 120, 120), width=1)
        draw.line([(20, y_start + 35), (width - 20, y_start + 35)], fill=(100, 100, 100), width=2)

        draw.text((30, y_start + 10), "N°", fill=(50, 50, 50))
        draw.text((100, y_start + 10), "CATEGORIA PRODOTTO", fill=(50, 50, 50))
        draw.text((480, y_start + 10), "PESO (KG)", fill=(50, 50, 50))

        if rows_text is None:
            rows_text = [
                ("1", "Pasta", "12.5 kg"),
                ("2", "Pasta", "10.5 kg"),
                ("3", "Tonno", "15.0 kg"),
                ("4", "Pelati", "10.0 kg"),
            ]

        for i in range(1, 11):
            y_row = y_start + 35 + (i - 1) * 55
            draw.line([(20, y_row), (width - 20, y_row)], fill=(200, 200, 200), width=1)
            draw.text((35, y_row + 15), f"{i}", fill=(100, 100, 100))

        for idx, row in enumerate(rows_text):
            y_row = y_start + 35 + idx * 55
            draw.text((100, y_row + 15), row[1], fill=(20, 20, 120))
            draw.text((480, y_row + 15), row[2], fill=(20, 20, 120))

        # Sezione Totale in fondo
        draw.rectangle([(20, 800), (width - 20, 880)], outline=(30, 120, 50), width=2)
        draw.text((40, 815), "RIASSUNTO SCRITTO A MANO:", fill=(50, 50, 50))
        draw.text((40, 840), total_text, fill=(150, 30, 30) if "65" in total_text else (20, 100, 30))

        # Footer
        draw.text((40, 910), "Giornata Nazionale della Colletta Alimentare - Modulo Corriere Ufficiale", fill=(120, 120, 120))

        img.save(file_path)
        return file_path


deskew_image = ScannerService.deskew_image
detect_skew_angle = ScannerService.detect_skew_angle

