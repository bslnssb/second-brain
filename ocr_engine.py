# -*- coding: utf-8 -*-
"""OCR 引擎统一入口（第 380 项）。

背景：原来各处直接 `from rapidocr_onnxruntime import RapidOCR` 并依赖其
`ocr(img) -> ([[box, text, score], ...], elapse)` 的旧契约。第 380 项把引擎
升到 RapidOCR 3.x + PP-OCRv6 small（Apache-2.0，模型随包内置、全离线），
3.x 的 API 变成 `engine(img) -> RapidOCROutput`（`.txts/.scores/.boxes`）。

为了不动 brain / snip_ocr / selftest / env_check 里既有的调用与排序逻辑，
这里做一个薄适配层：对外仍然是「可调用对象 → (旧格式列表, 耗时)」。

模型：PP-OCRv6 det/rec small（rapidocr 3.x wheel 里自带，不需要联网下载）。
选它的依据是 180DPI 下的本机实测：与 v4 同为 0.8~1.5s/页、峰值内存无差别，
印刷体页码召回比 v4 更全，且省掉一套 16MB 的旧模型。
"""
import hashlib
import threading
import time

_lock = threading.Lock()
_engine = None
_info = {}


def _model_dir():
    try:
        import rapidocr
        from pathlib import Path
        return Path(rapidocr.__file__).parent / 'models'
    except Exception:
        return None


def engine_info():
    """给体检 / 关于页用：引擎版本、模型文件名与大小。"""
    if _info:
        return dict(_info)
    out = {'engine': 'rapidocr'}
    try:
        import importlib.metadata as md
        out['version'] = md.version('rapidocr')
    except Exception:
        out['version'] = '?'
    d = _model_dir()
    models = []
    try:
        if d and d.is_dir():
            for p in sorted(d.glob('*.onnx')):
                models.append({'name': p.name, 'mb': round(p.stat().st_size / 1e6, 2)})
    except Exception:
        pass
    out['models'] = models
    out['models_dir'] = str(d) if d else ''
    _info.update(out)
    return dict(out)


def model_digest():
    """模型 SHA-256，用于固定工件身份（合规要求：不能只看文件名）。"""
    d = _model_dir()
    out = {}
    try:
        if not d:
            return out
        for p in sorted(d.glob('*.onnx')):
            h = hashlib.sha256()
            with open(p, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            out[p.name] = h.hexdigest()
    except Exception:
        pass
    return out


def get_engine():
    """惰性加载单例；首次约 1~2 秒。"""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr import RapidOCR
                # log_level=error：静默掉「某页没检出文字」这类 WARNING，别写进 debug.log
                # 第 399 项：扫描件定位命中页会在后台连续 OCR，默认 -1 会让 onnxruntime
                # 吃满所有核（实测 3 核多），用户前台搜索明显被打扰 → 限 2 线程 + 1 个
                # inter-op，单页约 0.5~1s，整份 30 页 6~10s，够快也不卡。
                _engine = RapidOCR(params={'Global.log_level': 'error',
                                           'EngineConfig.onnxruntime.intra_op_num_threads': 2,
                                           'EngineConfig.onnxruntime.inter_op_num_threads': 1})
    return _engine


class RapidOCRCompat:
    """旧契约包装：ocr(img) -> ([[box, text, score], ...], elapse秒)。"""

    def __call__(self, img):
        eng = get_engine()
        t0 = time.time()
        out = eng(img)
        txts = list(getattr(out, 'txts', None) or ())
        scores = list(getattr(out, 'scores', None) or ())
        boxes = getattr(out, 'boxes', None)
        if boxes is None:
            boxes = [None] * len(txts)
        res = []
        for i, txt in enumerate(txts):
            box = boxes[i]
            try:
                box = [[float(x), float(y)] for x, y in box]
            except Exception:
                box = None
            score = float(scores[i]) if i < len(scores) else 0.0
            res.append([box, txt, score])
        return res, time.time() - t0
