"""⑥ 구현 — 코딩에이전트(T2)를 도구로 호출.

기본 에이전트 = claude CLI(headless). config.agent_cmd로 임의 에이전트 치환 가능
({prompt_path} placeholder, 테스트에선 스텁 스크립트를 꽂는다).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path


def _kill_tree(proc: subprocess.Popen) -> None:
    """에이전트 프로세스를 자식까지 죽인다.

    shell=True면 셸이 먼저 뜨고 그 아래에서 실제 에이전트가 돈다. 직접 자식만
    죽이면 진짜 작업 프로세스가 고아로 남아 레포를 계속 고친다.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:  # /T = 트리 전체, /F = 강제
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 9)
            return
        except (OSError, AttributeError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


RULES = """[규칙 — 반드시 준수]
1. 기능(소스) 코드만 수정한다. docker/compose/CI/인프라 파일은 절대 만지지 않는다.
2. 요청 범위 밖의 파일은 수정하지 않는다.
3. 기존 코드 스타일을 따른다. 불필요한 주석을 달지 않는다.
4. 커밋은 하지 않는다(루프가 수행)."""


def build_prompt(query: str, intent: str, landing: list[dict], legacy_notes: str,
                 chain: list[dict] | None = None,
                 dependents: list[dict] | None = None) -> str:
    landing_lines = "\n".join(
        f"- [{n['kind']}] {n['name']} — {n['repo']}:{n['path']}:{n.get('line', 0)}"
        for n in landing[:8])
    chain_block = ""
    if chain:
        chain_lines = "\n".join(
            f"- [{c['kind']}] {c['name']} ({'/'.join(c['relation'])}, hop {c['hop']}) "
            f"— {c['repo']}:{c['path']}"
            for c in chain[:12] if c.get("hop", 0) > 0)
        if chain_lines:
            chain_block = ("\n[연결된 워크플로우 체인 — 착지점과 import/call/endpoint로 이어진 곳. "
                           "같이 봐야 회귀를 막는다]\n" + chain_lines + "\n")
    # 의존자(나를 쓰는 쪽) — 회귀는 여기서 난다. chain은 정방향(내가 쓰는 쪽)이라
    # 시그니처를 바꿔도 '누가 깨지는지'를 못 알려준다. 역방향을 따로 넣어야 한다.
    dep_block = ""
    if dependents:
        dep_lines = "\n".join(
            f"- [{d['kind']}] {d['name']} — {d['repo']}:{d.get('path', '')}"
            f"{':' + str(d['line']) if d.get('line') else ''} (거리 {d.get('distance', '?')})"
            for d in dependents[:12] if d.get("kind") != "repo")
        if dep_lines:
            dep_block = ("\n[이 코드를 쓰는 곳 — 시그니처·동작을 바꾸면 여기가 깨진다. "
                         "바꿔야 하면 호출부까지 같이 고쳐라]\n" + dep_lines + "\n")
    return (f"[요청]\n{query}\n\n[intent] {intent}\n\n"
            f"[지식그래프 착지점 — 여기부터 조사]\n{landing_lines}\n"
            f"{chain_block}{dep_block}\n[레거시 확인 메모]\n{legacy_notes or '(없음)'}\n\n{RULES}\n"
            f"위 요청을 이 저장소에서 구현하라.")


def run_agent(repo_path: str | Path, prompt: str, session_dir: Path,
              agent_cmd: str | None = None, timeout: int = 1800,
              should_cancel=None) -> dict:
    prompt_path = session_dir / "agent-prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    stdin_payload = None
    if agent_cmd:
        command = agent_cmd.format(prompt_path=str(prompt_path))
        shell = True
    else:
        exe = shutil.which("claude")
        if not exe:
            return {"ok": False, "output": "", "error": "claude CLI 미발견 — config.agent_cmd 필요"}
        # 프롬프트는 stdin으로 전달 — 멀티라인 argv의 셸 인용 문제 회피.
        # Windows npm 심(.cmd/.ps1)은 CreateProcess 직접 실행 불가 → cmd /c 경유.
        command = [exe, "--permission-mode", "acceptEdits", "-p"]
        if exe.lower().endswith((".cmd", ".bat", ".ps1")):
            base = exe[:-4] + ".cmd" if exe.lower().endswith(".ps1") else exe
            command = ["cmd", "/c", base, "--permission-mode", "acceptEdits", "-p"]
        stdin_payload = prompt
        shell = False
    # subprocess.run은 블로킹이라 실행 중 중지 요청을 볼 수 없다. 그러면 사용자가
    # 중지를 눌러도 에이전트가 타임아웃(기본 30분)까지 레포를 계속 고친다.
    # → Popen + 폴링으로 중지를 감시하고, 요청 시 프로세스 트리를 죽인다.
    try:
        proc = subprocess.Popen(command, cwd=repo_path, shell=shell,
                                stdin=subprocess.PIPE if stdin_payload else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except (OSError, FileNotFoundError) as error:
        return {"ok": False, "output": "", "error": f"에이전트 실행 실패: {error}"}

    box: dict = {}

    def pump():
        try:
            box["out"] = proc.communicate(input=stdin_payload)
        except Exception as e:  # noqa: BLE001 — 종료 경합 시 파이프 오류는 무시
            box["out"] = ("", str(e))

    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        if should_cancel is not None and should_cancel():
            _kill_tree(proc)
            worker.join(timeout=5)
            return {"ok": False, "output": "", "error": "중지됨(사용자 요청)",
                    "cancelled": True}
        if time.monotonic() > deadline:
            _kill_tree(proc)
            worker.join(timeout=5)
            return {"ok": False, "output": "", "error": f"에이전트 타임아웃({timeout}s)"}
        worker.join(timeout=0.25)
    stdout, stderr = box.get("out", ("", ""))
    output = (stdout or "") + (stderr or "")
    (session_dir / "agent-output.log").write_text(output, encoding="utf-8")
    code = proc.returncode
    return {"ok": code == 0, "output": output[-4000:],
            "error": None if code == 0 else f"exit={code}"}
