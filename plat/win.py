"""Win32 依赖的「导入期兜底」（第 382 项）。

问题：38 个模块里 18 个用 Win32，而 mac/linux 上 **import 阶段**就会炸——
`ctypes.WINFUNCTYPE` / `ctypes.windll` / `ctypes.WinDLL` 在非 Windows 上根本不存在。
CI 实测：Windows 上能收集 1132 条测试，mac/linux 只有 685 条，差的 447 条不是「跑挂」，
而是**模块 import 就失败**，unittest 直接记成 `_FailedTest`。看不见的东西没法修。

做法：在非 Windows 上给 `ctypes` 补几个「空实现」占位符，让这些模块**能 import**：

    ctypes.windll  → 任何属性、任何调用都返回一个空对象（链式都不炸）
    ctypes.WinDLL  → 同上（工厂）
    ctypes.WINFUNCTYPE → 当类型用：CB = WINFUNCTYPE(restype, ...) 返回一个「拿到函数就原样返回」的对象
    ctypes.oledll  → 同 windll
    ctypes.HRESULT → c_long（只是当 restype 用）
    ctypes.wintypes → 优先真模块；真模块也进不来时给最小替代（这些结构体还要能用）

**边界（必须记住）**：
1. 只在 `sys.platform` 不是 Windows 时打补丁；Windows 上这个模块什么都不做。
2. 补丁只保证「能 import、模块级定义不炸」，**不保证功能可用**——
   托盘、热键、剪贴板、覆盖窗这些在 mac 上仍然要靠 P3~P5 真实现，现在调用等于空转。
3. 不要用这个兜底去掩盖真实逻辑：新代码要判断平台，就查 `IS_WIN`。
"""

import ctypes
import sys

IS_WIN = sys.platform.startswith('win')


class _Null:
    """万能空对象：属性、下标、调用、bool 全都不炸。"""

    def __getattr__(self, name):
        # 每次返回一个新的空对象 —— 不要缓存，否则 `a.b.c = 1` 会污染 `a.b` 这个公共节点
        return _Null()

    def __setattr__(self, name, value):
        pass

    def __getitem__(self, key):
        return _Null()

    def __call__(self, *args, **kwargs):
        return _Null()

    def __bool__(self):
        return False

    def __int__(self):
        return 0

    def __eq__(self, other):
        return isinstance(other, _Null) or other in (0, None)

    def __hash__(self):
        return 0

    def __repr__(self):
        return '<win32 stub>'


class _FuncTypeSyntax:
    """`WINFUNCTYPE(restype, *argtypes)` 的替身：返回的类型再被调用来包装函数时，原样返回。"""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, func):
        return func


def _min_wintypes():
    """`ctypes.wintypes` 在极少数环境下也进不来时的最小替代。
    只覆盖这些模块真的会用到的名字；结构体字段要的是**真 ctypes 类型**，所以不能给 _Null。
    """
    ns = {
        'BYTE': ctypes.c_ubyte, 'WORD': ctypes.c_ushort, 'DWORD': ctypes.c_ulong,
        'UINT': ctypes.c_uint, 'ULONG': ctypes.c_ulong, 'LONG': ctypes.c_long,
        'BOOL': ctypes.c_long, 'WPARAM': ctypes.c_size_t, 'LPARAM': ctypes.c_ssize_t,
        'HWND': ctypes.c_void_p, 'HANDLE': ctypes.c_void_p, 'HDC': ctypes.c_void_p,
        'LPVOID': ctypes.c_void_p, 'HMODULE': ctypes.c_void_p, 'ATOM': ctypes.c_ushort,
    }

    class POINT(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                    ('right', ctypes.c_long), ('bottom', ctypes.c_long)]

    class MSG(ctypes.Structure):
        _fields_ = [('hwnd', ctypes.c_void_p), ('message', ctypes.c_uint),
                    ('wParam', ctypes.c_size_t), ('lParam', ctypes.c_ssize_t),
                    ('time', ctypes.c_ulong), ('pt', POINT)]

    ns.update(POINT=POINT, RECT=RECT, MSG=MSG)
    return type('wintypes', (), ns)


def install() -> bool:
    """补齐缺失的 Win32 占位。返回是否真的补过（Windows 上什么都不缺，返回 False）。

    注意：判断条件是**「ctypes 上缺不缺这些名字」**，而不是 `IS_WIN`。
    这样在 Windows 机器上也能复现非 Windows 的 import 行为（见 tmp/_nowin_probe.py：
    先 delattr 再 import，就能在本地跑出「哪些模块在 mac 上 import 会炸」），
    真 Windows 上这些名字本来就在，这里自然一个都不补、行为零变化。
    """
    if getattr(ctypes, '_memex_win_stub', False):
        return True

    patched = False
    for name in ('windll', 'oledll'):
        if not hasattr(ctypes, name):
            setattr(ctypes, name, _Null())
            patched = True
    if not hasattr(ctypes, 'WinDLL'):
        # 有的模块写 `hasattr(ctypes, 'WinDLL')` 来判断平台 —— 这里必须是真属性，
        # 但值本身是个空工厂，调用之后返回空对象。
        setattr(ctypes, 'WinDLL', _Null())
        patched = True
    if not hasattr(ctypes, 'WINFUNCTYPE'):
        setattr(ctypes, 'WINFUNCTYPE', _FuncTypeSyntax)
        patched = True
    if not hasattr(ctypes, 'HRESULT'):
        setattr(ctypes, 'HRESULT', ctypes.c_long)
        patched = True

    if not patched:
        return False

    try:                                # 先试真模块：能进来说明结构体定义都是真类型
        import ctypes.wintypes as _wt   # noqa: F401
    except Exception:                   # noqa: BLE001
        ctypes.wintypes = _min_wintypes()

    ctypes._memex_win_stub = True
    return True


install()
