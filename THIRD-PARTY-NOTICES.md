# Memex 第三方开源组件声明

本产品包含以下第三方开源组件。各组件的完整许可原文随安装包提供，
位于安装目录的 `licenses\` 目录（即 `Memex\_internal\licenses\`）。

| 组件 | 版本 | 许可 | 用途 | 上游 |
| --- | --- | --- | --- | --- |
| RapidOCR | 3.10.0 | Apache-2.0 | 离线 OCR 引擎（本产品 OCR 能力由其驱动） | https://github.com/RapidAI/RapidOCR |
| PP-OCRv6 检测/识别模型 | — | Apache-2.0 | 由 RapidOCR 随包内置，源自 PaddleOCR（百度飞桨）；模型工件见 licenses/rapidocr-MODEL_LICENSES.md | https://github.com/PaddlePaddle/PaddleOCR |
| ONNX Runtime | 1.31.0 | MIT | 模型推理运行时 | https://github.com/microsoft/onnxruntime |
| OmegaConf | 2.4.0 | BSD-3-Clause | RapidOCR 3.x 的配置解析 | https://github.com/omry/omegaconf |
| colorlog | 6.12.0 | MIT | RapidOCR 3.x 的日志着色 | https://github.com/borntyping/python-colorlog |
| Shapely | 2.2.0 | BSD-3-Clause | OCR 文本框几何运算 | https://github.com/shapely/shapely |
| pyclipper | 1.4.0 | MIT | OCR 文本框多边形裁剪 | https://github.com/fonttools/pyclipper |
| OpenCV (opencv-python) | 5.0.0.93 | Apache-2.0 | 图像预处理与截图识别 | https://github.com/opencv/opencv-python |
| NumPy | 2.4.4 | BSD-3-Clause | 数组运算 | https://numpy.org |
| Pillow | 13.0.0 | MIT-CMU (HPND) | 图像读写 | https://python-pillow.org |
| PyYAML | 6.0.3 | MIT | RapidOCR 配置读取 | https://pyyaml.org |
| six | 1.17.0 | MIT | RapidOCR 兼容层 | https://github.com/benjaminp/six |
| tqdm | 4.67.3 | MPL-2.0 / MIT | RapidOCR 进度条（仅源码可用；打包版未启用进度显示） | https://github.com/tqdm/tqdm |
| requests | 2.33.1 | Apache-2.0 | RapidOCR 依赖（本产品运行期不外发请求） | https://requests.readthedocs.io |
| pywebview | 6.2.1 | BSD-3-Clause | 桌面窗口（WebView2 前端外壳） | https://pywebview.flowrl.com |
| pythonnet | 3.2.1 | MIT | pywebview 的 Windows 后端 | https://pythonnet.github.io |
| clr-loader | 0.3.1 | MIT | pythonnet 的运行时加载 | https://github.com/pythonnet/clr-loader |
| pypdfium2 | 5.15.2 | BSD-3-Clause / Apache-2.0 | PDF 渲染与页数（替代 PyMuPDF） | https://github.com/pypdfium2-team/pypdfium2 |
| PDFium | — | BSD-3-Clause | pypdfium2 内置的 PDF 引擎（含 Chromium 第三方组件，见 pypdfium2 许可目录） | https://pdfium.googlesource.com/pdfium |
| pypinyin | 0.55.0 | MIT | 拼音检索 | https://github.com/mozillazg/python-pinyin |
| zstandard | 0.25.0 | BSD-3-Clause | 正文压缩存储 | https://github.com/indygreg/python-zstandard |
| pywin32 | 312 | PSF-2.0 | Win32 集成（钩子/托盘/剪贴板） | https://github.com/mhammond/pywin32 |
| JSZip | — | MIT | 前端解析 docx/xlsx（vendor/jszip.min.js） | https://stuk.github.io/jszip/ |
| docx-preview | — | Apache-2.0 | docx 版面预览（vendor/docx-preview.min.js） | https://github.com/VolodymyrBaydalka/docxjs |
| SheetJS (xlsx) | — | Apache-2.0 | xlsx 版面预览（vendor/xlsx.full.min.js） | https://sheetjs.com |

## 说明

- OCR 模型（PP-OCRv6 det/rec small）由 PaddleOCR 项目以 Apache-2.0 发布，
  经 RapidOCR 打包为 ONNX 工件后随本产品分发；上游对训练语料的版权不作担保。
- 上述许可均**不授予商标使用权**：本产品不使用 PaddleOCR / PaddlePaddle / RapidOCR
  等项目的名称、标识为自身背书。
- 模型工件身份以文件名 + SHA-256 固定（见 `About → 开源组件` 页），避免与
  其他同名工件混淆。
- 本文件与 `licenses/` 由 `tools/make_notices.py` 从已安装包体自动生成，
  版本升级后请重新运行。
