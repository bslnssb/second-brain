# -*- mode: python ; coding: utf-8 -*-
# Memex 桌面版打包配置（onedir）
#
# 用法：python -m PyInstaller --noconfirm --clean second-brain-app.spec
#
# 为什么用 onedir 而不是 onefile 单文件：
#   索引每个 PDF 都要开一个子进程解析（防死锁，见 brain.extract_text_safe）。
#   单文件 exe 每次启动都要把几百 MB 解压到临时目录，实测单次 5~8 秒，
#   一千个 PDF 就是两个小时起步。onedir 常驻解包目录，启动约 1 秒。
#
# 产物：dist/Memex/Memex.exe + _internal/
#   把整个 Memex 文件夹放到 brain.db 旁边即可，数据库就在 exe 同目录。
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

for pkg in ('rapidocr',              # 第 380 项：离线 OCR 引擎（3.x，含 PP-OCRv6 小模型）
            'omegaconf',             # 3.x 的配置解析（yaml → OmegaConf）
            'pypdfium2',             # 第 352 项：PDF 渲染/页数/高亮（Apache-2.0，替代 AGPL 的 pymupdf）
            'webview',               # 桌面窗口
            'pythonnet',             # pywebview 的 Windows 后端
            'clr_loader',
            'tts'):                # 第 346 项：朗读（本机 SAPI，handler 里是延迟 import）
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# 第 346 项：tts 是仓库根目录的单文件模块（brain 里延迟 import），collect_all 收不到它，
# 必须显式列进来 —— 否则装机版 /api/tts 直接回「No module named tts」。
hiddenimports.append('tts')
# 第 352 项：pdf_engine 是根目录单文件模块（brain/pro_api 里延迟 import），
# 与 tts 同一个坑，必须显式声明。
hiddenimports.append('pdf_engine')
# 体积评估 #3：正文 zstd 压缩，brain 里是延迟 import（老库 v1 不需要它），
# 不显式列出来装机版会在 doc_rec 一读就 ModuleNotFoundError。
hiddenimports += ['zstandard', 'zstandard.backend_c']

# 托盘图标要读 ico 文件；窗口图标由 exe 资源自带，这里补一份给托盘用
# 运行期要读的图片资源：图标（关于弹窗用 icon_preview，托盘/窗口用 icon.ico）
# 和赞赏码（donate.png / donate-6.png …）。
# 之前只打了 icon.ico，于是装完「关于」里没有二维码、图标也是通用文件图标 —— 用户实测发现的。
for _f in ('icon.ico', 'icon_preview.png', 'ball.png'):
    if Path('assets', _f).is_file():
        datas += [(str(Path('assets') / _f), 'assets')]
for _d in sorted(Path('assets').glob('donate*')):
    if _d.is_file():
        datas += [(str(_d), 'assets')]
# React 前端（web/dist）跟着走：运行期由 brain.web_ui_dir() 从 _internal/web 找 index.html。
# 没构建过就跳过，旧的 PAGE_TPL 界面照样能用。
_webdist = Path('web') / 'dist'
if _webdist.is_dir():
    datas += [(str(_webdist), 'web')]

# 第 380 项：开源许可合规 —— Apache-2.0/MIT/BSD 都要求「随分发附许可原文 + 归属声明」。
# PyInstaller 只搬包体不搬 dist-info/licenses，所以清单与原文由 tools/make_notices.py
# 生成后在这里显式打进 _internal（运行期 /api/licenses 读的就是它）。
if Path('THIRD-PARTY-NOTICES.md').is_file():
    datas += [('THIRD-PARTY-NOTICES.md', '.')]
if Path('licenses').is_dir():
    datas += [(str(Path('licenses')), 'licenses')]

# hotkey / tray 是在函数里 import 的（进程起来才装钩子和托盘），
# 显式列出来省得 PyInstaller 漏掉；win32* 由 pywin32 自带的 hook 收集。
# win32file 是 v0.9.47 实时索引用的（brain._watch_worker 里函数内 import），
# 不列出来 PyInstaller 不会收 —— 源码跑得好好的，exe 一启动监听就静默失败。
hiddenimports += ['PIL.Image', 'cv2', 'numpy', 'scan_docs', 'selftest', 'env_check', 'env_msgs',   # 体检文案的西/葡/印地语表（环境体检要五语言）
                  'plat', 'plat.shell', 'plat.paths', 'plat.win', 'doc_render',   # 第 382 项：平台抽象层（brain 静态 import，仍显式声明备用）
                  'ocr_engine',    # 第 380 项：OCR 统一入口（brain/snip_ocr/selftest 都走它）
                  'hotkey', 'tray', 'pypinyin', 'license_core', 'libsets',
                  'typewatch', 'quick_switch',   # 第 194/197 项：函数内 import 的 Win32 模块
                  'follow_click',  # 第 307 项：鼠标钩子（点资源管理器让对话框跟过去）
                  'audio_probe',   # 第 243 项：音频时长探测（brain 顶层 import，但列出来更保险）
                  'app_entries',   # 第 246 项：商店应用入口（Get-StartApps / shell:AppsFolder）
                  'snip_ocr',      # 屏幕截图识字（自主全屏遮罩 + RapidOCR）
                  'snip_overlay',  # 自主遮罩截图器 (WinForms)
                  'snip_card',     # 第 369 项：截图翻译的原位译文卡片 (WinForms)
                  'dev_override',  # 第 370 项：免打包调试通道（App\dev_override\*.py 顶替内置模块）
                  'floating_ball', # 桌面常驻悬浮球 (WinForms)

                  'error_report', 'settings_backup',  # 错误日志 / 设置备份
                  'translator', 'clipboard_mgr',     # 第 277 项：功能级埋点所在的模块
                  'uninstall',   # 第 296 项：卸载（函数内 import，漏了按钮点了没反应）
                  'sys_apps',    # 第 297 项：系统程序清单（设置/cmd…，查询时补结果）
                  'netproxy',    # 第 299 项：网络代理唯一出口（brain/translator 都 import）
                  'feature_metrics', 'breadcrumb',   # 第 277 项：指标表 / 面包屑（import 在 try 里，漏了会静默失效）
                  'pro', 'pro.pro_api', 'pro.ocr_batch', 'pro.xlsx_writer',  # Pro 私有包
                  'pythoncom', 'win32com.client',
                  'win32gui', 'win32api', 'win32con', 'win32process', 'win32file', 'win32clipboard']

# 本机装了一堆和本项目无关的重型包，PyInstaller 顺着 import 链会把 torch 整包拖进来
# （实测多出 2GB 以上）。这里显式排除，产物只保留 OCR 真正需要的部分。
EXCLUDES = [
    'torch', 'torchvision', 'torchaudio', 'torchtext',
    'tensorflow', 'keras', 'jax', 'sklearn', 'scipy', 'pandas',
    'matplotlib', 'IPython', 'notebook', 'jupyter', 'pytest',
    'tkinter', 'PySide2', 'PyQt5', 'PyQt6', 'pyqtgraph',
    'sphinx', 'docutils', 'setuptools._distutils',
    # 第 374 项 B/C：license_core 的 Ed25519 是纯 hashlib 实现，全仓没有一处 import
    # cryptography（9.4MB 的 _rust.pyd 纯属被别的包顺带拖进来）；Avif 解码插件也从没
    # 用过——截图翻译只走 PNG/BMP，PIL 的 avif 模块（7.5MB）一起排掉。
    'cryptography', 'PIL.AvifImagePlugin',
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
#
# 第 374 项：excludes 只管「模块」，扩展 .pyd 有时候会以二进制身份跟着进来，
# 所以这里再按文件名兜一手（合起来约 17MB）。
DROP = ('pymupdf/mupdf-devel/', 'opencv_videoio_ffmpeg',
        'cryptography/', 'pil/_avif')


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
    name='Memex',
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
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Memex',
)


