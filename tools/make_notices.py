# -*- coding: utf-8 -*-
"""第 380 项：生成第三方许可清单与许可原文目录。

为什么要有这脚本：
    Apache-2.0 / MIT / BSD 都要求「随分发附许可原文 + 保留归属声明」，而
    PyInstaller 只搬包体、不搬 dist-info/licenses，装机版里一份 LICENSE 都没有
    （实测 _internal\\rapidocr_onnxruntime-1.4.4.dist-info 只有 METADATA/RECORD）。
    手抄许可证原文既容易抄错、发版时又会忘，所以统一从已安装的包体里取原文。

用法（在 上传的干净版 根目录）：
    python tools/make_notices.py
产出：
    THIRD-PARTY-NOTICES.md     组件清单（版本 / 许可 / 上游地址）
    licenses/*.txt|md          各组件许可原文（随包进 _internal\\licenses）
"""
from __future__ import annotations

import importlib.metadata as md
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # tools/
ROOT = HERE.parent                              # 上传的干净版
# 开发根才是唯一维护地：清单生成到那里，构建前由 tools/sync_public.py 同步过来
DEV = ROOT.parent / '开发测试版'
OUT = DEV / 'licenses'

# (显示名, dist 名, 期望许可, 上游地址, 说明)
COMPONENTS = [
    ('RapidOCR', 'rapidocr', 'Apache-2.0', 'https://github.com/RapidAI/RapidOCR',
     '离线 OCR 引擎（本产品 OCR 能力由其驱动）'),
    ('PP-OCRv6 检测/识别模型', None, 'Apache-2.0', 'https://github.com/PaddlePaddle/PaddleOCR',
     '由 RapidOCR 随包内置，源自 PaddleOCR（百度飞桨）；模型工件见 licenses/rapidocr-MODEL_LICENSES.md'),
    ('ONNX Runtime', 'onnxruntime', 'MIT', 'https://github.com/microsoft/onnxruntime',
     '模型推理运行时'),
    ('OmegaConf', 'omegaconf', 'BSD-3-Clause', 'https://github.com/omry/omegaconf',
     'RapidOCR 3.x 的配置解析'),
    ('colorlog', 'colorlog', 'MIT', 'https://github.com/borntyping/python-colorlog',
     'RapidOCR 3.x 的日志着色'),
    ('Shapely', 'shapely', 'BSD-3-Clause', 'https://github.com/shapely/shapely',
     'OCR 文本框几何运算'),
    ('pyclipper', 'pyclipper', 'MIT', 'https://github.com/fonttools/pyclipper',
     'OCR 文本框多边形裁剪'),
    ('OpenCV (opencv-python)', 'opencv-python', 'Apache-2.0', 'https://github.com/opencv/opencv-python',
     '图像预处理与截图识别'),
    ('NumPy', 'numpy', 'BSD-3-Clause', 'https://numpy.org',
     '数组运算'),
    ('Pillow', 'pillow', 'MIT-CMU (HPND)', 'https://python-pillow.org',
     '图像读写'),
    ('PyYAML', 'PyYAML', 'MIT', 'https://pyyaml.org',
     'RapidOCR 配置读取'),
    ('six', 'six', 'MIT', 'https://github.com/benjaminp/six',
     'RapidOCR 兼容层'),
    ('tqdm', 'tqdm', 'MPL-2.0 / MIT', 'https://github.com/tqdm/tqdm',
     'RapidOCR 进度条（仅源码可用；打包版未启用进度显示）'),
    ('requests', 'requests', 'Apache-2.0', 'https://requests.readthedocs.io',
     'RapidOCR 依赖（本产品运行期不外发请求）'),
    ('pywebview', 'pywebview', 'BSD-3-Clause', 'https://pywebview.flowrl.com',
     '桌面窗口（WebView2 前端外壳）'),
    ('pythonnet', 'pythonnet', 'MIT', 'https://pythonnet.github.io',
     'pywebview 的 Windows 后端'),
    ('clr-loader', 'clr-loader', 'MIT', 'https://github.com/pythonnet/clr-loader',
     'pythonnet 的运行时加载'),
    ('pypdfium2', 'pypdfium2', 'BSD-3-Clause / Apache-2.0', 'https://github.com/pypdfium2-team/pypdfium2',
     'PDF 渲染与页数（替代 PyMuPDF）'),
    ('PDFium', None, 'BSD-3-Clause', 'https://pdfium.googlesource.com/pdfium',
     'pypdfium2 内置的 PDF 引擎（含 Chromium 第三方组件，见 pypdfium2 许可目录）'),
    ('pypinyin', 'pypinyin', 'MIT', 'https://github.com/mozillazg/python-pinyin',
     '拼音检索'),
    ('zstandard', 'zstandard', 'BSD-3-Clause', 'https://github.com/indygreg/python-zstandard',
     '正文压缩存储'),
    ('pywin32', 'pywin32', 'PSF-2.0', 'https://github.com/mhammond/pywin32',
     'Win32 集成（钩子/托盘/剪贴板）'),
    ('JSZip', None, 'MIT', 'https://stuk.github.io/jszip/',
     '前端解析 docx/xlsx（vendor/jszip.min.js）'),
    ('docx-preview', None, 'Apache-2.0', 'https://github.com/VolodymyrBaydalka/docxjs',
     'docx 版面预览（vendor/docx-preview.min.js）'),
    ('SheetJS (xlsx)', None, 'Apache-2.0', 'https://sheetjs.com',
     'xlsx 版面预览（vendor/xlsx.mini.min.js）'),
]

# 需要从已安装包体里原样拷出的原文（显示名, dist, 包内路径, 目标文件名）
COPY = [
    ('Apache-2.0', 'pypdfium2', 'licenses/BUILD_LICENSES/SHARED/Apache-2.0.txt', 'Apache-2.0.txt'),
    ('MIT', 'onnxruntime', 'LICENSE', 'MIT.txt'),
    ('BSD-3-Clause', 'pywebview', 'licenses/LICENSE', 'BSD-3-Clause.txt'),
    ('Pillow (HPND/MIT-CMU)', 'pillow', 'licenses/LICENSE', 'Pillow-LICENSE.txt'),
    ('PSF-2.0', 'pywin32', 'pythonwin/License.txt', 'PSF-2.0.txt'),
    ('PyYAML', 'PyYAML', None, 'PyYAML-LICENSE.txt'),
    ('six', 'six', None, 'six-LICENSE.txt'),
    ('tqdm', 'tqdm', 'licenses/LICENCE', 'tqdm-LICENSE.txt'),
    ('requests', 'requests', None, 'requests-LICENSE.txt'),
    ('OmegaConf (BSD-3)', 'omegaconf', 'licenses/LICENSE', 'omegaconf-LICENSE.txt'),
    ('Shapely (BSD-3)', 'shapely', 'licenses/LICENSE.txt', 'shapely-LICENSE.txt'),
    ('pyclipper (MIT)', 'pyclipper', 'licenses/LICENSE', 'pyclipper-LICENSE.txt'),
    ('RapidOCR (Apache-2.0)', 'rapidocr', 'licenses/LICENSE', 'rapidocr-LICENSE.txt'),
    ('RapidOCR 模型许可说明', 'rapidocr', 'licenses/MODEL_LICENSES.md', 'rapidocr-MODEL_LICENSES.md'),
    ('ONNX Runtime 第三方声明', 'onnxruntime', 'ThirdPartyNotices.txt', 'onnxruntime-ThirdPartyNotices.txt'),
    ('pypdfium2 许可与第三方清单', 'pypdfium2', 'licenses/BUILD_LICENSES', 'pypdfium2-BUILD_LICENSES'),
]


def _dist_file(dist_name, rel):
    d = md.distribution(dist_name)
    for f in d.files or []:
        parts = str(f).replace('\\', '/')
        # dist-info 前缀各家不一：按「以 rel 结尾」匹配
        if parts.endswith(rel):
            return Path(d.locate_file(f))
    return None


def _version(dist_name):
    try:
        return md.version(dist_name)
    except Exception:
        return '随源码内置'


def main():
    OUT.mkdir(exist_ok=True)
    copied, missing = [], []
    for label, dist, rel, dst in COPY:
        src = None
        if rel:
            src = _dist_file(dist, rel)
        else:
            src = _dist_file(dist, 'LICENSE') or _dist_file(dist, 'LICENSE.txt') \
                or _dist_file(dist, 'LICENSE.md')
        if src is None and rel:
            # 目录型条目（pypdfium2 的 BUILD_LICENSES/）：按单文件匹配不到，
            # 改为把该目录下的每个文件按相对路径逐个拷到目标目录
            d = md.distribution(dist)
            matched = [f for f in (d.files or []) if rel in str(f).replace('\\', '/')]
            if matched:
                base = OUT / dst
                if base.exists():
                    shutil.rmtree(base)
                for f in matched:
                    p = Path(d.locate_file(f))
                    tail = str(f).replace('\\', '/').split(rel + '/', 1)[-1]
                    target = base / tail
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, target)
                copied.append(dst + '/（%d 份）' % len(matched))
                continue
        if src and Path(src).exists():
            target = OUT / dst
            if Path(src).is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
            copied.append(dst)
        else:
            missing.append(f'{label} ({dist})')

    lines = [
        '# Memex 第三方开源组件声明',
        '',
        '本产品包含以下第三方开源组件。各组件的完整许可原文随安装包提供，',
        '位于安装目录的 `licenses\\` 目录（即 `Memex\\_internal\\licenses\\`）。',
        '',
        '| 组件 | 版本 | 许可 | 用途 | 上游 |',
        '| --- | --- | --- | --- | --- |',
    ]
    for name, dist, lic, url, note in COMPONENTS:
        ver = _version(dist) if dist else '—'
        lines.append(f'| {name} | {ver} | {lic} | {note} | {url} |')
    lines += [
        '',
        '## 说明',
        '',
        '- OCR 模型（PP-OCRv6 det/rec small）由 PaddleOCR 项目以 Apache-2.0 发布，',
        '  经 RapidOCR 打包为 ONNX 工件后随本产品分发；上游对训练语料的版权不作担保。',
        '- 上述许可均**不授予商标使用权**：本产品不使用 PaddleOCR / PaddlePaddle / RapidOCR',
        '  等项目的名称、标识为自身背书。',
        '- 模型工件身份以文件名 + SHA-256 固定（见 `About → 开源组件` 页），避免与',
        '  其他同名工件混淆。',
        '- 本文件与 `licenses/` 由 `tools/make_notices.py` 从已安装包体自动生成，',
        '  版本升级后请重新运行。',
        '',
    ]
    (DEV / 'THIRD-PARTY-NOTICES.md').write_text('\n'.join(lines), encoding='utf-8')
    print('写出 THIRD-PARTY-NOTICES.md：%d 个组件' % len(COMPONENTS))
    print('许可原文：%d 份' % len(copied))
    if missing:
        print('缺原文（需人工补）：' + '；'.join(missing))
    return 0


if __name__ == '__main__':
    sys.exit(main())
