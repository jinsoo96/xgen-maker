"""256건 기준 손잡이 훑기 — 작은 표본에서 내린 판단을 다시 검증한다."""
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import eval_mr_search as E
from xgen_maker.kg.search import lexicon
from xgen_maker.kg.lexicon import bridge_terms
from xgen_maker.loop.pipeline import _fuse
from xgen_maker.kg.anchor import find_anchors, expand as aexp, rank_within

MEMO = json.loads((pathlib.Path(__file__).resolve().parent.parent /
                   "bench" / "mr_expansions.json").read_text(encoding="utf-8"))
CASES = sorted([c for c in E.cases if c["q"] in MEMO], key=lambda c: c["q"])

def kw_of(q):
    f = MEMO.get(q, "")
    return f[len(q):].strip() if f.startswith(q) else ""

def land(q, k=10, head=2):
    lex = lexicon(E.g)
    kw = kw_of(q); br = bridge_terms(lex, q)
    m = f"{kw} {br}".strip() if kw else (br or q)
    h = _fuse(E.search(E.g, q, k=24), E.search(E.g, m, k=24), k=k, head=head)
    a = find_anchors(E.g, q, kw)
    if a:
        r = rank_within(aexp(E.g, a), q, kw, k=24)
        if r: h = _fuse(r, h, k=k)
    return h

def measure(label, fn=None, k=10):
    fn = fn or land
    t1 = tk = 0; rr = 0.0
    for c in CASES:
        paths = [(h.get("path") or "") for h in fn(c["q"], k)]
        r = next((i + 1 for i, p in enumerate(paths) if p in c["files"]), 0)
        if r == 1: t1 += 1
        if r: tk += 1; rr += 1 / r
    n = len(CASES)
    print(f"  {label:30} R@1={t1/n:.3f} R@10={tk/n:.3f} MRR={rr/n:.3f}", flush=True)
    return rr / n
