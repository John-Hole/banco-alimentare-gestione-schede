"""Punto di ingresso principale dell'applicazione desktop Banco Alimentare (V4 Ufficiale)."""

import os
import sys
import ctypes
from pathlib import Path

# Assicura che la cartella corrente sia la radice del progetto
BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def enable_high_dpi():
    """Abilita il supporto High-DPI per Windows per un rendering nitido dei font e widget."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    enable_high_dpi()
    from src.services.session_logger import get_session_logger
    session_logger = get_session_logger()
    session_logger.logger.info("Avvio del processo principale Banco Alimentare (main.py)")

    try:
        from src.ui.app import BancoAlimentareApp
        app = BancoAlimentareApp()
        app.mainloop()
        session_logger.log_session_end("Chiusura normale dell'applicazione")
    except Exception:
        import traceback
        err_msg = traceback.format_exc()
        session_logger.logger.critical("Errore fatale durante l'avvio o l'esecuzione:\n" + err_msg)
        session_logger.sync_latest_pointer()
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Errore Avvio Banco Alimentare",
                f"Si è verificato un errore durante l'avvio dell'applicazione:\n\n{err_msg}\n\n"
                f"Dettagli salvati nel log:\n{session_logger.get_current_log_path()}"
            )
        except Exception:
            print(f"Errore all'avvio:\n{err_msg}", file=sys.stderr)
            input("Premi Invio per chiudere...")


if __name__ == "__main__":
    main()
