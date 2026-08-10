"""남은 상수들도 넓은 표본에서 최적점이 맞는지 본다.

앞 회차에서 세 개가 옮겨 갔다(B·재료·크기 보정). 같은 표본으로 고른 나머지도
확인해야 한다 — 하나만 치우침이었을 리 없다.
확인 대상: 저장소 라우팅 가중 · 화면 문구 무게 · 종류 가중(파일) · 테스트 감점.
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
from xgen_maker.kg import search as search_mod
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


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


def score():
    lex = lexicon(GRAPH)
    material = _LEXICAL_MATERIAL
    r1 = r10 = mrr = 0
    for case in FRESH:
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
    n = len(FRESH) or 1
    return r1 / n, r10 / n, mrr / n


def show(label, current=False):
    a, b, m = score()
    mark = "  ← 현재 값" if current else ""
    print(f"    {label:16} R@1={a:.3f} R@10={b:.3f} MRR={m:.3f}{mark}", flush=True)


def main() -> None:
    print(f"처음 보는 MR {len(FRESH)}건\n", flush=True)

    print("저장소 라우팅 가중")
    for value in (1.0, 1.15, 1.3, 1.5):
        search_mod._REPO_HINT = value
        show(f"가중 {value}", value == 1.3)
    search_mod._REPO_HINT = 1.3

    print("\n화면 문구 무게")
    for value in (1, 2, 3):
        rank_mod._LABEL_WEIGHT = value
        GRAPH.touch()
        show(f"무게 {value}", value == 2)
    rank_mod._LABEL_WEIGHT = 2
    GRAPH.touch()

    print("\n파일 노드 종류 가중")
    original = rank_mod._KIND_BOOST["file"]
    for value in (1.3, 1.6, 2.0):
        rank_mod._KIND_BOOST["file"] = value
        GRAPH.touch()
        show(f"file {value}", value == original)
    rank_mod._KIND_BOOST["file"] = original
    GRAPH.touch()

    print("\n테스트 감점")
    original = rank_mod._TEST_PENALTY
    for value in (0.2, 0.35, 0.6):
        rank_mod._TEST_PENALTY = value
        GRAPH.touch()
        show(f"감점 {value}", value == original)
    rank_mod._TEST_PENALTY = original
    GRAPH.touch()


if __name__ == "__main__":
    main()
