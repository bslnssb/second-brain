# -*- coding: utf-8 -*-
"""第二大脑 安装程序（单文件 exe）。

界面语言跟着系统走（中文系统中文、其它英文），右上角也能手动切。
双击 → 选安装位置 → 装好 → 建快捷方式 → 直接启动。
装完之后程序、数据库、设置都在你选的那个文件夹里（写不进去会自动退到 LOCALAPPDATA）。
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

APP_ZH = '第二大脑'
APP_EN = 'Second Brain'
DIR_NAME = 'SecondBrain'   # 安装目录固定英文名，不跟界面语言变
VERSION = '0.9.55'

SILENT = os.environ.get('SB_SILENT') == '1'
NO_LAUNCH = os.environ.get('SB_NO_LAUNCH') == '1'
FORCE_DIR = os.environ.get('SB_INSTALL_DIR')

# WebView2 运行时：Win11 和打了补丁的 Win10 自带，但 LTSC 2019 / Server /
# 各种精简版系统**没有**。缺它的话装完双击是白屏或闪退，用户根本不知道
# 世界上有这么一个组件。所以安装程序自己查一次、缺了就直接把下载页给他。
WV2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
WV2_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'


def wv2_version():
    """返回 WebView2 Runtime 版本；没装返回 ''。注册表和目录都查一遍。"""
    try:
        import winreg
        for hive, path in ((winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\\' + WV2_GUID),
                           (winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\EdgeUpdate\Clients\\' + WV2_GUID),
                           (winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\EdgeUpdate\Clients\\' + WV2_GUID)):
            try:
                with winreg.OpenKey(hive, path) as k:
                    v = str(winreg.QueryValueEx(k, 'pv')[0] or '')
                    if v and not v.startswith('0.0.0'):
                        return v
            except Exception:
                pass
    except Exception:
        pass
    try:
        import glob
        for pat in ('%s\\Microsoft\\EdgeWebView\\Application\\*\\msedgewebview2.exe' % os.environ.get('ProgramFiles(x86)', ''),
                    '%s\\Microsoft\\EdgeWebView\\Application\\*\\msedgewebview2.exe' % os.environ.get('ProgramFiles', ''),
                    '%s\\Microsoft\\EdgeWebView\\Application\\*\\msedgewebview2.exe' % os.environ.get('LOCALAPPDATA', '')):
            if pat.startswith('\\'):
                continue
            hits = glob.glob(pat)
            if hits:
                return os.path.basename(os.path.dirname(hits[0]))
    except Exception:
        pass
    return ''


INK = '#1a2233'
SUB = '#6b7280'
LINE = '#e6e9f2'
BG = '#ffffff'
SOFT = '#f6f7fb'
ACC = '#4f6ef7'
ACC_D = '#3d5bd9'
DARK = '#18223c'

L = {
    'zh': {
        'title': '第二大脑 安装',
        'app': APP_ZH,
        'sub': '本地文件搜索 · 安装程序 v%s' % VERSION,
        'en': 'Second Brain',
        'where': '安装位置',
        'hint': '程序、数据库、设置都会放在这里；以后想卸载，删掉整个文件夹就行。',
        'browse': '浏览…',
        'keep': '创建桌面快捷方式',
        'applang': '程序语言',
        'install': '安装',
        'cancel': '取消',
        'installing': '正在安装：%s',
        'preparing': '准备安装…',
        'done': '装好了：%s',
        'open': '打开第二大脑',
        'boot': '开机启动（默认开启）',
        'as_ok': '已设置开机启动',
        'as_no': '未开启开机启动（可在设置里打开）',
        'sc_ok': '已创建桌面快捷方式',
        'sc_no': '没能创建快捷方式（可以直接双击目录里的 exe）',
        'foot': '装到当前用户，不需要管理员权限',
        'bad_dir': '这个文件夹写不进去，换一个吧',
        'failed': '安装失败：%s',
        'no_exe': '解压后没找到主程序（可能被杀毒软件拦了）',
        'bad_payload': '安装包不完整：里面没有主程序，请重新下载安装器',
        'empty_pack': '安装包是空的：一个文件都没解压出来（打包时漏了），请换一个新版安装器',
        'av_del': '解压后主程序不见了（目录里已有 %d 个文件），基本是杀毒软件删的：把安装目录加入白名单后重试',
        'switch': 'ZH',
    },
    'en': {
        'title': 'Install Second Brain',
        'app': APP_EN,
        'sub': 'Local file search · Setup v%s' % VERSION,
        'en': APP_ZH,
        'where': 'Install location',
        'hint': 'Program, database and settings all live here. To uninstall, just delete this folder.',
        'browse': 'Browse…',
        'keep': 'Create a desktop shortcut',
        'applang': 'App language',
        'install': 'Install',
        'cancel': 'Cancel',
        'installing': 'Installing: %s',
        'preparing': 'Preparing…',
        'done': 'Installed to %s',
        'open': 'Open Second Brain',
        'boot': 'Start at boot (default on)',
        'as_ok': 'Start at boot enabled',
        'as_no': 'Start at boot off (enable it in Settings)',
        'sc_ok': 'Desktop shortcut created',
        'sc_no': 'Could not create a shortcut (you can run the exe in this folder)',
        'foot': 'Installs for the current user, no admin rights needed',
        'bad_dir': 'This folder is not writable — pick another one',
        'failed': 'Install failed: %s',
        'no_exe': 'Second Brain.exe missing after extraction (antivirus?)',
        'bad_payload': 'This installer is incomplete — the main program is missing from the package. Please download it again.',
        'empty_pack': 'The package is empty: nothing was extracted (broken build). Please use a freshly built installer.',
        'av_del': 'The main program vanished after extraction (%d files were written). An antivirus most likely removed it — whitelist the folder and retry.',
        'switch': 'EN',
    },
    'es': {
        'title': 'Instalar Segundo Cerebro',
        'app': APP_EN,
        'sub': 'Búsqueda local de archivos · Instalador v%s' % VERSION,
        'en': APP_ZH,
        'where': 'Ubicación de instalación',
        'hint': 'El programa, la base de datos y los ajustes se guardan aquí. Para desinstalar, borra esta carpeta.',
        'browse': 'Examinar…',
        'keep': 'Crear un acceso directo en el escritorio',
        'applang': 'Idioma de la app',
        'install': 'Instalar',
        'cancel': 'Cancelar',
        'installing': 'Instalando: %s',
        'preparing': 'Preparando…',
        'done': 'Instalado en %s',
        'open': 'Abrir Second Brain',
        'boot': 'Iniciar al arrancar (activado)',
        'as_ok': 'Inicio automático activado',
        'as_no': 'Inicio automático desactivado (actívalo en Ajustes)',
        'sc_ok': 'Acceso directo creado',
        'sc_no': 'No se pudo crear el acceso directo (puedes ejecutar el exe de esta carpeta)',
        'foot': 'Para el usuario actual, sin permisos de admin',
        'bad_dir': 'No se puede escribir en esta carpeta, elige otra',
        'failed': 'Error de instalación: %s',
        'no_exe': 'No se encontró Second Brain.exe tras descomprimir (¿antivirus?)',
        'bad_payload': 'El instalador está incompleto: falta el programa principal. Vuelve a descargarlo.',
        'empty_pack': 'El paquete está vacío: no se extrajo ningún archivo (compilación defectuosa). Usa un instalador nuevo.',
        'av_del': 'El programa principal desapareció tras descomprimir (se escribieron %d archivos). Probablemente lo eliminó el antivirus: añade la carpeta a las exclusiones y reintenta.',
        'switch': 'ES',
    },
    'pt': {
        'title': 'Instalar Second Brain',
        'app': APP_EN,
        'sub': 'Busca local de arquivos · Instalador v%s' % VERSION,
        'en': APP_ZH,
        'where': 'Local de instalação',
        'hint': 'Programa, banco de dados e configurações ficam aqui. Para desinstalar, apague esta pasta.',
        'browse': 'Procurar…',
        'keep': 'Criar atalho na área de trabalho',
        'applang': 'Idioma do app',
        'install': 'Instalar',
        'cancel': 'Cancelar',
        'installing': 'Instalando: %s',
        'preparing': 'Preparando…',
        'done': 'Instalado em %s',
        'open': 'Abrir Second Brain',
        'boot': 'Iniciar com o Windows (padrão)',
        'as_ok': 'Início automático ativado',
        'as_no': 'Início automático desativado (ative em Configurações)',
        'sc_ok': 'Atalho criado',
        'sc_no': 'Não foi possível criar o atalho (você pode executar o exe desta pasta)',
        'foot': 'Para o usuário atual, sem direitos de admin',
        'bad_dir': 'Não é possível gravar nesta pasta, escolha outra',
        'failed': 'Falha na instalação: %s',
        'no_exe': 'Second Brain.exe não encontrado após extrair (antivírus?)',
        'bad_payload': 'O instalador está incompleto: falta o programa principal. Baixe novamente.',
        'empty_pack': 'O pacote está vazio: nenhum arquivo foi extraído (build defeituosa). Use um instalador novo.',
        'av_del': 'O programa principal desapareceu após extrair (%d arquivos gravados). Provavelmente o antivírus removeu: adicione a pasta às exceções e tente de novo.',
        'switch': 'PT',
    },
    'hi': {
        'title': 'Second Brain इंस्टॉल करें',
        'app': APP_EN,
        'sub': 'स्थानीय फ़ाइल खोज · इंस्टॉलर v%s' % VERSION,
        'en': APP_ZH,
        'where': 'इंस्टॉल स्थान',
        'hint': 'प्रोग्राम, डेटाबेस और सेटिंग्स यहीं रहेंगी। अनइंस्टॉल करने के लिए यह फ़ोल्डर मिटा दें।',
        'browse': 'ब्राउज़…',
        'keep': 'डेस्कटॉप शॉर्टकट बनाएँ',
        'applang': 'ऐप की भाषा',
        'install': 'इंस्टॉल',
        'cancel': 'रद्द करें',
        'installing': 'इंस्टॉल हो रहा है: %s',
        'preparing': 'तैयारी…',
        'done': 'इंस्टॉल हो गया: %s',
        'open': 'Second Brain खोलें',
        'boot': 'बूट पर चालू (डिफ़ॉल्ट)',
        'as_ok': 'ऑटो-स्टार्ट चालू',
        'as_no': 'ऑटो-स्टार्ट बंद (सेटिंग में चालू करें)',
        'sc_ok': 'डेस्कटॉप शॉर्टकट बन गया',
        'sc_no': 'शॉर्टकट नहीं बन सका (इस फ़ोल्डर का exe चला सकते हैं)',
        'foot': 'मौजूदा उपयोगकर्ता के लिए, एडमिन अधिकार नहीं चाहिए',
        'bad_dir': 'इस फ़ोल्डर में लिखा नहीं जा सकता, दूसरा चुनें',
        'failed': 'इंस्टॉल विफल: %s',
        'no_exe': 'निकालने के बाद Second Brain.exe नहीं मिला (एंटीवायरस?)',
        'bad_payload': 'इंस्टॉलर अधूरा है — पैकेज में मुख्य प्रोग्राम नहीं है। कृपया दोबारा डाउनलोड करें।',
        'empty_pack': 'पैकेज खाली है: कोई फ़ाइल एक्सट्रैक्ट नहीं हुई (खराब बिल्ड)। नया इंस्टॉलर लें।',
        'av_del': 'एक्सट्रैक्ट के बाद मुख्य प्रोग्राम गायब हो गया (%d फ़ाइलें लिखी गईं)। संभवतः एंटीवायरस ने हटाया — फ़ोल्डर को अनुमति सूची में जोड़कर फिर कोशिश करें।',
        'switch': 'HI',
    },
}


def system_lang():
    """系统界面语言：中文（简/繁）→ zh，其余 → en。可用 SB_LANG 强制。"""
    forced = (os.environ.get('SB_LANG') or '').lower()
    if forced in ('zh', 'en', 'es', 'pt', 'hi'):
        return forced
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        prim = langid & 0x3FF
        return {0x04: 'zh',             # LANG_CHINESE
                0x0A: 'es',             # LANG_SPANISH
                0x16: 'pt',             # LANG_PORTUGUESE
                0x39: 'hi'}.get(prim, 'en')
    except Exception:
        pass
    try:
        import locale
        loc = (locale.getdefaultlocale()[0] or '').lower()
        return 'zh' if loc.startswith('zh') else 'en'
    except Exception:
        return 'zh'


def default_dir():
    if FORCE_DIR:
        return Path(FORCE_DIR)
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData/Local')
    return Path(base) / 'SecondBrain' / 'App'


def payload_path():
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'payload.zip'
    return Path(__file__).resolve().parent / 'payload.zip'


def _members(z):
    return [n for n in z.namelist()
            if n.startswith('SecondBrain/') and '..' not in n and not n.startswith('/')]


def payload_ok(payload):
    """预检：包里的清单必须带主程序。返回 (是否可用, 条目数, 是否含主程序)。"""
    try:
        with zipfile.ZipFile(payload) as z:
            names = _members(z)
    except Exception:
        return False, 0, False
    return ('SecondBrain/SecondBrain.exe' in names and len(names) > 100,
            len(names), 'SecondBrain/SecondBrain.exe' in names)


def extract(payload, dest, progress=None):
    dest.mkdir(parents=True, exist_ok=True)
    # 暂存目录必须「无论如何都不和 dest 撞名」：解得进 staging，再从 staging/SecondBrain 搬进 dest。
    # 旧写法把解压根设成 staging.parent，当 dest 自己就叫 SecondBrain 时（用户点「浏览…」选了
    # 一个父目录，browse() 会自动补一层 SecondBrain），解出来的 SecondBrain 就是 dest 本身 ——
    # 随后 rmtree(target) 先把真文件删掉，move 再找源就报 [WinError 2]（v0.9.43 用户实测）。
    staging = dest.parent / ('.sb-install-%d-%d' % (os.getpid(), int(time.time())))
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    # 顺手清掉旧版本留下的 .installing 残骸（老版本崩溃时会留）
    stale = dest.parent / (dest.name + '.installing')
    if stale.exists() and stale != staging:
        shutil.rmtree(stale, ignore_errors=True)
    with zipfile.ZipFile(payload) as z:
        names = _members(z)
        for i, n in enumerate(names, 1):
            z.extract(n, staging)
            if progress:
                progress(i, len(names), n)
    src = staging / 'SecondBrain'
    if not src.exists():
        # 包是空的 / 条目不对：这里必须炸，否则会一路走到「没找到主程序」，
        # 把打包事故误报成杀毒软件（v0.9.32 就栽在这）
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError('payload empty: %d entries' % len(names))
    moved = 0
    try:
        for item in src.iterdir():
            target = dest / item.name
            # 兜底：源和目标同一路径时先删后搬 = 自己删自己，必然 WinError 2
            if os.path.normcase(str(item)) == os.path.normcase(str(target)):
                continue
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists() or target.is_symlink():
                try:
                    target.unlink()
                except OSError:
                    pass
            shutil.move(str(item), str(target))
            moved += 1
    finally:
        # 无论成败都清掉暂存，否则中途报错会留下 300 MB 垃圾
        shutil.rmtree(staging, ignore_errors=True)
    return moved


APP_LANGS = [('zh', '中文'), ('en', 'English'), ('es', 'Español'), ('pt', 'Português'), ('hi', 'हिन्दी')]


def write_lang(dest, code):
    """把安装时选的语言写成 lang.txt，程序第一次启动读它决定界面语言。"""
    try:
        (Path(dest) / 'lang.txt').write_text(str(code), encoding='utf-8')
        return True
    except Exception:
        return False


def make_shortcut(exe, name):
    made = False
    try:
        from win32com.client import Dispatch
        shell = Dispatch('WScript.Shell')
        for lnk in (Path(os.environ.get('USERPROFILE', '')) / 'Desktop' / ('%s.lnk' % name),
                    Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Start Menu'
                    / 'Programs' / ('%s.lnk' % name)):
            try:
                lnk.parent.mkdir(parents=True, exist_ok=True)
                sc = shell.CreateShortCut(str(lnk))
                sc.TargetPath = str(exe)
                sc.WorkingDirectory = str(exe.parent)
                sc.IconLocation = '%s,0' % exe
                sc.Description = name
                sc.Save()
                made = True
            except Exception:
                continue
    except Exception:
        pass
    return made


def set_autostart(exe, on=True):
    """装完默认写 HKCU\\...\\Run：开机后台启动（`--silent` 不弹窗）。

    键名 SecondBrain 与程序里「设置 → 开机启动」用的是同一个，用户在设置里
    关掉再打开时路径仍然指向这次安装的 exe。
    取消勾选 / SB_NO_AUTOSTART=1（静默安装验收）时什么都不做，尤其**不删**
    已有的同名值 —— 那可能是别处安装或用户自己配的命令，不该由安装器抹掉。
    """
    if not on or os.environ.get('SB_NO_AUTOSTART') == '1':
        return False
    try:
        import winreg
        sub = r'Software\Microsoft\Windows\CurrentVersion\Run'
        cmd = '"%s" --silent' % exe
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, sub) as k:
            winreg.SetValueEx(k, 'SecondBrain', 0, winreg.REG_SZ, cmd)
        return True
    except Exception:
        return False


def _launch(exe):
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
    except Exception:
        pass


def install_cli(payload, dest, lang='zh'):
    def p(done, total, name):
        if done % 100 == 0 or done == total:
            print('%d/%d' % (done, total), flush=True)

    ok, n, has_exe = payload_ok(payload)
    if not ok:
        print('payload incomplete: %d entries, main exe %s'
              % (n, 'ok' if has_exe else 'MISSING'), flush=True)
        return 5
    extract(payload, dest, p)
    write_lang(dest, os.environ.get('SB_APP_LANG') or lang)
    exe = dest / 'SecondBrain.exe'
    if not exe.exists():
        print(L[lang]['no_exe'], flush=True)
        return 4
    if os.environ.get('SB_NO_SHORTCUT') != '1':
        make_shortcut(exe, L[lang]['app'])
    auto = set_autostart(exe)
    print('installed to %s (autostart=%s)' % (dest, auto), flush=True)
    if not NO_LAUNCH:
        _launch(exe)
    return 0


def install_gui(payload, lang):
    import tkinter as tk
    from tkinter import filedialog

    dest = default_dir()
    st = {'busy': False, 'done': False, 'exe': None, 'lang': lang, 'sc': False}
    texts = []          # [(widget, key)] 切换语言时统一刷新

    def tr(key):
        return L[st['lang']].get(key, key)

    root = tk.Tk()
    root.configure(bg=BG)
    root.resizable(False, False)
    W, H = 560, 406
    try:
        ico = Path(getattr(sys, '_MEIPASS', '.')) / 'app.ico'
        if ico.exists():
            root.iconbitmap(default=str(ico))
    except Exception:
        pass

    head = tk.Frame(root, bg=DARK, height=106)
    head.pack(fill='x')
    head.pack_propagate(False)
    try:
        p = Path(getattr(sys, '_MEIPASS', '.')) / 'logo64.png'
        logo = tk.PhotoImage(file=str(p)) if p.exists() else None
    except Exception:
        logo = None
    if logo is not None:
        tk.Label(head, image=logo, bg=DARK).place(x=24, y=14)
    # 品牌名固定双行：主名随界面语言（中文界面「第二大脑」、其它「Second Brain」），
    # 副行固定另一种写法，五行一致，别再让它跟着语言变成两个一样的字。
    name_lbl = tk.Label(head, bg=DARK, fg='#ffffff', font=('Microsoft YaHei UI', 17, 'bold'))
    name_lbl.place(x=98, y=12)
    en_lbl = tk.Label(head, bg=DARK, fg='#c9d6f5', font=('Microsoft YaHei UI', 10))
    en_lbl.place(x=99, y=43)
    sub_lbl = tk.Label(head, bg=DARK, fg='#9fb0d8', font=('Microsoft YaHei UI', 9))
    sub_lbl.place(x=99, y=70)
    texts += [(name_lbl, 'app'), (en_lbl, 'en'), (sub_lbl, 'sub')]

    body = tk.Frame(root, bg=BG)
    body.pack(fill='both', expand=True, padx=26, pady=(16, 0))

    where_lbl = tk.Label(body, bg=BG, fg=INK, font=('Microsoft YaHei UI', 10, 'bold'))
    where_lbl.grid(row=0, column=0, columnspan=4, sticky='w')
    texts.append((where_lbl, 'where'))

    path_var = tk.StringVar(value=str(dest))
    wrap = tk.Frame(body, bg=BG, highlightthickness=1,
                    highlightbackground=LINE, highlightcolor=ACC)
    wrap.grid(row=1, column=0, columnspan=3, sticky='we', pady=(6, 8))
    entry = tk.Entry(wrap, textvariable=path_var, relief='flat', bd=0, bg=BG, fg=INK,
                     font=('Microsoft YaHei UI', 10), insertbackground=INK)
    entry.pack(side='left', fill='x', expand=True, ipady=7, padx=(10, 6))
    entry.bind('<FocusIn>', lambda e: wrap.configure(highlightbackground=ACC))
    entry.bind('<FocusOut>', lambda e: wrap.configure(highlightbackground=LINE))

    def browse():
        if st['busy']:
            return
        cur = path_var.get().strip() or str(dest)
        base = cur if os.path.isdir(cur) else str(Path(cur).parent)
        picked = filedialog.askdirectory(initialdir=base, title=tr('where'), mustexist=False)
        if picked:
            p = Path(picked)
            # 目录名固定 ASCII（跟默认的 SecondBrain 一致），不跟界面语言变。
            # 已经是 App / SecondBrain 的目录就直接用，不再套一层
            keep_as_is = p.name.lower() in ('app', DIR_NAME.lower())
            path_var.set(str(p if keep_as_is else (p / DIR_NAME)))

    def flat_btn(parent, key, cmd, bg=SOFT, fg=INK, hover='#eceff8'):
        b = tk.Label(parent, bg=bg, fg=fg, cursor='hand2',
                     font=('Microsoft YaHei UI', 10), padx=16, pady=6)
        b.bind('<Button-1>', lambda e: cmd())
        b.bind('<Enter>', lambda e: b.configure(bg=hover))
        b.bind('<Leave>', lambda e: b.configure(bg=bg))
        texts.append((b, key))
        return b

    flat_btn(body, 'browse', browse).grid(row=1, column=3, sticky='w', padx=(8, 0), pady=(6, 8))

    hint_lbl = tk.Label(body, bg=BG, fg='#98a1b3', font=('Microsoft YaHei UI', 9),
                        wraplength=496, justify='left')
    hint_lbl.grid(row=2, column=0, columnspan=4, sticky='w', pady=(0, 12))
    texts.append((hint_lbl, 'hint'))

    opt_row = tk.Frame(body, bg=BG)
    opt_row.grid(row=3, column=0, columnspan=3, sticky='w')
    keep = tk.BooleanVar(value=True)
    chk = tk.Checkbutton(opt_row, variable=keep, bg=BG, fg=INK, activebackground=BG,
                         activeforeground=INK, selectcolor=BG,
                         font=('Microsoft YaHei UI', 9), bd=0, highlightthickness=0)
    chk.pack(side='left')
    texts.append((chk, 'keep'))
    # 开机启动默认勾选：装完就在托盘常驻，双击 Ctrl 随时搜。用户可以取消，
    # 也可以以后在「设置 → 开机启动」里再打开（同一个注册表键）。
    boot = tk.BooleanVar(value=True)
    chk_boot = tk.Checkbutton(opt_row, variable=boot, bg=BG, fg=INK,
                              activebackground=BG, activeforeground=INK,
                              selectcolor=BG, font=('Microsoft YaHei UI', 9),
                              bd=0, highlightthickness=0)
    chk_boot.pack(side='left', padx=(18, 0))
    texts.append((chk_boot, 'boot'))

    # 程序语言：这一行选的不是安装器语言，而是「装好之后程序用什么语言」
    # （写进 <安装目录>/lang.txt，程序首启读它，免得用户装完看到一屏中文直接卸载）
    applang_row = tk.Frame(body, bg=BG)
    applang_row.grid(row=3, column=3, sticky='e')
    app_lang_lbl = tk.Label(applang_row, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 9))
    app_lang_lbl.pack(side='left', padx=(0, 6))
    texts.append((app_lang_lbl, 'applang'))
    # 默认跟安装器语言走；用户自己在下拉里选过之后就不再被语言切换带走（st['applang_user']）
    _force_applang = (os.environ.get('SB_APP_LANG') or '').lower()
    st['applang'] = _force_applang if _force_applang in dict(APP_LANGS) else st['lang']
    st['applang_user'] = bool(_force_applang)
    app_lang_var = tk.StringVar(value=dict(APP_LANGS)[st['applang']])

    def pick_applang(name):
        st['applang'] = {n: c for c, n in APP_LANGS}[name]
        st['applang_user'] = True

    app_lang_box = tk.OptionMenu(applang_row, app_lang_var, *[n for _, n in APP_LANGS],
                                 command=pick_applang)
    app_lang_box.configure(font=('Microsoft YaHei UI', 9), bd=0, relief='flat',
                           bg=SOFT, fg=INK, activebackground=SOFT, highlightthickness=0,
                           width=9)
    app_lang_box['menu'].configure(font=('Microsoft YaHei UI', 9))
    app_lang_box.pack(side='left')

    bar_cv = tk.Canvas(body, height=6, bg=SOFT, highlightthickness=0, bd=0)
    bar_cv.grid(row=4, column=0, columnspan=4, sticky='we', pady=(14, 6))
    last = {'pct': 0.0}

    def rebar():
        bar_cv.delete('all')
        w = max(bar_cv.winfo_width(), 1)
        bar_cv.create_rectangle(0, 0, w, 6, fill=SOFT, outline='')
        if last['pct'] > 0:
            bar_cv.create_rectangle(0, 0, int(w * last['pct']), 6, fill=ACC, outline='')

    bar_cv.bind('<Configure>', lambda e: rebar())

    status = tk.Label(body, text='', bg=BG, fg=SUB, anchor='w',
                      font=('Microsoft YaHei UI', 9), wraplength=496, justify='left')
    status.grid(row=5, column=0, columnspan=4, sticky='we')

    foot = tk.Frame(root, bg=BG)
    foot.pack(fill='x', padx=26, pady=(0, 16))
    hint = tk.Label(foot, bg=BG, fg='#98a1b3', font=('Microsoft YaHei UI', 8),
                    wraplength=300, justify='left', anchor='w')
    hint.pack(side='left', fill='x', expand=True)
    texts.append((hint, 'foot'))

    acts = tk.Frame(foot, bg=BG)
    acts.pack(side='right')
    cancel = flat_btn(acts, 'cancel', lambda: (root.destroy() if not st['busy'] else None),
                      bg=SOFT, fg=SUB, hover='#eceff8')
    cancel.pack(side='right', padx=(10, 0))

    go_btn = tk.Label(acts, bg=ACC, fg='#ffffff', cursor='hand2',
                      font=('Microsoft YaHei UI', 10, 'bold'), padx=26, pady=7)
    go_btn.pack(side='right')
    texts.append((go_btn, 'install'))

    def set_busy(b):
        st['busy'] = b
        go_btn.configure(bg=ACC if not b else '#c8d2f8')

    def open_app():
        if st['exe']:
            _launch(st['exe'])

    def go():
        if st['busy']:
            return
        if st['done']:
            open_app()
            root.destroy()
            return
        target = Path(path_var.get().strip())
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / '.sb_write_test'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
        except Exception as e:
            status.configure(text=tr('bad_dir'), fg='#c0392b')
            hint.configure(text=str(e)[:90])
            return
        set_busy(True)
        go_btn.configure(text=tr('preparing'))
        status.configure(text=tr('preparing'), fg=SUB)
        # 先验包：清单里没有主程序就直接说「安装包不完整」，别让用户白等三分钟再看到杀毒软件
        ok, n, has_exe = payload_ok(payload)
        if not ok:
            set_busy(False)
            go_btn.configure(text=tr('install'))
            status.configure(text=tr('bad_payload'), fg='#c0392b')
            hint.configure(text='payload.zip: %d entries · main exe %s'
                               % (n, 'ok' if has_exe else 'MISSING'))
            return
        threading.Thread(target=worker, args=(target,), daemon=True).start()

    def finish_ok(target, exe, sc, auto):
        st['done'] = True
        st['exe'] = exe
        st['sc'] = sc
        last['pct'] = 1.0
        rebar()
        # 装完先看 WebView2。装得再成功，缺这个组件双击就是白屏 —— 用户只会认为
        # 「装坏了 / 这软件是骗人的」，而完全不知道世界上有这么一个组件。
        # 所以这里宁可打断一下，把下载页递到手上，也不自动启动去看那张白屏。
        wv = wv2_version()
        if not wv:
            zh = st.get('lang') == 'zh'
            status.configure(text='已安装，但还缺 WebView2 运行时' if zh
                                 else 'Installed, but WebView2 Runtime is missing',
                             fg='#b26a00')
            hint.configure(text=(tr('sc_ok') if sc else tr('sc_no')) + '\n'
                                + (tr('as_ok') if auto else tr('as_no')))
            try:
                go_btn.configure(
                    text=('下载 WebView2' if zh else 'Get WebView2'), bg=ACC,
                    command=lambda: os.startfile(WV2_URL))
            except Exception:
                pass
            set_busy(False)
            try:
                from tkinter import messagebox
                ask = messagebox.askyesno(
                    '还需要 WebView2' if zh else 'WebView2 Runtime needed',
                    ('第二大脑的界面靠 Windows 的 WebView2 组件渲染，这台电脑上没有。\n'
                     '不装的话打开会是白屏。安装包约 2 MB，装一次以后都不用再装。\n\n'
                     '现在打开官方下载页吗？') if zh else
                    ('The interface is rendered by the WebView2 runtime, which is '
                     'missing on this PC.\nWithout it the app opens to a blank window.\n\n'
                     'Open the official download page now?'))
                if ask:
                    os.startfile(WV2_URL)
            except Exception:
                pass
            return                      # 不自动启动：必然白屏，不如先让用户装组件
        status.configure(text=tr('done') % target, fg='#1a7f37')
        hint.configure(text=(tr('sc_ok') if sc else tr('sc_no')) + '\n'
                            + (tr('as_ok') if auto else tr('as_no')))
        go_btn.configure(text=tr('open'), bg=ACC)
        set_busy(False)
        if not NO_LAUNCH:
            root.after(700, lambda: (open_app(), root.destroy()))

    def finish_err(err):
        set_busy(False)
        last['pct'] = 0.0
        rebar()
        status.configure(text=tr('failed') % err, fg='#c0392b')

    def worker(target):
        try:
            def prog(done, total, name):
                last['pct'] = done / max(total, 1)
                if done % 20 == 0 or done == total:
                    root.after(0, lambda nm=name: (
                        rebar(),
                        status.configure(text=tr('installing') % os.path.basename(nm), fg=SUB)))
            extract(payload, target, prog)
            exe = target / 'SecondBrain.exe'
            # 杀软扫描完才放行/删文件，给几秒缓冲再判定，别急着报错
            for _ in range(5):
                if exe.exists():
                    break
                time.sleep(0.6)
            if not exe.exists():
                n_files = sum(1 for q in Path(target).rglob('*') if q.is_file())
                # 解出几百个文件却没主程序 = 被删/被隔离；一个文件都没有 = 包的问题
                raise RuntimeError(tr('av_del') % n_files if n_files > 50
                                   else tr('empty_pack'))
            write_lang(target, st.get('applang') or st['lang'])
            sc = False
            if keep.get() and os.environ.get('SB_NO_SHORTCUT') != '1':
                sc = make_shortcut(exe, L[st['lang']]['app'])
            auto = set_autostart(exe, boot.get())
            root.after(0, lambda: finish_ok(target, exe, sc, auto))
        except Exception as e:
            root.after(0, lambda err=e: finish_err(err))

    go_btn.bind('<Button-1>', lambda e: go())
    go_btn.bind('<Enter>', lambda e: (not st['busy']) and go_btn.configure(bg=ACC_D))
    go_btn.bind('<Leave>', lambda e: (not st['busy']) and go_btn.configure(bg=ACC))

    # 右上角语言开关
    lang_btn = tk.Label(head, bg=DARK, fg='#9fb0d8', cursor='hand2',
                        font=('Microsoft YaHei UI', 9))
    lang_btn.place(relx=1.0, x=-22, y=22, anchor='ne')

    def apply_lang():
        root.title(tr('title'))
        for w, key in texts:
            if key == 'install':
                if st['done']:
                    w.configure(text=tr('open'))
                elif not st['busy']:
                    w.configure(text=tr('install'))
            else:
                try:
                    w.configure(text=tr(key))
                except Exception:
                    pass
        lang_btn.configure(text=L[st['lang']].get('switch', 'EN'))
        # 「程序语言」跟安装器语言保持一致：用户没手动选过，就跟着切
        if not st.get('applang_user'):
            st['applang'] = st['lang']
            try:
                app_lang_var.set(dict(APP_LANGS)[st['applang']])
            except Exception:
                pass
        try:
            app_lang_lbl.configure(text=tr('applang'))
        except Exception:
            pass
        if st['done'] and st['exe']:
            hint.configure(text=tr('sc_ok') if st['sc'] else tr('sc_no'))

    LANGS_CYCLE = ['zh', 'en', 'es', 'pt', 'hi']

    def switch_lang():
        i = LANGS_CYCLE.index(st['lang']) if st['lang'] in LANGS_CYCLE else 0
        st['lang'] = LANGS_CYCLE[(i + 1) % len(LANGS_CYCLE)]
        apply_lang()

    lang_btn.bind('<Button-1>', lambda e: switch_lang())
    apply_lang()

    root.update_idletasks()
    root.geometry('%dx%d+%d+%d' % (W, H, (root.winfo_screenwidth() - W) // 2,
                                   (root.winfo_screenheight() - H) // 3))
    if os.environ.get('SB_AUTOCLICK') == '1':
        root.after(1200, go)
    root.mainloop()


def main():
    payload = payload_path()
    lang = system_lang()
    if not payload.exists():
        print('payload.zip missing', flush=True)
        return 2
    if SILENT:
        return install_cli(payload, default_dir(), lang)
    install_gui(payload, lang)
    return 0


if __name__ == '__main__':
    sys.exit(main())
