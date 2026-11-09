@echo off
REM Startet KeyTool direkt aus dem Quellcode.
REM Beim ersten Start wird eine virtuelle Umgebung eingerichtet; wenn sich requirements.txt
REM geaendert hat (neue Funktionen/Pakete), werden die Pakete automatisch nachinstalliert.
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
    echo Installiere bzw. aktualisiere Pakete ...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt || goto :error
    copy /y requirements.txt .venv\requirements.installed >nul
)
start "" pythonw -m keytool %*
exit /b 0
:error
echo Installation fehlgeschlagen.
pause
exit /b 1
