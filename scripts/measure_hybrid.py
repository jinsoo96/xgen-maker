"""어휘 검색 + 의미 검색(임베딩)을 합쳤을 때 실제로 나아지는지 잰다.

지금까지 저장소 선택도 순위도 어휘로 풀 수 있는 데까지 갔다. 남은 실패는 말이
겹치지 않는 것들이다 — "관리자 SQL 콘솔의 role·user 컬럼 오탐 제거"의 답은
sql_safety.py인데, 질의의 어떤 낱말도 그 파일에 없다. 의미가 필요한 자리다.

거르지 않고 융합만 한다. 임베딩도 절반쯤 맞는 신호이므로, 틀렸을 때 어휘 근거가
이길 수 있어야 한다 — 저장소 라우팅에서 배운 것과 같다.
"""
import json
import pathlib
import re
import sys
import urllib.request

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.loop.pipeline import _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

BASE = "http://127.0.0.1:12341/v1"
MODEL = "Qwen/Qwen3-Embedding-8B"
BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def embed(texts: list[str]) -> np.ndarray:
    body = json.dumps({"model": MODEL, "input": texts}).encode()
    request = urllib.request.Request(BASE + "/embeddings", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.loads(response.read())
    rows = sorted(data["data"], key=lambda d: d["index"])
    return np.array([r["embedding"] for r in rows], dtype=np.float32)


# Qwen3-Embedding은 질의에 지시문을 붙이는 것이 표준 사용법이다. 문서는 원문 그대로,
# 질의만 "무엇을 찾는지"를 앞에 적는다 — 같은 문장이라도 '찾는 쪽'과 '찾히는 쪽'의
# 표현을 갈라 주면 검색 품질이 달라진다.
INSTRUCT = ("Instruct: 개발 요청을 읽고 고쳐야 할 소스 코드 위치를 찾는다\n"
            "Query: {q}")


def query_vectors(queries: list[str], instruct: bool = False) -> dict[str, np.ndarray]:
    memo_path = BENCH / ("query_vectors_instruct.npz" if instruct
                         else "query_vectors.npz")
    memo: dict[str, np.ndarray] = {}
    if memo_path.exists():
        cached = np.load(memo_path, allow_pickle=True)
        memo = {q: v for q, v in zip(cached["queries"], cached["vectors"])}
    todo = [q for q in queries if q not in memo]
    for start in range(0, len(todo), 64):
        chunk = todo[start:start + 64]
        payload = [INSTRUCT.format(q=q) for q in chunk] if instruct else chunk
        for query, vector in zip(chunk, embed(payload)):
            memo[query] = vector
        np.savez_compressed(memo_path,
                            queries=np.array(list(memo), dtype=object),
                            vectors=np.array(list(memo.values()), dtype=np.float16))
    return memo


def main() -> None:
    graph = Graph.load(pathlib.Path("kg/merged.json"))
    routes = json.loads((BENCH / "mr_shipped_routes.json").read_text(encoding="utf-8"))
    cases = [c for c in S.CASES if not BOOK.search(c["q"])]
    splits = {"A": cases[0::2], "B": cases[1::2], "C": cases[:132], "D": cases[132:]}

    store = np.load(BENCH / "node_vectors.npz", allow_pickle=True)
    ids = list(store["ids"])
    matrix = np.asarray(store["vectors"], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    print(f"노드 벡터 {matrix.shape[0]}개 · {matrix.shape[1]}차원", flush=True)

    plain_vecs = query_vectors([c["q"] for c in cases])
    inst_vecs = query_vectors([c["q"] for c in cases], instruct=True)
    qvecs = plain_vecs
    lex = lexicon(graph)

    def dense(query: str, k: int, table=None) -> list[dict]:
        vector = np.asarray((table or qvecs)[query], dtype=np.float32)
        vector /= np.linalg.norm(vector) + 1e-9
        scores = matrix @ vector
        top = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
        top = top[np.argsort(-scores[top])]
        out = []
        for index in top:
            node = graph.nodes.get(ids[index])
            if node is not None:
                out.append({"score": float(scores[index]), **node})
        return out

    def lexical(query: str, k: int = 10) -> list[dict]:
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

    def score(lands, subset) -> tuple[float, float, float]:
        r1 = r10 = mrr = 0
        for case in subset:
            hits = lands(case["q"])
            rank = next((i for i, h in enumerate(hits, 1)
                         if (h.get("path") or "") in case["files"]), None)
            if rank:
                r10 += 1
                mrr += 1 / rank
                if rank == 1:
                    r1 += 1
        n = len(subset)
        return r1 / n, r10 / n, mrr / n

    def report(label, lands) -> None:
        a, b, m = score(lands, cases)
        parts = "  ".join(f"{k}:{score(lands, v)[1]:.3f}" for k, v in splits.items())
        print(f"  {label:26} R@1={a:.3f} R@10={b:.3f} MRR={m:.3f} | 분할 {parts}",
              flush=True)

    print(f"기능질의 {len(cases)}건")
    report("어휘만(현행)", lambda q: lexical(q, 10))
    report("의미만(원문)", lambda q: dense(q, 10, plain_vecs))
    report("의미만(지시문)", lambda q: dense(q, 10, inst_vecs))
    best = inst_vecs
    for head in (0, 1, 2):
        report(f"융합 · 어휘머리{head}",
               lambda q, h=head: _fuse(lexical(q, 24), dense(q, 24, best), k=10, head=h))


if __name__ == "__main__":
    main()
