# -*- coding: utf-8 -*-
"""Memex 安装程序（单文件 exe）。

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

APP_ZH = 'Memex'
APP_EN = 'Memex'
DIR_NAME = 'Memex'   # 安装目录固定英文名，不跟界面语言变
VERSION = '0.9.56'

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
        'app': 'Memex',
        'title': 'Memex 安装',
        'app': APP_ZH,
        'sub': '本地文件搜索 · 安装程序 v%s' % VERSION,
        'en': 'Memex',
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
        'open': '打开 Memex',
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
        'f11': '秒级搜索 · 即输即出', 'f12': '全文检索 · 不止文件', 'f13': '实时索引 · 新建即搜',
        'f21': '聚合翻译 · 截图即取', 'f22': '轻量常驻 · 不抢资源', 'f23': '隐私优先 · 只在本机',
        'ver_fail': '暂时查不到最新版本，可以继续安装，装好后在设置里再看一次',
        'switch': 'ZH',
    },
    'en': {
        'app': 'Memex',
        'title': 'Install Memex',
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
        'open': 'Open Memex',
        'boot': 'Start at boot (default on)',
        'as_ok': 'Start at boot enabled',
        'as_no': 'Start at boot off (enable it in Settings)',
        'sc_ok': 'Desktop shortcut created',
        'sc_no': 'Could not create a shortcut (you can run the exe in this folder)',
        'foot': 'Installs for the current user, no admin rights needed',
        'bad_dir': 'This folder is not writable — pick another one',
        'failed': 'Install failed: %s',
        'no_exe': 'Memex.exe missing after extraction (antivirus?)',
        'bad_payload': 'This installer is incomplete — the main program is missing from the package. Please download it again.',
        'empty_pack': 'The package is empty: nothing was extracted (broken build). Please use a freshly built installer.',
        'av_del': 'The main program vanished after extraction (%d files were written). An antivirus most likely removed it — whitelist the folder and retry.',
        'f11': 'Instant search · as you type', 'f12': 'Full-text · inside any file', 'f13': 'Live index · new files in',
        'f21': 'Translate · screenshot capture', 'f22': 'Always on · low footprint', 'f23': 'Privacy first · on-device only',
        'ver_fail': 'Could not reach the version server. You can install now and check again in Settings',
        'switch': 'EN',
    },
    'es': {
        'app': 'Memex',
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
        'open': 'Abrir Memex',
        'boot': 'Iniciar al arrancar (activado)',
        'as_ok': 'Inicio automático activado',
        'as_no': 'Inicio automático desactivado (actívalo en Ajustes)',
        'sc_ok': 'Acceso directo creado',
        'sc_no': 'No se pudo crear el acceso directo (puedes ejecutar el exe de esta carpeta)',
        'foot': 'Para el usuario actual, sin permisos de admin',
        'bad_dir': 'No se puede escribir en esta carpeta, elige otra',
        'failed': 'Error de instalación: %s',
        'no_exe': 'No se encontró Memex.exe tras descomprimir (¿antivirus?)',
        'bad_payload': 'El instalador está incompleto: falta el programa principal. Vuelve a descargarlo.',
        'empty_pack': 'El paquete está vacío: no se extrajo ningún archivo (compilación defectuosa). Usa un instalador nuevo.',
        'av_del': 'El programa principal desapareció tras descomprimir (se escribieron %d archivos). Probablemente lo eliminó el antivirus: añade la carpeta a las exclusiones y reintenta.',
        'f11': 'Búsqueda al instante · al teclear', 'f12': 'Texto completo · en el archivo', 'f13': 'Índice vivo · archivo nuevo',
        'f21': 'Traducción · desde captura', 'f22': 'Siempre activo · ligero', 'f23': 'Privacidad · solo en tu PC',
        'ver_fail': 'No se pudo consultar la versión. Puedes instalar y revisarlo luego en Ajustes',
        'switch': 'ES',
    },
    'pt': {
        'app': 'Memex',
        'title': 'Instalar Memex',
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
        'open': 'Abrir Memex',
        'boot': 'Iniciar com o Windows (padrão)',
        'as_ok': 'Início automático ativado',
        'as_no': 'Início automático desativado (ative em Configurações)',
        'sc_ok': 'Atalho criado',
        'sc_no': 'Não foi possível criar o atalho (você pode executar o exe desta pasta)',
        'foot': 'Para o usuário atual, sem direitos de admin',
        'bad_dir': 'Não é possível gravar nesta pasta, escolha outra',
        'failed': 'Falha na instalação: %s',
        'no_exe': 'Memex.exe não encontrado após extrair (antivírus?)',
        'bad_payload': 'O instalador está incompleto: falta o programa principal. Baixe novamente.',
        'empty_pack': 'O pacote está vazio: nenhum arquivo foi extraído (build defeituosa). Use um instalador novo.',
        'av_del': 'O programa principal desapareceu após extrair (%d arquivos gravados). Provavelmente o antivírus removeu: adicione a pasta às exceções e tente de novo.',
        'f11': 'Busca na hora · ao digitar', 'f12': 'Texto completo · no arquivo', 'f13': 'Índice ao vivo · arquivo novo',
        'f21': 'Tradução · por captura', 'f22': 'Sempre ativo · leve', 'f23': 'Privacidade · só no seu PC',
        'ver_fail': 'Não foi possível consultar a versão. Pode instalar e ver depois nas Configurações',
        'switch': 'PT',
    },
    'hi': {
        'app': 'Memex',
        'title': 'Memex इंस्टॉल करें',
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
        'open': 'Memex खोलें',
        'boot': 'बूट पर चालू (डिफ़ॉल्ट)',
        'as_ok': 'ऑटो-स्टार्ट चालू',
        'as_no': 'ऑटो-स्टार्ट बंद (सेटिंग में चालू करें)',
        'sc_ok': 'डेस्कटॉप शॉर्टकट बन गया',
        'sc_no': 'शॉर्टकट नहीं बन सका (इस फ़ोल्डर का exe चला सकते हैं)',
        'foot': 'मौजूदा उपयोगकर्ता के लिए, एडमिन अधिकार नहीं चाहिए',
        'bad_dir': 'इस फ़ोल्डर में लिखा नहीं जा सकता, दूसरा चुनें',
        'failed': 'इंस्टॉल विफल: %s',
        'no_exe': 'निकालने के बाद Memex.exe नहीं मिला (एंटीवायरस?)',
        'bad_payload': 'इंस्टॉलर अधूरा है — पैकेज में मुख्य प्रोग्राम नहीं है। कृपया दोबारा डाउनलोड करें।',
        'empty_pack': 'पैकेज खाली है: कोई फ़ाइल एक्सट्रैक्ट नहीं हुई (खराब बिल्ड)। नया इंस्टॉलर लें।',
        'av_del': 'एक्सट्रैक्ट के बाद मुख्य प्रोग्राम गायब हो गया (%d फ़ाइलें लिखी गईं)। संभवतः एंटीवायरस ने हटाया — फ़ोल्डर को अनुमति सूची में जोड़कर फिर कोशिश करें।',
        'f11': 'तुरंत खोज · टाइप करते ही', 'f12': 'पूरा पाठ · फ़ाइल के अंदर', 'f13': 'लाइव इंडेक्स · नई फ़ाइल',
        'f21': 'अनुवाद · स्क्रीनशॉट से', 'f22': 'हमेशा चालू · हल्का', 'f23': 'निजता · सिर्फ़ आपके PC',
        'ver_fail': 'संस्करण सर्वर तक नहीं पहुँचे। अभी इंस्टॉल करें, बाद में सेटिंग्स में जाँचें',
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
    return Path(base) / 'Memex' / 'App'


def payload_path():
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'payload.zip'
    return Path(__file__).resolve().parent / 'payload.zip'


def _members(z):
    return [n for n in z.namelist()
            if n.startswith('Memex/') and '..' not in n and not n.startswith('/')]


def payload_ok(payload):
    """预检：包里的清单必须带主程序。返回 (是否可用, 条目数, 是否含主程序)。"""
    try:
        with zipfile.ZipFile(payload) as z:
            names = _members(z)
    except Exception:
        return False, 0, False
    return ('Memex/Memex.exe' in names and len(names) > 100,
            len(names), 'Memex/Memex.exe' in names)


def extract(payload, dest, progress=None):
    dest.mkdir(parents=True, exist_ok=True)
    # 暂存目录必须「无论如何都不和 dest 撞名」：解得进 staging，再从 staging/Memex 搬进 dest。
    # 旧写法把解压根设成 staging.parent，当 dest 自己就叫 Memex 时（用户点「浏览…」选了
    # 一个父目录，browse() 会自动补一层 Memex），解出来的 Memex 就是 dest 本身 ——
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
    src = staging / 'Memex'
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

    键名 Memex 与程序里「设置 → 开机启动」用的是同一个，用户在设置里
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
            winreg.SetValueEx(k, 'Memex', 0, winreg.REG_SZ, cmd)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub, 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, 'SecondBrain')
        except OSError:
            pass
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
    exe = dest / 'Memex.exe'
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


ART_BG = '#F1F3F7'          # 右侧插画面板的底色（与 install_art.png 的底一致）
REPO_SLUG = 'bslnssb/second-brain'
VER_SOURCES = (
    'https://cdn.jsdelivr.net/gh/%s@master/version.json' % REPO_SLUG,
    'https://raw.gitmirror.com/%s/master/version.json' % REPO_SLUG,
    'https://raw.githubusercontent.com/%s/master/version.json' % REPO_SLUG,
)
GH_LATEST = 'https://api.github.com/repos/%s/releases/latest' % REPO_SLUG
UN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Memex'


def _vt(s):
    """'v0.9.55' / '0.9.55-beta' → (0, 9, 55)，比大小用。"""
    s = str(s or '').strip().lower().lstrip('v')
    out = []
    for part in s.replace('-', '.').replace('_', '.').split('.'):
        num = ''
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def fetch_latest_version(timeout=6.0):
    """查最新版本，返回 (tag, url)；查不到返回 ('', '')。

    三个镜像 + GitHub Releases API 并发跑，谁先拿到算谁，整体预算 6s。
    （原先顺序试镜像，3 个各 1.4s 超时就把 GitHub 兜底挤掉，界面只剩一个“—”。）
    """
    import json
    import threading
    import urllib.request

    box = {}
    lock = threading.Lock()

    def put(tag, url):
        if not tag:
            return False
        with lock:
            if 'tag' not in box:
                box['tag'] = tag
                box['url'] = url or ''
                return True
        return False

    def from_json(src):
        try:
            req = urllib.request.Request(src, headers={'User-Agent': 'Memex-Setup'})
            with urllib.request.urlopen(req, timeout=2.6) as r:
                v = json.loads(r.read(4096).decode('utf-8', 'replace'))
            put(str(v.get('version') or v.get('tag') or '').strip(),
                str(v.get('url') or v.get('page') or v.get('html_url') or ''))
        except Exception:
            pass

    def from_github():
        try:
            req = urllib.request.Request(
                GH_LATEST, headers={'User-Agent': 'Memex-Setup',
                                    'Accept': 'application/vnd.github+json'})
            with urllib.request.urlopen(req, timeout=3.2) as r:
                v = json.loads(r.read(1 << 16).decode('utf-8', 'replace'))
            put(str(v.get('tag_name') or '').strip(), str(v.get('html_url') or ''))
        except Exception:
            pass

    jobs = [(from_json, (src,)) for src in VER_SOURCES] + [(from_github, ())]
    ths = []
    for fn, a in jobs:
        t = threading.Thread(target=fn, args=a, daemon=True)
        t.start()
        ths.append(t)
    deadline = time.time() + timeout
    for t in ths:
        left = deadline - time.time()
        if left <= 0:
            break
        t.join(left)
    with lock:
        return box.get('tag', ''), box.get('url', '')


def installed_dir():
    """本机已装的 Memex：(目录, 版本) 或 None。只认注册表里登记过、且目录里真有 exe 的。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UN_KEY) as k:
            loc = str(winreg.QueryValueEx(k, 'InstallLocation')[0] or '')
            ver = str(winreg.QueryValueEx(k, 'DisplayVersion')[0] or '')
    except Exception:
        return None
    if loc:
        d = Path(loc)
        if (d / 'Memex.exe').is_file():
            return (d, ver)
    return None


def memex_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Memex.exe'],
                             capture_output=True, text=True, timeout=8, encoding='utf-8',
                             errors='replace').stdout or ''
        return 'Memex.exe' in out
    except Exception:
        return False


def close_memex():
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'Memex.exe'],
                       capture_output=True, timeout=15)
        time.sleep(1.2)
    except Exception:
        pass


U = {
    'zh': {
        'title': 'Memex 安装', 'brand_sub': '本地文件搜索',
        'welcome': '安装 Memex',
        'welcome_sub': '这一步只做检查：核对新版本、看看本机是不是已经装过。不会改动任何文件。',
        'chk_upd': '正在检查新版本…', 'chk_inst': '正在检查本机安装情况…',
        'latest': '已是最新版本 %s', 'newer': '发现新版本 %s（这份安装包是 %s）',
        'dl_new': '下载新版 ›', 'inst_yes': '本机已安装 Memex %s，这一步会覆盖更新',
        'inst_no': '本机没有装过 Memex，这是一次全新安装',
        'next': '下一步', 'back': '‹ 返回', 'cancel': '取消',
        'opts': '安装选项', 'opts_sub': '选好位置和选项，点安装就开始。',
        'where': '安装位置', 'hint': '程序、数据库、设置都放这里；以后卸载就删掉整个文件夹。',
        'browse': '浏览…', 'cust': '自定义', 'cust_hide': '收起',
        'keep': '创建桌面快捷方式', 'boot': '开机启动', 'applang': '程序语言',
        'install': '安装', 'preparing': '准备安装…', 'installing': '正在安装：%s',
        'done': '装好了：%s', 'open': '打开 Memex',
        'sc_ok': '已创建桌面快捷方式', 'sc_no': '没能创建快捷方式（可以直接双击目录里的 exe）',
        'as_ok': '已设置开机启动', 'as_no': '未开启开机启动（可在设置里打开）',
        'foot': '装到当前用户，不需要管理员权限',
        'upd_lock': '已检测到安装，目录固定在这里：升级只覆盖程序文件，索引和设置都会保留。',
        'update': '更新', 'updating': '正在更新…',
        'bad_dir': '这个文件夹写不进去，换一个吧', 'failed': '安装失败：%s',
        'no_exe': '解压后没找到主程序（可能被杀毒软件拦了）',
        'bad_payload': '安装包不完整：里面没有主程序，请重新下载安装器',
        'empty_pack': '安装包是空的：一个文件都没解压出来，请换一个新版安装器',
        'av_del': '解压后主程序不见了（目录里已有 %d 个文件），基本是杀毒软件删的：把安装目录加入白名单后重试',
        'f11': '秒级搜索 · 即输即出', 'f12': '全文检索 · 不止文件', 'f13': '实时索引 · 新建即搜',
        'f21': '聚合翻译 · 截图即取', 'f22': '轻量常驻 · 不抢资源', 'f23': '隐私优先 · 只在本机',
        'ver_fail': '暂时查不到最新版本，可以继续安装，装好后在设置里再看一次',
    },
    'en': {
        'title': 'Memex Setup', 'brand_sub': 'Local file search',
        'welcome': 'Install Memex',
        'welcome_sub': 'This step only checks things: the latest version and whether Memex is already on this PC. Nothing is modified.',
        'chk_upd': 'Checking for a newer version…', 'chk_inst': 'Checking what is already installed…',
        'latest': 'You have the latest version %s', 'newer': 'Newer version %s available (this installer is %s)',
        'dl_new': 'Download ›', 'inst_yes': 'Memex %s is already installed — this will update it',
        'inst_no': 'Memex is not installed on this PC — fresh install',
        'next': 'Next', 'back': '‹ Back', 'cancel': 'Cancel',
        'opts': 'Install options', 'opts_sub': 'Pick a location and options, then install.',
        'where': 'Install location', 'hint': 'Program, database and settings live here. To uninstall, delete this folder.',
        'browse': 'Browse…', 'cust': 'Custom', 'cust_hide': 'Hide',
        'keep': 'Create a desktop shortcut', 'boot': 'Start with Windows', 'applang': 'App language',
        'install': 'Install', 'preparing': 'Preparing…', 'installing': 'Installing: %s',
        'done': 'Done: %s', 'open': 'Open Memex',
        'sc_ok': 'Desktop shortcut created', 'sc_no': 'No shortcut (you can run the exe in that folder)',
        'as_ok': 'Starts with Windows', 'as_no': 'Will not start with Windows (can enable later in Settings)',
        'foot': 'Installs for the current user — no admin rights needed',
        'upd_lock': 'Existing install found, the folder is fixed: updating only replaces program files, your index and settings stay.',
        'update': 'Update', 'updating': 'Updating…',
        'bad_dir': 'That folder is not writable, pick another', 'failed': 'Install failed: %s',
        'no_exe': 'Main executable missing after extraction (antivirus?)',
        'bad_payload': 'This installer is incomplete (no main executable). Please download it again',
        'empty_pack': 'The payload is empty. Please use a newer installer',
        'av_del': 'The main executable vanished after extraction (%d files present) — antivirus removed it. Whitelist the folder and retry',
        'f11': 'Instant search · as you type', 'f12': 'Full-text · inside any file', 'f13': 'Live index · new files in',
        'f21': 'Translate · screenshot capture', 'f22': 'Always on · low footprint', 'f23': 'Privacy first · on-device only',
        'ver_fail': 'Could not reach the version server. You can install now and check again in Settings',
    },
    'es': {
        'title': 'Instalación de Memex', 'brand_sub': 'Búsqueda local de archivos',
        'welcome': 'Instalar Memex',
        'welcome_sub': 'Este paso solo comprueba dos cosas: la versión más reciente y si Memex ya está instalado. No se modifica nada.',
        'chk_upd': 'Buscando una versión más reciente…', 'chk_inst': 'Comprobando la instalación existente…',
        'latest': 'Ya tienes la última versión %s', 'newer': 'Hay una versión nueva %s (este instalador es %s)',
        'dl_new': 'Descargar ›', 'inst_yes': 'Memex %s ya está instalado: se actualizará',
        'inst_no': 'Memex no está instalado: instalación nueva',
        'next': 'Siguiente', 'back': '‹ Atrás', 'cancel': 'Cancelar',
        'opts': 'Opciones de instalación', 'opts_sub': 'Elige la ubicación y las opciones y pulsa Instalar.',
        'where': 'Ubicación', 'hint': 'Programa, base de datos y ajustes viven aquí. Para desinstalar, borra esta carpeta.',
        'browse': 'Examinar…', 'cust': 'Personalizar', 'cust_hide': 'Ocultar',
        'keep': 'Crear acceso directo', 'boot': 'Iniciar con Windows', 'applang': 'Idioma de la app',
        'install': 'Instalar', 'preparing': 'Preparando…', 'installing': 'Instalando: %s',
        'done': 'Listo: %s', 'open': 'Abrir Memex',
        'sc_ok': 'Acceso directo creado', 'sc_no': 'Sin acceso directo (puedes abrir el exe de la carpeta)',
        'as_ok': 'Se inicia con Windows', 'as_no': 'No se iniciará con Windows (puedes activarlo luego)',
        'foot': 'Se instala para el usuario actual, sin permisos de administrador',
        'upd_lock': 'Instalación existente detectada: la carpeta queda fija. Actualizar solo reemplaza los programas; tu índice y ajustes se conservan.',
        'update': 'Actualizar', 'updating': 'Actualizando…',
        'bad_dir': 'Esa carpeta no se puede escribir, elige otra', 'failed': 'Error al instalar: %s',
        'no_exe': 'No se encontró el ejecutable tras descomprimir (¿antivirus?)',
        'bad_payload': 'El instalador está incompleto. Descárgalo de nuevo',
        'empty_pack': 'El paquete está vacío. Usa un instalador más reciente',
        'av_del': 'El ejecutable desapareció (%d archivos presentes): lo borró el antivirus. Añade la carpeta a exclusiones y reintenta',
        'f11': 'Búsqueda al instante · al teclear', 'f12': 'Texto completo · en el archivo', 'f13': 'Índice vivo · archivo nuevo',
        'f21': 'Traducción · desde captura', 'f22': 'Siempre activo · ligero', 'f23': 'Privacidad · solo en tu PC',
        'ver_fail': 'No se pudo consultar la versión. Puedes instalar y revisarlo luego en Ajustes',
    },
    'pt': {
        'title': 'Instalação do Memex', 'brand_sub': 'Busca local de arquivos',
        'welcome': 'Instalar o Memex',
        'welcome_sub': 'Esta etapa só verifica duas coisas: a versão mais recente e se o Memex já está instalado. Nada é alterado.',
        'chk_upd': 'Procurando uma versão mais nova…', 'chk_inst': 'Verificando a instalação existente…',
        'latest': 'Você já tem a versão mais recente %s', 'newer': 'Nova versão %s disponível (este instalador é %s)',
        'dl_new': 'Baixar ›', 'inst_yes': 'O Memex %s já está instalado — será atualizado',
        'inst_no': 'O Memex não está instalado — instalação nova',
        'next': 'Avançar', 'back': '‹ Voltar', 'cancel': 'Cancelar',
        'opts': 'Opções de instalação', 'opts_sub': 'Escolha o local e as opções, depois clique em Instalar.',
        'where': 'Local de instalação', 'hint': 'Programa, banco de dados e configurações ficam aqui. Para desinstalar, apague esta pasta.',
        'browse': 'Procurar…', 'cust': 'Personalizar', 'cust_hide': 'Recolher',
        'keep': 'Criar atalho na área de trabalho', 'boot': 'Iniciar com o Windows', 'applang': 'Idioma do app',
        'install': 'Instalar', 'preparing': 'Preparando…', 'installing': 'Instalando: %s',
        'done': 'Pronto: %s', 'open': 'Abrir o Memex',
        'sc_ok': 'Atalho criado', 'sc_no': 'Sem atalho (você pode abrir o exe da pasta)',
        'as_ok': 'Inicia com o Windows', 'as_no': 'Não inicia com o Windows (pode ativar depois)',
        'foot': 'Instala para o usuário atual, sem precisar de administrador',
        'upd_lock': 'Instalação existente encontrada: a pasta fica fixa. Atualizar só substitui os arquivos do programa; seu índice e configurações ficam.',
        'update': 'Atualizar', 'updating': 'Atualizando…',
        'bad_dir': 'Essa pasta não permite gravação, escolha outra', 'failed': 'Falha na instalação: %s',
        'no_exe': 'Executável não encontrado após extrair (antivírus?)',
        'bad_payload': 'Instalador incompleto. Baixe novamente',
        'empty_pack': 'O pacote está vazio. Use um instalador mais recente',
        'av_del': 'O executável sumiu (%d arquivos presentes): o antivírus removeu. Adicione a pasta à lista branca e tente de novo',
        'f11': 'Busca na hora · ao digitar', 'f12': 'Texto completo · no arquivo', 'f13': 'Índice ao vivo · arquivo novo',
        'f21': 'Tradução · por captura', 'f22': 'Sempre ativo · leve', 'f23': 'Privacidade · só no seu PC',
        'ver_fail': 'Não foi possível consultar a versão. Pode instalar e ver depois nas Configurações',
    },
    'hi': {
        'title': 'Memex इंस्टॉल', 'brand_sub': 'स्थानीय फ़ाइल खोज',
        'welcome': 'Memex इंस्टॉल करें',
        'welcome_sub': 'यह चरण सिर्फ़ जाँच करता है: नया संस्करण और क्या Memex पहले से इंस्टॉल है। कुछ भी बदला नहीं जाता।',
        'chk_upd': 'नया संस्करण जाँचा जा रहा है…', 'chk_inst': 'मौजूदा इंस्टॉल जाँचा जा रहा है…',
        'latest': 'आपके पास नवीनतम संस्करण %s है', 'newer': 'नया संस्करण %s उपलब्ध (यह इंस्टॉलर %s है)',
        'dl_new': 'डाउनलोड ›', 'inst_yes': 'Memex %s पहले से इंस्टॉल है — इसे अपडेट किया जाएगा',
        'inst_no': 'Memex इस कंप्यूटर पर नहीं है — नई इंस्टॉल',
        'next': 'आगे', 'back': '‹ वापस', 'cancel': 'रद्द करें',
        'opts': 'इंस्टॉल विकल्प', 'opts_sub': 'जगह और विकल्प चुनें, फिर इंस्टॉल दबाएँ।',
        'where': 'इंस्टॉल स्थान', 'hint': 'प्रोग्राम, डेटाबेस और सेटिंग्स यहीं रहते हैं। अनइंस्टॉल के लिए यह फ़ोल्डर मिटा दें।',
        'browse': 'चुनें…', 'cust': 'कस्टम', 'cust_hide': 'छिपाएँ',
        'keep': 'डेस्कटॉप शॉर्टकट बनाएँ', 'boot': 'Windows के साथ शुरू करें', 'applang': 'ऐप भाषा',
        'install': 'इंस्टॉल', 'preparing': 'तैयार हो रहा है…', 'installing': 'इंस्टॉल हो रहा है: %s',
        'done': 'हो गया: %s', 'open': 'Memex खोलें',
        'sc_ok': 'शॉर्टकट बन गया', 'sc_no': 'शॉर्टकट नहीं बना (फ़ोल्डर का exe सीधे खोल सकते हैं)',
        'as_ok': 'Windows के साथ शुरू होगा', 'as_no': 'Windows के साथ शुरू नहीं होगा (सेटिंग से बाद में चालू करें)',
        'foot': 'मौजूदा उपयोगकर्ता के लिए इंस्टॉल, एडमिन अधिकार नहीं चाहिए',
        'upd_lock': 'मौजूदा इंस्टॉल मिला: फ़ोल्डर तय है। अपडेट सिर्फ़ प्रोग्राम फ़ाइलें बदलता है; आपका इंडेक्स और सेटिंग्स बने रहते हैं।',
        'update': 'अपडेट', 'updating': 'अपडेट हो रहा है…',
        'bad_dir': 'इस फ़ोल्डर में लिखा नहीं जा सकता, दूसरा चुनें', 'failed': 'इंस्टॉल विफल: %s',
        'no_exe': 'खोलने के बाद मुख्य exe नहीं मिला (एंटीवायरस?)',
        'bad_payload': 'इंस्टॉलर अधूरा है। दोबारा डाउनलोड करें',
        'empty_pack': 'पैकेज खाली है। नया इंस्टॉलर लें',
        'av_del': 'मुख्य exe गायब हो गया (%d फ़ाइलें मौजूद): एंटीवायरस ने हटाया। फ़ोल्डर को अनुमति दें और फिर कोशिश करें',
        'f11': 'तुरंत खोज · टाइप करते ही', 'f12': 'पूरा पाठ · फ़ाइल के अंदर', 'f13': 'लाइव इंडेक्स · नई फ़ाइल',
        'f21': 'अनुवाद · स्क्रीनशॉट से', 'f22': 'हमेशा चालू · हल्का', 'f23': 'निजता · सिर्फ़ आपके PC',
        'ver_fail': 'संस्करण सर्वर तक नहीं पहुँचे। अभी इंस्टॉल करें, बाद में सेटिंग्स में जाँचें',
    },
}


for _d in U.values():
    _d.setdefault('app', 'Memex')


def install_gui(payload, lang):
    import tkinter as tk
    from tkinter import filedialog, messagebox

    if lang not in U:
        lang = 'zh'
    dest = default_dir()

    st = {'lang': lang, 'busy': False, 'done': False, 'exe': None, 'sc': False,
          'page': 1, 'field_open': False, 'ver_tag': '', 'ver_url': '',
          'newer': False, 'scanned': False}
    texts = []          # [(widget, key)]，切语言时统一刷新
    W, H = 840, 520
    LW = 540                                  # 左侧内容区宽度，右边 300 是插画面板

    def tr(key):
        return U.get(st['lang'], U['zh']).get(key, key)

    def trf(key, *a):
        try:
            return tr(key) % a
        except Exception:
            return tr(key)

    text_font = ('Microsoft YaHei UI', 10)
    title_font = ('Microsoft YaHei UI', 23, 'bold')

    root = tk.Tk()
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        ico = Path(getattr(sys, '_MEIPASS', '.')) / 'app.ico'
        if ico.exists():
            root.iconbitmap(default=str(ico))
    except Exception:
        pass

    # ── 右侧插画面板：整块浅灰，插画缩在下沿，上面留白 ──
    right = tk.Frame(root, bg=ART_BG)
    right.place(x=LW, y=0, width=W - LW, height=H)
    # 第 360 项：两页各一张插画 —— 第一页男（桌面场景），第二页女（放大镜翻文档）。
    # 两张都按 300×520 画布、内容压在下沿、底色 #F1F3F7，翻页只换图不换排版。
    def _load_art(name):
        try:
            p = Path(getattr(sys, '_MEIPASS', '.')) / name
            return tk.PhotoImage(file=str(p)) if p.exists() else None
        except Exception:
            return None

    RW = W - LW
    ART = {1: _load_art('install_art.png'), 2: _load_art('install_art2.png')}
    # 新图没打进包（老构建目录残留）就退回第一页那张，右栏不能空着
    ART[2] = ART[2] or ART[1]
    art_label = tk.Label(right, bg=ART_BG, bd=0)
    if ART[1] is not None:
        art_label.configure(image=ART[1])
        art_label.place(x=(RW - ART[1].width()) // 2, y=H - ART[1].height())

    # ── 右栏卖点：第一页讲搜索，第二页讲翻译 / 常驻 / 隐私；随页码和语言切换 ──
    FEAT_KEYS = {1: ('f11', 'f12', 'f13'), 2: ('f21', 'f22', 'f23')}
    feat_holder = tk.Frame(right, bg=ART_BG, bd=0, highlightthickness=0)
    feat_holder.place(x=0, y=0, width=RW, height=250)

    def feat_render(n):
        for w in feat_holder.winfo_children():
            w.destroy()
        import tkinter.font as tkfont
        keys = FEAT_KEYS[2 if n == 2 else 1]
        lines_ = [tr(k) for k in keys]
        fnt = ('Microsoft YaHei UI', 9)
        for size in (9, 8.5, 8, 7.5, 7):          # 长语言（西/葡/印地）自动缩字号
            fnt = ('Microsoft YaHei UI', size)
            tw = max(tkfont.Font(font=fnt).measure(t) for t in lines_)
            if tw <= RW - 56:
                break
        block_w = 15 + tw
        x0 = max(14, (RW - block_w) // 2)        # 灰色面板里水平居中
        y0 = 112
        for i, txt in enumerate(lines_):
            yy = y0 + i * 34
            tk.Frame(feat_holder, bg='#3F62E8' if i == 0 else '#c9d0dd',
                     width=3, height=12).place(x=x0, y=yy + 4)
            tk.Label(feat_holder, text=txt, bg=ART_BG, fg='#5c6678',
                     font=fnt).place(x=x0 + 15, y=yy)

    left = tk.Frame(root, bg=BG)
    left.place(x=0, y=0, width=LW, height=H)

    # ── 两个页面 ──
    p1 = tk.Frame(left, bg=BG)
    p2 = tk.Frame(left, bg=BG)
    p1.place(x=0, y=0, width=LW, height=H)

    # =================== 第一页：检查 ===================
    t1 = tk.Label(p1, bg=BG, fg=INK, font=title_font)
    t1.place(x=36, y=148)
    texts.append((t1, 'welcome'))
    s1 = tk.Label(p1, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 9),
                  wraplength=440, justify='left')
    s1.place(x=36, y=196)
    texts.append((s1, 'welcome_sub'))

    row1 = tk.Frame(p1, bg=BG)
    row1.place(x=36, y=252)
    dot1 = tk.Label(row1, text='•', bg=BG, fg='#9aa3b2',
                    font=('Microsoft YaHei UI', 11))
    dot1.pack(side='left', padx=(0, 8))
    ver_lbl = tk.Label(row1, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 10))
    ver_lbl.pack(side='left')
    texts.append((ver_lbl, 'chk_upd'))
    ver_link = tk.Label(p1, bg=BG, fg=ACC, cursor='hand2',
                        font=('Microsoft YaHei UI', 10, 'underline'))
    ver_link.bind('<Button-1>', lambda e: st['ver_url'] and os.startfile(st['ver_url']))

    row2 = tk.Frame(p1, bg=BG)
    row2.place(x=36, y=288)
    dot2 = tk.Label(row2, text='•', bg=BG, fg='#9aa3b2',
                    font=('Microsoft YaHei UI', 11))
    dot2.pack(side='left', padx=(0, 8))
    inst_lbl = tk.Label(row2, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 10))
    inst_lbl.pack(side='left')
    texts.append((inst_lbl, 'chk_inst'))

    # =================== 第二页：选项 ===================
    t2 = tk.Label(p2, bg=BG, fg=INK, font=('Microsoft YaHei UI', 17, 'bold'))
    t2.place(x=36, y=96)
    texts.append((t2, 'opts'))
    s2 = tk.Label(p2, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 9))
    s2.place(x=36, y=128)
    texts.append((s2, 'opts_sub'))

    where_lbl = tk.Label(p2, bg=BG, fg=INK, font=('Microsoft YaHei UI', 9, 'bold'))
    where_lbl.place(x=36, y=168)
    texts.append((where_lbl, 'where'))

    # 路径一行：平时只显示「折叠后的路径 + 自定义」，点开才长出输入框
    path_row = tk.Frame(p2, bg=BG)
    path_show = tk.Label(path_row, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 9),
                         anchor='w', justify='left')
    path_show.pack(side='left')
    texts.append((path_show, '_path'))     # 特殊：由 layout 里重算文本

    def short_path(s, n=46):
        if len(s) <= n:
            return s
        return s[:9] + '…' + s[-34:]

    def pill_parent():
        return p2

    def make_pill(parent, key, cmd):
        b = tk.Label(parent, bg=SOFT, fg=SUB, cursor='hand2',
                     font=('Microsoft YaHei UI', 9), padx=10, pady=3)
        b.bind('<Button-1>', lambda e: cmd())
        b.bind('<Enter>', lambda e: b.configure(bg='#eceff8'))
        b.bind('<Leave>', lambda e: b.configure(bg=SOFT))
        texts.append((b, key))
        return b

    path_var = tk.StringVar(value=str(dest))
    wrap = tk.Frame(p2, bg=BG, highlightthickness=1,
                    highlightbackground=LINE, highlightcolor=ACC)
    entry = tk.Entry(wrap, textvariable=path_var, relief='flat', bd=0, bg=BG, fg=INK,
                     font=('Microsoft YaHei UI', 10), insertbackground=INK)
    entry.pack(side='left', fill='x', expand=True, ipady=6, padx=(10, 6))
    entry.bind('<FocusIn>', lambda e: wrap.configure(highlightbackground=ACC))
    entry.bind('<FocusOut>', lambda e: wrap.configure(highlightbackground=LINE))

    def get_path():
        try:
            return entry.get().strip() or str(dest)
        except Exception:
            return str(dest)

    def set_path(v):
        try:
            entry.delete(0, 'end')
            entry.insert(0, v)
        except Exception:
            pass

    def browse():
        if st['busy']:
            return
        cur = get_path()
        base = cur if os.path.isdir(cur) else str(Path(cur).parent)
        picked = filedialog.askdirectory(initialdir=base, title=tr('where'), mustexist=False)
        if picked:
            q = Path(picked)
            keep_as_is = q.name.lower() in ('app', DIR_NAME.lower())
            set_path(str(q if keep_as_is else (q / DIR_NAME)))

    browse_btn = make_pill(p2, 'browse', browse)

    cust_pill = make_pill(p2, 'cust', lambda: set_field_open(not st['field_open']))

    hint_lbl = tk.Label(p2, bg=BG, fg='#98a1b3', font=('Microsoft YaHei UI', 9),
                        wraplength=468, justify='left', anchor='w')
    texts.append((hint_lbl, 'hint'))

    upd_lbl = tk.Label(p2, bg=BG, fg='#2F5BD7', font=('Microsoft YaHei UI', 9),
                       wraplength=468, justify='left', anchor='w')
    texts.append((upd_lbl, 'upd_lock'))

    # ── 自绘复选框（原生 Checkbutton 的系统方块是「土」的主要来源）──
    class Check:
        def __init__(self, parent, var, key):
            self.var = var
            self.cv = tk.Canvas(parent, width=16, height=16, bg=BG, highlightthickness=0,
                                bd=0, cursor='hand2')
            self.lbl = tk.Label(parent, bg=BG, fg=INK, font=('Microsoft YaHei UI', 9),
                                cursor='hand2')
            texts.append((self.lbl, key))
            self.lbl.bind('<Button-1>', lambda e: self.toggle())
            self.cv.bind('<Button-1>', lambda e: self.toggle())
            var.trace_add('write', lambda *a: self.draw())
            self.draw()

        def toggle(self):
            self.var.set(not self.var.get())

        def draw(self):
            c = self.cv
            c.delete('all')
            on = bool(self.var.get())
            c.create_rectangle(1, 1, 15, 15, outline=ACC if on else '#cbd1de',
                               fill=ACC if on else BG, width=1)
            if on:
                c.create_line(4, 8, 7, 11, 12, 5, fill='#ffffff', width=2)

        def place(self, x, y):
            self.cv.place(x=x, y=y)
            self.lbl.place(x=x + 24, y=y - 2)

    keep = tk.BooleanVar(value=True)
    boot = tk.BooleanVar(value=True)
    chk_keep = Check(p2, keep, 'keep')
    chk_boot = Check(p2, boot, 'boot')

    app_lang_lbl = tk.Label(p2, bg=BG, fg=SUB, font=('Microsoft YaHei UI', 9))
    texts.append((app_lang_lbl, 'applang'))
    _force_applang = (os.environ.get('SB_APP_LANG') or '').lower()
    st['applang'] = _force_applang if _force_applang in dict(APP_LANGS) else st['lang']
    st['applang_user'] = bool(_force_applang)
    app_lang_btn = tk.Label(p2, bg=SOFT, fg=INK, cursor='hand2',
                            font=('Microsoft YaHei UI', 9), padx=10, pady=3)

    def app_lang_menu(x, y):
        m = tk.Menu(root, tearoff=0, font=('Microsoft YaHei UI', 9))
        names = [n for _, n in APP_LANGS]

        def pick(name):
            st['applang'] = {n: c for c, n in APP_LANGS}[name]
            st['applang_user'] = True
            app_lang_btn.configure(text=name)

        var = tk.StringVar(value=dict(APP_LANGS)[st['applang']])
        for n in names:
            m.add_radiobutton(label=n, value=n, variable=var, command=lambda n=n: pick(n))
        m.tk_popup(x, y)

    app_lang_btn.bind('<Button-1>', lambda e: app_lang_menu(e.x_root, e.y_root))

    bar_cv = tk.Canvas(p2, height=6, bg=SOFT, highlightthickness=0, bd=0)
    last = {'pct': 0.0}

    def rebar():
        bar_cv.delete('all')
        w = max(bar_cv.winfo_width(), 1)
        bar_cv.create_rectangle(0, 0, w, 6, fill=SOFT, outline='')
        if last['pct'] > 0:
            bar_cv.create_rectangle(0, 0, int(w * last['pct']), 6, fill=ACC, outline='')

    bar_cv.bind('<Configure>', lambda e: rebar())

    status = tk.Label(p2, bg=BG, fg=SUB, anchor='w', font=('Microsoft YaHei UI', 9),
                      wraplength=468, justify='left')

    def layout():
        """按当前状态摆第二页的每一行（收起/展开自定义、更新模式、进度条）。"""
        update_mode = bool(st.get('installed'))
        y = 160
        where_lbl.place(x=36, y=y)
        y += 26
        path_row.place(x=36, y=y, width=468)
        path_show.configure(text=short_path(str(st['path'])) if update_mode else
                            short_path(get_path()))
        if update_mode:
            cust_pill.place_forget()
        else:
            cust_pill.place(x=LW - 36 - 74, y=y - 4, width=74, height=24)
        y += 34
        if st['field_open'] and not update_mode:
            wrap.place(x=36, y=y, width=398, height=32)
            browse_btn.place(x=440, y=y + 2, width=64, height=26)
            y += 44
        else:
            wrap.place_forget()
            browse_btn.place_forget()
        if update_mode:
            upd_lbl.place(x=36, y=y, width=468)
            y += 40
        else:
            upd_lbl.place_forget()
        if update_mode:
            hint_lbl.place_forget()
        else:
            hint_lbl.place(x=36, y=y, width=468)
            y += 34
        chk_keep.place(36, y)
        chk_boot.place(210, y)
        app_lang_lbl.place(x=364, y=y + 1)
        app_lang_btn.place(x=364 + 56, y=y - 3)
        app_lang_btn.configure(text=dict(APP_LANGS)[st['applang']])
        y += 40
        bar_cv.place(x=36, y=y, width=468, height=6)
        if last['pct'] <= 0:
            bar_cv.place_forget()
        y += 18
        status.place(x=36, y=y, width=468)

    # ── 底部按钮 ──
    def round_btn(parent, key, cmd, primary=True):
        b = tk.Label(parent, bg=ACC if primary else BG,
                     fg='#ffffff' if primary else SUB,
                     cursor='hand2', font=('Microsoft YaHei UI', 10,
                                           'bold' if primary else 'normal'),
                     padx=24 if primary else 10, pady=7 if primary else 4)
        b.bind('<Button-1>', lambda e: cmd())
        if primary:
            b.bind('<Enter>', lambda e: b.configure(bg=ACC_D))
            b.bind('<Leave>', lambda e: b.configure(bg=ACC))
        texts.append((b, key))
        return b

    def set_field_open(v):
        st['field_open'] = v
        cust_pill.configure(text=tr('cust_hide') if v else tr('cust'))
        layout()

    # 第一页底部
    next_btn = round_btn(p1, 'next', lambda: show_page(2))
    next_btn.place(relx=1.0, rely=1.0, x=-36, y=-26, anchor='se')
    cancel1 = round_btn(p1, 'cancel', lambda: root.destroy(), primary=False)
    cancel1.place(x=30, rely=1.0, y=-24, anchor='sw')
    foot1 = tk.Label(p1, bg=BG, fg='#a6adbb', font=('Microsoft YaHei UI', 8))
    foot1.place(x=36, rely=1.0, y=-52, anchor='sw')
    texts.append((foot1, 'foot'))

    # 第二页底部
    go_btn = tk.Label(p2, bg=ACC, fg='#ffffff', cursor='hand2',
                      font=('Microsoft YaHei UI', 10, 'bold'), padx=26, pady=7)
    go_btn.place(relx=1.0, rely=1.0, x=-36, y=-26, anchor='se')
    back_btn = round_btn(p2, 'back', lambda: show_page(1), primary=False)
    back_btn.place(relx=1.0, rely=1.0, x=-160, y=-26, anchor='se')
    cancel2 = round_btn(p2, 'cancel', lambda: root.destroy() if not st['busy'] else None,
                        primary=False)
    cancel2.place(x=30, rely=1.0, y=-24, anchor='sw')
    foot2 = tk.Label(p2, bg=BG, fg='#a6adbb', font=('Microsoft YaHei UI', 8))
    foot2.place(x=36, rely=1.0, y=-52, anchor='sw')
    texts.append((foot2, 'foot'))

    def show_page(n):
        st['page'] = n
        # 第 360 项：右栏插画跟着页码换（第一页男 / 第二页女）
        _art = ART.get(n) or ART.get(1)
        if _art is not None:
            art_label.configure(image=_art)
            art_label.place(x=(RW - _art.width()) // 2, y=H - _art.height())
        if n == 1:
            p2.place_forget()
            p1.place(x=0, y=0, width=LW, height=H)
        else:
            p1.place_forget()
            p2.place(x=0, y=0, width=LW, height=H)
            layout()
        feat_render(n)

    # ── 品牌（两页都在）：只留文字，不再摆第二个放大镜图标 ──
    # 用户两次提「两个图标凑得太近」：标题栏那个 ico 和内容区这个 logo 叠在同一列上，
    # 看着挤。卡巴斯基那边也只有一行字。所以内容区只保留「Memex + 本地文件搜索」，
    # 图标交给标题栏；右边空出来的位置放版本号 + 语言开关，不再留一大片白。
    brand = tk.Label(left, bg=BG, fg=INK, font=('Microsoft YaHei UI', 15, 'bold'))
    brand.place(x=36, y=24)
    texts.append((brand, 'app'))
    brand_sub = tk.Label(left, bg=BG, fg='#9aa3b2', font=('Microsoft YaHei UI', 8))
    brand_sub.place(x=36, y=52)
    texts.append((brand_sub, 'brand_sub'))
    ver_tag_lbl = tk.Label(left, bg=BG, fg='#aeb5c2',
                           font=('Microsoft YaHei UI', 8))
    ver_tag_lbl.place(relx=1.0, x=-96, y=34, anchor='e')
    ver_tag_lbl.configure(text='v%s' % VERSION)

    lang_btn = tk.Label(left, bg=BG, fg=SUB, cursor='hand2',
                        font=('Microsoft YaHei UI', 9), padx=8, pady=3)
    lang_btn.place(relx=1.0, x=-30, y=28, anchor='ne')
    lang_btn.configure(text={c: n for c, n in APP_LANGS}.get(st['lang'], '中文'))

    def lang_menu(x, y):
        m = tk.Menu(root, tearoff=0, font=('Microsoft YaHei UI', 10))
        for code, name in APP_LANGS:
            m.add_command(label=name, command=lambda c=code: (st.update(lang=c), apply_lang()))
        m.tk_popup(x, y)

    lang_btn.bind('<Button-1>', lambda e: lang_menu(e.x_root, e.y_root))

    # =================== 检查（第一页后台跑） ===================
    def run_checks():
        tag, url = ('', '')
        if os.environ.get('SB_NO_UPDATE') != '1':
            tag, url = fetch_latest_version()
        st['ver_tag'], st['ver_url'] = tag, url
        root.after(0, paint_checks)

    def paint_checks():
        st['scanned'] = True
        if not st['ver_tag']:
            dot1.configure(text='—', fg='#c3c9d4')
            ver_lbl.configure(text=tr('ver_fail'), fg='#a6adbb')      # 查不到就说清楚
        else:
            if _vt(st['ver_tag']) > _vt(VERSION):
                st['newer'] = True
                dot1.configure(text='↑', fg='#e08a1e')
                ver_lbl.configure(text=trf('newer', st['ver_tag'], VERSION), fg='#b26a00')
                ver_link.configure(text=tr('dl_new'))
                ver_link.place(x=36 + 22 + ver_lbl.winfo_reqwidth() + 10, y=254)
            else:
                dot1.configure(text='✓', fg='#22a06b')
                ver_lbl.configure(text=trf('latest', st['ver_tag']), fg='#1a7f37')
                ver_link.place_forget()
        if st.get('installed'):
            dot2.configure(text='✓', fg='#22a06b')
            inst_lbl.configure(text=trf('inst_yes', st['iv'] or '?'), fg=INK)
        else:
            dot2.configure(text='•', fg='#9aa3b2')
            inst_lbl.configure(text=tr('inst_no'), fg=SUB)
        if os.environ.get('SB_AUTOCLICK') == '1' and st['page'] == 1:
            root.after(600, lambda: show_page(2))

    st['installed'] = None if os.environ.get('SB_FAKE_FRESH') == '1' else installed_dir()
    st['iv'] = st['installed'][1] if st['installed'] else ''
    st['path'] = str(st['installed'][0]) if st['installed'] else str(dest)
    set_path(st['path'])

    # =================== 安装 ===================
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
        target = Path(get_path())
        if st['installed']:
            old, ver = st['installed']
            zh = st['lang'] == 'zh'
            msg = ('这台电脑上已经装了 Memex %s（%s）。\n\n'
                   '继续会覆盖更新到 v%s：程序文件会被替换，索引和设置都保留。'
                   '安装前会先关掉正在运行的 Memex。\n\n要继续吗？'
                   % (ver or '?', old, VERSION)) if zh else (
                  'Memex %s is already installed at %s.\n\n'
                  'This will update it to v%s: program files are replaced, your index '
                  'and settings are kept. The running app will be closed first.\n\n'
                  'Continue?' % (ver or '?', old, VERSION))
            if not messagebox.askyesno(tr('update'), msg):
                return
            if memex_running():
                status.configure(text=tr('updating'), fg=SUB)
                close_memex()
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / '.sb_write_test'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
        except Exception as e:
            status.configure(text=tr('bad_dir'), fg='#c0392b')
            hint_lbl.configure(text=str(e)[:90])
            return
        set_busy(True)
        go_btn.configure(text=tr('preparing'))
        status.configure(text=tr('preparing'), fg=SUB)
        last['pct'] = 0.002
        layout()
        ok, n, has_exe = payload_ok(payload)
        if not ok:
            set_busy(False)
            go_btn.configure(text=tr('install'))
            status.configure(text=tr('bad_payload'), fg='#c0392b')
            hint_lbl.configure(text='payload.zip: %d entries · main exe %s'
                                    % (n, 'ok' if has_exe else 'MISSING'))
            return
        threading.Thread(target=worker, args=(target,), daemon=True).start()

    def finish_ok(target, exe, sc, auto):
        st['done'] = True
        st['exe'] = exe
        st['sc'] = sc
        last['pct'] = 1.0
        rebar()
        wv = wv2_version()
        if not wv:
            zh = st['lang'] == 'zh'
            status.configure(text='已安装，但还缺 WebView2 运行时' if zh
                                 else 'Installed, but WebView2 Runtime is missing',
                             fg='#b26a00')
            hint_lbl.configure(text=(tr('sc_ok') if sc else tr('sc_no')) + '\n'
                                    + (tr('as_ok') if auto else tr('as_no')))
            try:
                go_btn.configure(text=('下载 WebView2' if zh else 'Get WebView2'),
                                 bg=ACC, cursor='hand2')
                go_btn.bind('<Button-1>', lambda e: os.startfile(WV2_URL))
            except Exception:
                pass
            set_busy(False)
            try:
                ask = messagebox.askyesno(
                    '还需要 WebView2' if zh else 'WebView2 Runtime needed',
                    ('Memex 的界面靠 Windows 的 WebView2 组件渲染，这台电脑上没有。\n'
                     '不装的话打开会是白屏。安装包约 2 MB，装一次以后都不用再装。\n\n'
                     '现在打开官方下载页吗？') if zh else
                    ('The interface is rendered by the WebView2 runtime, which is '
                     'missing on this PC.\nWithout it the app opens to a blank window.\n\n'
                     'Open the official download page now?'))
                if ask:
                    os.startfile(WV2_URL)
            except Exception:
                pass
            return
        status.configure(text=trf('done', target), fg='#1a7f37')
        hint_lbl.configure(text=(tr('sc_ok') if sc else tr('sc_no')) + '\n'
                                + (tr('as_ok') if auto else tr('as_no')))
        go_btn.configure(text=tr('open'), bg=ACC)
        set_busy(False)
        if not NO_LAUNCH:
            root.after(700, lambda: (open_app(), root.destroy()))

    def finish_err(err):
        set_busy(False)
        last['pct'] = 0.0
        rebar()
        status.configure(text=trf('failed', err), fg='#c0392b')

    def worker(target):
        try:
            def prog(done, total, name):
                last['pct'] = done / max(total, 1)
                if done % 20 == 0 or done == total:
                    root.after(0, lambda nm=name: (
                        rebar(),
                        status.configure(text=trf('installing', os.path.basename(nm)), fg=SUB)))
            extract(payload, target, prog)
            exe = target / 'Memex.exe'
            for _ in range(5):
                if exe.exists():
                    break
                time.sleep(0.6)
            if not exe.exists():
                n_files = sum(1 for q in Path(target).rglob('*') if q.is_file())
                raise RuntimeError(trf('av_del', n_files) if n_files > 50
                                   else tr('empty_pack'))
            write_lang(target, st.get('applang') or st['lang'])
            sc = False
            if keep.get() and os.environ.get('SB_NO_SHORTCUT') != '1':
                sc = make_shortcut(exe, DIR_NAME)
            auto = set_autostart(exe, boot.get())
            root.after(0, lambda: finish_ok(target, exe, sc, auto))
        except Exception as e:
            root.after(0, lambda err=e: finish_err(err))

    go_btn.bind('<Button-1>', lambda e: go())
    go_btn.bind('<Enter>', lambda e: (not st['busy']) and go_btn.configure(bg=ACC_D))
    go_btn.bind('<Leave>', lambda e: (not st['busy']) and go_btn.configure(bg=ACC))

    def apply_lang():
        root.title(tr('title'))
        for w, key in texts:
            try:
                if key == '_path':
                    w.configure(text=short_path(st['path']))
                elif key == 'install':
                    pass
                else:
                    w.configure(text=tr(key))
            except Exception:
                pass
        lang_btn.configure(text={c: n for c, n in APP_LANGS}.get(st['lang'], '中文'))
        if not st.get('applang_user'):
            st['applang'] = st['lang']
        app_lang_btn.configure(text=dict(APP_LANGS)[st['applang']])
        cust_pill.configure(text=tr('cust_hide') if st['field_open'] else tr('cust'))
        # 按钮文案跟状态走
        go_btn.configure(text=tr('open') if st['done']
                         else (tr('update') if st.get('installed') else tr('install')))
        if st['scanned']:
            paint_checks()
        feat_render(st['page'])
        layout()

    apply_lang()
    feat_render(st['page'])
    layout()
    if os.environ.get('SB_OPEN_PATH') == '1' and not st['installed']:
        set_field_open(True)

    root.update_idletasks()
    root.geometry('%dx%d+%d+%d' % (W, H, (root.winfo_screenwidth() - W) // 2,
                                   (root.winfo_screenheight() - H) // 3))

    if SILENT:
        # 老的自检/自动化路径：不弹窗，直接装
        root.destroy()
        return
    threading.Thread(target=run_checks, daemon=True).start()
    if os.environ.get('SB_PAGE') == '2':
        root.after(300, lambda: show_page(2))
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
