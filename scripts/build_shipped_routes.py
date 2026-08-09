"""배포되는 프롬프트로 라우팅 추측 메모를 만든다(재개 가능).

앞서 잰 가중 1.5의 이득은 '독립 라우팅 프롬프트' 추측 위에서 나온 수치다.
실제로는 어휘 변환 호출에 얹어 보내고, 두 추측의 일치율은 62.5%였다 —
배포하는 것으로 다시 재지 않으면 그 숫자는 내 것이 아니다.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
import eval_mr_search as E
from check_shipped_routing import shipped_guess
from xgen_maker.kg.profiles import profile_block

BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
MEMO = pathlib.Path(__file__).resolve().parent.parent / "bench" / "mr_shipped_routes.json"


def main() -> None:
    memo = json.loads(MEMO.read_text(encoding="utf-8")) if MEMO.exists() else {}
    block = profile_block(E.g)
    queries = sorted({c["q"] for c in S.CASES if not BOOK.search(c["q"])})
    todo = [q for q in queries if q not in memo]
    print(f"전체 {len(queries)} · 남음 {len(todo)}", flush=True)
    for i, query in enumerate(todo, 1):
        memo[query] = shipped_guess(query, block)
        if i % 5 == 0 or i == len(todo):
            MEMO.write_text(json.dumps(memo, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {i}/{len(todo)}", flush=True)
    MEMO.write_text(json.dumps(memo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"완료 — {sum(1 for q in queries if memo.get(q))}/{len(queries)} 채워짐")


if __name__ == "__main__":
    main()
