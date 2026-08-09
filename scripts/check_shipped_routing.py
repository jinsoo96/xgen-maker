"""배포되는 프롬프트가 실측한 정확도를 실제로 내는지 확인한다.

가중 1.5는 '독립 라우팅 프롬프트'로 만든 추측 위에서 쟀다. 실제로는 어휘 변환
호출에 얹어 보내므로 프롬프트가 다르다 — 다르면 그 숫자는 옮겨가지 않는다.
같은 질의 표본에 배포 프롬프트를 그대로 태워 두 정확도를 나란히 본다.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
import eval_mr_search as E
from xgen_maker import llm
from xgen_maker.kg.profiles import profile_block

BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
ROUTES = json.loads((pathlib.Path(__file__).resolve().parent.parent
                     / "bench" / "mr_repo_routes.json").read_text(encoding="utf-8"))


def shipped_guess(query: str, block: str) -> str:
    """pipeline.py가 보내는 것과 같은 프롬프트."""
    system = (
        'Extract 4-7 plain lowercase english search words from the dev request '
        '(the domain nouns/verbs, e.g. gateway, login, token, validate). Keep any '
        'service name given (gateway, workflow, frontend, model). Do not invent '
        'class names. Also give a 2-4 word hyphen branch slug for the work. '
        'Reply JSON only: {"keywords": ["..."], "branch": "...", "repo": "..."}')
    user = (f"{query}\n\n[repositories — pick the one whose code must change, "
            f'exact name, or "" if unsure]\n{block}')
    out = llm.json_chat("claude_cli", "cli",
                        [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
                        max_tokens=200, timeout=45, retries=3)
    if not isinstance(out, dict):
        return ""
    return str(out.get("repo") or "").strip()


def main() -> None:
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    real = [c for c in S.CASES if not BOOK.search(c["q"])]
    path_repo = {}
    for node in E.g.nodes.values():
        if node.get("path"):
            path_repo.setdefault(node["path"], node.get("repo"))
    block = profile_block(E.g)
    seen, sample = set(), []
    for case in real:                      # 정렬 순서로 고정 — 재현 가능
        if case["q"] in seen:
            continue
        seen.add(case["q"])
        sample.append(case)
        if len(sample) >= size:
            break

    ship_ok = solo_ok = same = 0
    keywords_ok = 0
    for case in sample:
        target = {path_repo.get(f) for f in case["files"]} - {None}
        guess = shipped_guess(case["q"], block)
        solo = ROUTES.get(case["q"], "")
        ship_ok += guess in target
        solo_ok += solo in target
        same += guess == solo
        keywords_ok += bool(guess)         # 응답 자체가 왔는지
        print(f"  {case['q'][:38]:40} 배포 {guess:22} 독립 {solo:22} "
              f"정답 {sorted(target)[0] if target else '-'}", flush=True)

    n = len(sample)
    print(f"\n표본 {n}건")
    print(f"  배포 프롬프트 정확도  {ship_ok/n:.1%}")
    print(f"  독립 프롬프트 정확도  {solo_ok/n:.1%}")
    print(f"  두 추측이 같은 비율   {same/n:.1%}   · 응답 온 비율 {keywords_ok/n:.1%}")


if __name__ == "__main__":
    main()
