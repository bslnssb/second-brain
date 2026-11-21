#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Memex · 全盘文档扫描器（重扫清单用，只记录路径/大小，不读内容）
排除规则与建库时一致：小说零接触、系统/游戏/缓存目录跳过。"""
import csv
import os
import sys
import time
from pathlib import Path

NOVEL_WORDS = ('小说', '书屋', '书城', '书架', 'novel', 'fiction', '轻小说', '网文')
SKIP_DIRS = {'windows', 'program files', 'program files (x86)', 'appdata', '$recycle.bin',
             'node_modules', '.git', '__pycache__', 'site-packages', 'recovery',
             'system volume information', 'pylibs', '.vs', 'packages',
             # Windows「跨设备」虚拟文件夹（手机经「连接至 Windows」映射进来的
             # C:\Users\<user>\CrossDevice\<手机名>）：枚举它会尝试连接手机，手机不在
             # 就弹「连接至 Windows」对话框（第 122 项），且内容随手机在线状态变化，
             # 索引它没有意义。
             'crossdevice'}

# ===== 第 236 项：内置默认排除（不再靠用户手工一条条配）=====
# 依据（跨平台共识，不是拍脑袋）：
#   · 微软 Windows Search 官方默认索引排除表：AppData、AppData\Local\Temp、
#     ProgramData、Program Files、Windows、$Recycle.Bin、*\Temp\*
#   · Everything 官方建议排除 c:\users*\appdata
#   · Recoll（GitHub）默认 skippedNames：Cache/cache*/tmp/.git/.svn/.hg
#   · KDE Baloo 默认排除：node_modules/__pycache__/.venv/.git/*.pyc
#   · Spotlight 社区脚本（sdolard/setup_spotlight_exclusions）：node_modules/dist/
#     build/.next/.turbo/coverage/target/.idea/.cache
# 只收「高置信度」的名字：像 cache / temp / build / dist / target 这种用户也可能
# 拿来存资料的通用名**故意不收**，宁可漏杀不可错杀。
DEFAULT_SKIP_NAMES = {
    # 游戏平台 / 引擎缓存
    'steamapps', 'appcache', 'depotcache', 'shadercache', 'blob_storage',
    # 浏览器 / Electron 缓存（名字本身即可判定）
    'gpucache', 'code cache', 'crashpad', 'dawncache',
    'inetcache', 'service worker',
    # 前端资源包 / 界面文案（飞书、豆包、剪映这些客户端里全是这类目录）
    'webcontent', 'local_webcontents', 'webcontents', 'locales', 'licenses',
    # 打包产物（PyInstaller 的运行时目录）
    '_internal',
    # 依赖 / 构建产物（Baloo + Spotlight 共识）
    'node_modules', 'bower_components', '.pnpm-store', '.yarn-cache', '.npm',
    '__pycache__', 'site-packages', '.mypy_cache', '.pytest_cache', '.tox',
    '.venv', 'venv', '.next', '.nuxt', '.turbo', '.parcel-cache',
    '.svelte-kit', '.gradle', '.idea', '.vs', '.vscode',
    # VCS 元数据
    '.git', '.svn', '.hg', '.bzr',
}

# 绝对路径级排除：整棵树都不该进索引。用 normcase 子串匹配，所以写法带反斜杠，
# 保证只命中目录段（`\\appdata\\` 不会误伤 `D:\\我的appdata备份`）。
# 「开始菜单」单独白名单：应用搜索全靠它，见 _excluded() 的 KEEP 判断。
DEFAULT_SKIP_SEGS = (
    '\\appdata\\',          # 微软官方默认排除（含 Local/Roaming/LocalLow 全部）
    '\\programdata\\',
    '\\$recycle.bin\\',
    '\\$windows.~',
    '\\recovery\\',
    '\\system volume information\\',
    '\\windows\\winsxs\\',
    '\\windows\\servicing\\',
    '\\windows\\installer\\',
    '\\windows\\assembly\\',
    # 游戏平台与加速器：装完就是一整棵资源树，名字全无检索价值。
    # 注意不要写成 `\steam\` —— 那会把 `C:\Program Files\Steam` 这种「用户显式
    # 加进范围、且要作为独立扫描根」的路径一起否掉（tests/test_scan_docs.py 钉着）。
    '\\steamapps\\', '\\steamlibrary\\', '\\leigod_acc\\',
    '\\steam\\userdata\\', '\\steam\\controller_base\\', '\\steam\\depotcache\\',
)

# 白名单：命中这些子串就一律保留，即使上面把整棵树排掉了。
# %APPDATA%\\Microsoft\\Windows\\Start Menu 与 %PROGRAMDATA%\\...\\Start Menu 都在
# 上面被排掉的 AppData / ProgramData 里，但它们是「搜应用名出应用」的唯一数据源。
DEFAULT_KEEP_SEGS = (
    '\\start menu\\programs',
)

# 第 236 项：只挡「正文」、不挡「文件名」的路径。
# 参照 Recoll 的 noContentSuffixes 与 KDE Baloo 的 only-basic-indexing：
# 程序自带的 readme / license / 界面文案仍能被文件名搜到（不丢功能），
# 但不进正文池 —— 否则搜一个词，结果里一半是插件文档和前端 chunk。
# 实测：库里 12,754 条正文有 3,378 条（26%）躺在这些目录里。
BODY_SKIP_SEGS = (
    '\\webcontent\\', '\\local_webcontents\\', '\\webcontents\\',
    '\\locales\\', '\\licenses\\', '\\swiftshader\\', '\\app.asar',
    '\\mingw64\\', '\\git\\usr\\', '\\resources\\app\\',
)

# 安装根：这些目录下只保留「能启动的东西」，不收 png/js/dll 这类资源。
# 整盘扫描本来就故意不跳过 Program Files（否则搜不到 chrome.exe / Weixin.exe），
# 但把它下面的 3000 张 png、2000 个 js 也收进来就是纯噪声了。
INSTALL_ROOT_SEGS = (
    '\\program files\\', '\\program files (x86)\\',
    '\\appdata\\local\\programs\\', '\\windows\\system32\\', '\\windows\\syswow64\\',
)
APP_ONLY_EXTS = {'.exe', '.lnk', '.msi', '.appref-ms', '.url', '.bat', '.cmd'}

# ---------------------------------------------------------------------------
# 第 240 项：软件安装目录「内容嗅探」
# ---------------------------------------------------------------------------
# 为什么还要它：`INSTALL_ROOT_SEGS` 是按**固定路径段**匹配的（Program Files 等），
# 可用户把软件装在 D:\ 下面怎么办？钉钉 D:\DingDing\main\current 里有 2,176 张 png、
# 腾讯会议 D:\tenxunhuiyi\WeMeet 里有 2,635 张 png —— 全是安装包自带素材，
# 用户永远搜不到也不需要，却实实在在占着索引（钉钉 3,947 条、WeMeet 2,842 条）。
#
# 为什么不按「exe + resources/app.asar」这种固定特征：实测那只抓到 299 条
# （钉钉/腾讯会议/PotPlayer 都不是 Electron 布局），覆盖面太窄。
#
#
# 第 242 项：**整目录嗅探这条路走不通，判据整个换掉。**
#   用户实测反馈：① 2GB 上限不该有（压缩包/系统镜像正是要按名字找的）；
#   ② 该排的是几 KB 的应用图标；③ D:\phpstudy_pro\WWW 是渗透靶场，绝不能排。
#
#   我拿真实数据把「按子树文件构成比例猜软件目录」这条路验证到底，结论是**不可行**：
#     · 「程序物占比」判据：钉钉 current 69.0%、腾讯会议 66.9%、靶场 66.5%
#       —— 区分度不到 3 个百分点，课件包 95.7% 还会被误判成软件目录。
#     · 「源码占比」判据：格式工厂 40.7%（软件自带 .py）、PotPlayer 20%（.pyd 绑定）
#       被当成用户源码而放过；反过来靶场 32% 又让它显得像用户项目。
#     · 「项目标志文件」判据（社区通用的找项目根目录法）：靶场里的靶机是
#       **散装拷贝的**，没有 package.json 也没有 .git —— 靠标志文件反而会误伤。
#   文件层面根本区分不了「用户存的 .mp3」和「软件自带的 .mp3」。
#
#   所以改成 Everything 的模型：**不猜，只排明确的**（voidtools 官方文档：
#   "Everything will index every file and folder, you can then exclude files
#   from the index" —— 它靠的是排除清单，不是自动判断）。
#
# 最终规则（SOFTDIR_ASSET_SEGS）：只排**明确的程序资产子目录**，且要求
#   **它的父目录里有 exe**（= 那是一个安装目录，不是用户的项目）。
#   实测命中 901 条，靶场 / 课件 / 下载 / 桌面 / 文档 **全部 0 误伤**。
SOFTWARE_DIR_SNIFF = True
SOFTDIR_SCAN_LIMIT = 1500        # 采样上限：只看前 N 个文件，别为判断把整棵树走完
SOFTDIR_MAX_DEPTH = 3
SOFTDIR_DOC_RATIO = 0.03
SOFTDIR_SRC_RATIO = 0.01         # 源码占比超过 1% 就当作用户自己的项目，不排
SOFTDIR_MIN_FILES = 30

# 程序资产子目录名：这些名字在**安装目录**里是程序自带的素材/运行时，
# 在用户的项目目录里几乎不会出现。命中还要附加「父目录里有 exe」的条件。
# 逐个的实测依据：
#   resources  → 剪映 Resources（1415 条 json/js/md/png）、有道云笔记 build、PikPak
#   runtime    → GPT-SoVITS 的 Python venv（101 个 exe + 100 个 .h）
#   locales    → 软件的多语言包
#   webcontent → Chromium/Electron 的网页资源
# ⚠️ 不用 'www' / 'lib' / 'bin' / 'assets' —— 这些名字用户的项目里也常见，
#    实测 'www' 会误伤 D:\phpstudy_pro\WWW（用户的靶场）。
SOFTDIR_ASSET_SEGS = {
    'resources', 'locales', 'runtime', 'webcontent', 'uiresources',
    'ffmodules',
}

# 项目标志文件（目录里有这些 → 是用户/开发者自己的东西，绝不当软件目录排掉）。
# 判据来自社区通行的项目识别法：找项目根目录时看这一组文件
# （.git / package.json / pyproject.toml / requirements.txt / go.mod / composer.json…）。
# 比「按扩展名猜」准得多，也不怕软件自带的 .py 混进来。
#
# ⚠️ 只认**强标志**（package.json / requirements.txt / composer.json 这类
# 「几乎只有项目才会有」的文件）。readme.md / license / makefile / index.js
# 这些太泛 —— 软件目录里随便一个就命中，判据会整个失效（实测钉钉、腾讯会议
# 全变成「保护」）。弱标志（readme/license）要**成对出现**才算，见下。
SOFTDIR_PROJECT_MARKERS = {
    '.git',                       # 目录本身是仓库
    'package.json', 'pyproject.toml', 'requirements.txt', 'setup.py',
    'composer.json', 'go.mod', 'cargo.toml', 'pom.xml', 'build.gradle',
    'gemfile', 'pipfile', 'cmakelists.txt', 'dockerfile',
    'docker-compose.yml', 'environment.yml',
}

# 弱标志：单独一个不算（软件目录里也有 readme），但**同一个目录里成对出现**
# 基本只有项目才会这样 —— 软件安装目录通常只有一个 readme。
SOFTDIR_PROJECT_MARKERS_WEAK = {
    'readme.md', 'readme.txt', 'readme', 'license', 'license.md',
    'changelog.md', 'contributing.md', 'authors', 'notice',
}

# 靶场/靶机目录名：这些名字一出就**无条件保护**，不管里面长什么样。
# 用户明说 D:\phpstudy_pro\WWW 是渗透测试靶场（DVWA / sqli-labs / pikachu…），
# 里面是散装拷贝的 PHP 靶机，没有 package.json 也没有 .git ——
# 靠标志文件会误伤，所以按目录名直接保。
SOFTDIR_ALWAYS_KEEP_SEGS = {
    'www', 'wwwroot', 'htdocs', 'webroot',
    'dvwa', 'sqli-labs', 'pikachu', 'sqlmap', 'burpsuite',
    '靶场', '靶机', 'vulnhub', 'vulnerable', 'pentest',
}

# ---------------------------------------------------------------------------
# 第 243 项：**短音频 + 音乐平台目录 + 已知软件目录清单**
# ---------------------------------------------------------------------------
# 用户提的三条诉求，原话大意：
#   ①「用户存的 .mp3」和「软件自带的 .mp3」分不出来 —— 那就跳过 20 秒以内的
#      mp3，歌曲通常都是一两分钟；
#   ② 也可以看是不是网易云 / QQ 音乐 / 酷狗音乐这些音乐平台下的目录；
#   ③「是不是应该内置一个世界常用软件 top500 的目录来判断这个软件的目录
#      该不该被索引」。
#
# 三条都落地，但第 ③ 条的**用法**要摆正（见 SOFTWARE_DIR_NAMES 的注释）：
#   名单不是「黑名单式地把这些目录整棵删掉」，而是「**认出这是软件目录**，
#   于是这棵子树只保留应用入口（exe/lnk/msi…），素材不入索引」。
#   为什么不能整棵删：微信/QQ 聊天文件、网盘同步目录、Office 模板、Steam 截图
#   全都躺在软件名目录下，整棵删会把用户真正要搜的东西删掉。
#   为什么不能只看「目录名像不像软件」：D:\Tencent 下可能是用户的资料，
#   D:\phpstudy_pro\WWW 是渗透靶场。所以名单分两档：
#     · 具体软件名（chrome / pycharm / vlc …）→ 直接认定；
#     · 厂商/容器名（tencent / adobe / google …）→ 还要该目录里确实有 exe。
#   并且所有「用户数据目录名」优先豁免（见 USER_DATA_DIR_NAMES）。
#
# ① 短音频：见 brain.NAMES_MIN_AUDIO_SECONDS 与 audio_probe.py。
# ② 音乐平台：目录名命中 MUSIC_APP_DIR_SEGS → 该目录下的**音频文件**不入索引
#    （只排音频，不排整棵目录 —— 用户在音乐平台目录里也可能存着自己的歌单）。

# 音乐平台目录名（小写整段匹配；中文保持原样）。
# 只排这些目录下的音频文件，不排目录本身 —— 用户自己下载的歌单还要留着。
MUSIC_APP_DIR_SEGS = {
    # 国内
    'cloudmusic', '网易云音乐', 'netease cloudmusic',
    'qqmusic', 'qq音乐',
    'kugou', '酷狗音乐', 'kugou music', 'kugou8',
    'kuwo', '酷我音乐',
    'migu', '咪咕音乐', 'qishui', '汽水音乐',
    'musicfree', 'listen1',
    # 国外
    'spotify', 'apple music', 'itunes', 'itunes library',
    'amazon music', 'youtube music', 'pandora', 'deezer', 'tidal',
    'foobar2000', 'winamp', 'aimp', 'foobar',
    # 常见的「曲库缓存」目录名
    'musiccache', 'songcache', 'audiocache', '音乐缓存', '歌曲缓存',
}

# 已知软件目录名 —— **具体软件名**，命中即认定该目录是软件目录。
# 收录标准：目录名足够具体、不可能同时是用户的资料目录名。
# 命名一律小写（normcase 之后比较），中文保持原样。
SOFTWARE_DIR_NAMES = {
    # ---- 浏览器 ----
    'chrome', 'chromium', 'firefox', 'msedge', 'edge', 'microsoft edge',
    'opera', 'opera gx',
    'brave', 'vivaldi', 'yandexbrowser', 'waterfox', 'seamonkey', 'comodo dragon',
    'iridium', 'centbrowser', 'epic privacy', 'ucbrowser', '360se', '360se6',
    '360chrome', '360browser', 'maxthon', 'theworld', 'liebao', 'sogouexplorer',
    'sogou browser', 'qqbrowser', '115browser', 'flash center', 'cent browser',
    'browser', 'chromium-bin',
    # ---- 编辑器 / IDE ----
    'vscode', 'visual studio code', 'vs code insiders', 'sublime text',
    'sublime', 'notepad++', 'notepadplusplus', 'necoedit', 'hbuilder',
    'hbuilderx', 'dcloud', 'pycharm', 'pycharm64', 'pycharm community edition',
    'intellij idea', 'intellij', 'intellijidea', 'idea', 'idea64',
    'jetbrains', 'jetbrains toolbox',
    'webstorm', 'goland', 'clion', 'datagrip', 'rider', 'rubymine',
    'phpstorm',     'android studio', 'androidstudio', 'eclipse', 'netbeans', 'visual studio',
    'microsoft visual studio', 'codeblocks', 'qtcreator', 'qt creator',
    'kdevelop', 'geany', 'dev-c++', 'devcpp', 'zed', 'lapce', 'trae',
    'windsurf', 'cursor', 'zed editor', 'fleet', 'devenv', 'android-ndk',
    # ---- 语言运行时 / 开发环境 ----
    'node.js', 'nodejs', 'deno', 'bun', 'golang', 'rustup', 'rust',
    'anaconda3', 'miniconda3', 'conda', 'pyenv', 'virtualenv',
    'python27', 'python36', 'python37', 'python38', 'python39', 'python310',
    'python311', 'python312', 'python313',
    'jdk', 'jre', 'openjdk', 'adoptium', 'temurin', 'zulu', 'corretto',
    'dotnet', 'netcore', 'visual c++ redistributable', 'vcredist',
    'vc_redist', 'directx', 'dxwebsetup', 'windows sdk', 'winsxs',
    'phpstudy', 'xampp', 'wamp', 'laragon', 'iis express',
    'cygwin', 'msys64', 'mingw64', 'mingw32', 'msys',
    # ---- 数据库 / 消息队列 / 基础设施 ----
    'mysql', 'mysql8', 'postgresql', 'postgres', 'mariadb', 'mongodb',
    'redis', 'sqlserver', 'microsoft sql server', 'oracle', 'sqlite',
    'elasticsearch', 'kibana', 'rabbitmq', 'kafka', 'zookeeper', 'hadoop',
    'spark', 'flink', 'presto', 'hive', 'neo4j', 'etcd', 'consul',
    'navicat', 'premiumsoft', 'dbeaver', 'pgadmin', 'redisdesktop',
    'anotherredis', 'mysql workbench', 'heidisql', 'tosql',
    # ---- 容器 / 虚拟 / 远程 ----
    'docker desktop', 'podman', 'kubernetes', 'minikube',
    'vmware', 'vmware vmx', 'virtualbox', 'oracle virtualbox', 'hyper-v',
    'parallels', 'bochs', 'qemu', 'wsl', 'vagrant', 'utm',
    'putty', 'winscp', 'filezilla', 'xshell', 'mobaxterm', 'securecrt',
    # ---- 版本控制 / 抓包 / 接口 ----
    'git', 'git bash', 'tortoisegit', 'sourcetree', 'github desktop',
    'gitkraken', 'postman', 'insomnia', 'apifox', 'swagger', 'charles',
    'fiddler', 'wireshark', 'metasploit', 'nmap', 'nessus', 'openvas',
    'burp suite', 'burpsuite pro',
    # ---- 办公 / 笔记 / 协作 ----
    'microsoft office', 'microsoft office 16', 'microsoft office 15',
    'microsoft office 14', 'office16', 'office15', 'office14',
    'wps office', 'wps', 'kingsoft office', 'kingsoft wps',
    'xmind', 'mindmaster', 'processon', '石墨', 'graphite docs',
    'evernote', '印象笔记', 'onenote', 'outlook', 'mailbird', 'thebat',
    'foxmail', '网易邮箱大师', 'mailmaster', 'becky', 'sogoumail',
    'notion', 'obsidian', 'logseq', 'roam research', 'wolai', 'flomo',
    '滴答清单', 'ticktick', '有道云笔记', 'youdaonote', '有道词典',
    'youdaodict', '必应词典', 'bingdict', 'eudic', '欧路词典', 'shanbay',
    '墨墨背单词', 'ankidroid', 'anki',
    # ---- 影音播放 / 剪辑 ----
    'vlc', 'vlc media player', 'potplayer', 'potplayer64', 'kmplayer',
    'mpv', 'mpc-hc', 'mpc-be', 'aplayer', 'realplay', 'iina', 'smplayer',
    'gom player', '暴风影音', 'storm video', 'nplayer', 'jfinal',
    'audacity', 'ocenaudio', 'fl studio', 'ableton', 'logic', 'cubase',
    'reaper', 'bandlab', 'garageband', 'obs', 'obs studio', 'nvidia broadcast',
    'camtasia', 'filmora', 'filmora9', '万兴喵影', '万兴', 'wondershare',
    'adobe premiere pro', 'premiere pro', 'after effects', 'davinci resolve',
    'davinci', 'nuke', 'sony vegas', 'vegas', 'kdenlive', 'shotcut',
    'openshot', 'handbrake', 'ffmpeg', 'kodi', 'plex', 'potplayer64.exe',
    '爱剪辑', '快剪辑', 'wonderfilm', 'videowave',
    # ---- 图像 / 设计 / 音频制作 ----
    'adobe', 'adobe photoshop', 'photoshop', 'illustrator', 'indesign',
    'lightroom', 'audition', 'acrobat', 'adobe acrobat', 'acrobat reader',
    'autodesk', '3ds max', 'maya', 'autocad', 'sketchup', 'fusion 360',
    'solidworks', 'inventor', 'zbrush', 'cinema 4d', 'c4d', 'blender',
    'gimp', 'krita', 'paint tool sai', 'clip studio paint', 'coreldraw',
    'ulead',     'affinity designer', 'affinity photo', 'sketch',
    'zeplin', 'axure rp', '墨刀设计', '摹客', '蓝湖', 'teamlab',
    'izotope', 'native instruments', 'arturia', 'ujam', 'wavetable',
    'soundforge', 'fl studio 21', 'magix', 'avid',
    # ---- 播放器 / 通讯 / 会议 ----
    'discord', 'telegram', 'signal', 'whatsapp', 'zoom', 'microsoft teams',
    'teams', 'slack', 'skype', 'line', 'viber', 'zalo', 'wickr',
    '钉钉', '阿里钉钉', 'dingding', 'dingtalk', 'dingtalklauncher',
    '飞书', 'feishu', 'lark', '企业微信', 'wecom',
    'wxwork', 'wechat', 'weixin', '微信', 'qq', 'tim', 'tencentqq',
    '腾讯会议', 'wemeet', 'tencentmeeting', 'voovmeeting', 'voov meeting',
    '网易cc', 'cc talk',
    # ---- 剪辑 / 录屏 / 下载工具（用户机器上确实有这几类）----
    'jianying', 'jianyingpro', 'capcut', '必剪', 'format factory',
    'formatfactory', 'internet download manager', 'idm', 'thunder9',
    'steam++', 'watt toolkit', 'snipaste', 'picgo', 'typora', 'yuque',
    '语雀', 'mastergo', 'pixso', 'jsdesign', '即时设计', 'x64dbg', 'x32dbg',
    'ida pro', 'ida', 'ghidra', '2345browser', '2345浏览器',
    '腾讯视频', 'qqvideo', '爱奇艺', 'iqiyi', '优酷', 'youku',
    '哔哩哔哩', 'bilibili',
    # ---- 游戏平台 / 加速器 ----
    'steam', 'steamvr', 'epic games', 'epicgameslauncher', 'origin',
    'ubisoft', 'uplay', 'battle.net', 'battlenet', 'riot client',
    'riotgames', 'wegame', 'wemix', 'mihoyo launcher', 'hoyolab',
    '完美世界', 'steamclient', 'gamemanagement', 'leigod', '迅游', 'xunyou',
    'uu加速器', '网易uu', 'game loop', 'nvidia geforce experience',
    'amd radeon software', 'mumu', 'mumuplayer', '雷电模拟器', 'ldplayer',
    'ld9box', '夜神模拟器', 'nox', 'bluestacks', 'taptap', '豌豆荚',
    'wandoujia', '手游', '原神', 'genshin', '绝区零', 'zenless zone zero',
    '明日方舟', 'arknights', 'mihoyo', 'hoyoverse', '网易大神',
    # ---- 安全 / 系统工具 / 硬件 ----
    'kaspersky', 'kaspersky lab', 'avast', 'avg', 'avira', 'bitdefender',
    'norton', 'mcafee', 'symantec', 'malwarebytes', 'eset',
    '360安全卫士', '360安全浏览器', 'huorong', '火绒', '腾讯电脑管家',
    '百度安全卫士', '2345安全卫士', '驱动人生', '驱动精灵',
    'driver genius', 'driver easy', 'ccleaner', '电脑管家', '联想电脑管家',
    'rufus', 'balenaetcher', 'hwinfo', 'speccy', 'cpuz', 'aida64',
    'hdtune', 'crystaldiskinfo', 'treesize', 'wiztree',
    'autohotkey', 'powertoys', 'sharex', 'greenshot', 'snagit', 'picpick',
    'lightshot', 'everything', 'screencapture',
    'nvidia', 'amd', 'intel', 'realtek', 'atheros', 'logitech', 'razer',
    'steelseries', 'corsair', 'vcredist_x64', 'directx9', 'opengl',
    'gpu-z', 'hwinfo64', 'microsoft mouse', 'zotac', 'asus', 'acer',
    # ---- 压缩 / 下载 / 输入法 / 翻译 ----
    '7-zip', '7zip', 'winrar', 'bandizip', 'winzip', 'peazip', 'haozip',
    '好压', '360zip', '360rar', '2345', 'thunder', 'thunderx', 'thunderstore',
    '迅雷', 'xunlei', '万能五笔', 'sogou', 'sogouinput', '搜狗输入法',
    'qq拼音', 'qq pinyin', 'iflytek', '讯飞输入法', 'baiduime', 'msime',
    'rime', '中州韵', '小狼毫', 'fcitx', 'ibus', 'baidu translator',
    # ---- 翻译 / 词典 / 学习 ----
    'youdao', '有道', 'babylon', 'linguis', 'youdao dict',
}

# 厂商 / 容器级目录名：命中**还要**该目录里确实有 exe 才算软件目录。
# 为什么单列一档：这些名字是「厂牌名」，用户完全可能拿它放自己的资料
# （D:\Tencent 里放项目、D:\Adobe 里放素材），所以要加一道物理佐证。
SOFTWARE_VENDOR_DIRS = {
    'tencent', 'qq', 'baidu', 'netease', '163', 'microsoft', 'adobe',
    'google', 'jetbrains', 'kingsoft', '360', 'alibaba', 'bytedance',
    'sina', 'sohu', 'iflytek', 'kingsoft office', 'apple', 'sony',
    'seagate', 'western digital', 'sandisk', 'crucial', 'kingston',
    'asus', 'lenovo', 'huawei', 'xiaomi', 'oppo', 'vivo', 'meizu',
    'dsym', 'sangfor', 'aigo', 'aoc', 'philips', 'logitech g',
    # phpstudy：用户 D:\phpstudy_pro 是渗透靶场宿主（WWW 必须完整保留），
    # 但它同目录下的 Apache/MySQL/PHP 运行时是纯软件资产。
    # 放 vendor 档（要目录里有 exe 才认定）—— 命中后 WWW 子树仍被
    # USER_DATA_DIR_NAMES 里的 'www' 豁免，靶场一条不丢。
    'phpstudy_pro', 'phpstudy',
    # 这两个刻意只做「整段相等」：前缀匹配会命中 D:\\DockerData /
    # D:\\DockerBackup / D:\\figmafiles —— 那是用户的数据目录，不是软件本体。
    'docker', 'figma',
}

# 用户数据目录名（**优先豁免**）：命中任一段，整条路径都不按软件目录处理。
# 这是这套规则最重要的一道保险 —— 软件目录名单再全，也挡不住
# 「微信聊天文件 / 网盘同步目录 / 我的音乐」这些用户真正要搜的东西。
USER_DATA_DIR_NAMES = {
    # 系统「我的」目录
    'documents', 'desktop', 'downloads', 'pictures', 'videos', 'music',
    '我的文档', '我的音乐', '我的图片', '我的视频', '我的收藏',
    '文档', '桌面', '下载', '图片', '视频', '音乐', '收藏',
    'favorites', 'saved games', 'saved games',
    # 网盘 / 同步盘（**同步目录里全是用户文件**，绝不能当软件目录）
    'onedrive', 'dropbox', 'icloud drive', 'icloud', 'google drive',
    'googledrive', 'nutstore', '坚果云', 'baidusyncdisk', '百度同步盘',
    'mega', 'pcloud', 'seafile', 'owncloud', 'nextcloud', 'syncthing',
    'resilio sync', 'quark', '夸克', 'weiyun', '微云', 'aliyundrive',
    '阿里云盘', 'tianyiyunpan', '天翼云盘', '189cloud', 'baidunetdiskdownload',
    '百度网盘', '阿里云盘', '115', '115browser data', 'pan',
    # 聊天 / 协作软件的数据目录
    'wechat files', 'tencent files', 'wecom files', 'wxwork',
    '钉钉文件', '飞书文档', '企业微信文件',
    # 靶场 / 渗透工具（用户明说 D:\phpstudy_pro\WWW 是靶场，必须完整保留）
    'www', 'wwwroot', 'htdocs', 'webroot', 'dvwa', 'sqli-labs',
    'pikachu', 'sqlmap', 'burpsuite', '靶场', '靶机', 'vulnhub',
    'vulnerable', 'pentest', 'webshell', '一句话', '中国蚁剑', 'antsword',
    'behinder', '冰蝎', 'godzilla', 'cobalt strike', 'meterpreter',
    # 软件名单的前缀匹配会命中这些「用户内容目录」，显式再豁免一次：
    'pycharmprojects', 'jetbrainsprojects', 'webstormprojects',
    'golandprojects', 'jianyingpro drafts', 'obsidian vault',
}

# 用户内容目录的**后缀**豁免。
#
# 起因是真实磁盘验证（第 243 项）：前缀匹配把
#   D:\PycharmProjects        → 用户自己的 Python 项目
#   D:\JianyingPro Drafts     → 用户的剪映草稿（753 个文件，全是用户自己的工程）
#   D:\obsidian-vaults        → 用户的笔记库
# 认成了软件目录 —— 那会把用户真正的东西排掉，比多收几条软件素材严重得多。
# 规则：目录名以这些后缀结尾 → 一律按用户内容处理，不走软件目录规则。
# ⚠️ 只豁免「软件目录规则」，音乐平台规则不受影响（网易云目录里的音频照样排）。
USER_DATA_SUFFIXES = {
    'project', 'projects', 'draft', 'drafts', '草稿',
    'workspace', 'workspaces', 'vault', 'vaults',
    '素材', '素材库', '笔记', '备份',
}

# 用户内容目录的**包含**豁免（目录名里含这些词）。
#
# 同样是真实磁盘验证逼出来的：
#   E:\手机备份2026年3月16日\QQ / Telegram / Weixin → 手机备份里的聊天记录，
#       排掉等于用户自己的照片语音全搜不到；
#   X:\工具合集\Nessus                        → 用户收集的网安工具，不是软件本体；
#   E:\html工具和课件…\HBuilder_9.1.29          → 课件资料里附带的工具副本。
# 这类目录名一眼就是「用户收集的东西」，不该按软件目录处理。
USER_DATA_CONTAINS = {
    '备份', 'backup', '工具', '合集', '资料', '课件', '课程', '收藏', '下载',
}

# 音乐平台目录命中 → 其下音频不入索引。
_MUSIC_APP_DIRS_NC = frozenset(os.path.normcase(s) for s in MUSIC_APP_DIR_SEGS)
_SW_DIRS_NC = frozenset(os.path.normcase(s) for s in SOFTWARE_DIR_NAMES)
_SW_VENDOR_NC = frozenset(os.path.normcase(s) for s in SOFTWARE_VENDOR_DIRS)
_USER_DATA_NC = frozenset(os.path.normcase(s) for s in USER_DATA_DIR_NAMES)
_USER_DATA_SUFFIX_NC = frozenset(os.path.normcase(s) for s in USER_DATA_SUFFIXES)
_USER_DATA_CONTAINS_NC = tuple(sorted(os.path.normcase(s) for s in USER_DATA_CONTAINS))

# 名单项常常带版本号（pycharm64 / pycharm2023 / intellijidea2023 /
# vscode-insiders / unityhub / zulu17 …），整段相等会漏掉一大半。所以对
# **长度 ≥ 5** 的具体软件名再做一层前缀匹配：用前 5 个字符建索引，
# 命中候选才逐个 startswith —— 每个目录段只花一次字典查找，很便宜。
#
# 长度门槛是刻意加的：短词做前缀匹配会乱伤
#   'qq'(2)     → 'qqmusic' 会被当成软件目录
#   'obs'(3)    → 'obscura' 会被当成 OBS
#   'ida'(3)    → 'identity' 会被当成 IDA
# 厂商名单（tencent / adobe …）**不做**前缀匹配 —— 那些是厂牌名，
# 带版本号的情况本来就少，误伤代价却高。
_SW_PREFIX_INDEX = {}
for _nm in sorted(_SW_DIRS_NC):
    if len(_nm) >= 5:
        _SW_PREFIX_INDEX.setdefault(_nm[:5], []).append(_nm)

# 厂商目录的 exe 佐证缓存（目录 → 有没有 exe）
_sw_exe_cache = {}


def _path_segs(path):
    """把路径拆成小写段列表（normcase 会把 / 换成 \\ 并转小写）。"""
    try:
        lp = os.path.normcase(str(path))
    except Exception:
        return []
    if '/' in lp:
        lp = lp.replace('/', '\\')
    return [s for s in lp.split('\\') if s]


def _sw_root_has_exe(root):
    """这个目录里有没有 exe（厂商级名单的物理佐证）。带缓存。"""
    key = os.path.normcase(os.path.abspath(root))
    v = _sw_exe_cache.get(key)
    if v is not None:
        return v
    v = False
    try:
        with os.scandir(key) as it:
            for e in it:
                try:
                    if e.is_file() and e.name.lower().endswith('.exe'):
                        v = True
                        break
                except OSError:
                    continue
    except Exception:
        v = False
    _sw_exe_cache[key] = v
    return v


def is_media_app_path(path):
    """这个路径是不是在「音乐平台目录」下（该目录下的音频不入索引）。

    只认**整段相等**的目录名，避免 `MyCloudMusic` 这种撞名。
    """
    for s in _path_segs(path):
        if s in _MUSIC_APP_DIRS_NC:
            return True
    return False


def _is_user_data_seg(seg):
    """这个目录段是不是「用户内容」的标志（是 → 豁免软件目录规则）。

    三种命中：整段在名单里 / 以某个后缀结尾 / 名字里含某个词。
    """
    if seg in _USER_DATA_NC:
        return True
    for suf in _USER_DATA_SUFFIX_NC:
        if len(seg) > len(suf) and seg.endswith(suf):
            return True
    for w in _USER_DATA_CONTAINS_NC:
        if w in seg:
            return True
    return False


def is_user_data_path(path):
    """这个路径是不是落在「用户数据目录」里（是 → 豁免一切软件目录规则）。

    三种命中方式：
      ① 目录名整段命中 USER_DATA_DIR_NAMES（网盘同步、聊天文件、靶场…）；
      ② 目录名以 USER_DATA_SUFFIXES 里的后缀结尾（…Projects / …Drafts /
         …Vaults / 素材）；
      ③ 目录名含 USER_DATA_CONTAINS 里的词（备份 / 工具 / 资料 / 课件…）。
    """
    for s in _path_segs(path):
        if _is_user_data_seg(s):
            return True
    return False


def is_known_software_dir(path):
    """这个目录是不是「已知软件目录」（该目录只保留应用入口，素材不入索引）。

    判定顺序：
      ① 命中用户数据目录名/后缀/关键词 → 立刻放行（用户的资料优先于任何名单）；
      ② 命中具体软件名（chrome / pycharm / vlc …）→ 认定；
      ③ 命中厂商名（tencent / adobe …）→ 还要该目录里确实有 exe 才认定。
    """
    segs = _path_segs(path)
    if len(segs) < 2:
        return False
    for s in segs:
        if _is_user_data_seg(s):
            return False
    for i in range(len(segs) - 1, 0, -1):
        seg = segs[i]
        if seg in _SW_DIRS_NC:
            return not has_keep_marker(path)
        cands = _SW_PREFIX_INDEX.get(seg[:5])
        if cands:
            for nm in cands:
                if seg.startswith(nm):
                    return not has_keep_marker(path)
        if seg in _SW_VENDOR_NC:
            root = '\\'.join(segs[:i + 1])
            if _sw_root_has_exe(root):
                return not has_keep_marker(path)
    return False


# ---------------------------------------------------------------------------
# 第 243 项：标记文件（竞品同源设计）
# ---------------------------------------------------------------------------
# 调研竞品源码后的结论：**没有任何一个索引器靠"猜"来排除目录**，全都是
# 显式清单 + 通配（Recoll 的 skippedNames/noContentSuffixes/skippedPaths、
# Baloo 的 fileexcludefilters.cpp 两个默认数组、Tracker 的 ignored-directories、
# Everything 的 Exclude 列表）。竞品里"用户自己标记某个目录别索引"的通行
# 做法是**在目录里放一个标记文件**：
#     Recoll    → .recoll-noindex   （nowalkfn 参数）
#     Tracker   → .trackerignore / .nomedia
#     macOS     → .metadata_never_index
# Memex 取同样的机制：目录里放一个 .memex-noindex，整棵子树不进索引。
# 这是「内置软件名单」永远追不上新软件时的正解 —— 用户自己一放就生效，
# 零误伤风险，也不必等我们更新名单。
NOINDEX_MARKER = '.memex-noindex'


def has_noindex_marker(path):
    """这个目录里有没有「不索引」标记文件（.memex-noindex）。"""
    try:
        return os.path.isfile(os.path.join(str(path), NOINDEX_MARKER))
    except Exception:
        return False


# 与 .memex-noindex 一对：用户想让某个目录**一定**被索引（软件名单误伤了它）
# 时，在目录里放一个 .memex-keep 即可豁免。名字在资源管理器里能看见，
# 删掉就恢复默认 —— 不需要改配置、不需要重启。
KEEP_MARKER = '.memex-keep'


def has_keep_marker(path):
    """这个路径所在的目录链上有没有「保留」标记文件（.memex-keep）。

    向上最多找 4 层：在 D:\\ 放一个就能豁免 D:\\ 下所有被名单误伤的目录。
    """
    try:
        p = os.path.abspath(str(path))
    except Exception:
        return False
    for _ in range(4):
        try:
            if os.path.isfile(os.path.join(p, KEEP_MARKER)):
                return True
        except Exception:
            pass
        np_ = os.path.dirname(p)
        if not np_ or np_ == p:
            break
        p = np_
    return False


SOFTDIR_DOC_EXTS = {
    '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.pdf', '.rtf',
    '.epub', '.md', '.csv', '.txt', '.wps', '.et', '.dps', '.html', '.htm',
}
# 真源码：有它的目录一律不当软件目录（否则用户的项目会被误排）。
# 清单要全：用户的课件包里就有 1,770 个 .rb，早先漏了 .rb → 课件包被当成
# 「软件目录」误排（那是用户自己的资料，绝不能排）。
SOFTDIR_SRC_EXTS = {
    '.py', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.rb', '.php',
    '.cs', '.c', '.cpp', '.h', '.hpp', '.swift', '.kt', '.vue', '.ipynb',
    '.lua', '.pl', '.sh', '.ps1', '.psm1', '.r', '.m', '.mm', '.scala',
    '.dart', '.hs', '.jl', '.zig', '.sql',
}

_softdir_cache = {}


def is_install_root(path):
    """这个路径是不是落在「软件安装目录」里？是的话只收应用类扩展名。"""
    lp = os.path.normcase(str(path))
    return any(s in lp for s in INSTALL_ROOT_SEGS)


def looks_like_software_dir(path):
    """这个目录是不是「软件安装目录」（该只收应用入口，不该收几千张素材图）？

    判定用子树的扩展名构成，见上面 SOFTWARE_DIR_SNIFF 的注释。
    缓存按目录，结果只依赖目录内容，重建索引期间不会变。
    """
    if not SOFTWARE_DIR_SNIFF:
        return False
    try:
        key = os.path.normcase(os.path.abspath(str(path)))
    except Exception:
        return False
    if key in _softdir_cache:
        return _softdir_cache[key]
    try:
        res = _probe_softdir(path)
    except Exception:
        res = False
    _softdir_cache[key] = res
    return res


def _probe_softdir(path):
    """判断这个目录是不是「软件资产目录」（该只收应用入口，不该收几千张素材）。

    第 242 项：判据整个换掉了。

    旧判据（按子树文件构成比例）实测**不可行**，原因都写在上面那段注释里：
    「程序物占比」在钉钉/腾讯会议/靶场之间区分度不到 3 个百分点；
    「源码占比」分不清软件自带的 .py 和用户写的代码；
    「项目标志文件」会误伤散装拷贝的渗透靶机。

    新判据（Everything 的模型：不猜，只排明确的）：
      ① 路径里有一段是**程序资产目录名**（resources/locales/runtime/webcontent…）；
      ② 它的**父目录里有 .exe** —— 这一条是「这是安装目录」的确证，
         也是安全阀：用户的项目目录里就算有 resources/runtime，父目录没有 exe 就不排。
      ③ 靶场目录名无条件保护（用户明说 phpstudy 的 WWW 是渗透靶场）。
    """
    import collections as _c

    lp = os.path.normcase(str(path))
    # ③ 靶场目录名：路径里出现就无条件保护
    for seg in SOFTDIR_ALWAYS_KEEP_SEGS:
        s = os.path.normcase(seg)
        if ('\\' + s + '\\') in (lp + '\\') or lp.endswith('\\' + s):
            return False

    # ① 路径里有程序资产目录名？
    segs = [x for x in lp.split('\\') if x]
    if not any(x in SOFTDIR_ASSET_SEGS for x in segs):
        return False

    # ② 该资产段的父目录里有 exe？（向上找 2 级，够覆盖 <app>\Resources\<ver>\ 这种）
    for i, x in enumerate(segs):
        if x not in SOFTDIR_ASSET_SEGS:
            continue
        if i == 0:
            continue
        parent = '\\'.join(segs[:i])
        for up in range(2):
            try:
                names = os.listdir(parent)
            except Exception:
                names = []
            has_exe = False
            for n in names:
                if n.lower().endswith('.exe'):
                    has_exe = True
                    break
            if has_exe:
                return True
            parent = os.path.dirname(parent)
            if not parent:
                break
    return False


def looks_like_software_dir_parent(path):
    """path 的**父目录**是不是软件安装目录（含 exe）？

    第 242 项：实时监听与清理路径要用它判断「这个文件是不是躺在程序资产目录里」。
    """
    try:
        d = os.path.dirname(os.path.abspath(str(path)))
    except Exception:
        return False
    for _ in range(2):
        try:
            for n in os.listdir(d):
                if n.lower().endswith('.exe'):
                    return True
        except Exception:
            pass
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return False


def is_asset_path(path):
    """这个路径是不是「程序资产目录」里的文件（要排）。

    两个条件都要满足：路径里有资产段名，且该段的父目录里有 exe。
    """
    lp = os.path.normcase(str(path))
    segs = [x for x in lp.split('\\') if x]
    if not any(x in SOFTDIR_ASSET_SEGS for x in segs):
        return False
    for seg in SOFTDIR_ALWAYS_KEEP_SEGS:
        s = os.path.normcase(seg)
        if ('\\' + s + '\\') in (lp + '\\') or lp.endswith('\\' + s):
            return False
    # 找出资产段的位置，往上找带 exe 的父目录
    for i, x in enumerate(segs):
        if x not in SOFTDIR_ASSET_SEGS or i == 0:
            continue
        parent = '\\'.join(segs[:i])
        for _ in range(2):
            try:
                for n in os.listdir(parent):
                    if n.lower().endswith('.exe'):
                        return True
            except Exception:
                pass
            np_ = os.path.dirname(parent)
            if not np_ or np_ == parent:
                break
            parent = np_
    return False


def never_index(path):
    """这个路径是不是「内置硬排除」的树（AppData / ProgramData / 回收站 / 旧系统目录）。

    为什么要单独一个函数：这些目录即使用户手工写进了 include_dirs，也不能作为扫描根。
    踩过的坑：`drive_targets()` 原先把「显式 include 但命中 SKIP_DIRS」的目录单独当根扫
    （为便携部署在 Program Files 下留的口子），于是 include 里那条 AppData 反而被
    原样扫进来 96,031 条。微软 Windows Search 对这些目录也是硬排除，不给用户开口子。
    末尾不带反斜杠的写法（`C:\\Users\\x\\AppData`）也要命中，所以先 rstrip 再比结尾。"""
    lp = os.path.normcase(str(path)).rstrip('\\')
    for s in DEFAULT_SKIP_SEGS:
        t = os.path.normcase(s)
        if t in lp or lp.endswith(t.rstrip('\\')):
            return True
    return False

# Memex从「文档检索」扩为「全文件检索」（对标 Listary）：
# 视频/图片/应用/音频/压缩包/源码/文档全部入索引，按 size 跳过超 50MB 的大文件。
# 系统临时文件、缓存、备份类型主动排除，避免索引冗余。
ALL_FILES = {
    # 文档（原 DOCS）
    '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.md', '.markdown', '.rtf', '.html', '.htm', '.txt', '.epub', '.mobi',
    # 视频
    '.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm', '.m4v', '.mpg', '.mpeg',
    # 图片
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.ico', '.heic', '.psd',
    # 应用程序
    '.exe', '.lnk', '.bat', '.cmd', '.ps1', '.appref-ms', '.msi', '.scr',
    # 音频
    '.mp3', '.aac', '.flac', '.wav', '.ogg', '.m4a', '.wma',
    # 压缩包
    '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso',
    # 源码 / 配置
    '.py', '.js', '.ts', '.tsx', '.jsx', '.cpp', '.c', '.h', '.hpp', '.cs',
    '.java', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.json', '.xml',
    '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.csv', '.log',
    # 其它常见格式
    '.torrent', '.apk', '.dmg', '.deb', '.rpm',
}
# 索引时按 size 上限跳过：视频/压缩包常 >100MB，全收会很慢且检索意义不大
SIZE_LIMIT = 80 * 1024 * 1024   # 80 MB
# 第 235 项：媒体文件（视频/音频/光盘镜像）**不受体积上限**。
# 它们本来就不读正文，进清单只为「文件名能搜到」；而 4K 影片动辄 20~60GB，
# 被上限挡掉之后，用户加了盘、点了更新，里面一部片都搜不出来。
MEDIA_EXTS = {
    # 视频
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts',
    '.rmvb', '.rm', '.mpg', '.mpeg', '.m2ts', '.vob', '.3gp', '.asf', '.f4v',
    # 音频
    '.mp3', '.flac', '.wav', '.aac', '.m4a', '.ogg', '.wma', '.ape', '.dff', '.dsf',
    # 光盘镜像（体积大但用户会按名字找）
    '.iso', '.img', '.mdf', '.nrg',
}
DOCS = ALL_FILES                # 兼容旧引用
BIG_TXT = 200 * 1024

def app_dir():
    """放扫描清单的地方 —— 必须和 brain.app_dir() 一致。

    否则用户在只读目录安装、或设置了 SB_DATA_DIR 时，清单会写到程序目录，
    而正文索引去数据目录找，结果是「扫描跑完了、一篇正文都没有」。
    老办法（按脚本位置推断）保留为兜底。"""
    try:
        import brain
        return brain.app_dir()
    except Exception:
        return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

def load_includes():
    """读 scope.json 的 include_dirs（读不到/格式坏就返回空表，不影响全盘扫描）"""
    try:
        import json
        p = app_dir() / 'scope.json'
        data = json.loads(p.read_text(encoding='utf-8'))
        return [str(x) for x in data.get('include_dirs', [])]
    except Exception:
        return []

def _has_skip_segment(path):
    """路径中是否含 SKIP_DIRS 某一段：这类目录全盘扫描会被跳过，必须单独当根扫"""
    return any(part.lower() in SKIP_DIRS for part in Path(path).parts)

def drive_targets():
    import string
    out = []
    for d in string.ascii_uppercase:
        root = f'{d}:\\'
        if os.path.exists(root):
            out.append(os.path.expanduser('~')
                       if d + ':' == os.environ.get('SystemDrive', 'C:') else root)
    # 用户的资料目录可能被放在 "Program Files" 这类系统目录下（便携部署很常见），
    # 整条路径命中 SKIP_DIRS 就会被全盘扫描整片跳过，所以显式范围内的这类目录
    # 单独作为扫描根，重复的按规范化路径去重。
    seen = {os.path.normcase(os.path.abspath(x)) for x in out}
    for inc in load_includes():
        if not os.path.isdir(inc) or not _has_skip_segment(inc):
            continue
        # 第 236 项：AppData / ProgramData 这类「内置硬排除」的树，即使被写进
        # include_dirs 也不当扫描根 —— 否则用户那句「我明明没加它」就永远说不清。
        if never_index(inc):
            continue
        key = os.path.normcase(os.path.abspath(inc))
        if key not in seen:
            seen.add(key)
            out.append(inc)
    return out

def is_novel_dir(path):
    low = path.lower()
    return any(w in low for w in NOVEL_WORDS)

def run_scan(targets=None):
    """targets=None 时全盘扫描（命令行老行为）；桌面版传入用户选的文件夹列表。"""
    import brain  # 调用时机在 brain 加载完成后，无循环依赖问题
    rows, novel_kw = [], 0
    roots = [str(t) for t in targets] if targets is not None else drive_targets()
    brain._prog(state='busy', phase='scan', done=0, total=len(roots), ok=0, fail=0, err='')
    for i, target in enumerate(roots):
        # 范围里的文件夹被搬走/盘符变了：os.walk 会安静地返回空，看起来和"这盘确实
        # 没文档"一模一样。这里必须留痕，否则后面 prune 会把整库当"用户删了"清掉。
        if not os.path.isdir(target):
            print(f'[!] 跳过：{target} 不存在（搬走了？盘符变了？）', flush=True)
            brain._prog(done=i, msg=f'{target} 不存在，已跳过', k='')
            continue
        brain._prog(done=i, msg=f'正在扫描 {target}', k='m_scanning', a={'p': target})
        # 扫描是"静默"的：一分钟一条输出都没有，用户会以为死机，去连点两次、
        # 或者干脆关掉黑窗。每 3 秒报一次心跳（含当前目录），看着心里有数。
        n_seen = 0
        t_hb = time.time()
        errs = [0]

        def _onerr(e):
            errs[0] += 1

        for cur, dirs, files in os.walk(target, onerror=_onerr):
            # 剪掉 junction / 符号链接：os.walk(followlinks=False) 用 os.path.islink 判断，
            # 而 Windows 上 junction 的 islink 是 False，指向上层的 junction 会成环，
            # 让正文扫描永远跑不完。判据见 brain._is_reparse_dir。
            dirs[:] = [d for d in dirs if not brain._is_reparse_dir(os.path.join(cur, d))]
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith('.')]
            if is_novel_dir(cur):
                dirs[:] = []
                novel_kw += 1
                continue
            n_seen += len(files)
            if time.time() - t_hb >= 3:
                t_hb = time.time()
                print(f'  ...已看过 {n_seen:,} 个文件（当前：{cur}）', flush=True)
                brain._prog(msg=f'正在扫描 {target}（已看 {n_seen:,} 个文件）', k='')
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in DOCS:
                    continue
                p = os.path.join(cur, f)
                try:
                    sz = os.path.getsize(p)
                except OSError:
                    continue
                # 大文件不入索引（视频/压缩包常 >80MB，意义不大还拖慢检索）
                # 第 235 项：媒体例外 —— 它们不读正文，进清单只为文件名可搜。
                if sz > SIZE_LIMIT and ext not in MEDIA_EXTS:
                    continue
                rows.append((p, sz))
        if errs[0]:
            print(f'  [!] 有 {errs[0]} 个文件夹读不进去（权限/占用），已跳过', flush=True)
    brain._prog(phase='scan-end', msg=f'发现 {len(rows)} 个文档，准备入库',
                k='m_scan_end', a={'n': len(rows)})

    # 同一文件可能被"盘符根"和"显式范围根"各走到一次：按路径去重（保序）
    uniq = {}
    for p, sz in rows:
        uniq.setdefault(p, sz)
    rows = list(uniq.items())

    out = app_dir() / 'scan_raw.csv'
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['path', 'size'])
        w.writerows(rows)
    print(f'扫描完成：{len(rows)} 个文件（文档/视频/图片/应用/音频/压缩包等全文件类型，跳过小说类目录 {novel_kw} 个，超 {SIZE_LIMIT//1024//1024}MB 跳过）-> {out.name}')
    return len(rows)

if __name__ == '__main__':
    run_scan()
