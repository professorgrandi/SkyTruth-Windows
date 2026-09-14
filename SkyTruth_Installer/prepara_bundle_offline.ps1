# ============================================================================
# prepara_bundle_offline.ps1
# Prepara AUTOMATICAMENTE tutti i file necessari per l'installazione
# offline di SkyTruth (file grandi + pacchetti Python), scaricandoli da
# internet e mettendoli nella cartella 'offline\' con i nomi e nei posti
# giusti - non serve piu' scaricare/rinominare i file a mano uno per uno.
#
# IMPORTANTE: questo script richiede una connessione internet (deve
# scaricare i file una volta); il risultato che produce (la cartella
# 'offline\' completa) potra' poi essere usato per installare SkyTruth
# su QUALSIASI altro PC senza connessione (es. da chiavetta USB).
#
# Va eseguito dalla cartella 'SkyTruth_Installer' (accanto a install.ps1).
# ============================================================================

$SourceDir = $PSScriptRoot
$OfflineDir = Join-Path $SourceDir "offline"

$PythonVersione = "3.12.7"
$PythonZipUrl     = "https://www.python.org/ftp/python/$PythonVersione/python-$PythonVersione-embed-amd64.zip"
$PythonFullExeUrl = "https://www.python.org/ftp/python/$PythonVersione/python-$PythonVersione-amd64.exe"
$GetPipUrl        = "https://bootstrap.pypa.io/get-pip.py"
$Dump1090ZipUrl   = "https://github.com/timseed/Dump1090_Windows/archive/refs/heads/main.zip"
$FFmpegZipUrl     = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$WebView2BootstrapperUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

$PacchettiPip = @("setuptools", "wheel", "pywebview", "opencv-python", "pygrabber", "Pillow")

# Se hai gia' installato SkyTruth online su questo PC, riusiamo il suo
# Python (con pip gia' pronto) per scaricare i pacchetti; altrimenti va
# fatta prima un'installazione online almeno una volta.
$PythonEsistente = "C:\SkyTruth\python\python.exe"


function Scrivi-Passo {
    param([string]$Testo)
    Write-Host ""
    Write-Host "==== $Testo ====" -ForegroundColor Cyan
}

function Scarica-File {
    param([string]$Url, [string]$Destinazione, [string]$Descrizione)
    if (Test-Path $Destinazione) {
        Write-Host "OK  - '$Descrizione' e' gia' presente, salto il download." -ForegroundColor Green
        return $true
    }
    try {
        Write-Host "...  Download di '$Descrizione' in corso..." -ForegroundColor Yellow
        Invoke-WebRequest -Uri $Url -OutFile $Destinazione -UseBasicParsing -ErrorAction Stop
        Write-Host "OK  - '$Descrizione' scaricato." -ForegroundColor Green
        return $true
    } catch {
        Write-Host "ERR - Impossibile scaricare '$Descrizione': $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}


Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "   SkyTruth - Preparazione automatica del bundle OFFLINE"           -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Questo script scarica tutto il necessario in: $OfflineDir"
Write-Host "(serve internet ORA; il risultato funzionera' poi senza internet)"
Write-Host ""

# --- Creazione struttura cartelle ---
$cartelle = @(
    (Join-Path $OfflineDir "python"),
    (Join-Path $OfflineDir "python\wheels"),
    (Join-Path $OfflineDir "dump1090"),
    (Join-Path $OfflineDir "ffmpeg"),
    (Join-Path $OfflineDir "webview2")
)
foreach ($cartella in $cartelle) {
    New-Item -ItemType Directory -Path $cartella -Force | Out-Null
}

# --- Python embedded + get-pip + installer completo (per Tkinter) ---
Scrivi-Passo "Python embedded"
Scarica-File -Url $PythonZipUrl -Destinazione (Join-Path $OfflineDir "python\python-$PythonVersione-embed-amd64.zip") -Descrizione "Python $PythonVersione embedded" | Out-Null
Scarica-File -Url $GetPipUrl -Destinazione (Join-Path $OfflineDir "python\get-pip.py") -Descrizione "get-pip.py" | Out-Null
Scarica-File -Url $PythonFullExeUrl -Destinazione (Join-Path $OfflineDir "python\python-$PythonVersione-amd64.exe") -Descrizione "Python $PythonVersione completo (per Tkinter)" | Out-Null

# --- dump1090 ---
Scrivi-Passo "dump1090"
Scarica-File -Url $Dump1090ZipUrl -Destinazione (Join-Path $OfflineDir "dump1090\main.zip") -Descrizione "dump1090" | Out-Null

# --- FFmpeg ---
Scrivi-Passo "FFmpeg"
$ffmpegDestinazione = Join-Path $OfflineDir "ffmpeg\ffmpeg-release-essentials.zip"
Scarica-File -Url $FFmpegZipUrl -Destinazione $ffmpegDestinazione -Descrizione "FFmpeg (build essentials)" | Out-Null

# --- WebView2 ---
Scrivi-Passo "WebView2 Runtime"
Scarica-File -Url $WebView2BootstrapperUrl -Destinazione (Join-Path $OfflineDir "webview2\MicrosoftEdgeWebview2Setup.exe") -Descrizione "WebView2 Runtime Bootstrapper" | Out-Null

# --- Pacchetti Python (.whl) ---
Scrivi-Passo "Pacchetti Python (pywebview, opencv-python, pygrabber, Pillow)"

if (Test-Path $PythonEsistente) {
    Write-Host "Uso l'interprete Python gia' installato in: $PythonEsistente" -ForegroundColor Cyan
    $cartellaWheels = Join-Path $OfflineDir "python\wheels"

    foreach ($pacchetto in $PacchettiPip) {
        Write-Host "...  Download del pacchetto '$pacchetto' (con tutte le sue dipendenze)..." -ForegroundColor Yellow
        & $PythonEsistente -m pip download $pacchetto --dest $cartellaWheels --no-cache-dir 2>&1 | ForEach-Object {
            Write-Host "     $_"
        }
    }
    Write-Host "OK  - Pacchetti Python scaricati in: $cartellaWheels" -ForegroundColor Green
} else {
    Write-Host "ATTENZIONE: non ho trovato un'installazione Python di SkyTruth in $PythonEsistente." -ForegroundColor Yellow
    Write-Host "Per scaricare anche i pacchetti Python (.whl) in modalita' offline:" -ForegroundColor Yellow
    Write-Host "  1. Esegui prima 'install.bat' normalmente (con connessione internet)" -ForegroundColor Yellow
    Write-Host "  2. Poi rilancia questo script: i pacchetti verranno aggiunti alla" -ForegroundColor Yellow
    Write-Host "     cartella offline senza dover rifare tutto da capo." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "   Preparazione completata"                                        -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "La cartella '$OfflineDir' e' ora pronta per essere copiata (es. su"
Write-Host "una chiavetta USB) insieme al resto di SkyTruth_Installer, per"
Write-Host "installare SkyTruth su qualsiasi PC senza connessione internet."
Write-Host ""
Read-Host "Premi INVIO per uscire"
