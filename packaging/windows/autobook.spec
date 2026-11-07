# PyInstaller: the Windows app as a folder (Velopack packs and updates folders, not single files).
#   uv run --with pyinstaller pyinstaller --noconfirm packaging/windows/autobook.spec
# Two programs share the folder: autobook.exe (no console; started by the installer's shortcuts)
# and autobook-cli.exe (the command line). Fotobuch is copied in afterwards (get_fotobuch.ps1).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

HERE = Path(SPECPATH)
ICON = str(HERE / "autobook.ico")

datas = (collect_data_files("autobook", excludes=["**/__pycache__/**"])
         + collect_data_files("reverse_geocoder")  # the GeoNames city list
         + collect_data_files("pypdfium2") + collect_data_files("pypdfium2_raw")
         + copy_metadata("autobook")  # the version shown in the app and used for updates
         + copy_metadata("keyring"))  # keyring finds its backends through package metadata
binaries = collect_dynamic_libs("pypdfium2_raw") + collect_dynamic_libs("pillow_heif")
hidden = (collect_submodules("autobook") + collect_submodules("uvicorn")
          + ["keyring.backends.Windows", "pystray._win32", "velopack"])


def analysis(script):
    return Analysis([str(HERE / script)], datas=datas, binaries=binaries, hiddenimports=hidden,
                    excludes=["tkinter", "pytest"], noarchive=False)


app, cli = analysis("app_main.py"), analysis("cli_main.py")
app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="autobook", console=False, icon=ICON)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="autobook-cli", console=True, icon=ICON)
COLLECT(app_exe, app.binaries, app.datas, cli_exe, cli.binaries, cli.datas, name="autobook")
