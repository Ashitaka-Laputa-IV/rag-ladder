"""Build the experiment corpus from Chinese Wikipedia snapshots.

    python scripts/prepare_corpus.py --size 200                  # LLM classifier
    python scripts/prepare_corpus.py --size 200 --classifier rule # offline fallback
    python scripts/prepare_corpus.py --size 200 --dry-run

Pipeline: read parquet/JSON -> normalize -> filter -> simplify Chinese ->
classify into five themes -> nested sampling -> data/corpus.jsonl + manifest.

Sampling is deterministic: rank = blake2b(seed:title), themes interleaved, so
--size 100 is always a prefix of --size 2000. doc_id derives from the title, so
annotations survive resampling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import zhconv

THEME_KEYWORDS: dict[str, list[str]] = {
    "person": ["演员", "作家", "歌手", "导演", "政治家", "科学家", "哲学家", "画家",
               "作曲家", "诗人", "小说家", "运动员", "将军", "主教", "皇帝", "总统",
               "首相", "教授", "发明家", "建筑师", "主持人", "企业家", "音乐家",
               "少将", "上将", "中将", "大将", "元帅", "烈士", "学者"],
    "geo": ["市", "县", "省", "自治区", "河", "山脉", "山", "湖", "岛", "半岛",
            "海峡", "盆地", "高原", "平原", "沙漠", "国家", "共和国", "王国",
            "港口", "群岛", "瀑布", "运河", "地区", "州"],
    "tech": ["计算机", "软件", "算法", "物理", "化学", "数学", "生物", "医学",
             "工程", "互联网", "人工智能", "航天", "卫星", "核", "电子",
             "机械", "材料", "天文", "语言学", "心理学", "经济学", "技术"],
    "hist": ["朝代", "王朝", "战争", "战役", "条约", "革命", "起义", "帝国",
             "考古", "遗址", "文物", "年号", "古代", "近代", "历史", "事变",
             "运动", "世纪"],
    "art": ["电影", "小说", "音乐", "绘画", "诗", "戏剧", "歌剧", "博物馆",
            "建筑", "节日", "宗教", "神话", "传说", "文学", "艺术", "雕塑",
            "书法", "舞蹈", "戏曲", "摄影", "游戏", "动漫", "电视剧", "剧集",
            "综艺", "动画", "漫画", "专辑", "乐队", "乐团", "歌曲"],
}
THEMES = list(THEME_KEYWORDS)

PERSON_MARKERS = ["出生于", "卒于", "毕业于", "原名", "本名", "祖籍", "籍贯", "逝世", "生于"]
PERSON_PATTERN = re.compile(r"[，,]\s*(?:男|女)\s*[，,]|(?:汉|回|满|藏|苗|彝|壮|维吾尔|蒙古)族[，,]|人[，。]")
EXCLUDE_TITLE = ["列表", "消歧义", "索引", "年表", "图集", "车站", "分类", "模板", "小作品",
                 "机场", "铁路", "公路", "线路", "站"]

CJK = re.compile(r"[\u4e00-\u9fff]")
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
BRACKET_REF = re.compile(r"\[(?:\d{1,3}|注\s*\d+|编辑|來源請求|需要引用|原創研究|谁|何时)\]")
SPACES = re.compile(r"[ \t\u00a0\u3000]+")
BLANKLINES = re.compile(r"\n{3,}")
CONVERSION = re.compile(r"-\{(.*?)\}-")
EMPTY_PARENS = re.compile(r"[（(]\s*[，,、;；]?\s*[）)]")
ORPHAN_PARENS = re.compile(r"[（(]\s*[，,、;；]+\s*")
DOUBLE_PERIOD = re.compile(r"。{2,}")
SENTENCE_END = re.compile(r"[。！？!?]")
REFERENCE_HEADS = ("\n参考文献", "\n参考资料", "\n参考与注释", "\n外部链接", "\n延伸阅读", "\n注释")

LLM_PROMPT = """Classify the Chinese Wikipedia article into exactly one theme.

person: biography of a human
geo: a place (country, province, city, county, river, mountain, island)
tech: science, engineering, medicine, computing, university or research institute
hist: historical event, dynasty, war, treaty, political movement, past state
art: film, TV, music, literature, game, museum, religion, festival, architecture
other: does not clearly fit

Reply with JSON only: {{"theme": "person|geo|tech|hist|art|other"}}

标题：{title}
正文：{body}"""


# ---------------------------------------------------------------- text cleanup

def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CTRL.sub("", text)
    text = BRACKET_REF.sub("", text)
    text = SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return BLANKLINES.sub("\n\n", text).strip()


def resolve_conversion_markup(text: str) -> str:
    def pick(match: re.Match) -> str:
        options: dict[str, str] = {}
        for part in match.group(1).split(";"):
            key, sep, value = part.partition(":")
            options[key.strip() if sep else ""] = value.strip() if sep else part.strip()
        for key in ("zh-cn", "zh-hans", "zh-sg", "zh"):
            if key in options:
                return options[key]
        return options.get("", "")

    return CONVERSION.sub(pick, text)


def cut_reference_sections(text: str) -> str:
    best = min((text.find(h) for h in REFERENCE_HEADS if h in text), default=-1)
    return text[:best].rstrip() if best > len(text) * 0.4 else text


def strip_category_tail(text: str) -> str:
    parts = text.split("\n\n")
    while len(parts) > 1:
        tail = parts[-1].strip()
        if not tail or (len(tail) < 400 and not SENTENCE_END.search(tail)):
            parts.pop()
            continue
        break
    return "\n\n".join(parts).strip()


def strip_trailing_short_lines(text: str, min_lines: int = 2, max_len: int = 30) -> str:
    lines = text.split("\n")
    cut = len(lines)
    while cut > 0:
        line = lines[cut - 1].strip()
        if line and (len(line) >= max_len or SENTENCE_END.search(line)):
            break
        cut -= 1
    return "\n".join(lines[:cut] if len(lines) - cut >= min_lines else lines).strip()


def clean_text(text: str) -> str:
    text = resolve_conversion_markup(text)
    text = cut_reference_sections(text)
    text = strip_category_tail(text)
    text = strip_trailing_short_lines(text)
    text = ORPHAN_PARENS.sub("（", text)
    text = EMPTY_PARENS.sub("", text)
    return DOUBLE_PERIOD.sub("。", BLANKLINES.sub("\n\n", text).strip())


def normalize_title(title: str) -> str:
    return "".join(ch for ch in title.strip().lower() if ch.isalnum())


def cjk_ratio(text: str) -> float:
    return len(CJK.findall(text)) / len(text) if text else 0.0


def reject_reason(title: str, text: str, min_chars: int, max_chars: int) -> str | None:
    if not title or not text:
        return "empty"
    if len(text) < min_chars:
        return "too_short"
    if len(text) > max_chars:
        return "too_long"
    if any(word in title for word in EXCLUDE_TITLE):
        return "title_blacklist"
    if text.startswith("可以指") or "消歧义" in text[:100]:
        return "disambiguation"
    if cjk_ratio(text) < 0.3:
        return "not_chinese"
    return None


# ---------------------------------------------------------------- classifiers

def classify_rule(title: str, text: str) -> str | None:
    head = text[:200]
    scores = {t: sum(1 for k in kws if k in title) for t, kws in THEME_KEYWORDS.items()}
    best = max(scores, key=lambda t: (scores[t], -THEMES.index(t)))
    if scores[best] > 0:
        return best
    if len(title) <= 14 and any(m in head for m in PERSON_MARKERS):
        return "person"
    if len(title) <= 14 and PERSON_PATTERN.search(head[:80]):
        return "person"
    body = {t: sum(1 for k in kws if k in head) for t, kws in THEME_KEYWORDS.items()}
    best_body = max(body, key=lambda t: (body[t], -THEMES.index(t)))
    return best_body if body[best_body] >= 4 else None


def build_llm(model: str, base_url: str, api_key: str, workers: int):
    """Return (classify_one, classify_many, usage) built on LangChain.

    usage accumulates real token counts reported by the provider, so the corpus
    manifest can report cost instead of guessing.
    """
    import json as _json
    import re as _re

    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0,
        max_tokens=400,
        timeout=60,
        max_retries=2,
        default_headers={"x-opencode-session": os.environ.get("OPENCODE_SESSION_ID", "")},
    )
    prompt = ChatPromptTemplate.from_template(LLM_PROMPT)
    chain = prompt | llm
    usage = {"calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def parse(message) -> str | None:
        usage["calls"] += 1
        meta = getattr(message, "usage_metadata", None) or {}
        usage["prompt_tokens"] += int(meta.get("input_tokens", 0))
        usage["completion_tokens"] += int(meta.get("output_tokens", 0))
        raw = getattr(message, "content", "") or ""
        if not raw:
            raw = (getattr(message, "additional_kwargs", {}) or {}).get("reasoning_content", "")
        match = _re.search(r"\{.*?\}", raw, _re.S)
        if not match:
            usage["failed"] += 1
            return None
        try:
            theme = str(_json.loads(match.group(0))["theme"]).lower()
        except (ValueError, KeyError):
            usage["failed"] += 1
            return None
        return theme if theme in THEME_KEYWORDS else None

    def classify(title: str, text: str) -> str | None:
        try:
            message = chain.invoke({"title": title, "body": text[:220]})
        except Exception:  # noqa: BLE001
            usage["failed"] += 1
            return None
        return parse(message)

    def classify_many(rows: list[dict]) -> list[str | None]:
        inputs = [{"title": r["title"], "body": r["text"][:220]} for r in rows]
        messages = chain.batch(inputs, config={"max_concurrency": workers},
                               return_exceptions=True)
        results: list[str | None] = []
        for message in messages:
            if isinstance(message, Exception):
                usage["failed"] += 1
                results.append(None)
            else:
                results.append(parse(message))
        return results

    return classify, classify_many, usage


def load_api_key() -> str:
    for name in ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY", "SILICONFLOW_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            for name in ("OPENCODE_GO_API_KEY", "OPENCODE_API_KEY", "SILICONFLOW_API_KEY"):
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    return ""


# ---------------------------------------------------------------- io + sampling

def iter_rows(path: Path):
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        cols = [c for c in ("id", "url", "title", "text") if c in pf.schema_arrow.names]
        for batch in pf.iter_batches(batch_size=2000, columns=cols):
            for row in batch.to_pylist():
                yield row.get("title", ""), row.get("text", ""), row.get("url", "")
    else:
        import ijson

        with path.open("rb") as f:
            for row in ijson.items(f, "item"):
                yield (row.get("title") or "", row.get("completion") or row.get("text") or "",
                       row.get("source") or "")


def pick_source(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    for pattern in ("*.parquet", "*.json"):
        found = [p for p in sorted(Path("data/raw").glob(pattern)) if p.stat().st_size > 1_000_000]
        if found:
            return found[0]
    return Path("data/raw/zhwiki-00002.parquet")


def digest(*parts: str, size: int = 8) -> str:
    return hashlib.blake2b(":".join(parts).encode("utf-8"), digest_size=size).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=100)
    ap.add_argument("--seed", default="42")
    ap.add_argument("--source", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/corpus.jsonl"))
    ap.add_argument("--min-chars", type=int, default=800)
    ap.add_argument("--max-chars", type=int, default=4000)
    ap.add_argument("--per-theme-pool", type=int, default=0, help="default: size/5*3")
    ap.add_argument("--candidate-limit", type=int, default=0, help="default: pool*6")
    ap.add_argument("--classifier", choices=["llm", "rule"], default="llm")
    ap.add_argument("--llm-model", default="deepseek-v4.1-flash")
    ap.add_argument("--llm-base", default="https://opencode.ai/inference/go/openai/v1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    args = ap.parse_args()
    started = time.perf_counter()

    source = pick_source(args.source)
    if not source.exists():
        print(f"source not found: {source}")
        return 2

    per_theme = max(1, args.size // len(THEMES))
    pool_size = args.per_theme_pool or per_theme * 3
    candidate_limit = args.candidate_limit or pool_size * 6

    classify = classify_rule
    classify_many = None
    usage: dict = {"calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0}
    if args.classifier == "llm":
        key = load_api_key()
        if not key:
            print("missing OPENCODE_GO_API_KEY / OPENCODE_API_KEY / SILICONFLOW_API_KEY")
            return 2
        classify, classify_many, usage = build_llm(args.llm_model, args.llm_base, key, 8)
        print(f"classifier: llm ({args.llm_model})")
    else:
        print("classifier: rule")

    seen: set[str] = set()
    candidates: list[dict] = []
    rejects: Counter = Counter()
    scanned = 0

    for raw_title, raw_text, url in iter_rows(source):
        scanned += 1
        title = normalize_text(raw_title).replace("\n", " ")
        text = normalize_text(raw_text)
        reason = reject_reason(title, text, args.min_chars, args.max_chars)
        if reason:
            rejects[reason] += 1
            continue
        title = EMPTY_PARENS.sub("", zhconv.convert(title, "zh-cn")).strip()
        text = clean_text(zhconv.convert(text, "zh-cn"))
        if len(text) < args.min_chars:
            rejects["too_short_after_clean"] += 1
            continue
        if any(word in title for word in EXCLUDE_TITLE):
            rejects["title_blacklist"] += 1
            continue
        key = normalize_title(title)
        if not key or key in seen:
            rejects["duplicate"] += 1
            continue
        seen.add(key)
        candidates.append({"title": title, "text": text, "chars": len(text), "url": url or ""})
        if len(candidates) >= candidate_limit:
            break

    print(f"scanned {scanned:,} rows -> {len(candidates)} candidates")
    if args.classifier == "llm":
        print(f"classifying {len(candidates)} candidates...", file=sys.stderr)

    picked: dict[str, list[dict]] = defaultdict(list)
    themes = (classify_many(candidates) if classify_many
              else [classify(c["title"], c["text"]) for c in candidates])
    for cand, theme in zip(candidates, themes):
        if theme is None or len(picked[theme]) >= pool_size:
            if theme is None:
                rejects["no_theme"] += 1
            continue
        picked[theme].append(
            {
                "doc_id": f"{theme}-{digest(args.seed, normalize_title(cand['title']))}",
                "title": cand["title"],
                "theme": theme,
                "text": cand["text"],
                "chars": cand["chars"],
                "url": cand["url"],
                "license": "CC-BY-SA-4.0",
                "rank": digest(args.seed, "rank", normalize_title(cand["title"]), size=16),
            }
        )

    for theme in picked:
        picked[theme].sort(key=lambda row: row["rank"])
    ordered: list[dict] = []
    for i in range(pool_size):
        for theme in THEMES:
            if i < len(picked.get(theme, [])):
                ordered.append(picked[theme][i])

    docs = ordered[: args.size]
    for doc in docs:
        doc.pop("rank", None)

    stats: dict[str, int] = defaultdict(int)
    for doc in docs:
        stats[doc["theme"]] += 1
    chars = sorted(doc["chars"] for doc in docs)

    print(f"\nselected {len(docs)} docs  " + "  ".join(f"{k}:{v}" for k, v in stats.items()))
    if rejects:
        print("rejected: " + "  ".join(f"{k}:{v}" for k, v in rejects.most_common()))
    if chars:
        print(f"chars min/median/max = {chars[0]} / {chars[len(chars) // 2]} / {chars[-1]}")
    elapsed = time.perf_counter() - started
    cost = usage["prompt_tokens"] / 1e6 * 0.15 + usage["completion_tokens"] / 1e6 * 0.60
    print(f"elapsed {elapsed:.1f}s  llm_calls {usage['calls']}  "
          f"tokens {usage['prompt_tokens']:,}+{usage['completion_tokens']:,}  "
          f"cost ${cost:.3f}")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as f:
        for doc in docs:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(
            {
                "source": str(source),
                "dataset": "wikimedia/wikipedia" if source.suffix == ".parquet" else "third-party JSON",
                "seed": args.seed,
                "size": len(docs),
                "themes": dict(stats),
                "filters": {"min_chars": args.min_chars, "max_chars": args.max_chars,
                            "exclude_title": EXCLUDE_TITLE},
                "normalization": "zhconv zh-cn",
                "classifier": args.classifier,
                "llm_model": args.llm_model if args.classifier == "llm" else None,
                "scanned_rows": scanned,
                "rejected": dict(rejects),
                "elapsed_sec": round(elapsed, 1),
                "llm_usage": usage,
                "cost_usd": round(cost, 4),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {args.out} and {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
