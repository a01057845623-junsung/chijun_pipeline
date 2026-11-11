# 상담·EAP 채용 공고 AI 모니터링 파이프라인 세팅 가이드

이 프로젝트는 **대학학생상담센터, EAP 위탁기업(다인, 이지웰니스 등), 청소년상담복지센터, 진로상담** 채용 공고를 매일 자동으로 수집하고, **Gemini 2.5 Flash**(또는 DeepSeek)로 핵심 자격요건을 요약하여 **Slack 카드 알림**으로 전송하는 완전 무료 서버리스 파이프라인입니다.

---

## 1단계: Slack Incoming Webhook URL 발급받기

1. 알림을 받을 슬랙 워크스페이스에 로그인한 뒤 [Slack API Apps 페이지](https://api.slack.com/apps)로 이동합니다.
2. 우측 상단의 **`Create New App`** -> **`From scratch`**를 클릭합니다.
3. **App Name**(예: `상담채용알리미`)을 입력하고, 알림을 받을 워크스페이스를 선택한 뒤 **`Create App`**을 누릅니다.
4. 좌측 메뉴에서 **`Incoming Webhooks`**를 클릭하고, 우측 상단 토글을 **`On`**으로 켭니다.
5. 페이지 하단의 **`Add New Webhook to Workspace`** 버튼을 클릭합니다.
6. 공고 알림을 수신할 슬랙 채널(예: `#채용공고-모니터링`)을 선택하고 **`허용(Allow)`**을 누릅니다.
7. 생성된 **`Webhook URL`** (`https://hooks.slack.com/services/T000.../B000.../XXXX...`) 옆의 **`Copy`** 버튼을 눌러 복사해 둡니다.

---

## 2단계: Gemini API Key 무료 발급받기

1. [Google AI Studio](https://aistudio.google.com/app/apikey)에 접속하여 구글 계정으로 로그인합니다.
2. **`Create API key`** 버튼을 클릭합니다.
3. 생성된 API 키(`AIzaSy...`로 시작하는 문자열)를 복사해 둡니다.
   - *참고: `gemini-2.5-flash` 모델은 무료 티어(Free-tier) 제공량이 넉넉하여 하루 2회 공고 요약 용도로는 비용이 전혀 발생하지 않습니다.*

---

## 3단계: GitHub 저장소 생성 및 코드 업로드

1. GitHub에서 새 저장소(Repository)를 생성합니다 (Public 또는 Private 모두 무료 범위 내에서 동작하며, 완전 무제한 무료 Actions 시간을 원하시면 **Public**을 권장합니다).
2. 현재 폴더의 파일들을 GitHub 저장소에 푸시합니다:
   ```bash
   git init
   git add .
   git commit -m "feat: initial counseling job monitoring pipeline"
   git branch -M main
   git remote add origin https://github.com/<본인계정>/<저장소이름>.git
   git push -u origin main
   ```

---

## 4단계: GitHub Secrets 및 권한 설정 (가장 중요)

### 4-1. API 키(Secrets) 등록
1. 본인의 GitHub 저장소 상단 탭에서 **`Settings`** 클릭
2. 좌측 사이드바에서 **`Secrets and variables`** -> **`Actions`** 클릭
3. **`New repository secret`** 버튼을 눌러 아래 2개의 시크릿을 각각 등록합니다:
   - **첫 번째 시크릿**
     - `Name`: `SLACK_WEBHOOK_URL`
     - `Secret`: 1단계에서 복사한 Slack Webhook URL 붙여넣기
   - **두 번째 시크릿**
     - `Name`: `GEMINI_API_KEY`
     - `Secret`: 2단계에서 복사한 Gemini API Key 붙여넣기
   - *(선택)* DeepSeek API를 사용하려면 `DEEPSEEK_API_KEY`를 추가하고, `Variables` 탭에 `LLM_PROVIDER` = `deepseek`를 등록하면 됩니다.

### 4-2. GitHub Actions 쓰기 권한 확인 (`seen_jobs.json` 자동 커밋용)
1. 저장소의 **`Settings`** -> 좌측 **`Actions`** -> **`General`** 메뉴로 이동합니다.
2. 하단의 **`Workflow permissions`** 섹션에서 **`Read and write permissions`**가 선택되어 있는지 확인하고 **`Save`**를 누릅니다. (워크플로우 파일에 `permissions: contents: write`가 선언되어 있으나, 저장소 기본 설정에서도 허용해 두면 가장 안전합니다.)

---

## 5단계: 즉시 테스트 실행해 보기

스케줄 시간(오전 8시 / 오후 1시)까지 기다리지 않고 바로 동작을 확인할 수 있습니다.

1. GitHub 저장소 상단 탭의 **`Actions`**를 클릭합니다.
2. 좌측 워크플로우 목록에서 **`Counseling & EAP Job Monitor`**를 클릭합니다.
3. 우측의 **`Run workflow`** 드롭다운 버튼을 누르고 초록색 **`Run workflow`** 버튼을 클릭합니다.
4. 약 1~2분 뒤 실행이 완료되면:
   - 지정한 **슬랙 채널**로 깔끔하게 요약된 채용 공고 카드가 도착합니다!
   - 저장소의 `seen_jobs.json` 파일에 발송 완료된 공고 해시 ID가 자동으로 커밋된 것을 확인할 수 있습니다.
