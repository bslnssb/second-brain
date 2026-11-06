# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['setup_app.py'],
    pathex=[],
    binaries=[],
    datas=[('payload.zip', '.'), ('logo64.png', '.'), ('app.ico', '.')],
    # winreg 在 set_autostart() 里是函数内 import，PyInstaller 静态分析容易漏；
    # 漏了安装包就不会写开机启动项，而且报错被 try 吞掉，极难发现。
    hiddenimports=['winreg'],
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
    a.binaries,
    a.datas,
    [],
    name='SecondBrain-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
