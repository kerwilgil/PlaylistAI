# -*- mode: python ; coding: utf-8 -*-

import os


project_root = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(project_root, "desktop.py")],
    pathex=[project_root],
    binaries=[],
    datas=[
        (os.path.join(project_root, "templates"), "templates"),
        (os.path.join(project_root, ".env.example"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PlaylistAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
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
    name="PlaylistAI",
)
app = BUNDLE(
    coll,
    name="PlaylistAI.app",
    icon=os.path.join(project_root, "assets", "playlistai-icon.icns"),
    bundle_identifier="app.playlistai.desktop",
    info_plist={
        "CFBundleDisplayName": "PlaylistAI",
        "CFBundleName": "PlaylistAI",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
)
