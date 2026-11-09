@echo off
REM Erstellt dist\KeyTool\KeyTool.exe und dist\KeyTool\KeyTool-MCP.exe
REM Voraussetzung: Python 3.10 - 3.13 (64 Bit) von python.org
setlocal
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
python -m pip install --upgrade pip || goto :error
python -m pip install -r requirements.txt pyinstaller || goto :error
pyinstaller packaging\KeyTool.spec --noconfirm --clean || goto :error
echo.
echo Fertig: dist\KeyTool\KeyTool.exe
echo MCP-Server: dist\KeyTool\KeyTool-MCP.exe
pause
exit /b 0
:error
echo Build fehlgeschlagen.
pause
exit /b 1
