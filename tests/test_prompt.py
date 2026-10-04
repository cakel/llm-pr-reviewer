"""Unit tests for prompt construction and output parsing."""

import unittest
from review_harness.prompt import (
    build_review_prompt,
    generate_tokens,
    sanitize_and_parse_review,
)


class TestPrompt(unittest.TestCase):
    def test_generate_tokens(self):
        d1, s1 = generate_tokens()
        d2, s2 = generate_tokens()
        self.assertNotEqual(d1, d2)
        self.assertNotEqual(s1, s2)
        self.assertTrue(s1.startswith("REVIEW_SUMMARY_"))

    def test_build_review_prompt(self):
        prompt = build_review_prompt(
            diff="diff --git a/foo b/foo\n+line",
            delimiter="DELIM123",
            summary_key="KEY123",
            previous_findings="old finding",
        )
        self.assertIn("--- BEGIN UNTRUSTED PULL REQUEST DIFF DELIM123 ---", prompt)
        self.assertIn("--- BEGIN UNTRUSTED PREVIOUS REVIEW FINDINGS DELIM123 ---", prompt)
        self.assertIn("KEY123=", prompt)

    def test_sanitize_and_parse_valid(self):
        summary_key = "KEY_ABC"
        raw = f"""
> ### Summary
좋은 변경입니다.

### Strengths
- 코드 정리

### Findings
No findings.

{summary_key}={{"critical": 0, "major": 0, "minor": 1, "nit": 2}}
"""
        review, counts, err = sanitize_and_parse_review(raw, summary_key)
        self.assertIsNone(err)
        self.assertEqual(counts["critical"], 0)
        self.assertEqual(counts["minor"], 1)
        self.assertEqual(counts["nit"], 2)
        self.assertNotIn(">", review.splitlines()[0])  # leading > removed
        self.assertIn("### Summary", review)

    def test_sanitize_and_parse_missing_summary(self):
        raw = "Just text without summary line"
        _, _, err = sanitize_and_parse_review(raw, "KEY_MISSING")
        self.assertIsNotNone(err)
        self.assertIn("machine-readable summary line", err)

    def test_sanitize_and_parse_trailing_text_fails(self):
        summary_key = "KEY_ABC"
        raw = f"""### Findings
No findings.

{summary_key}={{"critical": 0, "major": 0, "minor": 0, "nit": 0}}
Unexpected trailing non-empty content!
"""
        _, _, err = sanitize_and_parse_review(raw, summary_key)
        self.assertIsNotNone(err)
        self.assertIn("final non-empty output line", err)

    def test_sanitize_and_parse_html_and_url_neutralization(self):
        summary_key = "KEY_ABC"
        raw = f"""<script>evil()</script>
http://attacker.com/leak
![img](https://evil.com/img.png)

{summary_key}={{"critical": 0, "major": 0, "minor": 0, "nit": 0}}
"""
        review, _, err = sanitize_and_parse_review(raw, summary_key)
        self.assertIsNone(err)
        self.assertNotIn("<script>", review)
        self.assertIn("&lt;script&gt;", review)
        self.assertNotIn("http://attacker.com", review)
        self.assertIn("[external URL removed]", review)
        self.assertIn("[external image removed]", review)

    def test_sanitize_and_parse_invalid_json_counts(self):
        summary_key = "KEY_ABC"
        raw = f"""{summary_key}={{"critical": -1, "major": "invalid"}}"""
        _, _, err = sanitize_and_parse_review(raw, summary_key)
        self.assertIsNotNone(err)
        self.assertIn("Invalid review summary JSON", err)


if __name__ == "__main__":
    unittest.main()
