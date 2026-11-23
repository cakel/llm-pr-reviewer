"""Tests for splitting oversized diffs and merging per-part reviews."""

import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from review_harness.adapters.base import EngineAdapter
from review_harness.chunking import files_in, merge_reviews, split_diff
from review_harness import runner
from review_harness.runner import plan_workers, review_diff


def file_diff(path: str, hunks: int = 1, lines: int = 5, width: int = 20) -> str:
    out = [f"diff --git a/{path} b/{path}\n", "index 111..222 100644\n", f"--- a/{path}\n", f"+++ b/{path}\n"]
    for h in range(hunks):
        out.append(f"@@ -{h * 100 + 1},{lines} +{h * 100 + 1},{lines} @@\n")
        out.extend(f"+{path}:{h}:{i}:" + "q" * width + "\n" for i in range(lines))
    return "".join(out)


def size(text: str) -> int:
    return len(text.encode("utf-8"))


class TestSplitDiff(unittest.TestCase):
    def test_small_diff_is_one_part(self):
        diff = file_diff("a.py")
        self.assertEqual(split_diff(diff, 10_000), [diff])

    def test_whole_files_are_packed_in_order(self):
        diff = "".join(file_diff(f"f{i}.py") for i in range(10))
        parts = split_diff(diff, size(file_diff("f0.py")) * 3 + 10)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), diff)                  # nothing lost, nothing reordered
        for part in parts:
            self.assertTrue(part.startswith("diff --git "))       # every part starts at a file boundary
            self.assertLessEqual(size(part), size(file_diff("f0.py")) * 3 + 10)

    def test_large_file_splits_at_hunks_with_header_repeated(self):
        big = file_diff("big.py", hunks=8, lines=10)
        parts = split_diff(file_diff("a.py") + big, size(big) // 3)
        big_parts = [p for p in parts if "big.py" in p]
        self.assertGreater(len(big_parts), 1)
        for part in big_parts:
            self.assertTrue(part.startswith("diff --git a/big.py b/big.py\n"))
            self.assertIn("+++ b/big.py\n", part)
            self.assertTrue(re.search(r"(?m)^\+\+\+ b/big\.py\n@@ ", part))   # header then a whole hunk
            self.assertLessEqual(size(part), size(big) // 3)
        # Every added line survives exactly once
        added = [ln for p in parts for ln in p.splitlines() if ln.startswith("+big.py")]
        self.assertEqual(added, [ln for ln in big.splitlines() if ln.startswith("+big.py")])

    def test_oversized_hunk_and_line_still_fit(self):
        diff = file_diff("h.py", hunks=1, lines=200) + file_diff("min.js", lines=1, width=5000)
        parts = split_diff(diff, 1000)
        self.assertTrue(all(size(p) <= 1000 for p in parts))
        self.assertEqual(sum(p.count("q") for p in parts), diff.count("q"))

    def test_multibyte_budget(self):
        diff = file_diff("k.py", lines=1, width=0).replace(":\n", ":" + "가" * 500 + "\n")
        parts = split_diff(diff, 300)
        self.assertTrue(all(size(p) <= 300 for p in parts))
        self.assertEqual(sum(p.count("가") for p in parts), 500)

    def test_files_in(self):
        diff = file_diff("a.py") + file_diff("dir/b c.py") + (
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
        self.assertEqual(files_in(diff), ["a.py", "dir/b c.py", "gone.py"])


def review(summary: str, findings: str, previous: str | None = None) -> str:
    text = f"### Summary\n{summary}\n\n### Strengths\n- good {summary}\n\n### Findings\n{findings}"
    if previous is not None:
        text += f"\n\n### Previous findings\n{previous}"
    return text


class TestMergeReviews(unittest.TestCase):
    def test_counts_sum_and_sections_merge(self):
        parts = [
            (review("one", "[MAJOR]\n- `a.py:1` bad"), {"critical": 0, "major": 1, "minor": 0, "nit": 0}),
            (review("two", "No findings."), {"critical": 0, "major": 0, "minor": 0, "nit": 0}),
            (review("three", "[NIT]\n- `c.py:2` typo"), {"critical": 0, "major": 0, "minor": 0, "nit": 1}),
        ]
        text, counts = merge_reviews(parts, [["a.py"], ["b.py"], ["c.py"]], 500_000)
        self.assertEqual(counts, {"critical": 0, "major": 1, "minor": 0, "nit": 1})
        self.assertEqual([m for m in re.findall(r"(?m)^### .+$", text)], ["### Summary", "### Strengths", "### Findings"])
        self.assertIn("3개 묶음", text)
        self.assertIn("500,000 bytes", text)
        self.assertIn("**묶음 2/3 (`b.py`)** — two", text)
        self.assertIn("`a.py:1` bad", text)
        self.assertIn("`c.py:2` typo", text)
        self.assertNotIn("No findings.", text)          # one part's "No findings." must not hide others
        self.assertIn("- good one\n- good two", text)

    def test_all_parts_clean(self):
        zero = {"critical": 0, "major": 0, "minor": 0, "nit": 0}
        text, counts = merge_reviews([(review("a", "No findings."), zero)] * 2, [["a"], ["b"]], 1)
        self.assertIn("### Findings\nNo findings.", text)
        self.assertEqual(counts, zero)

    def test_previous_findings_drop_undecidable(self):
        zero = {"critical": 0, "major": 0, "minor": 0, "nit": 0}
        parts = [
            (review("a", "No findings.", "- `a.py` 문제: ✅ 해결됨 (수정됨)\n- `b.py` 문제: ➖ 판단불가 (없음)"), zero),
            (review("b", "No findings.", "- `a.py` 문제: ➖ 판단불가 (없음)\n- `b.py` 문제: ❌ 미해결"), zero),
        ]
        text, _ = merge_reviews(parts, [["a.py"], ["b.py"]], 1)
        prev = text.split("### Previous findings", 1)[1]
        self.assertIn("✅ 해결됨", prev)
        self.assertIn("❌ 미해결", prev)
        self.assertNotIn("➖ 판단불가 (없음)", prev)
        self.assertIn("#### 묶음 1/2", prev)


class FakeAdapter(EngineAdapter):
    def __init__(self, name, fail_on=(), max_prompt_bytes=None, delay=0.0):
        self.name, self.fail_on, self.max_prompt_bytes, self.calls = name, set(fail_on), max_prompt_bytes, []
        self.delay, self.running, self.peak, self.lock, self.dirs = delay, 0, 0, threading.Lock(), set()

    def detect(self):
        return True

    def run(self, prompt, work_dir, model=None, effort=None, timeout_sec=300):
        part = re.search(r"You see only part (\d+) of", prompt)
        n = int(part.group(1)) if part else 1
        with self.lock:
            self.calls.append(n)
            self.dirs.add(work_dir)
            self.running += 1
            self.peak = max(self.peak, self.running)
        time.sleep(self.delay)
        with self.lock:
            self.running -= 1
        if n in self.fail_on:
            return "quota exceeded", 1
        key = re.search(r"(REVIEW_SUMMARY_\w+)=", prompt).group(1)
        return review(f"{self.name} part {n}", "[MINOR]\n- `x` y") + f'\n{key}={{"critical":0,"major":0,"minor":1,"nit":0}}\n', 0


class TestReviewDiff(unittest.TestCase):
    DIFF = "".join(file_diff(f"f{i}.py", lines=20) for i in range(6))

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_review(self, adapters, part_bytes, max_parallel=1):
        return review_diff(self.DIFF, part_bytes, list(adapters), adapters, "DELIM", "REVIEW_SUMMARY_k",
                           self.tmp.name, None, None, previous_findings="- old", max_parallel=max_parallel)

    def test_single_call_when_within_limit(self):
        a = FakeAdapter("a")
        result = self.run_review({"a": a}, size(self.DIFF))
        self.assertEqual((result.parts, a.calls, result.counts["minor"]), (1, [1], 1))
        self.assertNotIn("묶음", result.review)

    def test_parts_merge_and_stick_to_working_engine(self):
        a, b = FakeAdapter("a", fail_on={2}), FakeAdapter("b")
        result = self.run_review({"a": a, "b": b}, size(self.DIFF) // 3)
        self.assertGreaterEqual(result.parts, 3)
        self.assertEqual(result.counts["minor"], result.parts)
        self.assertEqual(result.engines, ["a", "b"])
        self.assertEqual(a.calls, [1, 2])                        # after the fallback, b keeps the remaining parts
        self.assertEqual(b.calls, list(range(2, result.parts + 1)))

    def test_parts_run_in_parallel_and_merge_in_order(self):
        a = FakeAdapter("a", delay=0.2)
        with patch.object(runner, "available_cpus", return_value=8), \
             patch.object(runner, "available_memory_bytes", return_value=8 * 2**30):
            started = time.monotonic()
            result = self.run_review({"a": a}, size(self.DIFF) // 3, max_parallel=0)
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(result.parts, 3)
        self.assertGreater(a.peak, 1)                               # really concurrent
        self.assertLessEqual(a.peak, runner.DEFAULT_PARALLEL_CAP)
        self.assertLess(elapsed, 0.2 * result.parts)                # faster than one after another
        self.assertEqual(len(a.dirs), result.parts)                 # each CLI session has its own cwd
        order = [int(m) for m in re.findall(r"\*\*묶음 (\d+)/", result.review)]
        self.assertEqual(order, list(range(1, result.parts + 1)))  # merged in part order, not finish order

    def test_sensitive_alert_only_in_the_part_with_the_file(self):
        prompts = []

        class Capture(FakeAdapter):
            def run(self, prompt, *a, **k):
                prompts.append(prompt)
                return super().run(prompt, *a, **k)

        review_diff(self.DIFF, size(self.DIFF) // 3, ["a"], {"a": Capture("a")}, "DELIM", "REVIEW_SUMMARY_k",
                    self.tmp.name, None, None, sensitive_paths=["f0.py"], max_parallel=1)
        alerts = ["SECURITY SENSITIVE FILES ALERT" in p for p in prompts]
        self.assertEqual(alerts, [True] + [False] * (len(prompts) - 1))

    def test_any_failed_part_fails_the_review(self):
        a = FakeAdapter("a", fail_on={2})
        self.assertIsNone(self.run_review({"a": a}, size(self.DIFF) // 3))

    def test_prompt_cap_skips_engine(self):
        small, big = FakeAdapter("small", max_prompt_bytes=100), FakeAdapter("big")
        result = self.run_review({"small": small, "big": big}, size(self.DIFF))
        self.assertEqual((small.calls, result.engines), ([], ["big"]))


class TestPlanWorkers(unittest.TestCase):
    def plan(self, parts, cpus, mem, max_parallel=0):
        with patch.object(runner, "available_cpus", return_value=cpus), \
             patch.object(runner, "available_memory_bytes", return_value=mem):
            return plan_workers(parts, max_parallel)

    def test_bounded_by_parts_cpus_memory_and_cap(self):
        gib = 2**30
        self.assertEqual(self.plan(3, 16, 64 * gib), 3)            # no more workers than parts
        self.assertEqual(self.plan(10, 16, 64 * gib), 4)           # default cap (rate limits)
        self.assertEqual(self.plan(10, 16, 64 * gib, 8), 8)        # explicit cap
        self.assertEqual(self.plan(10, 2, 64 * gib, 8), 2)         # CPUs
        self.assertEqual(self.plan(10, 16, int(1.5 * gib), 8), 3)  # memory / 512 MiB
        self.assertEqual(self.plan(10, 16, 100, 8), 1)             # always at least one
        self.assertEqual(self.plan(10, 16, None, 8), 8)            # memory unknown

    def run_admitted(self, mem, busy):
        a = FakeAdapter("a", delay=0.1)
        diff = "".join(file_diff(f"f{i}.py", lines=20) for i in range(6))
        with patch.object(runner, "plan_workers", return_value=3), \
             patch.object(runner, "available_memory_bytes", return_value=mem), \
             patch.object(runner, "cpu_busy", return_value=busy), \
             patch.object(runner, "ADMIT_POLL_SEC", 0.01), \
             tempfile.TemporaryDirectory() as tmp:
            result = review_diff(diff, size(diff) // 3, ["a"], {"a": a}, "D", "REVIEW_SUMMARY_k", tmp, None, None)
        return a, result

    def test_admission_waits_when_memory_runs_low(self):
        a, result = self.run_admitted(mem=100 * 2**20, busy=False)   # below one worker's 512 MiB
        self.assertIsNotNone(result)                                   # first part always starts: no stall
        self.assertEqual(a.peak, 1)

    def test_admission_waits_when_cpus_saturated(self):
        a, result = self.run_admitted(mem=None, busy=True)
        self.assertIsNotNone(result)
        self.assertEqual(a.peak, 1)

    def test_admission_lets_parts_overlap_with_room(self):
        a, _ = self.run_admitted(mem=8 * 2**30, busy=False)
        self.assertGreater(a.peak, 1)

    def test_cgroup_of_this_process_and_ancestors(self):
        files = {"/proc/self/cgroup": "0::/system.slice/runner.service",
                 "/proc/meminfo": "MemAvailable:    8388608 kB",
                 "/sys/fs/cgroup/system.slice/runner.service/memory.max": str(2 * 2**30),
                 "/sys/fs/cgroup/system.slice/runner.service/memory.current": str(2**30),
                 "/sys/fs/cgroup/system.slice/runner.service/cpu.max": "max 100000",
                 "/sys/fs/cgroup/system.slice/cpu.max": "300000 100000"}
        with patch.object(runner, "_read", side_effect=files.get), \
             patch("os.sched_getaffinity", return_value=set(range(16))):
            self.assertEqual(runner.available_memory_bytes(), 2**30)   # service MemoryMax, not host RAM
            self.assertEqual(runner.available_cpus(), 3)              # parent slice CPUQuota=300%

    def test_cgroup_limits(self):
        files = {"/proc/meminfo": "MemTotal: 1 kB\nMemAvailable:    8388608 kB",
                 "/sys/fs/cgroup/memory.max": str(3 * 2**30), "/sys/fs/cgroup/memory.current": str(2**30),
                 "/sys/fs/cgroup/cpu.max": "200000 100000"}
        with patch.object(runner, "_read", side_effect=files.get), \
             patch("os.sched_getaffinity", return_value=set(range(16))):
            self.assertEqual(runner.available_memory_bytes(), 2 * 2**30)
            self.assertEqual(runner.available_cpus(), 2)


if __name__ == "__main__":
    unittest.main()
