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
   - 기본 200,000 바이트 초과 시 리뷰를 거부합니다.
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
