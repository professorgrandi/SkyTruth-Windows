"""
SkyTruth - main.py
Programma completo (v1.0): confronta gli aerei rilevati "dal vivo" da un
dongle RTL-SDR (dump1090) con i dati pubblici "mainstream" di OpenSky
Network, mostrando entrambi su mappe grafiche affiancate, insieme a una
vista live della webcam rivolta al cielo (senza registrazione) e alla
possibilita' di registrare l'intera sessione (video e/o dati testuali).

Funzionalita' principali:
- Rilevamento dongle RTL-SDR tramite Windows (PowerShell / Get-PnpDevice)
- Menu Tkinter con inserimento posizione/raggio di rilevazione
- Avvio dump1090 (minimizzato in taskbar) e lettura dati via rete (SBS)
- Mappa grafica "ghost" (dati reali dal dongle) e "mainstream" (OpenSky),
  in finestre native tramite pywebview
- Selezione e visualizzazione webcam (solo streaming, nessuna registrazione)
- Registrazione video dell'intera sessione (FFmpeg) e/o salvataggio delle
  rilevazioni aeree in file .txt
- Gestione robusta degli errori: nessuna condizione dovrebbe mai far
  crashare il programma senza un messaggio chiaro all'utente

Autori: by Professor Grandi (Mahatma) & Claude Sonnet 5
"""

import os
import sys
import time
import math
import ctypes
import socket
import shutil
import logging
import threading
import subprocess
import traceback
import webbrowser
import http.server
import json
import re
import base64
import importlib.util
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime

import tkinter as tk
from tkinter import messagebox
from tkinter import simpledialog


# --------------------------------------------------------------------------
# CONFIGURAZIONE PERCORSI DI BASE
# --------------------------------------------------------------------------
# Tutto il programma vive sotto questa cartella. Se in futuro si vuole
# spostare l'installazione, basta cambiare questa singola riga.
BASE_DIR = r"C:\SkyTruth"

VERSIONE_PROGRAMMA = "v1.0"

# Font unico usato per tutti i testi del menu (titolo escluso), cosi'
# l'aspetto e' uniforme in tutta la finestra.
FONT_MENU = ("Consolas", 12)
FONT_MENU_BOLD = ("Consolas", 12, "bold")
FONT_MENU_TITOLO = ("Consolas", 16, "bold")

LOG_DIR = os.path.join(BASE_DIR, "logs")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
DUMP1090_DIR = os.path.join(BASE_DIR, "dump1090")
MAPS_DIR = os.path.join(BASE_DIR, "maps")
FFMPEG_DIR = os.path.join(BASE_DIR, "ffmpeg")
SESSIONI_DIR = os.path.join(BASE_DIR, "sessioni_video")
SESSIONI_TXT_DIR = os.path.join(BASE_DIR, "sessioni_txt")
SNAPSHOTS_DIR = os.path.join(BASE_DIR, "snapshots")

# --- Sito web pubblico (SkyTruth-Windows-Site) ---
GITHUB_API_BASE = "https://api.github.com"
NOME_REPO_SITO = "SkyTruth-Windows-Site"
BROWSER_DIR = os.path.join(BASE_DIR, "browser")

DUMP1090_EXE = os.path.join(DUMP1090_DIR, "dump1090.exe")
DUMP1090_DOWNLOAD_URL = "https://github.com/timseed/Dump1090_Windows"


def ottieni_eseguibile_python_senza_console():
    """
    Ritorna il percorso di 'pythonw.exe' (la variante di Python che NON
    apre nessuna finestra console), se disponibile accanto all'interprete
    corrente. La usiamo per lanciare gli script "solo GUI" (mappe,
    webcam), cosi' non condividono per errore la console di main.py e non
    aprono finestre console indesiderate o ambigue.

    Se 'pythonw.exe' non viene trovato, ricade sull'interprete normale.
    """
    candidato = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.isfile(candidato):
        return candidato

    logging.warning("pythonw.exe non trovato accanto a %s, uso l'interprete normale.",
                     sys.executable)
    return sys.executable

TITOLO_FINESTRA_DUMP1090 = "SkyTruth - Dongle Live Data"
TITOLO_FINESTRA_MAPPA_REALE = "SkyTruth - Rilevazione Reale"
TITOLO_FINESTRA_MAPPA_OPENSKY = "SkyTruth - OpenSky Mainstream"

DUMP1090_SBS_HOST = "127.0.0.1"
DUMP1090_SBS_PORT = 30003
MAPPA_REALE_HTTP_PORT = 8081
MAPPA_OPENSKY_HTTP_PORT = 8082

OPENSKY_API_URL = "https://opensky-network.org/api/states/all"
OPENSKY_INTERVALLO_AGGIORNAMENTO_SEC = 15

# Autenticazione OAuth2 (obbligatoria dal 18 marzo 2026, sostituisce il
# vecchio sistema utente/password). Senza credenziali, si continua
# comunque in modalita' anonima (piu' limitata, ma funzionante).
OPENSKY_AUTH_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

_opensky_client_id = None
_opensky_client_secret = None
_opensky_token = None
_opensky_token_scadenza = 0  # timestamp Unix di scadenza del token

MAPPA_VIEWER_SCRIPT = os.path.join(MAPS_DIR, "mappa_viewer.py")
PYWEBVIEW_ISTRUZIONI_INSTALLAZIONE = "pip install pywebview"

WEBCAM_VIEWER_SCRIPT = os.path.join(BASE_DIR, "webcam_viewer.py")
OPENCV_ISTRUZIONI_INSTALLAZIONE = "pip install opencv-python"
TITOLO_FINESTRA_WEBCAM = "SkyTruth - Cielo Live"

FFMPEG_EXE = os.path.join(FFMPEG_DIR, "ffmpeg.exe")
FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html#build-windows"

# Elenco di tutti i processi "figli" avviati dal programma (dump1090,
# finestre mappa, webcam), cosi' possiamo chiuderli tutti insieme quando
# l'utente esce dal programma.
_processi_secondari = []


def registra_processo_secondario(processo):
    """Aggiunge un processo all'elenco di quelli da chiudere all'uscita."""
    _processi_secondari.append(processo)


def chiudi_tutti_i_processi_secondari():
    """
    Chiude (in modo ordinato) tutti i processi figli avviati dal
    programma: dump1090, le finestre delle mappe, la webcam. Ogni
    chiusura e' protetta da un try/except, cosi' un singolo processo
    gia' terminato o non rispondente non blocca la chiusura degli altri.
    """
    for processo in list(_processi_secondari):
        try:
            if processo.poll() is None:  # ancora in esecuzione
                processo.terminate()
                logging.info("Processo secondario (PID %s) terminato.", processo.pid)
        except Exception:
            logging.warning("Impossibile terminare un processo secondario:\n%s",
                             traceback.format_exc())

    _processi_secondari.clear()


# Evita di mostrare piu' volte lo stesso avviso "libreria non installata"
_avviso_pywebview_mostrato = False
_avviso_opencv_mostrato = False


# --------------------------------------------------------------------------
# LOGGING
# --------------------------------------------------------------------------
def setup_logging():
    """
    Prepara il sistema di log. Se la cartella logs non esiste la crea.
    Tutti gli errori del programma finiranno in un file di log invece
    di far crashare l'app senza spiegazioni.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        # Se anche la creazione della cartella log fallisce, non possiamo
        # scrivere su file: ci limitiamo a stampare a video.
        print("ATTENZIONE: impossibile creare la cartella logs. "
              "I log verranno mostrati solo a video.")

    log_filename = datetime.now().strftime("flyvsghost_%Y-%m-%d.log")
    log_path = os.path.join(LOG_DIR, log_filename)

    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def log_uncaught_exceptions(exc_type, exc_value, exc_traceback):
    """
    Intercetta QUALSIASI errore non previsto nel programma.
    Invece di far chiudere bruscamente il programma (crash), lo registra
    nel file di log e mostra un messaggio comprensibile all'utente.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        # Se l'utente ferma il programma con Ctrl+C, usciamo normalmente
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    error_text = "".join(
        traceback.format_exception(exc_type, exc_value, exc_traceback)
    )
    logging.error("ERRORE NON GESTITO:\n%s", error_text)

    try:
        messagebox.showerror(
            "SkyTruth - Errore",
            "Si e' verificato un errore imprevisto.\n"
            "Il dettaglio e' stato salvato nel file di log nella cartella:\n"
            f"{LOG_DIR}\n\n"
            "Il programma prova a continuare, se possibile."
        )
    except Exception:
        # Se anche mostrare il popup fallisce, non c'e' altro da fare
        # se non aver gia' scritto l'errore nel log.
        pass


# --------------------------------------------------------------------------
# CONTROLLO POWERSHELL (necessario per rilevare dongle e webcam)
# --------------------------------------------------------------------------
POWERSHELL_DOWNLOAD_URL = "https://aka.ms/powershell-release?tag=stable"

# Evita di mostrare piu' volte lo stesso avviso
_avviso_powershell_mostrato = False


def verifica_powershell_disponibile():
    """
    Controlla che 'powershell.exe' sia disponibile sul sistema (e' incluso
    di serie in ogni installazione standard di Windows 10/11, quindi in
    condizioni normali questo controllo passa sempre; serve solo come
    rete di sicurezza per casi anomali).
    """
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "exit"],
            capture_output=True, timeout=5
        )
        return True
    except FileNotFoundError:
        return False
    except Exception:
        # Se PowerShell risponde ma con un errore diverso, consideriamo
        # comunque che sia presente sul sistema.
        return True


def avvisa_se_powershell_mancante():
    """
    Se PowerShell non e' disponibile, avvisa l'utente una sola volta e
    apre automaticamente la pagina ufficiale Microsoft per scaricarlo,
    senza bloccare l'avvio del resto del programma.
    """
    global _avviso_powershell_mostrato

    if verifica_powershell_disponibile():
        return

    if _avviso_powershell_mostrato:
        return
    _avviso_powershell_mostrato = True

    logging.warning("PowerShell non risulta disponibile sul sistema.")
    messagebox.showwarning(
        "SkyTruth - PowerShell non trovato",
        "PowerShell non risulta disponibile sul sistema.\n"
        "E' necessario per rilevare il dongle e le webcam.\n\n"
        "Si aprira' ora la pagina ufficiale Microsoft per scaricarlo.\n"
        "Installalo, poi riavvia il programma."
    )
    try:
        webbrowser.open(POWERSHELL_DOWNLOAD_URL)
    except Exception:
        logging.warning("Impossibile aprire la pagina di download di PowerShell.")


# --------------------------------------------------------------------------
# RILEVAMENTO DONGLE RTL-SDR
# --------------------------------------------------------------------------
def rileva_dongle():
    """
    Chiede a Windows (tramite PowerShell) l'elenco delle periferiche USB
    collegate e cerca parole chiave tipiche dei dongle RTL-SDR
    (es. "NESDR", "RTL", "SDR").

    Ritorna:
        - il nome della periferica trovata (stringa), oppure
        - None se non viene trovata nessuna periferica compatibile
    """
    comando_powershell = (
        "Get-PnpDevice -PresentOnly | "
        "Where-Object {$_.FriendlyName -like '*RTL*' -or "
        "$_.FriendlyName -like '*SDR*' -or "
        "$_.FriendlyName -like '*NESDR*'} | "
        "Select-Object -ExpandProperty FriendlyName"
    )

    try:
        risultato = subprocess.run(
            ["powershell", "-NoProfile", "-Command", comando_powershell],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        logging.error("PowerShell non trovato sul sistema.")
        return None
    except subprocess.TimeoutExpired:
        logging.error("Timeout durante il rilevamento del dongle.")
        return None
    except Exception:
        logging.error("Errore imprevisto durante il rilevamento del dongle:\n%s",
                       traceback.format_exc())
        return None

    output = (risultato.stdout or "").strip()

    if output:
        # Se ci sono piu' righe, prendiamo la prima periferica trovata
        prima_riga = output.splitlines()[0].strip()
        logging.info("Dongle rilevato: %s", prima_riga)
        return prima_riga

    logging.warning("Nessun dongle RTL-SDR rilevato.")
    return None


# --------------------------------------------------------------------------
# AREA DI LAVORO REALE DELLO SCHERMO (esclude la barra delle applicazioni)
# --------------------------------------------------------------------------
class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def ottieni_area_lavoro():
    """
    Ritorna (left, top, right, bottom) dell'area di lavoro REALE dello
    schermo, cioe' escludendo la barra delle applicazioni di Windows.
    Usare questa funzione (invece di winfo_screenwidth/height, che
    includono anche l'area sotto la taskbar) evita che le finestre
    vengano posizionate parzialmente nascoste o sovrapposte.
    """
    SPI_GETWORKAREA = 0x0030
    rect = _RECT()
    try:
        ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
        )
        if rect.right > rect.left and rect.bottom > rect.top:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        logging.warning("Impossibile ottenere l'area di lavoro reale, "
                         "uso i valori di default.")

    # Valore di ripiego se la chiamata di sistema fallisse per qualche motivo
    return 0, 0, 1920, 1040


def calcola_quadranti():
    """
    Divide l'area di lavoro reale in 4 quadranti uguali, calcolati a
    partire dal CENTRO dello schermo verso i lati (invece che dividendo
    larghezza/altezza separatamente), per evitare disallineamenti da
    arrotondamento tra i vari riquadri.

    Ritorna un dizionario con le coordinate (x, y, larghezza, altezza)
    di ciascun quadrante: 'alto_sx', 'basso_sx', 'alto_dx', 'basso_dx'.
    """
    left, top, right, bottom = ottieni_area_lavoro()
    centro_x = (left + right) // 2
    centro_y = (top + bottom) // 2

    return {
        "alto_sx": (left, top, centro_x - left, centro_y - top),
        "basso_sx": (left, centro_y, centro_x - left, bottom - centro_y),
        "alto_dx": (centro_x, top, right - centro_x, centro_y - top),
        "basso_dx": (centro_x, centro_y, right - centro_x, bottom - centro_y),
    }


# --------------------------------------------------------------------------
# GESTIONE FINESTRE ESTERNE (posizionamento tramite "differenza")
# --------------------------------------------------------------------------
# NOTA TECNICA: la ricerca per PID del processo NON funziona per le
# finestre console (es. dump1090): su Windows, la finestra console
# visibile appartiene realmente a un processo di sistema associato
# ('conhost.exe'), non al processo che l'ha aperta. Per questo cerchiamo
# le finestre in un altro modo, valido per QUALSIASI tipo di finestra:
# facciamo una "fotografia" di quali finestre sono visibili PRIMA di
# avviare il processo, poi cerchiamo la prima finestra nuova che compare
# DOPO. Non serve sapere a quale processo appartenga davvero.
def enumera_finestre_visibili():
    """Ritorna la lista degli handle di tutte le finestre attualmente visibili e con un titolo."""
    user32 = ctypes.windll.user32
    hwnds = []

    ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def _callback(hwnd, lparam):
        try:
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                hwnds.append(hwnd)
        except Exception:
            pass
        return True

    callback_c = ENUM_WINDOWS_PROC(_callback)
    try:
        user32.EnumWindows(callback_c, 0)
    except Exception:
        logging.warning("Errore durante l'enumerazione delle finestre.")

    return hwnds


def attendi_nuova_finestra(finestre_precedenti, tentativi=50, attesa_secondi=0.3):
    """
    Attende che compaia una nuova finestra visibile, non presente
    nell'elenco 'finestre_precedenti' (preso PRIMA di avviare il
    processo). Ritorna l'handle della finestra piu' recente trovata,
    oppure None se nessuna nuova finestra compare in tempo.

    Per evitare di agganciare per errore una finestra "temporanea" (alcune
    applicazioni, specialmente quelle basate su Cygwin, possono mostrare
    per un istante una finestra transitoria prima di quella definitiva),
    verifichiamo che la stessa finestra sia ancora presente e visibile
    anche in un controllo successivo, prima di considerarla valida.
    """
    precedenti = set(finestre_precedenti)
    candidata = None

    for _ in range(tentativi):
        attuali = enumera_finestre_visibili()
        nuove = [h for h in attuali if h not in precedenti]

        if nuove:
            if candidata in nuove:
                # La stessa finestra e' ancora li' al controllo successivo:
                # la consideriamo stabile e la restituiamo.
                return candidata
            candidata = nuove[-1]
        else:
            candidata = None

        time.sleep(attesa_secondi)

    logging.warning("Nessuna nuova finestra apparsa (o stabile) dopo %s tentativi.", tentativi)
    return None


def posiziona_nuova_finestra_in_background(finestre_precedenti, x, y, larghezza, altezza, etichetta=""):
    """
    In un thread separato, attende la comparsa di una nuova finestra e la
    sposta/ridimensiona esattamente alle coordinate date (MoveWindow).
    """
    def _lavoro():
        hwnd = attendi_nuova_finestra(finestre_precedenti)
        if hwnd:
            try:
                ctypes.windll.user32.MoveWindow(hwnd, x, y, larghezza, altezza, True)
                logging.info("Finestra '%s' posizionata correttamente.", etichetta)
            except Exception:
                logging.warning("Impossibile posizionare la finestra '%s':\n%s",
                                 etichetta, traceback.format_exc())
        else:
            logging.warning("Finestra '%s' non trovata, impossibile posizionarla.", etichetta)

    threading.Thread(target=_lavoro, daemon=True).start()


def minimizza_nuova_finestra_in_background(finestre_precedenti, etichetta=""):
    """
    In un thread separato, attende la comparsa di una nuova finestra e la
    minimizza nella barra delle applicazioni, senza chiuderla: l'utente
    potra' comunque riaprirla in qualsiasi momento cliccandoci sopra.
    """
    SW_SHOWMINNOACTIVE = 7

    def _lavoro():
        hwnd = attendi_nuova_finestra(finestre_precedenti)
        if hwnd:
            try:
                ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
                logging.info("Finestra '%s' minimizzata in taskbar.", etichetta)
            except Exception:
                logging.warning("Impossibile minimizzare la finestra '%s':\n%s",
                                 etichetta, traceback.format_exc())
        else:
            logging.warning("Finestra '%s' non trovata, impossibile minimizzarla.", etichetta)

    threading.Thread(target=_lavoro, daemon=True).start()


# --------------------------------------------------------------------------
# AVVIO DUMP1090
# --------------------------------------------------------------------------
def avvia_dump1090():
    """
    Avvia dump1090.exe normalmente, poi lo minimizza nella barra delle
    applicazioni tramite il suo PID (non tramite il titolo o le opzioni
    di avvio, che con alcune build di dump1090 basate su Cygwin possono
    essere ignorate). Cosi' compare come icona in taskbar, richiamabile
    dall'utente in qualsiasi momento per controllare i dati in arrivo.

    Ritorna:
        - l'oggetto processo (subprocess.Popen) se avviato correttamente
        - None se dump1090.exe non e' stato trovato (mostra un messaggio
          d'errore all'utente, senza far crashare il programma)
    """
    if not os.path.isfile(DUMP1090_EXE):
        logging.error("dump1090.exe non trovato in: %s", DUMP1090_EXE)
        messagebox.showerror(
            "SkyTruth - dump1090 non presente",
            "dump1090.exe non e' stato trovato nella cartella:\n"
            f"{DUMP1090_DIR}\n\n"
            "Scarica ed installa la release da questo indirizzo:\n"
            f"{DUMP1090_DOWNLOAD_URL}\n\n"
            f"Estrai i file dentro:\n{DUMP1090_DIR}\n\n"
            "Poi riavvia il programma."
        )
        return None

    try:
        # CREATE_NEW_CONSOLE: garantisce che dump1090 abbia una console
        # TUTTA SUA, separata da quella di main.py. Senza questo,
        # Windows puo' far condividere la stessa console a piu' processi,
        # causando comportamenti anomali (es. un Ctrl+C che chiude piu'
        # processi contemporaneamente).
        flag_nuova_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

        finestre_prima = enumera_finestre_visibili()

        processo = subprocess.Popen(
            [DUMP1090_EXE, "--interactive", "--net", "--metric"],
            cwd=DUMP1090_DIR,
            creationflags=flag_nuova_console
        )
        logging.info("dump1090.exe avviato correttamente (PID %s).", processo.pid)
        registra_processo_secondario(processo)
        minimizza_nuova_finestra_in_background(finestre_prima, etichetta="dump1090")
        return processo
    except Exception:
        logging.error("Errore durante l'avvio di dump1090.exe:\n%s",
                       traceback.format_exc())
        messagebox.showerror(
            "SkyTruth - Errore avvio dump1090",
            "Non e' stato possibile avviare dump1090.exe.\n"
            f"Dettagli nel log: {LOG_DIR}"
        )
        return None


# --------------------------------------------------------------------------
# MAPPA REALE (Fase 3): riceve i dati da dump1090 e li mostra su mappa
# --------------------------------------------------------------------------
# Struttura dati condivisa tra il thread che legge da dump1090 e il thread
# del web server. Protetta da un lock per evitare accessi contemporanei.
_aereo_lock = threading.Lock()
_aerei_rilevati = {}          # chiave: icao, valore: dict con dati aereo

# "Storico" della sessione corrente: accumula OGNI aereo mai visto nel
# raggio durante l'intera sessione (non solo l'ultimo istante), cosi'
# il caricamento sul sito riflette davvero tutta la sessione, anche se
# al momento del click non c'e' piu' traffico nel cielo.
_sessione_storico_lock = threading.Lock()
_sessione_ghost_storico = {}
_sessione_mainstream_storico = {}
_mappa_reale_home = {"lat": None, "lon": None, "raggio_km": None}
_mappa_reale_server_avviato = False

# Se un aereo non riceve aggiornamenti da piu' di questo tempo (secondi),
# lo consideriamo "perso" e non lo mostriamo piu' sulla mappa.
SOGLIA_SCADENZA_AEREO_SEC = 60


def _calcola_distanza_km(lat1, lon1, lat2, lon2):
    """Calcola la distanza in km tra due coordinate (formula di haversine)."""
    raggio_terra_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return raggio_terra_km * c


def _elabora_riga_sbs(riga):
    """
    Analizza una singola riga di dati in formato SBS (BaseStation) inviata
    da dump1090 sulla porta 30003, ed aggiorna i dati dell'aereo
    corrispondente. Formato SBS (campi separati da virgola), quelli che
    ci interessano:
      indice 4  -> codice ICAO (identificativo univoco aereo)
      indice 10 -> nominativo/callsign
      indice 11 -> altitudine
      indice 12 -> velocita' al suolo
      indice 13 -> rotta/track (gradi)
      indice 14 -> latitudine
      indice 15 -> longitudine
    Ogni riga aggiorna solo i campi che contiene: i messaggi SBS sono di
    tipi diversi (alcuni hanno solo la posizione, altri solo il callsign).
    """
    try:
        campi = riga.strip().split(",")
        if len(campi) < 16 or campi[0] != "MSG":
            return

        icao = campi[4].strip()
        if not icao:
            return

        with _aereo_lock:
            aereo = _aerei_rilevati.setdefault(icao, {"icao": icao})
            aereo["ultimo_aggiornamento"] = time.time()

            if campi[10].strip():
                aereo["callsign"] = campi[10].strip()
            if campi[11].strip():
                aereo["altitudine"] = campi[11].strip()
            if campi[12].strip():
                aereo["velocita"] = campi[12].strip()
            if campi[13].strip():
                aereo["rotta"] = campi[13].strip()
            if campi[14].strip() and campi[15].strip():
                aereo["lat"] = float(campi[14])
                aereo["lon"] = float(campi[15])

                # Se conosciamo gia' il punto di osservazione, calcoliamo
                # subito la distanza: serve sia per il salvataggio in txt,
                # sia in generale per avere il dato sempre pronto.
                if _mappa_reale_home["lat"] is not None and _mappa_reale_home["lon"] is not None:
                    aereo["distanza_km"] = round(
                        _calcola_distanza_km(
                            _mappa_reale_home["lat"], _mappa_reale_home["lon"],
                            aereo["lat"], aereo["lon"]
                        ), 1
                    )

                # Ogni aggiornamento di posizione e' un buon momento per
                # controllare se l'aereo ha ormai tutti i dati completi e,
                # se il salvataggio in txt e' attivo, scrivere una riga.
                _scrivi_riga_txt_se_completa(aereo)
    except Exception:
        # Una singola riga malformata non deve mai bloccare il programma:
        # la scartiamo e proseguiamo con la prossima.
        logging.debug("Riga SBS ignorata (formato inatteso): %s", riga.strip())


def _thread_lettura_dati_dump1090():
    """
    Si collega in continuazione alla porta 30003 di dump1090 (dati SBS) e
    aggiorna la lista degli aerei rilevati. Se la connessione cade o
    dump1090 non e' ancora pronto, riprova automaticamente ogni pochi
    secondi, senza mai bloccare il resto del programma.
    """
    while True:
        try:
            with socket.create_connection(
                (DUMP1090_SBS_HOST, DUMP1090_SBS_PORT), timeout=5
            ) as conn:
                logging.info("Connesso a dump1090 (dati SBS, porta %s).",
                             DUMP1090_SBS_PORT)
                file_dati = conn.makefile("r", encoding="utf-8", errors="ignore")
                for riga in file_dati:
                    _elabora_riga_sbs(riga)
        except Exception:
            logging.debug("Dati SBS non disponibili al momento, nuovo tentativo tra 3 secondi.")
            time.sleep(3)


def _aerei_nel_raggio():
    """
    Ritorna la lista degli aerei attualmente validi (con posizione nota,
    non scaduti) che si trovano entro il raggio impostato dall'utente,
    insieme alla loro distanza in km dal punto di osservazione.
    """
    lat_casa = _mappa_reale_home["lat"]
    lon_casa = _mappa_reale_home["lon"]
    raggio_km = _mappa_reale_home["raggio_km"]

    if lat_casa is None or lon_casa is None or raggio_km is None:
        return []

    ora = time.time()
    risultato = []

    with _aereo_lock:
        elenco_aerei = list(_aerei_rilevati.values())

    for aereo in elenco_aerei:
        if "lat" not in aereo or "lon" not in aereo:
            continue
        if ora - aereo.get("ultimo_aggiornamento", 0) > SOGLIA_SCADENZA_AEREO_SEC:
            continue

        distanza = _calcola_distanza_km(lat_casa, lon_casa, aereo["lat"], aereo["lon"])
        if distanza <= raggio_km:
            aereo_con_distanza = dict(aereo)
            aereo_con_distanza["distanza_km"] = round(distanza, 1)
            risultato.append(aereo_con_distanza)

    # Aggiorniamo lo storico della sessione: ogni aereo visto qui viene
    # ricordato per tutta la durata della sessione, non solo nell'istante
    # in cui viene chiamata questa funzione.
    if risultato:
        with _sessione_storico_lock:
            for aereo in risultato:
                icao = aereo.get("icao")
                if icao:
                    _sessione_ghost_storico[icao] = {
                        "icao": icao,
                        "callsign": (aereo.get("callsign") or icao).strip(),
                        "altitudine": aereo.get("altitudine", ""),
                    }

    return risultato


_PAGINA_HTML_MAPPA_REALE = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>__TITOLO__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html, body, #mappa { height: 100%; margin: 0; padding: 0; background: #111; }
  #pannello {
    position: absolute; top: 8px; left: 8px; z-index: 1000;
    background: rgba(20,20,20,0.85); color: #0f0; font-family: Consolas, monospace;
    padding: 6px 10px; border-radius: 4px; font-size: 12px;
  }
</style>
</head>
<body>
<div id="pannello">SkyTruth - Rilevazione Reale (dati dongle)</div>
<div id="mappa"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  const mappa = L.map('mappa').setView([__LAT__, __LON__], 8);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: 'OpenStreetMap'
  }).addTo(mappa);

  const iconaCasa = L.divIcon({className: '', html: '<div style="font-size:22px;">📍</div>'});
  L.marker([__LAT__, __LON__], {icon: iconaCasa}).addTo(mappa).bindPopup('Punto di osservazione');

  L.circle([__LAT__, __LON__], {radius: __RAGGIO_METRI__, color: '#0f0', fill: false}).addTo(mappa);

  let markerAerei = {};

  async function aggiorna() {
    try {
      const risposta = await fetch('/data.json');
      const dati = await risposta.json();

      const icaoPresenti = new Set();

      dati.aerei.forEach(a => {
        icaoPresenti.add(a.icao);
        const etichetta = (a.callsign || a.icao) + ' - ' + (a.distanza_km ?? '?') + ' km';

        if (markerAerei[a.icao]) {
          markerAerei[a.icao].setLatLng([a.lat, a.lon]);
          markerAerei[a.icao].setPopupContent(etichetta);
        } else {
          const icona = L.divIcon({className: '', html: '<div style="font-size:18px;">✈️</div>'});
          markerAerei[a.icao] = L.marker([a.lat, a.lon], {icon: icona})
            .addTo(mappa).bindPopup(etichetta);
        }
      });

      // Rimuoviamo i marker degli aerei non piu' presenti (usciti dal raggio o scaduti)
      Object.keys(markerAerei).forEach(icao => {
        if (!icaoPresenti.has(icao)) {
          mappa.removeLayer(markerAerei[icao]);
          delete markerAerei[icao];
        }
      });

      document.getElementById('pannello').innerText =
        'SkyTruth - Rilevazione Reale | aerei nel raggio: ' + dati.aerei.length;
    } catch (e) {
      document.getElementById('pannello').innerText =
        'SkyTruth - Rilevazione Reale | in attesa di dati...';
    }
  }

  aggiorna();
  setInterval(aggiorna, 3000);
</script>
</body>
</html>
"""


class _GestoreRichiesteMappaReale(http.server.BaseHTTPRequestHandler):
    """
    Piccolo web server locale (solo libreria standard di Python) che serve
    la pagina della mappa e i dati aggiornati degli aerei in formato JSON.
    """

    def do_GET(self):
        try:
            if self.path.startswith("/data.json"):
                self._rispondi_json()
            else:
                self._rispondi_pagina_html()
        except Exception:
            logging.error("Errore nel server della mappa reale:\n%s",
                           traceback.format_exc())
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _rispondi_pagina_html(self):
        lat = _mappa_reale_home["lat"] or 0
        lon = _mappa_reale_home["lon"] or 0
        raggio_km = _mappa_reale_home["raggio_km"] or 100

        html = (_PAGINA_HTML_MAPPA_REALE
                .replace("__TITOLO__", TITOLO_FINESTRA_MAPPA_REALE)
                .replace("__LAT__", str(lat))
                .replace("__LON__", str(lon))
                .replace("__RAGGIO_METRI__", str(raggio_km * 1000)))

        corpo = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _rispondi_json(self):
        dati = {"aerei": _aerei_nel_raggio()}
        corpo = json.dumps(dati).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, format, *args):
        # Evitiamo che ogni richiesta della pagina (ogni 3 secondi) riempia
        # la console di log: la mandiamo solo a livello DEBUG.
        logging.debug("Richiesta mappa reale: " + format, *args)


def _thread_web_server_mappa_reale():
    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", MAPPA_REALE_HTTP_PORT), _GestoreRichiesteMappaReale
        )
        server.serve_forever()
    except Exception:
        logging.error("Impossibile avviare il web server della mappa reale:\n%s",
                       traceback.format_exc())


def avvia_mappa_reale(lat, lon, raggio_km, x, y, larghezza, altezza):
    """
    Avvia (se non gia' attivi) i thread di lettura dati e web server per
    la mappa reale, poi apre la finestra nativa (pywebview) gia'
    posizionata nelle coordinate indicate.
    """
    global _mappa_reale_server_avviato

    _mappa_reale_home["lat"] = lat
    _mappa_reale_home["lon"] = lon
    _mappa_reale_home["raggio_km"] = raggio_km

    if not _mappa_reale_server_avviato:
        try:
            threading.Thread(target=_thread_lettura_dati_dump1090, daemon=True).start()
            threading.Thread(target=_thread_web_server_mappa_reale, daemon=True).start()
            _mappa_reale_server_avviato = True
            logging.info("Server mappa reale avviato sulla porta %s.", MAPPA_REALE_HTTP_PORT)
        except Exception:
            logging.error("Errore nell'avvio dei servizi della mappa reale:\n%s",
                           traceback.format_exc())
            messagebox.showerror(
                "SkyTruth - Errore mappa reale",
                "Non e' stato possibile avviare la mappa reale.\n"
                f"Dettagli nel log: {LOG_DIR}"
            )
            return

    apri_mappa_in_finestra_nativa(
        f"http://127.0.0.1:{MAPPA_REALE_HTTP_PORT}/",
        TITOLO_FINESTRA_MAPPA_REALE, x, y, larghezza, altezza
    )


# --------------------------------------------------------------------------
# VISUALIZZAZIONE MAPPE IN FINESTRA NATIVA (pywebview)
# --------------------------------------------------------------------------
# Invece di dipendere da un browser esterno (che ha dato problemi di
# "istanza gia' in uso"), usiamo 'pywebview': apre finestre native che
# sfruttano il motore WebView2 gia' incluso in Windows 11, senza barra
# indirizzi ne' schede - solo contenuto e barra del titolo con la X.
# Ogni finestra e' un processo separato e indipendente, quindi possiamo
# averne aperte quante ne vogliamo senza conflitti tra loro.
_CONTENUTO_SCRIPT_VISUALIZZATORE = '''"""
SkyTruth - mappa_viewer.py (generato automaticamente da main.py)
Apre una singola pagina web in una finestra nativa pulita, usando pywebview.
Il posizionamento preciso viene corretto dall'esterno (da main.py, tramite
il PID di questo processo), quindi qui usiamo solo una dimensione di
partenza indicativa.
Uso: mappa_viewer.py <url> <titolo> <larghezza> <altezza>
"""
import sys
import os

LOG_PATH = os.path.join(r"%(log_dir)s", "mappa_viewer.log")


def scrivi_log(messaggio):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(messaggio + "\\n")
    except Exception:
        pass


def mostra_errore(titolo, messaggio):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(titolo, messaggio)
    except Exception:
        pass


def main():
    try:
        url = sys.argv[1]
        titolo = sys.argv[2]
        larghezza = int(sys.argv[3])
        altezza = int(sys.argv[4])
    except Exception as e:
        scrivi_log(f"Argomenti non validi: {e}")
        return

    try:
        import webview
    except ImportError:
        messaggio = (
            "La libreria 'pywebview' non e' installata.\\n"
            "Apri un prompt dei comandi ed esegui:\\n"
            "pip install pywebview"
        )
        scrivi_log("Libreria 'pywebview' non installata.")
        mostra_errore("SkyTruth - Libreria mancante", messaggio)
        return

    try:
        webview.create_window(titolo, url, width=larghezza, height=altezza)
        webview.start()
    except Exception as e:
        scrivi_log(f"Errore nell'avvio della finestra webview: {e}")
        mostra_errore("SkyTruth - Errore mappa",
                       f"Impossibile aprire la finestra della mappa.\\nDettagli: {e}")


if __name__ == "__main__":
    main()
'''


def scrivi_script_visualizzatore_mappa():
    """
    Genera (o rigenera) lo script 'mappa_viewer.py' dentro la cartella
    'maps\\'. Lo facciamo ad ogni avvio del programma per tenerlo sempre
    aggiornato, senza che l'utente debba installarlo o copiarlo a mano.
    """
    try:
        os.makedirs(MAPS_DIR, exist_ok=True)
        contenuto = _CONTENUTO_SCRIPT_VISUALIZZATORE % {"log_dir": LOG_DIR}
        with open(MAPPA_VIEWER_SCRIPT, "w", encoding="utf-8") as f:
            f.write(contenuto)
    except Exception:
        logging.warning("Impossibile generare lo script mappa_viewer.py:\n%s",
                         traceback.format_exc())


def verifica_pywebview_disponibile():
    """
    Controlla se la libreria 'pywebview' e' installata nell'ambiente
    Python corrente. Se manca, avvisa l'utente una sola volta con le
    istruzioni per installarla, senza bloccare il resto del programma.
    """
    global _avviso_pywebview_mostrato

    disponibile = importlib.util.find_spec("webview") is not None

    if not disponibile and not _avviso_pywebview_mostrato:
        _avviso_pywebview_mostrato = True
        messagebox.showwarning(
            "SkyTruth - Libreria mancante",
            "La libreria 'pywebview' non risulta installata.\n"
            "Le finestre delle mappe non si apriranno finche' non viene "
            "installata.\n\n"
            "Apri un prompt dei comandi ed esegui:\n"
            f"{PYWEBVIEW_ISTRUZIONI_INSTALLAZIONE}\n\n"
            "Poi riprova a cliccare il bottone della mappa."
        )

    return disponibile


def apri_mappa_in_finestra_nativa(url, titolo, x, y, larghezza, altezza):
    """
    Avvia lo script 'mappa_viewer.py' come processo separato (pywebview
    crea la finestra con una dimensione indicativa), poi ne corregge la
    posizione/dimensione ESATTA dal nostro processo principale, con lo
    stesso meccanismo "prima/dopo" usato per dump1090. Questo evita
    discrepanze di scala tra le diverse tecnologie grafiche.
    """
    if not verifica_pywebview_disponibile():
        return

    scrivi_script_visualizzatore_mappa()

    try:
        eseguibile = ottieni_eseguibile_python_senza_console()
        finestre_prima = enumera_finestre_visibili()

        processo = subprocess.Popen([
            eseguibile, MAPPA_VIEWER_SCRIPT,
            url, titolo, str(larghezza), str(altezza)
        ])
        logging.info("Finestra mappa avviata (pywebview, PID %s): %s - %s",
                     processo.pid, titolo, url)
        registra_processo_secondario(processo)
        posiziona_nuova_finestra_in_background(
            finestre_prima, x, y, larghezza, altezza, etichetta=titolo
        )
    except Exception:
        logging.error("Errore nell'avvio della finestra mappa (pywebview):\n%s",
                       traceback.format_exc())
        messagebox.showerror(
            "SkyTruth - Errore mappa",
            "Non e' stato possibile aprire la finestra della mappa.\n"
            f"Dettagli nel log: {LOG_DIR}"
        )


# --------------------------------------------------------------------------
# MAPPA OPENSKY "MAINSTREAM" (Fase 4)
# --------------------------------------------------------------------------
# OpenSky Network e' un'API pubblica e gratuita che fornisce posizioni di
# aerei "mainstream" (cioe' quelli che trasmettono pubblicamente la loro
# posizione). La confrontiamo con i dati del nostro dongle per vedere
# eventuali differenze.
_opensky_lock = threading.Lock()
_opensky_aerei = []
_opensky_home = {"lat": None, "lon": None, "raggio_km": None}
_opensky_server_avviato = False
_opensky_ultimo_errore = None


def _calcola_bounding_box(lat, lon, raggio_km):
    """
    Calcola un rettangolo geografico (lamin, lomin, lamax, lomax) che
    contiene per intero il cerchio di raggio 'raggio_km' centrato su
    (lat, lon). Serve perche' l'API di OpenSky richiede un'area
    rettangolare, non un cerchio.
    """
    delta_lat = raggio_km / 111.0
    # La distanza in km di un grado di longitudine varia con la latitudine
    coseno_lat = max(math.cos(math.radians(lat)), 0.01)
    delta_lon = raggio_km / (111.320 * coseno_lat)

    return (lat - delta_lat, lon - delta_lon, lat + delta_lat, lon + delta_lon)


def imposta_credenziali_opensky(client_id, client_secret):
    """Salva le credenziali OpenSky in memoria (mai su disco), per questa sessione."""
    global _opensky_client_id, _opensky_client_secret, _opensky_token, _opensky_token_scadenza
    _opensky_client_id = client_id
    _opensky_client_secret = client_secret
    # Forziamo la richiesta di un nuovo token al prossimo utilizzo
    _opensky_token = None
    _opensky_token_scadenza = 0


def _ottieni_token_opensky():
    """
    Ritorna un token OAuth2 valido per OpenSky, richiedendone uno nuovo
    solo se manca o sta per scadere. Ritorna None se non sono state
    impostate credenziali, o se la richiesta del token fallisce (in tal
    caso si procede in modalita' anonima, senza bloccare il programma).
    """
    global _opensky_token, _opensky_token_scadenza

    if not _opensky_client_id or not _opensky_client_secret:
        return None

    # Rinnoviamo con un margine di sicurezza di 60 secondi prima della
    # scadenza effettiva, per non rischiare di usare un token appena
    # scaduto durante una richiesta.
    if _opensky_token and time.time() < (_opensky_token_scadenza - 60):
        return _opensky_token

    try:
        dati_form = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": _opensky_client_id,
            "client_secret": _opensky_client_secret,
        }).encode("utf-8")

        richiesta = urllib.request.Request(
            OPENSKY_AUTH_URL, data=dati_form, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(richiesta, timeout=15) as risposta:
            corpo = json.loads(risposta.read().decode("utf-8"))

        _opensky_token = corpo["access_token"]
        _opensky_token_scadenza = time.time() + corpo.get("expires_in", 1800)
        logging.info("Token OpenSky ottenuto correttamente (valido %s secondi).",
                     corpo.get("expires_in", 1800))
        return _opensky_token
    except Exception:
        logging.warning("Impossibile ottenere il token OpenSky, procedo in modalita' anonima:\n%s",
                         traceback.format_exc())
        return None


def _thread_lettura_opensky():
    """
    Interroga periodicamente l'API pubblica di OpenSky Network e aggiorna
    la lista degli aerei "mainstream" nel raggio impostato. Gestisce in
    modo tollerante eventuali errori di rete o limiti di richieste,
    riprovando piu' lentamente in caso di problemi, senza mai bloccare
    il resto del programma.
    """
    global _opensky_ultimo_errore

    intervallo_corrente = OPENSKY_INTERVALLO_AGGIORNAMENTO_SEC

    while True:
        lat = _opensky_home["lat"]
        lon = _opensky_home["lon"]
        raggio_km = _opensky_home["raggio_km"]

        if lat is None or lon is None or raggio_km is None:
            time.sleep(2)
            continue

        try:
            lamin, lomin, lamax, lomax = _calcola_bounding_box(lat, lon, raggio_km)
            url = (f"{OPENSKY_API_URL}?lamin={lamin}&lomin={lomin}"
                   f"&lamax={lamax}&lomax={lomax}")

            intestazioni = {"User-Agent": "SkyTruth/1.0"}
            token = _ottieni_token_opensky()
            if token:
                intestazioni["Authorization"] = f"Bearer {token}"

            richiesta = urllib.request.Request(url, headers=intestazioni)
            with urllib.request.urlopen(richiesta, timeout=10) as risposta:
                dati_grezzi = json.loads(risposta.read().decode("utf-8"))

            nuovi_aerei = []
            for stato in (dati_grezzi.get("states") or []):
                try:
                    icao = stato[0]
                    callsign = (stato[1] or "").strip()
                    lon_aereo = stato[5]
                    lat_aereo = stato[6]
                    altitudine = stato[7]
                    velocita = stato[9]

                    if lat_aereo is None or lon_aereo is None:
                        continue

                    distanza = _calcola_distanza_km(lat, lon, lat_aereo, lon_aereo)
                    if distanza <= raggio_km:
                        nuovi_aerei.append({
                            "icao": icao,
                            "callsign": callsign or icao,
                            "lat": lat_aereo,
                            "lon": lon_aereo,
                            "altitudine": altitudine,
                            "velocita": velocita,
                            "distanza_km": round(distanza, 1),
                        })
                except (IndexError, TypeError):
                    continue

            with _opensky_lock:
                _opensky_aerei.clear()
                _opensky_aerei.extend(nuovi_aerei)

            # Aggiorniamo lo storico della sessione, stesso principio
            # usato lato ghost: ricordiamo ogni aereo visto durante
            # l'intera sessione, non solo nell'ultimo aggiornamento.
            if nuovi_aerei:
                with _sessione_storico_lock:
                    for aereo in nuovi_aerei:
                        icao = aereo.get("icao")
                        if icao:
                            _sessione_mainstream_storico[icao] = {
                                "icao": icao,
                                "callsign": (aereo.get("callsign") or icao).strip(),
                                "altitudine": aereo.get("altitudine", ""),
                            }

            _opensky_ultimo_errore = None
            intervallo_corrente = OPENSKY_INTERVALLO_AGGIORNAMENTO_SEC
            logging.info("OpenSky: %s aerei nel raggio.", len(nuovi_aerei))

        except urllib.error.HTTPError as e:
            _opensky_ultimo_errore = f"Errore HTTP {e.code} da OpenSky."
            logging.warning(_opensky_ultimo_errore)
            # Se veniamo limitati (troppe richieste), rallentiamo
            intervallo_corrente = min(intervallo_corrente * 2, 300)
        except Exception:
            _opensky_ultimo_errore = "Connessione a OpenSky non disponibile."
            logging.warning("Errore nella lettura dati OpenSky:\n%s",
                             traceback.format_exc())
            intervallo_corrente = min(intervallo_corrente * 2, 300)

        time.sleep(intervallo_corrente)


_PAGINA_HTML_MAPPA_OPENSKY = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>__TITOLO__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html, body, #mappa { height: 100%; margin: 0; padding: 0; background: #111; }
  #pannello {
    position: absolute; top: 8px; left: 8px; z-index: 1000;
    background: rgba(20,20,20,0.85); color: #ffa500; font-family: Consolas, monospace;
    padding: 6px 10px; border-radius: 4px; font-size: 12px;
  }
</style>
</head>
<body>
<div id="pannello">SkyTruth - OpenSky Mainstream (dati pubblici)</div>
<div id="mappa"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
  const mappa = L.map('mappa').setView([__LAT__, __LON__], 8);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: 'OpenStreetMap'
  }).addTo(mappa);

  const iconaCasa = L.divIcon({className: '', html: '<div style="font-size:22px;">📍</div>'});
  L.marker([__LAT__, __LON__], {icon: iconaCasa}).addTo(mappa).bindPopup('Punto di osservazione');

  L.circle([__LAT__, __LON__], {radius: __RAGGIO_METRI__, color: '#ffa500', fill: false}).addTo(mappa);

  let markerAerei = {};

  async function aggiorna() {
    try {
      const risposta = await fetch('/data.json');
      const dati = await risposta.json();

      const icaoPresenti = new Set();

      dati.aerei.forEach(a => {
        icaoPresenti.add(a.icao);
        const etichetta = a.callsign + ' - ' + (a.distanza_km ?? '?') + ' km';

        if (markerAerei[a.icao]) {
          markerAerei[a.icao].setLatLng([a.lat, a.lon]);
          markerAerei[a.icao].setPopupContent(etichetta);
        } else {
          const icona = L.divIcon({className: '', html: '<div style="font-size:18px;">🛩️</div>'});
          markerAerei[a.icao] = L.marker([a.lat, a.lon], {icon: icona})
            .addTo(mappa).bindPopup(etichetta);
        }
      });

      Object.keys(markerAerei).forEach(icao => {
        if (!icaoPresenti.has(icao)) {
          mappa.removeLayer(markerAerei[icao]);
          delete markerAerei[icao];
        }
      });

      document.getElementById('pannello').innerText =
        'SkyTruth - OpenSky Mainstream | aerei nel raggio: ' + dati.aerei.length +
        (dati.errore ? ' | ' + dati.errore : '');
    } catch (e) {
      document.getElementById('pannello').innerText =
        'SkyTruth - OpenSky Mainstream | in attesa di dati...';
    }
  }

  aggiorna();
  setInterval(aggiorna, 5000);
</script>
</body>
</html>
"""


class _GestoreRichiesteMappaOpensky(http.server.BaseHTTPRequestHandler):
    """Web server locale per la mappa OpenSky, analogo a quello della mappa reale."""

    def do_GET(self):
        try:
            if self.path.startswith("/data.json"):
                self._rispondi_json()
            else:
                self._rispondi_pagina_html()
        except Exception:
            logging.error("Errore nel server della mappa OpenSky:\n%s",
                           traceback.format_exc())
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _rispondi_pagina_html(self):
        lat = _opensky_home["lat"] or 0
        lon = _opensky_home["lon"] or 0
        raggio_km = _opensky_home["raggio_km"] or 100

        html = (_PAGINA_HTML_MAPPA_OPENSKY
                .replace("__TITOLO__", TITOLO_FINESTRA_MAPPA_OPENSKY)
                .replace("__LAT__", str(lat))
                .replace("__LON__", str(lon))
                .replace("__RAGGIO_METRI__", str(raggio_km * 1000)))

        corpo = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _rispondi_json(self):
        with _opensky_lock:
            aerei = list(_opensky_aerei)
        dati = {"aerei": aerei, "errore": _opensky_ultimo_errore}
        corpo = json.dumps(dati).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, format, *args):
        logging.debug("Richiesta mappa OpenSky: " + format, *args)


def _thread_web_server_mappa_opensky():
    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", MAPPA_OPENSKY_HTTP_PORT), _GestoreRichiesteMappaOpensky
        )
        server.serve_forever()
    except Exception:
        logging.error("Impossibile avviare il web server della mappa OpenSky:\n%s",
                       traceback.format_exc())


def avvia_mappa_opensky(lat, lon, raggio_km, x, y, larghezza, altezza):
    """
    Avvia (se non gia' attivi) i thread di lettura dati OpenSky e web
    server, poi apre la finestra nativa (pywebview) gia' posizionata
    nelle coordinate indicate.
    """
    global _opensky_server_avviato

    _opensky_home["lat"] = lat
    _opensky_home["lon"] = lon
    _opensky_home["raggio_km"] = raggio_km

    if not _opensky_server_avviato:
        try:
            threading.Thread(target=_thread_lettura_opensky, daemon=True).start()
            threading.Thread(target=_thread_web_server_mappa_opensky, daemon=True).start()
            _opensky_server_avviato = True
            logging.info("Server mappa OpenSky avviato sulla porta %s.",
                         MAPPA_OPENSKY_HTTP_PORT)
        except Exception:
            logging.error("Errore nell'avvio dei servizi della mappa OpenSky:\n%s",
                           traceback.format_exc())
            messagebox.showerror(
                "SkyTruth - Errore mappa OpenSky",
                "Non e' stato possibile avviare la mappa OpenSky.\n"
                f"Dettagli nel log: {LOG_DIR}"
            )
            return

    apri_mappa_in_finestra_nativa(
        f"http://127.0.0.1:{MAPPA_OPENSKY_HTTP_PORT}/",
        TITOLO_FINESTRA_MAPPA_OPENSKY, x, y, larghezza, altezza
    )


# --------------------------------------------------------------------------
# WEBCAM (Fase 5): visualizzazione live del cielo, senza registrazione
# --------------------------------------------------------------------------
_CONTENUTO_SCRIPT_WEBCAM_VIEWER = '''"""
SkyTruth - webcam_viewer.py (generato automaticamente da main.py)
Mostra il flusso live di una webcam in una finestra nativa (OpenCV),
SENZA registrare nulla. Ogni fotogramma viene ridimensionato (con
letterbox, senza deformazioni) per riempire esattamente la finestra,
indipendentemente dall'orientamento nativo della webcam. Il
posizionamento preciso viene corretto dall'esterno (da main.py,
tramite il PID di questo processo).
Uso: webcam_viewer.py <indice_webcam> <titolo> <larghezza> <altezza>
"""
import sys
import os

LOG_PATH = os.path.join(r"%(log_dir)s", "webcam_viewer.log")


def scrivi_log(messaggio):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(messaggio + "\\n")
    except Exception:
        pass


def mostra_errore(titolo, messaggio):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(titolo, messaggio)
    except Exception:
        pass


def ridimensiona_con_letterbox(frame, larghezza_target, altezza_target, np_module):
    """
    Ridimensiona il fotogramma per riempire ESATTAMENTE la finestra
    (larghezza_target x altezza_target), mantenendo pero' le proporzioni
    originali dell'immagine: se non coincidono, aggiunge bande nere
    invece di deformare/schiacciare il video. Funziona correttamente
    qualunque sia l'orientamento nativo della webcam (verticale od
    orizzontale), senza bisogno di sapere in anticipo quale sara'.
    """
    import cv2
    altezza_originale, larghezza_originale = frame.shape[:2]
    if larghezza_originale == 0 or altezza_originale == 0:
        return frame

    scala = min(larghezza_target / larghezza_originale, altezza_target / altezza_originale)
    nuova_larghezza = max(1, int(larghezza_originale * scala))
    nuova_altezza = max(1, int(altezza_originale * scala))

    frame_ridimensionato = cv2.resize(
        frame, (nuova_larghezza, nuova_altezza), interpolation=cv2.INTER_AREA
    )

    tela = np_module.zeros((altezza_target, larghezza_target, 3), dtype=frame.dtype)
    offset_x = (larghezza_target - nuova_larghezza) // 2
    offset_y = (altezza_target - nuova_altezza) // 2
    tela[offset_y:offset_y + nuova_altezza, offset_x:offset_x + nuova_larghezza] = frame_ridimensionato
    return tela


def main():
    try:
        indice = int(sys.argv[1])
        titolo = sys.argv[2]
        larghezza_target = int(sys.argv[3])
        altezza_target = int(sys.argv[4])
    except Exception as e:
        scrivi_log(f"Argomenti non validi: {e}")
        return

    try:
        import cv2
        import numpy as np
    except ImportError:
        messaggio = (
            "La libreria 'opencv-python' non e' installata.\\n"
            "Apri un prompt dei comandi ed esegui:\\n"
            "pip install opencv-python"
        )
        scrivi_log("Libreria 'opencv-python' non installata.")
        mostra_errore("SkyTruth - Libreria mancante", messaggio)
        return

    cap = cv2.VideoCapture(indice, cv2.CAP_DSHOW)
    if not cap.isOpened():
        scrivi_log(f"Impossibile aprire la webcam indice {indice}.")
        mostra_errore("SkyTruth - Webcam",
                       "Impossibile aprire la webcam selezionata.")
        return

    cv2.namedWindow(titolo)

    try:
        while True:
            letto, frame = cap.read()
            if not letto:
                scrivi_log("Flusso webcam interrotto.")
                break

            frame = ridimensiona_con_letterbox(frame, larghezza_target, altezza_target, np)
            cv2.imshow(titolo, frame)

            # ESC da tastiera per chiudere manualmente, se serve
            if cv2.waitKey(1) & 0xFF == 27:
                break

            # Se l'utente chiude la finestra con la X, ce ne accorgiamo qui
            try:
                if cv2.getWindowProperty(titolo, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except Exception:
                break
    except Exception as e:
        scrivi_log(f"Errore durante la visualizzazione webcam: {e}")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
'''


def scrivi_script_webcam_viewer():
    """Genera (o rigenera) lo script 'webcam_viewer.py' ad ogni avvio del programma."""
    try:
        contenuto = _CONTENUTO_SCRIPT_WEBCAM_VIEWER % {"log_dir": LOG_DIR}
        with open(WEBCAM_VIEWER_SCRIPT, "w", encoding="utf-8") as f:
            f.write(contenuto)
    except Exception:
        logging.warning("Impossibile generare lo script webcam_viewer.py:\n%s",
                         traceback.format_exc())


def verifica_opencv_disponibile():
    """
    Controlla se la libreria 'opencv-python' e' installata. Se manca,
    avvisa l'utente una sola volta con le istruzioni per installarla.
    """
    global _avviso_opencv_mostrato

    disponibile = importlib.util.find_spec("cv2") is not None

    if not disponibile and not _avviso_opencv_mostrato:
        _avviso_opencv_mostrato = True
        messagebox.showwarning(
            "SkyTruth - Libreria mancante",
            "La libreria 'opencv-python' non risulta installata.\n"
            "La visualizzazione webcam non funzionera' finche' non viene "
            "installata.\n\n"
            "Apri un prompt dei comandi ed esegui:\n"
            f"{OPENCV_ISTRUZIONI_INSTALLAZIONE}\n\n"
            "Poi riprova."
        )

    return disponibile


def rileva_webcam_con_nomi(max_indici=5):
    """
    Rileva le webcam disponibili E i loro nomi corretti, garantendo che
    l'abbinamento indice<->nome sia esatto. Usiamo 'pygrabber' (libreria
    leggera e open source) per interrogare le periferiche video tramite
    DirectShow: e' lo STESSO meccanismo di enumerazione usato da OpenCV
    su Windows, quindi l'ordine coincide sempre (a differenza di
    PowerShell/Get-PnpDevice, che usa un elenco di sistema con un ordine
    diverso e puo' associare nomi sbagliati agli indici).

    Ritorna una lista di tuple (indice, nome).
    Se 'opencv-python' non e' installato, ritorna una lista vuota.
    Se 'pygrabber' non e' installato, i nomi vengono comunque rilevati,
    ma con etichette generiche ("Webcam 0", "Webcam 1", ...).
    """
    try:
        import cv2
    except ImportError:
        return []

    nomi_dshow = None
    try:
        from pygrabber.dshow_graph import FilterGraph
        nomi_dshow = FilterGraph().get_input_devices()
    except Exception:
        logging.info("'pygrabber' non disponibile: uso nomi generici per le webcam.")

    risultato = []
    for indice in range(max_indici):
        try:
            # cv2.CAP_DSHOW forza lo stesso backend usato da pygrabber per
            # l'enumerazione, cosi' gli indici coincidono esattamente.
            cap = cv2.VideoCapture(indice, cv2.CAP_DSHOW)
            aperta = cap.isOpened()
            cap.release()
        except Exception:
            aperta = False

        if aperta:
            if nomi_dshow and indice < len(nomi_dshow):
                nome = nomi_dshow[indice]
            else:
                nome = f"Webcam {indice}"
            risultato.append((indice, nome))

    return risultato


def avvia_webcam(indice, x, y, larghezza, altezza):
    """
    Avvia lo script 'webcam_viewer.py' come processo separato per l'indice
    di webcam scelto, poi ne corregge la posizione/dimensione con lo
    stesso meccanismo "prima/dopo" usato per dump1090 e per le mappe.
    """
    scrivi_script_webcam_viewer()

    try:
        eseguibile = ottieni_eseguibile_python_senza_console()
        finestre_prima = enumera_finestre_visibili()

        processo = subprocess.Popen([
            eseguibile, WEBCAM_VIEWER_SCRIPT, str(indice), TITOLO_FINESTRA_WEBCAM,
            str(larghezza), str(altezza)
        ])
        logging.info("Finestra webcam avviata (PID %s), indice %s.", processo.pid, indice)
        registra_processo_secondario(processo)
        posiziona_nuova_finestra_in_background(
            finestre_prima, x, y, larghezza, altezza, etichetta=TITOLO_FINESTRA_WEBCAM
        )
    except Exception:
        logging.error("Errore nell'avvio della finestra webcam:\n%s",
                       traceback.format_exc())
        messagebox.showerror(
            "SkyTruth - Errore webcam",
            "Non e' stato possibile avviare la visualizzazione webcam.\n"
            f"Dettagli nel log: {LOG_DIR}"
        )


# --------------------------------------------------------------------------
# REGISTRAZIONE SESSIONE (Fase 6): cattura schermo con FFmpeg
# --------------------------------------------------------------------------
_processo_ffmpeg_registrazione = None
_percorso_video_temp = None


def avvia_registrazione_schermo():
    """
    Avvia la registrazione dell'intero schermo con FFmpeg, salvando un
    file temporaneo nella cartella 'temp'. FFmpeg viene lanciato senza
    finestra visibile; la fermeremo in modo pulito scrivendo 'q' sul suo
    canale di input standard (e' il modo corretto per farlo finalizzare
    il file senza corromperlo).

    Ritorna True se avviata correttamente, False altrimenti (mostra un
    messaggio d'errore appropriato, senza far crashare il programma).
    """
    global _processo_ffmpeg_registrazione, _percorso_video_temp

    if not os.path.isfile(FFMPEG_EXE):
        logging.error("ffmpeg.exe non trovato in: %s", FFMPEG_EXE)
        messagebox.showerror(
            "SkyTruth - FFmpeg non presente",
            "ffmpeg.exe non e' stato trovato nella cartella:\n"
            f"{FFMPEG_DIR}\n\n"
            "Scarica una build ufficiale gratuita per Windows da:\n"
            f"{FFMPEG_DOWNLOAD_URL}\n\n"
            f"Estrai ffmpeg.exe dentro:\n{FFMPEG_DIR}\n\n"
            "Poi riprova."
        )
        return False

    nome_file_temp = datetime.now().strftime("sessione_%Y-%m-%d_%H-%M-%S_temp.mp4")
    _percorso_video_temp = os.path.join(TEMP_DIR, nome_file_temp)

    comando = [
        FFMPEG_EXE, "-y",
        "-f", "gdigrab", "-framerate", "15", "-i", "desktop",
        "-vcodec", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        _percorso_video_temp
    ]

    try:
        flag_no_finestra = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _processo_ffmpeg_registrazione = subprocess.Popen(
            comando, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flag_no_finestra
        )
        registra_processo_secondario(_processo_ffmpeg_registrazione)
        logging.info("Registrazione schermo avviata: %s", _percorso_video_temp)
        return True
    except Exception:
        logging.error("Errore nell'avvio della registrazione:\n%s",
                       traceback.format_exc())
        messagebox.showerror(
            "SkyTruth - Errore registrazione",
            "Non e' stato possibile avviare la registrazione.\n"
            f"Dettagli nel log: {LOG_DIR}"
        )
        return False


def ferma_registrazione_schermo_e_salva():
    """
    Ferma la registrazione (se attiva) in modo pulito, converte il file
    temporaneo in un formato compresso SENZA metadati (con una semplice
    copia dei flussi, quindi velocissima), lo sposta nella cartella
    'sessioni_video', poi svuota completamente la cartella 'temp'.
    """
    global _processo_ffmpeg_registrazione, _percorso_video_temp

    if _processo_ffmpeg_registrazione is not None:
        try:
            if _processo_ffmpeg_registrazione.stdin:
                _processo_ffmpeg_registrazione.stdin.write(b"q\n")
                _processo_ffmpeg_registrazione.stdin.flush()
            _processo_ffmpeg_registrazione.wait(timeout=15)
        except Exception:
            logging.warning("Chiusura non pulita di FFmpeg, termino forzatamente:\n%s",
                             traceback.format_exc())
            try:
                _processo_ffmpeg_registrazione.terminate()
            except Exception:
                pass

    percorso_temp = _percorso_video_temp
    _processo_ffmpeg_registrazione = None
    _percorso_video_temp = None

    if not percorso_temp or not os.path.isfile(percorso_temp):
        logging.warning("Nessun file di registrazione temporaneo trovato da salvare.")
        pulisci_cartella_temp()
        return None

    nome_finale = os.path.basename(percorso_temp).replace("_temp.mp4", ".mp4")
    percorso_finale = os.path.join(SESSIONI_DIR, nome_finale)

    try:
        comando_conversione = [
            FFMPEG_EXE, "-y", "-i", percorso_temp,
            "-map_metadata", "-1", "-c", "copy", percorso_finale
        ]
        flag_no_finestra = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(comando_conversione, capture_output=True,
                        creationflags=flag_no_finestra, timeout=120)
        logging.info("Sessione video salvata in: %s", percorso_finale)
    except Exception:
        logging.error("Errore nella conversione finale del video:\n%s",
                       traceback.format_exc())
        percorso_finale = None

    pulisci_cartella_temp()
    return percorso_finale


def pulisci_cartella_temp():
    """
    Svuota completamente la cartella 'temp', cosi' non restano mai file
    residui tra una sessione e l'altra. La cartella stessa non viene
    eliminata, solo il suo contenuto.
    """
    if not os.path.isdir(TEMP_DIR):
        return

    for nome_file in os.listdir(TEMP_DIR):
        percorso = os.path.join(TEMP_DIR, nome_file)
        try:
            if os.path.isfile(percorso) or os.path.islink(percorso):
                os.remove(percorso)
            elif os.path.isdir(percorso):
                shutil.rmtree(percorso, ignore_errors=True)
        except Exception:
            logging.warning("Impossibile rimuovere '%s':\n%s",
                             percorso, traceback.format_exc())

    logging.info("Cartella temp ripulita.")


# --------------------------------------------------------------------------
# SALVATAGGIO RILEVAZIONI IN FILE TXT
# --------------------------------------------------------------------------
# A differenza del file JSON che alcune versioni di dump1090 sovrascrivono
# di continuo, noi leggiamo gia' i dati direttamente dal flusso SBS in
# tempo reale (vedi _elabora_riga_sbs), quindi possiamo scrivere una riga
# nel file txt ogni volta che un aereo ha TUTTI i dati richiesti, senza
# bisogno di rileggere nessuno snapshot esterno.
INTESTAZIONE_TXT_RILEVAZIONI = (
    "timestamp;icao;callsign;altitudine_ft;velocita_kt;rotta_gradi;lat;lon;distanza_km\n"
)
CAMPI_RICHIESTI_RIGA_TXT = ("icao", "callsign", "altitudine", "velocita", "rotta", "lat", "lon")

_lock_registrazione_txt = threading.Lock()
_file_txt_attivo = None       # oggetto file aperto, oppure None se non attiva
_percorso_txt_temp = None


def avvia_salvataggio_txt():
    """
    Crea il file temporaneo (con riga di intestazione) dentro 'temp\\' e
    lo tiene aperto per le scritture successive. Ritorna True se avviato
    correttamente.
    """
    global _file_txt_attivo, _percorso_txt_temp

    with _lock_registrazione_txt:
        if _file_txt_attivo is not None:
            return True  # gia' attiva

        nome_file = datetime.now().strftime("rilevazioni_%Y-%m-%d_%H-%M-%S_temp.txt")
        _percorso_txt_temp = os.path.join(TEMP_DIR, nome_file)

        try:
            _file_txt_attivo = open(_percorso_txt_temp, "w", encoding="utf-8")
            _file_txt_attivo.write(INTESTAZIONE_TXT_RILEVAZIONI)
            _file_txt_attivo.flush()
            logging.info("Salvataggio rilevazioni in txt avviato: %s", _percorso_txt_temp)
            return True
        except Exception:
            logging.error("Errore nell'avvio del salvataggio txt:\n%s",
                           traceback.format_exc())
            _file_txt_attivo = None
            return False


def _scrivi_riga_txt_se_completa(aereo):
    """
    Chiamata dal thread di lettura SBS ogni volta che un aereo riceve un
    aggiornamento di posizione. Se il salvataggio txt e' attivo E
    l'aereo ha ormai tutti i campi richiesti (niente dati parziali o
    falsi positivi), scrive una riga nel file.
    """
    if _file_txt_attivo is None:
        return

    for campo in CAMPI_RICHIESTI_RIGA_TXT:
        valore = aereo.get(campo)
        if valore is None or valore == "":
            return  # dati ancora incompleti, non scriviamo nulla

    riga = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')};"
        f"{aereo['icao']};{aereo['callsign']};{aereo['altitudine']};"
        f"{aereo['velocita']};{aereo['rotta']};{aereo['lat']};{aereo['lon']};"
        f"{aereo.get('distanza_km', '')}\n"
    )

    with _lock_registrazione_txt:
        if _file_txt_attivo is None:
            return
        try:
            _file_txt_attivo.write(riga)
            _file_txt_attivo.flush()
        except Exception:
            logging.warning("Errore nella scrittura della riga txt:\n%s",
                             traceback.format_exc())


def ferma_salvataggio_txt_e_salva():
    """
    Chiude il file temporaneo e lo sposta nella cartella 'sessioni_txt',
    con lo stesso schema di nomi (data/ora) usato per le sessioni video.
    Ritorna il percorso finale se tutto e' andato bene, altrimenti None.
    """
    global _file_txt_attivo, _percorso_txt_temp

    with _lock_registrazione_txt:
        if _file_txt_attivo is None:
            return None

        try:
            _file_txt_attivo.close()
        except Exception:
            logging.warning("Errore nella chiusura del file txt:\n%s",
                             traceback.format_exc())

        percorso_temp = _percorso_txt_temp
        _file_txt_attivo = None
        _percorso_txt_temp = None

    if not percorso_temp or not os.path.isfile(percorso_temp):
        logging.warning("Nessun file di rilevazioni temporaneo trovato da salvare.")
        return None

    try:
        os.makedirs(SESSIONI_TXT_DIR, exist_ok=True)
        nome_finale = os.path.basename(percorso_temp).replace("_temp.txt", ".txt")
        percorso_finale = os.path.join(SESSIONI_TXT_DIR, nome_finale)
        shutil.move(percorso_temp, percorso_finale)
        logging.info("Sessione rilevazioni txt salvata in: %s", percorso_finale)
        return percorso_finale
    except Exception:
        logging.error("Errore nello spostamento del file txt finale:\n%s",
                       traceback.format_exc())
        return None


# --------------------------------------------------------------------------
# SITO WEB PUBBLICO: cattura snapshot e caricamento sessione su GitHub
# --------------------------------------------------------------------------
def cattura_snapshot_webcam():
    """
    Cattura uno screenshot del SOLO quadrante basso-destra (dove si trova
    la finestra webcam), lo salva in 'snapshots\\' con nome basato su
    data/ora. Ritorna il percorso del file salvato, oppure None in caso
    di errore.

    Usiamo Pillow (ImageGrab) invece di arrangiarci con le sole API di
    Windows: e' il modo piu' semplice ed affidabile per catturare una
    porzione precisa dello schermo.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        logging.error("Libreria 'Pillow' non installata.")
        messagebox.showerror(
            "SkyTruth - Libreria mancante",
            "La libreria 'Pillow' non e' installata.\n"
            "Apri un prompt dei comandi ed esegui:\n"
            "pip install Pillow"
        )
        return None

    try:
        x, y, larghezza, altezza = calcola_quadranti()["basso_dx"]
        immagine = ImageGrab.grab(bbox=(x, y, x + larghezza, y + altezza))

        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        nome_file = datetime.now().strftime("snapshot_%Y-%m-%d_%H-%M-%S.png")
        percorso = os.path.join(SNAPSHOTS_DIR, nome_file)
        immagine.save(percorso)

        logging.info("Snapshot catturato: %s", percorso)
        return percorso
    except Exception:
        logging.error("Errore nella cattura dello snapshot:\n%s",
                       traceback.format_exc())
        messagebox.showerror(
            "SkyTruth - Errore snapshot",
            "Non e' stato possibile catturare lo snapshot.\n"
            f"Dettagli nel log: {LOG_DIR}"
        )
        return None


def _richiesta_github(url, token, metodo="GET", corpo_dict=None):
    """Esegue una richiesta HTTP verso le API di GitHub, con autenticazione."""
    dati = json.dumps(corpo_dict).encode("utf-8") if corpo_dict is not None else None
    richiesta = urllib.request.Request(
        url, data=dati, method=metodo,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "SkyTruth-App",
        }
    )
    with urllib.request.urlopen(richiesta, timeout=20) as risposta:
        return json.loads(risposta.read().decode("utf-8"))


def _leggi_file_repo_sito(nome_utente, token, percorso_file):
    """
    Legge un file dal repository del sito. Ritorna (contenuto_testo, sha)
    se il file esiste, oppure (None, None) se non esiste ancora.
    """
    url = f"{GITHUB_API_BASE}/repos/{nome_utente}/{NOME_REPO_SITO}/contents/{percorso_file}"
    try:
        risultato = _richiesta_github(url, token)
        contenuto = base64.b64decode(risultato["content"]).decode("utf-8")
        return contenuto, risultato["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise


def _scrivi_file_repo_sito(nome_utente, token, percorso_file, contenuto_bytes,
                             messaggio_commit, sha_esistente=None):
    """Crea o aggiorna un file nel repository del sito."""
    url = f"{GITHUB_API_BASE}/repos/{nome_utente}/{NOME_REPO_SITO}/contents/{percorso_file}"
    corpo = {
        "message": messaggio_commit,
        "content": base64.b64encode(contenuto_bytes).decode("utf-8"),
    }
    if sha_esistente:
        corpo["sha"] = sha_esistente
    return _richiesta_github(url, token, metodo="PUT", corpo_dict=corpo)


def carica_sessione_su_github(nome_utente, token, percorso_snapshot,
                                orario_inizio, orario_fine,
                                lista_ghost, lista_mainstream):
    """
    Carica una nuova sessione sul sito pubblico (repository
    '<nome_utente>/SkyTruth-Windows-Site'):
      1. Legge 'sessions.json' esistente e calcola il prossimo id libero
      2. Carica l'immagine snapshot dentro 'snapshots/'
      3. Aggiunge la nuova sessione a 'sessions.json' e lo salva
      4. Aggiorna il campo 'nomeUtenteGitHub' dentro 'config.js'

    'lista_ghost' e 'lista_mainstream' sono liste di dict
    {"icao": ..., "callsign": ...} con gli aerei rilevati da ciascuna
    fonte; i conteggi mostrati sul sito vengono calcolati da queste
    liste, che vengono salvate per intero (servono anche per il
    confronto "aerei in comune / solo ghost / solo mainstream").

    Ritorna una tupla (successo: bool, messaggio: str).
    """
    try:
        contenuto_json, sha_json = _leggi_file_repo_sito(nome_utente, token, "sessions.json")
        sessioni = json.loads(contenuto_json) if contenuto_json else []

        nuovo_id = max((s.get("id", 0) for s in sessioni), default=0) + 1
        nome_file_snapshot = f"snapshot_{nuovo_id}.png"

        nuova_sessione = {
            "id": nuovo_id,
            "titolo": f"Sessione {nuovo_id}",
            "data_inizio": orario_inizio.strftime("%Y-%m-%dT%H:%M:%S"),
            "data_fine": orario_fine.strftime("%Y-%m-%dT%H:%M:%S"),
            "aerei_ghost": len(lista_ghost),
            "aerei_mainstream": len(lista_mainstream),
            "aerei_ghost_lista": lista_ghost,
            "aerei_mainstream_lista": lista_mainstream,
            "snapshot": f"snapshots/{nome_file_snapshot}",
        }
        sessioni.append(nuova_sessione)

        with open(percorso_snapshot, "rb") as f:
            dati_immagine = f.read()
        _scrivi_file_repo_sito(
            nome_utente, token, f"snapshots/{nome_file_snapshot}", dati_immagine,
            f"Aggiunto snapshot sessione {nuovo_id}"
        )

        nuovo_contenuto_json = json.dumps(sessioni, indent=2, ensure_ascii=False).encode("utf-8")
        _scrivi_file_repo_sito(
            nome_utente, token, "sessions.json", nuovo_contenuto_json,
            f"Aggiunta sessione {nuovo_id}", sha_esistente=sha_json
        )

        # Aggiorniamo anche il nome utente dentro config.js, cosi' il sito
        # mostra sempre il titolo/link corretti senza bisogno di modifiche
        # manuali da parte dell'utente.
        contenuto_config, sha_config = _leggi_file_repo_sito(nome_utente, token, "config.js")
        if contenuto_config:
            nuovo_config = re.sub(
                r'nomeUtenteGitHub:\s*"[^"]*"',
                f'nomeUtenteGitHub: "{nome_utente}"',
                contenuto_config
            )
            if nuovo_config != contenuto_config:
                _scrivi_file_repo_sito(
                    nome_utente, token, "config.js", nuovo_config.encode("utf-8"),
                    "Aggiornato nome utente GitHub", sha_esistente=sha_config
                )

        logging.info("Sessione %s caricata correttamente sul sito.", nuovo_id)
        return True, f"Sessione {nuovo_id} caricata correttamente sul sito."

    except urllib.error.HTTPError as e:
        logging.error("Errore HTTP durante il caricamento sul sito: %s %s", e.code, e.reason)
        if e.code == 401:
            return False, "Token non valido o scaduto. Controlla di averlo copiato correttamente."
        if e.code == 404:
            return False, (
                f"Repository '{nome_utente}/{NOME_REPO_SITO}' non trovato.\n"
                "Verifica il nome utente e che il repository del sito esista."
            )
        return False, f"Errore GitHub (HTTP {e.code}): {e.reason}"
    except Exception as e:
        logging.error("Errore durante il caricamento sul sito:\n%s", traceback.format_exc())
        return False, f"Errore imprevisto: {e}"


# --------------------------------------------------------------------------
# FINESTRA MENU PRINCIPALE
# --------------------------------------------------------------------------
class MenuApp:
    """
    Gestisce la finestra del menu principale (riquadro sinistro, meta' alta
    dello schermo). In questa Fase 1 gestisce solo:
    - visualizzazione stato dongle
    - inserimento dati (latitudine, longitudine, raggio km)
    """

    def __init__(self, root):
        self.root = root
        self.root.title("SkyTruth - Menu")

        # Dati che l'utente inserira'. Verranno usati nelle fasi successive
        # da dump1090, dalla mappa reale e dalla mappa OpenSky.
        self.latitudine = None
        self.longitudine = None
        self.raggio_km = None
        self._registrazione_attiva = False
        self._registrazione_txt_attiva = False
        self._webcam_attiva = False
        self._ultimo_snapshot = None
        self._orario_inizio_sessione = None
        self._opensky_credenziali_richieste = False

        self._posiziona_finestra()
        self._costruisci_interfaccia()

        # Anche chiudendo la finestra con la X, vogliamo che tutto venga
        # chiuso ordinatamente (stessa procedura del bottone "Esci").
        self.root.protocol("WM_DELETE_WINDOW", self._esci_dal_programma)

    def _posiziona_finestra(self):
        """
        Posiziona la finestra nel quadrante alto-sinistra dell'area di
        lavoro reale. Usiamo MoveWindow (lo stesso meccanismo a basso
        livello usato per tutte le altre finestre del programma) invece
        di affidarci a root.geometry(), perche' Tkinter puo' interpretare
        le coordinate secondo una propria scala interna non sempre
        coerente con i pixel fisici reali dello schermo.
        """
        try:
            x, y, larghezza, altezza = calcola_quadranti()["alto_sx"]

            # Diamo comunque una dimensione di partenza ragionevole,
            # nel caso MoveWindow impiegasse un istante ad applicarsi.
            self.root.geometry(f"{larghezza}x{altezza}+{x}+{y}")
            self.root.update_idletasks()

            # winfo_id() su Windows ritorna l'handle di una finestra interna
            # di disegno; il vero handle della finestra principale (quello
            # con la barra del titolo) e' il suo "genitore" diretto.
            hwnd_interno = self.root.winfo_id()
            hwnd_principale = ctypes.windll.user32.GetParent(hwnd_interno)
            if not hwnd_principale:
                hwnd_principale = hwnd_interno

            ctypes.windll.user32.MoveWindow(hwnd_principale, x, y, larghezza, altezza, True)
        except Exception:
            logging.warning("Impossibile posizionare la finestra automaticamente:\n%s",
                             traceback.format_exc())
            self.root.geometry("600x400")

    def _costruisci_interfaccia(self):
        """Crea i widget iniziali del menu: titolo, autori, stato dongle."""

        tk.Label(
            self.root,
            text=f"SkyTruth {VERSIONE_PROGRAMMA} per Windows  —  by Professor Grandi (Mahatma) & Claude Sonnet 5",
            font=FONT_MENU_TITOLO
        ).pack(pady=(8, 8))

        # --- Stato dongle ---
        self.dongle_trovato = rileva_dongle()

        if self.dongle_trovato:
            testo_dongle = f"Periferica rilevata: {self.dongle_trovato}"
            colore_dongle = "green"
        else:
            testo_dongle = (
                "Nessuna periferica dongle rilevata.\n"
                "Collegare una periferica, installare i driver,\n"
                "riavviare il programma."
            )
            colore_dongle = "red"

        tk.Label(
            self.root, text=testo_dongle, fg=colore_dongle,
            font=FONT_MENU, justify="center"
        ).pack(pady=(0, 10))

        # Se non c'e' nessun dongle, non ha senso proseguire con
        # l'inserimento dati: mostriamo solo lo stato ed usciamo qui.
        if not self.dongle_trovato:
            return

        # --- Sezione inserimento dati ---
        self._chiedi_latitudine()

    def _chiedi_latitudine(self):
        riga = tk.Frame(self.root)
        riga.pack(pady=(4, 4))

        tk.Label(
            riga, text="Inserire latitudine (es. 40.8358) e premere Invio:",
            font=FONT_MENU
        ).pack(side="left", padx=(0, 6))

        self.entry_lat = tk.Entry(riga, font=FONT_MENU, justify="center", width=12)
        self.entry_lat.pack(side="left")
        self.entry_lat.focus()
        self.entry_lat.bind("<Return>", self._conferma_latitudine)

    def _conferma_latitudine(self, event=None):
        if self.latitudine is not None:
            return
        valore = self.entry_lat.get().strip().replace(",", ".")
        try:
            valore_float = float(valore)
            if not (-90 <= valore_float <= 90):
                raise ValueError("Latitudine fuori range")
            self.latitudine = valore_float
            self.entry_lat.config(state="disabled")
            logging.info("Latitudine impostata: %s", self.latitudine)
            self._chiedi_longitudine()
        except ValueError:
            messagebox.showerror(
                "Valore non valido",
                "Inserire una latitudine valida, compresa tra -90 e 90.\n"
                "Esempio: 40.8358"
            )

    def _chiedi_longitudine(self):
        riga = tk.Frame(self.root)
        riga.pack(pady=(4, 4))

        tk.Label(
            riga, text="Inserire longitudine (es. 14.6098) e premere Invio:",
            font=FONT_MENU
        ).pack(side="left", padx=(0, 6))

        self.entry_lon = tk.Entry(riga, font=FONT_MENU, justify="center", width=12)
        self.entry_lon.pack(side="left")
        self.entry_lon.focus()
        self.entry_lon.bind("<Return>", self._conferma_longitudine)

    def _conferma_longitudine(self, event=None):
        if self.longitudine is not None:
            return
        valore = self.entry_lon.get().strip().replace(",", ".")
        try:
            valore_float = float(valore)
            if not (-180 <= valore_float <= 180):
                raise ValueError("Longitudine fuori range")
            self.longitudine = valore_float
            self.entry_lon.config(state="disabled")
            logging.info("Longitudine impostata: %s", self.longitudine)
            self._chiedi_raggio()
        except ValueError:
            messagebox.showerror(
                "Valore non valido",
                "Inserire una longitudine valida, compresa tra -180 e 180.\n"
                "Esempio: 14.6098"
            )

    def _chiedi_raggio(self):
        riga = tk.Frame(self.root)
        riga.pack(pady=(4, 4))

        tk.Label(
            riga, text="Inserire la circonferenza di rilevamento in km (es. 250) e premere Invio:",
            font=FONT_MENU
        ).pack(side="left", padx=(0, 6))

        self.entry_raggio = tk.Entry(riga, font=FONT_MENU, justify="center", width=8)
        self.entry_raggio.pack(side="left")
        self.entry_raggio.focus()
        self.entry_raggio.bind("<Return>", self._conferma_raggio)

    def _conferma_raggio(self, event=None):
        # Protezione anti-doppio-invio: se il raggio e' gia' stato
        # confermato in precedenza, ignoriamo eventuali eventi ripetuti
        # (es. doppio Invio o doppio trigger del binding).
        if self.raggio_km is not None:
            return

        valore = self.entry_raggio.get().strip().replace(",", ".")
        try:
            valore_float = float(valore)
            if valore_float <= 0:
                raise ValueError("Raggio deve essere positivo")
            self.raggio_km = valore_float
            self.entry_raggio.config(state="disabled")
            logging.info("Raggio impostato: %s km", self.raggio_km)
            self._dati_completati()
        except ValueError:
            messagebox.showerror(
                "Valore non valido",
                "Inserire un numero di km valido, maggiore di zero.\n"
                "Esempio: 250"
            )

    def _dati_completati(self):
        """
        A questo punto abbiamo tutti i dati necessari (dongle + posizione
        + raggio). Avviamo dump1090 minimizzato nella barra delle
        applicazioni (il suo posto nel layout a 4 quadranti e' occupato
        dalla mappa grafica "ghost", ma l'utente puo' comunque riaprire
        dump1090 dalla taskbar per controllare i dati in arrivo).
        """
        tk.Label(
            self.root,
            text="Dati acquisiti correttamente.\n\n"
                 "Avvio di dump1090 in corso...",
            font=FONT_MENU_BOLD, fg="blue", justify="center"
        ).pack(pady=(8, 0))

        logging.info(
            "Configurazione completata - lat: %s, lon: %s, raggio: %s km",
            self.latitudine, self.longitudine, self.raggio_km
        )

        # Impostiamo subito il punto di osservazione (anche prima di aprire
        # la mappa ghost): serve al calcolo automatico della distanza per
        # ogni aereo, usato anche dal salvataggio delle rilevazioni in txt.
        _mappa_reale_home["lat"] = self.latitudine
        _mappa_reale_home["lon"] = self.longitudine
        _mappa_reale_home["raggio_km"] = self.raggio_km

        # Segniamo l'inizio della sessione: usato come "data_inizio" se
        # in seguito la sessione viene caricata sul sito pubblico.
        self._orario_inizio_sessione = datetime.now()

        # Nuova sessione: ripartiamo con uno storico aerei pulito.
        with _sessione_storico_lock:
            _sessione_ghost_storico.clear()
            _sessione_mainstream_storico.clear()

        avviato = avvia_dump1090()

        if avviato:
            tk.Label(
                self.root,
                text="dump1090 avviato (minimizzato in taskbar).",
                font=FONT_MENU_BOLD, fg="green", justify="center"
            ).pack(pady=(3, 10))

            # --- Le due mappe grafiche, affiancate sulla stessa riga ---
            riga_mappe = tk.Frame(self.root)
            riga_mappe.pack(pady=(0, 10))

            tk.Button(
                riga_mappe,
                text="Visualizza gli aerei ghost sulla piantina grafica",
                font=FONT_MENU,
                command=self._apri_mappa_reale
            ).pack(side="left", padx=10)

            tk.Button(
                riga_mappe,
                text="Visualizza gli aerei mainstream sulla piantina grafica",
                font=FONT_MENU,
                command=self._apri_mappa_opensky
            ).pack(side="left", padx=10)

            # --- Webcam ---
            self._pulsante_webcam = tk.Button(
                self.root,
                text="Scegli la webcam da utilizzare (attendere qualche minuto per la rilevazione)",
                font=FONT_MENU,
                command=self._scegli_webcam
            )
            self._pulsante_webcam.pack(pady=(0, 10))

            # --- Registrazione ed uscita, affiancate sulla stessa riga ---
            riga_finale = tk.Frame(self.root)
            riga_finale.pack(pady=(0, 8))

            self._pulsante_txt = tk.Button(
                riga_finale,
                text="Avvia salvataggio rilevazioni in file txt",
                font=FONT_MENU,
                command=self._toggla_salvataggio_txt
            )
            self._pulsante_txt.pack(side="left", padx=10)

            self._pulsante_registrazione = tk.Button(
                riga_finale,
                text="Avvia registrazione video della sessione",
                font=FONT_MENU,
                command=self._toggla_registrazione
            )
            self._pulsante_registrazione.pack(side="left", padx=10)

            tk.Button(
                riga_finale,
                text="Esci",
                font=FONT_MENU_BOLD, fg="darkred",
                command=self._esci_dal_programma
            ).pack(side="left", padx=10)

            # --- Sito web pubblico: snapshot e caricamento sessione ---
            riga_sito = tk.Frame(self.root)
            riga_sito.pack(pady=(0, 8))

            tk.Button(
                riga_sito,
                text="Cattura snapshot per il sito",
                font=FONT_MENU,
                command=self._cattura_snapshot
            ).pack(side="left", padx=10)

            tk.Button(
                riga_sito,
                text="Carica sessione sul sito",
                font=FONT_MENU,
                command=self._apri_popup_carica_sito
            ).pack(side="left", padx=10)

            self._crea_sezione_donazioni()

    def _cattura_snapshot(self):
        """
        Cattura uno snapshot della sola finestra webcam (quadrante
        basso-destra) e lo tiene pronto per un eventuale caricamento
        sul sito pubblico.
        """
        if not self._webcam_attiva:
            messagebox.showwarning(
                "SkyTruth - Snapshot",
                "Devi prima avviare la visualizzazione della webcam."
            )
            return

        percorso = cattura_snapshot_webcam()
        if percorso:
            self._ultimo_snapshot = percorso
            messagebox.showinfo(
                "SkyTruth - Snapshot",
                f"Snapshot catturato correttamente:\n{percorso}"
            )

    def _apri_popup_carica_sito(self):
        """
        Apre una finestra dedicata per inserire nome utente GitHub e
        token, poi carica l'ultimo snapshot e i dati della sessione
        corrente sul sito pubblico (in un thread separato, per non
        bloccare l'interfaccia durante la comunicazione di rete).
        """
        if not self._ultimo_snapshot:
            messagebox.showwarning(
                "SkyTruth - Carica sessione",
                "Devi prima catturare uno snapshot con l'apposito bottone."
            )
            return

        finestra = tk.Toplevel(self.root)
        finestra.title("SkyTruth - Carica sessione sul sito")
        finestra.grab_set()

        tk.Label(
            finestra, text="Nome utente GitHub:", font=FONT_MENU
        ).pack(padx=24, pady=(20, 4))
        campo_utente = tk.Entry(finestra, font=FONT_MENU, width=32)
        campo_utente.pack(padx=24)
        campo_utente.focus()

        tk.Label(
            finestra, text="Token GitHub:", font=FONT_MENU
        ).pack(padx=24, pady=(14, 4))
        campo_token = tk.Entry(finestra, font=FONT_MENU, width=32, show="*")
        campo_token.pack(padx=24)

        etichetta_stato = tk.Label(finestra, text="", font=FONT_MENU, fg="orange")
        etichetta_stato.pack(pady=(12, 0))

        def _invia():
            nome_utente = campo_utente.get().strip()
            token = campo_token.get().strip()

            if not nome_utente or not token:
                messagebox.showerror(
                    "SkyTruth - Carica sessione",
                    "Inserisci sia il nome utente GitHub che il token."
                )
                return

            bottone_invia.config(state="disabled")
            campo_utente.config(state="disabled")
            campo_token.config(state="disabled")
            etichetta_stato.config(text="Caricamento in corso, attendere...")

            def _lavoro():
                # Usiamo lo storico dell'intera sessione (non solo
                # l'istante attuale): cosi' la sessione caricata riflette
                # davvero tutti gli aerei visti dall'inizio, anche se in
                # questo preciso momento il cielo fosse vuoto.
                with _sessione_storico_lock:
                    lista_ghost = list(_sessione_ghost_storico.values())
                    lista_mainstream = list(_sessione_mainstream_storico.values())

                orario_inizio = self._orario_inizio_sessione or datetime.now()

                ok, messaggio = carica_sessione_su_github(
                    nome_utente, token, self._ultimo_snapshot,
                    orario_inizio, datetime.now(),
                    lista_ghost, lista_mainstream
                )
                self.root.after(0, lambda: _fine_caricamento(ok, messaggio))

            threading.Thread(target=_lavoro, daemon=True).start()

        def _fine_caricamento(ok, messaggio):
            finestra.destroy()
            if ok:
                messagebox.showinfo("SkyTruth - Carica sessione", messaggio)
            else:
                messagebox.showerror("SkyTruth - Errore caricamento", messaggio)

        bottone_invia = tk.Button(
            finestra, text="Invia", font=FONT_MENU_BOLD, command=_invia
        )
        bottone_invia.pack(pady=18)

        finestra.update_idletasks()
        x_root = self.root.winfo_rootx()
        y_root = self.root.winfo_rooty()
        larghezza_root = self.root.winfo_width()
        altezza_root = self.root.winfo_height()
        larghezza_fin = finestra.winfo_width()
        altezza_fin = finestra.winfo_height()
        x_fin = x_root + (larghezza_root - larghezza_fin) // 2
        y_fin = y_root + (altezza_root - altezza_fin) // 2
        finestra.geometry(f"+{max(x_fin, 0)}+{max(y_fin, 0)}")

    def _crea_sezione_donazioni(self):
        """
        Sezione per donazioni volontarie in Monero (XMR): titolo su una
        riga, sotto la casella con l'indirizzo completo (in sola lettura
        ma copiabile) affiancata da un bottone "Copia" rapido.
        """
        tk.Label(
            self.root, text="Donazioni XMR Monero:",
            font=("Consolas", 14, "bold")
        ).pack(pady=(6, 3))

        riga_donazioni = tk.Frame(self.root)
        riga_donazioni.pack(pady=(0, 8))

        campo_indirizzo = tk.Entry(
            riga_donazioni, font=("Consolas", 13), width=90, justify="center"
        )
        campo_indirizzo.insert(
            0,
            "45QW5EeHKzjFhwhhjB6byJdSXd7ZUR4FgELHGz1e4Mt5M3Yt6TSiXzaEHdcEeneWVX3FtCpdvt4toge3aqCvrihf38SSVGv"
        )
        campo_indirizzo.config(state="readonly", readonlybackground="white")
        campo_indirizzo.pack(side="left", padx=(0, 6))

        tk.Button(
            riga_donazioni, text="Copia", font=FONT_MENU,
            command=lambda: self._copia_negli_appunti(campo_indirizzo)
        ).pack(side="left")

    def _copia_negli_appunti(self, campo_entry):
        """Copia il contenuto del campo indicato negli appunti di Windows."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(campo_entry.get())
            self.root.update()
        except Exception:
            logging.warning("Impossibile copiare negli appunti:\n%s", traceback.format_exc())

    def _toggla_registrazione(self):
        """
        Avvia o ferma la registrazione dell'intero schermo, a seconda
        dello stato attuale. Aggiorna il testo del bottone di conseguenza.
        """
        if not self._registrazione_attiva:
            avviata = avvia_registrazione_schermo()
            if avviata:
                self._registrazione_attiva = True
                self._pulsante_registrazione.config(
                    text="Ferma registrazione video della sessione", fg="red"
                )
        else:
            self._pulsante_registrazione.config(text="Salvataggio in corso...", state="disabled")
            self.root.update_idletasks()

            percorso_finale = ferma_registrazione_schermo_e_salva()

            self._registrazione_attiva = False
            self._pulsante_registrazione.config(
                text="Avvia registrazione video della sessione", fg="black", state="normal"
            )

            if percorso_finale:
                messagebox.showinfo(
                    "SkyTruth - Registrazione salvata",
                    f"Sessione video salvata in:\n{percorso_finale}"
                )
            else:
                messagebox.showwarning(
                    "SkyTruth - Registrazione",
                    "Non e' stato possibile salvare la sessione video.\n"
                    f"Controlla il log in:\n{LOG_DIR}"
                )

    def _toggla_salvataggio_txt(self):
        """
        Avvia o ferma il salvataggio delle rilevazioni dell'aereo in un
        file txt, a seconda dello stato attuale.
        """
        if not self._registrazione_txt_attiva:
            avviato = avvia_salvataggio_txt()
            if avviato:
                self._registrazione_txt_attiva = True
                self._pulsante_txt.config(
                    text="Termina salvataggio rilevazioni in file txt", fg="red"
                )
        else:
            self._pulsante_txt.config(text="Salvataggio in corso...", state="disabled")
            self.root.update_idletasks()

            percorso_finale = ferma_salvataggio_txt_e_salva()

            self._registrazione_txt_attiva = False
            self._pulsante_txt.config(
                text="Avvia salvataggio rilevazioni in file txt", fg="black", state="normal"
            )

            if percorso_finale:
                messagebox.showinfo(
                    "SkyTruth - Rilevazioni salvate",
                    f"File delle rilevazioni salvato in:\n{percorso_finale}"
                )
            else:
                messagebox.showwarning(
                    "SkyTruth - Rilevazioni",
                    "Non e' stato possibile salvare il file delle rilevazioni.\n"
                    f"Controlla il log in:\n{LOG_DIR}"
                )

    def _esci_dal_programma(self):
        """
        Chiude ordinatamente tutto il programma: ferma un'eventuale
        registrazione in corso (salvandola), chiude dump1090, le finestre
        delle mappe e della webcam, ripulisce la cartella temp, poi
        chiude anche il menu.
        """
        conferma = messagebox.askyesno(
            "SkyTruth - Uscita",
            "Vuoi davvero uscire?\n"
            "Verranno chiuse tutte le finestre aperte (dump1090, mappe, webcam)."
        )
        if not conferma:
            return

        if self._registrazione_attiva:
            self._pulsante_registrazione.config(text="Salvataggio in corso...", state="disabled")
            self.root.update_idletasks()
            ferma_registrazione_schermo_e_salva()
            self._registrazione_attiva = False

        if self._registrazione_txt_attiva:
            self._pulsante_txt.config(text="Salvataggio in corso...", state="disabled")
            self.root.update_idletasks()
            ferma_salvataggio_txt_e_salva()
            self._registrazione_txt_attiva = False

        chiudi_tutti_i_processi_secondari()
        pulisci_cartella_temp()

        logging.info("=== Uscita dal programma richiesta dall'utente ===")
        self.root.destroy()

    def _apri_mappa_reale(self):
        """
        Avvia il server della mappa "ghost" (dati dongle, se non gia'
        attivo) e apre la finestra nativa gia' posizionata nel quadrante
        basso-sinistra.
        """
        x, y, larghezza, altezza = calcola_quadranti()["basso_sx"]

        avvia_mappa_reale(
            self.latitudine, self.longitudine, self.raggio_km,
            x, y, larghezza, altezza
        )

    def _apri_mappa_opensky(self):
        """
        La prima volta in questa sessione, chiede se si vogliono
        impostare le credenziali OpenSky (facoltative: senza, si procede
        comunque in modalita' anonima). Poi avvia il server della mappa
        OpenSky "mainstream" e apre la finestra nativa gia' posizionata
        nel quadrante alto-destra.
        """
        if not self._opensky_credenziali_richieste:
            self._opensky_credenziali_richieste = True
            self._mostra_popup_credenziali_opensky(self._continua_apertura_mappa_opensky)
        else:
            self._continua_apertura_mappa_opensky()

    def _continua_apertura_mappa_opensky(self):
        x, y, larghezza, altezza = calcola_quadranti()["alto_dx"]

        avvia_mappa_opensky(
            self.latitudine, self.longitudine, self.raggio_km,
            x, y, larghezza, altezza
        )

    def _mostra_popup_credenziali_opensky(self, callback_continua):
        """
        Finestra facoltativa per impostare client_id/client_secret di
        OpenSky (autenticazione OAuth2). Senza credenziali si procede
        comunque in modalita' anonima (piu' limitata, ma funzionante).
        Le credenziali NON vengono mai salvate su disco.
        """
        finestra = tk.Toplevel(self.root)
        finestra.title("SkyTruth - Credenziali OpenSky (facoltative)")
        finestra.grab_set()

        tk.Label(
            finestra,
            text="Puoi impostare le credenziali OpenSky per un accesso\n"
                 "più affidabile ai dati (facoltativo).",
            font=FONT_MENU, justify="center"
        ).pack(padx=24, pady=(20, 10))

        tk.Label(finestra, text="Client ID:", font=FONT_MENU).pack(padx=24, pady=(6, 4))
        campo_client_id = tk.Entry(finestra, font=FONT_MENU, width=36)
        campo_client_id.pack(padx=24)
        campo_client_id.focus()

        tk.Label(finestra, text="Client Secret:", font=FONT_MENU).pack(padx=24, pady=(14, 4))
        campo_client_secret = tk.Entry(finestra, font=FONT_MENU, width=36, show="*")
        campo_client_secret.pack(padx=24)

        def _usa_credenziali():
            client_id = campo_client_id.get().strip()
            client_secret = campo_client_secret.get().strip()
            if client_id and client_secret:
                imposta_credenziali_opensky(client_id, client_secret)
                logging.info("Credenziali OpenSky impostate per questa sessione.")
            finestra.destroy()
            callback_continua()

        def _modalita_anonima():
            finestra.destroy()
            callback_continua()

        riga_bottoni = tk.Frame(finestra)
        riga_bottoni.pack(pady=(18, 20))

        tk.Button(
            riga_bottoni, text="Usa queste credenziali",
            font=FONT_MENU_BOLD, command=_usa_credenziali
        ).pack(side="left", padx=10)

        tk.Button(
            riga_bottoni, text="Continua in modalità anonima",
            font=FONT_MENU, command=_modalita_anonima
        ).pack(side="left", padx=10)

        finestra.update_idletasks()
        x_root = self.root.winfo_rootx()
        y_root = self.root.winfo_rooty()
        larghezza_root = self.root.winfo_width()
        altezza_root = self.root.winfo_height()
        larghezza_fin = finestra.winfo_width()
        altezza_fin = finestra.winfo_height()
        x_fin = x_root + (larghezza_root - larghezza_fin) // 2
        y_fin = y_root + (altezza_root - altezza_fin) // 2
        finestra.geometry(f"+{max(x_fin, 0)}+{max(y_fin, 0)}")

    def _scegli_webcam(self):
        """
        Avvia il rilevamento delle webcam in un thread separato (puo'
        richiedere qualche secondo) per non bloccare l'interfaccia
        grafica, che altrimenti apparirebbe "non risponde". Quando il
        rilevamento e' completato, il risultato torna sul thread
        principale per mostrare il dialogo di scelta.
        """
        if not verifica_opencv_disponibile():
            return

        self._pulsante_webcam.config(state="disabled", text="Rilevamento in corso...")

        def _rileva_in_background():
            webcam_trovate = rileva_webcam_con_nomi()
            # Il resto (dialoghi, avvio webcam) deve girare sul thread
            # principale di Tkinter: lo pianifichiamo con 'after'.
            self.root.after(0, lambda: self._webcam_rilevate(webcam_trovate))

        threading.Thread(target=_rileva_in_background, daemon=True).start()

    def _webcam_rilevate(self, webcam_trovate):
        """
        Chiamato sul thread principale quando il rilevamento webcam (in
        background) e' terminato. Mostra una finestra di scelta dedicata
        (piu' grande e leggibile della finestrella di sistema), con un
        bottone per ogni webcam trovata e l'opzione per non usarne nessuna.
        """
        self._pulsante_webcam.config(
            state="normal",
            text="Scegli la webcam da utilizzare (attendere qualche minuto per la rilevazione)"
        )

        if not webcam_trovate:
            messagebox.showerror(
                "SkyTruth - Webcam non rilevata",
                "Nessuna webcam rilevata.\n"
                "Collegare una periferica, installare i driver,\n"
                "riavviare il programma."
            )
            return

        self._mostra_finestra_scelta_webcam(webcam_trovate)

    def _mostra_finestra_scelta_webcam(self, webcam_trovate):
        """
        Costruisce una piccola finestra dedicata (Toplevel) con un bottone
        grande per ciascuna webcam rilevata, piu' un'opzione in fondo per
        non usare nessuna webcam.
        """
        finestra = tk.Toplevel(self.root)
        finestra.title("SkyTruth - Scegli la webcam")
        finestra.grab_set()  # rende la finestra modale

        tk.Label(
            finestra, text="Webcam rilevate:",
            font=("Consolas", 15, "bold")
        ).pack(padx=30, pady=(20, 12))

        def _scegli(indice):
            finestra.destroy()
            messagebox.showinfo(
                "SkyTruth - Webcam",
                "Posiziona la webcam verso l'alto ed inizia la visualizzazione "
                "del cielo (ma senza registrare)."
            )
            x, y, larghezza, altezza = calcola_quadranti()["basso_dx"]
            avvia_webcam(indice, x, y, larghezza, altezza)
            self._webcam_attiva = True

        for indice, nome in webcam_trovate:
            tk.Button(
                finestra, text=f"{indice} = {nome}",
                font=("Consolas", 14), width=40,
                command=lambda i=indice: _scegli(i)
            ).pack(padx=30, pady=6)

        tk.Frame(finestra, height=2, bg="gray70").pack(fill="x", padx=20, pady=(14, 14))

        tk.Button(
            finestra, text="Non usare la webcam",
            font=("Consolas", 14), width=40, fg="darkred",
            command=finestra.destroy
        ).pack(padx=30, pady=(0, 20))

        finestra.update_idletasks()

        # Centriamo la finestra rispetto al menu principale
        x_root = self.root.winfo_rootx()
        y_root = self.root.winfo_rooty()
        larghezza_root = self.root.winfo_width()
        altezza_root = self.root.winfo_height()

        larghezza_fin = finestra.winfo_width()
        altezza_fin = finestra.winfo_height()

        x_fin = x_root + (larghezza_root - larghezza_fin) // 2
        y_fin = y_root + (altezza_root - altezza_fin) // 2

        finestra.geometry(f"+{max(x_fin, 0)}+{max(y_fin, 0)}")


# --------------------------------------------------------------------------
# AVVIO PROGRAMMA
# --------------------------------------------------------------------------
def main():
    # IMPORTANTE: rendiamo il processo "consapevole" della scalatura DPI di
    # Windows PRIMA di creare qualsiasi finestra. Senza questo, su schermi
    # con scalatura al 125%/150% (molto comuni), le coordinate schermo che
    # otteniamo da Windows non corrispondono ai pixel fisici reali, causando
    # posizionamenti imprecisi o sovrapposti delle finestre.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            logging.warning("Impossibile impostare la DPI-awareness del processo.")

    setup_logging()
    logging.info("=== Avvio SkyTruth (Fase 1) ===")

    # Creiamo subito le cartelle base, se non esistono, cosi' il resto
    # del programma le trova sempre pronte.
    for cartella in (LOG_DIR, TEMP_DIR, DUMP1090_DIR, MAPS_DIR,
                      FFMPEG_DIR, SESSIONI_DIR, SESSIONI_TXT_DIR, SNAPSHOTS_DIR, BROWSER_DIR):
        try:
            os.makedirs(cartella, exist_ok=True)
        except Exception:
            logging.warning("Impossibile creare/verificare la cartella: %s", cartella)

    scrivi_script_visualizzatore_mappa()

    root = tk.Tk()

    avvisa_se_powershell_mancante()

    # Fix per un problema noto di Tkinter su Windows: anche con il processo
    # gia' reso DPI-aware, Tkinter puo' applicare una sua scala interna che
    # non corrisponde ai pixel fisici reali. Forzando la scala a 1.0,
    # le coordinate che usiamo per posizionare le finestre (calcolate in
    # pixel fisici tramite ottieni_area_lavoro) risultano corrette.
    try:
        root.tk.call('tk', 'scaling', 1.0)
    except Exception:
        logging.warning("Impossibile impostare la scala Tk a 1.0.")

    # Da questo momento in poi, qualsiasi errore non previsto nell'interfaccia
    # grafica viene intercettato e loggato invece di far crashare tutto.
    sys.excepthook = log_uncaught_exceptions

    try:
        app = MenuApp(root)
        root.mainloop()
    except Exception:
        logging.error("Errore critico nell'avvio dell'interfaccia:\n%s",
                       traceback.format_exc())
        messagebox.showerror(
            "SkyTruth - Errore critico",
            "Impossibile avviare l'interfaccia grafica.\n"
            f"Dettagli nel log: {LOG_DIR}"
        )

    logging.info("=== Chiusura SkyTruth ===")


if __name__ == "__main__":
    main()
