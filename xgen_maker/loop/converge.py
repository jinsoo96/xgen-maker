"""수렴 루프 — "통과할 때까지 스스로 무는" /goal 하네스의 핵심.

xgen-harness(PyPI)를 임포트해 그 수렴 계약과 샌드박스를 차용한다:
- decide 계약: 엔진 core/pipeline.py의 loop_decision(continue/retry/stop) 의미론을 그대로.
- 샌드박스: xgen_harness.run_sandboxed 로 변경 코드를 격리 실행 검증(사장님이 말한 "샌드박스 테스트").
xgen-harness가 없으면 자체 폴백(격리 없이 로컬 검증)으로 degrade — MAKER는 하드 의존 안 함.

루프: 구현(claude, 실패 피드백 되먹임) → 샌드박스+checks 검증 → judge → decide.
retry면 실패 사유를 다음 프롬프트에 실어 재구현. stop이면 수렴.
"""
from __future__ import annotations

from pathlib import Path

from .implement import build_prompt, run_agent, RULES
from .testing import run_checks
from .judge import judge

# 엔진 임포트 — xgen-sdk가 하네스 엔진을 흡수했으므로 xgen_sdk.harness 우선,
# 없으면 standalone xgen_harness, 둘 다 없으면 로컬 폴백(코어 의존성 0 유지).
_xh = None
HAS_HARNESS = False
HARNESS_VERSION = None
HARNESS_SOURCE = None
for _mod, _label in (("xgen_sdk.harness", "xgen-sdk"), ("xgen_harness", "xgen-harness")):
    try:
        _xh = __import__(_mod, fromlist=["run_sandboxed"])
        HAS_HARNESS = True
        HARNESS_VERSION = getattr(_xh, "__version__", "?")
        HARNESS_SOURCE = _label
        break
    except Exception:  # noqa: BLE001 — 임포트 실패는 다음 후보/폴백
        continue


def sandbox_verify_python(repo_path: Path, changed: list[str],
                          timeout: float = 20.0) -> dict:
    """변경된 .py를 xgen-harness 샌드박스에서 격리 컴파일 검증. 엔진 없으면 skip."""
    py_files = [f for f in changed if f.endswith(".py") and (repo_path / f).is_file()]
    if not py_files:
        return {"name": "sandbox_py", "status": "skipped", "reason": "py 변경 없음"}
    if not HAS_HARNESS:
        return {"name": "sandbox_py", "status": "skipped",
                "reason": "xgen-harness 미설치 — 로컬 checks로 대체"}
    abs_paths = [str((repo_path / f).resolve()) for f in py_files]
    code = (
        "import py_compile, sys\n"
        f"files = {abs_paths!r}\n"
        "bad = []\n"
        "for f in files:\n"
        "    try:\n"
        "        py_compile.compile(f, doraise=True)\n"
        "    except Exception as e:\n"
        "        bad.append(f'{f}: {e}')\n"
        "result = bad\n"
        "sys.exit(1 if bad else 0)\n"
    )
    try:
        res = _xh.run_sandboxed(code, timeout_sec=timeout)
    except Exception as error:  # noqa: BLE001
        return {"name": "sandbox_py", "status": "skipped", "reason": f"샌드박스 실행불가: {error}"}
    if res.timed_out:
        return {"name": "sandbox_py", "status": "skipped", "reason": "샌드박스 타임아웃"}
    ok = res.exit_code == 0
    return {"name": "sandbox_py",
            "status": "passed" if ok else "failed",
            "isolated": True, "harness": HARNESS_VERSION, "engine": HARNESS_SOURCE,
            "bad": res.return_value if not ok else []}


def decide(checks: dict, sandbox: dict, judge_result: dict | None,
           iteration: int, max_iterations: int) -> str:
    """엔진 loop_decision 계약: continue(더 시도)/retry(고치고 재시도)/stop(수렴/포기)."""
    if sandbox["status"] == "failed":
        return "retry" if iteration < max_iterations else "stop"
    if checks["blocked"]:
        return "retry" if iteration < max_iterations else "stop"
    if judge_result is not None and not judge_result["passed"]:
        return "retry" if iteration < max_iterations else "stop"
    return "stop"  # 전부 통과 → 수렴


def _feedback(checks: dict, sandbox: dict, judge_result: dict | None) -> str:
    lines = ["[이전 시도가 실패했다 — 아래 문제를 고쳐서 다시 구현하라]"]
    if sandbox["status"] == "failed":
        lines.append("● 샌드박스 구문검증 실패: " +
                     "; ".join(str(b) for b in (sandbox.get("bad") or []))[:600])
    for row in checks.get("checks", []):
        if row["status"] == "failed":
            lines.append(f"● {row['name']} 실패: {str(row.get('output',''))[:600]}")
    if judge_result is not None and not judge_result["passed"]:
        reasons = "; ".join(judge_result.get("reasons", []))
        lines.append(f"● 품질 게이트 미달(judge {judge_result.get('score')} < "
                     f"{judge_result.get('theta')}): {reasons}")
    return "\n".join(lines)


def converge(config, repo_path: Path, repo: str, query: str, intent: str,
             landing: list, chain: list, legacy_notes: str,
             base_branch: str, repo_git, journal, cost=None) -> dict:
    """수렴 루프 실행. 반환 {converged, iterations, checks, sandbox, judge, changed, diff}."""
    max_iterations = max(1, getattr(config, "max_iterations", 3))
    feedback = ""
    last = {"converged": False, "iterations": 0}

    for iteration in range(1, max_iterations + 1):
        prompt = build_prompt(query, intent, landing, legacy_notes, chain=chain)
        if feedback:
            prompt += "\n\n" + feedback
        agent_result = run_agent(repo_path, prompt, journal.dir,
                                 config.agent_cmd, config.agent_timeout)
        if cost is not None:
            cost.add_agent(prompt, agent_result.get("output", ""))
        if not agent_result["ok"]:
            journal.event("iteration", "fail", n=iteration, phase="implement",
                          error=agent_result.get("error"))
            last.update({"iterations": iteration, "agent_error": agent_result.get("error")})
            return {**last, "converged": False, "stopped": "implement_failed"}

        repo_git.stage_all()
        changed = repo_git.staged_files(base_branch)
        diff_text = repo_git.staged_diff(base_branch)

        sandbox = sandbox_verify_python(repo_path, changed)
        checks = run_checks(repo_path, changed, test_timeout=config.check_timeout)
        judge_result = None
        if sandbox["status"] != "failed" and not checks["blocked"]:
            judge_result = judge(config, query, diff_text, changed,
                                 checks=checks["summary"])

        decision = decide(checks, sandbox, judge_result, iteration, max_iterations)
        journal.event("iteration",
                      "pass" if decision == "stop" and (judge_result or {}).get("passed", sandbox["status"] != "failed" and not checks["blocked"]) else decision,
                      n=iteration, sandbox=sandbox["status"],
                      checks=checks["summary"],
                      judge=(judge_result or {}).get("score"), decision=decision)

        last = {"iterations": iteration, "checks": checks, "sandbox": sandbox,
                "judge": judge_result, "changed": changed, "diff": diff_text,
                "agent_summary": agent_result["output"][:500]}

        if decision == "stop":
            converged = (sandbox["status"] != "failed" and not checks["blocked"]
                         and (judge_result is None or judge_result["passed"]))
            return {**last, "converged": converged,
                    "stopped": "converged" if converged else "max_iterations"}
        feedback = _feedback(checks, sandbox, judge_result)  # 다음 회차로 되먹임

    return {**last, "converged": False, "stopped": "max_iterations"}
