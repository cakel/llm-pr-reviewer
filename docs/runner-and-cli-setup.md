# 러너 구성 및 CLI 설치 가이드 (Runner & CLI Setup)

Self-hosted 러너 머신에 러너 서비스를 격리 등록하고, AI CLI(`kiro-cli`, `codex`, `agy`)를 설치·인증하는 방법입니다.

---

## 1. 러너 계정 분리 및 등록 (권장)

보안을 위해 메인 계정이 아닌 전용 사용자 계정에서 러너를 구동합니다.

```bash
# 1. 러너 전용 시스템 사용자 생성
sudo useradd -m -s /bin/bash actions-runner
sudo usermod -aG docker actions-runner  # docker가 필요한 경우

# 2. 러너 다운로드 및 설치
sudo -u actions-runner -i
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/download/...
tar xzf ./actions-runner-linux-x64.tar.gz

# 3. 저장소/Org 등록 (GitHub Settings > Actions > Runners 참조)
./config.sh --url https://github.com/YOUR_ORG/YOUR_REPO --labels self-hosted,llm-reviewer
./run.sh
```

---

## 2. CLI 도구 설치 및 로그인

러너 사용자 환경에 사용할 CLI를 설치하고 로그인합니다. 3개 중 사용 가능한 것만 설치해도 동작합니다.

### A. Kiro CLI (`kiro-cli`)
```bash
# 버전 확인
kiro-cli --version

# 로그인 (AWS Builder ID 또는 IAM Identity Center)
kiro-cli login

# 인증 상태 확인
kiro-cli whoami
```

### B. Codex CLI (`codex`)
```bash
# 설치 및 버전 확인
codex --version

# 로그인
codex login
```

### C. Antigravity CLI (`agy`)
```bash
# 설치 및 버전 확인
agy --version

# 모델 목록 정상 조회 확인
agy models
```

### D. Claude Code (`claude`) + Ollama / Cloud 모델 연동
Claude Code는 Anthropic의 공식 CLI이지만, `ANTHROPIC_BASE_URL` 환경 변수를 통해 Ollama의 Anthropic 호환 API(`/v1/messages`)와 직접 연결하여 저렴한 클라우드/로컬 모델을 활용할 수 있습니다.

```bash
# 1. Claude Code 버전 확인
claude --version

# 2. Ollama 실행 및 모델 확인 (예: gemma4:31b-cloud)
curl -s http://localhost:11434/api/tags

# 3. 환경 변수 설정 (기본값으로 자동 인식됨)
export ANTHROPIC_BASE_URL="http://localhost:11434"
export ANTHROPIC_API_KEY="ollama"

# 4. 테스트 실행 (tools 비활성화로 텍스트 리뷰만 수행)
claude -p "Say hello in Korean" --model "gemma4:31b-cloud" --tools ""
```
