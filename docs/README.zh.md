# hx-bib 使用说明

[English](../README.md)

## 安装

需要 Python 3.11+。在项目目录执行：

```bash
python -m pip install -e .
```

## 图形界面

```bash
hx-bib gui
# 指定端口，不自动打开浏览器：
hx-bib gui --port 9000 --no-show
```

默认访问 [localhost:8080](http://127.0.0.1:8080)。

1. 选择 `.bib` 文件、粘贴内容、填写本地路径，或点击「试用示例」。
2. 左侧直接选择所需工具。整理、清理、归一化和格式校验会随设置自动更新；联网查询请点击「联网核查」或「arXiv 录用核查」。
3. 在右侧看修改前后的代码。可展开「逐条对照与复制」复制单条，也可应用、撤销或重做后导出工作副本。
4. 「文件操作」可查看历史、还原；通过本地路径打开的文件可另选「保存到文件」。arXiv 核查会列出证据，未找到证据不等于未录用。

顶部切换到「LaTeX 表格」后，粘贴 LaTeX 或导入 `.tex` → 格式化 → 对照、复制或下载。缩进和规则前后空行可在「高级设置」中调整。

## 命令行

写入命令默认原地修改文件。建议先加 `--dry-run` 预览，或用 `-o` 另存。

```bash
hx-bib format refs.bib --indent 2 --sort key --dry-run
hx-bib clean refs.bib -r abstract -r keywords --doi --pages -o cleaned.bib
hx-bib normalize refs.bib --venue-style abbr --arxiv-style journal --dry-run
hx-bib check refs.bib --strict
hx-bib verify refs.bib --workers 4 --html report.html --json report.json
hx-bib venues list --family ACM
hx-bib venues search retrieval
hx-bib table paper.tex --amp align --indent 2 --dry-run
```

`format`、`clean`、`normalize` 和 `table` 通用选项：

| 选项 | 用法 |
| --- | --- |
| `-n`、`--dry-run` | 只预览，不写文件 |
| `-o PATH`、`--output PATH` | 结果写入其他文件 |
| `--stdout` | 输出结果文本 |

运行 `hx-bib 命令 --help` 查看完整选项，也可用 `python -m hx_bib` 替代 `hx-bib`。
