"""LLM이 요청만 보고 '어느 저장소를 고칠 일인가'를 맞힐 수 있는지 표본으로 잰다.

어휘 신호로 저장소를 고르는 시도는 셋 다 실패했다(포괄도 가중·CORI 선택·다양화).
남은 건 의미 기반 라우팅뿐인데, 만들기 전에 되는지부터 본다. 층화 표본 —
지금 저장소를 틀리는 건과 맞히는 건을 같은 수로 섞어, 고치는 만큼 망가뜨리는지 본다.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import sweep256 as S
import eval_mr_search as E
from xgen_maker import llm

BOOK = re.compile(r"^\s*(?:revert\b|merge\s+(?:branch|remote)\b|\[release\]|release[:/ ])"
                  r"|develop\s*(?:→|->)\s*(?:stage|stg|main|master)", re.I)
PROFILES = json.loads((pathlib.Path(__file__).resolve().parent.parent
                       / "bench" / "repo_profiles.json").read_text(encoding="utf-8"))


def profile_block() -> str:
    lines = []
    for repo, info in PROFILES.items():
        lines.append(f"- {repo}: 주요 경로 {', '.join(info['dirs'][:6])}"
                     + (f" / 대표 심볼 {', '.join(info['names'][:6])}" if info["names"] else ""))
    return "\n".join(lines)


def ask(query: str) -> str:
    out = llm.json_chat("claude_cli", "cli", [{"role": "user", "content": f"""아래는 한 제품을 이루는 저장소 목록과 각 저장소의 주요 경로·심볼이다.

{profile_block()}

요청: "{query}"

이 요청을 처리하려면 어느 저장소의 코드를 고쳐야 하는가?
반드시 위 목록에 있는 이름 하나만 고른다.
{{"repo": "<저장소 이름>"}} 형식의 JSON만 출력."""}], retries=2)
    return (out or {}).get("repo", "") if isinstance(out, dict) else ""


def main() -> None:
    real = [c for c in S.CASES if not BOOK.search(c["q"])]
    path_repo = {}
    for node in E.g.nodes.values():
        if node.get("path"):
            path_repo.setdefault(node["path"], node.get("repo"))

    right, wrong = [], []
    for case in real:
        target = {path_repo.get(f) for f in case["files"]} - {None}
        hits = S.land(case["q"], 1, head=1)
        got = hits[0].get("repo") if hits else ""
        (right if got in target else wrong).append((case, target, got))
    # 층화: 틀리는 쪽과 맞히는 쪽을 같은 수로. 표본은 정렬 순서로 고정(재현 가능).
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    sample = wrong[:size] + right[:size]

    stat = {"틀리던 건": [0, 0], "맞히던 건": [0, 0]}
    for case, target, got in sample:
        key = "틀리던 건" if got not in target else "맞히던 건"
        guess = ask(case["q"])
        stat[key][1] += 1
        if guess in target:
            stat[key][0] += 1
        print(f"  [{key}] {case['q'][:40]:42} 현행 {got:22} LLM {guess:22} "
              f"정답 {sorted(target)[0]}", flush=True)

    print("\n=== LLM 저장소 라우팅 정확도 ===")
    for key, (ok, total) in stat.items():
        base = "0%" if key == "틀리던 건" else "100%"
        print(f"  {key} {ok}/{total} ({ok/max(total,1):.1%})   현행 {base}")


if __name__ == "__main__":
    main()
