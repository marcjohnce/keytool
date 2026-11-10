@echo off
REM Builds dist\KeyTool\KeyTool.exe and dist\KeyTool\KeyTool-MCP.exe
REM Requires Python 3.10 - 3.13 (64 bit) from python.org
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
echo Done: dist\KeyTool\KeyTool.exe
echo MCP server: dist\KeyTool\KeyTool-MCP.exe
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
