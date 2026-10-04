# 아키텍처 및 상세 명세 (Architecture & Spec)

`llm-pr-reviewer`의 내부 동작 원리, 멀티 엔진 폴백 메커니즘, 기계 판독 요약 스키마 및 이력 관리 구조를 설명합니다.

---

## 1. 아키텍처 개요

```
PR Event (opened / sync) OR Comment (/review)
                     │
                     ▼
         [ 1. Maintainer Gating ]
         Collaborator 권한 (write, maintain, admin) 검증
                     │ (통과 시)
                     ▼
         [ 2. Diff Security Guard ]
         크기 제한 (200KB), 서브모듈/속성 변조, 탈옥 패턴 검사
                     │
                     ▼
      [ 3. PR Description Update ]
      git diff --numstat 기반 결정론적 변경 요약 갱신
                     │
                     ▼
     [ 4. Fetch Previous Findings ]
     직전 리뷰의 Findings 및 Commit SHA 추출
                     │
                     ▼
      [ 5. Engine Selection & Fallback ]
      State Cache (Last Success) -> Kiro -> Codex -> Agy
                     │ (정상 완료 시)
                     ▼
      [ 6. Post & Collapse Reviews ]
      이전 리뷰 <details> 접기 + 신규 리뷰 코멘트 게시
                     │
                     ▼
        [ 7. Enforce Gate Decision ]
        Critical/Major 발견 시 request-changes 및 CI 실패
```

---

## 2. 멀티 엔진 폴백 & 상태 캐시 명세

### A. 엔진 어댑터 계약 (`EngineAdapter`)
모든 CLI 어댑터(`kiro`, `codex`, `agy`)는 공통 인터페이스를 따릅니다:
1. `detect() -> bool`: CLI 실행 파일 존재 및 인증 세션 유효 여부를 검사.
2. `run(prompt, work_dir, model, effort, timeout_sec) -> (output, exit_code)`: CLI를 비대화형/읽기전용으로 호출.
3. `classify_error(output, exit_code) -> str`: Quota 소진, 인증 오류, Rate limit 등을 분류.

### B. 상태 캐시 (`StateManager`)
- **저장 위치**: `~/.llm-pr-reviewer/state.json` (기본값)
- **TTL**: 기본 3,600초 (1시간)
- **동작 방식**:
  1. 엔진이 성공적으로 리뷰를 마치면 해당 엔진 이름을 캐시에 기록.
  2. 다음 PR 리뷰 실행 시, 캐시가 유효하다면 우선순위 목록의 맨 앞으로 이동시킴.
  3. 만약 1순위로 시도한 엔진이 실패하면 즉시 다음 후보 엔진으로 순차 폴백.

---

## 3. 프롬프트 및 응답 스키마 명세

### A. 프롬프트 구조
- Delimiter: 암호학적 난수 32바이트 16진수 문자열 (`--- BEGIN UNTRUSTED PULL REQUEST DIFF <delimiter> ---`)
- 언어: 한국어 우선 작성 (코드, 식별자, API는 원본 유지)
- 섹션 구성:
  - `### Summary`: 변경점 개요 (2~4문장)
  - `### Strengths`: 긍정적인 설계/코드 포인트 (불릿 목록)
  - `### Findings`: 심각도별 지적사항 (`[CRITICAL]`, `[MAJOR]`, `[MINOR]`, `[NIT]`)
  - `### Previous findings` (직전 리뷰 존재 시): 각 항목별 ✅ 해결됨, ❌ 미해결, ➖ 판단불가
  - 기계 판독 요약 라인: `{summary_key}={"critical":0,"major":0,"minor":0,"nit":0}` (출력의 마지막 줄에 단독 위치)

### B. 판정 기준
- `critical` > 0 또는 `major` > 0: **`BLOCKED — changes requested`** (CI exit 1, GitHub PR request-changes 제출)
- `minor` 및 `nit`만 존재하거나 이슈 없음: **`APPROVED`** (CI exit 0)
