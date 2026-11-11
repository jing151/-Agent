from __future__ import annotations

import logging
import re

import jieba

jieba.setLogLevel(logging.ERROR)

STOPWORDS = set("公司 员工 的 了 是 在 和 与 应 可以 请 问 如何 怎么 什么 哪里 哪个 多少 几 那 也 具体 目前 需要 进行 通过 有 吗 呢 上 后 之前 最 一个 是否 提交 应该 出现 操作 支持 企业 服务 申请 完成 设置 入口".split())
# Small, inspectable business vocabulary. These expand retrieval, never generate answers.
SYNONYM_GROUPS = (
    ("报销", "费用报销", "报销申请", "交票", "垫付", "交发票"),
    ("期限", "时限", "最晚", "最迟", "几天内", "什么时候", "多久"),
    ("密码重置", "重置密码", "找回密码", "忘记密码", "修改密码", "密码忘了"),
    ("移动端", "手机", "手机端", "企业微信", "掌上"),
    ("设备", "电脑", "笔记本", "办公设备"),
    ("报修", "故障", "坏了", "维修", "修理", "不能开机"),
    ("年假", "休假", "带薪假"),
)


def tokenize(text: str) -> list[str]:
    return [word for word in jieba.lcut(text.lower())
            if word.strip() and word not in STOPWORDS and re.search(r"[\w\u4e00-\u9fff]", word)]


def expand_query(text: str) -> str:
    additions = [word for group in SYNONYM_GROUPS if any(word in text for word in group) for word in group]
    return text + " " + " ".join(dict.fromkeys(additions))


def char_ngrams(text: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    # Remove generic workflow expressions before character matching as well as BM25.
    for word in sorted((word for word in STOPWORDS if len(word) > 1), key=lambda word: (-len(word), word)):
        text = text.replace(word, " ")
    clean = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())
    return {clean[i:i + n] for n in sizes for i in range(len(clean) - n + 1)}
