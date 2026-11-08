"""Modulo per l'acquisizione da scanner (WIA), elaborazione immagini e preset."""

from .scanner_service import (
    ScannerService,
    TEST_IMAGES_DIR,
    SOURCE_FEEDER,
    SOURCE_FLATBED,
    FeederNotAvailableError,
    ScanBatchError,
    detect_scanners,
    get_scanner_status,
    apply_image_tuning,
)
from .presets_config import (
    PRESETS_CONFIG,
    AUTO_CLASSIFIER_THRESHOLDS,
    EXPORT_CONFIG,
)
from .preset_engine import (
    apply_preset,
    apply_preset_0,
    apply_preset_1,
    apply_preset_2,
    apply_preset_3,
    apply_preset_4,
    apply_preset_5,
    detect_best_preset,
    prepare_scan_for_pipeline,
    create_ai_optimized_copy,
    clean_temp_ai_dir,
    TEMP_AI_DIR,
)

__all__ = [
    "ScannerService",
    "TEST_IMAGES_DIR",
    "SOURCE_FEEDER",
    "SOURCE_FLATBED",
    "FeederNotAvailableError",
    "ScanBatchError",
    "detect_scanners",
    "get_scanner_status",
    "apply_image_tuning",
    "PRESETS_CONFIG",
    "AUTO_CLASSIFIER_THRESHOLDS",
    "EXPORT_CONFIG",
    "apply_preset",
    "apply_preset_0",
    "apply_preset_1",
    "apply_preset_2",
    "apply_preset_3",
    "apply_preset_4",
    "apply_preset_5",
    "detect_best_preset",
    "prepare_scan_for_pipeline",
    "create_ai_optimized_copy",
    "clean_temp_ai_dir",
    "TEMP_AI_DIR",
]
