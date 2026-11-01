# -*- mode: python ; coding: utf-8 -*-
# 第二大脑 桌面版打包配置（onedir）
#
# 用法：python -m PyInstaller --noconfirm --clean second-brain-app.spec
#
# 为什么用 onedir 而不是 onefile 单文件：
#   索引每个 PDF 都要开一个子进程解析（防死锁，见 brain.extract_text_safe）。
#   单文件 exe 每次启动都要把几百 MB 解压到临时目录，实测单次 5~8 秒，
#   一千个 PDF 就是两个小时起步。onedir 常驻解包目录，启动约 1 秒。
#
# 产物：dist/SecondBrain/SecondBrain.exe + _internal/
#   把整个 SecondBrain 文件夹放到 brain.db 旁边即可，数据库就在 exe 同目录。
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ('rapidocr_onnxruntime',  # 离线 OCR（含 onnx 模型）
            'pymupdf',               # 扫描版 PDF 渲染
            'webview',               # 桌面窗口
            'pythonnet',             # pywebview 的 Windows 后端
            'clr_loader'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += ['PIL.Image', 'cv2', 'numpy', 'scan_docs', 'selftest']

# 本机装了一堆和本项目无关的重型包，PyInstaller 顺着 import 链会把 torch 整包拖进来
# （实测多出 2GB 以上）。这里显式排除，产物只保留 OCR 真正需要的部分。
EXCLUDES = [
    'torch', 'torchvision', 'torchaudio', 'torchtext',
    'tensorflow', 'keras', 'jax', 'sklearn', 'scipy', 'pandas',
    'matplotlib', 'IPython', 'notebook', 'jupyter', 'pytest',
    'tkinter', 'PySide2', 'PyQt5', 'PyQt6', 'pyqtgraph',
    'sphinx', 'docutils', 'setuptools._distutils',
]

a = Analysis(
    ['app_window.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SecondBrain',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SecondBrain',
)
