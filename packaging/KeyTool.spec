# PyInstaller spec – builds dist/KeyTool/ with two executables:
#   KeyTool.exe      GUI (no console window)
#   KeyTool-MCP.exe  MCP server (console/stdio, for Claude Desktop etc.)
# Run from the project root:  pyinstaller packaging/KeyTool.spec --noconfirm
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas, binaries, hidden = [], [], []
for pkg in ("librosa", "av", "mcp", "soundfile", "lazy_loader", "pooch", "pedalboard"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += collect_submodules("keytool") + collect_submodules("scipy.signal") + collect_submodules("sklearn.utils") + [
    "PySide6.QtMultimedia", "numba", "llvmlite", "mutagen", "pydantic", "anyio", "uvicorn", "starlette",
    # song sections (keytool/structure.py)
    "scipy.cluster.vq", "scipy.sparse.csgraph", "scipy.ndimage", "scipy.linalg",
]
datas += [(os.path.join(ROOT, "keytool", "resources"), "keytool/resources")]
excludes = ["tkinter", "matplotlib", "IPython", "pytest"]
ICON = os.path.join(SPECPATH, "icon.ico")


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[ROOT], binaries=binaries, datas=datas,
                    hiddenimports=hidden, excludes=excludes, noarchive=False)


a_gui = analysis("launch_gui.py")
a_mcp = analysis("launch_mcp.py")

exe_gui = EXE(PYZ(a_gui.pure), a_gui.scripts, [], exclude_binaries=True, name="KeyTool",
              console=False, icon=ICON, upx=False)
exe_mcp = EXE(PYZ(a_mcp.pure), a_mcp.scripts, [], exclude_binaries=True, name="KeyTool-MCP",
              console=True, icon=ICON, upx=False)

coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas,
               exe_mcp, a_mcp.binaries, a_mcp.datas,
               name="KeyTool", upx=False)
