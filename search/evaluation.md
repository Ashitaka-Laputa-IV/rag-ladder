# 评测方案与公开数据集

> 结论：**评测方案有，公开集也有，但没有任何一个公开集能让 M1–M6 同台**。
> 所以采用三层结构：公开集做"外部锚点"，自建集做"内部对比"。

---

## 1. 三层评测架构

```
L3  自建 150 题（同语料）      → 六范式同台对比   ← 主榜
L2  公开端到端集（子集）        → 与外部工作可比   ← 附录
L1  公开检索集（C-MTEB/BEIR）  → 只评检索层       ← 附录
```

| 层 | 评什么 | 能否跨范式 | 成本 |
|---|---|---|---|
| L1 | 检索质量（只到检索为止） | ✅ 但只覆盖 M1/M2/M3/M4 的检索部分 | 极低（无 LLM，只算 nDCG） |
| L2 | 端到端答案质量 | ⚠️ 需要把语料换成公开集，且六个范式都要重索引 | 中（judge 调用） |
| L3 | 六种能力全覆盖 | ✅ 唯一可行 | 低（150 题 × 6 范式） |

**为什么 L3 必须是主榜**：L1 测不到生成与弃答；L2 换语料后，M4 的索引成本重来一遍，且公开集里没有"全局综述"题。

---

## 2. L3：自建集（主榜）

### 150 题构成

| 题型 | 题数 | 出题方式 | 考察里程碑 |
|---|---|---|---|
| 单跳事实 | 40 | RAGAS `TestsetGenerator` 合成 + 筛 | M1–M6 全部 |
| 专名/编号/代码符号 | 25 | 合成 + 人工挑（型号、人名、数字） | M2 的 BM25 分支 |
| 多跳（2–3 跳） | 30 | 人工写 | M6 / M4 |
| 全局综述 | 30 | 人工写，必须跨多篇文档 | M3 / M4 |
| 无答案/应拒答 | 25 | 人工造（问语料里没有的事） | M5 核心 |

合成只解决 43%，其余必须人写（半天到一天）。

### 指标

| 指标 | 定义 | 来源 | 说明 |
|---|---|---|---|
| faithfulness | 答案是否被给定上下文支持 | RAGAS | 0–1 |
| answer_correctness | 与 gold 的一致性 | RAGAS（LLM judge） | 只报相对胜率 |
| **evidence_recall** | gold chunk 是否进入最终上下文 | 自写 | **替代 recall@k**，兼容跨层/多轮 |
| citation_precision | 引用中命中的比例 | 自写 | 只对"给了引用"的范式有意义 |
| abstain_accuracy | 无答案题正确拒答率 | 自写 | τ 统一标定 |
| cost | tokens / LLM 调用数 / P95 延迟 / ¥ | 自写 | 必须与准确率同表 |

### Judge 协议

- 同一 judge 模型（DeepSeek V4.1 Flash）、`temperature=0`、同一 prompt
- **A/B 顺序对调各跑一次**，只报一致部分
- 不报绝对分；报"胜/平/负"与置信区间
- 已知偏差：self-preference（生成与裁判同模型）+ 位置偏差（KDD'26 实测显著）

### 呈现

| 题型 | M1 | M2 | M3 | M4 | M5 | M6 |
|---|---|---|---|---|---|---|
| 单跳 40 | | | | | | |
| 专名 25 | | | | | | |
| 多跳 30 | | | | | | |
| 全局 30 | N/A | N/A | | | N/A | |
| 无答案 25 | 套τ | 套τ | 套τ | 套τ | | |

`N/A` 不记 0 分。另附成本表（每行一个范式：tokens/查询、调用数、P95、¥）。

---

## 3. 公开集清单（实测/已核实）

### 3.1 端到端 RAG 评测（L2 候选）

| 数据集 | 规模 | 语言 | 许可 | 自带指标/标签 | 适合谁 |
|---|---|---|---|---|---|
| **RAGBench** `galileo-ai/ragbench` | 95,381 行 / 0.45 GB / 12 子集 | 英 | **CC BY 4.0** | TRACe：Utilization / Relevance / Adherence / Completeness，含 reference answer | M1/M2/M5 端到端，**最省事**（不用出题） |
| **CRAG** `facebookresearch/CRAG` | 4,409 QA / 5 领域 | 英 | 研究（镜像标 CC BY-NC） | perfect/acceptable/missing/incorrect，**含"该拒答"样本** | M5 弃答、M6 时效 |
| **RGB** `chen700564/RGB` | 600 题 × 中/英，各 4 套（基础/噪声/整合/反事实） | 中英 | CC BY-NC-SA | 噪声鲁棒、拒答、信息整合、反事实 | **M5/M6 中文首选** |
| **CRUD-RAG** `IAAR-Shanghai/CRUD_RAG` | 80k 新闻 + 4 类任务 | 中 | 代码 Apache-2.0 | 问答/摘要/续写/幻觉检测 | 中文端到端补充 |
| **MultiHop-RAG** `yixuantt/MultiHopRAG` | 609 篇 / 2,556 query / 12 MB | 英 | ODC-BY | 多跳标注（证据跨 2–4 篇） | **M6 vs M1 多跳交叉验证** |
| RAGAS 示例 `explodinggradients/amnesty_qa` | 20 条 | 英 | Apache-2.0 | 四件套（q/gt/ctx/ans） | 只用于调试指标实现 |

### 3.2 检索评测（L1）

| 数据集 | corpus | queries | 语言 | 用途 |
|---|---|---|---|---|
| `C-MTEB/T2Retrieval` | 118,605 | 22,812 | 中 | 中文检索 nDCG |
| `C-MTEB/DuRetrieval` | 100,001 | 2,000 | 中 | 中文检索 |
| `C-MTEB/MMarcoRetrieval` | 106,813 | 6,980 | 中 | 中文检索 |
| `BeIR/scifact` + `-qrels` | 5,183 | 1,109 | 英 | 小规模、跑得快 |
| `BeIR/nfcorpus` + `-qrels` | 3,633 | 323 | 英 | 最小，适合冒烟测试 |

⚠️ **qrels 在单独的 `BeIR/<name>-qrels`**，不下一半算不出分。

### 3.3 覆盖矩阵（为什么没有全能公开集）

| 数据集 | 单跳 | 多跳 | 全局综述 | 弃答 | 中文 |
|---|---|---|---|---|---|
| RAGBench | ✅ | ⚠️ | ❌ | ⚠️ | ❌ |
| CRAG | ✅ | ⚠️ | ⚠️ | ✅ | ❌ |
| RGB | ✅ | ⚠️ | ❌ | ✅ | ✅ |
| MultiHop-RAG | ✅ | ✅ | ⚠️ | ❌ | ❌ |
| CRUD-RAG | ✅ | ❌ | ⚠️ | ❌ | ✅ |
| BEIR / C-MTEB | ✅ | ❌ | ❌ | ❌ | 部分 |

**没有一个同时覆盖"全局综述 + 弃答 + 中文"** —— 这就是必须自建 L3 的根本原因。

---

## 4. 执行顺序（按成本从低到高）

| 步骤 | 内容 | 时间 | 成本 |
|---|---|---|---|
| 1 | L1 冒烟：`BeIR/nfcorpus` 跑 M1 检索，验证管线通 | 10 分钟 | ¥0 |
| 2 | RAGAS 生成 65 道合成题（单跳 + 专名），人工筛到 40+25 | 半天 | ~$0.5 |
| 3 | 人工写 85 道（多跳/全局/无答案） | 1 天 | ¥0 |
| 4 | L3 主榜：150 × 6 范式，先 30 题跑通 | 1 天 | ~$1.8 |
| 5 | L1 正式：C-MTEB `T2Retrieval` 子集（1 万 corpus）评 M1/M2/M3/M4 的检索层 | 半天 | ¥0（embedding 免费） |
| 6 | L2 交叉：RAGBench `emanual` 500 条 + MultiHop-RAG 全量 | 1 天 | ~$3 |

L2 可选：如果时间紧，只做 MultiHop-RAG（它同时给多跳与"检索式 RAG 该赢还是该输"的对照）。

---

## 5. L2 落地细节（RAGBench 为例）

```python
from datasets import load_dataset
ds = load_dataset("galileo-ai/ragbench", "emanual", split="test")
# 字段：question, documents, response(gold), generated_response(待填), context
# 用法：把 documents 当语料建索引 → 六个范式各跑一遍 → 用 TRACe 指标打分
```

注意三点：
1. 换语料 = **六个范式都要重建索引**（M4 又是一笔 $），所以 L2 只选一个小子集（500–1,000 条）。
2. RAGBench 的 `documents` 已经是分段好的，会绕开你自己的切分 —— 这与"同一切分"原则冲突，所以 L2 只进附录，不进主榜。
3. 它的指标（TRACe）与 RAGAS 不完全等价，报告里要分表呈现，别混在一起算平均。

---

## 6. 交付物

```
eval/queries.jsonl              150 题（含 gold_doc_ids / gold_chunk_ids / type / should_abstain）
eval/run.py                     统一 runner：--method m1..m6 --set l3|l2|l1
results/leaderboard.md          主榜（题型 × 范式 + 成本表）
results/appendix-l1.md          检索层 nDCG 对比
results/appendix-l2.md          公开集交叉验证
```

`leaderboard.md` 由脚本自动追加，不手写 —— 这是"每个里程碑只加一行"的落地方式。
