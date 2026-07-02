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
    def __init__(self, worklogs_dir: str | Path, query: str):
        self.slug = slugify(query)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        self.dir = Path(worklogs_dir) / f"{stamp}-{self.slug}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "journal.jsonl"
        self.events: list[dict] = []
        self.event("session_start", "ok", query=query)

    def event(self, step: str, status: str, **data) -> None:
        record = {"ts": time.time(),
                  "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "step": step, "status": status, **data}
        self.events.append(record)
        with self.file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

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
