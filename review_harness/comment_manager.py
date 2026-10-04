"""PR description and comment history lifecycle manager."""

import json
import os
import re
import subprocess
from pathlib import Path


class CommentManager:
    """Manages sticky review comments, older comment folding, and description tables."""

    def __init__(self, repo: str, pr_number: int, token: str | None = None):
        self.repo = repo
        self.pr_number = pr_number
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    def _gh(self, *args: str) -> str:
        env = os.environ.copy()
        if self.token:
            env["GH_TOKEN"] = self.token
        res = subprocess.run(["gh", *args], env=env, capture_output=True, text=True, check=True)
        return res.stdout

    def update_pr_description(self, change_summary_markdown: str) -> None:
        """Update PR body with deterministic change summary, stripping legacy review blocks."""
        raw_body = self._gh("pr", "view", str(self.pr_number), "--repo", self.repo, "--json", "body", "--jq", ".body")

        start_summary = "<!-- kiro-change-summary:start -->"
        end_summary = "<!-- kiro-change-summary:end -->"
        legacy_start = "<!-- kiro-review:start -->"
        legacy_end = "<!-- kiro-review:end -->"

        def strip_block(text: str, s: str, e: str) -> str:
            if s not in text:
                return text
            head, rest = text.split(s, 1)
            tail = rest.split(e, 1)[1] if e in rest else ""
            return (head.rstrip() + "\n\n" + tail.lstrip()).strip()

        clean_body = strip_block(raw_body, legacy_start, legacy_end)
        clean_body = strip_block(clean_body, start_summary, end_summary)

        updated = change_summary_markdown.strip() + "\n\n" + clean_body.strip()
        updated = updated.strip() + "\n"

        temp_body = Path("/tmp") / f"pr_body_{self.pr_number}.md"
        temp_body.write_text(updated, encoding="utf-8")
        self._gh("pr", "edit", str(self.pr_number), "--repo", self.repo, "--body-file", str(temp_body))

    def fetch_previous_findings(self, current_head: str) -> str | None:
        """Extract Findings section from the most recent review comment of an earlier commit."""
        try:
            out = self._gh(
                "api", "--paginate", f"repos/{self.repo}/issues/{self.pr_number}/comments",
                "--jq", '.[] | select(.user.login == "github-actions[bot]" and (.body | contains("<!-- kiro-review-comment -->"))) | {id, body, created_at}'
            )
        except Exception:
            return None

        comments = [json.loads(line) for line in out.splitlines() if line.strip()]
        # Sort by creation time to ensure strict chronological order
        comments.sort(key=lambda c: c.get("created_at", ""))

        older = [c for c in comments if f"kiro-review-sha:{current_head}" not in c["body"]]
        if not older:
            return None

        latest_prev = older[-1]["body"]
        sha_match = re.search(r"kiro-review-sha:([0-9a-f]{7,40})", latest_prev)
        if not sha_match:
            sha_match = re.search(r"Commit `([0-9a-f]{7,40})`", latest_prev)
        short_sha = sha_match.group(1)[:7] if sha_match else "unknown"

        findings_match = re.search(r"### Findings\n(.*?)(?=\n### |\n---\n|\Z)", latest_prev, re.DOTALL)
        if findings_match and findings_match.group(1).strip():
            return f"Previous review commit: {short_sha}\n\n{findings_match.group(1).strip()}"
        return None

    def post_and_collapse_reviews(self, head_sha: str, review_comment_markdown: str) -> str:
        """Post a new review comment or update current commit's comment, and fold older ones."""
        out = self._gh(
            "api", "--paginate", f"repos/{self.repo}/issues/{self.pr_number}/comments",
            "--jq", '.[] | select(.user.login == "github-actions[bot]" and (.body | contains("<!-- kiro-review-comment -->"))) | {id, body, created_at}'
        )
        comments = [json.loads(line) for line in out.splitlines() if line.strip()]
        comments.sort(key=lambda c: c.get("created_at", ""))

        temp_comment = Path("/tmp") / f"review_comment_{head_sha[:7]}.md"
        temp_comment.write_text(review_comment_markdown, encoding="utf-8")

        same_commit = [c for c in comments if f"kiro-review-sha:{head_sha}" in c["body"]]
        if same_commit:
            target_id = same_commit[-1]["id"]
            self._gh("api", "-X", "PATCH", f"repos/{self.repo}/issues/comments/{target_id}", "-F", f"body=@{temp_comment}")
        else:
            res = self._gh("api", "-X", "POST", f"repos/{self.repo}/issues/{self.pr_number}/comments", "-F", f"body=@{temp_comment}")
            target_id = json.loads(res)["id"]

        # Fold older reviews into collapsible <details>
        for c in comments:
            cid = c["id"]
            body = c["body"]
            if cid == target_id or "<!-- kiro-review-collapsed -->" in body:
                continue

            sha_m = re.search(r"kiro-review-sha:([0-9a-f]{7,40})", body)
            if not sha_m:
                sha_m = re.search(r"Commit `([0-9a-f]{7,40})`", body)
            short = sha_m.group(1)[:7] if sha_m else "unknown"

            verdict_m = re.search(r"VERDICT: (\w+)", body)
            verdict = verdict_m.group(1) if verdict_m else "unknown"

            counts_m = re.search(r"critical \d+ · major \d+ · minor \d+ · nit \d+", body)
            counts_str = counts_m.group(0) if counts_m else ""

            inner = re.sub(r"<!-- kiro-review-(comment|sha:[0-9a-f]+) -->\n?", "", body)
            title = f"📜 이전 리뷰 · commit `{short}` · {verdict}"
            if counts_str:
                title += f" · {counts_str}"

            collapsed = (
                "<!-- kiro-review-comment -->\n"
                + (f"<!-- kiro-review-sha:{sha_m.group(1)} -->\n" if sha_m else "")
                + "<!-- kiro-review-collapsed -->\n"
                f"<details>\n<summary>{title}</summary>\n\n{inner.strip()}\n\n</details>\n"
            )

            temp_fold = Path("/tmp") / f"fold_{cid}.md"
            temp_fold.write_text(collapsed, encoding="utf-8")
            try:
                self._gh("api", "-X", "PATCH", f"repos/{self.repo}/issues/comments/{cid}", "-F", f"body=@{temp_fold}")
            except subprocess.CalledProcessError as exc:
                print(f"::warning::Could not fold review comment {cid}: {exc.stderr.strip()}")

        return str(target_id)

    def post_fix_comment(self, body: str, engine_name: str, head_sha: str) -> str:
        """Post AI fix suggestions as a dedicated comment on the PR."""
        marker = f"<!-- kiro-fix-comment -->\n<!-- kiro-fix-sha:{head_sha} -->\n"
        header = f"## 🛠️ AI Fix Suggestions ({engine_name.upper()})\n\n"
        full_comment = marker + header + body
        temp_file = Path("/tmp") / f"fix_comment_{self.pr_number}.md"
        temp_file.write_text(full_comment, encoding="utf-8")
        out = self._gh("pr", "comment", str(self.pr_number), "--repo", self.repo, "--body-file", str(temp_file))
        return out

