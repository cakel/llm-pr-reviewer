# Multi-CLI LLM PR Reviewer (`llm-pr-reviewer`)

> **Self-hosted 러너에 인증된 CLI(`kiro`, `codex`, `agy`, `claude`) 세션을 활용한 무비용 AI 코드리뷰 및 자동 Quota 폴백 하네스**

## ⚡ 주요 특징

* **멀티 CLI 엔진 & Quota Fallback**: 한 도구의 할당량 소진 시 무중단으로 다음 도구로 자동 전환
* **Precheck 권한 검증**: Fork PR 차단, Collaborator 권한 확인
* **이전 지적사항 추적**: 해결 여부(✅/❌) 자동 표시

---

## 🚀 빠른 시작

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
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@b764845ad7f07d4c39b7b3471acac5bd03174d11  # v1.0.0
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
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
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@b764845ad7f07d4c39b7b3471acac5bd03174d11  # v1.0.0
    with:
      runner: '["self-hosted", "llm-reviewer"]'
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

---

## 📚 문서

| 문서 | 설명 |
| :--- | :--- |
| **[Reusable Workflow 가이드](docs/reusable-workflow-guide.md)** | Inputs, 보안 고려사항, 공개/비공개 설정 |
| **[아키텍처 및 상세 명세](docs/architecture-and-spec.md)** | 파이프라인 흐름, 어댑터, 프롬프트 스키마 |
| **[보안 및 권한 게이팅](docs/security-and-gating.md)** | 권한 검증, Fork 차단, Diff 안전성 |
| **[러너 및 CLI 설치](docs/runner-and-cli-setup.md)** | kiro-cli, codex, agy, claude 설치 |
| **[연동 가이드](docs/integration-guide.md)** | 인라인 구현 vs 액션 사용 |

---

## 📄 라이선스

MIT License.

