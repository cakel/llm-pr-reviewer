"""Split a diff that is too large for one review call, and merge the per-part reviews.

A part never exceeds the per-call byte budget. Whole files are packed together in git's order (related
paths stay adjacent); a file larger than the budget is split at hunk boundaries with its header repeated,
and a hunk larger than the budget is split by lines (a single huge line, e.g. minified code, by characters).
"""

import re

# Engines drift from the exact '### Name' form ('## Findings', '### Findings:', '**Findings**'); accept those too
_SECTION_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?(Summary|Strengths|Findings|Previous findings)(?:\*\*)?[ \t]*:?(?:\*\*)?[ \t]*$")
_CANONICAL = {"summary": "Summary", "strengths": "Strengths", "findings": "Findings",
              "previous findings": "Previous findings"}
_NO_FINDINGS_RE = re.compile(r"^\W*no findings\W*$", re.IGNORECASE)
SEVERITIES = ("critical", "major", "minor", "nit")


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_text(text: str, budget: int) -> list[str]:
    """Split by characters so each piece is at most `budget` UTF-8 bytes."""
    pieces, cur, cur_size = [], [], 0
    for ch in text:
        n = _size(ch)
        if cur and cur_size + n > budget:
            pieces.append("".join(cur))
            cur, cur_size = [], 0
        cur.append(ch)
        cur_size += n
    if cur:
        pieces.append("".join(cur))
    return pieces


def _pack(units: list[str], budget: int) -> list[str]:
    """Greedily concatenate units into groups of at most `budget` bytes (oversized units are split)."""
    groups, cur, cur_size = [], [], 0
    for unit in units:
        n = _size(unit)
        if n > budget:
            if cur:
                groups.append("".join(cur))
                cur, cur_size = [], 0
            lines = unit.splitlines(keepends=True)
            if len(lines) > 1:
                groups.extend(_pack(lines, budget))
            else:
                groups.extend(_split_text(unit, budget))
            continue
        if cur and cur_size + n > budget:
            groups.append("".join(cur))
            cur, cur_size = [], 0
        cur.append(unit)
        cur_size += n
    if cur:
        groups.append("".join(cur))
    return groups


def _file_blocks(diff: str) -> list[str]:
    """Split a unified git diff into one block per file (text before the first header stays its own block)."""
    starts = [m.start() for m in re.finditer(r"(?m)^diff --git ", diff)]
    if not starts:
        return [diff] if diff else []
    blocks = [diff[:starts[0]]] if starts[0] > 0 else []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(diff)
        blocks.append(diff[start:end])
    return [b for b in blocks if b]


def _split_file_block(block: str, budget: int) -> list[str]:
    """Split one file's diff at hunk boundaries, repeating the file header in every piece."""
    first_hunk = re.search(r"(?m)^@@ ", block)
    if not first_hunk:
        return _pack([block], budget)
    header, body = block[:first_hunk.start()], block[first_hunk.start():]
    hunk_starts = [m.start() for m in re.finditer(r"(?m)^@@ ", body)]
    hunks = [body[s:(hunk_starts[i + 1] if i + 1 < len(hunk_starts) else len(body))]
             for i, s in enumerate(hunk_starts)]
    room = budget - _size(header)
    if room < budget // 2:
        # Pathological header (huge rename/mode lines): don't let it starve the hunks
        return _pack([block], budget)
    return [header + group for group in _pack(hunks, room)]


def split_diff(diff: str, max_bytes: int) -> list[str]:
    """Split `diff` into parts of at most `max_bytes` UTF-8 bytes each, preserving file order."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if _size(diff) <= max_bytes:
        return [diff]
    units: list[str] = []
    for block in _file_blocks(diff):
        units.extend([block] if _size(block) <= max_bytes else _split_file_block(block, max_bytes))
    return _pack(units, max_bytes)


def files_in(diff: str) -> list[str]:
    """Paths touched by a diff, in order, without duplicates (deleted files use their old path)."""
    seen: dict[str, None] = {}
    for block in _file_blocks(diff):
        new = re.search(r"(?m)^\+\+\+ b/(.+)$", block)
        old = re.search(r"(?m)^--- a/(.+)$", block)
        git = re.match(r"diff --git a/(.+?) b/(.+)$", block.split("\n", 1)[0])
        path = (new and new.group(1)) or (old and old.group(1)) or (git and git.group(2))
        if path:
            seen.setdefault(path.strip(), None)
    return list(seen)


def _sections(review: str) -> dict[str, str]:
    """Map the standard '### ' sections of one review to their bodies."""
    matches = list(_SECTION_RE.finditer(review))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(review)
        body = review[m.end():end].strip()
        name = _CANONICAL[m.group(1).lower()]
        out[name] = (out[name] + "\n\n" + body).strip() if name in out else body
    return out


def _label(index: int, total: int, files: list[str], shown: int = 3) -> str:
    names = ", ".join(f"`{f.replace('`', chr(39))}`" for f in files[:shown])
    more = f" 외 {len(files) - shown}개" if len(files) > shown else ""
    return f"묶음 {index}/{total}" + (f" ({names}{more})" if names else "")


def merge_reviews(parts: list[tuple[str, dict[str, int]]], part_files: list[list[str]],
                  total_bytes: int) -> tuple[str, dict[str, int]]:
    """Merge per-part reviews into one review in the standard section layout, summing the counts."""
    total = len(parts)
    counts = {k: sum(c.get(k, 0) for _, c in parts) for k in SEVERITIES}
    secs = [_sections(text) for text, _ in parts]
    labels = [_label(i + 1, total, part_files[i] if i < len(part_files) else []) for i in range(total)]

    summary = [
        f"> 변경점이 커서(diff {total_bytes:,} bytes) 파일 단위로 {total}개 묶음으로 나눠 리뷰했습니다."
        " 각 묶음은 자기 묶음의 diff만 보았으므로, 여러 묶음에 걸친 문제(호출부와 정의가 다른 묶음에 있는 경우 등)는"
        " 놓쳤을 수 있습니다."
    ]
    for label, sec in zip(labels, secs):
        summary.append(f"**{label}** — {sec.get('Summary', '').strip() or '(요약 없음)'}")

    strengths = [s["Strengths"] for s in secs if s.get("Strengths", "").strip()]

    findings = []
    for (text, part_counts), label, sec in zip(parts, labels, secs):
        body = sec.get("Findings", "").strip()
        if "Findings" not in sec and text.strip():
            # Unparseable layout: keep the part's whole review so no counted finding goes missing
            findings.append(f"#### {label} (형식을 해석하지 못해 원문을 그대로 붙입니다)\n{text.strip()}")
        elif body and not _NO_FINDINGS_RE.match(body):
            findings.append(body)
        elif any(part_counts.get(k, 0) for k in SEVERITIES):
            findings.append(f"#### {label} (개수는 있지만 Findings 본문이 비어 있어 원문을 그대로 붙입니다)\n{text.strip()}")

    previous, dropped = [], False
    for label, sec in zip(labels, secs):
        body = sec.get("Previous findings", "").strip()
        if not body:
            continue
        # A part that does not contain a finding's file can only say ➖; keep the parts that could judge it.
        # Only a line whose status is ➖ (no ✅/❌ on it) goes; a ✅/❌ line that mentions ➖ in its evidence stays.
        kept = [ln for ln in body.splitlines() if "➖" not in ln or "✅" in ln or "❌" in ln]
        dropped = dropped or len(kept) != len(body.splitlines())
        kept_text = "\n".join(kept).strip()
        if kept_text:
            previous.append(f"#### {label}\n{kept_text}")

    out = ["### Summary", "\n\n".join(summary), "", "### Strengths",
           "\n".join(strengths) if strengths else "- (없음)", "", "### Findings",
           "\n\n".join(findings) if findings else "No findings."]
    if any("Previous findings" in s for s in secs):
        out += ["", "### Previous findings"]
        if previous:
            out.append("\n\n".join(previous))
        if dropped:
            out.append("<sub>➖ 판단불가 항목은 해당 파일이 없는 묶음에서 나온 것이라 생략했습니다."
                       " 위에 없는 이전 지적은 어느 묶음에서도 판단하지 못한 것입니다.</sub>")
    return "\n".join(out).strip(), counts
