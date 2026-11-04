@echo off
title Banco Alimentare - Gestionale Colletta (V4)
cd /d "%~dp0"

:: Determina l'interprete Python disponibile (py -3 oppure python)
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo ============================================================================
    echo  [ERRORE] Python non trovato nel sistema!
    echo  1. Scarica e installa Python 3.10+ da https://www.python.org/downloads/
    echo  2. Assicurati di spuntare la casella "Add Python to PATH" durante il setup.
    echo ============================================================================
    echo.
    pause
    exit /b 1
)

:: 1. Verifica rapida delle librerie Python richieste (1° avvio automatico)
%PY_CMD% -c "import customtkinter, openpyxl, PIL, cv2, win32com, pydantic, requests, google.genai, fitz" >nul 2>nul
if errorlevel 1 (
    echo ============================================================================
    echo  PRIMO AVVIO RILEVATO - INSTALLAZIONE AUTOMATICA LIBRERIE IN CORSO...
    echo  Attendere qualche istante ^(operazione eseguita solo la prima volta^).
    echo ============================================================================
    echo.
    %PY_CMD% -m pip install --upgrade pip
    %PY_CMD% -m pip install -r "APP_DATA\requirements.txt"
    if errorlevel 1 (
        echo.
        echo ============================================================================
        echo  [ERRORE] Si e' verificato un problema durante l'installazione automatica.
        echo  Verifica la connessione a Internet e riprova.
        echo ============================================================================
        echo.
        pause
        exit /b 1
    )
    echo.
    echo [OK] Librerie installate con successo! Avvio dell'applicazione...
)

:: 2. Avvia APP_DATA\main.py senza finestra di terminale (pyw / pythonw)
where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw "APP_DATA\main.py"
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw "APP_DATA\main.py"
    exit /b 0
)

:: 3. Fallback: avvio con finestra console
%PY_CMD% "APP_DATA\main.py"
if errorlevel 1 (
    echo.
    echo ========================================================
    echo  Si e' verificato un errore durante l'avvio dell'app.
    echo ========================================================
    echo.
    pause
)
