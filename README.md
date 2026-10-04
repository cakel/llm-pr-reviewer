# Multi-CLI LLM PR Reviewer (`llm-pr-reviewer`)

> **로컬 Self-hosted 러너에 인증된 CLI(`kiro`, `codex`, `agy`)를 활용한 무비용 AI 코드리뷰 및 자동 Quota 폴백 하네스**

`llm-pr-reviewer`는 유료 API Key를 별도로 발급받지 않고, Self-hosted 러너 머신에 이미 로그인되어 있는 터미널 AI CLI(`kiro-cli`, `codex`, `agy`) 세션을 활용하여 GitHub PR을 자동 리뷰하는 GitHub Action / 워크플로우 하네스입니다.

---

## ✨ 핵심 기능

1. **멀티 CLI 엔진 & 자동 Quota Fallback**:
   - `kiro-cli`, `codex`, `agy` 중 설치 및 인증된 CLI를 자동 감지합니다.
   - 기본 우선순위(예: `kiro` $\to$ `codex` $\to$ `agy`)에 따라 실행하며, Quota 소진·인증 만료·일시 에러 발생 시 즉시 다음 도구로 폴백합니다.
   - **Last Success 캐시**: 직전에 성공한 엔진을 상태 파일에 기억(TTL 1시간)하여, 다음 PR 리뷰 시 해당 엔진을 1순위로 즉시 호출합니다.
2. **유지보수자 권한 게이팅 (Maintainer Gating)**:
   - **자동 실행**: 저장소의 `write`, `maintain`, `admin` 권한을 가진 협업자가 PR을 생성하거나 푸시한 경우에만 자동 실행됩니다.
   - **외부 기여자 보호**: Fork 또는 외부 기여자의 PR인 경우 리소스 남용 및 러너 악용을 방지하기 위해 자동 실행되지 않으며, 유지보수자가 PR에 `/review` 코멘트를 달았을 때만 실행됩니다.
3. **엄격한 러너 보안 & Prompt Injection 방어**:
   - **도구 차단**: CLI에 `--trust-tools=`(Kiro) 또는 read-only 샌드박스를 강제하여 모델이 러너 머신의 파일을 읽거나 임의 명령을 실행할 수 없습니다.
   - **Diff 검증 (Fail-Closed)**: Diff 크기(200KB 초과 차단), 서브모듈 변조(`:160000`), `.gitattributes` 인젝션, 프롬프트 탈옥 패턴(`ignore all previous instructions` 등)을 사전에 차단합니다.
4. **Jules 스타일 가독성 & 변경 이력 보존**:
   - **PR Description**: `git diff --numstat` 기반의 결정론적 파일 변경 요약 테이블 자동 갱신.
   - **PR Comment**: 이전 커밋의 리뷰는 `<details>`로 접어 스레드에 이력을 남기고, 최신 커밋의 리뷰만 확장 표시합니다.
   - **Previous Findings 추적**: 이전 리뷰의 지적사항이 현재 diff에서 해결되었는지(✅ 해결됨, ❌ 미해결, ➖ 판단불가)를 추적합니다.

---

## 🛠️ 1. Self-hosted Runner 설치 및 환경 구성

이 하네스는 CLI가 설치되어 있는 Linux Self-hosted Runner에서 동작합니다.

### A. 러너 전용 사용자 생성 및 격리 (권장)
보안을 위해 전용 계정(예: `ptreview` 또는 `actions-runner`)에서 러너를 실행하는 것을 권장합니다.

```bash
sudo useradd -m -s /bin/bash runner-user
sudo usermod -aG docker runner-user # docker 사용 시
```

### B. GitHub Actions Runner 등록
저장소 또는 Organization의 **Settings > Actions > Runners > New self-hosted runner**에서 안내하는 스크립트를 실행합니다.

```bash
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/download/...
tar xzf ./actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/YOUR_ORG/YOUR_REPO --labels self-hosted,llm-reviewer
./run.sh
```

---

## 🔑 2. CLI 도구 설치 및 로그인

러너 머신(러너 실행 사용자 계정)에 사용할 CLI 도구들을 설치하고 로그인합니다.

### 1) Kiro CLI (`kiro-cli`)
```bash
# 설치 확인
kiro-cli --version

# 브라우저 또는 IAM Identity Center 로그인
kiro-cli login
kiro-cli whoami
```

### 2) Codex CLI (`codex`)
```bash
# 설치 확인
codex --version

# 로그인
codex login
```

### 3) Antigravity CLI (`agy`)
```bash
# 설치 확인
agy --version

# 로그인 확인
agy models
```

> **참고**: 세 CLI 중 하나만 설치/인증되어 있어도 하네스가 자동 감지하여 동작합니다. 세 개 모두 구성해 두면 Quota 소진 시 무중단 자동 전환됩니다.

---

## 🚀 3. 대상 저장소 연동 가이드

리뷰를 적용할 대상 저장소의 `.github/workflows/ai-review.yml`에 아래 워크플로우를 추가합니다.

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

      - name: Run LLM PR Reviewer
        uses: cakel/llm-pr-reviewer@main
        with:
          github-token: ${{ github.token }}
          engines: "kiro,codex,agy"   # 시도할 엔진 우선순위
          effort: "medium"            # reasoning effort
```

---

## ⚙️ 액션 입력 파라미터 (Inputs)

| 파라미터 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `github-token` | `${{ github.token }}` | PR 코멘트 및 리뷰 작성을 위한 GitHub Token |
| `engines` | `"kiro,codex,agy"` | 우선순위 순서의 콤마 구분 엔진 목록 |
| `model` | `""` | 특정 모델 강제 지정 (기본값은 각 CLI의 디폴트) |
| `effort` | `"medium"` | 추론 강도 (`low`, `medium`, `high`) |
| `state-file` | `~/.llm-pr-reviewer/state.json` | 성공 엔진 캐싱 상태 파일 경로 |

---

## 📋 리뷰 출력 포맷 예시

### PR 설명 (Description) 변경 요약
```markdown
<!-- kiro-change-summary:start -->
## 변경 요약

**3개 파일** 변경 · `+120` / `-45`  
**Head:** `a1b2c3d`

<details open>
<summary>변경된 파일</summary>

| 파일 | 추가 | 삭제 |
| --- | ---: | ---: |
| `src/auth.py` | 80 | 12 |
| `src/utils.py` | 40 | 33 |

</details>
<!-- kiro-change-summary:end -->
```

### PR 코멘트 (Jules 스타일)
```markdown
## 🤖 AI Review (KIRO)

### Summary
이번 PR은 백엔드 인증 모듈을 리팩토링하고 토큰 갱신 로직을 개선합니다.

### Strengths
- 세션 탈취 방지를 위한 IP 바인딩 검증이 추가된 점이 우수합니다.

### Findings
[MAJOR]
- `src/auth.py` 라인 45-48: 토큰 만료 시간 검증에서 시간대(timezone) 처리가 누락되어 UTC 기준 불일치 시 조기 만료될 위험이 있습니다.

### Previous findings
- ✅ 해결됨 — 라인 12-15 세션 누수: 리소스 컨텍스트 매니저 도입으로 해결됨.

### Verdict
🛑 **BLOCKED — changes requested** (`VERDICT: request_changes`)  
critical 0 · major 1 · minor 0 · nit 0
```

---

## 🔒 보안 원칙 (Security Principles)

1. **Fail-Closed**: 권한 검증 API 실패, diff 보안 검증 실패 시 자동 승인을 진행하지 않고 안전하게 차단합니다.
2. **Untrusted Diff as Data**: PR diff와 이전 findings는 항상 암호학적 난수 구분자(`--- BEGIN UNTRUSTED DIFF ... ---`) 사이에 배치되며, 명령어가 아닌 순수 데이터로만 취급됩니다.
3. **No Direct Secret Sharing**: 러너의 환경 변수나 토큰을 LLM 프롬프트에 절대 노출하지 않습니다.
