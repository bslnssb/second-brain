# -*- coding: utf-8 -*-
"""常用应用白名单：搜「微信」时，先给微信的程序本身，而不是微信聊天记录/微信文件夹。

为什么是「手写清单」而不是去抓「全球 Top 500」：
- 没有一份权威榜单同时覆盖「Windows 桌面程序 + 中文用户习惯 + exe 文件名」；
  榜单是 App Store / 下载站的排名，和「用户电脑里那个 .exe 叫什么」对不上。
- 真正决定排序的是 **exe 文件名**（WeChat.exe / Weixin.exe / QQScLauncher.exe…），
  这些只能一个个核对。所以这里的做法是：把常见应用按「别名 + 可执行文件名 + 安装目录」
  三样写清楚，命中就加权。清单可以随时加，加一行就生效。
- 兜底逻辑仍在：非清单里的程序照样按「启动器目录 / 应用扩展名 / 最近打开」加权，
  所以清单不全也不会让新程序消失，只是没有「插队」待遇。

`match(q)` 的规则（大小写无关、全角已归一）：
  1. 别名完全相等            → 命中（权重最高）
  2. 别名以查询词开头（≥2 字）→ 命中
  3. 查询词以别名的英文开头（例如 `wech`）→ 命中
"""

# (中文名, 英文/其它别名空格分隔, 可执行文件名空格分隔)
_RAW = [
    # —— 沟通 / 办公 ——
    ('微信', 'wechat weixin wx', 'wechat.exe weixin.exe'),
    ('企业微信', 'wework wxwork qywx', 'wxwork.exe wxworklauncher.exe'),
    ('QQ', 'qq tencentqq', 'qq.exe qqsc.exe qqsclauncher.exe'),
    ('TIM', 'tim', 'tim.exe'),
    ('钉钉', 'dingtalk dingding', 'dingtalk.exe'),
    ('飞书', 'feishu lark', 'feishu.exe lark.exe bytedancefeishu.exe'),
    ('蓝信', 'lanxin', 'lanxin.exe'),
    ('腾讯会议', 'tencentmeeting voov', 'wemeetapp.exe tencentmeeting.exe'),
    ('zoom', 'zoom', 'zoom.exe'),
    ('outlook', 'outlook', 'outlook.exe'),
    ('foxmail', 'foxmail', 'foxmail.exe'),
    ('网易邮箱大师', 'mailmaster', 'mailmaster.exe'),
    ('telegram', 'telegram', 'telegram.exe'),
    ('discord', 'discord', 'discord.exe'),
    ('skype', 'skype', 'skype.exe'),
    ('whatsapp', 'whatsapp', 'whatsapp.exe'),
    ('slack', 'slack', 'slack.exe'),
    ('teams', 'teams microsoftteams', 'ms-teams.exe teams.exe'),
    # —— 浏览器 ——
    ('chrome', 'chrome googlechrome', 'chrome.exe'),
    ('edge', 'edge msedge', 'msedge.exe'),
    ('firefox', 'firefox', 'firefox.exe'),
    ('360浏览器', '360se 360chrome', '360se.exe 360chrome.exe'),
    ('QQ浏览器', 'qqbrowser', 'qqbrowser.exe'),
    ('搜狗浏览器', 'sogouexplorer', 'sogouexplorer.exe'),
    ('夸克', 'quark', 'quark.exe'),
    ('opera', 'opera', 'opera.exe'),
    ('brave', 'brave', 'brave.exe'),
    # —— 办公套件 / 文档 ——
    ('wps', 'wps kingsoft', 'wps.exe et.exe wpp.exe etmain.exe wppmain.exe'),
    ('word', 'word msword', 'winword.exe'),
    ('excel', 'excel', 'excel.exe'),
    ('powerpoint', 'powerpoint ppt', 'powerpnt.exe'),
    ('onenote', 'onenote', 'onenote.exe'),
    ('visio', 'visio', 'visio.exe'),
    ('acrobat', 'acrobat adobeacrobat', 'acrobat.exe acrord32.exe'),
    ('foxit', 'foxit foxitreader', 'foxitreader.exe foxitpdfreader.exe'),
    ('typora', 'typora', 'typora.exe'),
    ('obsidian', 'obsidian', 'obsidian.exe'),
    ('notion', 'notion', 'notion.exe'),
    ('印象笔记', 'evernote yinxiang', 'evernote.exe yinxiang.exe'),
    ('有道云笔记', 'youdaonote youdao', 'youdaonote.exe'),
    ('豆包', 'doubao', 'doubao.exe'),
    ('网易有道翻译', 'youdao fanyi', 'youdaodict.exe youdaotranslator.exe'),
    ('石墨文档', 'shimo', 'shimo.exe'),
    ('腾讯文档', 'tencentdocs docs', 'tencentdocs.exe'),
    ('xmind', 'xmind', 'xmind.exe'),
    ('processon', 'processon', 'processon.exe'),
    # —— 开发 / 终端 ——
    ('vscode', 'vscode code visualstudiocode', 'code.exe'),
    ('visualstudio', 'visualstudio devenv', 'devenv.exe'),
    ('pycharm', 'pycharm', 'pycharm64.exe pycharm.exe'),
    ('intellij', 'idea intellij intellijidea', 'idea64.exe idea.exe'),
    ('webstorm', 'webstorm', 'webstorm64.exe'),
    ('goland', 'goland', 'goland64.exe'),
    ('clion', 'clion', 'clion64.exe'),
    ('androidstudio', 'androidstudio', 'studio64.exe'),
    ('eclipse', 'eclipse', 'eclipse.exe'),
    ('git', 'git gitbash', 'git-bash.exe git-cmd.exe'),
    ('github desktop', 'githubdesktop', 'githubdesktop.exe'),
    ('sourcetree', 'sourcetree', 'sourcetree.exe'),
    ('node', 'node nodejs', 'node.exe'),
    ('python', 'python python3', 'python.exe pythonw.exe'),
    ('conda', 'anaconda miniconda conda', 'anaconda-navigator.exe'),
    ('docker', 'docker dockerdesktop', 'docker desktop.exe docker.exe'),
    ('postman', 'postman', 'postman.exe'),
    ('apifox', 'apifox', 'apifox.exe'),
    ('navicat', 'navicat', 'navicat.exe'),
    ('dbeaver', 'dbeaver', 'dbeaver.exe'),
    ('xshell', 'xshell', 'xshell.exe'),
    ('xftp', 'xftp', 'xftp.exe'),
    ('mobaxterm', 'mobaxterm', 'mobaxterm.exe'),
    ('putty', 'putty', 'putty.exe'),
    ('winscp', 'winscp', 'winscp.exe'),
    ('fiddler', 'fiddler', 'fiddler.exe'),
    ('wireshark', 'wireshark', 'wireshark.exe'),
    ('burpsuite', 'burp burpsuite', 'burpsuite.exe burp.exe'),
    ('charles', 'charles', 'charles.exe'),
    ('ida', 'ida ida64', 'ida64.exe ida.exe'),
    ('ollydbg', 'ollydbg od', 'ollydbg.exe'),
    ('x64dbg', 'x64dbg x32dbg', 'x64dbg.exe x32dbg.exe'),
    ('cheatengine', 'cheatengine ce', 'cheatengine-x86_64.exe cheatengine.exe'),
    ('vmware', 'vmware', 'vmware.exe vmware-vmx.exe'),
    ('virtualbox', 'virtualbox vbox', 'virtualbox.exe'),
    ('mumu模拟器', 'mumu', 'mumusimulator.exe mumuplayer.exe'),
    ('雷电模拟器', 'ldplayer ld', 'dnplayer.exe ldplayer.exe'),
    # —— 影音 ——
    ('网易云音乐', 'cloudmusic neteasecloudmusic', 'cloudmusic.exe'),
    ('qq音乐', 'qqmusic', 'qqmusic.exe'),
    ('酷狗', 'kugou', 'kugou.exe'),
    ('酷我', 'kuwo', 'kuwo.exe'),
    ('spotify', 'spotify', 'spotify.exe'),
    ('potplayer', 'potplayer', 'potplayermini64.exe potplayer.exe'),
    ('vlc', 'vlc', 'vlc.exe'),
    ('mpv', 'mpv', 'mpv.exe'),
    ('bilibili', 'bilibili bili', 'bilibili.exe'),
    ('抖音', 'douyin tiktok', 'douyin.exe'),
    ('快手', 'kuaishou', 'kuaishou.exe'),
    ('爱奇艺', 'iqiyi', 'iqiyi.exe'),
    ('腾讯视频', 'tencentvideo qqlive', 'qqlive.exe'),
    ('优酷', 'youku', 'youku.exe'),
    ('剪映', 'jianying capcut', 'jianyingpro.exe capcut.exe'),
    ('obs', 'obs obsstudio', 'obs64.exe obs.exe'),
    ('pr', 'premiere adobepremiere', 'adobe premiere pro.exe'),
    ('ae', 'aftereffects', 'afterfx.exe'),
    ('ps', 'photoshop adobephotoshop', 'photoshop.exe'),
    ('ai', 'illustrator adobeillustrator', 'illustrator.exe'),
    ('figma', 'figma', 'figma.exe'),
    ('audacity', 'audacity', 'audacity.exe'),
    ('foobar2000', 'foobar', 'foobar2000.exe'),
    # —— 图像 / 截图 ——
    ('snipaste', 'snipaste', 'snipaste.exe'),
    ('bandizip', 'bandizip', 'bandizip.exe'),
    ('7zip', '7z 7zip', '7zfm.exe 7zg.exe'),
    ('winrar', 'winrar rar', 'winrar.exe'),
    ('everything', 'everything', 'everything.exe'),
    ('listary', 'listary', 'listary.exe'),
    ('utools', 'utools', 'utools.exe'),
    ('powertoys', 'powertoys', 'powertoys.exe'),
    ('honeyview', 'honeyview', 'honeyview.exe'),
    ('irfanview', 'irfanview', 'i_view64.exe'),
    ('gimp', 'gimp', 'gimp.exe'),
    ('blender', 'blender', 'blender.exe'),
    # —— 网盘 / 传输 ——
    ('百度网盘', 'baidunetdisk', 'baidunetdisk.exe'),
    ('阿里云盘', 'alidrive alipan', 'alidrive.exe aDrive.exe'),
    ('夸克网盘', 'quarkdrive', 'quarkdrive.exe'),
    ('迅雷', 'thunder xunlei', 'thunder.exe xldl.exe'),
    ('utorrent', 'utorrent', 'utorrent.exe'),
    ('qbitTorrent', 'qbittorrent', 'qbittorrent.exe'),
    ('向日葵', 'sunlogin oray', 'sunloginclient.exe'),
    ('todesk', 'todesk', 'todesk.exe'),
    ('anydesk', 'anydesk', 'anydesk.exe'),
    ('teamviewer', 'teamviewer', 'teamviewer.exe'),
    ('rustdesk', 'rustdesk', 'rustdesk.exe'),
    ('filezilla', 'filezilla', 'filezilla.exe'),
    ('dropbox', 'dropbox', 'dropbox.exe'),
    ('onedrive', 'onedrive', 'onedrive.exe'),
    # —— 系统工具 / 安全 ——
    ('火绒', 'huorong', 'hipstray.exe huorong.exe'),
    ('360安全卫士', '360safe 360', '360safe.exe 360tray.exe'),
    ('腾讯电脑管家', 'qqpcmgr pcmgr', 'qqpcmgr.exe'),
    ('鲁大师', 'ludashi', 'ludashi.exe'),
    ('geek uninstaller', 'geek geekuninstaller', 'geek.exe'),
    ('cpu-z', 'cpuz', 'cpuz.exe'),
    ('gpu-z', 'gpuz', 'gpuz.exe'),
    ('hwinfo', 'hwinfo', 'hwinfo64.exe'),
    ('aida64', 'aida64', 'aida64.exe'),
    ('crystaldiskinfo', 'crystaldiskinfo', 'crystaldiskinfo.exe'),
    ('memtest', 'memtest', 'memtest.exe'),
    ('diskgenius', 'diskgenius', 'diskgenius.exe'),
    ('rufus', 'rufus', 'rufus.exe'),
    ('balenaetcher', 'etcher balena', 'balenaetcher.exe'),
    ('ventoy', 'ventoy', 'ventoy2disk.exe'),
    ('wiztree', 'wiztree', 'wiztree.exe'),
    ('treesize', 'treesize', 'treesize.exe'),
    ('spaceSniffer', 'spacesniffer', 'spacesniffer.exe'),
    # —— 游戏 / 平台 ——
    ('steam', 'steam', 'steam.exe'),
    ('epic', 'epic epicgames', 'epicgameslauncher.exe'),
    ('wegame', 'wegame', 'wegame.exe'),
    ('battle.net', 'battle battlenet', 'battle.net.exe'),
    ('uplay', 'uplay ubisoft', 'ubisoftconnect.exe'),
    ('gog', 'gog galaxy', 'galaxyclient.exe'),
    ('minecraft', 'minecraft', 'minecraft.exe'),
    ('原神', 'genshin', 'yuanshen.exe genshinimpact.exe'),
    ('崩坏星穹铁道', 'starrail honkaistarrail', 'starrail.exe'),
    ('英雄联盟', 'lol leagueoflegends', 'leagueclient.exe'),
    ('csgo', 'csgo cs2', 'cs2.exe csgo.exe'),
    ('穿越火线', 'crossfire cf', 'crossfire.exe'),
    ('dnf', 'dnf dungeonfighter', 'dnf.exe'),
    ('wemod', 'wemod', 'wemod.exe'),
    # —— 通讯录 / 其它常用 ——
    ('支付宝', 'alipay', 'alipay.exe'),
    ('淘宝', 'taobao', 'taobao.exe'),
    ('京东', 'jd jingdong', 'jd.exe'),
    ('拼多多', 'pinduoduo pdd', 'pinduoduo.exe'),
    ('美团', 'meituan', 'meituan.exe'),
    ('高德地图', 'amap gaode', 'amap.exe'),
    ('百度地图', 'baidumap', 'baidumap.exe'),
    ('滴滴', 'didi', 'didi.exe'),
    ('小红书', 'xiaohongshu rednote', 'xiaohongshu.exe'),
    ('微博', 'weibo', 'weibo.exe'),
    ('知乎', 'zhihu', 'zhihu.exe'),
    ('豆瓣', 'douban', 'douban.exe'),
    ('微信读书', 'weread', 'weread.exe'),
    ('kindle', 'kindle', 'kindle.exe'),
    ('calibre', 'calibre', 'calibre.exe'),
    ('anki', 'anki', 'anki.exe'),
    ('zoomlauncher', 'zoomlauncher', 'zoomlauncher.exe'),
]


def _norm(s):
    """全角转半角 + 去空格 + 小写，避免「ＷｅＣｈａｔ」这种漏网。"""
    out = []
    for ch in s.strip():
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        elif code == 0x3000:
            ch = ' '
        out.append(ch)
    return ''.join(out).replace(' ', '').lower()


class App(object):
    __slots__ = ('name', 'aliases', 'exes', 'dirs')

    def __init__(self, name, aliases, exes):
        self.name = name
        # 中文名本身也算别名：搜「微信」当然要命中「微信」这一条
        raw = [name] + aliases.split()
        self.aliases = tuple(a for a in (_norm(x) for x in raw) if a)
        self.exes = set(e for e in (_norm(x) for x in exes.split()) if e)
        # 安装目录通常就是 exe 名去掉后缀（WeChat\WeChat.exe）
        self.dirs = set(e[:-4] for e in self.exes if e.endswith('.exe'))


APPS = [App(n, a, e) for n, a, e in _RAW]

# exe 名 → 该应用（一个 exe 只归一个应用；重复的以先出现的为准）
_BY_EXE = {}
for _app in APPS:
    for _e in _app.exes:
        _BY_EXE.setdefault(_e, _app)


def match(q):
    """查询词命中的「常用应用」列表，按匹配强度排序（完全相等优先）。"""
    nq = _norm(q or '')
    if len(nq) < 1:
        return []
    hit = []
    for app in APPS:
        primary = app.aliases[0]          # 中文名/主名
        if primary == nq:
            hit.append((0, -len(primary), app))
            continue
        if len(nq) >= 2 and primary.startswith(nq):
            hit.append((1, -len(primary), app))
            continue
        for a in app.aliases[1:]:
            if a == nq:
                hit.append((2, -len(a), app))
                break
            if len(nq) >= 2 and a.startswith(nq):
                hit.append((3, -len(a), app))
                break
    # 主名完全相等 > 主名前缀 > 别名相等 > 别名前缀；同档里名字更长的更具体
    hit.sort(key=lambda t: (t[0], t[1]))
    # 返回 [(app, 档位)]：0=主名完全相等 1=主名前缀 2=别名相等 3=别名前缀
    return [(t[2], t[0]) for t in hit]


def boost(q, name, path):
    """该结果对这次查询的「常用应用」加权（0 表示不加权）。

    name 是文件名（含扩展名），path 是全路径。返回 0~1 的加成：
      命中 exe 名        +0.55
      命中安装目录       +0.20
    其余情况（例如「微信」命中了「微信聊天记录.txt」）不加权 —— 它凭什么排在微信前面。
    """
    apps = match(q)
    if not apps:
        return 0.0
    # 档位越靠前越强：搜 qq 时 QQ 本体（档 0）要压过 QQ 音乐（档 3）
    _TIER = {0: 1.00, 1: 0.92, 2: 0.86, 3: 0.70}
    n = _norm(name)
    stem = n
    for _ext in ('.exe', '.lnk', '.url', '.appref-ms'):
        if n.endswith(_ext):
            stem = n[:-len(_ext)]
            break
    np = _norm(path or '').replace('\\', '/')
    best = 0.0
    for app, tier in apps:
        strong = _TIER.get(tier, 0.70)
        if n in app.exes:
            return strong
        # 桌面上的「微信.lnk」就是微信本人，也得排前面
        if n.endswith(('.lnk', '.url', '.appref-ms')):
            if stem in app.aliases:
                return strong - 0.08
            # 「WPS Office.lnk」这种带后缀的快捷方式：前缀命中也算，
            # 但「QQ音乐.lnk」不能被算成 QQ —— 后面接中文说明是另一个应用
            for a in app.aliases:
                if len(a) < 2 or not stem.startswith(a):
                    continue
                rest = stem[len(a):]
                if any('一' <= ch <= '鿿' for ch in rest):
                    continue
                return strong - 0.12
        if stem and stem in app.dirs and '/' in np:
            best = max(best, 0.20)
    return best


def name_for_exe(filename):
    """「Weixin.exe」→「微信」。只在白名单里查，查不到返回 None。

    用途：列表里显示中文名、并且**不显示路径**（路径是开发者视角，
    普通用户看到 C:/Program Files/Tencent/Weixin 只会犹豫一下）。
    """
    n = _norm(filename or '')
    app = _BY_EXE.get(n)
    return app.name if app else None


def is_app_file(filename):
    """这个文件名是不是「程序类」文件（exe/lnk/bat…）。"""
    n = _norm(filename or '')
    if n.endswith(('.exe', '.lnk', '.bat', '.cmd', '.msi', '.appref-ms', '.url', '.ps1')):
        return True
    return n in _BY_EXE
