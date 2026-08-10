"""융합 방식을 다시 훑는다 — RRF가 한쪽에만 있는 정답을 희석하는 문제.

실패 41건을 보니 어휘 단독으로 6~9위인 정답이 융합 뒤 11~22위로 밀려나 있었다.
RRF는 순위만 보므로 '양쪽에 어중간하게 있는 것'이 '한쪽에 확실히 있는 것'을 이긴다.
양쪽 머리를 지키는 방식과 가중 RRF, 점수 기반 융합을 나란히 잰다.
"""
import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def interleave(first, second, k, heads=1):
    """양쪽 머리를 번갈아 지킨 뒤 나머지를 RRF로 채운다."""
    kept, seen = [], set()
    for i in range(heads):
        for side in (first, second):
            if i < len(side) and side[i]["id"] not in seen:
                kept.append(side[i])
                seen.add(side[i]["id"])
    rest = _fuse([h for h in first if h["id"] not in seen],
                 [h for h in second if h["id"] not in seen], k=k, head=0)
    return (kept + rest)[:k]


def weighted_rrf(first, second, k, w_first=1.0, w_second=1.0, c=60):
    score, node = {}, {}
    for ranked, weight in ((first, w_first), (second, w_second)):
        for i, hit in enumerate(ranked):
            score[hit["id"]] = score.get(hit["id"], 0.0) + weight / (c + i + 1)
            node[hit["id"]] = hit
    order = sorted(score, key=lambda x: -score[x])
    return [node[i] for i in order[:k]]


def norm_sum(first, second, k, w_first=1.0, w_second=1.0):
    """점수를 각자 0~1로 펴서 더한다 — 순위만 보는 RRF와 달리 '얼마나 확실한가'를 남긴다."""
    score, node = {}, {}
    for ranked, weight in ((first, w_first), (second, w_second)):
        if not ranked:
            continue
        values = [h.get("score", 0.0) for h in ranked]
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        for hit in ranked:
            norm = (hit.get("score", 0.0) - low) / span
            score[hit["id"]] = score.get(hit["id"], 0.0) + weight * norm
            node[hit["id"]] = hit
    order = sorted(score, key=lambda x: -score[x])
    return [node[i] for i in order[:k]]


def main() -> None:
    graph = Graph.load(pathlib.Path("kg/merged.json"))
    routes = json.loads((BENCH / "mr_shipped_routes.json").read_text(encoding="utf-8"))
    index = DenseIndex("kg/vectors.npz")
    with np.load(BENCH / "query_vectors_instruct.npz", allow_pickle=True) as store:
        qvecs = {q: v for q, v in zip(store["queries"], store["vectors"])}
    cases = [c for c in S.CASES if not BOOK.search(c["q"])]
    splits = {"A": cases[0::2], "B": cases[1::2],
              "C": cases[:132], "D": cases[132:]}
    lex = lexicon(graph)

    def lexical(query, k):
        hint = routes.get(query, "")
        keywords = S.kw_of(query)
        merged = f"{keywords} {bridge_terms(lex, query)}".strip() or query
        hits = _fuse(ksearch(graph, query, k=24, hint_repo=hint),
                     ksearch(graph, merged, k=24, hint_repo=hint), k=k, head=1)
        anchors = find_anchors(graph, query, keywords)
        if anchors:
            within = rank_within(aexp(graph, anchors), query, keywords, k=24)
            if within:
                hits = _fuse(within, hits, k=k)
        return hits

    def dense(query, k):
        vector = np.asarray(qvecs[query], dtype=np.float32)
        vector /= np.linalg.norm(vector) + 1e-9
        scores = index._matrix @ vector
        top = np.argsort(-scores)[:k]
        return [{"score": float(scores[i]), **graph.nodes[index.ids[i]]}
                for i in top if index.ids[i] in graph.nodes]

    memo = {}

    def material(query, m):
        key = (query, m)
        if key not in memo:
            memo[key] = (lexical(query, m), dense(query, m))
        return memo[key]

    def score(fuse, m, subset):
        r1 = r10 = mrr = 0
        for case in subset:
            first, second = material(case["q"], m)
            hits = fuse(first, second, 10)
            rank = next((i for i, h in enumerate(hits, 1)
                         if (h.get("path") or "") in case["files"]), None)
            if rank:
                r10 += 1
                mrr += 1 / rank
                if rank == 1:
                    r1 += 1
        n = len(subset)
        return r1 / n, r10 / n, mrr / n

    def report(label, fuse, m=12):
        a, b, c = score(fuse, m, cases)
        parts = "  ".join(f"{k}:{score(fuse, m, v)[1]:.3f}" for k, v in splits.items())
        print(f"  {label:30} R@1={a:.3f} R@10={b:.3f} MRR={c:.3f} | {parts}", flush=True)

    print(f"기능질의 {len(cases)}건 — 융합 방식")
    report("현행 RRF(머리0)", lambda a, b, k: _fuse(a, b, k=k, head=0))
    report("양쪽 머리1 + RRF", lambda a, b, k: interleave(a, b, k, heads=1))
    report("양쪽 머리2 + RRF", lambda a, b, k: interleave(a, b, k, heads=2))
    report("양쪽 머리3 + RRF", lambda a, b, k: interleave(a, b, k, heads=3))
    for wl, wd in ((1.5, 1.0), (1.0, 1.5), (2.0, 1.0)):
        report(f"가중 RRF 어휘{wl}/의미{wd}",
               lambda a, b, k, x=wl, y=wd: weighted_rrf(a, b, k, x, y))
    report("점수정규화 합", lambda a, b, k: norm_sum(a, b, k))
    report("점수정규화 어휘1.5", lambda a, b, k: norm_sum(a, b, k, 1.5, 1.0))


if __name__ == "__main__":
    main()
