@echo off
REM ============================================================================
REM SkyTruth - install.bat
REM Avvia install.ps1 con doppio click, senza dover aprire manualmente
REM PowerShell. Verifica prima che PowerShell sia disponibile sul sistema
REM (e' incluso di serie in Windows 10/11, ma controlliamo comunque).
REM La richiesta dei permessi di amministratore viene gestita direttamente
REM da install.ps1.
REM ============================================================================

title SkyTruth - Installazione

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   ERRORE: PowerShell non e' stato trovato su questo sistema.
    echo.
    echo   PowerShell e' incluso di serie in ogni installazione
    echo   standard di Windows 10 e Windows 11: se manca, il tuo
    echo   sistema potrebbe non essere aggiornato correttamente.
    echo.
    echo   Verifica gli aggiornamenti di Windows, oppure scarica
    echo   PowerShell manualmente da:
    echo   https://aka.ms/powershell-release?tag=stable
    echo ============================================================
    echo.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

pause
