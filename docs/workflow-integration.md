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
        uses: cakel/llm-pr-reviewer@86406954488dc77bbf657cc639ba5bab86f366cf  # v1.3.0 — 태그·브랜치가 아닌 SHA로 고정
        with:
          github-token: ${{ github.token }}
          engines: "kiro,codex,agy"
          effort: "medium"
```

---

## 2. 방법 B: Reusable Workflow 사용

precheck(권한·fork 검증)가 포함된 `reusable-ai-review.yml`을 호출합니다. 입력과 보안 설명은 [Reusable Workflow 가이드](reusable-workflow-guide.md)를 참고하세요.

```yaml
name: AI PR Code Review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]

permissions:
  contents: read
  pull-requests: write
  issues: read

jobs:
  ai-review:
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@86406954488dc77bbf657cc639ba5bab86f366cf  # v1.3.0
    with:
      runner: '["self-hosted", "llm-reviewer"]'
      review-runner: '["self-hosted", "llm-reviewer"]'
      engines: "kiro,codex,agy"
      effort: "medium"
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

> `review.yml`은 이 저장소 자체 리뷰용이며 내부에서 action을 `@main`으로 호출합니다. 바깥을 SHA로 고정해도 리뷰 코드는 고정되지 않으므로 외부 저장소에서는 쓰지 마세요.

---

## 3. 입력 파라미터 (Action Inputs)

| 파라미터 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `github-token` | `${{ github.token }}` | GitHub API 호출 및 코멘트 작성을 위한 토큰 |
| `engines` | `"kiro,codex,agy"` | 시도할 CLI 엔진 우선순위 (콤마 구분) |
| `model` | `""` | 모든 엔진에 넘기는 모델 (기본값: 각 CLI의 디폴트 모델). 자기 모델 입력이 있는 엔진(claude)에는 그쪽이 우선 |
| `claude-model` | `"sonnet"` | claude 엔진 전용 모델. `sonnet`은 최신 Sonnet 별칭. `""`이면 `model`, 그것도 없으면 러너의 Claude Code 기본 모델. `model`은 kiro·codex·agy에도 넘어가므로 Claude 모델 이름은 여기에 지정 |
| `effort` | `"medium"` | 추론 강도 (`low`, `medium`, `high`) |
| `state-file` | `~/.llm-pr-reviewer/state.json` | 성공 엔진 상태 파일 경로 |
| `max-diff-bytes` | `"200000"` | 리뷰 1회에 보내는 diff 한도(UTF-8 바이트). 넘으면 파일 단위로 나눠 병렬 리뷰 후 합침. `/fix`는 나누지 않고 여기서 멈춤 |
| `max-total-diff-bytes` | `"1000000"` | 전체 diff 한도. 넘으면 리뷰하지 않고 `skipped_oversize`(승인 아님) |
| `max-parallel` | `"0"` | 동시에 리뷰할 묶음 수 상한(0 = 4). 실제 수는 러너 CPU·메모리(cgroup 포함)에 맞춰 묶음마다 다시 확인 |
