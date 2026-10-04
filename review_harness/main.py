"""Main orchestrator for LLM PR Reviewer."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .adapters.agy import AgyAdapter
from .adapters.claude import ClaudeAdapter
from .adapters.codex import CodexAdapter
from .adapters.kiro import KiroAdapter
from .comment_manager import CommentManager
from .diff_guard import DiffGuard
from .gate import GateKeeper
from .prompt import build_fix_prompt, build_review_prompt, generate_tokens, sanitize_and_parse_review
from .state import StateManager


AVAILABLE_ADAPTERS = {
    "kiro": KiroAdapter(),
    "codex": CodexAdapter(),
    "agy": AgyAdapter(),
    "claude": ClaudeAdapter(),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-engine LLM PR Reviewer")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr", type=int, default=int(os.environ.get("PR_NUMBER", "0") or "0"))
    parser.add_argument("--base", default=os.environ.get("BASE_SHA", ""))
    parser.add_argument("--head", default=os.environ.get("HEAD_SHA", ""))
    parser.add_argument("--actor", default=os.environ.get("GITHUB_ACTOR", ""))
    parser.add_argument("--pr-author", default=os.environ.get("PR_AUTHOR", ""))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", "pull_request"))
    parser.add_argument("--comment-body", default=os.environ.get("COMMENT_BODY", ""))
    parser.add_argument("--workspace", default=os.environ.get("GITHUB_WORKSPACE", "."))
    parser.add_argument("--engines", default="kiro,codex,agy,claude")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--state-file", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Auto-resolve missing SHAs or PR author (useful for issue_comment triggers)
    if (not args.base or not args.head or not args.pr_author) and args.repo and args.pr:
        try:
            cmd = ["gh", "pr", "view", str(args.pr), "--repo", args.repo, "--json", "baseRefOid,headRefOid,author"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            info = json.loads(res.stdout)
            args.base = args.base or info.get("baseRefOid", "")
            args.head = args.head or info.get("headRefOid", "")
            if not args.pr_author and "author" in info:
                args.pr_author = info["author"].get("login", "")
        except Exception as exc:
            print(f"::warning::Failed to auto-resolve PR SHAs: {exc}")

    if not args.repo or not args.pr or not args.base or not args.head:
        print("Error: Missing required PR arguments (--repo, --pr, --base, --head)", file=sys.stderr)
        return 1

    # 1. Maintainer Gating Check
    gate = GateKeeper()
    authorized, reason = gate.is_authorized(
        repo=args.repo,
        event_name=args.event_name,
        actor=args.actor,
        pr_author=args.pr_author,
        comment_body=args.comment_body,
    )
    if not authorized:
        print(f"Skipping LLM review: {reason}")
        return 0
    print(f"Authorization confirmed: {reason}")

    cmd, guidance = GateKeeper.parse_comment_command(args.comment_body)
    is_fix_mode = (cmd == "/fix")

    # 2. Diff Extraction & Safety Guard
    diff_guard = DiffGuard(workspace=args.workspace, base_sha=args.base, head_sha=args.head)
    try:
        diff_content = diff_guard._git("diff", "--no-ext-diff", "--no-textconv", f"{args.base}...{args.head}")
    except Exception as exc:
        print(f"Failed to generate diff: {exc}", file=sys.stderr)
        return 1

    safe, message = diff_guard.verify_safety(diff_content)
    if not safe:
        print(f"Diff security check failed: {message}", file=sys.stderr)
        return 1
    print(f"Diff verified: {message}")

    # 3. Update PR Description with Change Summary
    comment_mgr = CommentManager(repo=args.repo, pr_number=args.pr)
    try:
        summary_table = diff_guard.build_change_summary()
        comment_mgr.update_pr_description(summary_table)
        print("Updated PR description with change summary.")
    except Exception as exc:
        print(f"::warning::Could not update PR description: {exc}")

    # 4. Fetch Previous Findings
    previous_findings = comment_mgr.fetch_previous_findings(args.head)

    # 5. Build Delimited Prompt
    delimiter, summary_key = generate_tokens()
    sensitive_paths = diff_guard.get_sensitive_paths()
    if is_fix_mode:
        prompt = build_fix_prompt(
            diff=diff_content,
            delimiter=delimiter,
            summary_key=summary_key,
            previous_findings=previous_findings,
            user_guidance=guidance,
            sensitive_paths=sensitive_paths,
        )
    else:
        prompt = build_review_prompt(
            diff=diff_content,
            delimiter=delimiter,
            summary_key=summary_key,
            previous_findings=previous_findings,
            user_guidance=guidance,
            sensitive_paths=sensitive_paths,
        )


    # 6. Engine Selection & Fallback Loop
    requested_engines = [e.strip() for e in args.engines.split(",") if e.strip() in AVAILABLE_ADAPTERS]
    state_mgr = StateManager(state_file_path=args.state_file)
    candidate_engines = state_mgr.prioritize_engines(requested_engines)

    chosen_engine = None
    cleaned_review = None
    counts = None
    work_dir = Path("/tmp") / f"review_{args.pr}_{args.head[:7]}"
    work_dir.mkdir(parents=True, exist_ok=True)

    for eng_name in candidate_engines:
        adapter = AVAILABLE_ADAPTERS[eng_name]
        if not adapter.detect():
            print(f"Engine '{eng_name}' is not installed or not authenticated; skipping.")
            continue

        print(f"Attempting review with engine: {eng_name}...")
        raw_output, retcode = adapter.run(
            prompt=prompt,
            work_dir=str(work_dir),
            model=args.model,
            effort=args.effort,
        )

        review_text, parsed_counts, parse_err = sanitize_and_parse_review(raw_output, summary_key)
        if retcode == 0 and not parse_err:
            chosen_engine = eng_name
            cleaned_review = review_text
            counts = parsed_counts
            state_mgr.record_success(eng_name)
            print(f"Engine '{eng_name}' completed review successfully.")
            break
        else:
            err_kind = adapter.classify_error(raw_output, retcode)
            reason_msg = parse_err or f"exited with code {retcode} ({err_kind})"
            print(f"::warning::Engine '{eng_name}' failed ({reason_msg}). Trying fallback...")

    if not chosen_engine or not cleaned_review or not counts:
        print("All candidate LLM review engines failed or produced invalid summaries.", file=sys.stderr)
        return 1

    if is_fix_mode:
        try:
            comment_mgr.post_fix_comment(body=cleaned_review, engine_name=chosen_engine, head_sha=args.head)
            print(f"AI fix suggestions posted for commit {args.head[:7]}.")
        except Exception as exc:
            print(f"Failed to post fix comment: {exc}", file=sys.stderr)
            return 1
        return 0

    # 7. Classify Decision
    blocking = counts.get("critical", 0) + counts.get("major", 0)
    decision = "request_changes" if blocking > 0 else "approve"
    heading = "BLOCKED — changes requested" if decision == "request_changes" else "APPROVED"
    icon = "🛑" if decision == "request_changes" else "✅"
    verdict = "request_changes" if decision == "request_changes" else "approve"
    counts_line = " · ".join(f"{k} {counts[k]}" for k in ["critical", "major", "minor", "nit"])

    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server_url}/{args.repo}/actions/runs/{run_id}"

    comment_body = (
        "<!-- kiro-review-comment -->\n"
        f"<!-- kiro-review-sha:{args.head} -->\n"
        f"## 🤖 AI Review ({chosen_engine.upper()})\n\n"
        f"{cleaned_review}\n\n"
        "### Verdict\n"
        f"{icon} **{heading}** (`VERDICT: {verdict}`)  \n"
        f"{counts_line}\n\n"
        "---\n"
        f"<sub>Commit `{args.head[:7]}` · engine `{chosen_engine}` · [workflow run]({run_url}) · 새 커밋이 push되면 이 코멘트가 갱신됩니다.</sub>\n"
    )

    # 8. Post Comment and Collapse Older Ones
    try:
        comment_mgr.post_and_collapse_reviews(head_sha=args.head, review_comment_markdown=comment_body)
        print("Review comment posted and previous reviews collapsed.")
    except Exception as exc:
        print(f"Failed to post review comment: {exc}", file=sys.stderr)
        return 1

    # 9. Gate Block if Changes Requested
    if decision == "request_changes":
        print(f"Review blocked PR with {blocking} critical/major findings.")
        try:
            comment_mgr._gh(
                "pr", "review", str(args.pr), "--repo", args.repo,
                "--request-changes",
                "--body", f"AI review ({chosen_engine.upper()}) detected {blocking} critical/major issues. Please address findings."
            )
        except Exception as exc:
            print(f"::warning::Could not submit request-changes review: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
