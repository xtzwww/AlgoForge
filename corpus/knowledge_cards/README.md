# 算法知识卡片语料

阶段 8（`AGENTS.md` S5）的检索语料。**当前还没有代码读它**——索引器在
`sidecar/app/rag/indexer.py`（尚未实现）。本文件定义格式，供索引器与后续维护者对齐。

## 为什么是 Markdown 而不是纯 YAML

`VIBECODING.md` P2-15 的验收要求是「每个卡片有标题、知识点标签、正文」——
**正文是要被切分、嵌入、检索的自然语言**，而 YAML 的多行字符串写起来别扭、
读起来也不像给人看的。所以采用 **YAML front-matter + Markdown 正文**：

- front-matter 提供机器可读的字段（过滤、关联、统计）；
- 正文是纯 Markdown，可直接交给 TextSplitter 切分。

题库（`corpus/problems/`）用纯 YAML 是对的——那是结构化数据，没有正文。
**两者格式不同是有意的**，不要为了"统一"把卡片改成 YAML。

## 卡片格式

```markdown
---
id: binary-search                     # 唯一标识，kebab-case，与文件名一致
title: 二分查找与数值逼近               # 中文标题，面向学生
knowledge_tags: [二分, 数组, 数值计算]   # 必须取自题库已用的知识点标签
related_problems: [p011-binary-search] # 题库里的真实题号（含前缀）
common_errors: [off_by_one, boundary_condition]  # 取自 12 个错因标签
---

# 标题

## 什么时候会用到
## 要点
## 常见错误
## 自检清单
```

### 字段约束

| 字段 | 约束 |
|---|---|
| `id` | kebab-case，与文件名（去 `.md`）一致 |
| `title` | 非空中文标题 |
| `knowledge_tags` | 每项必须出现在 `corpus/problems/*.yaml` 的 `knowledge_tags` 里 |
| `related_problems` | 每项必须是题库里真实的 `id` |
| `common_errors` | 每项必须是 `app/eval_cases.py::ROOT_CAUSE_LABELS` 里的 12 个之一 |
| 正文 | 至少含一个 `##` 小节 |

### 为什么 `common_errors` 必须取自那 12 个标签

这 12 个标签是**闭集**（`ROOT_CAUSE_LABELS`），Agent 的输出与评估集打分都锚在它上面。
卡片里写别的标签名，检索出来会污染 Agent 的上下文，让它产出无法被打分的错因。
`tools/validate_knowledge_cards.py` 会拦下这类错误。

## 校验

```bash
python tools/validate_knowledge_cards.py
```

检查：front-matter 字段齐全、`id` 与文件名一致、题号真实存在、
错因标签在闭集内、正文非空，并报告 32 个知识点标签的覆盖情况。
