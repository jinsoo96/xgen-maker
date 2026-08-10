"""넓은 표본으로 상수를 다시 정한다 — 세 개가 옮겨 갔다.

기존 294건은 대표성이 없었다. 정답이 작은 저장소에 몰려 있고(게이트웨이 14%·모델
13%, 노드 비중은 1%·3.6%) 시기도 최근에 치우쳤다. 그 위에서 고른 값들이 처음 보는
MR 2,412건에서 최적이 아니다:
  BM25 길이 정규화  0.2 → 0.1 쪽이 R@1·R@10·MRR 전부 낫다
  융합 재료         12 → 16 쪽이 셋 다 낫다
  저장소 크기 보정   0.1 → 0.0 쪽이 최근·이전 양쪽에서 낫다

서로 영향을 주므로 한 번에 하나씩 고정해 두 바퀴 돈다. 마지막에 기존 표본에서도
무너지지 않는지 확인한다 — 새 표본에 다시 맞추는 것이 목적이 아니다.
"""
import collections
import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker.kg.graph import Graph
from xgen_maker.kg import rank as rank_mod
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
STATE = {"material": 12}


def build():
    old = {c["title"] for c in
           json.loads((BENCH / "mr_cases.json").read_text(encoding="utf-8"))}
    cases = json.loads((BENCH / "mr_cases_v2.json").read_text(encoding="utf-8"))
    expansions = json.loads((BENCH / "expansions_v2.json").read_text(encoding="utf-8"))
    routes = json.loads((BENCH / "routes_v2.json").read_text(encoding="utf-8"))
    with np.load(BENCH / "qvectors_v2.npz", allow_pickle=True) as store:
        qvecs = {str(q): v for q, v in zip(store["queries"], store["vectors"])}
    graph = Graph.load(pathlib.Path("kg/merged.json"))
    known = {n.get("path") for n in graph.nodes.values() if n.get("path")}
    variants = collections.defaultdict(set)
    for case in cases:
        variants[case["title"]].add(frozenset(case["files"]))
    ambiguous = {t for t, files in variants.items() if len(files) > 1}
    usable = [c for c in cases
              if not BOOK.search(c["title"]) and c["title"] not in ambiguous
              and len(c["title"]) >= 15 and (set(c["files"]) & known)
              and expansions.get(c["title"]) and c["title"] in qvecs]
    fresh = [c for c in usable if c["title"] not in old]
    seen = [c for c in usable if c["title"] in old]
    return graph, fresh, seen, expansions, routes, qvecs


GRAPH, FRESH, SEEN, EXPANSIONS, ROUTES, QVECS = build()
INDEX = DenseIndex("kg/vectors.npz")


def score(subset):
    lex = lexicon(GRAPH)
    material = STATE["material"]
    r1 = r10 = mrr = 0
    for case in subset:
        query = case["title"]
        hint = ROUTES.get(query, "")
        keywords = EXPANSIONS.get(query, "")
        merged = f"{keywords} {bridge_terms(lex, query)}".strip() or query
        hits = _fuse(ksearch(GRAPH, query, k=24, hint_repo=hint),
                     ksearch(GRAPH, merged, k=24, hint_repo=hint),
                     k=material, head=1)
        anchors = find_anchors(GRAPH, query, keywords)
        if anchors:
            within = rank_within(aexp(GRAPH, anchors), query, keywords, k=24)
            if within:
                hits = _fuse(within, hits, k=material)
        vector = np.asarray(QVECS[query], dtype=np.float32)
        vector /= np.linalg.norm(vector) + 1e-9
        scores = INDEX._matrix @ vector
        top = np.argsort(-scores)[:material]
        dense = [{"score": float(scores[i]), **GRAPH.nodes[INDEX.ids[i]]}
                 for i in top if INDEX.ids[i] in GRAPH.nodes]
        fused = _fuse(hits[:material], dense, k=10, head=0)
        rank = next((i for i, h in enumerate(fused, 1)
                     if (h.get("path") or "") in case["files"]), None)
        if rank:
            r10 += 1
            mrr += 1 / rank
            if rank == 1:
                r1 += 1
    n = len(subset) or 1
    return r1 / n, r10 / n, mrr / n


def show(label):
    a, b, m = score(FRESH)
    print(f"    {label:16} R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}", flush=True)
    return b + m          # 회수와 순위 질을 함께 본다


def main() -> None:
    print(f"처음 보는 MR {len(FRESH)}건 · 기존 표본 {len(SEEN)}건\n", flush=True)
    for round_no in (1, 2):
        print(f"[{round_no}바퀴] BM25 길이 정규화")
        best = (None, -1)
        for value in (0.0, 0.05, 0.1, 0.15, 0.2):
            rank_mod._B = value
            GRAPH.touch()
            got = show(f"B {value}")
            best = max(best, (value, got), key=lambda x: x[1])
        rank_mod._B = best[0]
        GRAPH.touch()
        print(f"    → B {best[0]} 채택\n")

        print(f"[{round_no}바퀴] 저장소 크기 보정")
        best = (None, -1)
        for value in (0.0, 0.05, 0.1):
            rank_mod._REPO_SIZE_DAMP = value
            GRAPH.touch()
            got = show(f"α {value}")
            best = max(best, (value, got), key=lambda x: x[1])
        rank_mod._REPO_SIZE_DAMP = best[0]
        GRAPH.touch()
        print(f"    → α {best[0]} 채택\n")

        print(f"[{round_no}바퀴] 융합 재료")
        best = (None, -1)
        for value in (12, 16, 20, 24):
            STATE["material"] = value
            got = show(f"재료 {value}")
            best = max(best, (value, got), key=lambda x: x[1])
        STATE["material"] = best[0]
        print(f"    → 재료 {best[0]} 채택\n")

    print(f"최종: B={rank_mod._B} · α={rank_mod._REPO_SIZE_DAMP} "
          f"· 재료={STATE['material']}")
    a, b, m = score(FRESH)
    print(f"  처음 보는 MR  R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}")
    a, b, m = score(SEEN)
    print(f"  기존 표본     R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}")


if __name__ == "__main__":
    main()
