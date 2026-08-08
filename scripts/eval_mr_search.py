"""실제 머지된 MR로 코드그래프 검색을 잰다.

질의 = MR 제목(사람이 그렇게 요청했을 말), 정답 = 그 MR이 실제로 고친 파일.
합성 벤치와 달리 라벨을 사람이 만든 게 아니라 '실제로 그 코드를 고쳤다'는 사실이다.
"""
import json, re, sys, pathlib
from xgen_maker.config import MakerConfig
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.search import search

SC = pathlib.Path(__file__).resolve().parent.parent / "bench"
cfg = MakerConfig.from_file("maker.config.json")
g = Graph.load(cfg.kg_path)

# 그래프가 아는 (repo, path) 집합
known = {}
for n in g.nodes.values():
    p = n.get("path") or ""
    if p:
        known.setdefault(p, set()).add(n["repo"])

raw = json.loads((SC / "mr_cases.json").read_text(encoding="utf-8"))

# 관례 접두사 제거 — 사람이 물을 때 "fix:"라고 하진 않는다
PREFIX = re.compile(r"^(feat|fix|refactor|chore|docs|style|test|perf|build|ci|release)"
                    r"(\([^)]*\))?\s*:\s*", re.I)
DRAFT = re.compile(r"^(draft|wip)\s*:\s*", re.I)

cases = []
for mr in raw:
    files = [f for f in mr.get("files", []) if f in known]     # 그래프가 아는 파일만 정답
    if not files:
        continue
    q = DRAFT.sub("", mr["title"]).strip()
    q = PREFIX.sub("", q).strip()
    if len(q) < 6:
        continue
    cases.append({"q": q, "files": set(files),
                  "proj": mr.get("proj", "-"), "iid": mr.get("iid", 0)})

def evaluate(k=10, expand=None, label="MR 제목 그대로"):
    hit1 = hitk = 0
    rr = 0.0
    misses = []
    for c in cases:
        q = expand(c["q"]) if expand else c["q"]
        hits = search(g, q, k=k)
        paths = [h.get("path") or "" for h in hits]
        rank = next((i + 1 for i, p in enumerate(paths) if p in c["files"]), 0)
        if rank == 1: hit1 += 1
        if rank: hitk += 1; rr += 1.0 / rank
        else: misses.append(c)
    n = len(cases) or 1
    print(f"  {label:22} 케이스 {n:3}  R@1={hit1/n:.3f}  R@{k}={hitk/n:.3f}  MRR={rr/n:.3f}")
    return misses

if __name__ == "__main__":
    print(f"실제 MR 벤치마크 (그래프가 아는 파일을 고친 MR만)")
    misses = evaluate()
    if "-v" in sys.argv:
        print(f"\n못 찾은 {len(misses)}건 중 앞 12건:")
        for c in misses[:12]:
            print(f"  [{c['proj'].split('/')[-1]:22}] {c['q'][:52]}")
            print(f"      정답 파일: {sorted(c['files'])[:2]}")


def _expanded(q: str) -> str:
    """MAKER 파이프라인과 같은 어휘 변환(한글 → 코드 용어)."""
    from xgen_maker import llm
    d = llm.json_chat(cfg.llm_base, cfg.llm_model, [
        {"role": "system", "content":
         'Extract 4-7 plain lowercase english search words from the dev request '
         '(the domain nouns/verbs, e.g. gateway, login, token, validate). Keep any '
         'service name given (gateway, workflow, frontend, model). Do not invent '
         'class names. Also give a 2-4 word hyphen branch slug for the work. '
         'Reply JSON only: {"keywords": ["..."], "branch": "..."}'},
        {"role": "user", "content": q}], max_tokens=200, timeout=45, retries=2)
    kw = " ".join(str(x) for x in (d or {}).get("keywords", []))
    return f"{q} {kw}".strip() if kw else q
