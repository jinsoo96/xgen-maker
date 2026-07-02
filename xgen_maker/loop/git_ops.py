"""⑤ git 조작 — MR-only 안전 가드가 코드로 강제되는 계층.

불변 규칙: 보호 브랜치(develop/main/...)로는 checkout -b 대상도, push 대상도 될 수 없다.
브랜치는 fix/·feature/·refactor/·chore/ prefix만 허용.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import is_allowed_branch, is_protected_branch


class GitOpsError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not (self.path / ".git").exists():
            raise GitOpsError(f"git 저장소가 아님: {self.path}")

    def _run(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(["git", *args], cwd=self.path, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        if check and result.returncode != 0:
            raise GitOpsError(f"git {' '.join(args)} 실패: {result.stderr.strip()}")
        return result.stdout

    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def is_clean(self) -> bool:
        return not self._run("status", "--porcelain").strip()

    def create_branch(self, name: str) -> str:
        if not is_allowed_branch(name):
            raise GitOpsError(
                f"허용되지 않는 브랜치명 '{name}' — fix/·feature/·refactor/·chore/ prefix 필수, "
                f"보호 브랜치 금지")
        self._run("checkout", "-b", name)
        return name

    def checkout(self, name: str) -> None:
        self._run("checkout", name)

    def changed_files(self, base: str = "HEAD") -> list[str]:
        tracked = self._run("diff", "--name-only", base).splitlines()
        untracked = self._run("ls-files", "--others", "--exclude-standard").splitlines()
        return sorted({f.strip() for f in tracked + untracked if f.strip()})

    def diff(self, base: str = "HEAD") -> str:
        return self._run("diff", base)

    def stage_all(self) -> None:
        self._run("add", "-A")

    def staged_files(self, base: str = "HEAD") -> list[str]:
        lines = self._run("diff", "--cached", "--name-only", base).splitlines()
        return sorted({f.strip() for f in lines if f.strip()})

    def staged_diff(self, base: str = "HEAD") -> str:
        return self._run("diff", "--cached", base)

    def commit_all(self, title: str, body: str) -> str:
        self._run("add", "-A")
        message = f"{title}\n\n{body}" if body else title
        self._run("commit", "-m", message)
        return self._run("rev-parse", "HEAD").strip()

    def push(self, branch: str, remote: str = "origin") -> None:
        if is_protected_branch(branch):
            raise GitOpsError(f"보호 브랜치 '{branch}' 푸시는 설계상 불가")
        if not is_allowed_branch(branch):
            raise GitOpsError(f"허용 prefix 밖 브랜치 '{branch}' 푸시 거부")
        current = self.current_branch()
        if current != branch:
            raise GitOpsError(f"현재 브랜치({current})와 푸시 대상({branch}) 불일치")
        self._run("push", "-u", remote, branch)
