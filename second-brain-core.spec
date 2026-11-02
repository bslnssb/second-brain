# -*- mode: python ; coding: utf-8 -*-
# 第二大脑 桌面版打包配置（core，不带 OCR，体积小，onedir）
#
# 用法：python -m PyInstaller --noconfirm --clean --distpath dist-core \
#           --workpath build-core second-brain-core.spec
#
# 与完整版（second-brain-app.spec）的差别：
#   - 不打包 rapidocr_onnxruntime / pymupdf / onnxruntime / cv2
#   - 扫描版 PDF 提不出文字（记为「无文本」），普通文本型 PDF 走 pypdf 正常解析
#   - 产物体积约为完整版的七分之一
#
# 为什么同样是 onedir 而不是 onefile：
#   索引时每个 PDF 都开一个子进程解析（见 brain.extract_text_safe，防 GIL 死锁）。
#   打包后子进程用 sys.executable 启动，若是单文件版，每篇 PDF 都要重新解压整个
#   程序包（实测 2~8 秒），一千篇 PDF 要多花一小时以上。onedir 解包目录常驻，
#   子进程启动约 0.3 秒。
#
# 产物：dist-core/SecondBrain-Core/SecondBrain-Core.exe + _internal/
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ('webview',               # 桌面窗口
            'pythonnet',             # pywebview 的 Windows 后端
            'clr_loader'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 托盘图标要读 ico 文件；窗口图标由 exe 资源自带，这里补一份给托盘用
datas += [('assets/icon.ico', 'assets')]

# hotkey / tray 在函数里 import（进程起来才装钩子和托盘），显式列出避免漏收
hiddenimports += ['pypdf', 'scan_docs', 'selftest', 'hotkey', 'tray',
                  'win32gui', 'win32api', 'win32con', 'win32process']

# 不打包 OCR 链路，同时挡掉 PyInstaller 顺着本机环境误收的重型包
EXCLUDES = [
    'rapidocr_onnxruntime', 'onnxruntime', 'pymupdf', 'fitz',
    'cv2', 'pyclipper', 'shapely', 'numpy',
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
    name='SecondBrain-Core',
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
    name='SecondBrain-Core',
)
