# -*- mode: python ; coding: utf-8 -*-
# 第二大脑 打包配置（core 版，不带 OCR，体积小）
# 用法：python -m PyInstaller --noconfirm second-brain-core.spec
# 与 full 版的差别：不打包 rapidocr_onnxruntime / pymupdf，
# 扫描版 PDF 提不出文字（会记「无文本」），普通 PDF 仍用 pypdf 正常解析。
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

tmp_ret = collect_all('webview')                # 桌面窗口
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['app_window.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['rapidocr_onnxruntime', 'pymupdf', 'fitz', 'onnxruntime',
              'cv2', 'pyclipper', 'shapely'],
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
    name='second-brain-core',
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
