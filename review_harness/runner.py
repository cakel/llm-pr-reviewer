"""Run a review over one or more diff parts with engine fallback, and merge the result.

Parts are reviewed concurrently: each review is its own engine CLI process, and a small thread pool only
waits on them. The pool size follows the machine (CPUs and memory this process may use, cgroup limits
included) and is capped so one account does not hit the engine's rate limit with too many sessions.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.base import EngineAdapter
from .chunking import files_in, merge_reviews, split_diff
from .prompt import build_review_prompt, sanitize_and_parse_review


@dataclass
class ReviewResult:
    review: str
    counts: dict[str, int]
    engines: list[str] = field(default_factory=list)   # engines that produced a part, in part order
    parts: int = 1
    last_engine: str = ""                               # engine that produced the last finished review


# Peak RSS of one engine CLI measured at ~165-177 MB (kiro-cli, incl. a 200 KB prompt) and ~265 MB (claude);
# leave headroom for big prompts and the CLI's own children
WORKER_MEMORY_BYTES = 512 * 1024 * 1024
# Same account, same engine: more concurrent sessions than this mostly buys 429s. Raise it with --max-parallel.
DEFAULT_PARALLEL_CAP = 4
ADMIT_POLL_SEC = 2.0


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _cgroup_dirs() -> list[str]:
    """This process's cgroup v2 directory and its ancestors (a runner service's MemoryMax sits on its own cgroup)."""
    rel = ""
    for line in (_read("/proc/self/cgroup") or "").splitlines():
        if line.startswith("0::"):
            rel = line[3:].strip().strip("/")
    dirs, parts = [], [p for p in rel.split("/") if p and p != ".."]
    for i in range(len(parts), -1, -1):
        dirs.append("/".join(["/sys/fs/cgroup", *parts[:i]]))
    return dirs


def available_memory_bytes() -> int | None:
    """Memory this process can still use: MemAvailable, lowered by the tightest cgroup v2 memory limit."""
    avail = None
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) * 1024
    for d in _cgroup_dirs():
        limit, current = _read(f"{d}/memory.max"), _read(f"{d}/memory.current")
        if limit and limit.isdigit() and current and current.isdigit():
            room = max(0, int(limit) - int(current))
            avail = room if avail is None else min(avail, room)
    return avail


def available_cpus() -> int:
    """CPUs this process may run on, lowered by the tightest cgroup v2 CPU quota."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    for d in _cgroup_dirs():
        quota = (_read(f"{d}/cpu.max") or "").split()
        if len(quota) == 2 and quota[0].isdigit() and quota[1].isdigit() and int(quota[1]) > 0:
            cpus = min(cpus, max(1, int(quota[0]) // int(quota[1])))
    return max(1, cpus)


def cpu_busy(cpus: int) -> bool:
    """True when the machine is already saturated (1-minute load average at or above the usable CPUs)."""
    try:
        return os.getloadavg()[0] >= cpus
    except (AttributeError, OSError):
        return False


def plan_workers(parts: int, max_parallel: int = 0) -> int:
    """Upper bound on parts reviewed at once: min(parts, cap, CPUs, available memory / 512 MiB), at least 1.

    `max_parallel` <= 0 means the default cap (4). Each start is still admitted at run time (see Admission).
    """
    cap = max_parallel if max_parallel > 0 else DEFAULT_PARALLEL_CAP
    workers = min(parts, cap, available_cpus())
    mem = available_memory_bytes()
    if mem is not None:
        workers = min(workers, mem // WORKER_MEMORY_BYTES)
    return max(1, workers)


class Admission:
    """Re-checks the machine before each part starts, so a runner shared with other jobs is not overcommitted.

    A part waits while memory is below one worker's budget or the load average is at the CPU count, but only
    while another part is running: the first one always starts, so the review never stalls.
    """

    def __init__(self, cpus: int, poll_sec: float = ADMIT_POLL_SEC):
        self.cpus, self.poll_sec = cpus, poll_sec
        self.running = 0
        self.cond = threading.Condition()

    def _room(self) -> tuple[bool, str]:
        mem = available_memory_bytes()
        if mem is not None and mem < WORKER_MEMORY_BYTES:
            return False, f"memory available {mem // 2**20:,} MiB < {WORKER_MEMORY_BYTES // 2**20} MiB"
        if cpu_busy(self.cpus):
            return False, f"load average >= {self.cpus} CPUs"
        return True, ""

    def __enter__(self):
        with self.cond:
            waited = False
            while self.running > 0:
                ok, why = self._room()
                if ok:
                    break
                if not waited:
                    print(f"Waiting for a running part to finish before starting another ({why}).")
                    waited = True
                self.cond.wait(timeout=self.poll_sec)
            self.running += 1
        return self

    def __exit__(self, *exc):
        with self.cond:
            self.running -= 1
            self.cond.notify_all()
        return False


def available_engines(candidates: list[str], adapters: dict[str, EngineAdapter]) -> list[str]:
    """Keep the candidates whose CLI is installed and authenticated (checked once, not per part)."""
    ready = []
    for name in candidates:
        if adapters[name].detect():
            ready.append(name)
        else:
            print(f"Engine '{name}' is not installed or not authenticated; skipping.")
    return ready


def run_with_fallback(prompt: str, engines: list[str], adapters: dict[str, EngineAdapter], summary_key: str,
                      work_dir: str, model: str | None, effort: str | None,
                      label: str = "") -> tuple[str, str, dict[str, int]] | None:
    """Try `engines` in order until one returns a parseable review. Returns (engine, review, counts)."""
    prompt_bytes = len(prompt.encode("utf-8"))
    for name in engines:
        adapter = adapters[name]
        if adapter.max_prompt_bytes is not None and prompt_bytes > adapter.max_prompt_bytes:
            print(f"::warning::Engine '{name}' skipped{label}: prompt {prompt_bytes:,} bytes > its limit "
                  f"{adapter.max_prompt_bytes:,} bytes. Trying fallback...")
            continue
        print(f"Attempting review{label} with engine: {name}...")
        raw_output, retcode = adapter.run(prompt=prompt, work_dir=work_dir, model=model, effort=effort)
        review, counts, parse_err = sanitize_and_parse_review(raw_output, summary_key)
        if retcode == 0 and not parse_err:
            print(f"Engine '{name}' completed review{label} successfully.")
            return name, review, counts
        reason = parse_err or f"exited with code {retcode} ({adapter.classify_error(raw_output, retcode)})"
        print(f"::warning::Engine '{name}' failed{label} ({reason}). Trying fallback...")
    return None


def review_diff(diff: str, part_bytes: int, engines: list[str], adapters: dict[str, EngineAdapter],
                delimiter: str, summary_key: str, work_dir: str, model: str | None, effort: str | None,
                previous_findings: str | None = None, user_guidance: str | None = None,
                sensitive_paths: list[str] | None = None, max_parallel: int = 0) -> ReviewResult | None:
    """Review `diff` in one call, or split into parts of at most `part_bytes` and merge the reviews.

    Every part must succeed: a review that skipped part of the diff is not a review of the PR, so a part
    that fails on every engine fails the whole run (None) instead of approving the rest.
    Parts run concurrently (see plan_workers); `max_parallel` caps the pool (<= 0: default cap).
    """
    parts = split_diff(diff, part_bytes)
    if len(parts) == 1:
        got = run_with_fallback(
            build_review_prompt(diff=diff, delimiter=delimiter, summary_key=summary_key,
                                previous_findings=previous_findings, user_guidance=user_guidance,
                                sensitive_paths=sensitive_paths),
            engines, adapters, summary_key, work_dir, model, effort)
        if not got:
            return None
        engine, review, counts = got
        return ReviewResult(review=review, counts=counts, engines=[engine], last_engine=engine)

    part_files = [files_in(p) for p in parts]
    all_files = list(dict.fromkeys(f for files in part_files for f in files))
    total_bytes = len(diff.encode("utf-8"))
    print(f"Diff is {total_bytes:,} bytes > {part_bytes:,} per review call; reviewing in {len(parts)} parts.")

    workers = plan_workers(len(parts), max_parallel)
    mem = available_memory_bytes()
    print(f"Reviewing {len(parts)} parts with {workers} parallel worker(s) "
          f"(cpus {available_cpus()}, memory available {'unknown' if mem is None else f'{mem // 2**20:,} MiB'}).")

    # A sensitive path no part's headers name (e.g. a git-quoted non-ASCII path) is flagged in every part:
    # dropping the alert is the unsafe direction.
    unmatched = [f for f in (sensitive_paths or []) if f not in all_files]

    def part_sensitive(i: int) -> list[str]:
        return [f for f in (sensitive_paths or []) if f in part_files[i - 1]] + unmatched

    admission = Admission(available_cpus())
    lock = threading.Lock()
    order = list(engines)           # shared: the engine that last succeeded goes first for the next part
    failed = threading.Event()

    def review_part(i: int, part: str) -> tuple[str, str, dict[str, int]] | None:
        if failed.is_set():             # another part already failed every engine; don't spend more quota
            print(f"Part {i}/{len(parts)} not started: another part failed on every engine.")
            return None
        label = f" (part {i}/{len(parts)}, {len(part.encode('utf-8')):,} bytes)"
        prompt = build_review_prompt(diff=part, delimiter=delimiter, summary_key=summary_key,
                                     previous_findings=previous_findings, user_guidance=user_guidance,
                                     sensitive_paths=part_sensitive(i),
                                     part=(i, len(parts)), all_files=all_files)
        part_dir = Path(work_dir) / f"part{i}"     # concurrent CLI sessions must not share a cwd
        part_dir.mkdir(parents=True, exist_ok=True)
        with admission:
            if failed.is_set():
                print(f"Part {i}/{len(parts)} not started: another part failed on every engine.")
                return None
            with lock:
                tried = list(order)
            got = run_with_fallback(prompt, tried, adapters, summary_key, str(part_dir), model, effort, label)
        if not got:
            failed.set()
            print(f"Every engine failed on part {i}/{len(parts)}; not merging a partial review.")
            return None
        with lock:
            order[:] = [got[0]] + [e for e in order if e != got[0]]
        return got

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="review-part") as pool:
        outcomes = list(pool.map(review_part, range(1, len(parts) + 1), parts))
    if failed.is_set() or any(o is None for o in outcomes):
        return None

    used = list(dict.fromkeys(engine for engine, _, _ in outcomes))
    results = [(review, counts) for _, review, counts in outcomes]

    review, counts = merge_reviews(results, part_files, total_bytes)
    return ReviewResult(review=review, counts=counts, engines=used, parts=len(parts), last_engine=order[0])
