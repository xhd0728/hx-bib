"""User-facing descriptions and next steps for offline validation issues."""

from .i18n import t

_ZH_MESSAGES = {
    "duplicate_key": "同一个引用 key 出现了多次",
    "parse": "BibTeX 语法不完整或无法正确解析",
    "empty_entry": "条目中没有字段内容",
    "missing_required": "缺少必需字段「{field}」",
    "empty_required": "必需字段「{field}」的内容为空",
    "book_author": "书籍条目缺少作者 author 或编者 editor",
    "empty_field": "字段「{field}」的内容为空",
    "braces": "字段「{field}」中的花括号不匹配",
    "year": "年份 year 应为四位数字",
    "year_range": "年份超出了常见出版年份范围，请核对",
    "pages": "页码格式需要检查",
    "others": "作者列表以 and others 结尾，可能未列出全部作者",
    "month": "月份可以统一为 jan…dec 形式",
    "title_period": "标题末尾包含句号，可以统一去除",
    "url": "网址 url 缺少 http:// 或 https:// 前缀",
    "placeholder": "字段「{field}」仍是待填写的占位内容",
    "unknown_venue": "会议或期刊未收录在内置预设中，不代表引用有误",
    "article_booktitle": "期刊类型 article 使用了会议字段 booktitle，请核对条目类型",
    "conference_journal": "会议类型使用了期刊字段 journal，请核对条目类型",
    "arxiv_eprint": "识别到了 arXiv 编号，但缺少 eprint 字段",
    "arxiv_prefix": "已有 eprint，但缺少 archiveprefix = {arXiv}",
    "arxiv_volume": "arXiv 预印本使用了伪卷号 volume",
    "misc_location": "misc 条目缺少可定位来源的网址、DOI 或说明",
    "key_space": "引用 key 中包含空白字符",
    "key_case": "引用 key 含大写字母，可以考虑统一风格",
}

_HINT_GROUP = {
    "duplicate_key": "duplicates", "parse": "syntax", "braces": "syntax",
    "missing_required": "required", "empty_required": "required", "book_author": "required",
    "empty_entry": "required", "empty_field": "empty", "year": "year", "year_range": "year",
    "pages": "pages", "others": "others", "month": "clean", "title_period": "clean",
    "url": "source", "misc_location": "source", "placeholder": "venue",
    "unknown_venue": "preset", "article_booktitle": "normalize", "conference_journal": "normalize",
    "arxiv_eprint": "normalize", "arxiv_prefix": "normalize", "arxiv_volume": "normalize",
    "key_space": "key", "key_case": "key",
}

_HINTS = {
    "duplicates": ("先对照重复条目的内容，再到「清理」选择去重及保留哪一条。",
                   "Compare the duplicate entries, then use Clean to choose which occurrence to keep."),
    "syntax": ("核对原文的花括号、引号和逗号，修复后重新导入并校验。",
               "Check braces, quotes and commas in the source, then import and validate again."),
    "required": ("对照文献的正式出版信息补全字段，再重新导入并校验。",
                 "Complete the fields using the publication record, then import and validate again."),
    "empty": ("补全字段内容；如果该字段不需要，可到「清理」删除空字段。",
              "Fill in the value, or remove unused empty fields with Clean."),
    "year": ("对照正式出版记录核对年份；预印本与正式版本的年份可能不同。",
             "Check the publication year; preprints and final versions can have different years."),
    "pages": ("先核对页码范围，可到「清理」统一为 12--34 形式。",
              "Check the page range, then use Clean to normalize it to 12--34."),
    "others": ("如果需要完整作者名单，请对照正式记录补全；仅去掉 and others 可在「清理」完成。",
               "Check the publication for the complete author list; Clean can remove the and others suffix."),
    "clean": ("到「清理」选择对应的月份或标题选项，预览后再应用。",
              "Choose the matching month or title option in Clean, then preview and apply."),
    "source": ("补全正确的网址、DOI 或出版说明；文献是否真实存在需使用「引用核查」。",
               "Add a valid URL, DOI or publication note. Use Verify to check the citation online."),
    "venue": ("填写真实的会议或期刊名称，可在「会议期刊预设」查找标准写法。",
              "Enter the actual venue; Venues lists supported names and abbreviations."),
    "preset": ("预设无法覆盖所有刊物。确认名称正确即可保留，也可以到「会议期刊预设」查找。",
               "The preset list is not exhaustive. Keep the name if correct, or look it up in Venues."),
    "normalize": ("到「归一化」核对会议、期刊或 arXiv 选项，预览后再应用。",
                  "Review venue and arXiv options in Normalize, then preview and apply."),
    "key": ("修改 key 时，请同步修改论文中的引用命令，避免引用失效。",
            "If you rename a key, update the matching citation commands in your paper as well."),
}


def issue_text(issue, lang: str) -> tuple[str, str]:
    code = getattr(issue, "code", "")
    message = issue.message
    if lang == "zh" and code in _ZH_MESSAGES:
        # Replace only the field token: BibTeX snippets such as {arXiv} stay literal.
        message = _ZH_MESSAGES[code].replace("{field}", issue.field_name or "")
    group = _HINT_GROUP.get(code)
    hint = _HINTS[group][0 if lang == "zh" else 1] if group else t(lang, "sev_" + issue.severity + "_hint")
    return message, hint
