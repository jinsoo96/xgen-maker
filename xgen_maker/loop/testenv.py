"""대상 저장소의 테스트를 실제로 돌린다 — 빠진 의존성을 격리 캐시에 깔아서.

"의존성이 없어서 못 돈다"는 결론이 아니라 문제다. 샌드박스가 있는 이유가 바로
격리된 곳에 필요한 걸 깔고 돌리기 위해서다.

현실: 대상 저장소의 사설 의존성(사내 SDK)은 대개 이 환경에 이미 깔려 있고, 빠진 건
psutil·aiosmtplib 같은 평범한 PyPI 패키지뿐이다. 그건 깔 수 있다.

방식:
- 저장소별 캐시 디렉토리(~/.xgen-maker/testdeps/<repo>)에 pip install --target.
- 그 디렉토리를 PYTHONPATH 앞에 붙여 pytest를 돌린다(현재 env의 xgen-sdk 등은 그대로 상속).
- 수집이 ModuleNotFoundError로 깨지면 그 모듈을 캐시에 깔고 다시 돌린다(횟수 제한).
- 사설 패키지(PyPI에 없는 것)는 설치가 실패하고, 그건 정직하게 사유로 남긴다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_MISSING = re.compile(r"No module named ['\"]([\w.]+)['\"]")
# 흔히 최상위 임포트명과 배포명이 다른 것들 — 자동설치가 헛돌지 않게 매핑
_PYPI_NAME = {
    "yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python", "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn", "dotenv": "python-dotenv", "jose": "python-jose",
    "dateutil": "python-dateutil", "OpenSSL": "pyopenssl",
}
# 로컬에 없는 게 당연한 것들 — 자동설치를 시도하지 않는다(사설·네임스페이스)
_SKIP_INSTALL = ("xgen", "xgen_sdk", "service", "controller", "editor", "app", "src")


def _cache_dir(repo: str) -> Path:
    base = Path.home() / ".xgen-maker" / "testdeps" / re.sub(r"[^\w.-]", "_", repo)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _pip_install(target: Path, package: str, timeout: int) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "--target", str(target), package],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def run_pytest_with_deps(repo: str, repo_root: str | Path, changed: list[str],
                         timeout: int = 600, max_installs: int = 8) -> dict:
    """pytest를 돌리되, 빠진 PyPI 의존성은 캐시에 깔아 가며 실제로 실행한다.

    반환 {name, status(passed|failed|skipped), reason?, installed[], collected?, output}.
    """
    repo_root = Path(repo_root)
    py_changed = [f for f in changed if f.endswith(".py")]
    if not py_changed:
        return {"name": "pytest", "status": "skipped", "reason": "py 변경 없음", "kind": "na"}
    if not ((repo_root / "tests").is_dir() or (repo_root / "pytest.ini").exists()
            or (repo_root / "conftest.py").exists()):
        return {"name": "pytest", "status": "skipped", "reason": "테스트 폴더 없음", "kind": "na"}

    deps = _cache_dir(repo)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(deps) + os.pathsep + env.get("PYTHONPATH", "")
    installed: list[str] = []
    tried: set[str] = set()

    for _ in range(max_installs + 1):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--continue-on-collection-errors",
                 "-p", "no:cacheprovider"],
                cwd=str(repo_root), env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"name": "pytest", "status": "skipped", "kind": "env",
                    "reason": f"타임아웃({timeout}s) — 스위트가 너무 큽니다",
                    "installed": installed}
        except (OSError, subprocess.SubprocessError) as e:
            return {"name": "pytest", "status": "skipped", "kind": "env",
                    "reason": f"실행 불가: {e}", "installed": installed}
        out = proc.stdout + proc.stderr

        # 빠진 PyPI 모듈이 있으면 깔고 다시 돈다
        missing = _next_installable(out, tried)
        if missing:
            tried.add(missing)
            pkg = _PYPI_NAME.get(missing, missing)
            if _pip_install(deps, pkg, timeout):
                installed.append(pkg)
                continue
            # 설치 실패(사설 등) — 더 시도해도 같으니 그대로 판정으로 넘어간다
        return _classify(proc, out, installed)

    return _classify(proc, out, installed)


def _next_installable(output: str, tried: set[str]) -> str | None:
    for mod in _MISSING.findall(output):
        top = mod.split(".")[0]
        if top in tried or top in _SKIP_INSTALL:
            continue
        return top
    return None


def _collected_count(output: str) -> int:
    m = re.search(r"(\d+)\s+(?:passed|failed|error|deselected|skipped)", output)
    return int(m.group(1)) if m else 0


def _classify(proc, output: str, installed: list[str]) -> dict:
    tail = output[-1500:]
    code = proc.returncode
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    npass = int(passed.group(1)) if passed else 0
    nfail = int(failed.group(1)) if failed else 0
    base = {"name": "pytest", "installed": installed, "passed": npass, "failed": nfail}
    if nfail:
        return {**base, "status": "failed", "output": tail}
    if npass:
        return {**base, "status": "passed", "output": tail[-400:]}
    if code == 5:                                   # pytest: 수집된 테스트 없음
        return {**base, "status": "skipped", "kind": "na", "reason": "수집된 테스트 없음"}
    # 통과도 실패도 아니고(수집 자체가 사설 의존성으로 다 깨진 경우) — 정직하게 남긴다
    still = _next_installable(output, set())
    reason = (f"'{still}' 등 설치 불가 의존성으로 수집 실패" if still
              else "테스트를 실행하지 못했습니다")
    return {**base, "status": "skipped", "kind": "env", "reason": reason, "output": tail}
