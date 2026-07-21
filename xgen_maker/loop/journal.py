"""⑩ 세션 journal — "작업로그 확인가능" 요구의 구현.

세션마다 worklogs/<UTC일자>-<slug>/ 디렉토리에 journal.jsonl(이벤트 스트림)과
SUMMARY.md(사람용 타임라인)를 남긴다.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path


def slugify(text: str, fallback: str = "task") -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(ascii_part) >= 4:
        return ascii_part[:40]
    return f"{fallback}-{abs(hash(text)) % 10**8:08d}"


class Journal:
    def __init__(self, worklogs_dir: str | Path, query: str, verbose: bool = False):
        self.slug = slugify(query)
        self.verbose = verbose
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        self.dir = Path(worklogs_dir) / f"{stamp}-{self.slug}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "journal.jsonl"
        self.events: list[dict] = []
        self.event("session_start", "ok", query=query)

    def cancelled(self) -> bool:
        """사용자가 중지를 요청했는가. CLI 경로엔 중지 개념이 없어 항상 False.

        오래 걸리는 단계(에이전트 실행 등)는 단계 경계뿐 아니라 실행 '도중'에도
        이걸 폴링해야 한다. 안 그러면 중지를 눌러도 그 단계가 끝날 때까지(에이전트는
        기본 30분) 계속 돌며 레포를 고친다.
        """
        return False

    def event(self, step: str, status: str, **data) -> None:
        record = {"ts": time.time(),
                  "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "step": step, "status": status, **data}
        self.events.append(record)
        with self.file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if self.verbose:
            # 실시간 진행 로그 — "지금 뭘 하는지"를 사람이 따라 읽는 표면
            extras = json.dumps(data, ensure_ascii=False, default=str) if data else ""
            if len(extras) > 220:
                extras = extras[:220] + "…"
            mark = {"ok": "✓", "pass": "✓", "fail": "✗", "empty": "·",
                    "skipped": "·", "observe": "◇"}.get(status, "▸")
            print(f"{mark} [{record['iso'][11:19]}] {step:<14} {status:<8} {extras}",
                  flush=True)

    def close(self, outcome: str) -> Path:
        self.event("session_end", outcome)
        lines = [f"# MAKER 세션 — {self.slug}", ""]
        for record in self.events:
            extras = {k: v for k, v in record.items()
                      if k not in ("ts", "iso", "step", "status")}
            detail = f" — `{json.dumps(extras, ensure_ascii=False, default=str)[:300]}`" if extras else ""
            lines.append(f"- `{record['iso']}` **{record['step']}** [{record['status']}]{detail}")
        summary = self.dir / "SUMMARY.md"
        summary.write_text("\n".join(lines), encoding="utf-8")
        return summary
