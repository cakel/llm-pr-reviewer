# Multi-CLI LLM PR Reviewer (`llm-pr-reviewer`)

> **Self-hosted 러너에 인증된 CLI(`kiro`, `codex`, `agy`, `claude`) 세션을 활용한 무비용 AI 코드리뷰 및 자동 Quota 폴백 하네스**

`llm-pr-reviewer`는 별도의 유료 API Key 발급 없이, 러너 머신에 이미 로그인되어 있는 터미널 AI CLI(`kiro-cli`, `codex`, `agy`) 세션을 활용하여 GitHub PR을 자동 리뷰하는 GitHub Action / Reusable Workflow입니다.

---

## ⚡ 주요 특징

* **멀티 CLI 엔진 & Quota Fallback**: `kiro-cli` $\to$ `codex` $\to$ `agy` $\to$ `claude` 순으로 시도하며, 한 도구의 할당량(Quota) 소진 시 무중단으로 다음 도구로 자동 전환됩니다.
* **Last Success 캐시**: 직전에 성공한 엔진을 기억(TTL 1시간)하여, 다음 PR 리뷰 시 해당 도구를 1순위로 즉시 호출합니다.
* **유지보수자 권한 게이팅**: 저장소 Collaborator(`write`, `maintain`, `admin`)의 PR만 자동 실행하며, 외부 기여자 PR은 `/review` 코멘트 승인 시에만 동작하여 러너 자원을 보호합니다.
* **Jules 스타일 가독성 & 이력 추적**:
  * **PR 설명**: `git diff --numstat` 기반의 결정론적 파일 변경 테이블 갱신
  * **PR 코멘트**: 이전 리뷰는 `<details>`로 접고 최신 리뷰만 확장 표시하며, 이전 지적사항의 해결 여부(✅ 해결됨, ❌ 미해결)를 추적합니다.

---

## 🚀 빠른 시작 (Quick Start)

대상 저장소의 `.github/workflows/ai-review.yml`에 추가합니다:

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
    runs-on: [self-hosted, llm-reviewer]   # CLI가 설치된 러너 라벨
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
          engines: "kiro,codex,agy,claude"
```

---
## 🔄 Reusable Workflow (권장)

precheck(권한 검증) + ai-review가 포함된 재사용 가능한 워크플로우입니다.

### 공개 저장소
```yaml
name: AI Code Review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: read

jobs:
  ai-review:
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@main
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
    # runner 기본값: ubuntu-latest (precheck), self-hosted (ai-review)
```

### 비공개 저장소
```yaml
name: AI Code Review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: read

jobs:
  ai-review:
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@main
    with:
      runner: '["self-hosted", "llm-reviewer"]'  # precheck도 self-hosted 사용 (분 수 절약)
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```


### 보안 고려사항

**Self-hosted 러너 격리:**
- AI 리뷰는 CLI 인증이 있는 self-hosted 러너에서 실행됩니다
- PR 콘텐츠가 체크아웃되므로, 악성 설정 파일이 CLI 동작에 영향을 줄 수 있습니다
- **권장**: 리뷰 전용 러너를 별도로 구성하고, 최소 권한 원칙 적용
- **권장**: 민감한 인증 정보는 러너 환경이 아닌 별도 시크릿으로 관리

**Fork PR 차단:**
- Fork PR은 precheck에서 자동으로 차단됩니다
- 동일 저장소의 브랜치만 리뷰 대상

**SHA 핀:**
- 프로덕션 환경에서는 `@main` 대신 commit SHA로 고정 권장
- 예: `uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@<SHA>`

### Inputs

| Input | 기본값 | 설명 |
|-------|--------|------|
| `runner` | `ubuntu-latest` | precheck job 러너 (공개: ubuntu-latest, 비공개: self-hosted) |
| `review-runner` | `self-hosted,llm-reviewer` | ai-review job 러너 (CLI 인증 필요) |
| `engines` | `kiro,codex,agy,claude` | 시도할 CLI 엔진 목록 |
| `effort` | `medium` | 추론 노력 수준 |


---

## 📚 상세 문서 (Documentation)

자세한 설정 방법과 내부 사양은 아래 문서에서 확인하실 수 있습니다:

| 문서 | 설명 |
| :--- | :--- |
| **[아키텍처 및 상세 명세](docs/architecture-and-spec.md)** | 전체 파이프라인 흐름, 어댑터 명세, 프롬프트 및 응답 스키마, 상태 캐시 |
| **[보안 및 권한 게이팅](docs/security-and-gating.md)** | 유지보수자 권한 검증, 외부 PR `/review` 트리거, Diff 안전성 검사, 도구 차단 |
| **[러너 및 CLI 설치 가이드](docs/runner-and-cli-setup.md)** | 러너 전용 사용자 계정 분리, `kiro-cli` / `codex` / `agy` 설치 및 로그인 |
| **[타 저장소 연동 가이드](docs/workflow-integration.md)** | Composite Action vs Reusable Workflow 사용법, 입력 파라미터(Inputs) 목록 |

---

## 📄 라이선스

MIT License.
