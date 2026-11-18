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
# 在终端输出服务启动、请求、缓存和异常的详细日志：
hx-bib gui --debug
```

默认访问 [localhost:8080](http://127.0.0.1:8080)。

1. 选择 `.bib` 文件、粘贴内容、填写本地路径，或点击「试用示例」。
2. 在左侧展开所需设置，开启「纳入应用」，再点「应用」。这次修改可以一键撤销。
3. 右侧按条目显示修改前后。用「仅显示差异 / 显示全部」切换范围，也可以复制单条。顶部可复制或下载工作副本，「文件操作」中可保存从本地路径打开的文件。
4. 「arXiv 录用核查」和「联网核查」单独运行。arXiv 核查遇到错误或超时会再试两次，可查看记录并重试失败条目。

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
