import subprocess
import tempfile
import unittest
from pathlib import Path

from xgen_maker.loop.git_ops import GitRepo


def _init(root: Path) -> None:
    for a in (["init", "-b", "trunk"], ["config", "user.email", "repo@local"],
              ["config", "user.name", "RepoDefault"]):
        subprocess.run(["git", *a], cwd=root, capture_output=True)
    (root / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)


def _last(root: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--pretty=%an <%ae>|%cn <%ce>"],
        cwd=root, capture_output=True, text=True, encoding="utf-8").stdout.strip()


class TestCommitAuthor(unittest.TestCase):
    def test_author_forced_overrides_repo_config(self):
        # MAKER가 작업 시 대상 레포 config와 무관하게 저자를 강제하는지(값은 예시 신원)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            (root / "f.txt").write_text("y", encoding="utf-8")
            GitRepo(root).commit_all("fix: x", "본문",
                                     author_name="Work Identity",
                                     author_email="work@example.com")
            author, committer = _last(root).split("|")
            self.assertEqual(author, "Work Identity <work@example.com>")
            self.assertEqual(committer, "Work Identity <work@example.com>")

    def test_no_author_falls_back_to_repo_config(self):
        # 저자 미지정 시 대상 레포 기존 config로 폴백(하위호환)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            (root / "f.txt").write_text("z", encoding="utf-8")
            GitRepo(root).commit_all("chore: x", "")
            author, _ = _last(root).split("|")
            self.assertEqual(author, "RepoDefault <repo@local>")

    def test_partial_author_ignored(self):
        # name만/email만 있으면 강제 안 함(둘 다 있어야 적용)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init(root)
            (root / "f.txt").write_text("w", encoding="utf-8")
            GitRepo(root).commit_all("chore: x", "", author_name="Work Identity")
            author, _ = _last(root).split("|")
            self.assertEqual(author, "RepoDefault <repo@local>")


if __name__ == "__main__":
    unittest.main()
