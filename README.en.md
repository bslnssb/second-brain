# Second Brain

[简体中文](README.md) | **English**

Turn the documents scattered across your hard drive into a local knowledge base you can actually search. **Full-text search, CJK-friendly, completely offline** — no cloud, no indexing service, no account.

Built with Python + SQLite FTS5. A single `brain.db` holds all the text; it works with the network cable unplugged.

> Why not Everything? Everything searches file *names*. This searches what's written *inside* the files.
> Why not an online note app? Your contracts, medical records and internal training material don't belong on someone else's server.

---

<p align="center"><img src="assets/screenshot-en.png" alt="Second Brain searching for a term: 7 matches, each showing the exact lines that matched with the keyword highlighted"></p>

<p align="center"><sub>Same index, switched to Chinese in the top-right corner — the UI language never changes what gets indexed</sub><br>
<img src="assets/screenshot-zh.png" width="620" alt="Second Brain Chinese interface"></p>

## Features

- **Full-text search that understands Chinese/Japanese/Korean** — 3+ characters use the FTS5 trigram index (fast, with hit highlighting); 1–2 character queries automatically fall back to `LIKE`
- **Automatic OCR for scans** — image-only PDFs get their first 15 pages recognised offline (RapidOCR); no network after install
- **Wide format coverage** — PDF / DOCX / PPTX / XLSX / EPUB / RTF / HTML / TXT / MD
- **Incremental maintenance** — modified files are re-indexed, deleted files are pruned, unchanged files are skipped instantly
- **Resumable indexing** — interrupt a 50k-file build and restart it; it continues where it stopped
- **Bilingual UI** — the web interface has a 中 / EN switch in the top-right corner; it follows your browser language by default and remembers your choice
- **Quality self-test (rare, but it matters)** — `python brain.py selftest` validates retrieval accuracy, cross-checks two PDF extractors, measures OCR character error rate, fuzzes 9 kinds of malformed files and checks database integrity. The report is reproducible by anyone
- **Zero upload** — every parser runs locally; the HTTP server binds to `127.0.0.1` only

## Two ways to run it

| Way | Who it's for | How to start |
|---|---|---|
| **Download the exe** (no install) | You'd rather not touch a terminal | From [Releases](https://github.com/bslnssb/second-brain/releases/latest) take `SecondBrain-Windows.zip` (with OCR, 157 MB download / ~320 MB unpacked) or `SecondBrain-Core-Windows.zip` (no OCR, 25 MB / ~52 MB), unzip anywhere, double-click `SecondBrain.exe` |
| **Run from source** | You want to read or change the code | Follow *Install* below |

The exe and the source version read and write the **same** `brain.db`, so you can mix them — drop the exe next to an existing library and it picks it up automatically.

Both editions are a **folder, not a single file**: `_internal/` next to the exe is part of the program.
Move the whole folder; pulling the exe out on its own leaves it unable to start.

The core edition gives up OCR and nothing else — image-only PDFs get no text (recorded as “no text” in the
failure breakdown), while text-based PDFs, Office files, EPUB and the rest behave identically.

## Install

Python 3.10+ required (3.12 recommended).

```bash
pip install -r requirements.txt
```

Create your scope configuration:

```bash
cp scope.example.json scope.json    # Windows: copy scope.example.json scope.json
```

Edit `scope.json` and put your folders in `include_dirs` (lowercase paths; backslashes must be doubled).

## Usage

```bash
python scan_docs.py                 # 1. scan disks, write the candidate list (paths only, contents untouched)
python brain.py index scope.json    # 2. build the index (resumable, shows count / rate / ETA)
python brain.py serve               # 3. open the web UI at http://127.0.0.1:8765
```

Routine maintenance is one command (rescan → incremental index → prune stale entries):

```bash
python brain.py update
```

Other commands:

```bash
python brain.py search <keywords> [limit] [ext] [path-contains]   # search from the terminal
python brain.py prune                                            # only remove entries for vanished files
python brain.py selftest                                         # quality self-test → eval_report.txt
```

### Desktop window

Don't want a console window? Run `python app_window.py` (or double-click `start.bat` on Windows): it opens a
**standalone desktop window** via pywebview — not a browser tab — backed by the local HTTP server.
If pywebview isn't installed it falls back to your default browser.

`update.bat` is the routine maintenance entry point — it's the same as `python brain.py update`.

## Configuring the index scope

Two fields matter in `scope.json`:

| Field | Meaning |
|---|---|
| `include_dirs` | Folders to index, matched by path prefix. Lowercase, doubled backslashes on Windows |
| `exclude_dirs` | Keywords; any path containing one is skipped |

`scan_docs.py` adds a second safety net: any directory whose name contains 「小说 / 书城 / 书架 / novel / fiction / 轻小说」 is never touched — it doesn't even appear in the candidate list.

**If your documents live under a system folder such as `C:\Program Files`** (portable installs, for example):
those folders are on the scanner's skip list. List them in `include_dirs` and each one is added as its
own scan root automatically (since v6.0), so nothing is missed.

## Supported formats and known limits

| Format | Supported | Notes |
|---|---|---|
| .txt / .md | Yes | Detects utf-8 / gbk / utf-16, gb18030 as a fallback |
| .pdf | Yes | pypdf reads the first 20 pages; image-only files get OCR on the first 15 (60 s budget) |
| .docx / .pptx / .xlsx / .epub | Yes | zip + XML unpacking, no external dependencies |
| .html / .htm | Yes | Tags stripped, body text kept |
| .rtf | Yes | Control words stripped; Chinese is decoded with the document's own `\ansicpg` codepage, so `\'b1\'a3`-style GBK and `\uNNNN` escapes both survive |
| .doc / .xls / .ppt / .wps / .et / .dps / .mobi | No | Legacy binary Office / ebook formats; pure-Python parsing is unreliable. Convert to .docx / .xlsx / .pptx with WPS or LibreOffice first |
| Vendor-encrypted documents | No | Files with an abnormal header are recorded as “encrypted” and skipped; they never pollute the index |
| Body length | 2 MB cap | Longer text is truncated so one giant file can't stall the index |

Files that fail to yield text are recorded with a reason in the `meta` table (encrypted / corrupted /
empty / temp file / legacy format). The end of an index run prints a breakdown by reason, so you can
tell at a glance whether you need OCR or a format conversion — nothing disappears silently.

## Search syntax

Plain keywords work. Multiple space-separated words are combined with AND, and every word is treated
as a literal phrase, so `C++`, `node.js`, `a-b`, `"quoted"` and `(parens)` are all safe to type —
they never trigger an FTS syntax error.

**File names are searched too.** If a keyword appears in a file's name but not in its body, the
document is still returned, with the name highlighted — so searching “2026 budget” finds
`2026-budget-draft.xlsx` even when the sheet itself never spells it out.

Filters: the **Type** box takes an extension (`pdf`, `docx`, …), the **Path contains** box filters by
substring of the full path (case-insensitive).

### URL parameters

Handy for bookmarks and shortcuts:

| Parameter | Effect |
|---|---|
| `?q=keyword` | Open the page with results already rendered |
| `?lang=zh` / `?lang=en` | Force the interface language for this link |

Example: `http://127.0.0.1:8765/?q=invoice&lang=en`

## Quality self-test

Output from an actual `python brain.py selftest` run (run it yourself and the full report lands in `eval_report.txt`):

```
[PASS] A1 retrieval recall@3 = 5/5 —— recall@1=5/5, MRR=1.000
[PASS] A2 relevance ranking —— bm25 ordering effective
[PASS] A3 encrypted PDF excluded —— zero leakage confirmed
[PASS] B  dual extractor agreement pypdf vs pymupdf = 1.00
[PASS] C  OCR character error rate CER = 7.3%
[PASS] D  malformed-file fuzz: 9 cases —— zero crashes
[PASS] E  SQLite/FTS5 integrity check
[PASS] F  file-change auto re-index
[PASS] G1 failure attribution / G2 prune
OVERALL: PASS
```

The whole run uses synthetic data in a temporary directory — **your real index is never touched**.

### QA suite (before release)

`selftest.py` answers "does it work". `qa_test.py` answers "does it hold up":
boundary inputs (FTS syntax, wildcards, SQL-injection shapes, 5 000-character queries),
latency on a real library, concurrent requests, resource leaks, the `/open` endpoint,
and whether the English UI leaks Chinese. It runs read-only against your own index and
writes `QA_REPORT.md`.

```
python qa_test.py            # full run
python qa_test.py --quick    # skip concurrency / leak sections
```

Last run on an 8,626-document / 2.37 GB library: **98 pass, 0 fail, 1 warning**.
The seven defects it caught — and the numbers behind the fixes — are in
[QA_FINDINGS.md](QA_FINDINGS.md).

## Privacy

- Parsing, OCR and indexing all happen locally; no network requests are made
- The HTTP server binds to `127.0.0.1` only, validates Host/Origin, and accepts `application/json` POSTs only
- The index is just `brain.db` next to the executable — delete it and everything is gone
- `.gitignore` excludes `brain.db`, `scan_raw.csv` and `scope.json`: they contain document text and local paths

## Project layout

```
brain.py            core: indexing / search / web server / pruning / self-test dispatch
scan_docs.py        disk scanner (exclusion rules, fiction-folder safety net)
app_window.py       desktop window launcher (pywebview)
selftest.py         quality self-test (synthetic data)
qa_test.py          QA suite: boundaries / latency / concurrency / leaks
ocr_backfill.py     re-run indexing for files that previously yielded no text
gen_icon.py         generates the app icon
scope.example.json  scope configuration template
start.bat           Windows: open the desktop window
update.bat          Windows: rescan → index → prune in one click
build_exe.bat       Windows: build both executables (needs PyInstaller)
second-brain-app.spec   PyInstaller config, full edition (with OCR)
second-brain-core.spec  PyInstaller config, core edition (no OCR)
assets/             UI screenshots and app icon used by the READMEs
README.en.md        this document (README.md is the Chinese version)
QA_FINDINGS.md      the 7 defects the pre-release QA round turned up, with before/after numbers
brain.db            (generated) SQLite index — docs = FTS5 table, meta = processed files
scan_raw.csv        (generated) last scan candidate list
eval_report.txt     (generated) last self-test report
QA_REPORT.md        (generated) last QA suite report
```

## License

MIT
