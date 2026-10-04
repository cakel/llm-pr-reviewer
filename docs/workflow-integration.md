# 타 저장소 연동 가이드 (Workflow Integration)

개별 프로젝트 저장소에서 `llm-pr-reviewer`를 연동하는 2가지 방법(Composite Action 및 Reusable Workflow)과 전체 설정 옵션입니다.

---

## 1. 방법 A: Composite Action 사용 (가장 유연함, 권장)

대상 저장소의 `.github/workflows/ai-review.yml`을 작성합니다.

```yaml
name: AI PR Code Review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: write

concurrency:
  group: ai-review-pr-${{ github.event.pull_request.number || github.event.issue.number }}
  cancel-in-progress: true

jobs:
  review:
    runs-on: [self-hosted, llm-reviewer]
    timeout-minutes: 15
    steps:
      - name: Checkout Code
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0

      - name: Run Multi-CLI LLM Reviewer
        uses: cakel/llm-pr-reviewer@main
        with:
          github-token: ${{ github.token }}
          engines: "kiro,codex,agy"
          effort: "medium"
```

---

## 2. 방법 B: Reusable Workflow 사용

```yaml
name: AI PR Code Review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]

jobs:
  call-reviewer:
    uses: cakel/llm-pr-reviewer/.github/workflows/review.yml@main
    with:
      runs-on: '["self-hosted", "llm-reviewer"]'
      engines: "kiro,codex,agy"
      effort: "medium"
```

---

## 3. 입력 파라미터 (Action Inputs)

| 파라미터 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `github-token` | `${{ github.token }}` | GitHub API 호출 및 코멘트 작성을 위한 토큰 |
| `engines` | `"kiro,codex,agy"` | 시도할 CLI 엔진 우선순위 (콤마 구분) |
| `model` | `""` | 특정 모델 강제 지정 (기본값: 각 CLI의 디폴트 모델) |
| `effort` | `"medium"` | 추론 강도 (`low`, `medium`, `high`) |
| `state-file` | `~/.llm-pr-reviewer/state.json` | 성공 엔진 상태 파일 경로 |
