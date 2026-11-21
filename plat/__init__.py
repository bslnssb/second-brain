"""平台抽象层（第 382 项）。

为什么有这一层：Memex 的「壳」是 pywebview + 前端（跨平台），但**动作**是 Win32 的——
打开文件走 os.startfile、定位走 explorer /select、数据目录在 %LOCALAPPDATA%……
这些散在 brain.py / app_window.py 里几十处，将来接 mac 只能到处 if sys.platform。

约定（很重要，别破坏）：
1. 这一层只做「本机特有动作」的收敛，**不碰业务**：不读 DB、不 import brain、不碰 web。
2. Windows 的既有行为**一个字节都不许变**（现在的装机版就是基线），新增分支只能是 mac/linux。
3. 每个动作都要能被单测直接测「会派发哪条命令」，所以命令拼装写成纯函数（_xxx_argv），
   真正的 subprocess 调用放在最后一层薄包装里。

第一批只收三个动作（打开、定位、开数据目录），其余（托盘/剪贴板/热键/截图窗/标题栏）
在 CROSSPLATFORM.md 里排了序，一批一批搬。
"""

from . import paths, shell  # noqa: F401
from .paths import data_dir, install_dir, platform_name  # noqa: F401
from .shell import open_path, reveal  # noqa: F401

__all__ = [
    'data_dir',
    'install_dir',
    'open_path',
    'platform_name',
    'paths',
    'reveal',
    'shell',
]
