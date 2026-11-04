# Banco Alimentare (V4 Ufficiale) — App Inserimento Automatico Schede Corriere nel Gestionale Excel

Applicazione desktop per Windows per l'acquisizione automatica da scanner ADF/Piano Vetro o file, l'estrazione tramite **Intelligenza Artificiale di Visione a Cascata**, la validazione matematica a regole e la scrittura diretta delle **schede corriere cartacee (MOD. A)** all'interno dei file Excel gestionali durante la **Giornata Nazionale della Colletta Alimentare**.

---

## 🚀 1. Installazione e Avvio Rapido 1-Click su un Nuovo PC (da GitHub)

1. **Scarica o clona il progetto da GitHub**:
   ```powershell
   git clone <URL_DEL_REPOSITORY>
   ```
2. **Avvia con 1 clic**:
   - Fai doppio clic su **`AVVIA_BANCO_ALIMENTARE.bat`**.
   - Se è il **1° avvio** su un nuovo PC e mancano le librerie Python, lo script le installa automaticamente da `APP_DATA\requirements.txt` e poi apre l'applicazione senza finestra di console.
3. **Configura le chiavi API al 1° avvio**:
   - **Sblocco Rapido con Password**: inserisci la password segreta nel box **"🔐 Sblocco Rapido Profilo Giovanni"** nella finestra di benvenuto (o in `Impostazioni -> Chiavi API`) per sbloccare e attivare all'istante tutte le 3 chiavi API cifrate.
   - **Inserimento Manuale**: in alternativa, incolla una chiave gratuita Google AI Studio (Gemini) e/o OpenRouter.
   - Il file `APP_DATA/config.json` generato localmente è protetto da `.gitignore` e non viene mai caricato su GitHub.

---

## 📁 2. Struttura Pulita della Cartella Principale

Chi apre la cartella principale vede **solo i file e le cartelle operative**, mentre tutto il motore tecnico è racchiuso in `APP_DATA/`:

```text
V_UFFICIALE/ (e V4/)
├── AVVIA_BANCO_ALIMENTARE.bat    # Unico file da cliccare (al 1° avvio installa da solo le librerie mancanti)
├── COME_INSTALLARE.txt           # Guida rapida e chiara per il 1° avvio su un nuovo PC
├── README.md                     # Documentazione completa del progetto
├── .gitignore                    # Protegge config.json, BACKUP/, SCANSIONI/, logs/ e file temporanei
├── BACKUP/                       # Cartella dove vengono salvati i backup automatici dei file Excel
├── SCANSIONI/                    # Cartella dello storico scansioni (immagini + JSON validati)
├── FOGLI_COLLETTA/               # Cartella predefinita dei file Excel di zona (opzionale: selezionabile ovunque)
└── APP_DATA/                     # Tutti i file tecnici e di codice racchiusi qui dentro
    ├── main.py
    ├── main_v3.py
    ├── requirements.txt
    ├── config.template.json
    ├── colletta_index.json
    ├── gestionale_banco_alimentare_MASTER.xlsx
    ├── PROMPT_INTELLIGENZA_ARTIFICIALE.md
    ├── logs/
    ├── test_images/
    ├── src/
    │   ├── config.py             # Punta automaticamente a ../BACKUP, ../SCANSIONI, ../FOGLI_COLLETTA
    │   ├── crypto_keys.py        # Cifratura PBKDF2-HMAC-SHA256 e sblocco chiavi API tramite password
    │   ├── models.py
    │   ├── excel/
    │   ├── extractors/
    │   ├── scanner/
    │   ├── services/
    │   ├── ui/
    │   └── validation/
    └── tests/
```

---

## ✨ 3. Funzionalità Principali della Versione 4

### 3.1 Pannelli Principali Ridimensionabili e Invertibili (`⇄`)
- **Divisione Ridimensionabile (`tk.PanedWindow`)**: trascina con il mouse la barra centrale tra il **Pannello Immagine** e il **Pannello Tabella Dati** per allargare o stringere a piacere ciascun lato (con larghezza minima di sicurezza `380px`).
- **Inversione Posizione Pannelli (`⇄`)**: clicca il pulsante **`⇄`** nella barra in alto a destra per scambiare all'istante di posto il Pannello Immagine e il Pannello Tabella (Immagine a Sinistra ↔ Immagine a Destra). La preferenza (`swap_panels`) viene salvata in `config.json`.

### 3.2 Sblocco Chiavi API Cifrate con Password e Scelta Cartella al 1° Avvio
- Le 3 chiavi API del profilo principale sono cifrate in `APP_DATA/src/crypto_keys.py` con **PBKDF2-HMAC-SHA256 (200.000 iterazioni) + cifratura autenticata HMAC-SHA256**: né la password né le chiavi in chiaro compaiono mai nel codice sorgente.
- Sia al 1° avvio (`WelcomeSetupDialogV3`) sia in qualsiasi momento da `⚙️ Impostazioni -> 🔑 Chiavi API`, basta inserire la password per caricare tutte le chiavi.
- La notifica verde di conferma scansione cartella Excel (`✓ Rilevati 21 file Excel e 399 supermercati...`) scompare automaticamente dopo **5 secondi** per lasciare pulita la barra superiore.

### 3.3 Tabella Categorie a 4 Colonne con Conferma Interattiva (`✓` / `✗`)
- La tabella mostra **4 colonne**: **`Categoria`**, **`Kg`**, **`Scatole`**, **`Stato`** (`✓` confermata in verde / `✗` da verificare).
- **Focus automatico da tastiera**: appena una scheda viene aperta o selezionata dal carosello, il focus si posiziona automaticamente sulla prima riga della tabella per navigare subito con `Freccia Su` / `Freccia Giù`.
- **Espansione con clic singolo**: un clic singolo su una categoria espande o comprime le singole scatole (`1a`, `2a`, ...). Con `Freccia Destra` su una categoria già espansa il cursore scende direttamente sulla prima scatola figlia.
- **Conferma riga o scatola**: premendo `Invio` (o cliccando sulla 4ª colonna `Stato`) si conferma/sconferma la singola scatola o l'intera categoria madre.

### 3.4 Doppia Modalità di Editing Inline dei Pesi
- **Modifica Rapida** (`Shift+Invio` o doppio clic sulla cella): apre l'editor inline sul peso; premendo `Invio` o `Freccia Su/Giù` salva il valore, ricalcola istantaneamente totali e flag ed **esce automaticamente dalla modalità modifica** tornando alla navigazione.
- **Modifica Continua** (pulsante `✏️ Modifica`): mantiene attivo l'editor inline mentre ci si sposta tra le righe con le frecce direzionali.

### 3.5 Scorciatoie da Tastiera Personalizzabili
Tutte le scorciatoie sono personalizzabili da **Impostazioni (`Ctrl+P`) -> ⌨️ Scorciatoie da Tastiera**:

| Azione | Scorciatoia Predefinita |
| :--- | :--- |
| Scansione ADF (Alimentatore) | `F2` |
| Scansione Piano Vetro | `Shift+F2` |
| Carica Immagini / PDF | `Ctrl+O` |
| Salva su Excel (Principale) | `Ctrl+Invio` |
| Salva su Excel (Alternativo) | `Alt+Invio` |
| Modifica Rapida Cella | `Shift+Invio` |
| Conferma Riga / Categoria (`✓`/`✗`) | `Invio` |
| Spunta Autorizzazione Forzata | `Tab` |
| Azzera Sessione | `F5` |
| Apri Impostazioni | `Ctrl+P` |

---

## 🧪 4. Esecuzione dei Test Automatici

Il progetto include **20 suite di test unitari e di integrazione (153 test totali)** in `APP_DATA/tests/`.
Per eseguire tutti i test:

```powershell
python -m unittest discover -s APP_DATA/tests -p "test_*.py" -t APP_DATA
```
