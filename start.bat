@echo off
REM Startet KeyTool direkt aus dem Quellcode (richtet beim ersten Start eine virtuelle Umgebung ein)
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :error
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt || goto :error
) else (
    call .venv\Scripts\activate.bat
)
start "" pythonw -m keytool %*
exit /b 0
:error
echo Installation fehlgeschlagen.
pause
exit /b 1
