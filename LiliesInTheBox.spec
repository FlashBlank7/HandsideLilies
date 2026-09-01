# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

import shiboken6


python_dll_root = Path(sys.base_prefix) / 'DLLs'
shiboken_root = Path(shiboken6.__file__).resolve().parent
version_resource = Path(SPECPATH) / 'packaging' / 'windows_version_info.txt'
if not version_resource.is_file():
    raise FileNotFoundError(f'Windows version resource is missing: {version_resource}')
# PyInstaller otherwise follows the ambient PATH and can accidentally pair
# Python's ``_ssl.pyd`` with an unrelated MySQL/Git OpenSSL build.
os.environ['PATH'] = str(python_dll_root) + os.pathsep + os.environ.get('PATH', '')


a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=[
        (str(shiboken_root / 'shiboken6.abi3.dll'), 'PySide6'),
        (str(python_dll_root / 'libssl-3-x64.dll'), '.'),
        (str(python_dll_root / 'libcrypto-3-x64.dll'), '.'),
    ],
    datas=[('qml', 'qml'), ('themes', 'themes'), ('assets', 'assets'), ('src/lilies/local_model_worker.py', 'runtime')],
    hiddenimports=[
        'PySide6.QtMultimedia',
        'PySide6.QtQuickControls2',
        'cryptography.hazmat.primitives.ciphers.aead',
        'tzdata',
    ],
    hookspath=[str(Path(SPECPATH) / 'packaging' / 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt's broad plugin categories include development-only QML debugger tooling
# and the PDF image-format plugin even though Lilies imports neither feature.
# Their PE dependencies reintroduce QtQuick3D/Pdf after the QML allowlist has
# already succeeded, so prune the exact plugin surfaces and their now-orphaned
# DLLs from the collected TOC.  The packaged resource probe independently
# rescans the final dist and fails closed if any forbidden family remains.
def keep_lilies_runtime_binary(entry):
    destination = str(entry[0]).replace('\\', '/').casefold()
    if destination.startswith('pyside6/plugins/qmltooling/'):
        return False
    return destination not in {
        'pyside6/plugins/imageformats/qpdf.dll',
        'pyside6/qt6pdf.dll',
        'pyside6/qt6quick3dutils.dll',
    }


a.binaries = [entry for entry in a.binaries if keep_lilies_runtime_binary(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LiliesInTheBox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    version=str(version_resource),
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LiliesInTheBox',
)
