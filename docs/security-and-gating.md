# 보안 및 권한 게이팅 (Security & Gating)

Self-hosted 러너 환경에서 임의의 외부 PR을 안전하게 검증하기 위한 보안 계층과 권한 모델을 설명합니다.

---

## 1. 유지보수자 권한 게이팅 (Maintainer Gating)

공개(Public) 및 사설(Private) 저장소 모두에서 Self-hosted 러너의 자원 남용과 임의 코드 실행을 방지하기 위해 2단계 게이팅을 적용합니다.

### A. 자동 트리거 (Auto-Trigger)
- **대상**: PR 작성자(`pr_author`) 또는 커밋 푸셔(`actor`)가 대상 저장소의 `admin`, `maintain`, `write` 권한을 가진 협업자인 경우.
- **동작**: PR `opened`, `synchronize`, `reopened` 이벤트 시 자동으로 LLM 리뷰 실행.

### B. 수동 트리거 (Manual Trigger for Fork / External PR)
- **대상**: 외부 기여자나 Fork 저장소에서 들어온 PR.
- **동작**: 자동 실행되지 않으며, 유지보수자가 PR 코멘트로 `/review` 명령어를 입력할 때만 API로 작성자 권한을 실시간 검증한 후 1회 실행.
- **비인가자 코멘트 차단**: 권한 없는 사용자가 `/review`를 입력해도 권한 검사에서 즉시 거부(Fail-closed)됩니다.

---

## 2. Diff 보안 검증 (`DiffGuard`)

외부 PR diff는 신뢰할 수 없는 데이터(Untrusted Input)이므로 LLM 및 Git 실행 전 다음 항목을 검사합니다:

1. **크기 제한 (Max Size Limit)**:
   - 리뷰 1회에 보내는 diff는 `max-diff-bytes`(기본 200,000 바이트, UTF-8) 이하입니다. 더 큰 diff는 파일 단위(큰 파일은 hunk 단위, 파일 헤더 반복)로 나눠 묶음마다 따로 리뷰하고, 결과를 합쳐 하나의 코멘트로 올립니다. 심각도 개수는 묶음의 합이고, 한 묶음이라도 모든 엔진에서 실패하면 전체 리뷰가 실패합니다(일부만 보고 승인하지 않음).
   - 묶음 프롬프트에는 "전체 PR 중 k/N 묶음"이라는 사실과 PR 전체 변경 파일 목록(신뢰하지 않는 데이터 구분자 안)을 넣어, 다른 묶음에 있을 정의·호출부를 누락으로 지적하지 않게 합니다. 묶음 사이에 걸친 문제는 놓칠 수 있으며, 코멘트 Summary에 그렇게 표시합니다.
   - 묶음은 병렬로 리뷰합니다. 각 리뷰는 별도 엔진 CLI 프로세스(묶음별 작업 디렉터리)입니다. 리뷰 머신 사양이 제각각이므로 동시 실행 수는 실행 시점의 자원으로 정합니다.
     - 최대 동시 수 = min(묶음 수, 상한, 사용 가능 CPU, 사용 가능 메모리 / 512 MiB), 최소 1. CPU는 affinity와 cgroup `cpu.max`, 메모리는 `MemAvailable`과 cgroup `memory.max - memory.current` 중 작은 값입니다. cgroup은 이 프로세스의 cgroup부터 상위까지 보므로, systemd 서비스로 돌리는 러너의 `MemoryMax`/`CPUQuota`도 반영됩니다. (512 MiB는 엔진 CLI 1개의 측정 최대 RSS — kiro-cli 약 170 MB, claude 약 265 MB — 에 여유를 둔 값입니다.)
     - 묶음을 하나 시작할 때마다 다시 확인합니다. 같은 러너의 다른 job 때문에 메모리가 512 MiB 아래로 떨어지거나 1분 load average가 CPU 수 이상이면, 실행 중인 묶음이 끝날 때까지 기다립니다. 실행 중인 묶음이 없으면 항상 시작하므로 멈추지 않습니다.
     - 상한은 `max-parallel`(기본 0 = 4)입니다. 같은 계정으로 동시 세션이 많으면 rate limit(429)만 늘기 때문에 기본 4로 두었고, 큰 머신·넉넉한 요금제에서는 올릴 수 있습니다.
   - 전체 diff가 `max-total-diff-bytes`(기본 1,000,000 바이트)를 넘으면 리뷰를 거부합니다. `/fix`는 나누지 않으므로 `max-diff-bytes`에서 거부합니다.
   - 프롬프트를 argv로만 받는 엔진(agy)은 Linux 인자 한도(128 KiB) 때문에 120,000 바이트를 넘는 프롬프트에서 건너뛰고 다음 엔진으로 넘어갑니다. claude/kiro/codex는 stdin으로 받습니다.
   - 한도를 넘으면 리뷰를 하지 않고 `decision=skipped_oversize`(중립)로 끝냅니다. 체크를 실패로 두지 않는 이유: 빨간 체크는 "코드를 고쳐 다시 push하라"로 읽혀, 에이전트가 같은 oversize 결과를 무한히 재시도했습니다.
   - `skipped_oversize`는 승인이 아닙니다. 머지 게이트는 `decision == approve`로 걸어야 큰 diff로 리뷰를 우회할 수 없습니다.
   - PR에 "같은 oversize 결과가 나오니 추가 리뷰를 요청하지 말라"는 중지 안내를 달고, 이 커밋을 보지 않은 이전 리뷰는 접습니다.
2. **서브모듈 변조 감지 (`:160000`)**:
   - 악성 외부 저장소를 참조하는 서브모듈 커밋이 포함된 경우 거부합니다.
3. **`.gitattributes` 인젝션 방어**:
   - `diff.external` 또는 `textconv` 등을 조작하여 임의 바이너리를 실행하려는 시도를 차단합니다.
4. **리뷰 제어 패턴 (Prompt Injection)**:
   - diff에 추가된 라인(`+`)에 `ignore all previous instructions`, `system prompt`, `review_summary_json` 등의 탈옥 패턴이 포함되어 있으면 즉시 리뷰를 중단합니다.

---

## 3. CLI 도구 실행 차단 (Tool Denial)

리뷰 엔진은 오직 **입력받은 diff 텍스트를 읽고 리뷰 마크다운을 출력하는 순수 텍스트 함수(Pure text in/out)**로만 작동해야 합니다:
- **Kiro**: `--trust-tools=` 옵션을 주어 파일시스템 읽기(`fs_read`), 쓰기(`fs_write`), 명령 실행(`execute_bash`)을 완전히 거부합니다.
- **Codex**: `-s read-only` 및 `--ignore-rules`, `--ignore-user-config` 모드로 격리합니다.
- **Agy**: 헤드리스 모드에서 도구 권한 요청을 자동 거부합니다.
