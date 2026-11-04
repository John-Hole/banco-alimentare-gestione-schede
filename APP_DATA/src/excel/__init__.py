"""Modulo Excel con automazione COM e gestione backup."""

from .excel_manager import (
    ExcelManager,
    ExcelNotRunningError,
    ExcelWorkbookNotOpenError,
)
from .backup_service import BackupService

__all__ = [
    "ExcelManager",
    "ExcelNotRunningError",
    "ExcelWorkbookNotOpenError",
    "BackupService",
]
