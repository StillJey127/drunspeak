# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['client.pyw'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.ico', '.'),
        ('icons', 'icons'),  # Копирует всю папку icons со всеми иконками внутрь сборки
        ('custom_icon_animations.py', '.'),
    ],
    hiddenimports=[
        'dxcam', 'comtypes', 'comtypes.client', 'custom_icon_animations',
        'sounddevice', '_cffi_backend', 'numpy', 'mss', 'PIL'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Оконный режим без черного терминала
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='client',
)