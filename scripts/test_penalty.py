"""테스트 감점을 풀면 지표가 오르는데, 그게 진짜 이득인지 가른다.

넓은 표본에서 감점 0.6이 0.35보다 낫게 나왔다. 그런데 벤치마크는 그 MR이 바꾼
파일 중 **아무거나** 찾으면 맞춘 것으로 센다 — 감점을 풀면 테스트 파일을 더 잘 찾고,
그게 점수로 잡힌다. 정답 전부가 테스트인 MR은 1%뿐이니, 대부분은 구현 파일이
따로 있다는 뜻이다.

그래서 정답에서 테스트를 빼고 다시 잰다. 거기서도 오르면 진짜 이득이고,
안 오르면 지표만 올리는 것이다 — 에이전트는 고칠 자리가 아니라 테스트에 착지한다.
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
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
MARKERS = ("test", "spec", "__tests__", "conftest", "fixture")


def is_test(path: str) -> bool:
    return any(marker in path.lower() for marker in MARKERS)


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
    fresh = [c for c in cases
             if c["title"] not in old and not BOOK.search(c["title"])
             and c["title"] not in ambiguous and len(c["title"]) >= 15
             and (set(c["files"]) & known)
             and expansions.get(c["title"]) and c["title"] in qvecs]
    return graph, fresh, expansions, routes, qvecs


GRAPH, FRESH, EXPANSIONS, ROUTES, QVECS = build()
INDEX = DenseIndex("kg/vectors.npz")
# 구현 파일이 따로 있는 MR만 — 정답이 테스트뿐이면 테스트에 착지하는 게 맞다.
IMPL = [c for c in FRESH if any(not is_test(f) for f in c["files"])]


def score(subset, answers):
    lex = lexicon(GRAPH)
    material = _LEXICAL_MATERIAL
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
        want = answers(case)
        rank = next((i for i, h in enumerate(fused, 1)
                     if (h.get("path") or "") in want), None)
        if rank:
            r10 += 1
            mrr += 1 / rank
            if rank == 1:
                r1 += 1
        n = len(subset) or 1
    n = len(subset) or 1
    return r1 / n, r10 / n, mrr / n


def main() -> None:
    print(f"처음 보는 MR {len(FRESH)}건 · 구현 파일이 있는 것 {len(IMPL)}건\n", flush=True)
    for value in (0.35, 0.5, 0.6, 0.8, 1.0):
        rank_mod._TEST_PENALTY = value
        GRAPH.touch()
        a1, b1, m1 = score(FRESH, lambda c: set(c["files"]))
        a2, b2, m2 = score(IMPL, lambda c: {f for f in c["files"] if not is_test(f)})
        print(f"  감점 {value:<5} 전체 R@1={a1:.3f} R@10={b1:.3f} MRR={m1:.3f}"
              f"  |  구현만 R@1={a2:.3f} R@10={b2:.3f} MRR={m2:.3f}", flush=True)
    rank_mod._TEST_PENALTY = 0.35


if __name__ == "__main__":
    main()
