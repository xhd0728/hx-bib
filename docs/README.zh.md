# hx-bib 中文文档

hx-bib 是一个 BibTeX 整理工具箱：**格式化**、**清理**、**会议期刊归一化**、**格式校验**与**多源引用核查**（检测 AI 生成/凭空捏造的虚假引用）。提供 **CLI**（Typer + Rich）与 **GUI**（NiceGUI 本地网页）两种入口，界面支持 中文 / English 切换。

[English README](../README.md)

## 目录

- [安装](#安装)
- [快速上手](#快速上手)
- [命令行（CLI）](#命令行cli)
  - [format 格式化](#format-格式化)
  - [clean 清理](#clean-清理)
  - [normalize 归一化](#normalize-归一化)
  - [check 格式校验](#check-格式校验)
  - [verify 引用核查](#verify-引用核查)
  - [venues 会议期刊预设](#venues-会议期刊预设)
  - [table 表格格式化](#table-表格格式化)
  - [gui 图形界面](#gui-图形界面)
- [修改预览：逐项对照 + 红绿 Diff](#修改预览逐项对照--红绿-diff)
- [会议期刊分组规则](#会议期刊分组规则)
- [引用核查的判定等级](#引用核查的判定等级)
- [开发与测试](#开发与测试)
- [License](#license)

## 安装

需要 Python 3.9 及以上版本。

```bash
git clone <this repo> && cd citation-refactor
pip install -e .          # 安装后可直接使用 hx-bib 命令
```

不安装也可以运行：在仓库根目录执行 `PYTHONPATH=src python -m hx_bib ...`。

常用示例文件：`examples/custom.bib`（约 50 条真实/虚构混合的参考文献，适合体验全部功能）。

## 快速上手

```bash
hx-bib format  examples/custom.bib --indent 2 --sort key --dry-run   # 先预览
hx-bib clean   examples/custom.bib -r abstract --drop-others --months --doi --pages --clean-urls
hx-bib normalize examples/custom.bib --venue-style family
hx-bib check   examples/custom.bib
hx-bib verify  examples/custom.bib --workers 4 --html report.html
hx-bib table   paper.tex --dry-run   # LaTeX 表格格式化（先预览）
hx-bib gui     # 打开浏览器界面
```

所有会写文件的命令（`format` / `clean` / `normalize` / `table`）默认**原地修改**，并统一支持：

| 选项 | 作用 |
| --- | --- |
| `-n, --dry-run` | 只输出预览（逐项对照 + 红绿 Diff），不写文件 |
| `--stdout` | 结果打印到标准输出 |
| `-o, --output PATH` | 写入指定文件而不是原文件 |

## 命令行（CLI）

### format 格式化

统一缩进、字段顺序与条目排序，输出规范整洁的 bib。

```bash
hx-bib format refs.bib -i 2 --field-order standard --sort key
```

| 选项 | 取值 | 说明 |
| --- | --- | --- |
| `-i, --indent` | `2` / `4` / `tab` / `align` / `0` | 空格数、制表符，或对齐到左花括号下方 |
| `--align-equals` | 开关 | 等号纵向对齐 |
| `-f, --field-order` | `standard` / `alphabetical` / `keep` | 标准字段顺序 / 字母顺序 / 保持原样 |
| `-s, --sort` | `none` / `key` / `year` / `type` / `title` | 条目排序方式 |
| `--trailing-comma` | 开关 | 最后一个字段也保留逗号 |
| `--drop-comments` | 开关 | 删除 `@comment` 注释 |

多行折行的字段值（例如逐行书写的作者列表）会被折叠为一行——这是 BibTeX 推荐写法，diff 中也能看得更清楚。

### clean 清理

删除/保留/增加/重命名字段，处理 `and others`、月份宏、重复 key 等。

```bash
hx-bib clean refs.bib -r abstract -r keywords --drop-others --title-period --months --dedupe
```

| 选项 | 说明 |
| --- | --- |
| `-r, --remove FIELD` | 删除字段（可重复） |
| `-k, --keep FIELD` | 只保留这些字段（白名单，可重复） |
| `-a, --add field=value` | 增加字段（可重复）；配合 `--overwrite` 覆盖已有值 |
| `--rename old=new` | 重命名字段（可重复） |
| `--drop-empty` | 删除值为空的字段 |
| `--drop-others` | 去掉作者/编者列表末尾的 `and others` |
| `--months` | 月份规范为 `jan … dec` 宏形式 |
| `--title-period` | 去掉标题末尾的句号（保留 `?` `!`） |
| `--dedupe` | 删除重复 cite key；`--keep-last` 改为保留最后一条 |
| `--doi` | 统一 DOI（去掉 `https://doi.org/`、`DOI:` 前缀） |
| `--pages` | 页码统一为 `12--34` 双连字符形式（兼容 `-`、`–`） |
| `--clean-urls` | 清除 URL 跟踪参数（`utm_*`、`fbclid` 等），保留其他查询参数 |
| `--dedupe-title` | 按标题去重（忽略大小写与结尾标点），配合 `--keep-last` 选择保留哪条 |

### normalize 归一化

基于内置预设统一会议/期刊写法，并规范 arXiv 条目（思路借鉴 rebiber）。

```bash
hx-bib normalize refs.bib --venue-style family --arxiv-style journal
```

| 选项 | 取值 | 说明 |
| --- | --- | --- |
| `--venue-style` | `abbr` / `full` / `family` / `keep` | `abbr` → `Proceedings of EMNLP`；`full` → 官方全称；`family` → 归组（`ARR` / `ACM` …） |
| `--arxiv-style` | `journal` / `misc` / `keep` | 期刊形式（`arXiv preprint arXiv:2411.13154`，`@article`）或 `@misc` + `eprint` 形式 |
| `--fix-types` | 开关（默认开） | 修正 `@article` ↔ `@inproceedings` 类型错误（按 booktitle/journal 字段判断） |
| `--drop-arxiv-volume` | 开关（默认开） | 清理 arXiv 伪卷号（`abs/2411.13154`） |
| `--add-arxiv-url` | 开关（默认开） | 缺少 `url` 时补充 `https://arxiv.org/abs/…` |

能识别 `ofKDD`（缺空格）、`Findings of …` 等常见不规范写法；期刊出现在 `booktitle`、会议出现在 `journal` 时会自动移到正确字段并联动修正条目类型。

### check 格式校验

离线（不联网）结构校验：

- 必需字段（按条目类型）、空必需字段；
- 年份形如 `2020` 且在合理范围内，页码 `start--end` 形式，花括号配对；
- 重复 cite key、解析告警；
- 占位 venue（`TBD` / `TODO` …）与非预设 venue；
- 风格陷阱：`and others`、英文全称月份、标题末尾句号、大小写 cite key、url 缺协议等；
- arXiv 条目的小建议（补 `eprint` / `archiveprefix`、清理伪卷号）。

```bash
hx-bib check refs.bib             # 全部问题（按严重级别排序）
hx-bib check refs.bib --show issues   # 只显示 error/warning，隐藏建议
hx-bib check refs.bib --strict    # warning 也视为失败（退出码非 0）
hx-bib check refs.bib --json out.json
```

| 选项 | 说明 |
| --- | --- |
| `--json PATH` | 同时输出 JSON 报告 |
| `--strict` | warning 也算失败 |
| `--show` | `all`（默认）/ `issues`（隐藏建议） |

存在 `error` 时退出码为 1，适合接入 CI。

### verify 引用核查

联网逐条核对标题 / 作者 / 年份是否与 **arXiv、Crossref、DBLP、OpenReview** 中的真实记录一致，用于发现 AI 生成的虚假引用。

```bash
hx-bib verify refs.bib --html report.html --json report.json
hx-bib verify refs.bib --workers 4                        # 4 线程并发
hx-bib verify refs.bib --only smith2020 --only lee2021   # 只查指定条目
```

| 选项 | 说明 |
| --- | --- |
| `--html PATH` / `--json PATH` | 输出可筛选的 HTML 报告 / 机器可读 JSON |
| `--only KEY` | 只核查指定 cite key（可重复） |
| `--workers, -j N` | 并发线程数（1–8，默认 1）；不同网站同时查询，同一网站仍保持礼貌间隔 |
| `--delay SECONDS` | 请求间隔（礼貌抓取，默认 0.8s） |
| `--no-cache` | 忽略本地 HTTP 缓存 |
| `--verbose` | 打印每个 HTTP 请求 |
| `--retries N` | 对限流/瞬时失败条目追加重试轮数（默认 1） |

查询顺序与策略：`doi` 可解析则先用 Crossref/doi.org；有 arXiv id 则批量预取 arXiv；否则按 Crossref 检索、DBLP 检索依次尝试，OpenReview 用于带 `openreview.net` 链接的条目。每个来源的结论分三种状态：**找到**、**明确不存在**、**暂时性错误**（网络问题，不算作“查无此文”）。

**arXiv 录用检测**：arXiv 条目会额外检查 arXiv 元数据中的 `journal_ref` 与 `comment` 字段（如 "accepted at …"、"to appear"、"camera-ready"），仅含 arXiv 信息的条目还会向 DBLP 探测一次正式出版记录——发现录用信号时在结果中提示正式版本信息，方便把 `arXiv preprint` 引用升级为正式引用。

结果按难度分级缓存到本地（Windows：`%LOCALAPPDATA%\hx-bib\http-cache.json`；其他平台：`~/.cache/hx-bib/`），重复运行会非常快。存在 `FAIL` 条目时退出码为 1。

### venues 会议期刊预设

```bash
hx-bib venues list                  # 全部预设
hx-bib venues list --family ACM     # 只看某个分组
hx-bib venues search retrieval      # 搜索
```

### table 表格格式化

```bash
hx-bib table paper.tex --dry-run              # 先预览逐行 Diff
hx-bib table paper.tex                        # 原地写回
hx-bib table paper.tex --amp align -i 2       # 按列对齐 + 行缩进 2 空格
cat paper.tex | hx-bib table - --stdout       # 从 stdin 读取，输出到 stdout
```

只处理 `tabular` / `tabular*` / `tabularx` / `tabulary` / `longtable` / `array` 环境的**内部**，环境之外（`\begin{table}`、`\caption` 等）原样保留：

| 选项 | 取值 | 说明 |
| --- | --- | --- |
| `--amp` | `space`（默认）/ `align` / `none` | `a & b` 单空格围绕 `&`；`align` 按列补空格对齐；`none` 紧凑的 `a&b` |
| `--blank-lines / --no-blank-lines` | 开关（默认开） | `\toprule` / `\midrule` / `\hline` 等规则命令前后空一行（连续规则成组，`\begin` 之后 / `\end` 之前不加空行） |
| `--indent, -i N` | 非负整数（默认 0） | 每个格式化行额外缩进 N 个空格 |

规范要点：**一行只放一个数据行**（跨行书写的单元格会被合并）；每条规则命令**独立成行**；`\multicolumn` 的跨列信息保留，列对齐只按普通单元格计算宽度。

安全性：分词器只在**花括号外、嵌套环境外**（如 `aligned`、`\makecell{a \\ b}`）才把 `&` / `\\` 当作列 / 行分隔；`\&`、`\%` 等转义字符与数学模式内容不受影响；表格内的 `%` 注释会被删除并给出提示（独立注释行保留）；未闭合的环境、缺 `\\` 的行等异常输入只产生警告，不会崩溃。测试会对每张格式化后的表重新解析，断言单元格 / 规则结构保持不变（含幂等性）。

### gui 图形界面

```bash
hx-bib gui               # 默认 http://127.0.0.1:8080，自动打开浏览器
hx-bib gui --port 9000 --no-show
```

上传页面支持**拖拽/选择文件**或**直接输入本地路径**；不加载文件也可以先**浏览会议期刊预设**。加载后进入工作台：每个选项卡是一类功能——**格式化 / 清理 / 归一化 / 格式校验 / 引用核查 / 会议期刊预设**。顶部 `中 / EN` 按钮随时切换界面语言，所有面板共享同一个工作副本。

#### 工作副本与暂存 / 撤销 / 重做

所有修改都作用于同一个**工作副本**，而不是各自的临时结果：

1. 每个操作选项卡的流程一致：设置选项 → **生成修改预览** → 查看三种视图 → **应用到工作副本**；
2. 每次“应用”都会作为**一个暂存步骤**记入历史（状态栏显示“已暂存 n 步”，并提示相对原始文件有多少条发生变化）；
3. 随时可以**撤销 / 重做**任意已应用步骤，或一键**还原原始文件**（都会弹确认框，不会改动磁盘上的文件）；
4. 格式化后再去清理、归一化，前一步的改动都会继承——不再需要来回导出导入。

#### 复制与导出

- 状态栏的**复制全部**一键复制当前工作副本全文，**下载 .bib** 下载结果；
- 每个预览都提供**复制结果**；
- 「逐条查看」视图中，每条结果独立成卡，各自带**复制此条**按钮。

#### 修改预览的三种视图

每个预览面板都可以切换三种视图（详见下一节）：**逐项对照 / 左右 Diff / 逐条查看**，Diff 视图还可以切换**仅看改动 / 查看全部**。

#### 引用核查

输入可选的 key 列表，设置**并发线程数**与请求间隔后开始核查：进度条实时显示，可随时**暂停 / 继续 / 停止**（停止后保留已完成的结果），本地缓存可开关，并会检测 arXiv 是否已被录用。结果按判定等级着色，可下载 HTML 报告。

#### 其他

- **高级清理**：DOI 统一、页码统一、URL 跟踪参数清除、按标题去重等开关位于「清理」选项卡的“高级清理”折叠区；
- **会议期刊预设**：可搜索 / 按分组筛选，每条预设给出缩写 / 全称 / 归组三种写法，均可逐条复制；
- **LaTeX 表格格式化页**：页头的「LaTeX 表格 →」进入独立页面——粘贴整篇 .tex 或表格片段，选择 `&` 风格 / 规则空行 / 行缩进后一键格式化，提供逐表红绿 Diff、提示列表和复制 / 下载 .tex；
- **设置本地保存**：语言与所有面板选项自动保存在浏览器 `localStorage`，下次打开自动恢复；
- 界面已通过 Lighthouse 审计（无障碍 / 最佳实践 / SEO 均为满分），主色与对比度满足 WCAG AA。

## 修改预览：逐项对照 + 红绿 Diff + 逐条查看

CLI 的 `--dry-run` 与 GUI 的每个预览都包含同样的变更信息，GUI 提供三种视图：

1. **逐项对照列表**：每条受影响条目一行，标注变化类型（新增/删除/修改），以及涉及的字段徽标：`+eprint`（新增）、`−volume`（删除）、`~booktitle`（修改）；条目类型变化显示为 `@type inproceedings → article`。
2. **左右对照 Diff**：类似 GitHub 的红绿分栏——左侧“修改前”用红色高亮删除/旧行，右侧“修改后”用绿色高亮新增/新行，并显示两侧行号。
3. **逐条查看**：结果中每条 bib 条目独立成卡（cite key、类型、标题 + 完整文本），每张卡带自己的**复制此条**按钮，适合只取用其中几条。

GUI 中可在“仅看改动”和“查看全部”之间切换（CLI 加 `--dry-run` 时也会先打印条目级汇总）。

## 会议期刊分组规则

预设覆盖常见计算机与人工智能会议/期刊，按用户规则分组：

| 分组 | 包含 |
| --- | --- |
| **ARR** | ACL、EMNLP、NAACL、EACL、AACL、COLING、Findings of …、TACL、CL |
| **ACM** | KDD、SIGIR、MM、WWW、CIKM、WSDM、RecSys、CHI、SIGMOD、CCS、TOIS、TKDD |
| **PMLR** | ICML、AISTATS、UAI |
| 其他 | NeurIPS、ICLR、AAAI、IJCAI、IEEE（CVPR/ICCV/TPAMI/TKDE/TNNLS…）、ECCV（Springer）、JMLR、TMLR |

`--venue-style family` 时上述归组直接作为 booktitle/journal 输出（如 EMNLP → `ARR`、KDD → `ACM`）。

## 引用核查的判定等级

| 等级 | 含义 |
| --- | --- |
| `OK` | 标题、作者、年份均与来源记录一致 |
| `INFO` | 无法核实（各来源均无覆盖，或对应来源暂时不可达） |
| `MINOR` | 小差异（如标题标点/拼写差异、作者大小写、预印本与正式版年份差） |
| `WARN` | 明显差异（标题相似度偏低、作者大量对不上、venue 与记录不符） |
| `FAIL` | 疑似伪造（各可靠来源明确查无此文，或 DOI/arXiv id 不存在） |

作者对照会把 bib 中的作者列表与来源记录逐名比对（容忍 `and others` 截断、姓名缩写差异），HTML 报告中以红色标出对不上的名字；带 `and others` 截断的作者列表会单独给出提示。

## 开发与测试

```bash
pip install -e ".[dev]"

pytest                                    # 单元 + 集成测试（152 项）
PYTHONPATH=src python tests/gui_smoke.py  # GUI 无头冒烟测试（NiceGUI user simulation）
```

前端使用 Lighthouse（headless Chrome）审计，无障碍 / 最佳实践 / SEO 均为 100，性能 99；如需复现：启动 `hx-bib gui --no-show` 后对页面跑 `npx lighthouse http://127.0.0.1:8080/`。

项目结构：

```
src/hx_bib/
├── cli.py              # Typer 命令行入口
├── core/               # parser / writer / diff / cleaner / venues / normalize / validator
├── verify/             # 多源核查：net / sources / checker / report
└── gui/                # NiceGUI 界面（app.py + i18n.py）
```

## License

MIT
