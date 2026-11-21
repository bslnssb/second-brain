# -*- coding: utf-8 -*-
"""第 297 项：系统程序（设置 / cmd / 记事本…）不在文件索引里，要能在快搜窗直接搜到。

用户的反馈很具体：「搜『设置』和『cmd』都应该要能找到系统的程序打开，现在都没实现」。

为什么原来的索引里没有它们，不是扫描漏了：

  * `C:\\Windows\\System32` 是**系统目录**，默认排除规则直接跳过（这是对的：
    里面 3 万多个 dll/exe，收进来会把搜索结果淹掉）；
  * 「设置」是 MSIX 应用，磁盘上根本没有同名 exe 或 .lnk，`Get-StartApps` 也
    不列它（它不在开始菜单里），所以 app_entries 那套 AUMID 采集也收不到；
  * `cmd` 是**命令**而不是「已安装的程序」，开始菜单里只有「命令提示符」这一条
    快捷方式（而且很多精简系统 / 企业镜像里连这条都没有）。

所以这里不靠索引，而是在**查询时**给一份内置的系统程序清单：命中就直接作为
结果返回（`ab` 加权让它排在最前），点击时和普通应用走同一条路径
（`app_entries.launch` → 磁盘 exe 用 os.startfile，设置用 shell:AppsFolder AUMID）。

清单只收「Windows 自带、路径固定、打开就一定有用」的项目，不猜第三方程序；
路径不存在的条目（精简版 Windows 没有 snippingtool / wt）当场跳过，不会给出
一条点了没反应的结果。
"""
from __future__ import annotations

import os

WIN = os.environ.get('SystemRoot') or r'C:\Windows'

# 「设置」的 AUMID：Win10 1507 起固定不变，开始菜单自己就是用它启动设置的。
SETTINGS_AUMID = ('windows.immersivecontrolpanel_cw5n1h2txyewy'
                  '!microsoft.windows.immersivecontrolpanel')
SETTINGS_PATH = 'shell:AppsFolder\\' + SETTINGS_AUMID


def _n(zh, en, es=None, pt=None, hi=None):
    """五种语言的显示名；没给的语言一律回落英文，绝不让英文界面露出中文。"""
    return {'zh': zh, 'en': en, 'es': es or en, 'pt': pt or en, 'hi': hi or en}


# path: 相对 %SystemRoot% 的路径，或 'shell:AppsFolder\\<AUMID>'；'which:xxx' 表示 PATH 里的命令
ENTRIES = [
    # —— 用户点名要的两个 ——
    {'path': SETTINGS_PATH, 'ext': '',
     'names': _n('设置', 'Settings', 'Configuracion', 'Configuracoes', 'सेटिंग्स'),
     'keys': '设置 系统设置 設定 settings windows设置 shezhi configuracion configuracoes'},
    {'path': r'System32\cmd.exe', 'ext': '.exe',
     'names': _n('命令提示符', 'Command Prompt', 'Simbolo del sistema', 'Prompt de comando',
                 'कमांड प्रॉम्प्ट'),
     'keys': 'cmd cmdexe 命令提示符 命令行 命令窗 commandprompt 终端 console'},
    # —— 其它常用系统程序 ——
    {'path': r'System32\control.exe', 'ext': '.exe',
     'names': _n('控制面板', 'Control Panel', 'Panel de control', 'Painel de Controle',
                 'कंट्रोल पैनल'),
     'keys': '控制面板 control controlpanel 控制台 configuracion configuracoes'},
    {'path': r'System32\WindowsPowerShell\v1.0\powershell.exe', 'ext': '.exe',
     'names': _n('Windows PowerShell', 'Windows PowerShell'),
     'keys': 'powershell ps pwsh 命令行 shell'},
    {'path': 'which:wt.exe', 'ext': '.exe',
     'names': _n('Windows 终端', 'Windows Terminal', 'Terminal de Windows', 'Terminal do Windows',
                 'विंडोज़ टर्मिनल'),
     'keys': 'wt terminal 终端 终端窗口'},
    {'path': r'System32\taskmgr.exe', 'ext': '.exe',
     'names': _n('任务管理器', 'Task Manager', 'Administrador de tareas', 'Gerenciador de tarefas',
                 'टास्क मैनेजर'),
     'keys': '任务管理器 taskmgr taskmanager 进程管理'},
    {'path': r'System32\regedit.exe', 'ext': '.exe',
     'names': _n('注册表编辑器', 'Registry Editor', 'Editor del Registro', 'Editor do Registro',
                 'रजिस्ट्री एडिटर'),
     'keys': 'regedit 注册表 注册表编辑器 registry'},
    {'path': r'System32\notepad.exe', 'ext': '.exe',
     'names': _n('记事本', 'Notepad', 'Bloc de notas', 'Bloco de notas', 'नोटपैड'),
     'keys': 'notepad 记事本 文本编辑器'},
    {'path': r'System32\calc.exe', 'ext': '.exe',
     'names': _n('计算器', 'Calculator', 'Calculadora', 'Calculadora', 'कैलकुलेटर'),
     'keys': 'calc calculator 计算器 计算机'},
    {'path': r'System32\mspaint.exe', 'ext': '.exe',
     'names': _n('画图', 'Paint', 'Paint', 'Paint', 'पेंट'),
     'keys': 'mspaint paint 画图 绘图'},
    {'path': r'System32\SnippingTool.exe', 'ext': '.exe',
     'names': _n('截图工具', 'Snipping Tool', 'Recortes', 'Ferramenta de Captura',
                 'स्निपिंग टूल'),
     'keys': 'snippingtool snip 截图 截图工具'},
    {'path': r'System32\mstsc.exe', 'ext': '.exe',
     'names': _n('远程桌面连接', 'Remote Desktop Connection', 'Conexion a Escritorio remoto',
                 'Conexao de Area de Trabalho Remota', 'रिमोट डेस्कटॉप'),
     'keys': 'mstsc 远程桌面 rdp remotedesktop'},
    {'path': r'explorer.exe', 'ext': '.exe',
     'names': _n('资源管理器', 'File Explorer', 'Explorador de archivos', 'Explorador de arquivos',
                 'फ़ाइल एक्सप्लोरर'),
     'keys': 'explorer 资源管理器 文件管理器 我的电脑 fileexplorer'},
    {'path': r'System32\msconfig.exe', 'ext': '.exe',
     'names': _n('系统配置', 'System Configuration', 'Configuracion del sistema',
                 'Configuracao do sistema', 'सिस्टम कॉन्फ़िगरेशन'),
     'keys': 'msconfig 系统配置 启动项'},
    {'path': r'System32\msinfo32.exe', 'ext': '.exe',
     'names': _n('系统信息', 'System Information', 'Informacion del sistema',
                 'Informacoes do sistema', 'सिस्टम जानकारी'),
     'keys': 'msinfo32 系统信息'},
    {'path': r'System32\dxdiag.exe', 'ext': '.exe',
     'names': _n('DirectX 诊断工具', 'DirectX Diagnostic Tool', 'Herramienta de diagnostico de DirectX',
                 'Ferramenta de diagnostico do DirectX', 'DirectX निदान टूल'),
     'keys': 'dxdiag directx 诊断'},
    {'path': r'System32\charmap.exe', 'ext': '.exe',
     'names': _n('字符映射表', 'Character Map', 'Mapa de caracteres', 'Mapa de caracteres',
                 'कैरेक्टर मैप'),
     'keys': 'charmap 字符映射表'},
    {'path': r'System32\osk.exe', 'ext': '.exe',
     'names': _n('屏幕键盘', 'On-Screen Keyboard', 'Teclado en pantalla', 'Teclado virtual',
                 'ऑन-स्क्रीन कीबोर्ड'),
     'keys': 'osk 屏幕键盘 软键盘'},
    {'path': r'System32\magnify.exe', 'ext': '.exe',
     'names': _n('放大镜', 'Magnifier', 'Lupa', 'Lupa', 'मैग्निफ़ायर'),
     'keys': 'magnify 放大镜'},
    # —— 管理工具（.msc：os.startfile 会用文件关联拉起 mmc）——
    {'path': r'System32\devmgmt.msc', 'ext': '.msc',
     'names': _n('设备管理器', 'Device Manager', 'Administrador de dispositivos',
                 'Gerenciador de dispositivos', 'डिवाइस मैनेजर'),
     'keys': 'devmgmt 设备管理器 devicemanager'},
    {'path': r'System32\diskmgmt.msc', 'ext': '.msc',
     'names': _n('磁盘管理', 'Disk Management', 'Administracion de discos',
                 'Gerenciamento de disco', 'डिस्क प्रबंधन'),
     'keys': 'diskmgmt 磁盘管理 diskmanagement'},
    {'path': r'System32\services.msc', 'ext': '.msc',
     'names': _n('服务', 'Services', 'Servicios', 'Servicos', 'सेवाएँ'),
     'keys': 'services 服务 系统服务'},
    {'path': r'System32\eventvwr.msc', 'ext': '.msc',
     'names': _n('事件查看器', 'Event Viewer', 'Visor de eventos', 'Visualizador de eventos',
                 'इवेंट व्यूअर'),
     'keys': 'eventvwr 事件查看器 eventviewer'},
    {'path': r'System32\compmgmt.msc', 'ext': '.msc',
     'names': _n('计算机管理', 'Computer Management', 'Administracion de equipos',
                 'Gerenciamento do computador', 'कंप्यूटर प्रबंधन'),
     'keys': 'compmgmt 计算机管理 computermanagement'},
    {'path': r'System32\taskschd.msc', 'ext': '.msc',
     'names': _n('任务计划程序', 'Task Scheduler', 'Programador de tareas',
                 'Agendador de tarefas', 'टास्क शेड्यूलर'),
     'keys': 'taskschd 任务计划 计划任务 taskscheduler'},
]

ALIAS_LANGS = ('zh', 'en', 'es', 'pt', 'hi')

_EXISTS = {}          # path -> (checked_at, bool)
_TTL = 60.0


def _norm(s):
    """全角转半角 + 去空格 + 小写（与 apprank._norm 同一套规则）。"""
    out = []
    for ch in str(s or '').strip():
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        elif code == 0x3000:
            ch = ' '
        out.append(ch)
    return ''.join(out).replace(' ', '').lower()


def _target(e):
    """条目 → 可打开的完整路径；不存在返回 ''（精简版 Windows 会少几个工具）。"""
    p = e.get('path') or ''
    if p.startswith('shell:AppsFolder\\'):
        return p                     # 虚拟应用：磁盘上没有路径，交给 Shell
    if p.startswith('which:'):
        import shutil
        return shutil.which(p[6:]) or ''
    full = os.path.join(WIN, p) if not os.path.isabs(p) else p
    now = time_now()
    got = _EXISTS.get(full)
    if got and now - got[0] < _TTL:
        return full if got[1] else ''
    ok = os.path.isfile(full)
    _EXISTS[full] = (now, ok)
    return full if ok else ''


def time_now():
    import time
    return time.time()


def display_name(e, lang='zh'):
    names = e.get('names') or {}
    lg = str(lang or 'zh').lower()[:2]
    return names.get(lg) or names.get('en') or names.get('zh') or ''


def _aliases(e, lang='zh'):
    out = []
    for lg in (str(lang or 'zh').lower()[:2],) + ALIAS_LANGS:
        v = (e.get('names') or {}).get(lg)
        if v:
            out.append(_norm(v))
    out.extend(_norm(x) for x in str(e.get('keys') or '').split())
    # 去重但保序
    seen, uniq = set(), []
    for a in out:
        if a and a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def match(q, lang='zh', limit=5):
    """查询命中的系统程序：[(dict(name, path, ext, ab, app))]，完全相等 > 前缀。

    只做「相等 / 前缀（≥2 字）」两档：`c` 这种单字不去猜 cmd，
    `设置`/`cmd`/`settings`/`命令` 都能直中；不做子串匹配，免得 `ps` 把
    「截图」「磁盘管理」全带出来。
    """
    nq = _norm(q)
    if not nq:
        return []
    hits = []
    for e in ENTRIES:
        path = _target(e)
        if not path:
            continue
        tier = None
        for a in _aliases(e, lang):
            if a == nq:
                tier = 0
                break
            if len(nq) >= 2 and a.startswith(nq):
                if tier is None or tier > 1:
                    tier = 1
        if tier is None:
            continue
        hits.append((tier, -len(display_name(e, lang)), e, path))
    hits.sort(key=lambda t: (t[0], t[1]))
    out = []
    for tier, _neg, e, path in hits[:limit]:
        out.append({'name': display_name(e, lang), 'path': path,
                    'ext': e.get('ext') or '', 'ab': 100 - tier * 8, 'app': 1})
    return out
