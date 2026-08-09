"""벤치마크 질의별 LLM 저장소 추측을 메모에 쌓는다(재개 가능).

부분 데이터로 재면 결론이 뒤집힌다 — 전에 확장어 캐시가 차오르는 중에 재다가
판단이 두 번 뒤집혔다. 그래서 메모를 다 채운 뒤에만 측정한다.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
from probe_repo_routing import ask

BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
MEMO = pathlib.Path(__file__).resolve().parent.parent / "bench" / "mr_repo_routes.json"


def main() -> None:
    memo = json.loads(MEMO.read_text(encoding="utf-8")) if MEMO.exists() else {}
    queries = sorted({c["q"] for c in S.CASES if not BOOK.search(c["q"])})
    todo = [q for q in queries if q not in memo]
    print(f"전체 {len(queries)}건 · 이미 있음 {len(queries)-len(todo)} · 남음 {len(todo)}", flush=True)
    for i, query in enumerate(todo, 1):
        memo[query] = ask(query)
        if i % 5 == 0 or i == len(todo):
            MEMO.write_text(json.dumps(memo, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {i}/{len(todo)} 저장", flush=True)
    MEMO.write_text(json.dumps(memo, ensure_ascii=False, indent=1), encoding="utf-8")
    filled = sum(1 for q in queries if memo.get(q))
    print(f"완료 — 채워진 추측 {filled}/{len(queries)}")


if __name__ == "__main__":
    main()
