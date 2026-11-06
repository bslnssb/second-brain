# 公开仓库怎么维护（一页版）

这个仓库的内容**恰好等于** `tools/public_files.txt` 列的清单，没有别的。
下次发版别再凭记忆判断「哪些能传」——跑两条命令就行。

```bash
python tools/sync_public.py      # 按清单从开发根同步（开发根 = ../../）
python tools/check_public.py     # 体检：清单/黑名单/本机痕迹/版本号/体积
```

两者都返回 0 才算干净。CI 也会在每次 push 时跑 `check_public.py`（见
`.github/workflows/privacy.yml`），本地漏了还有一道。

## 清单里三种前缀

| 前缀 | 含义 | 例子 |
|---|---|---|
| `sync` | 从开发根（项目的上一层目录）同步过来；**开发根是唯一维护地** | `brain.py`、`web/src/`、`使用说明.md` |
| `local` | 只在这个仓库维护，开发根没有 | `*.spec`、`README.md`、`assets/screenshot-*.png` |
| `build` | 构建产物，`cd web && npm run build` 生成后同步过来；不进 git | `web/dist/` |

目录以 `/` 结尾表示整个子树。

## 常见场景

**加了新的源码文件**（比如 `brain.py` 里拆出一个 `foo.py`）
1. 在开发根里写好，在 `tools/public_files.txt` 加一行 `sync foo.py`
2. `python tools/sync_public.py && python tools/check_public.py`
3. 提交两个文件：`foo.py` 和 `public_files.txt`

**README 加了一张截图**
1. 图放进本仓库 `assets/`
2. 清单加一行 `local assets/xxx.png`
3. 跑 check（不加清单会被判「仓库里有多余文件」，这是故意的）

**改了界面**
```bash
cd ../../web && npm run build          # 产物在开发根 web/dist
cd - && python tools/sync_public.py    # 会把 web/src 和 web/dist 一起同步
```

**发版**
```bash
python tools/sync_public.py            # 1. 同步
python tools/check_public.py           # 2. 体检（含版本号三处一致）
# 3. 改版本号：brain.py APP_VERSION / setup_app.py VERSION / version.json 三处
mv dist/SecondBrain dist/SecondBrain-stale-$(date +%H%M)      # 见「坑」第 11 条
PY="C:/Users/…/Python312/python.exe"
$PY gen_version.py && $PY -m PyInstaller --noconfirm --clean second-brain-app.spec && $PY -B stage_app.py
$PY -B make_payload.py && $PY -m PyInstaller --noconfirm --clean SecondBrain-Setup.spec && $PY -B stage_setup.py
# 4. 提交推送，然后建 Release 传安装包
```

**check 报「仓库里有多余的跟踪文件」**
说明有人（可能是你）手滑 `git add` 了清单外的东西。要么加进清单，要么 `git rm`。
**不要**为了让 check 通过就把规则改松 —— 这条规则就是用来拦手滑的。

## 万一又泄露了：先分级，别急着重写历史

2026-11-06 那次上传花了 38 分钟，大头不是上传，是**重写 git 历史 + 全历史扫描 + 两次完整打包验证**。
以后按这个顺序判断，绝大多数情况根本不需要动历史：

| 情况 | 处理 | 要多久 |
|---|---|---|
| 还没 commit | `git checkout -- <文件>`，改干净再提交 | 10 秒 |
| 刚 commit，还没 push | `git reset --soft HEAD~1`，改完重新提交 | 1 分钟 |
| 已经 push，但没有别人 clone 过 | `git commit` 删掉内容后照常推；历史里那份**普通人不会去翻**，删了当前版本就够了 | 3 分钟 |
| 已经公开传播 / 里面有密钥 / 有身份信息 | 才算需要 `git filter-branch` 重写 + 强推 | 30 分钟起 |

判断依据是**内容有多敏感**，不是「有没有进过历史」：
- 目录名、机器型号、库体积 → 删当前版本即可
- 真名、身份证、电话、密码、token、私钥、私人照片 → 重写历史，并**立刻吊销密钥**

重写历史的命令（真要用时）：

```bash
git filter-branch -f --index-filter 'git rm -q --cached --ignore-unmatch <文件>' <起点>^..HEAD
git for-each-ref --format='%(refname)' refs/original | xargs -r -n1 git update-ref -d
git reflog expire --expire=now --all
git push --force-with-lease origin master
```

## 这个仓库里**永远不该有**的东西

`brain.db`（含你全部文档正文）、`scan_raw.csv`（全盘文件清单）、`scope.json`（你的目录结构）、
`settings.json`/`apps.json`（运行期配置）、`*.log`、`eval_report.txt`、
任何写着本机盘符和用户名的文档、任何私人笔记。

这些都被 `check_public.py` 的 `BAD_NAMES` / `private_words.txt` 拦着。
加词直接往 `tools/private_words.txt` 加一行，不用改代码。

> 为什么词表单独一个文件：脚本自己也在仓库里，词表明文写在脚本里会被脚本自己扫出来
> （第一版就是这样，自己把自己判成 FAIL）。扫描时会跳过这两个文件。

## 本地目录里什么会膨胀（不影响 git，但会吃掉几个 GB）

`.gitignore` 挡住了它们，所以不会误传，但它们会一直堆：

| 位置 | 每份多大 | 什么时候生成 | 什么时候能删 |
|---|---|---|---|
| `dist/SecondBrain-old-*` | 约 330 MB | 打包前 `mv dist/SecondBrain dist/SecondBrain-old-<hhmm>`（见坑 11） | **新版本验收通过后立刻删** |
| `build/` | 约 210 MB | PyInstaller 每次构建 | 构建成功即可删，下次自动重建 |
| `payload.zip` | 约 170 MB | `make_payload.py` | 安装器打成功后即可删 |
| `dist/SecondBrain-Setup.exe` | 约 170 MB | 打安装器的中间产物 | 已 `stage_setup.py` 复制出带版本号的安装包后即可删 |
| `*.log`、`debug.log` | 几 MB | 调试时重定向的日志 | 随手删 |

`dist/SecondBrain` **本体留着** —— 下次 PyInstaller 能增量复用，比全量重打快几分钟。

> ⚠️ Windows 上 `rm -rf` 可能被资源管理器或安全软件占着 DLL 而删不掉，
> 而且**退出码可能仍是 0**。删完务必重新 `ls` 确认，别信 `&&` 链。
> 同理，`stage_app.py` 的备份轮转以前用 `ignore_errors=True`，删失败也不吭声，
> 攒到 5 份 / 1.6 GB 才发现；现在它会打印 `[!]` 列出没删掉的目录。
