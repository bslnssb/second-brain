# 第二大脑 · Local Second Brain

[English](README.md) | **简体中文**

把你硬盘上散落多年的文档，变成一个能搜的本地知识库。**全文搜索、中文友好、全程离线**，不传云端、不建索引服务、不依赖任何账号。

Python + SQLite FTS5 实现，一个 `brain.db` 装下所有正文，断网可用。

> 为什么不是 Everything？Everything 只搜文件名。这里搜的是**文件里写了什么**。
> 为什么不用在线笔记？你的合同、病例、内部培训资料不该上传到别人服务器。

---

## 特性

- **中文全文搜索**：≥3 字走 FTS5 trigram 倒排（快且带高亮），1~2 字自动降级 LIKE 兜底
- **扫描件自动 OCR**：纯图片 PDF 自动识别前 15 页，离线中文识别（RapidOCR），装好后零联网
- **格式覆盖广**：PDF / DOCX / PPTX / XLSX / EPUB / RTF / HTML / TXT / MD
- **增量维护**：改过的文件自动重索引，删掉的自动清理，已索引的秒跳过
- **断点续建**：索引几万篇时中断也不要紧，重跑从断点继续
- **中英双语界面**：网页右上角有 中 / EN 切换，默认跟随浏览器语言，选择会记住
- **质量自检（少见但重要）**：`python brain.py selftest` 用合成数据独立验证检索准确率、双 PDF 引擎交叉比对、OCR 字符错误率、9 类畸形文件 fuzz、数据库完整性，报告可任何人重跑复核
- **零上传**：所有解析在本机完成，服务只绑 `127.0.0.1`

## 两种用法

| 方式 | 适合谁 | 怎么开始 |
|---|---|---|
| **下载 exe**（免安装） | 不想碰命令行 | 去 [Releases](../../releases) 下 `second-brain.exe`（带 OCR，约 350MB）或 `second-brain-core.exe`（不带 OCR，约 36MB），放到任意文件夹双击即开窗口 |
| **从源码跑** | 想改代码 / 想看清每一步 | 按下面「安装」走，装好依赖再启动 |

exe 版和源码版读写**同一个** `brain.db`，两者可以混用；把 exe 丢进已有索引库的目录，它会自动认旁边的库。

## 安装

需要 Python 3.10+（推荐 3.12）。

```bash
pip install -r requirements.txt
```

首次使用把范围配置准备好：

```bash
cp scope.example.json scope.json    # Windows: copy scope.example.json scope.json
```

编辑 `scope.json`，把你自己的目录写进 `include_dirs`（路径全小写，反斜杠要写成 `\\`）。

## 用法

```bash
python scan_docs.py                 # 1. 扫描磁盘，生成待索引进清单（只记路径，不读内容）
python brain.py index scope.json    # 2. 建索引（断点续建，实时显示 篇数/速率/ETA）
python brain.py serve               # 3. 打开网页界面 http://127.0.0.1:8765
```

日常维护一条命令搞定（重扫 → 增量索引 → 清理失效条目）：

```bash
python brain.py update
```

其他：

```bash
python brain.py search 关键词 [条数] [扩展名] [路径含]   # 命令行搜索
python brain.py prune                                  # 只清理已删除文件的条目
python brain.py selftest                               # 质量自检，报告写入 eval_report.txt
```

### 桌面窗口版

不想看到命令行窗口，运行 `python app_window.py`（Windows 上也可双击 `start.bat` 或 `启动第二大脑.bat`）：它用 pywebview 起一个**独立桌面窗口**
（不是浏览器标签页），背后跑本机的 HTTP 服务。没装 pywebview 时自动退回浏览器打开。

`update.bat`（或 `更新第二大脑.bat`）是日常维护入口，等价于 `python brain.py update`。

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

| 参数 | 作用 |
|---|---|
| `?q=关键词` | 打开就直接出结果 |
| `?lang=zh` / `?lang=en` | 这条链接强制用指定语言 |

例：`http://127.0.0.1:8765/?q=报销标准&lang=zh`

## 质量自检

`eval_report.txt` 是一份真实跑出来的样例报告：

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
ocr_backfill.py     把历史「提不出文本」的文件重走一遍索引
gen_icon.py         生成应用图标
scope.example.json  范围配置模板
start.bat           Windows：打开桌面窗口
update.bat          Windows：一键完成 重扫 → 索引 → 清理
README.md          本文档的英文版
brain.db            （运行时生成）SQLite 索引库，docs=FTS5 表，meta=已处理清单
scan_raw.csv        （运行时生成）最近一次扫描清单
eval_report.txt     最近一次自检报告
```

## 许可证

MIT
