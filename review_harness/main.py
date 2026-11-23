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
from .prompt import build_fix_prompt, generate_tokens
from .runner import ReviewResult, available_engines, engine_model, review_diff, run_with_fallback
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
    parser.add_argument("--model", default=None, help="Model passed to every engine (unless the engine has its own below)")
    parser.add_argument("--claude-model", default="sonnet",
                        help="Model for the claude engine only (default: sonnet, the latest Sonnet). '' = use --model, "
                             "or Claude Code's own default. --model would also reach kiro/codex/agy, which do not know Claude model names")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--diff-file", default=None, help="Path to pre-verified diff file")
    parser.add_argument("--max-diff-bytes", type=int, default=DiffGuard.DEFAULT_MAX_DIFF_BYTES,
                        help="Largest diff (UTF-8 bytes) sent in one review call; a bigger diff is split by file into parts")
    parser.add_argument("--max-total-diff-bytes", type=int, default=DiffGuard.DEFAULT_MAX_TOTAL_DIFF_BYTES,
                        help="Skip the review (skipped_oversize) above this total diff size; /fix is never split, so it stops at --max-diff-bytes")
    parser.add_argument("--max-parallel", type=int, default=0,
                        help="Cap on parts reviewed at once (0 = 4); the actual number follows CPUs and available memory, re-checked per part")
    parser.add_argument("--output-file", default=None, help="Save review comment markdown to specified file path")
    parser.add_argument("--summary-file", default=None, help="Save signed machine summary JSON to specified file path")
    parser.add_argument("--summary-key", default=os.environ.get("REVIEW_SUMMARY_KEY", None), help="Pre-shared secret key for machine summary signing and verification")
    parser.add_argument("--post-comment", action="store_true", default=True, help="Post review comments directly to GitHub PR")
    parser.add_argument("--no-post-comment", action="store_false", dest="post_comment", help="Do not post comments to GitHub; save output to file only")
    parser.add_argument("--submit-review", action="store_true", default=True, help="Submit formal PR request-changes review on blocking issues")
    parser.add_argument("--no-submit-review", action="store_false", dest="submit_review", help="Skip formal PR review submission and delegate gate to workflow")
    return parser.parse_args()


def build_oversize_comment(base: str, head: str, size: int, limit: int, setting: str = "max-total-diff-bytes") -> str:
    """PR notice when the diff is too large for automated review.

    `setting` names the input that set `limit`: max-total-diff-bytes for a review (bigger diffs are split,
    so raising max-diff-bytes would not help), max-diff-bytes for /fix (never split).
    """
    return (
        "<!-- kiro-review-comment -->\n"
        f"<!-- kiro-review-sha:{head} -->\n"
        "## ⏸️ AI 리뷰 중지 — 변경점이 큽니다 (decision: skipped_oversize)\n\n"
        f"Commit `{head[:7]}` · diff **{size:,} bytes** > 한도 {limit:,} bytes\n\n"
        "**추가 리뷰를 요청하지 마세요.** 코드를 고쳐 push하거나 `/review`를 다시 요청해도 같은 oversize 결과가 나옵니다"
        " (diff는 커밋이 쌓일수록 커집니다). 이것은 코드의 문제가 아니라 리뷰를 실시하지 않았다는 뜻이며, 승인도 아닙니다.\n\n"
        f"필요하면 사람이 결정합니다: PR 분할, 저장소의 `{setting}` 조정, 또는 로컬 리뷰"
        f" (`scripts/local_review.py --base {base[:7]} --head {head[:7]} --max-total-diff-bytes {size}`, llm-pr-reviewer 저장소).\n\n"
        "이전 AI 리뷰는 이 커밋을 보지 않았으므로 접어 두었습니다.\n"
        "<!-- VERDICT: skipped_oversize -->\n"
    )


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
    if args.diff_file and os.path.isfile(args.diff_file):
        try:
            with open(args.diff_file, "r", encoding="utf-8", errors="replace") as f:
                diff_content = f.read()
            print(f"Loaded pre-verified diff from {args.diff_file} ({len(diff_content.encode('utf-8'))} bytes)")
        except Exception as exc:
            print(f"Failed to read diff-file: {exc}", file=sys.stderr)
            return 1
    else:
        try:
            diff_content = diff_guard.get_diff()
        except Exception as exc:
            print(f"Failed to generate diff: {exc}", file=sys.stderr)
            return 1

    size = len(diff_content.encode("utf-8"))
    # A review above --max-diff-bytes is split into parts; --max-total-diff-bytes caps the whole run (time and
    # quota). /fix proposes patches across the whole diff, so it is never split.
    limit = args.max_diff_bytes if is_fix_mode else max(args.max_diff_bytes, args.max_total_diff_bytes)
    if size > limit:
        # Neutral, not a failure: a red check reads as "fix the code and push again", and an agent would
        # retry forever (the diff only grows). decision=skipped_oversize is never an approval, so a gate on
        # `decision` still blocks it. The PR comment says to stop, and folds older reviews so a stale
        # verdict of an earlier commit no longer reads as current.
        print(f"Diff size exceeds limit ({size} > {limit} bytes); review skipped (neutral).")
        notice = build_oversize_comment(args.base, args.head, size, limit,
                                        "max-diff-bytes" if is_fix_mode else "max-total-diff-bytes")
        # The job still ends green, so put "not an approval" on the check itself: a warning annotation
        # next to the ✓ and the notice at the top of the job summary.
        print(f"::warning title=AI 리뷰 중지 (승인 아님)::diff {size:,} bytes > 한도 {limit:,} bytes"
              " — 리뷰를 실시하지 않았습니다. 추가 리뷰를 요청하지 마세요.")
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary:
            try:
                with open(step_summary, "a", encoding="utf-8") as f:
                    f.write(notice)
            except Exception as exc:
                print(f"::warning::Failed to write GITHUB_STEP_SUMMARY: {exc}")
        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            try:
                with open(gh_output, "a", encoding="utf-8") as f:
                    f.write("decision=skipped_oversize\n")
                    f.write("blocking=0\n")
            except Exception as exc:
                print(f"::warning::Failed to write GITHUB_OUTPUT: {exc}")
        if args.post_comment:
            try:
                CommentManager(repo=args.repo, pr_number=args.pr).post_and_collapse_reviews(
                    head_sha=args.head,
                    review_comment_markdown=notice,
                )
            except Exception as exc:
                print(f"::warning::Could not post oversize notice: {exc}")
        return 0

    safe, message = diff_guard.verify_safety(diff_content, max_bytes=limit)
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

    # 5. Review (split into parts above --max-diff-bytes), with engine fallback
    delimiter, generated_key = generate_tokens()
    summary_key = args.summary_key if args.summary_key else generated_key
    sensitive_paths = diff_guard.get_sensitive_paths()

    requested_engines = [e.strip() for e in args.engines.split(",") if e.strip() in AVAILABLE_ADAPTERS]
    state_mgr = StateManager(state_file_path=args.state_file)
    engines = available_engines(state_mgr.prioritize_engines(requested_engines), AVAILABLE_ADAPTERS)

    engine_models = {"claude": args.claude_model} if args.claude_model else {}

    work_dir = Path("/tmp") / f"review_{args.pr}_{args.head[:7]}"
    work_dir.mkdir(parents=True, exist_ok=True)

    if is_fix_mode:
        prompt = build_fix_prompt(
            diff=diff_content,
            delimiter=delimiter,
            summary_key=summary_key,
            previous_findings=previous_findings,
            user_guidance=guidance,
            sensitive_paths=sensitive_paths,
        )
        got = run_with_fallback(prompt, engines, AVAILABLE_ADAPTERS, summary_key, str(work_dir), args.model, args.effort,
                                engine_models=engine_models)
        result = None if not got else ReviewResult(review=got[1], counts=got[2], engines=[got[0]], last_engine=got[0])
    else:
        result = review_diff(
            diff=diff_content,
            part_bytes=args.max_diff_bytes,
            engines=engines,
            adapters=AVAILABLE_ADAPTERS,
            delimiter=delimiter,
            summary_key=summary_key,
            work_dir=str(work_dir),
            model=args.model,
            engine_models=engine_models,
            effort=args.effort,
            previous_findings=previous_findings,
            user_guidance=guidance,
            sensitive_paths=sensitive_paths,
            max_parallel=args.max_parallel,
        )

    if not result or not result.review:
        print("All candidate LLM review engines failed or produced invalid summaries.", file=sys.stderr)
        return 1
    state_mgr.record_success(result.last_engine)
    chosen_engine = ",".join(result.engines)
    cleaned_review = result.review
    counts = result.counts

    # The model each engine that produced the review actually got ("auto" = the CLI's own default)
    model_tag = ",".join(dict.fromkeys(engine_model(e, args.model, engine_models) or "auto" for e in result.engines))
    effort_tag = args.effort if args.effort else "medium"

    if is_fix_mode:
        if args.output_file:
            try:
                Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output_file).write_text(cleaned_review, encoding="utf-8")
                print(f"Saved fix suggestions markdown to {args.output_file}")
            except Exception as exc:
                print(f"::warning::Failed to save output-file: {exc}")

        if args.summary_file:
            try:
                Path(args.summary_file).parent.mkdir(parents=True, exist_ok=True)
                summary_data = {
                    "schema_version": 1,
                    "summary_key": summary_key,
                    "counts": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
                    "blocking": 0,
                    "decision": "fix_suggested",
                    "engine": chosen_engine,
                    "head": args.head,
                }
                Path(args.summary_file).write_text(json.dumps(summary_data, indent=2) + "\n", encoding="utf-8")
                print(f"Saved machine review summary JSON to {args.summary_file}")
            except Exception as exc:
                print(f"::warning::Failed to save summary-file: {exc}")

        if args.post_comment:
            try:
                comment_mgr.post_fix_comment(
                    body=cleaned_review,
                    engine_name=chosen_engine,
                    head_sha=args.head,
                    model=model_tag,
                    effort=effort_tag,
                )
                print(f"AI fix suggestions posted for commit {args.head[:7]}.")
            except Exception as exc:
                print(f"Failed to post fix comment: {exc}", file=sys.stderr)
                return 1
        else:
            print("PR fix comment posting skipped (--no-post-comment).")

        gh_output = os.environ.get("GITHUB_OUTPUT")
        if gh_output:
            try:
                with open(gh_output, "a", encoding="utf-8") as f:
                    f.write("decision=fix_suggested\n")
                    f.write("blocking=0\n")
            except Exception:
                pass
        return 0

    # 7. Classify Decision
    blocking = counts.get("critical", 0) + counts.get("major", 0)
    decision = "request_changes" if blocking > 0 else "approve"
    heading = "BLOCKED — changes requested" if decision == "request_changes" else "APPROVED"
    icon = "🛑" if decision == "request_changes" else "✅"
    verdict = "request_changes" if decision == "request_changes" else "approve"
    counts_line = " · ".join(f"{k} {counts[k]}" for k in ["critical", "major", "minor", "nit"])

    if args.summary_file:
        try:
            Path(args.summary_file).parent.mkdir(parents=True, exist_ok=True)
            summary_data = {
                "schema_version": 1,
                "summary_key": summary_key,
                "counts": counts,
                "blocking": blocking,
                "decision": decision,
                "engine": chosen_engine,
                "head": args.head,
            }
            Path(args.summary_file).write_text(json.dumps(summary_data, indent=2) + "\n", encoding="utf-8")
            print(f"Saved machine review summary JSON to {args.summary_file}")
        except Exception as exc:
            print(f"::warning::Failed to save summary-file: {exc}")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        try:
            with open(gh_output, "a", encoding="utf-8") as f:
                f.write(f"decision={decision}\n")
                f.write(f"blocking={blocking}\n")
                f.write(f"critical_count={counts.get('critical', 0)}\n")
                f.write(f"major_count={counts.get('major', 0)}\n")
        except Exception as exc:
            print(f"::warning::Failed to write GITHUB_OUTPUT: {exc}")

    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server_url}/{args.repo}/actions/runs/{run_id}"

    comment_body = (
        "<!-- kiro-review-comment -->\n"
        f"<!-- kiro-review-sha:{args.head} -->\n"
        f"## 🤖 AI Review ({chosen_engine.upper()} / {model_tag}, {effort_tag})\n\n"
        f"{cleaned_review}\n\n"
        "### Verdict\n"
        f"{icon} **{heading}** (`VERDICT: {verdict}`)  \n"
        f"{counts_line}\n\n"
        "---\n"
        f"<sub>Commit `{args.head[:7]}` · engine `{chosen_engine}` ({model_tag}, {effort_tag})"
        f"{f' · {result.parts}개 묶음으로 분할 리뷰' if result.parts > 1 else ''} · [workflow run]({run_url}) · 새 커밋이 push되면 이 코멘트가 갱신됩니다.</sub>\n"
    )

    if args.output_file:
        try:
            Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_file).write_text(comment_body, encoding="utf-8")
            print(f"Saved review comment markdown to {args.output_file}")
        except Exception as exc:
            print(f"::warning::Failed to save output-file: {exc}")

    # 8. Post Comment and Collapse Older Ones
    if args.post_comment:
        try:
            comment_mgr.post_and_collapse_reviews(head_sha=args.head, review_comment_markdown=comment_body)
            print("Review comment posted and previous reviews collapsed.")
        except Exception as exc:
            print(f"Failed to post review comment: {exc}", file=sys.stderr)
            return 1
    else:
        print("PR review comment posting skipped (--no-post-comment).")

    # 9. Gate Block if Changes Requested
    if decision == "request_changes":
        print(f"Review identified {blocking} critical/major findings (decision: request_changes).")
        if args.submit_review:
            try:
                comment_mgr._gh(
                    "pr", "review", str(args.pr), "--repo", args.repo,
                    "--request-changes",
                    "--body", f"AI review ({chosen_engine.upper()}) detected {blocking} critical/major issues. Please address findings."
                )
            except Exception as exc:
                print(f"::warning::Could not submit request-changes review: {exc}")
            return 1
        else:
            print("Formal PR review submission skipped (--no-submit-review); gate decision delegated to workflow.")
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
