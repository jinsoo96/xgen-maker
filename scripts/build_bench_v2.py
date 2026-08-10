"""넓힌 표본에 필요한 것들(확장어·저장소 추측·질의 벡터)을 병렬로 만든다.

앞서 순차로 만들 때는 224건에 세 시간이 걸렸다. 요약·확장은 서로 독립인데 줄을
세울 이유가 없다 — 6워커면 38건/분이다(그 이상은 CLI 쪽이 한계).

부분 데이터로 재면 결론이 뒤집힌다. 그래서 다 채운 뒤에만 측정한다.
"""
import concurrent.futures as futures
import json
import pathlib
import re
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from xgen_maker import llm
from xgen_maker.kg.graph import Graph
from xgen_maker.kg.dense import embed_texts, _INSTRUCT
from xgen_maker.kg.profiles import profile_block

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
CASES = BENCH / "mr_cases_v2.json"
EXPANSIONS = BENCH / "expansions_v2.json"
ROUTES = BENCH / "routes_v2.json"
VECTORS = BENCH / "qvectors_v2.npz"
BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
_WORKERS = 4      # 장시간 돌릴 때는 낮춘다(일시 실패가 쌓였다)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def expand_one(query: str, block: str) -> tuple[str, dict]:
    """파이프라인이 실제로 보내는 프롬프트 그대로 — 확장어와 저장소 추측을 함께."""
    system = (
        'Extract 4-7 plain lowercase english search words from the dev request '
        '(the domain nouns/verbs, e.g. gateway, login, token, validate). Keep any '
        'service name given (gateway, workflow, frontend, model). Do not invent '
        'class names. Also give a 2-4 word hyphen branch slug for the work. '
        'Reply JSON only: {"keywords": ["..."], "branch": "...", "repo": "..."}')
    user = query
    if block:
        user = (f"{query}\n\n[repositories — pick the one whose code must change, "
                f'exact name, or "" if unsure]\n{block}')
    out = llm.json_chat("claude_cli", "cli",
                        [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
                        max_tokens=200, timeout=60, retries=2)
    return query, (out if isinstance(out, dict) else {})


def main() -> None:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    queries = sorted({c["title"] for c in cases if not BOOK.search(c["title"])})
    print(f"사례 {len(cases)}건 · 기능질의 {len(queries)}종", flush=True)

    graph = Graph.load(pathlib.Path("kg/merged.json"))
    block = profile_block(graph)
    expansions, routes = _load(EXPANSIONS), _load(ROUTES)
    # 둘 다 비어 있으면 호출이 실패한 것이다(모델이 저장소를 모르겠다고 답한 것과 다르다).
    # 장시간 병렬로 돌리면 일시 실패가 쌓인다 — 그대로 재면 실패한 파이프라인을 재는 셈.
    todo = [q for q in queries
            if q not in expansions or (not expansions[q] and not routes.get(q))]
    print(f"확장 필요 {len(todo)}건", flush=True)

    done = 0
    with futures.ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for query, answer in pool.map(lambda q: expand_one(q, block), todo):
            expansions[query] = " ".join(str(k) for k in (answer.get("keywords") or []))
            routes[query] = str(answer.get("repo") or "").strip()
            done += 1
            if done % 40 == 0:
                _save(EXPANSIONS, expansions)
                _save(ROUTES, routes)
                print(f"  {done}/{len(todo)}", flush=True)
    _save(EXPANSIONS, expansions)
    _save(ROUTES, routes)

    memo = {}
    if VECTORS.exists():
        with np.load(VECTORS, allow_pickle=True) as store:
            memo = {q: v for q, v in zip(store["queries"], store["vectors"])}
    need = [q for q in queries if q not in memo]
    print(f"질의 벡터 필요 {len(need)}건", flush=True)
    import os
    base = os.environ.get("XGEN_MAKER_EMBED_BASE", "http://127.0.0.1:12341/v1")
    model = os.environ.get("XGEN_MAKER_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
    for start in range(0, len(need), 64):
        chunk = need[start:start + 64]
        got = embed_texts(base, model, [_INSTRUCT.format(query=q) for q in chunk])
        if got is None:
            print("  임베딩 실패 — 중단"); break
        for query, vector in zip(chunk, got):
            memo[query] = vector
    np.savez_compressed(VECTORS, queries=np.array(list(memo), dtype=object),
                        vectors=np.array(list(memo.values()), dtype=np.float16))
    filled = sum(1 for q in queries if expansions.get(q) is not None and q in memo)
    print(f"완료 — 질의 {len(queries)}종 중 {filled}종 준비됨")


if __name__ == "__main__":
    main()
