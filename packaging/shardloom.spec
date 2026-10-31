# PyInstaller build: pyinstaller packaging/shardloom.spec  ->  dist/Shardloom/
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hidden = [], [], []
for package in ("imgui_bundle", "av", "miniaudio", "moderngl", "glcontext"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hidden += h
hidden += collect_submodules("glcontext") + ["_miniaudio"]

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "gi"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shardloom",
    console=True,  # the command line works; a double-click hides the console (cli.main)
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Shardloom")
