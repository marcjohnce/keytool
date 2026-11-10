@echo off
REM Starts KeyTool directly from the source code.
REM The first start sets up a virtual environment; whenever requirements.txt has changed
REM (new features/packages), the packages are installed again automatically.
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
    echo Installing / updating packages ...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt || goto :error
    copy /y requirements.txt .venv\requirements.installed >nul
)
start "" pythonw -m keytool %*
exit /b 0
:error
echo Installation failed.
pause
exit /b 1
