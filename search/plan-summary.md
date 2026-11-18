# rag-ladder 方案总结（语料 · 评测 · 成本 · 排期）

> 一句话：用一块中文维基语料、一份 150 题评测集、一个 `Pipeline` 接口，让 M1–M6 六个 RAG 范式同台对比。
> 里程碑细节见 `search/milestones.md`，评测见 `search/evaluation.md`。

---

## 1. 决策记录

| 决策 | 选择 | 否决的方案与原因 |
|---|---|---|
| 语料 | 中文维基 `pleisto/wikipedia-cn-20230720-filtered` | CAIL2018（答案不在文本里、短文本、无全局语义）；THUCNews（版权不清、2011 年前）；全量 254k（图索引 $4,900 / 2.8M 次调用，不可行） |
| 规模 | **开发 100 / 评测 2,000 / 极限 5,000 篇**（嵌套抽样，见 §2） | 10,000 篇一轮 $62 > 预算 |
| 切分 | chonkie RecursiveChunker，512 token / overlap 64，中文标点 | 换分块 = 索引全废，开发期冻结 |
| Embedding | `BAAI/bge-m3`（免费，1024 维） | Qwen3-Embedding-8B 只用于一次对比消融 |
| Rerank | `BAAI/bge-reranker-v2-m3`（免费） | Qwen3-Reranker-8B 只用于一次对比消融 |
| 生成/索引 LLM | DeepSeek V4.1 Flash（opencode-go） | 不自建、不自部署 |
| 索引库 | BM25: bm25s(mmap) + jieba；向量: 落盘库（LanceDB/Faiss） | in-memory 在 5,000 篇以上会爆内存 |
| 评测 | 自建 150 题 + RAGAS 指标 + 自写 evidence_recall | 任何单一公开集都覆盖不了六种能力 |

---

## 2. 语料

**来源**：`pleisto/wikipedia-cn-20230720-filtered`（254,574 条 / 约 2.4GB / CC-BY-SA-3.0，衍生自维基，需署名 + 相同方式共享）

**抽样规则（必须写死成脚本）**

```python
# data/raw → data/corpus.jsonl
1. 过滤：800 ≤ len(completion) ≤ 4000 字符
2. 排除标题含：列表|消歧义|索引|年表|图集|车站
3. 分层：人物 / 地理 / 科技 / 历史 / 文化艺术 各 1/5
4. 固定 seed，doc_id = blake2b(title, digest_size=16)
5. 输出 corpus.jsonl: {doc_id, title, text, source_url, license}
```

**为什么分层**：随机 500 篇会抽到大量村镇/物种/消歧义页，全局题答案变成"各种地名"，图和树都演示不出来。

**嵌套抽样（关键）**：开发集必须是评测集的**前缀**，否则调好的东西换语料就失效。

```python
docs = filter_and_stratify(seed=42)      # 先算出完整的有序列表（每层独立洗牌后 round-robin）
dev   = docs[:100]                        # 开发用
eval_ = docs[:2000]                       # 评测用，包含 dev
```

这样 `dev ⊂ eval`，任何在 100 篇上验证过的 chunk 与 gold 都仍然有效。

**100 篇只用于开发，不用于出结论**：
- 100 篇 ≈ 500 chunks，top-5 检索几乎必然命中 gold → 指标**顶到天花板**，六个范式看起来一模一样（这是"假无差异"，不是真无差异）
- M4 的 Leiden 在 100 篇上只会得到 5–10 个社区，社区报告很薄，**不要用它调图参数**
- 全局题在 100 篇上太容易，答"都是关于 X 的"就能蒙对

所以：**100 篇调代码，2,000 篇出数字。**

**体积换算**

| 语料 | 正文 token | chunks | 向量(fp32) |
|---|---|---|---|
| 100 篇（开发） | 0.22M | 500 | 2 MB |
| 2,000 篇（评测） | 4.4M | 10,000 | 41 MB |
| 5,000 篇 | 11M | 25,000 | 102 MB |
| 10,000 篇 | 22M | 50,000 | 205 MB |

**合规**：`data/` 必须进 `.gitignore`（2.4GB + CC-BY-SA，不能入库）；仓库只放 `scripts/prepare_corpus.py` 与 `data/CHUNKS.md`（记录来源、许可、抽样参数）。

---

## 3. 评测协议

**150 题构成**

| 题型 | 题数 | 出题方式 |
|---|---|---|
| 单跳事实 | 40 | RAGAS `TestsetGenerator` 合成 + 筛 |
| 专名/编号/代码符号 | 25 | 合成 + 人工挑 |
| 多跳（2–3 跳） | 30 | 人工写（合成质量差） |
| 全局综述 | 30 | 人工写，必须跨多篇 |
| 无答案/应拒答 | 25 | 人工造（问语料里没有的事） |

合成只解决前两类（43%），后三类必须人写，约半天到一天。

**`queries.jsonl` 字段**

```json
{"qid":"q001","question":"...","type":"single|entity|multi_hop|global|no_answer",
 "gold_doc_ids":["..."],"gold_chunk_ids":["..."],"gold_answer":"...","should_abstain":false}
```

`gold_chunk_ids` 由 `doc_id` + 切分规则自动展开；改了切分就自动失效，这也是"分块冻结"的原因。

**指标**

| 指标 | 定义 | 来源 |
|---|---|---|
| faithfulness | 答案是否被上下文支持 | RAGAS |
| answer_correctness | 与 gold 的一致性（LLM judge） | RAGAS / 自写 |
| evidence_recall | gold chunk 是否进入最终上下文 | 自写（核心） |
| citation_precision | 引用 chunk 中命中的比例 | 自写 |
| abstain_accuracy | 无答案题正确拒答率 | 自写 |
| cost | tokens / LLM 调用数 / P95 延迟 / ¥ | 自写 |

`evidence_recall` 替代 `recall@k`，因为 M3 跨层、M5 改写、M6 多轮，传统 recall 无法对齐。

**Judge 纪律**

- 同一 judge 模型、`temperature=0`、同一 prompt
- **A/B 顺序对调各跑一次**，只报一致的部分与相对胜率
- 不报绝对分（KDD'26 已证顺序偏差显著）
- 若后续能接入不同厂的模型做 judge，可信度更高（避免 self-preference）

---

## 4. 成本

**单价（实测查询 opencode-go 模型表）**
- DeepSeek V4.1 Flash：输入 **$0.15/M**、输出 **$0.60/M**、缓存读 **$0.003/M**
- bge-m3 / bge-reranker-v2-m3：**¥0**（硅基流动免费）

**每语料一轮（含 M1–M6 + 评测）**

| 语料 | M4 图索引 | M3 RAPTOR | 评测 | Embedding | 合计 |
|---|---|---|---|---|---|
| **100 篇（开发）** | $0.55 | $0.05 | 可只跑 30 题 ≈ $0.35 | $0 | **≈ $0.95** |
| 500 篇 | $2.75 | $0.26 | $1.76 | $0 | **$4.8** |
| 2,000 篇（评测） | $11.0 | $1.05 | $1.76 | $0 | **$13.8** |
| 5,000 篇 | $27.5 | $2.63 | $1.76 | $0 | **$31.9** |
| 10,000 篇 | $55.1 | $5.25 | $1.76 | $0 | **$62.1** |

**M4 占 80%** → 省钱靠缩 M4 语料，不是缩别的。

### 实测：硅基流动免费档（2026-11-18，本机）

| 探测项 | 结果 |
|---|---|
| bge-m3 单条冷启动 | 284 ms，1024 维 |
| bge-m3 batch 32（短文本，1,408 tok） | 290 / 566 / 678 ms |
| bge-m3 batch 32 × ~512 tok | **780 ms / 9,952 tok**（约 12.7k tok/s 单请求） |
| 8 并发 × batch32（79,616 tok） | **898 ms，全部 200**，折算 ≈ 5.3M TPM |
| 16 并发 × batch32（159,232 tok） | **1,165 ms，全部 200**，折算 ≈ 8.2M TPM |
| batch 64 / 128 | 均 200（19,904 / 39,808 tok），未见条数上限 |
| bge-reranker-v2-m3（20 篇，top_n=5） | **329 / 354 ms** |
| 弱网/墙/连接层限流 | 无（401 探测往返 ~60 ms） |

结论：**免费档目前完全不限速**，也没有排队。按实测吞吐，2,000 篇的 4.4M token ≈ **1–2 分钟**，10,000 篇的 22M ≈ **5 分钟** —— 之前按 500k TPM 估的 9/44 分钟过于保守。

注意两点：
- 这是**瞬时突发**外推，不是持续速率；正式跑仍要加 token bucket + 429 退避，别把"现在不限速"写进架构假设。
- 免费模型的策略随时可能变，索引步骤要能断点续跑（按 `chunk_id` 记录已完成的向量）。


**token 口径（2,000 篇）**

| 项 | 输入 | 输出 |
|---|---|---|
| 每 chunk 抽取 + gleaning | 2,000 | 1,200 |
| 描述摘要（摊薄） | 300 | 60 |
| RAPTOR（每 10 chunk） | 5,000 | 500 |
| 单次查询 | 5,000 | 500 |
| Agentic 单次查询 | 15,000 | 1,200 |
| Judge 单次 | 1,700 | 200 |

---

## 5. 额度计划

**你现有：LLM $60 / 硅基流动 ¥16**

| 阶段 | 内容 | 花费 |
|---|---|---|
| 开发（第 1 周） | **100 篇 × 10 轮**（改 prompt / 修 bug / 调分块，单轮不到 $1） | $9.5 |
| 正式（第 2 周） | 2,000 篇 × 1 轮 | $13.8 |
| M4 附录 | 500 篇（复用缓存，只补漏） | $2.8 |
| 评测 | 150 题 × 6 范式，分批跑 | $1.8 |
| **小计** | | **≈ $28（按 $35 预留重试）** |

比原来省 $5，更重要的是**开发迭代从"每轮 $4.8"降到"每轮 $0.95"**，敢随便试。

**硅基流动 ¥16 的用法**
- 主线 bge 系免费 → **¥0**
- Embedding 模型对比消融（Qwen3-Embedding-8B，2,000 篇）→ **约 ¥5**
- Rerank 模型对比消融（Qwen3-Reranker-8B）→ **约 ¥4**
- 余 ¥7 缓冲

**省额度的五个开关**

1. 抽取结果落缓存，key = `chunk_id + prompt 版本`（缓存读便宜 50 倍）
2. 先 20 chunk 试点，解析成功率 <90% 不改就上全量 = 白烧一次
3. 分块冻结（512/64），改了索引全废
4. 评测分批：先 30 题跑通，再上 150
5. M3/M4 只跑 500–1,000 篇，M1/M2/M5/M6 跑 2,000 篇 → 总量从 $13.8 降到约 $6

---

## 6. 排期

| 周 | 交付 | 判定标准 |
|---|---|---|
| W1 | 环境、`prepare_corpus.py --size 100`、切分、M1、评测脚手架（30 题） | `ragladder ask` 能答出并带 chunk 引用；排行榜能自动追加一行 |
| W1 | M2（BM25 → +Rerank → +Contextual 三档消融） | 专名题上 BM25 单独跑赢 dense，rerank 有可见增益 |
| W2 | M3 RAPTOR + M4 GraphRAG（500 篇） | 全局题上 M3/M4 明显优于 M1；M4 索引跑通且成本在预算内 |
| W3 | M5 CRAG + M6 Agentic + 150 题全量 + `results/leaderboard.md` | 六范式同表，成本与延迟齐全 |

---

## 7. 风险清单

| 风险 | 触发信号 | 对策 |
|---|---|---|
| GraphRAG 抽取格式失败 | 解析成功率 <90%、空图 | 加 few-shot / 换 prompt / 降 `max_gleanings`；先 20 chunk 试点 |
| 额度被重跑吃光 | 同一语料索引次数 >3 | 强制缓存层；开发期用 `deepseek-v4.1-flash-free` |
| 全局题失真 | 500 篇里主题太散或太窄 | 分层抽样；人工写题时只挑主题分散的 |
| LLM judge 偏差 | 同一答案 A/B 两次结论不一致 | 顺序对调；只报相对胜率 |
| 语料进仓库 | `git status` 出现 data/ | `.gitignore` 补 `data/`，`.venv/` 已由模板覆盖 |
| 时长超支 | M4 单轮 >6h | 并发提到 16；或语料降到 500 篇 |
| 免费 embedding 排队 | 批量 embedding 超时 | 32 条一批 + 指数退避；避开高峰 |

---

## 8. 建议的仓库结构

```
rag-ladder/
├─ pyproject.toml            uv 管理依赖
├─ data/                     # git-ignored
│   ├─ raw/                  # HF 下载
│   ├─ corpus.jsonl
│   ├─ chunks.parquet
│   └─ CHUNKS.md             # 来源 / 许可 / 抽样参数
├─ scripts/prepare_corpus.py --size 500 --seed 42
├─ src/ragladder/
│   ├─ core/types.py         # Document / Chunk / Query / Answer / Cost
│   ├─ index/                # chunk, bm25, dense, raptor, graph
│   ├─ retrieve/             # m1_naive, m2_advanced, m3_raptor, m4_graph, m5_crag, m6_agentic
│   ├─ pipeline.py           # 统一 Pipeline 接口（所有范式实现它）
│   └─ eval/                 # 指标、judge、排行榜生成
├─ eval/queries.jsonl        # 150 题（含 gold）
└─ search/                   # 调研与方案（本目录）
```

**统一接口**（六范式都必须实现，这是同台对比的前提）

```python
class Pipeline(Protocol):
    name: str
    def index(self, chunks: list[Chunk]) -> None: ...
    def run(self, question: str, budget: Budget) -> Answer: ...
    # Answer: answer, contexts[chunk_id], cost{tokens_in, tokens_out, llm_calls, latency_ms}
```
