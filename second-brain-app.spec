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

# 托盘图标要读 ico 文件；窗口图标由 exe 资源自带，这里补一份给托盘用
datas += [('assets/icon.ico', 'assets')]

# hotkey / tray 是在函数里 import 的（进程起来才装钩子和托盘），
# 显式列出来省得 PyInstaller 漏掉；win32* 由 pywin32 自带的 hook 收集。
hiddenimports += ['PIL.Image', 'cv2', 'numpy', 'scan_docs', 'selftest',
                  'hotkey', 'tray',
                  'win32gui', 'win32api', 'win32con', 'win32process']

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

# 裁掉运行期用不到的大块文件（合计约 37MB）：
#   pymupdf/mupdf-devel —— C 开发头文件（6.7MB），运行期无用
#   opencv_videoio_ffmpeg*.dll —— OpenCV 的视频解码后端（30MB），
#                          这里只拿 cv2 做图像预处理，从不读视频
#
# 注意：pymupdf/layout（51MB）**不能删**。看着像可选模块，但它的 __init__.py
# 在被导入时就调 activate() 并把 49MB 版面模型载进内存，缺了它 `import pymupdf`
# 直接失败，扫描版 PDF 全部退化成「无文本」。实测删掉后自检 B/C/E 直接跳过。
DROP = ('pymupdf/mupdf-devel/', 'opencv_videoio_ffmpeg')


def _keep(entry):
    name = entry[0].replace('\\', '/').lower()
    return not any(k in name for k in DROP)


a.datas = [d for d in a.datas if _keep(d)]
a.binaries = [b for b in a.binaries if _keep(b)]

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
