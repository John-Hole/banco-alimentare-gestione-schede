"""Servizio per la creazione e gestione dei backup timestampati preventivi."""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from ..config import BACKUP_DIR


class BackupService:
    """Gestisce la creazione di copie di sicurezza prima di ogni scrittura."""

    @staticmethod
    def generate_backup_filename(original_name: str = "gestionale") -> str:
        stem = Path(original_name).stem
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{stem}_BACKUP_{timestamp}.xlsx"

    @classmethod
    def create_backup(cls, workbook_com=None, file_path: Optional[str] = None) -> Path:
        """Crea una copia di backup nella cartella backups/."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        filename = cls.generate_backup_filename(
            workbook_com.Name if workbook_com else (Path(file_path).name if file_path else "gestionale")
        )
        dest_path = BACKUP_DIR / filename

        # Se abbiamo il workbook COM, possiamo usare SaveCopyAs per uno snapshot esatto
        if workbook_com is not None:
            try:
                workbook_com.SaveCopyAs(str(dest_path))
                if dest_path.exists() and dest_path.stat().st_size > 0:
                    return dest_path
            except Exception as e:
                print(f"Avviso: SaveCopyAs COM non riuscito ({e}), fallback su copia file fisico...")

        # Fallback su copia fisica da disco
        source_path = None
        if workbook_com and hasattr(workbook_com, "FullName"):
            try:
                source_path = Path(workbook_com.FullName)
            except Exception:
                pass
        if not source_path and file_path:
            source_path = Path(file_path)

        if source_path and source_path.exists():
            shutil.copy2(str(source_path), str(dest_path))
            return dest_path

        raise RuntimeError("Impossibile creare il backup: né il workbook COM né il file sorgente sono accessibili.")

    @classmethod
    def get_recent_backups(cls, limit: int = 10) -> List[Path]:
        """Restituisce l'elenco dei backup più recenti."""
        if not BACKUP_DIR.exists():
            return []
        files = sorted(BACKUP_DIR.glob("*.xlsx"), key=lambda f: f.stat().st_mtime, reverse=True)
        return files[:limit]
