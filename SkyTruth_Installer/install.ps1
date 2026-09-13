# ============================================================================
# SkyTruth - install.ps1
# Installer automatico per Windows 10/11.
#
# Cosa fa: prepara l'intero ambiente per eseguire SkyTruth (Mainstream Fly
# vs Ghost Fly), installando Python embedded, le librerie necessarie,
# dump1090, FFmpeg, verificando WebView2, e creando un collegamento sul
# Desktop con icona personalizzata.
#
# Ogni passaggio viene mostrato a video in modo chiaro E scritto in un file
# di log dettagliato, cosi' in caso di problemi e' sempre possibile capire
# esattamente cosa e' successo senza dover ripetere tutta l'installazione.
#
# Autori: by Professor Grandi (Mahatma) & Claude Sonnet 5
# ============================================================================

# ----------------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------------
$BaseDir      = "C:\SkyTruth"
$LogDir       = Join-Path $BaseDir "logs"
$PythonDir    = Join-Path $BaseDir "python"
$Dump1090Dir  = Join-Path $BaseDir "dump1090"
$FFmpegDir    = Join-Path $BaseDir "ffmpeg"
$MapsDir      = Join-Path $BaseDir "maps"
$TempDir      = Join-Path $BaseDir "temp"
$SessioniDir  = Join-Path $BaseDir "sessioni_video"
$SessioniTxt  = Join-Path $BaseDir "sessioni_txt"
$BrowserDir   = Join-Path $BaseDir "browser"

$PythonVersione = "3.12.7"
$PythonZipUrl   = "https://www.python.org/ftp/python/$PythonVersione/python-$PythonVersione-embed-amd64.zip"
$PythonFullExeUrl = "https://www.python.org/ftp/python/$PythonVersione/python-$PythonVersione-amd64.exe"
$GetPipUrl      = "https://bootstrap.pypa.io/get-pip.py"
$Dump1090ZipUrl = "https://github.com/timseed/Dump1090_Windows/archive/refs/heads/main.zip"
$FFmpegZipUrl   = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$WebView2BootstrapperUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$WebView2PaginaManuale   = "https://developer.microsoft.com/microsoft-edge/webview2/"

# Cartella dove si trova questo stesso script: ci aspettiamo che main.py e
# skytruth.ico siano gia' presenti li' accanto (distribuiti insieme
# all'installer, es. dentro lo stesso archivio scaricato da GitHub).
$SourceDir = $PSScriptRoot

# Cartella 'offline\': se l'utente ci ha gia' messo dentro i file necessari
# (es. per installare da una chiavetta USB, senza connessione internet),
# l'installer li usa direttamente invece di scaricarli.
$OfflineDir = Join-Path $SourceDir "offline"
$OfflinePythonZip = Join-Path $OfflineDir "python\python-$PythonVersione-embed-amd64.zip"
$OfflinePythonFullExe = Join-Path $OfflineDir "python\python-$PythonVersione-amd64.exe"
$OfflineGetPip    = Join-Path $OfflineDir "python\get-pip.py"
$OfflineDump1090Zip = Join-Path $OfflineDir "dump1090\main.zip"
$OfflineFFmpegDir = Join-Path $OfflineDir "ffmpeg"
$OfflineWebView2Exe = Join-Path $OfflineDir "webview2\MicrosoftEdgeWebview2Setup.exe"

$TotalePassi   = 13
$script:PassoCorrente = 0
$script:ErroriRiepilogo = @()

$TimestampLog = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = $null  # verra' impostato dopo aver creato la cartella logs


# ----------------------------------------------------------------------------
# FUNZIONI DI LOG E OUTPUT
# ----------------------------------------------------------------------------
function Scrivi-Log {
    param(
        [string]$Messaggio,
        [ValidateSet("INFO", "OK", "WARN", "ERROR")]
        [string]$Livello = "INFO"
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $riga = "$timestamp [$Livello] $Messaggio"

    if ($LogFile) {
        try {
            Add-Content -Path $LogFile -Value $riga -Encoding UTF8
        } catch {
            # Se anche scrivere sul log fallisce, non blocchiamo
            # l'installazione: continuiamo comunque con l'output a video.
        }
    }

    switch ($Livello) {
        "OK"    { Write-Host $Messaggio -ForegroundColor Green }
        "WARN"  { Write-Host $Messaggio -ForegroundColor Yellow }
        "ERROR" { Write-Host $Messaggio -ForegroundColor Red }
        default { Write-Host $Messaggio -ForegroundColor Cyan }
    }
}

function Scrivi-Passo {
    param([string]$Descrizione)
    $script:PassoCorrente++
    Write-Host ""
    Write-Host "==================================================================" -ForegroundColor DarkGray
    Scrivi-Log -Messaggio "Passo $($script:PassoCorrente) di $TotalePassi`: $Descrizione" -Livello "INFO"
}

function Segnala-Errore {
    param([string]$Componente, [string]$Dettaglio)
    $script:ErroriRiepilogo += "$Componente`: $Dettaglio"
    Scrivi-Log -Messaggio "$Componente`: $Dettaglio" -Livello "ERROR"
}

function Scarica-File {
    param(
        [string]$Url,
        [string]$Destinazione,
        [string]$Descrizione
    )
    for ($tentativo = 1; $tentativo -le 2; $tentativo++) {
        try {
            Scrivi-Log -Messaggio "Download di '$Descrizione' (tentativo $tentativo di 2)..." -Livello "INFO"
            Invoke-WebRequest -Uri $Url -OutFile $Destinazione -UseBasicParsing -ErrorAction Stop
            Scrivi-Log -Messaggio "Download completato: $Descrizione" -Livello "OK"
            return $true
        } catch {
            Scrivi-Log -Messaggio "Tentativo $tentativo fallito per '$Descrizione': $($_.Exception.Message)" -Livello "WARN"
            Start-Sleep -Seconds 2
        }
    }
    Segnala-Errore -Componente $Descrizione -Dettaglio "Impossibile scaricare il file dopo 2 tentativi."
    return $false
}

function Ottieni-File {
    <#
    Cerca prima il file in modalita' OFFLINE (cartella 'offline\'); se lo
    trova, lo copia direttamente senza toccare internet. Se non lo trova,
    prova a scaricarlo dall'URL indicato. Stessa firma di ritorno di
    Scarica-File ($true/$false), cosi' il resto del codice non deve sapere
    da dove e' arrivato il file.
    #>
    param(
        [string]$PercorsoOffline,
        [string]$Url,
        [string]$Destinazione,
        [string]$Descrizione
    )

    if ($PercorsoOffline -and (Test-Path $PercorsoOffline)) {
        try {
            Copy-Item -Path $PercorsoOffline -Destination $Destinazione -Force -ErrorAction Stop
            Scrivi-Log -Messaggio "'$Descrizione' trovato in modalita' offline ($PercorsoOffline): uso il file locale, nessun download necessario." -Livello "OK"
            return $true
        } catch {
            Scrivi-Log -Messaggio "Trovato '$Descrizione' offline ma impossibile copiarlo ($($_.Exception.Message)): provo a scaricarlo da internet." -Livello "WARN"
        }
    }

    return Scarica-File -Url $Url -Destinazione $Destinazione -Descrizione $Descrizione
}


# ----------------------------------------------------------------------------
# INTESTAZIONE
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "   SkyTruth - Installazione automatica per Windows"                  -ForegroundColor Green
Write-Host "   by Professor Grandi (Mahatma) & Claude Sonnet 5"                  -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Questo installer preparera' automaticamente tutto il necessario in:"
Write-Host "  $BaseDir" -ForegroundColor Yellow
Write-Host ""
Write-Host "Ogni passaggio verra' mostrato chiaramente e registrato in un file"
Write-Host "di log, cosi' in caso di problemi sapremo sempre cosa e' successo."
Write-Host ""
Start-Sleep -Seconds 2


# ----------------------------------------------------------------------------
# PASSO 1 - Verifica privilegi di amministratore
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Verifica dei privilegi di amministratore"

$utenteAttuale = [Security.Principal.WindowsIdentity]::GetCurrent()
$principale = New-Object Security.Principal.WindowsPrincipal($utenteAttuale)
$eAmministratore = $principale.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $eAmministratore) {
    Write-Host ""
    Write-Host "Questo installer deve essere eseguito come AMMINISTRATORE." -ForegroundColor Yellow
    Write-Host "Provo a riavviarlo automaticamente con i permessi corretti..." -ForegroundColor Yellow
    Start-Sleep -Seconds 2

    try {
        $argomenti = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
        Start-Process -FilePath "powershell.exe" -ArgumentList $argomenti -Verb RunAs -ErrorAction Stop
    } catch {
        Write-Host ""
        Write-Host "Non e' stato possibile riavviare automaticamente come amministratore." -ForegroundColor Red
        Write-Host "Fai clic destro su questo file e scegli 'Esegui con PowerShell come amministratore'." -ForegroundColor Red
    }
    exit
}

Write-Host "Privilegi di amministratore confermati." -ForegroundColor Green


# ----------------------------------------------------------------------------
# Tutto il resto dell'installazione (Passi 2-11) gira dentro un blocco
# try/catch generale: e' una "rete di sicurezza" che cattura QUALSIASI
# errore imprevisto non gia' gestito nei singoli passaggi, evitando che
# PowerShell si chiuda con un errore criptico senza mostrare il riepilogo
# finale. I singoli passaggi hanno comunque i propri try/catch specifici,
# quindi in condizioni normali questa rete di sicurezza non dovrebbe mai
# doversi attivare.
# ----------------------------------------------------------------------------
try {

# PASSO 2 - Creazione struttura cartelle
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Creazione della struttura di cartelle"

$cartelle = @($BaseDir, $LogDir, $PythonDir, $Dump1090Dir, $FFmpegDir,
              $MapsDir, $TempDir, $SessioniDir, $SessioniTxt, $BrowserDir)

foreach ($cartella in $cartelle) {
    try {
        New-Item -ItemType Directory -Path $cartella -Force -ErrorAction Stop | Out-Null
    } catch {
        Write-Host "Impossibile creare la cartella: $cartella" -ForegroundColor Red
        Write-Host "Dettagli: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Verifica di avere i permessi necessari e riprova." -ForegroundColor Red
        exit 1
    }
}

# Ora che la cartella logs esiste, possiamo attivare il file di log vero.
$LogFile = Join-Path $LogDir "install_log_$TimestampLog.txt"
Scrivi-Log -Messaggio "=== Avvio installazione SkyTruth ===" -Livello "INFO"
Scrivi-Log -Messaggio "Struttura cartelle creata correttamente in $BaseDir." -Livello "OK"


# ----------------------------------------------------------------------------
# PASSO 3 - Download ed estrazione di Python embedded
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Download ed installazione di Python (versione $PythonVersione, embedded)"

$pythonEmbeddedOk = $false
$pythonExe = Join-Path $PythonDir "python.exe"
$pythonwExe = Join-Path $PythonDir "pythonw.exe"

if ((Test-Path $pythonExe) -and (Test-Path $pythonwExe)) {
    Scrivi-Log -Messaggio "Python risulta gia' presente in $PythonDir, salto il download." -Livello "OK"
    $pythonEmbeddedOk = $true
} else {
    $zipPython = Join-Path $env:TEMP "python_embed_skytruth.zip"

    if (Ottieni-File -PercorsoOffline $OfflinePythonZip -Url $PythonZipUrl -Destinazione $zipPython -Descrizione "Python $PythonVersione embedded") {
        try {
            Expand-Archive -Path $zipPython -DestinationPath $PythonDir -Force -ErrorAction Stop
            Scrivi-Log -Messaggio "Python estratto correttamente in $PythonDir." -Livello "OK"

            if ((Test-Path $pythonExe) -and (Test-Path $pythonwExe)) {
                $pythonEmbeddedOk = $true
                Scrivi-Log -Messaggio "Trovati sia python.exe che pythonw.exe: perfetto." -Livello "OK"
            } elseif (Test-Path $pythonExe) {
                $pythonEmbeddedOk = $true
                Scrivi-Log -Messaggio "Trovato python.exe ma NON pythonw.exe: SkyTruth funzionera' comunque, ma alcune finestre potrebbero mostrare una console in piu' del previsto." -Livello "WARN"
            } else {
                Segnala-Errore -Componente "Python" -Dettaglio "python.exe non trovato dopo l'estrazione."
            }
        } catch {
            Segnala-Errore -Componente "Python" -Dettaglio "Errore nell'estrazione: $($_.Exception.Message)"
        } finally {
            Remove-Item -Path $zipPython -Force -ErrorAction SilentlyContinue
        }
    }
}


# ----------------------------------------------------------------------------
# PASSO 4 - Aggiunta del supporto Tkinter (necessario per l'interfaccia grafica)
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Aggiunta del supporto Tkinter (necessario per l'interfaccia grafica)"

# Il pacchetto "embeddable" di Python NON include Tkinter (la libreria
# usata da SkyTruth per il menu principale) per scelta di design ufficiale
# di Python. Senza questo passaggio, il collegamento sul Desktop non
# farebbe assolutamente nulla al click (l'errore avviene prima che la
# nostra gestione errori possa intervenire, perche' l'app parte con
# pythonw.exe, che non ha nessuna console dove mostrare l'errore).
$tkinterOk = $false

if ($pythonEmbeddedOk) {
    $tkinterPyd = Join-Path $PythonDir "_tkinter.pyd"

    if (Test-Path $tkinterPyd) {
        Scrivi-Log -Messaggio "Supporto Tkinter gia' presente, salto questo passaggio." -Livello "OK"
        $tkinterOk = $true
    } else {
        # Per ottenere i file di Tkinter, installiamo temporaneamente
        # (in una cartella a parte, senza PATH/icone/associazioni file)
        # l'installer completo ufficiale di Python, prendiamo solo i file
        # che ci servono, poi disinstalliamo tutto il resto.
        $installerCompletoPath = Join-Path $env:TEMP "python_full_skytruth.exe"
        $tempInstallDir = Join-Path $env:TEMP "skytruth_python_full_temp"

        if (Ottieni-File -PercorsoOffline $OfflinePythonFullExe -Url $PythonFullExeUrl -Destinazione $installerCompletoPath -Descrizione "Python $PythonVersione completo (solo per prelevare Tkinter)") {
            try {
                if (Test-Path $tempInstallDir) {
                    Remove-Item -Path $tempInstallDir -Recurse -Force -ErrorAction SilentlyContinue
                }

                Scrivi-Log -Messaggio "Installazione temporanea di Python in corso (solo per prelevare Tkinter, verra' rimossa subito dopo, puo' richiedere un minuto)..." -Livello "INFO"

                $argInstall = @(
                    "/quiet", "InstallAllUsers=0", "PrependPath=0", "Shortcuts=0",
                    "AssociateFiles=0", "CompileAll=0", "Include_launcher=0",
                    "Include_pip=0", "Include_test=0", "Include_doc=0",
                    "Include_dev=0", "Include_tcltk=1",
                    "TargetDir=$tempInstallDir"
                )
                $procInstall = Start-Process -FilePath $installerCompletoPath -ArgumentList $argInstall -Wait -PassThru -ErrorAction Stop

                $tkinterPydOrigine = Join-Path $tempInstallDir "DLLs\_tkinter.pyd"

                if (Test-Path $tkinterPydOrigine) {
                    Copy-Item -Path $tkinterPydOrigine -Destination $PythonDir -Force
                    Copy-Item -Path (Join-Path $tempInstallDir "DLLs\tcl86t.dll") -Destination $PythonDir -Force
                    Copy-Item -Path (Join-Path $tempInstallDir "DLLs\tk86t.dll") -Destination $PythonDir -Force
                    Copy-Item -Path (Join-Path $tempInstallDir "Lib\tkinter") -Destination (Join-Path $PythonDir "tkinter") -Recurse -Force
                    Copy-Item -Path (Join-Path $tempInstallDir "tcl") -Destination (Join-Path $PythonDir "tcl") -Recurse -Force

                    # zlib1.dll: usata internamente da Tcl per le funzioni di
                    # compressione. Senza di essa, tcl86t.dll non si carica
                    # (errore "DLL load failed" all'avvio di Tkinter).
                    $zlibOrigine = Join-Path $tempInstallDir "DLLs\zlib1.dll"
                    if (Test-Path $zlibOrigine) {
                        Copy-Item -Path $zlibOrigine -Destination $PythonDir -Force
                        Scrivi-Log -Messaggio "zlib1.dll copiata (richiesta da tcl86t.dll)." -Livello "OK"
                    } else {
                        Scrivi-Log -Messaggio "zlib1.dll non trovata nell'installazione temporanea: Tkinter potrebbe non funzionare." -Livello "WARN"
                    }

                    Scrivi-Log -Messaggio "File di Tkinter copiati correttamente." -Livello "OK"
                    $tkinterOk = $true
                } else {
                    Segnala-Errore -Componente "Tkinter" -Dettaglio "File di Tkinter non trovati nell'installazione temporanea (codice di uscita installer: $($procInstall.ExitCode))."
                }

                # Disinstalliamo subito l'installazione temporanea completa,
                # cosi' non resta nulla di superfluo sul sistema.
                Scrivi-Log -Messaggio "Rimozione dell'installazione temporanea di Python..." -Livello "INFO"
                Start-Process -FilePath $installerCompletoPath -ArgumentList @("/uninstall", "/quiet", "TargetDir=$tempInstallDir") -Wait -ErrorAction SilentlyContinue | Out-Null

            } catch {
                Segnala-Errore -Componente "Tkinter" -Dettaglio $_.Exception.Message
            } finally {
                Remove-Item -Path $installerCompletoPath -Force -ErrorAction SilentlyContinue
                Remove-Item -Path $tempInstallDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

        if ($tkinterOk) {
            # Verifica finale: proviamo davvero ad importare tkinter.
            # Catturiamo l'output (invece di scartarlo) cosi' se fallisce
            # vediamo il messaggio di errore reale nel log, non solo il
            # codice di uscita.
            $tkinterVerificaOutput = & $pythonExe -c "import tkinter" 2>&1
            if ($LASTEXITCODE -eq 0) {
                Scrivi-Log -Messaggio "Verifica Tkinter: importazione riuscita." -Livello "OK"
            } else {
                foreach ($rigaOutput in $tkinterVerificaOutput) {
                    Scrivi-Log -Messaggio "  $rigaOutput" -Livello "WARN"
                }
                Scrivi-Log -Messaggio "Verifica Tkinter: l'importazione di prova non e' riuscita (codice $LASTEXITCODE)." -Livello "WARN"
                $tkinterOk = $false
                Segnala-Errore -Componente "Tkinter" -Dettaglio "Verifica di importazione fallita dopo la copia dei file. SkyTruth potrebbe non avviarsi."
            }
        }
    }
} else {
    Scrivi-Log -Messaggio "Python non disponibile: salto l'aggiunta di Tkinter." -Livello "WARN"
}


# ----------------------------------------------------------------------------
# PASSO 5 - Abilitazione di pip nell'interprete embedded + installazione pacchetti
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Abilitazione di pip ed installazione delle librerie Python necessarie"

if ($pythonEmbeddedOk) {
    try {
        # Il pacchetto "embedded" di Python disabilita di default 'site-packages'.
        # Bisogna decommentare la riga 'import site' nel file <versione>._pth
        # per poter installare ed usare librerie esterne (pip, pywebview, ecc.).
        $filePth = Get-ChildItem -Path $PythonDir -Filter "python3*._pth" | Select-Object -First 1

        if ($filePth) {
            $contenuto = Get-Content -Path $filePth.FullName
            $nuovoContenuto = $contenuto -replace '^#\s*import site', 'import site'
            Set-Content -Path $filePth.FullName -Value $nuovoContenuto -Encoding ASCII
            Scrivi-Log -Messaggio "File $($filePth.Name) configurato per abilitare i pacchetti esterni." -Livello "OK"
        } else {
            Scrivi-Log -Messaggio "File '_pth' non trovato: proseguo comunque, potrebbe non servire in questa versione." -Livello "WARN"
        }

        # Scarichiamo ed eseguiamo il bootstrap ufficiale di pip.
        $getPipPath = Join-Path $PythonDir "get-pip.py"
        if (Ottieni-File -PercorsoOffline $OfflineGetPip -Url $GetPipUrl -Destinazione $getPipPath -Descrizione "get-pip.py") {
            Scrivi-Log -Messaggio "Installazione di pip in corso (puo' richiedere qualche minuto)..." -Livello "INFO"
            & $pythonExe $getPipPath --no-warn-script-location 2>&1 | ForEach-Object {
                Scrivi-Log -Messaggio "  $_" -Livello "INFO"
            }
            Remove-Item -Path $getPipPath -Force -ErrorAction SilentlyContinue

            # Il pip moderno NON installa piu' automaticamente 'setuptools' e
            # 'wheel'. Alcune dipendenze (es. 'proxy_tools', richiesta da
            # pywebview) sono distribuite solo come sorgente da compilare, e
            # senza setuptools l'installazione fallisce con l'errore
            # "Cannot import 'setuptools.build_meta'". Le installiamo prima
            # di tutto il resto per evitare il problema.
            Scrivi-Log -Messaggio "Installazione di 'setuptools' e 'wheel' (necessari per compilare alcune dipendenze)..." -Livello "INFO"
            & $pythonExe -m pip install setuptools wheel --no-warn-script-location --no-cache-dir 2>&1 | ForEach-Object {
                Scrivi-Log -Messaggio "  $_" -Livello "INFO"
            }
            if ($LASTEXITCODE -ne 0) {
                Segnala-Errore -Componente "pip install setuptools/wheel" -Dettaglio "Codice di uscita $LASTEXITCODE. L'installazione di pywebview potrebbe fallire."
            }

            # Installazione delle 3 librerie richieste da SkyTruth.
            $pacchetti = @("pywebview", "opencv-python", "pygrabber")
            foreach ($pacchetto in $pacchetti) {
                Scrivi-Log -Messaggio "Installazione di '$pacchetto' in corso (puo' richiedere qualche minuto)..." -Livello "INFO"
                & $pythonExe -m pip install $pacchetto --no-warn-script-location --no-cache-dir 2>&1 | ForEach-Object {
                    Scrivi-Log -Messaggio "  $_" -Livello "INFO"
                }
                if ($LASTEXITCODE -ne 0) {
                    Segnala-Errore -Componente "pip install $pacchetto" -Dettaglio "Codice di uscita $LASTEXITCODE. Controlla il log per i dettagli."
                } else {
                    Scrivi-Log -Messaggio "'$pacchetto' installato correttamente." -Livello "OK"
                }
            }
        }
    } catch {
        Segnala-Errore -Componente "Configurazione Python/pip" -Dettaglio $_.Exception.Message
    }
} else {
    Scrivi-Log -Messaggio "Python non disponibile: salto l'installazione delle librerie pip." -Livello "WARN"
}


# ----------------------------------------------------------------------------
# PASSO 6 - Download ed estrazione di dump1090
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Download ed installazione di dump1090"

$dump1090Exe = Join-Path $Dump1090Dir "dump1090.exe"

if (Test-Path $dump1090Exe) {
    Scrivi-Log -Messaggio "dump1090.exe risulta gia' presente, salto il download." -Livello "OK"
} else {
    $zipDump1090 = Join-Path $env:TEMP "dump1090_skytruth.zip"
    $estraiDump1090 = Join-Path $env:TEMP "dump1090_skytruth_estratto"

    if (Ottieni-File -PercorsoOffline $OfflineDump1090Zip -Url $Dump1090ZipUrl -Destinazione $zipDump1090 -Descrizione "dump1090 (timseed/Dump1090_Windows)") {
        try {
            Expand-Archive -Path $zipDump1090 -DestinationPath $estraiDump1090 -Force -ErrorAction Stop

            # Lo zip di GitHub contiene una sottocartella tipo
            # 'Dump1090_Windows-main': ne copiamo solo il CONTENUTO.
            $cartellaInterna = Get-ChildItem -Path $estraiDump1090 -Directory | Select-Object -First 1
            if ($cartellaInterna) {
                Copy-Item -Path (Join-Path $cartellaInterna.FullName "*") -Destination $Dump1090Dir -Recurse -Force
            }

            if (Test-Path $dump1090Exe) {
                Scrivi-Log -Messaggio "dump1090.exe installato correttamente in $Dump1090Dir." -Livello "OK"
            } else {
                Segnala-Errore -Componente "dump1090" -Dettaglio "dump1090.exe non trovato dopo l'estrazione."
            }
        } catch {
            Segnala-Errore -Componente "dump1090" -Dettaglio "Errore nell'estrazione: $($_.Exception.Message)"
        } finally {
            Remove-Item -Path $zipDump1090 -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $estraiDump1090 -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}


# ----------------------------------------------------------------------------
# PASSO 7 - Download ed estrazione di FFmpeg
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Download ed installazione di FFmpeg"

$ffmpegExe = Join-Path $FFmpegDir "ffmpeg.exe"

if (Test-Path $ffmpegExe) {
    Scrivi-Log -Messaggio "ffmpeg.exe risulta gia' presente, salto il download." -Livello "OK"
} else {
    $zipFFmpeg = Join-Path $env:TEMP "ffmpeg_skytruth.zip"
    $estraiFFmpeg = Join-Path $env:TEMP "ffmpeg_skytruth_estratto"

    Scrivi-Log -Messaggio "Il download di FFmpeg puo' richiedere qualche minuto (circa 100 MB)..." -Livello "INFO"

    # Il nome del file FFmpeg cambia ad ogni versione (es.
    # 'ffmpeg-7.1-essentials_build.zip'), quindi in modalita' offline
    # cerchiamo QUALSIASI file che inizi con 'ffmpeg-' e finisca in '.zip',
    # invece di pretendere un nome fisso.
    $offlineFFmpegZip = $null
    if (Test-Path $OfflineFFmpegDir) {
        $trovatoOffline = Get-ChildItem -Path $OfflineFFmpegDir -Filter "ffmpeg-*.zip" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($trovatoOffline) {
            $offlineFFmpegZip = $trovatoOffline.FullName
        }
    }

    if (Ottieni-File -PercorsoOffline $offlineFFmpegZip -Url $FFmpegZipUrl -Destinazione $zipFFmpeg -Descrizione "FFmpeg (build essentials, gyan.dev)") {
        try {
            Expand-Archive -Path $zipFFmpeg -DestinationPath $estraiFFmpeg -Force -ErrorAction Stop

            # Il nome della cartella interna cambia ad ogni versione
            # (es. 'ffmpeg-7.1-essentials_build'), quindi cerchiamo
            # ffmpeg.exe ovunque dentro la cartella estratta.
            $trovato = Get-ChildItem -Path $estraiFFmpeg -Filter "ffmpeg.exe" -Recurse | Select-Object -First 1

            if ($trovato) {
                Copy-Item -Path $trovato.FullName -Destination $ffmpegExe -Force
                Scrivi-Log -Messaggio "ffmpeg.exe installato correttamente in $FFmpegDir." -Livello "OK"
            } else {
                Segnala-Errore -Componente "FFmpeg" -Dettaglio "ffmpeg.exe non trovato dentro l'archivio scaricato."
            }
        } catch {
            Segnala-Errore -Componente "FFmpeg" -Dettaglio "Errore nell'estrazione: $($_.Exception.Message)"
        } finally {
            Remove-Item -Path $zipFFmpeg -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $estraiFFmpeg -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}


# ----------------------------------------------------------------------------
# PASSO 8 - Verifica/installazione di WebView2 Runtime
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Verifica del componente WebView2 Runtime (necessario per le mappe)"

# Windows 11 lo include gia' di serie; su Windows 10 potrebbe mancare.
# Il bootstrapper ufficiale Microsoft e' "intelligente": se il runtime e'
# gia' presente ed aggiornato, non fa nulla e termina subito senza danni.
# Per questo lo eseguiamo sempre, invece di provare ad indovinare se serve.
try {
    $bootstrapperPath = Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe"

    if (Ottieni-File -PercorsoOffline $OfflineWebView2Exe -Url $WebView2BootstrapperUrl -Destinazione $bootstrapperPath -Descrizione "WebView2 Runtime Bootstrapper") {
        Scrivi-Log -Messaggio "Verifica/installazione di WebView2 Runtime in corso. La procedura puo' richiedere qualche minuto, e' normale: attendere senza chiudere la finestra..." -Livello "INFO"
        $processo = Start-Process -FilePath $bootstrapperPath -ArgumentList "/silent", "/install" -Wait -PassThru -ErrorAction Stop

        if ($processo.ExitCode -eq 0) {
            Scrivi-Log -Messaggio "WebView2 Runtime verificato/installato correttamente." -Livello "OK"
        } else {
            Scrivi-Log -Messaggio "WebView2 Runtime: codice di uscita $($processo.ExitCode) (potrebbe essere gia' presente ed aggiornato)." -Livello "WARN"
        }

        Remove-Item -Path $bootstrapperPath -Force -ErrorAction SilentlyContinue
    }
} catch {
    Segnala-Errore -Componente "WebView2 Runtime" -Dettaglio "$($_.Exception.Message). Puoi installarlo manualmente da: $WebView2PaginaManuale"
}


# ----------------------------------------------------------------------------
# PASSO 9 - Copia di main.py e dell'icona nella cartella principale
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Copia del programma principale (main.py) e dell'icona"

$mainPyOrigine = Join-Path $SourceDir "main.py"
$iconaOrigine  = Join-Path $SourceDir "skytruth.ico"
$mainPyDestinazione = Join-Path $BaseDir "main.py"
$iconaDestinazione  = Join-Path $BaseDir "skytruth.ico"

if (Test-Path $mainPyOrigine) {
    try {
        Copy-Item -Path $mainPyOrigine -Destination $mainPyDestinazione -Force -ErrorAction Stop
        Scrivi-Log -Messaggio "main.py copiato in $BaseDir." -Livello "OK"
    } catch {
        Segnala-Errore -Componente "main.py" -Dettaglio $_.Exception.Message
    }
} else {
    Segnala-Errore -Componente "main.py" -Dettaglio "File non trovato accanto all'installer (atteso in: $mainPyOrigine)."
}

if (Test-Path $iconaOrigine) {
    try {
        Copy-Item -Path $iconaOrigine -Destination $iconaDestinazione -Force -ErrorAction Stop
        Scrivi-Log -Messaggio "Icona skytruth.ico copiata in $BaseDir." -Livello "OK"
    } catch {
        Scrivi-Log -Messaggio "Impossibile copiare l'icona: $($_.Exception.Message)" -Livello "WARN"
    }
} else {
    Scrivi-Log -Messaggio "Icona skytruth.ico non trovata accanto all'installer: il collegamento sul Desktop usera' l'icona predefinita." -Livello "WARN"
}


# ----------------------------------------------------------------------------
# PASSO 10 - Creazione del collegamento sul Desktop
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Creazione del collegamento sul Desktop"

try {
    $percorsoDesktop = [Environment]::GetFolderPath("Desktop")
    $percorsoCollegamento = Join-Path $percorsoDesktop "SkyTruth.lnk"

    # Usiamo pythonw.exe (senza console) per un avvio pulito, in stile "app".
    $lanciatore = if (Test-Path $pythonwExe) { $pythonwExe } else { $pythonExe }

    $wshShell = New-Object -ComObject WScript.Shell
    $collegamento = $wshShell.CreateShortcut($percorsoCollegamento)
    $collegamento.TargetPath = $lanciatore
    $collegamento.Arguments = "`"$mainPyDestinazione`""
    $collegamento.WorkingDirectory = $BaseDir
    if (Test-Path $iconaDestinazione) {
        $collegamento.IconLocation = $iconaDestinazione
    }
    $collegamento.Description = "SkyTruth - Mainstream Fly vs Ghost Fly"
    $collegamento.Save()

    Scrivi-Log -Messaggio "Collegamento creato sul Desktop: $percorsoCollegamento" -Livello "OK"
} catch {
    Segnala-Errore -Componente "Collegamento Desktop" -Dettaglio $_.Exception.Message
}


# ----------------------------------------------------------------------------
# PASSO 11 - Verifica di PowerShell (per coerenza con i controlli di main.py)
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Verifica finale di PowerShell"

# Se siamo arrivati fin qui, ovviamente PowerShell funziona: questo passo
# esiste solo per completezza e coerenza con quanto verificato anche da
# main.py stesso ad ogni avvio (rilevamento dongle/webcam).
Scrivi-Log -Messaggio "PowerShell funzionante (stiamo eseguendo questo stesso script)." -Livello "OK"


# ----------------------------------------------------------------------------
# PASSO 12 - Pulizia file temporanei di installazione
# ----------------------------------------------------------------------------
Scrivi-Passo -Descrizione "Pulizia dei file temporanei di installazione"

$fileTemporaneiInstaller = @(
    (Join-Path $env:TEMP "python_embed_skytruth.zip"),
    (Join-Path $env:TEMP "python_full_skytruth.exe"),
    (Join-Path $env:TEMP "skytruth_python_full_temp"),
    (Join-Path $env:TEMP "dump1090_skytruth.zip"),
    (Join-Path $env:TEMP "ffmpeg_skytruth.zip"),
    (Join-Path $env:TEMP "dump1090_skytruth_estratto"),
    (Join-Path $env:TEMP "ffmpeg_skytruth_estratto"),
    (Join-Path $env:TEMP "MicrosoftEdgeWebview2Setup.exe")
)

foreach ($elemento in $fileTemporaneiInstaller) {
    if (Test-Path $elemento) {
        try {
            Remove-Item -Path $elemento -Recurse -Force -ErrorAction SilentlyContinue
        } catch {
            # Non critico: un residuo in %TEMP% non impedisce a SkyTruth di funzionare.
        }
    }
}

Scrivi-Log -Messaggio "Pulizia dei file temporanei completata." -Livello "OK"

# ----------------------------------------------------------------------------
# Fine del blocco try generale (Passi 2-11).
# ----------------------------------------------------------------------------
} catch {
    # Rete di sicurezza: qualsiasi errore imprevisto non gestito nei
    # singoli passaggi finisce qui, invece di far chiudere PowerShell
    # bruscamente. Lo registriamo chiaramente e passiamo comunque al
    # riepilogo finale (nel blocco 'finally' sotto).
    Write-Host ""
    Write-Host "ERRORE IMPREVISTO DURANTE L'INSTALLAZIONE" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Scrivi-Log -Messaggio "ERRORE IMPREVISTO: $($_.Exception.Message)" -Livello "ERROR"
    Scrivi-Log -Messaggio $_.ScriptStackTrace -Livello "ERROR"
    $script:ErroriRiepilogo += "Errore imprevisto: $($_.Exception.Message)"
} finally {

# ----------------------------------------------------------------------------
# PASSO 13 - Riepilogo finale
# ----------------------------------------------------------------------------
# Questo passo viene mostrato SEMPRE, anche se l'installazione si e'
# interrotta prima del previsto per un errore imprevisto: l'utente deve
# sapere comunque a che punto si e' fermata e dove trovare il log.
Scrivi-Passo -Descrizione "Riepilogo finale"

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "   Installazione completata"                                        -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "SkyTruth e' stato installato in: $BaseDir"
Write-Host "Un collegamento e' stato creato sul Desktop."
Write-Host "Log completo dell'installazione:"
Write-Host "  $LogFile" -ForegroundColor Yellow
Write-Host ""

if ($script:ErroriRiepilogo.Count -eq 0) {
    Write-Host "Nessun errore rilevato durante l'installazione." -ForegroundColor Green
} else {
    Write-Host "Attenzione: si sono verificati $($script:ErroriRiepilogo.Count) problema/i:" -ForegroundColor Yellow
    foreach ($errore in $script:ErroriRiepilogo) {
        Write-Host "  - $errore" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "Controlla il file di log per i dettagli completi. SkyTruth potrebbe" -ForegroundColor Yellow
    Write-Host "comunque funzionare correttamente se i problemi riguardano solo" -ForegroundColor Yellow
    Write-Host "componenti opzionali." -ForegroundColor Yellow
}

Scrivi-Log -Messaggio "=== Installazione terminata (errori: $($script:ErroriRiepilogo.Count)) ===" -Livello "INFO"

Write-Host ""
Write-Host "Premi INVIO per chiudere questa finestra..."
Read-Host | Out-Null

} # fine del blocco finally
