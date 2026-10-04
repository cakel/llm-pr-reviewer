#!/usr/bin/env python3
"""Run a local Adversarial Review against a git branch or diff without touching GitHub APIs."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from review_harness.adapters.kiro import KiroAdapter
from review_harness.adapters.codex import CodexAdapter
from review_harness.adapters.agy import AgyAdapter
from review_harness.adapters.claude import ClaudeAdapter
from review_harness.diff_guard import DiffGuard
from review_harness.prompt import build_review_prompt, generate_tokens, sanitize_and_parse_review


def parse_args():
    parser = argparse.ArgumentParser(description="Local Adversarial Reviewer")
    parser.add_argument("--workspace", default=os.getcwd(), help="Target repository workspace")
    parser.add_argument("--base", default="origin/main", help="Base commit or branch (default: origin/main)")
    parser.add_argument("--head", default="HEAD", help="Head commit or branch (default: HEAD)")
    parser.add_argument("--engine", default="kiro", choices=["kiro", "codex", "agy", "claude"], help="LLM CLI engine")
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument("--effort", default="medium", help="Reasoning effort")
    parser.add_argument("--previous-findings", default=None, help="Path to previous findings markdown or raw text")
    parser.add_argument("--user-guidance", default=None, help="Maintainer direction or specific instructions")
    return parser.parse_args()


def main():
    args = parse_args()
    workspace = os.path.abspath(args.workspace)
    print(f"🔍 Running Local Adversarial Review on: {workspace}")
    print(f"   Base: {args.base}  |  Head: {args.head}  |  Engine: {args.engine}")

    # 1. Diff Extraction & Safety Guard
    diff_guard = DiffGuard(workspace=workspace, base_sha=args.base, head_sha=args.head)
    try:
        if args.head in ("WORKTREE", "", None):
            diff_content = diff_guard._git("diff", "--no-ext-diff", "--no-textconv", args.base)
        else:
            diff_content = diff_guard._git("diff", "--no-ext-diff", "--no-textconv", f"{args.base}...{args.head}")
    except Exception as exc:
        print(f"❌ Failed to extract git diff: {exc}", file=sys.stderr)
        return 2

    if not diff_content.strip():
        print("ℹ️ Diff is empty. Nothing to review.")
        return 0

    print(f"📦 Diff size: {len(diff_content.encode('utf-8'))} bytes")
    safe, message = diff_guard.verify_safety(diff_content)
    if not safe:
        print(f"❌ Pre-review diff security guard rejected the diff: {message}", file=sys.stderr)
        return 2
    print(f"✅ Diff safety check passed: {message}")

    sensitive_paths = diff_guard.get_sensitive_paths()
    if sensitive_paths:
        print(f"⚠️ Sensitive paths detected: {', '.join(sensitive_paths)}")

    previous_findings = None
    if args.previous_findings:
        p_path = Path(args.previous_findings)
        if p_path.is_file():
            previous_findings = p_path.read_text(encoding="utf-8")
        else:
            previous_findings = args.previous_findings

    # 2. Select Engine
    adapters = {
        "kiro": KiroAdapter(),
        "codex": CodexAdapter(),
        "agy": AgyAdapter(),
        "claude": ClaudeAdapter(),
    }
    adapter = adapters[args.engine]
    if not adapter.detect():
        print(f"❌ Engine '{args.engine}' is not detected or available in PATH.", file=sys.stderr)
        return 2

    # 3. Construct Prompt
    delimiter, summary_key = generate_tokens()
    prompt = build_review_prompt(
        diff=diff_content,
        delimiter=delimiter,
        summary_key=summary_key,
        previous_findings=previous_findings,
        user_guidance=args.user_guidance,
        sensitive_paths=sensitive_paths,
    )

    # 4. Execute Engine
    print(f"🚀 Invoking {args.engine} engine for adversarial analysis...")
    with tempfile.TemporaryDirectory() as temp_dir:
        raw_output, exit_code = adapter.run(
            prompt=prompt,
            work_dir=temp_dir,
            model=args.model,
            effort=args.effort,
        )

    if exit_code != 0:
        print(f"❌ Engine '{args.engine}' failed with exit code {exit_code}:\n{raw_output}", file=sys.stderr)
        return 2

    # 5. Sanitize and Parse Review
    cleaned_review, counts, err = sanitize_and_parse_review(raw_output, summary_key)
    blocking = counts.get("critical", 0) + counts.get("major", 0)
    decision = "request_changes" if blocking > 0 else "approve"
    icon = "🛑" if decision == "request_changes" else "✅"

    print("\n" + "=" * 60)
    print(f"## 🤖 Local Adversarial Review ({args.engine.upper()} / {args.model or 'auto'}, {args.effort})")
    print("=" * 60)
    print(cleaned_review)
    print("\n" + "-" * 60)
    counts_str = " · ".join(f"{k}: {counts[k]}" for k in ["critical", "major", "minor", "nit"])
    print(f"### Verdict: {icon} {decision.upper()} ({counts_str})")
    print("-" * 60)

    if blocking > 0:
        print(f"\n🛑 Review BLOCKED with {blocking} critical/major findings. Please resolve before pushing.")
        return 1
    else:
        print("\n✅ Review PASSED with 0 blocking issues. Ready to push!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
