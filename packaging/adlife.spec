"""PyInstaller specification for the AdLife frozen binary.

Build natively on each target OS — never cross-compile:

    uv run pyinstaller packaging/adlife.spec --clean --noconfirm

One-directory output by default (fast iteration, `dist/adlife/adlife[.exe]`);
one-file output for releases by setting ADLIFE_RELEASE=1. The frozen application
contains no model weights and works in rules and mock modes without Ollama or any
network resource: every data file the product reads at runtime — package YAML
(routine and population templates, the demo project), the report Jinja template,
both stylesheets, and Textual/Plotly resources — is collected below.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

_datas = (
    collect_data_files("adlife")
    + collect_data_files("textual")
    + collect_data_files("plotly")
)

_hiddenimports = (
    # The CLI registers command modules lazily; freeze them explicitly.
    collect_submodules("adlife.cli.commands")
    + collect_submodules("adlife.core")
    + collect_submodules("adlife.adapters")
    + collect_submodules("adlife.reporting")
    + ["adlife.tui.app", "adlife.tui.widgets", "adlife.tui.controller", "adlife.tui.event_bus"]
)

one_file = os.environ.get("ADLIFE_RELEASE") == "1"

a = Analysis(
    ["../src/adlife/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas.tests", "pytest", "pyinstaller"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if one_file:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="adlife",
        exclude_binaries=False,
        console=True,
        upx=False,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="adlife",
        console=True,
        upx=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name="adlife",
    )
