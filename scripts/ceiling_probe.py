"""남은 실패가 원리적으로 맞힐 수 있는 것인지 잰다.

착지 후보 10개를 사람(LLM)에게 그대로 보여 주고 "이 요청이 고칠 파일"을 고르게 한다.
같은 정보로 사람도 못 고르면, 천장은 검색이 아니라 그 질의에 단서가 없다는 뜻이다.
"""
import json, pathlib, re, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
from xgen_maker import llm
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import lexicon, search as ksearch
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.kg.dense import DenseIndex
from xgen_maker.loop.pipeline import _fuse, _LEXICAL_MATERIAL
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

B = pathlib.Path(__file__).resolve().parent.parent / "bench"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)


def main() -> None:
    rows = json.loads((B / "failures.json").read_text(encoding="utf-8"))
    g = Graph.load(pathlib.Path("kg/merged.json"))
    routes = json.loads((B / "mr_shipped_routes.json").read_text(encoding="utf-8"))
    idx = DenseIndex("kg/vectors.npz")
    with np.load(B / "query_vectors_instruct.npz", allow_pickle=True) as st:
        qv = {q: v for q, v in zip(st["queries"], st["vectors"])}
    lex = lexicon(g); M = 40      # 천장 측정 — 재료를 넓혀 회수부터 본다

    def land(query, k=30):
        h = routes.get(query, ""); kw = S.kw_of(query)
        mm = f"{kw} {bridge_terms(lex, query)}".strip() or query
        hits = _fuse(ksearch(g, query, k=40, hint_repo=h),
                     ksearch(g, mm, k=40, hint_repo=h), k=M, head=1)
        a = find_anchors(g, query, kw)
        if a:
            r = rank_within(aexp(g, a), query, kw, k=40)
            if r: hits = _fuse(r, hits, k=M)
        v = np.asarray(qv[query], dtype=np.float32); v /= np.linalg.norm(v) + 1e-9
        s = idx._matrix @ v; top = np.argsort(-s)[:M]
        dh = [{"score": float(s[i]), **g.nodes[idx.ids[i]]} for i in top if idx.ids[i] in g.nodes]
        return _fuse(hits[:M], dh, k=k, head=0)

    solvable = unsolvable = 0
    for row in rows:
        hits = land(row["q"], 30)
        paths = []
        for h in hits:
            p = h.get("path") or ""
            if p and p not in paths:
                paths.append(p)
        if not any(p in row["files"] for p in paths):
            print(f"  [후보에 정답 없음] {row['q'][:46]}")
            unsolvable += 1
            continue
        listing = "\n".join(f"{i+1}. {p}" for i, p in enumerate(paths[:20]))
        out = llm.json_chat("claude_cli", "cli", [
            {"role": "system", "content":
             'Pick the file this change request would modify. Reply JSON only: {"n": <number>}'},
            {"role": "user", "content": f"요청: {row['q']}\n\n후보:\n{listing}"}],
            max_tokens=60, retries=2)
        pick = (out or {}).get("n")
        chosen = paths[pick - 1] if isinstance(pick, int) and 1 <= pick <= len(paths) else ""
        ok = chosen in row["files"]
        solvable += ok
        unsolvable += (not ok)
        print(f"  [{'사람도 맞힘' if ok else '사람도 못 맞힘'}] {row['q'][:44]}")
    total = solvable + unsolvable
    print(f"\n후보 안에 정답이 있는데 못 고른 것 중 사람이 맞힌 비율: "
          f"{solvable}/{total} ({solvable/max(total,1):.0%})")


if __name__ == "__main__":
    main()
