"""Maintainer gating and permission verification."""

import json
import os
import subprocess


class GateKeeper:
    """Verifies that the PR or trigger request comes from an authorized collaborator."""

    ALLOWED_PERMISSIONS = {"admin", "maintain", "write"}

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    def check_user_permission(self, repo: str, username: str) -> bool:
        """Query GitHub API to verify if username has write, maintain, or admin rights."""
        if not self.token or not repo or not username:
            return False

        try:
            cmd = [
                "gh", "api",
                f"repos/{repo}/collaborators/{username}/permission",
                "--jq", ".permission, .role_name"
            ]
            env = os.environ.copy()
            env["GH_TOKEN"] = self.token
            res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
            if res.returncode != 0:
                return False

            tokens = res.stdout.strip().lower().split()
            return any(tok in self.ALLOWED_PERMISSIONS for tok in tokens)
        except Exception:
            return False

    @staticmethod
    def parse_comment_command(comment_body: str | None) -> tuple[str | None, str]:
        """Extract command ('/review' or '/fix') and optional guidance from comment body."""
        if not comment_body:
            return None, ""
        for raw_line in comment_body.splitlines():
            line = raw_line.strip()
            if line.startswith("/review") or line.startswith("/fix"):
                parts = line.split(maxsplit=1)
                cmd = parts[0].lower()
                guidance = parts[1].strip() if len(parts) > 1 else ""
                return cmd, guidance
        return None, ""

    def is_authorized(
        self,
        repo: str,
        event_name: str,
        actor: str,
        pr_author: str,
        comment_body: str | None = None,
    ) -> tuple[bool, str]:
        """Check if current CI trigger is authorized for automated LLM review or fix."""
        if event_name in ["pull_request", "pull_request_target"]:
            # Auto-review if author is a maintainer
            if self.check_user_permission(repo, pr_author):
                return True, f"PR author '{pr_author}' is an authorized maintainer."
            # Or if pusher/actor is a maintainer
            if actor and actor != pr_author and self.check_user_permission(repo, actor):
                return True, f"Actor '{actor}' is an authorized maintainer."
            return False, f"Neither PR author '{pr_author}' nor actor '{actor}' has maintainer write permissions."

        elif event_name in ["issue_comment"]:
            cmd, _ = self.parse_comment_command(comment_body)
            if not cmd:
                return False, "Comment does not contain explicit '/review' or '/fix' trigger command."
            if self.check_user_permission(repo, actor):
                return True, f"Command '{cmd}' requested by authorized maintainer '{actor}'."
            return False, f"Commenter '{actor}' does not have maintainer write permissions."

        return False, f"Unsupported event trigger: '{event_name}'"

