"""파이프라인 커버리지 — 설계한 단계가 실제로 도는지 기록으로 확인한다.

카탈로그에 25단계를 적어두는 것과 그게 실제로 도는 것은 다르다. 어떤 단계는 조건이
안 맞아 조용히 건너뛰고, 어떤 단계는 이벤트를 안 남겨 돌고도 안 돈 것처럼 보인다.
둘 다 기록만 봐서는 구분이 안 된다.

여기서는 저널에 남은 이벤트를 카탈로그와 대조해 셋으로 나눈다.
  - 돌았다        : 실제 이벤트가 있다
  - 안 돌았다(설명): 게이트가 꺼져 있거나 모드가 아니어서 — skipped 흔적이 있다
  - 안 돌았다(설명 없음): 흔적이 아예 없다. 이게 문제다.
"""
from __future__ import annotations

import json
from pathlib import Path


def _catalog() -> list[tuple[str, str, str | None]]:
    from .web import MakerWebHandler
    return [(step, label, gate) for step, label, _desc, gate in MakerWebHandler.PIPELINE]


def scan(worklogs_dir: str | Path) -> dict:
    """세션 기록 전체를 훑어 단계별 실행/스킵 횟수를 센다."""
    ran: dict[str, int] = {}
    skipped: dict[str, int] = {}
    reasons: dict[str, str] = {}
    sessions = 0
    root = Path(worklogs_dir)
    if root.is_dir():
        for journal in sorted(root.glob("*/journal.jsonl")):
            sessions += 1
            try:
                lines = journal.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                step, status = event.get("step", ""), event.get("status", "")
                if not step:
                    continue
                if status == "skipped":
                    skipped[step] = skipped.get(step, 0) + 1
                    if event.get("reason") and step not in reasons:
                        reasons[step] = str(event["reason"])[:120]
                else:
                    ran[step] = ran.get(step, 0) + 1

    stages = []
    for step, label, gate in _catalog():
        stages.append({"step": step, "label": label, "gate": gate,
                       "ran": ran.get(step, 0), "skipped": skipped.get(step, 0),
                       "reason": reasons.get(step, ""),
                       "silent": not ran.get(step) and not skipped.get(step)})
    silent = [s for s in stages if s["silent"]]
    return {"sessions": sessions, "stages": stages,
            "covered": sum(1 for s in stages if s["ran"]),
            "explained": sum(1 for s in stages if not s["ran"] and s["skipped"]),
            "silent": [s["step"] for s in silent]}


def format_report(result: dict) -> str:
    lines = [f"파이프라인 커버리지 — 세션 {result['sessions']}개 · "
             f"{len(result['stages'])}단계 중 {result['covered']}단계 실행"]
    for stage in result["stages"]:
        if stage["ran"]:
            mark, note = "✓", f"{stage['ran']}회"
        elif stage["skipped"]:
            mark, note = "·", f"건너뜀 — {stage['reason'] or '사유 미기록'}"
        else:
            mark, note = "✗", "기록 없음 — 돌았는지 알 수 없습니다"
        lines.append(f"  {mark} {stage['label']:22} {note}")
    if result["silent"]:
        lines.append("")
        lines.append("기록이 전혀 없는 단계 — 돌지 않았거나, 돌고도 남기지 않았습니다:")
        lines.append("  " + ", ".join(result["silent"]))
    return "\n".join(lines)
