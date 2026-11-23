# Reusable Workflow 가이드

precheck(권한 검증) + ai-review가 포함된 재사용 가능한 워크플로우입니다.

## 사용 정책

| 저장소 유형 | precheck 러너 | ai-review 러너 |
|------------|---------------|----------------|
| **공개** | `ubuntu-latest` (무료) | `self-hosted` |
| **비공개** | `self-hosted` (분 수 절약) | `self-hosted` |

## Inputs

| Input | 기본값 | 설명 |
|-------|--------|------|
| `runner` | `["ubuntu-latest"]` | precheck job 러너 |
| `review-runner` | `["self-hosted", "llm-reviewer"]` | ai-review job 러너 (CLI 인증 필요) |
| `engines` | `kiro,codex,agy,claude` | 시도할 CLI 엔진 목록 |
| `effort` | `medium` | 추론 노력 수준 |
| `claude-model` | `sonnet` | claude 엔진 전용 모델(`sonnet` = 최신 Sonnet). `''`이면 러너의 Claude Code 기본 모델 |
| `max-diff-bytes` | `200000` | 리뷰 1회에 보내는 diff 한도(UTF-8 바이트). 넘으면 파일 단위로 나눠 병렬 리뷰 후 합칩니다. `/fix`는 나누지 않고 여기서 멈춥니다 |
| `max-total-diff-bytes` | `1000000` | 전체 diff 한도. 넘으면 리뷰를 하지 않고 `skipped_oversize`(승인 아님)로 끝납니다 |
| `max-parallel` | `0` | 동시에 리뷰할 묶음 수 상한(0 = 4). 실제 수는 러너의 CPU·메모리(cgroup 포함)에 맞춰 정해지고 묶음마다 다시 확인합니다 |

## 공개 저장소 설정

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
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@86406954488dc77bbf657cc639ba5bab86f366cf  # v1.3.0
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
    # runner 기본값: ubuntu-latest (precheck), self-hosted (ai-review)
```

## 비공개 저장소 설정

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
    uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@86406954488dc77bbf657cc639ba5bab86f366cf  # v1.3.0
    with:
      runner: '["self-hosted", "llm-reviewer"]'  # precheck도 self-hosted 사용
    secrets:
      token: ${{ secrets.GITHUB_TOKEN }}
```

## 보안 고려사항

### Self-hosted 러너 격리

- AI 리뷰는 CLI 인증이 있는 self-hosted 러너에서 실행됩니다
- PR 콘텐츠가 체크아웃되므로, 악성 설정 파일이 CLI 동작에 영향을 줄 수 있습니다
- **권장**: 리뷰 전용 러너를 별도로 구성하고, 최소 권한 원칙 적용
- **권장**: 민감한 인증 정보는 러너 환경이 아닌 별도 시크릿으로 관리

### Fork PR 차단

- Fork PR은 precheck에서 자동으로 차단됩니다
- `headRepository.nameWithOwner`를 비교하여 동일 저장소 브랜치만 허용
- 외부 기여자의 PR은 리뷰되지 않음 (self-hosted 러너 보호)

### 버전 핀

commit SHA로 고정합니다. 태그는 다른 커밋으로 옮겨질 수 있으므로 태그 이름은 주석으로만 남깁니다:

```yaml
uses: cakel/llm-pr-reviewer/.github/workflows/reusable-ai-review.yml@86406954488dc77bbf657cc639ba5bab86f366cf  # v1.3.0
```

reusable workflow 안의 action도 SHA로 고정되어 있습니다(v1.3.0은 `37ec826`). 그래서 위 SHA 하나로 workflow와 리뷰 코드가 함께 고정됩니다. 버전 태그는 항상 "action 고정 SHA를 올린 커밋"에 붙습니다.

### 권한 요구사항

호출하는 워크플로우에 다음 권한이 필요합니다:

```yaml
permissions:
  contents: read       # 코드 체크아웃
  pull-requests: write # 리뷰 코멘트 게시
  issues: read         # issue_comment 이벤트 처리
```

## Precheck가 검증하는 항목

1. **이벤트 유형**: `pull_request` 또는 `/review` 코멘트
2. **작성자 권한**: `write`, `maintain`, `admin` 중 하나
3. **PR 상태**: `OPEN` 상태인지 확인
4. **Fork 여부**: 동일 저장소 브랜치만 허용
5. **SHA 바인딩**: 검증된 HEAD SHA를 ai-review job에 전달
