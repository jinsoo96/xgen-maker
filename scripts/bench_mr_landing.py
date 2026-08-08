"""실제 머지 MR 벤치 — 고정 집합에서만 측정한다.

전에 배경 작업이 확장 캐시를 채우는 중에 여러 번 쟀다. 잴 때마다 표본이 달라져
결론이 뒤집혔다(같은 변경이 좋다가 나빠졌다). 그래서 여기서는 확장이 전부 준비된
케이스만, 정렬된 순서로, 한 번에 잰다 — 안 되면 아예 재지 않는다.
"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import eval_mr_search as E
from xgen_maker.loop.pipeline import _prefer, _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

SC = pathlib.Path(__file__).resolve().parent.parent / "bench"
memo = json.loads((SC / "mr_expansions.json").read_text(encoding="utf-8"))
CASES = sorted([c for c in E.cases if c["q"] in memo], key=lambda c: (c["proj"], c["iid"]))
MISSING = [c["q"] for c in E.cases if c["q"] not in memo]

def kw_of(q): 
    full = memo.get(q, "")
    return full[len(q):].strip() if full.startswith(q) else ""

def measure(fn, label, k=10):
    t1 = tk = 0; rr = 0.0
    for c in CASES:
        paths = [(h.get("path") or "") for h in fn(c["q"], k)]
        r = next((i+1 for i, p in enumerate(paths) if p in c["files"]), 0)
        if r == 1: t1 += 1
        if r: tk += 1; rr += 1.0/r
    n = len(CASES) or 1
    print(f"  {label:26} R@1={t1/n:.3f} R@{k}={tk/n:.3f} MRR={rr/n:.3f}")
    return rr/n

def land_prefer(q, k=10):
    kw = kw_of(q); hits = E.search(E.g, q, k=k)
    if kw: hits = _prefer(E.search(E.g, kw, k=k), hits, k=k)
    a = find_anchors(E.g, q, kw)
    if a:
        r = rank_within(aexp(E.g, a), q, kw, k=k)
        if r: hits = _prefer(r, hits, k=k)
    return hits

def land_fuse(q, k=10):
    kw = kw_of(q); hits = E.search(E.g, q, k=k)
    if kw: hits = _fuse(E.search(E.g, kw, k=24), E.search(E.g, q, k=24), k=k)
    a = find_anchors(E.g, q, kw)
    if a:
        r = rank_within(aexp(E.g, a), q, kw, k=k)
        if r: hits = _prefer(r, hits, k=k)
    return hits

if __name__ == "__main__":
    if MISSING:
        print(f"확장 미완 {len(MISSING)}건 — 표본이 흔들리므로 측정하지 않는다.")
        sys.exit(1)
    print(f"고정 집합 {len(CASES)}건 (실제 머지된 MR)")
    measure(lambda q,k: E.search(E.g,q,k=k), "원문 검색만")
    measure(lambda q,k: E.search(E.g, kw_of(q) or q, k=k), "확장어 검색만")
    measure(land_prefer, "착지: _prefer(현행 전)")
    measure(land_fuse,   "착지: RRF 융합")
