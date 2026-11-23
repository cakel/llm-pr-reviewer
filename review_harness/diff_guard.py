"""Diff security verification and change summary generator."""

import base64
import os
import re
import subprocess
from pathlib import Path


class DiffGuard:
    """Enforces boundaries on untrusted diffs and produces change summaries."""

    DEFAULT_MAX_DIFF_BYTES = 200_000          # per review call; a bigger diff is split into parts
    DEFAULT_MAX_TOTAL_DIFF_BYTES = 1_000_000  # whole review; above it the review is skipped (skipped_oversize)

    # Base64 encoded Python regex:
    # "kiro_review_summary_json|self_review_summary_json|end untrusted pull request diff|ignore\s+(all|any|previous)\s+instructions|disregard\s+(all|any|previous)|system\s+prompt"
    CONTROL_PATTERN_B64 = (
        "a2lyb19yZXZpZXdfc3VtbWFyeV9qc29ufHNlbGZfcmV2aWV3X3N1bW1hcnlfanNvbnxlbmQgdW50cn"
        "VzdGVkIHB1bGwgcmVxdWVzdCBkaWZmfGlnbm9yZVxzKyhhbGx8YW55fHByZXZpb3VzKVxzK2luc3Ry"
        "dWN0aW9uc3xkaXNyZWdhcmRccysoc3lzdGVtfGFsbHxhbnl8cHJldmlvdXMpfHN5c3RlbVxzK3Byb2"
        "1wdA=="
    )

    SENSITIVE_PATTERNS = [
        r"^\.github/workflows/",
        r"^/etc/",
        r"\.service$",
        r"^\.env",
        r"\.env\.",
        r"(secret|credential|token|id_rsa|id_ed25519)",
    ]

    def __init__(self, workspace: str, base_sha: str, head_sha: str):
        self.workspace = workspace
        self.base_sha = base_sha
        self.head_sha = head_sha
        raw_pattern = base64.b64decode(self.CONTROL_PATTERN_B64).decode("utf-8")
        self.control_pattern = re.compile(raw_pattern, re.IGNORECASE)

    # --head values meaning "the working tree" (local review of uncommitted changes)
    WORKTREE_HEADS = ("WORKTREE", "")

    @property
    def is_worktree(self) -> bool:
        return self.head_sha is None or self.head_sha in self.WORKTREE_HEADS

    def _range(self) -> list[str]:
        """Revision arguments for every `git diff` here: `<base>` for the working tree, else `<base>...<head>`."""
        return [self.base_sha] if self.is_worktree else [f"{self.base_sha}...{self.head_sha}"]

    def untracked_files(self) -> list[str]:
        """New files not yet added (and not ignored); only the working tree has them."""
        if not self.is_worktree:
            return []
        out = self._git("ls-files", "--others", "--exclude-standard", "-z")
        # A nested repository is listed as 'dir/'; it is not a file to diff
        return [p for p in out.split("\0") if p and not p.endswith("/")]

    def get_diff(self, max_untracked_bytes: int | None = None) -> str:
        """The diff to review. For the working tree it also covers untracked files, as new-file diffs.

        An untracked file bigger than `max_untracked_bytes` raises ValueError before it is read: the review
        would refuse that diff anyway, and a stray dataset or build output must not be loaded into memory.
        """
        diff = self._git("diff", "--no-ext-diff", "--no-textconv", *self._range())
        for path in self.untracked_files():
            if max_untracked_bytes is not None:
                size = os.lstat(os.path.join(self.workspace, path)).st_size   # a symlink is diffed as its target path
                if size > max_untracked_bytes:
                    raise ValueError(f"untracked file '{path}' is {size:,} bytes > {max_untracked_bytes:,}; "
                                     "add it to .gitignore or move it out of the working tree")
            # --no-index exits 1 when the files differ, which is always the case against /dev/null
            diff += self._git("diff", "--no-index", "--no-ext-diff", "--no-textconv", "--", "/dev/null", path,
                              ok_codes=(0, 1))
        return diff

    def changed_paths(self) -> list[str]:
        """Every path the diff touches, untracked files included."""
        names = self._git("diff", "--name-only", "-z", *self._range())
        return [p for p in names.split("\0") if p] + self.untracked_files()

    def get_sensitive_paths(self) -> list[str]:
        """Detect any modified files matching security-sensitive paths.

        A git error propagates: returning [] would silently drop the sensitive-file alert.
        """
        sensitive = []
        for f in self.changed_paths():
            for pattern in self.SENSITIVE_PATTERNS:
                if re.search(pattern, f, re.IGNORECASE):
                    sensitive.append(f)
                    break
        return sensitive

    def _git(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
        }
        res = subprocess.run(
            ["git", "-C", self.workspace, "-c", "core.attributesFile=/dev/null",
             "-c", "diff.external=", "-c", "diff.textconv=", *args],
            env=env,
            capture_output=True,
            text=True,
        )
        if res.returncode not in ok_codes:
            raise subprocess.CalledProcessError(res.returncode, res.args, output=res.stdout, stderr=res.stderr)
        return res.stdout

    def verify_safety(self, diff_content: str, max_bytes: int = DEFAULT_MAX_DIFF_BYTES) -> tuple[bool, str]:
        """Verify the diff against prompt injection and attribute hijacking."""
        # 1. Size check
        size = len(diff_content.encode("utf-8"))
        if size > max_bytes:
            return False, f"Diff size exceeds maximum safety limit ({size} > {max_bytes} bytes)"
        if not diff_content.strip():
            return False, "Diff is empty; refusing review."

        # 2. Submodule check
        raw_diff = self._git("diff", "--raw", *self._range())
        if re.search(r"^:160000 ", raw_diff, re.MULTILINE):
            return False, "Submodule change detected; refusing automated review."

        # 3. Attributes check (reject PRs modifying .gitattributes)
        for path in self.changed_paths():
            if path.strip().endswith(".gitattributes"):
                return False, "PR modifies .gitattributes; refusing automated review."

        # 4. Review control pattern in added lines
        for line in diff_content.splitlines():
            # Check additions, skip diff headers and context
            if line.startswith("+") and not line.startswith("+++"):
                added_text = line[1:]
                if self.control_pattern.search(added_text):
                    return False, f"Review control marker pattern detected in added line: '{added_text[:60]}...'"

        return True, "Diff passed safety verification."

    def build_change_summary(self, limit: int = 50) -> str:
        """Construct the numstat Markdown table for PR description."""
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
        }
        res = subprocess.run(
            ["git", "-C", self.workspace, "-c", "core.attributesFile=/dev/null",
             "-c", "diff.external=", "-c", "diff.textconv=", "diff",
             "--no-ext-diff", "--no-textconv", "--numstat", "-z",
             *self._range()],
            env=env,
            capture_output=True,
            check=True,
        )
        tokens = res.stdout.decode("utf-8", errors="replace").split("\0")
        rows = []
        i = 0
        while i < len(tokens) and tokens[i]:
            parts = tokens[i].split("\t", 2)
            if len(parts) < 3:
                i += 1
                continue
            added, deleted, path = parts
            if path == "" and i + 2 < len(tokens):
                path = f"{tokens[i+1]} -> {tokens[i+2]}"
                i += 2
            i += 1
            is_binary = added == "-"
            rows.append((path, 0 if is_binary else int(added), 0 if is_binary else int(deleted), is_binary))

        def esc(val: str) -> str:
            return val.replace("`", "'").replace("|", "\\|").replace("\n", " ")

        total_add = sum(r[1] for r in rows)
        total_del = sum(r[2] for r in rows)
        short_head = self.head_sha[:7]

        lines = [
            "<!-- kiro-change-summary:start -->",
            "## 변경 요약",
            "",
            f"**{len(rows)}개 파일** 변경 · `+{total_add}` / `-{total_del}`  ",
            f"**Head:** `{short_head}`",
        ]

        sensitive_files = self.get_sensitive_paths()
        if sensitive_files:
            lines.append("")
            lines.append("> ⚠️ **보안 주의 경로 감지**: CI/인프라/인증 핵심 파일이 변경되었습니다. 인간 메인테이너의 직접 확인이 필요합니다.")
            for sf in sensitive_files[:5]:
                lines.append(f"> - `{esc(sf)}`")
            if len(sensitive_files) > 5:
                lines.append(f"> - … 외 {len(sensitive_files) - 5}개 파일")

        lines.extend([
            "",
            "<details open>",
            "<summary>변경된 파일</summary>",
            "",
            "| 파일 | 추가 | 삭제 |",
            "| --- | ---: | ---: |",
        ])
        for path, a, d, is_binary in rows[:limit]:
            lines.append(f"| `{esc(path)}` | {'binary' if is_binary else a} | {'' if is_binary else d} |")
        if len(rows) > limit:
            lines.append(f"| … 외 {len(rows) - limit}개 파일 | | |")
        lines.extend(["", "</details>", "<!-- kiro-change-summary:end -->"])
        return "\n".join(lines)
