"""⑥ 구현 — 코딩에이전트(T2)를 도구로 호출.

기본 에이전트 = claude CLI(headless). config.agent_cmd로 임의 에이전트 치환 가능
({prompt_path} placeholder, 테스트에선 스텁 스크립트를 꽂는다).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

RULES = """[규칙 — 반드시 준수]
1. 기능(소스) 코드만 수정한다. docker/compose/CI/인프라 파일은 절대 만지지 않는다.
2. 요청 범위 밖의 파일은 수정하지 않는다.
3. 기존 코드 스타일을 따른다. 불필요한 주석을 달지 않는다.
4. 커밋은 하지 않는다(루프가 수행)."""


def build_prompt(query: str, intent: str, landing: list[dict], legacy_notes: str) -> str:
    landing_lines = "\n".join(
        f"- [{n['kind']}] {n['name']} — {n['repo']}:{n['path']}:{n.get('line', 0)}"
        for n in landing[:8])
    return (f"[요청]\n{query}\n\n[intent] {intent}\n\n"
            f"[지식그래프 착지점 — 여기부터 조사]\n{landing_lines}\n\n"
            f"[레거시 확인 메모]\n{legacy_notes or '(없음)'}\n\n{RULES}\n"
            f"위 요청을 이 저장소에서 구현하라.")


def run_agent(repo_path: str | Path, prompt: str, session_dir: Path,
              agent_cmd: str | None = None, timeout: int = 1800) -> dict:
    prompt_path = session_dir / "agent-prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    if agent_cmd:
        command = agent_cmd.format(prompt_path=str(prompt_path))
        shell = True
    else:
        if not shutil.which("claude"):
            return {"ok": False, "output": "", "error": "claude CLI 미발견 — config.agent_cmd 필요"}
        command = ["claude", "--permission-mode", "acceptEdits", "-p", prompt]
        shell = False
    try:
        result = subprocess.run(command, cwd=repo_path, shell=shell, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"에이전트 타임아웃({timeout}s)"}
    output = (result.stdout or "") + (result.stderr or "")
    (session_dir / "agent-output.log").write_text(output, encoding="utf-8")
    return {"ok": result.returncode == 0, "output": output[-4000:],
            "error": None if result.returncode == 0 else f"exit={result.returncode}"}
