# 第二大脑 · Local Second Brain

**简体中文** | [English](README.en.md)

把你硬盘上散落多年的文档，变成一个能搜的本地知识库。**全文搜索、中文友好、全程离线**，不传云端、不建索引服务、不依赖任何账号。

Python + SQLite FTS5 实现，一个 `brain.db` 装下所有正文，断网可用。

> 为什么不是 Everything？它只知道文件叫什么，不知道里面写了什么。第二大脑两样都搜，但真正的价值在后者——你记得一句原文、却想不起存在哪份文件，这是它答不了的。
> 为什么不用在线笔记？你的合同、病例、内部培训资料不该上传到别人服务器。

---

<p align="center"><img src="assets/screenshot-quick.png" alt="双击 Ctrl 弹出的快搜窗：搜「报」，列出命中的文件（带真实系统图标）、正文命中带紫色标签，每行带 Ctrl+1~9 快捷键"></p>

<p align="center"><sub>双击 Ctrl 弹出的快搜窗 —— 敲一个字就出结果，名字和正文一起搜，Ctrl+1~9 直接打开</sub></p>

<p align="center"><img src="assets/screenshot-zh.png" alt="第二大脑的 exe 窗口：搜索「文档」，命中 7 篇，每条显示命中的那几行并高亮关键词"></p>

<p align="center"><sub>主窗口是「详细搜索」：正文检索、命中原句、类型与路径过滤。右上角可切 EN</sub><br>
<img src="assets/screenshot-en.png" width="620" alt="第二大脑 exe 窗口的英文界面"></p>

## 特性

- **双击 Ctrl 快搜**：任何地方连按两下 Ctrl，屏幕上方弹出搜索框，敲两个字出结果，`↑↓` 选、`Enter` 开、`Ctrl+1~9` 直接开第 N 条、`Esc` 收起
- **拼音也能搜**：输入 `azb` 出「安装包」，`xzrs` 出「行政人事」——首字母和全拼都认，不用记完整拼写
- **正文命中进快搜窗**：名字里没有、正文里有的，停手 300ms 后自动补进来，带紫色「正文」标签和命中那句话——这是只看文件名的工具做不到的
- **真实系统图标**：每行显示该扩展名在资源管理器里的同一张图标（PDF 红、Word 蓝、Excel 绿），不是自绘简笔画
- **中文全文搜索**：≥3 字走 FTS5 trigram 倒排（快且带高亮），1~2 字自动降级 LIKE 兜底
- **扫描件自动 OCR**：纯图片 PDF 自动识别前 15 页，离线中文识别（RapidOCR），装好后零联网
- **格式覆盖广**：PDF / DOCX / PPTX / XLSX / EPUB / RTF / HTML / TXT / MD
- **增量维护**：改过的文件自动重索引，删掉的自动清理，已索引的秒跳过
- **断点续建**：索引几万篇时中断也不要紧，重跑从断点继续
- **中英双语界面**：右上角有 中 / EN 切换，默认跟随系统语言，选择会记住
- **质量自检（少见但重要）**：`python brain.py selftest` 用合成数据独立验证检索准确率、双 PDF 引擎交叉比对、OCR 字符错误率、9 类畸形文件 fuzz、数据库完整性，报告可任何人重跑复核
- **零上传**：所有解析在本机完成，服务只绑 `127.0.0.1`

## 两种用法

| 方式 | 适合谁 | 怎么开始 |
|---|---|---|
| **下载 exe**（免安装） | 不想碰命令行 | 去 [Releases](https://github.com/bslnssb/second-brain/releases/latest) 下 `SecondBrain-Windows.zip`（带 OCR，下载 165MB，解压后约 321MB）或 `SecondBrain-Core-Windows.zip`（不带 OCR，下载 27MB，解压后约 53MB），解压到任意文件夹，双击 `SecondBrain.exe` 即开窗口 |
| **从源码跑** | 想改代码 / 想看清每一步 | 按下面「安装」走，装好依赖再启动 |

exe 版和源码版读写**同一个** `brain.db`，两者可以混用；把 exe 丢进已有索引库的目录，它会自动认旁边的库。

两个版本都是**整个文件夹**，不是单个文件：exe 旁边的 `_internal/` 是程序的一部分。
要移动就整夹移动——只把 exe 单独拎出来会打不开。

精简版只砍了 OCR，别的没动：扫描版 PDF 提不出文字（在失败归因里记为「无文本」），
文本型 PDF、Office、EPUB 等等表现完全一样。

## 安装

（exe 用户跳过本节——设置面板里的「🚀 智能扫描全盘」会替你完成这一切。）

需要 Python 3.10+（推荐 3.12）。

```bash
pip install -r requirements.txt
```

首次使用把范围配置准备好：

```bash
cp scope.example.json scope.json    # Windows: copy scope.example.json scope.json
```

编辑 `scope.json`，把你自己的目录写进 `include_dirs`（大小写随意，程序会还原成磁盘上的真实名字；反斜杠要写成 `\\`）。

## 用法

### 图形界面（exe 的正常用法，全程不碰命令行）

**日常：双击 Ctrl。** 任何时候连按两下 Ctrl，屏幕上方会弹出快搜窗——一个输入框、九行结果：

| 按键 | 作用 |
|---|---|
| 直接打字 | 边打边出结果（前缀命中走索引，通常 0~2 毫秒） |
| 打拼音 | `azb` 出「安装包」，`xzrs` 出「行政人事」——首字母和全拼都认 |
| `↑` `↓` | 上下选 |
| `Enter` | 打开当前行 |
| `Ctrl+1` ~ `Ctrl+9` | 直接打开第 N 行，手不用离开键盘 |
| `Ctrl+Enter` | 跳到主窗口，用同一个关键词做正文检索 |
| `Esc` | 收起（点到别的窗口也会自动收起） |

停手 300 毫秒后会自动补一次「包含」搜索：名字任意位置含关键词的、以及**正文里含关键词的**，带紫色「正文」标签和命中那句话一起排进列表。名字不够用的时候，正文来补。

关掉主窗口不会退出程序——它收在托盘里继续等你的双击 Ctrl。要真的退出，右键托盘图标选「退出」。

<p align="center"><img src="assets/screenshot-quick-empty.png" width="680" alt="刚唤出的快搜窗：一个输入框、最近打开的文件列表、每行带真实系统图标"></p>

<p align="center"><sub>刚唤出来的样子：最近开过的文件已经排在那了，不用打字也有东西可选</sub></p>

**第一次使用：**

1. 双击 `SecondBrain.exe` 打开主窗口，点右上角「⚙ 设置 / 更新」
2. 点「🚀 智能扫描全盘」——程序自动找出值得索引的文件夹，列出来让你确认后建库
3. 扫描完成后点「🗂 重建文件名索引」（约 30 秒~3 分钟，取决于文件数）——这是快搜窗的数据来源，
   只记名字不读内容，把全盘的文件名和文件夹名收进索引
4. **日常维护**：来了新文档，设置里点「🔄 按现有范围更新」；挪动了大批文件，再点一次「🗂 重建文件名索引」

主窗口本身是「详细搜索」：搜正文、看命中的那几行原文、按类型/路径过滤。

设置面板——扫描、更新、体检都在这里，不用记任何命令：

<p align="center"><img src="assets/screenshot-settings-zh.png" width="880" alt="第二大脑 exe 设置面板：索引范围与智能扫描/添加文件夹/保存并更新索引/按现有范围更新/引擎体检五个按钮"></p>

<p align="center"><sub>同一面板切到 English 的样子：</sub><br>
<img src="assets/screenshot-settings-en.png" width="620" alt="Second Brain settings panel in the exe window"></p>

### 和 Listary / Everything 是什么关系

**如果你要的就是「双击 Ctrl 打字找文件」，装上第二大脑就够了。**
快搜窗按大家熟悉的操作习惯做：双击 Ctrl 唤出、一行输入、九行结果、`Ctrl+1~9` 直接打开、`Esc` 收起、
**拼音模糊**（`azb` 出安装包）、**真实系统图标**（和资源管理器里同一张）、**空状态列最近文件**、
**路径分级显示**（`C: › … › Library › 行政人事`）。
文件名和文件夹名一起搜 —— 输入一个「报」字，文件和目录都排进同一个列表，`Enter` 打开文件或进到那个目录。

**正文也能在快搜窗里出**：停手 300ms 后自动补一次正文搜索，命中带紫色「正文」标签和那句话的片段。
这两家都只看名字，所以这一条是第二大脑独有的。

**它没做的部分，也说清楚**：Listary 还有几样第二大脑没有 —— 在「打开 / 保存」对话框里直接跳目录、
在资源管理器里选中文件、以及索引跟着 NTFS 实时更新（我们的文件名索引是一次扫描的快照，要手动重扫）。
所以如果你把 Listary 当「文件对话框加速器」用，两者不冲突，可以都装。

**Everything 呢**：它的索引直接由 NTFS 文件表建成，几百万个文件也是秒级，还跟着系统实时更新。
第二大脑的文件名索引是静态快照，胜在能顺手搜到「名字里没有、正文里有」的东西。

| | 找「我知道它叫什么」 | 问「哪句话在哪份文件里」 | 索引更新 |
|---|---|---|---|
| Everything | 最强，实时 | 做不到 | 跟盘实时 |
| Listary | 强，还带拼音和启动器 | 做不到（要靠搭档 Gety） | 跟盘实时 |
| **第二大脑** | 够用：全盘 60 万文件名 + 12 万文件夹名，前缀 0.2ms，带拼音 | **快搜窗里就有** | 手动「重建文件名索引」 |

装过 Everything 的老用户还能用 `/api/files` 那条兜底通道（装 Everything 并开着它的 HTTP 服务时生效），
但那只是给「已经装了 Everything、还没建过文件名索引」的人准备的过渡路径，正常用不到。

**命令行入口还在**（给快捷方式 / 别的启动器用）：

| 命令 | 效果 |
|---|---|
| `SecondBrain.exe 报销标准` | 开主窗口并直接出结果 |
| `SecondBrain.exe --settings` | 开主窗口直接展开设置面板 |
| `SecondBrain.exe --silent` | 后台常驻：不显示任何窗口，只等双击 Ctrl |
| `SecondBrain.exe --mini` | 启动时直接弹出快搜窗 |
| `SecondBrain.exe --lang en 报销` | 强制英文界面（`zh` 同理） |

**开机自启**：把 `SecondBrain.exe` 的快捷方式放进 `shell:startup`，目标后面补上 `--silent`，
开机后就安静待在托盘里，随时双击 Ctrl。要退出就右键托盘图标。

> 第二大脑是独立实现：没有使用上述任何产品的代码、图标或界面素材，与它们也没有任何隶属或授权关系。
> 文中提到的产品名只用于说明差异。

**如果你已经习惯 Listary 的输入框**：也可以在 Listary 里挂一条命令（选项 → 命令 → ➕），
关键字 `sb`、参数 `"{query}"`，输 `sb 报销流程` 直达第二大脑。只是现在有了快搜窗，这条属于可选。

### 命令行（源码版进阶，exe 用户不需要）

```bash
python brain.py update              # 等价于设置里的「🔄 按现有范围更新」：重扫 → 增量索引 → 清理
python brain.py serve               # 打开网页界面 http://127.0.0.1:8765
python scan_docs.py                 # 只扫描磁盘，生成待索引清单（不读内容）
python brain.py index scope.json    # 只建索引（断点续建，实时显示 篇数/速率/ETA）
python brain.py search 关键词 [条数] [扩展名] [路径含]   # 命令行搜索
python brain.py prune               # 只清理已删除文件的条目
python brain.py selftest            # 质量自检，报告写入 eval_report.txt
```

### 桌面窗口版（源码用户）

不想看到命令行窗口，运行 `python app_window.py`（Windows 上也可双击 `start.bat`）：它用 pywebview 起一个**独立桌面窗口**
（不是浏览器标签页），背后跑本机的 HTTP 服务。没装 pywebview 时自动退回浏览器打开。
`update.bat` 是日常维护入口，等价于 `python brain.py update`。

## 索引范围配置

`scope.json` 两个关键字段：

| 字段 | 说明 |
|---|---|
| `include_dirs` | 要索引的目录，前缀匹配。路径写小写，Windows 反斜杠要双写 |
| `exclude_dirs` | 排除的关键词，命中路径任意片段即跳过 |

`scan_docs.py` 另有一层保险：目录名含「小说 / 书城 / 书架 / novel / fiction / 轻小说」等关键词的整棵子树零接触，连扫描清单里都不会出现。

**如果你的资料放在 `C:\Program Files` 这类系统目录下**（比如便携式部署），注意 `scan_docs.py`
的跳过名单里有这些目录。把该目录写进 `include_dirs` 后，它会自动作为独立扫描根加入
（v6.0 起支持），不会漏扫。

## 支持的格式与已知限制

| 格式 | 支持 | 说明 |
|---|---|---|
| .txt / .md | 是 | 自动识别 utf-8 / gbk / utf-16，另有 gb18030 兜底 |
| .pdf | 是 | pypdf 抽前 20 页；纯扫描件自动 OCR 前 15 页（单篇 60 秒预算） |
| .docx / .pptx / .xlsx / .epub | 是 | zip + XML 解包取文本，无外部依赖 |
| .html / .htm | 是 | 去标签取正文 |
| .rtf | 是 | 去控制字取正文；中文按文档自带的 `\ansicpg` 代码页解码，`\'b1\'a3` 这类 GBK 字节转义和 `\uNNNN` 都能正确还原 |
| .doc / .xls / .ppt / .wps / .et / .dps / .mobi | 否 | 旧版二进制 Office / 电子书格式，纯 Python 解析不可靠；建议先用 WPS 或 LibreOffice 批量转 .docx / .xlsx / .pptx |
| 企业加密文档 | 否 | 文件头异常的加密件会记为「加密」并跳过，不会污染索引 |
| 单文档正文 | 2MB 上限 | 超长部分截断，避免个别巨型文件拖垮索引 |

索引失败的文件会在 `meta` 表里记原因（加密 / 损坏 / 空文件 / 临时文件 / 旧版格式），
每轮索引结束会按原因汇总打印一行，一眼看出是该补 OCR 还是该转格式，不会静默丢失。

## 搜索语法

直接输入关键词即可。多个词用空格分隔，按 AND 组合；每个词都当字面短语处理，
所以 `C++`、`node.js`、`a-b`、`"引号"`、`(括号)` 都能直接搜，不会触发 FTS 语法错误。

**文件名也参与检索。** 关键词只出现在文件名里、正文里完全没有的文档照样能搜到，
并且会把文件名里的命中处高亮出来——搜「2026 预算」能找到 `2026预算草案.xlsx`，
哪怕表格里面一次都没写过这个词。

两个过滤框：**类型**填扩展名（`pdf`、`docx`…），**路径包含**按完整路径的子串过滤（不区分大小写）。

### URL 参数

收藏常用搜索、做快捷方式时好用：

| 参数 | 作用 | exe 里的等价写法 |
|---|---|---|
| `?q=关键词` | 打开就直接出结果 | `SecondBrain.exe 关键词` |
| `?cfg=1` | 打开就展开设置面板 | `SecondBrain.exe --settings` |
| `?lang=zh` / `?lang=en` | 这条链接强制用指定语言 | `SecondBrain.exe --lang en` |

例：`http://127.0.0.1:8765/?q=报销标准&lang=zh`

## 质量自检

下面是 `python brain.py selftest` 真实跑出来的输出（自己跑一次，完整报告会写到同目录的 `eval_report.txt`）：

```
[PASS] A1 检索评测 recall@3 = 5/5 —— recall@1=5/5, MRR=1.000
[PASS] A2 相关度排序 —— bm25 排序生效
[PASS] A3 加密 PDF 不入库不可检索 —— 确认零泄漏
[PASS] B  双抽取器一致性 pypdf vs pymupdf = 1.00
[PASS] C  OCR 字符错误率 CER = 7.3%
[PASS] D  畸形文件 fuzz：9 例 —— 零崩溃
[PASS] E  SQLite/FTS5 完整性检查
[PASS] F  文件变更自动重索引
[PASS] G1 失败归因 / G2 prune 清理
OVERALL: PASS
```

自检全程使用合成数据，在临时目录建库，**不碰你的真实索引**。

### QA 套件（发布前跑）

`selftest.py` 回答「功能对不对」，`qa_test.py` 回答「扛不扛得住」：边界输入
（FTS 语法、通配符、SQL 注入形状、5000 字长查询）、真实库上的延迟、并发请求、
资源泄漏、`/open` 接口的越权、以及英文界面有没有漏中文。只读，跑完写 `QA_REPORT.md`。

```
python qa_test.py            # 全量
python qa_test.py --quick    # 跳过并发与泄漏两节
```

最近一次在 8,626 篇 / 2.37 GB 的库上：**98 通过 / 0 失败 / 1 警告**。
抓到的 7 个缺陷连同修复前后的数字见 [QA_FINDINGS.md](QA_FINDINGS.md)。

## 隐私

- 正文解析、OCR、索引全部在本机完成，不发起任何网络请求
- HTTP 服务只绑 `127.0.0.1`，并校验 Host/Origin，POST 只接受 `application/json`
- 索引库就是同目录下的 `brain.db`，删掉即彻底清除
- 仓库里 `.gitignore` 已排除 `brain.db`、`scan_raw.csv`、`scope.json`——它们含你的文档正文和本机路径

## 目录结构

```
brain.py            核心：建索引 / 搜索 / 网页服务 / 清理 / 自检调度
scan_docs.py        磁盘扫描器（排除规则、小说关键词保险）
app_window.py       桌面窗口启动器（pywebview）
selftest.py         质量自检（合成数据）
qa_test.py          QA 套件：边界 / 延迟 / 并发 / 泄漏
ocr_backfill.py     把历史「提不出文本」的文件重走一遍索引
gen_icon.py         生成应用图标
scope.example.json  范围配置模板
start.bat           Windows：打开桌面窗口
update.bat          Windows：一键完成 重扫 → 索引 → 清理
build_exe.bat       Windows：打包两个版本的可执行程序（需 PyInstaller）
second-brain-app.spec   完整版打包配置（含 OCR）
second-brain-core.spec  精简版打包配置（不含 OCR）
assets/             README 里的界面截图与应用图标
README.en.md        本文档的英文版
QA_FINDINGS.md      发布前那轮 QA 查出的 7 个缺陷与前后数据
brain.db            （运行时生成）SQLite 索引库，docs=FTS5 表，meta=已处理清单
scan_raw.csv        （运行时生成）最近一次扫描清单
eval_report.txt     （运行时生成）最近一次自检报告
QA_REPORT.md        （运行时生成）最近一次 QA 套件报告
```

## 许可证

MIT
