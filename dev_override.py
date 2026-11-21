"""免打包调试通道（第 370 项）。

**要解决的问题**：截图翻译卡片这类纯 WinForms 模块被打进 exe 里，
改一行也要走一遍 PyInstaller，一次三分钟，改版本来回就是半小时。

**做法**：启动时先看 `%LOCALAPPDATA%\\Memex\\App\\dev_override\\<name>.py` 在不在，
在就按**文件路径**把它装进 `sys.modules`，顶替内置那份；不在就走正常 import。
于是「改卡片 → 重新打开卡片」就能看到效果，不用再打包。

**边界**：
- 只认白名单（见 `ALLOW`）。允许随便覆盖 `brain` 之类，等于给自己留一颗哑弹。
- 装载失败不半上不下：`sys.modules` 里撤掉、回退内置版本，并在 `dev_override.log` 记一笔。
- 这个目录**不进发布包**（构建脚本只拷贝 `_internal`，不会造这里）。
"""

import importlib.util
import os
import sys
import time

DEV_SUBDIR = 'dev_override'

# 只放「改起来要重新打包、且不牵扯核心流程」的模块
ALLOW = ('snip_card', 'snip_ocr', 'snip_overlay', 'translator')

_INSTALLED = {}


def app_root():
    """和 brain.app_dir() 同一个位置：%LOCALAPPDATA%\\Memex\\App（打包后就是 exe 所在目录）。"""
    try:
        import brain
        return brain.app_dir()
    except Exception:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'Memex', 'App')


def dev_dir():
    return os.path.join(app_root(), DEV_SUBDIR)


def _log(msg):
    """单独一个日志文件：这行每次启动都写，别混进 debug.log 把人淹了。"""
    try:
        d = dev_dir()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(os.path.dirname(d), 'dev_override.log')
        try:
            if os.path.isfile(p) and os.path.getsize(p) > 256 * 1024:
                os.replace(p, p + '.1')
        except Exception:
            pass
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        with open(p, 'a', encoding='utf-8') as f:
            f.write('[%s] %s%s' % (stamp, msg, chr(10)))
    except Exception:
        pass


def install(name):
    """把 dev_override/<name>.py 装成 `name` 模块。返回 True 表示真的用上了覆盖版。"""
    if name not in ALLOW:
        return False
    path = os.path.join(dev_dir(), name + '.py')
    if not os.path.isfile(path):
        return False
    before = sys.modules.get(name)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError('载入不了 %s' % path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    except Exception as e:
        # 回退：撤掉半成品，恢复原样（原本没导入过就删掉，让后面的 import 走正常路径）
        if before is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = before
        _log('!! %s 覆盖失败，已回退内置版：%r' % (name, e))
        return False
    _INSTALLED[name] = path
    _log('>> %s <- %s' % (name, path))
    return True


def boot():
    """启动时扫一遍 dev_override 目录，把白名单里的覆盖模块装进来。"""
    if os.environ.get('MEMEX_NO_DEV_OVERRIDE'):
        return []
    d = dev_dir()
    if not os.path.isdir(d):
        return []
    ok = []
    for name in ALLOW:
        if install(name):
            ok.append(name)
    return ok


def status():
    """当前哪些模块在用覆盖版（排查时一眼看清，免得改了文件却没生效）。"""
    return dict(_INSTALLED)
