# rag-ladder ROADMAP

| 文件 | 内容 |
|---|---|
| [`search/plan-summary.md`](search/plan-summary.md) | **总方案**：语料抽样、切分、成本与额度、排期、风险、仓库结构 |
| [`search/milestones.md`](search/milestones.md) | **M1–M6 规格**：论文锚点、实现要点、成本、预期结论 |
| [`search/evaluation.md`](search/evaluation.md) | **评测方案**：三层架构、150 题规范、指标定义、公开集清单 |

## 三步走

1. **W1**：`prepare_corpus.py --size 100` + 切分 + M1 + 评测脚手架（30 题）
2. **W2**：M2（三档消融）→ M3 RAPTOR → M4 GraphRAG（500 篇）
3. **W3**：M5 CRAG → M6 Agentic → 150 题全量 → `results/leaderboard.md`

预算：LLM ≈ $28–35（有 $60），硅基流动 ≈ ¥9（有 ¥16）。
