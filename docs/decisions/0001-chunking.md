# 决策 0001：分块使用 `SentenceChunker`（原生 overlap）

日期：2026-11-19 · 状态：已决定

## 背景

实验需要把 200 篇中文维基条目切成 chunk，供六种 RAG 范式共用。硬性要求：

1. 按中文标点断句
2. 按 token 计长（`cl100k_base`）
3. 有重叠，避免答案被切在边界
4. **每个 chunk 能还原原文**：`text[start:end] == chunk.text`
5. 确定性、离线、Windows 可跑
6. 200 篇秒级完成

第 4 条最关键：后续 `gold_chunk_ids`、`evidence_recall`、引用准确率全依赖它。

## 候选与实测

| 方案 | 中文断句 | 重叠 | 偏移精确率 | 备注 |
|---|---|---|---|---|
| 自研（正则 + token 窗口 + 手动扩重叠） | ✅ | ✅ 固定 64 | 100% | 约 60 行，额度易算错 |
| chonkie `RecursiveChunker` | ✅ | ❌ 无参数 | 100% | 需外补 overlap，且 `OverlapRefinery` 会把偏移弄坏（13 个里 12 个对不上） |
| chonkie `TokenChunker` | ❌ 不切句 | ✅ 固定 64 | **66.6%** | tiktoken 边界切在 UTF-8 中间，365 个汉字变成 `�` |
| chonkie **`SentenceChunker`** | ✅ 传中文 delim 后句中切断 0% | ✅ 原生，句级回退 | **100%**（951/951） | 重叠中位 46 token、最大 63 |
| langchain `RecursiveCharacterTextSplitter` | ✅ | 29% 相邻对 | 99.45%（6 个 `-1`） | 断句与重叠二选一 |
| llama-index `SentenceSplitter` | 需自写中文断句 | 79% | 100% | 依赖 148MB，import 1.8s |
| `semchunk` | ❌ 58% | 100% | 100% | 不认中文标点 |
| `semantic-text-splitter` | ✅ | 31% | 100% | 重叠名不副实 |

`SentenceChunker` 实测（200 篇 / 341,239 字符 / 390,245 token）：

- 偏移精确 **951/951**
- token 上限 `max = 512`，**0 个超标**（不传中文 `delim` 时 2.8% 超标、最大 1,431）
- 速度 **1.21 ms/篇**，200 篇 0.24 秒
- 重叠：除每篇最后一块外均生效，中位 46 token、上限 63

## 决定

使用 `SentenceChunker`，**不额外自补固定 64 token 重叠**：

```python
SentenceChunker(
    tokenizer=tiktoken.get_encoding("cl100k_base"),
    chunk_size=512,
    chunk_overlap=64,
    delim=["。", "！", "？", "；", "\n"],
    include_delim="prev",
)
```

## 理由

1. 唯一同时满足"中文断句 + 硬 512 上限 + 100% 偏移 + 文本不损坏"的方案。
2. 自补固定 64 的价值只是一个数字：它的目的是"别把句子切在边界"，而句级回退**直接达成这个目的**，且对齐到句子。
3. 代码从约 60 行降到约 5 行；之前手写版本出过额度溢出（最大块 2,834 token）的 bug，原生实现没有这个失败面。
4. 六个范式共用同一份 chunk，重叠不恒定不影响对比公平性，只轻微影响绝对召回。

## 后果

- 报告中重叠只能写实测值（**中位 46 token、最大 63、约 9% 块大小**），不能写"固定 64"。
- 每篇最后一个 chunk 无重叠（无实际影响）。
- 512 token 中包含重叠，新增内容约 466 token；200 篇约 951 块。
- `tokenizer` 必须传 encoder：默认 `"character"` 会让 `chunk_size` 变成字符数。
- `delim` 必须显式传中文标点，否则退化为按段落切。

## 何时重新评估

- 检索评测显示明显的"边界丢句"问题（例如 evidence_recall 在特定题型上系统性偏低）
- 下游算法要求重叠必须恒定（届时再启用"按 token 二分前移起点"的自补方案）

## 参考数据

本语料中文换算：**1 字 ≈ 1.14 token**（341,239 字符 / 390,245 token）；500 token ≈ 437 字。嵌入模型 `bge-m3` 上限 8,192 token，512 token 占 6%，模型上限不构成约束。
