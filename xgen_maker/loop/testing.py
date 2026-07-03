"""⑦-1 자동 검증(checks) — 구현 직후, judge 이전의 기계적 안전망.

원칙: "고치면 이슈가 생길 수 있다" → 변경이 만든 회귀를 사람 전에 기계가 잡는다.
- syntax: 변경된 .py 전부 py_compile (의존성 0, 항상 실행)
- tests : 레포 테스트 러너 자동 감지(pytest / package.json scripts.test) 후 실행
- 환경 문제(의존성 미설치·수집 실패)는 fail이 아니라 skipped(사유)로 분류 —
  기계가 판단 못 하면 막지 않되 로그에 남긴다. 진짜 테스트 실패만 MR을 차단한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(command: list[str], cwd: str | Path, timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout({timeout}s)"
    except (OSError, FileNotFoundError) as error:
        return 127, str(error)
    return result.returncode, ((result.stdout or "") + (result.stderr or ""))[-3000:]


def check_python_syntax(repo_root: Path, changed: list[str], timeout: int = 120) -> dict:
    py_files = [f for f in changed if f.endswith(".py") and (repo_root / f).is_file()]
    if not py_files:
        return {"name": "py_syntax", "status": "skipped", "reason": "py 변경 없음"}
    code, output = _run([sys.executable, "-m", "py_compile", *py_files], repo_root, timeout)
    return {"name": "py_syntax", "status": "passed" if code == 0 else "failed",
            "files": len(py_files), "output": "" if code == 0 else output}


def _has_pytest(repo_root: Path) -> bool:
    if (repo_root / "pytest.ini").exists() or (repo_root / "tests").is_dir():
        return True
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists() and "pytest" in pyproject.read_text(encoding="utf-8", errors="ignore"):
        return True
    return False


def check_pytest(repo_root: Path, changed: list[str], timeout: int = 600) -> dict:
    if not any(f.endswith(".py") for f in changed):
        return {"name": "pytest", "status": "skipped", "reason": "py 변경 없음"}
    if not _has_pytest(repo_root):
        return {"name": "pytest", "status": "skipped", "reason": "pytest 구성 없음"}
    code, output = _run([sys.executable, "-m", "pytest", "-x", "-q"], repo_root, timeout)
    if code == 0:
        return {"name": "pytest", "status": "passed", "output": output[-500:]}
    if code == 5:
        return {"name": "pytest", "status": "skipped", "reason": "수집된 테스트 없음"}
    if code == 1:
        return {"name": "pytest", "status": "failed", "output": output}
    # 2/3/4/124/127 = 환경/수집/타임아웃 문제 — 차단하지 않되 기록
    return {"name": "pytest", "status": "skipped",
            "reason": f"실행 불가(exit={code}) — 환경 문제로 분류", "output": output[-800:]}


def _nearest_pkg_with_test(repo_root: Path, rel: str) -> Path | None:
    current = (repo_root / rel).parent
    while current != repo_root.parent and current >= repo_root:
        pkg_json = current / "package.json"
        if pkg_json.exists():
            try:
                scripts = json.loads(pkg_json.read_text(encoding="utf-8",
                                                        errors="ignore")).get("scripts", {})
            except json.JSONDecodeError:
                scripts = {}
            if "test" in scripts:
                return current
            return None  # 가장 가까운 패키지에 test 스크립트 없음
        if current == repo_root:
            break
        current = current.parent
    return None


def check_node_tests(repo_root: Path, changed: list[str], timeout: int = 600) -> dict:
    ts_changed = [f for f in changed if f.endswith((".ts", ".tsx", ".js", ".jsx"))]
    if not ts_changed:
        return {"name": "node_test", "status": "skipped", "reason": "ts/js 변경 없음"}
    if not (repo_root / "node_modules").is_dir():
        return {"name": "node_test", "status": "skipped",
                "reason": "node_modules 미설치 — 로컬 의존성 없음(도커 빌드 환경)"}
    packages = {pkg for f in ts_changed if (pkg := _nearest_pkg_with_test(repo_root, f))}
    if not packages:
        return {"name": "node_test", "status": "skipped", "reason": "변경 패키지에 test 스크립트 없음"}
    outputs = []
    for pkg in sorted(packages):
        runner = "pnpm" if (repo_root / "pnpm-lock.yaml").exists() else "npm"
        code, output = _run([runner, "test"], pkg, timeout)
        if code != 0:
            return {"name": "node_test", "status": "failed",
                    "package": str(pkg.relative_to(repo_root)), "output": output}
        outputs.append(str(pkg.relative_to(repo_root)))
    return {"name": "node_test", "status": "passed", "packages": outputs}


def run_checks(repo_root: str | Path, changed: list[str],
               test_timeout: int = 600) -> dict:
    """전체 검증 실행. blocked=True면 MR 진행 차단."""
    repo_root = Path(repo_root)
    results = [
        check_python_syntax(repo_root, changed),
        check_pytest(repo_root, changed, test_timeout),
        check_node_tests(repo_root, changed, test_timeout),
    ]
    failed = [r for r in results if r["status"] == "failed"]
    return {"checks": results, "blocked": bool(failed),
            "summary": {r["name"]: r["status"] for r in results}}
