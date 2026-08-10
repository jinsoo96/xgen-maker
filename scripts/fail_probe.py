"""남은 실패를 하나씩 들여다본다 — 고칠 수 있는 것과 원리적으로 못 맞히는 것을 가른다.

지표만 보면 "41건 남았다"로 끝나지만, 그 41건이 같은 종류가 아니다. 질의에 애초에
단서가 없는 것과, 단서가 있는데 못 찾는 것은 다른 일이다. 후자만 고칠 수 있다.
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
from xgen_maker.kg.rank import tokenize, node_terms
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def build():
    graph = Graph.load(pathlib.Path("kg/merged.json"))
    routes = json.loads((BENCH / "mr_shipped_routes.json").read_text(encoding="utf-8"))
    index = DenseIndex("kg/vectors.npz")
    with np.load(BENCH / "query_vectors_instruct.npz", allow_pickle=True) as store:
        qvecs = {q: v for q, v in zip(store["queries"], store["vectors"])}
    lex = lexicon(graph)
    material = _LEXICAL_MATERIAL

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

    def land(query, k=10):
        return _fuse(lexical(query, material), dense(query, material), k=k, head=0)

    return graph, land, lexical, dense, routes


def main() -> None:
    graph, land, lexical, dense, routes = build()
    cases = [c for c in S.CASES if not BOOK.search(c["q"])]
    path_repo = {}
    for node in graph.nodes.values():
        if node.get("path"):
            path_repo.setdefault(node["path"], node.get("repo"))

    rows = []
    for case in cases:
        hits = land(case["q"], 10)
        if any((h.get("path") or "") in case["files"] for h in hits):
            continue
        deep = land(case["q"], 300)
        rank = next((i for i, h in enumerate(deep, 1)
                     if (h.get("path") or "") in case["files"]), None)
        lex_rank = next((i for i, h in enumerate(lexical(case["q"], 300), 1)
                         if (h.get("path") or "") in case["files"]), None)
        den_rank = next((i for i, h in enumerate(dense(case["q"], 300), 1)
                         if (h.get("path") or "") in case["files"]), None)
        target_repos = {path_repo.get(f) for f in case["files"]} - {None}
        got_repo = hits[0].get("repo") if hits else ""
        query_words = set(tokenize(case["q"]) + tokenize(S.kw_of(case["q"])))
        target_words = set()
        for node in graph.nodes.values():
            if (node.get("path") or "") in case["files"]:
                target_words |= set(node_terms(node))
        rows.append({
            "q": case["q"], "files": sorted(case["files"]),
            "rank": rank, "lex": lex_rank, "dense": den_rank,
            "repo_ok": got_repo in target_repos,
            "shared": len(query_words & target_words),
            "hint": routes.get(case["q"], ""),
            "hint_ok": routes.get(case["q"], "") in target_repos,
        })

    print(f"실패 {len(rows)}건 / 전체 {len(cases)}건\n")
    print(f"{'융합':>5} {'어휘':>5} {'의미':>5} {'레포':>4} {'힌트':>4} {'공유어':>5}  질의")
    for r in sorted(rows, key=lambda x: (x["rank"] or 9999)):
        print(f"{str(r['rank'] or '-'):>5} {str(r['lex'] or '-'):>5} "
              f"{str(r['dense'] or '-'):>5} {'O' if r['repo_ok'] else 'X':>4} "
              f"{'O' if r['hint_ok'] else 'X':>4} {r['shared']:>5}  {r['q'][:52]}")
        print(f"{'':>27}    정답: {r['files'][0][:58]}")

    pathlib.Path(BENCH / "failures.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
