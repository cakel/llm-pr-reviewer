# Self-Hosted Runner 보안 강화 및 운영 가이드라인
> **Security Hardening & Operations Guide for LLM CI Self-Hosted Runners**  
> 대상: 로컬/프라이빗 인프라에서 LLM 자동 코드 검토 및 AI 수정 제안을 수행하는 Self-Hosted GitHub Actions 러너

---

## 1. 개요 및 위협 모델 (Threat Model)

GitHub Actions의 셀프 호스티드 러너(Self-Hosted Runner)는 클라우드 러너와 달리 **내부 네트워크, 호스트 머신의 파일시스템, 영구 자격증명(SSH 키, API 토큰 등)**에 직접 접근할 수 있는 환경에서 동작합니다.

따라서 풀 리퀘스트(PR) 코드 검토를 셀프 호스티드 러너에 맡길 경우 다음과 같은 심각한 보안 위협이 존재합니다:

1. **호스트 자격증명 및 데이터 탈취**: PR 코드나 빌드 스크립트가 실행되면서 개발자의 개인 홈 디렉토리(`~/.ssh`, `~/.aws`, `.env`, 개인 API 키)를 읽어 유출.
2. **LLM 도구 실행을 통한 시스템 침해**: LLM 에이전트가 도구(Tool/Bash) 실행 권한을 가진 채 실행되어 시스템 파일을 변조하거나 임의 명령 실행.
3. **프롬프트 인젝션 (Prompt Injection)**: PR diff 내에 악의적인 프롬프트 마커를 심어 AI 리뷰어가 보안 결함을 무시하고 자동 승인(`APPROVE`)하도록 조작.
4. **데이터 외부 유출 (Data Exfiltration)**: 러너 내부 데이터를 외부 공격자 서버로 `curl`/`wget` 전송.
5. **서플라이 체인 및 워크플로우 하이재킹**: 가변 태그(`@main`) 액션을 사용하거나 `.github/workflows/` 파일을 무단 수정하여 권한 탈취.

본 문서는 이러한 위협을 **7단계 심층 방어(Defense-in-Depth)** 체계로 완벽히 무력화하는 표준 보안 아키텍처와 구축 방법을 제시합니다.

---

## 2. 7계층 심층 방어 아키텍처 (Defense-in-Depth)

```
[PR Push or /review /fix Comment]
                 │
  (Layer 1) Fork 차단 & 협력자 권한 검증 (Gating)
                 │ (Pass)
  (Layer 2) 클라우드 단위 테스트 선행 (needs: unit-test)
                 │ (Success)
  (Layer 3) Diff & 민감 경로 가드 (200KB 상한, 인젝션 마커 검사, .github/workflows 감지)
                 │ (Safe)
  (Layer 4) OS 전용 격리 계정 (llm-reviewer: 홈 디렉토리 700 격리)
                 │
  (Layer 5) systemd 리눅스 커널 샌드박싱 (ProtectSystem, PrivateTmp, NoNewPrivileges)
                 │
  (Layer 6) 네트워크 아웃바운드(Egress) 방화벽 (iptables: GitHub & Ollama만 허용)
                 │
  (Layer 7) LLM 도구 실행 원천 차단 (Pure Text In/Out: --trust-tools=)
                 ▼
[안전한 Jules 리뷰 코멘트 게시 & /fix 수정안 제안]
```

---

## 3. 단계별 구축 및 설정 방법

### 3.1. OS 전용 격리 계정 생성
러너를 일반 사용자(`cakel`, `ubuntu` 등) 계정으로 구동하면 개인 홈 디렉토리가 노출됩니다. 로그인 권한이 없는 전용 시스템 계정을 생성합니다.

```bash
# 리뷰 전용 시스템 계정 생성 (로그인 쉘 비활성화)
sudo useradd -r -s /usr/sbin/nologin -d /var/lib/llm-reviewer -m llm-reviewer

# 일반 사용자 홈 디렉토리 권한 잠금 (llm-reviewer 사용자가 절대 읽지 못하도록 설정)
chmod 700 /home/cakel
```

### 3.2. 러너 디렉토리 권한 설정
```bash
sudo mkdir -p /opt/actions-runner-profittrailer-isolated
sudo chown -R llm-reviewer:llm-reviewer /opt/actions-runner-profittrailer-isolated
sudo chown -R llm-reviewer:llm-reviewer /var/lib/llm-reviewer
```

### 3.3. systemd 커널 레벨 샌드박싱 (`override.conf`)
systemd 유닛에 리눅스 커널 격리 지시자를 적용하여, 러너 프로세스가 탈취되더라도 루트 파일시스템 수정, 새 권한 획득, 디바이스 접근이 불가능하도록 차단합니다.

`/etc/systemd/system/actions.runner.<repo>.<runner-name>.service.d/override.conf`:
```ini
[Service]
# 권한 상승 및 su/sudo 실행 원천 차단
NoNewPrivileges=yes

# 프로세스 격리 임시 폴더 제공 (/tmp 공유 차단)
PrivateTmp=yes

# 루트 파일시스템 완전 읽기 전용 (ReadWritePaths만 쓰기 허용)
ProtectSystem=full

# 다른 사용자의 /home 디렉토리 접근 차단
ProtectHome=yes

# 커널 튜닝 파라미터(/proc/sys, /sys) 수정 금지
ProtectKernelTunables=yes

# 컨트롤 그룹(cgroups) 계층 수정 금지
ProtectControlGroups=yes

# 커널 모듈 로드 금지
ProtectKernelModules=yes

# 쓰기 허용 경로 엄격히 한정
ReadWritePaths=/opt/actions-runner-profittrailer-isolated /var/lib/llm-reviewer
```

적용:
```bash
sudo systemctl daemon-reload
sudo systemctl restart actions.runner.<repo>.<runner-name>.service
```

### 3.4. 네트워크 아웃바운드(Egress) 필터링
러너 프로세스가 임의의 외부 공격자 C2 서버로 통신하는 것을 차단합니다. `iptables`의 `owner` 모듈을 사용하여 `llm-reviewer` UID에만 엄격한 화이트리스트를 적용합니다.

```bash
# 1. 로컬 호스트 허용 (로컬 Ollama LLM http://localhost:11434 연동)
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -d 127.0.0.1 -j ACCEPT
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -d ::1 -j ACCEPT

# 2. DNS 질의 허용 (UDP/TCP 53)
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -p udp --dport 53 -j ACCEPT
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -p tcp --dport 53 -j ACCEPT

# 3. 기존 수립된 연결 허용
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -m state --state ESTABLISHED,RELATED -j ACCEPT

# 4. HTTPS (443) 통신 허용 (GitHub API 및 공식 LLM 엔드포인트)
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -p tcp --dport 443 -j ACCEPT

# 5. 그 외 모든 외부 아웃바운드 패킷 원천 거부 (리버스 쉘, 임의 포트 데이터 유출 차단)
sudo iptables -A OUTPUT -m owner --uid-owner llm-reviewer -j REJECT --reject-with icmp-port-unreachable
```

---

## 4. GitHub Actions 워크플로우 구성 (`ai-review.yml`)

### 4.1. 보안 워크플로우 예시
```yaml
name: Unit Tests and AI Code Review

on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize]
  issue_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: read

concurrency:
  group: ai-review-pr-${{ github.event.pull_request.number || github.event.issue.number }}
  cancel-in-progress: true

jobs:
  # 1. 포크 차단 및 클라우드 단위 테스트 (안전한 GitHub 호스티드 러너)
  unit-test:
    if: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository }}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # Immutable SHA pin
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
          clean: true
          persist-credentials: false
      - name: Run unit tests
        run: python3 -m unittest discover -v

  # 2. 격리 샌드박스 러너에서 다중 LLM 리뷰 실행
  ai-review:
    needs: unit-test
    if: |
      always() &&
      (
        (github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository && needs.unit-test.result == 'success') ||
        (github.event_name == 'issue_comment' && github.event.issue.pull_request && (startsWith(github.event.comment.body, '/review') || startsWith(github.event.comment.body, '/fix')))
      )
    runs-on: [self-hosted, Linux, X64, profittrailer-review-isolated]
    timeout-minutes: 15
    env:
      KIRO_HOME: /var/lib/llm-reviewer/.kiro
    steps:
      - name: Checkout pull request ref
        uses: actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
        with:
          fetch-depth: 0
          clean: true
          persist-credentials: false

      - name: Run Multi-CLI LLM Reviewer
        # 반드시 불변 커밋 SHA로 핀하여 공급망 공격 차단
        uses: cakel/llm-pr-reviewer@ecdb04bd2df4e86f933aaebf35ce6acc6e0bf7df # v0.2.0
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          engines: "kiro,codex,agy,claude"
```

---

## 5. 운영 인터랙션 (대화형 커맨드)

### 5.1. 자동 Push 검토
* PR에 새 커밋이 푸시되면 단위 테스트 통과 후 즉시 자동 실행됩니다.
* PR 설명(Description)에 deterministic numstat 변경표가 자동 갱신됩니다.
* 이전 커밋의 리뷰는 `<details>` 블록으로 자동 접히며, 해결된 지적 사항은 `✅ 해결됨`으로 상태가 추적됩니다.

### 5.2. `/review [방향성]` (추가 검토 및 방향성 지정)
* 관리자가 특정 관점에 집중하여 재검토를 요청할 때 사용합니다.
* 예시:
  ```markdown
  /review 비동기 동시성 처리 및 race condition 가능성 위주로 깊게 검토해줘
  ```
* 하네스가 이전 리뷰 이력과 메인테이너의 방향성 가이드를 프롬프트에 주입하여 집중 검토를 수행합니다.

### 5.3. `/fix [수정 방향성]` (AI 구체 수정 코드 생성)
* 리뷰 지적 사항에 대해 구체적인 코드 패치나 유닛 diff를 요청할 때 사용합니다.
* 예시:
  ```markdown
  /fix 이전 리뷰에서 지적된 200KB 상한 검사 및 예외 처리 로직에 대해 구체적인 코드 패치를 제안해줘
  ```
* 하네스가 `## 🛠️ AI Fix Suggestions` 코멘트로 구체적인 수정 파일과 diff 블록을 생성해 PR에 게시합니다.

---

## 6. 프로덕션 점검 체크리스트 (Production Checklist)

| 구분 | 점검 항목 | 권장 설정 | 적용 여부 |
| :--- | :--- | :--- | :---: |
| **Gating** | 포크(Fork) 저장소 PR 차단 | `github.event.pull_request.head.repo.full_name == github.repository` | [x] |
| **Gating** | 단위 테스트 선행 통과 필수화 | `needs: unit-test` (`result == 'success'`) | [x] |
| **Gating** | 협력자(Maintainer) 권한 확인 | Write / Maintain / Admin 권한자만 트리거 허용 | [x] |
| **Diff** | Diff 크기 상한 제한 | 최대 200,000 바이트 (초과 시 거부) | [x] |
| **Diff** | 서브모듈 변조 거부 | `diff --raw` 160000 모드 체인지 차단 | [x] |
| **Diff** | 민감 경로 변조 감지 | `.github/workflows/**`, `systemd`, `.env` 경고 표시 | [x] |
| **Diff** | 프롬프트 인젝션 패턴 차단 | 지시 무시(prompt bypass) 시도 등 정규식 차단 | [x] |
| **OS** | 전용 시스템 계정 격리 | `nologin` 쉘, 사용자 홈 `chmod 700` | [x] |
| **systemd** | 파일시스템 및 권한 격리 | `NoNewPrivileges=yes`, `ProtectSystem=full`, `PrivateTmp=yes` | [x] |
| **Network** | 아웃바운드(Egress) 방화벽 | iptables owner 매칭 (Localhost, DNS, 443 외 차단) | [x] |
| **Supply-Chain** | GitHub Action 불변 SHA 핀 | `@main` 금지, 40자리 풀 커밋 SHA 핀 (`@ecdb04...`) | [x] |
| **LLM Engine** | 도구 실행 원천 거부 | `--trust-tools=` (순수 텍스트 입출력 전용) | [x] |
