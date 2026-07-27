# Veronica booking operations

베로니카 예약 Edge Function의 배포 순서와 필수 설정 이름을 정리한다. 이 문서에는 Secret 값을 기록하지 않는다.

## 현재 범위

- 구현됨: 공용 로그인, 30일 서버 세션, 공개 예약 현황, 회원·예약 조회, 예약 생성·변경·취소, 암호화 연락처, 감사 이력, 개인정보를 제거한 알림 outbox.
- 아직 미연동: Edge Function 쪽 Google Calendar 생성·변경·취소와 `[예약불가]` 조회. 설정 전에는 API가 `calendarAvailable: false`를 명시한다. (Mac 소비기 쪽 Calendar 발송 레그는 아래에 구현됨.)
- 구현·활성: Mac의 예약 outbox 소비기(`scripts/consume_booking_outbox.py`)와 2단 발송 래퍼(`scripts/send_booking_integrations.py`)가 LaunchAgent로 30초마다 실행된다. 래퍼는 두 개의 멱등 발송 레그를 순서대로 실행한다 — 먼저 Google Calendar 발송기(`scripts/send_google_calendar_booking.py`), 그다음 Discord 발송기(`scripts/send_discord_booking_alert.py`). **두 레그가 모두 쓰기 후 재조회 검증에 성공해야만** 해당 행을 delivered로 표시한다. Edge Function은 개인정보를 제거한 outbox 행만 만들고, 소비기는 Mac에서 그 행을 읽어 래퍼로 넘긴다. 전화번호·탑승자 명단·취소코드·서약 원문을 payload에 넣지 않는다.
- 일시 중단: 카카오 발송 채널은 현재 **중단(paused)** 상태이며 이 저장소에서 사용하지 않는다. 카카오 SSoT 스킬(`22_2_tools/skills/asteria-kakao-booking-alerts`)은 별개의 중단된 채널로 보존만 하며, 예약 알림 대상은 Discord 스레드다.
- `/veronica/`는 staging 경로이며 운영 홈페이지 내비게이션에서 아직 연결하지 않는다.

## Secret 이름

필수 사용자 설정:

- `ASTERIA_PASSWORD_HASH`: `pbkdf2_sha256$반복수$salt$base64digest` 형식
- `ASTERIA_CONTACT_ENCRYPTION_KEY`: 32바이트 키의 base64 또는 64자리 hex
- `ASTERIA_ORIGIN_ALLOWLIST`: 쉼표로 구분한 정확한 HTTPS Origin 목록
- `ASTERIA_LOGIN_ID`: 기본값 `clubasteria`
- `ASTERIA_SESSION_VERSION`: 공용 비밀번호 변경 시 증가
- `ASTERIA_COOKIE_SAMESITE`: 같은 출처 프록시 기준 기본값 `Lax`
- `ASTERIA_COOKIE_PATH`: 기본값 `/api/veronica`

Cloudflare Pages의 선택적 비밀 아닌 환경 변수:

- `VERONICA_UPSTREAM_BASE`: preview에서 upstream을 바꿀 때만 사용한다. 운영 Pages Function에는 비밀이 아닌 기본 upstream URL이 있고 브라우저는 항상 `/api/veronica`만 호출한다.

Supabase가 Function 런타임에 주입:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Calendar 연동 시에만 추가:

- `VERONICA_CALENDAR_ID`
- `VERONICA_CALENDAR_OAUTH_CLIENT_ID`
- `VERONICA_CALENDAR_OAUTH_CLIENT_SECRET`
- `VERONICA_CALENDAR_OAUTH_REFRESH_TOKEN`

## 배포 순서

1. 로컬 검증을 모두 통과시킨다.
2. 추가 마이그레이션 `20260727090000_veronica_booking_rpcs.sql`을 원격 DB에 적용한다.
3. Secret을 Supabase Dashboard 또는 CLI의 안전한 입력 경로로 등록한다. 값은 셸 이력, argv, Git, 채팅에 남기지 않는다.
4. `veronica-booking` Function을 `--no-verify-jwt --import-map supabase/functions/deno.json`으로 배포한다. 자체 HttpOnly 세션을 검증하는 공개 로그인·availability 엔드포인트가 있고, 서버 번들러가 import map을 자동 탐지하지 못할 수 있기 때문이다.
5. Cloudflare Pages에 같은 출처 `/api/veronica` 프록시와 `/veronica/` UI를 배포한다. preview upstream을 바꿀 때만 `VERONICA_UPSTREAM_BASE`를 등록하며 서비스 키는 절대 넣지 않는다.
6. staging Origin이 `ASTERIA_ORIGIN_ALLOWLIST`와 정확히 일치하는지 확인한다. 브라우저가 `supabase.co`를 직접 호출하지 않는지도 확인한다.
7. 공개 availability, 로그인, 새로고침 후 세션 재개, 예약 생성·조회·변경·취소를 순서대로 검증한다.
8. Calendar와 Discord 알림은 별도 승인과 dry-run을 통과할 때까지 운영 공개의 차단 조건이다. 카카오 채널은 중단 상태로 사용하지 않는다.

## 로컬 검증

```bash
npx --yes deno fmt --check supabase/functions
npx --yes deno lint supabase/functions
npx --yes deno check --config supabase/functions/deno.json supabase/functions/veronica-booking/index.ts
npx --yes deno test --allow-read supabase/functions/tests
python3 supabase/tests/verify_schema.py
python3 supabase/tests/verify_followup.py
python3 supabase/tests/scan_secrets.py
git diff --check
```

## staging 확인

- 허용하지 않은 Origin의 로그인과 모든 쓰기가 거부된다.
- 로그인 쿠키에 `HttpOnly`, `Secure`, 설정한 `SameSite`, 30일 `Max-Age`가 붙는다.
- 로그아웃·생성·변경·취소는 세션 CSRF 토큰 없이는 거부된다.
- 동일 시간 예약은 하나만 성공하고 충돌 요청은 `409`다.
- 공개 availability에는 점유 시작·종료 시각만 있으며 예약자명·목적지·내부 ID·전화번호·탑승자·Calendar ID가 없다.
- outbox payload에 전화번호·탑승자·취소코드·서약 원문이 없다.
- 실제 Calendar/Discord가 미설정인 동안 연동 성공 문구를 표시하거나 전송을 재시도하지 않는다. 카카오는 중단 채널로 전송하지 않는다.

## Mac 예약 outbox 소비기 (Calendar + Discord)

웹 백엔드는 `public.notification_outbox`에 개인정보를 제거한 행만 기록한다. Mac 쪽 소비기 `scripts/consume_booking_outbox.py`가 그 행을 원자적으로 선점해 허용 목록에 고정된 2단 발송 래퍼 `scripts/send_booking_integrations.py`로 넘긴다. 래퍼는 두 멱등 레그를 순서대로 실행한다: **① Google Calendar 발송기 `scripts/send_google_calendar_booking.py` → ② Discord 발송기 `scripts/send_discord_booking_alert.py`.** Calendar가 먼저 실행되고, Calendar가 exit 0으로 검증된 뒤에만 Discord를 실행한다. **두 레그가 모두 쓰기 후 재조회 검증에 성공(exit 0)해야만** 소비기가 해당 행을 delivered로 표시한다. 어느 한 레그라도 재시도 가능(exit 2) 또는 불확정(exit 3)이면 각각 백오프 재시도·수동 검토로 처리한다.

네 스크립트는 모두 stdlib만 사용한다. Supabase 서비스 롤 비밀과 Discord 봇 토큰은 실행 시점에 Keychain에서만 읽고, Google OAuth 자격증명(client id/secret·refresh token·token uri)은 고정된 0600 자격증명 파일에서만 읽으며, 어느 것도 로그·설정·소스·argv·예외·HTTP 응답 본문에 절대 남기지 않는다. 발급된 access token도 메모리에서만 사용한다. 로그에는 집계 수치, 안정적 오류 코드, 불투명한 outbox 행 id(UUID)만 남는다. 메시지 본문·예약자·항로·예약번호는 로그에 절대 출력하지 않는다. 래퍼는 자식 레그의 stdout/stderr를 캡처만 하고 재출력하지 않아 자식의 내용이 래퍼를 통해 새지 않는다.

Calendar 레그는 booking id의 결정적 소문자 hex sha256을 Google 이벤트 id로 사용해 created/updated/cancelled가 예약당 하나의 이벤트를 멱등하게 갱신한다. 이벤트는 Asia/Seoul dateTime(합법적 24:00 종료는 다음날 자정으로 매핑), summary `베로니카 차터 예약 · {예약자}`, description은 예약번호·항로·예상 인원·source 마커만, location은 출발지, `visibility: private`, `extendedProperties.private`에 source·booking id·idempotency key를 담는다. 참석자·전화번호·취소코드·탑승자 상세는 담지 않으며 모든 호출은 `sendUpdates=none`이다. Calendar는 결정적·멱등이므로 Calendar 성공 후 래퍼 전체를 재시도해도 안전하다.

카카오 채널은 중단 상태다. 카카오 SSoT 스킬(`22_2_tools/skills/asteria-kakao-booking-alerts`)은 보존만 하며 이 파이프라인에서 호출하지 않는다.

### 소비기 런타임 설정

런타임 설정 파일 `~/.config/asteria-discord/outbox-consumer.json` (현재 사용자 소유, 권한 `0600` 이하). 비밀은 넣지 않는다.

```json
{
  "project_ref": "wbvbvfqrdhpsjmcwzouv",
  "activated_at": "2026-07-27T00:00:00+09:00",
  "sender_script": "/Users/ja/repos/55_MyLabs/asteria-web/scripts/send_booking_integrations.py",
  "keychain_bin": "/Users/ja/.local/bin/hermes-keychain",
  "batch_size": 5
}
```

```bash
mkdir -p ~/.config/asteria-discord
# outbox-consumer.json 을 위 형식으로 작성한 뒤:
chmod 600 ~/.config/asteria-discord/outbox-consumer.json
mkdir -p ~/.local/state/asteria-discord
```

- `project_ref`: 정확히 `wbvbvfqrdhpsjmcwzouv` 여야 한다. REST 기준 `https://wbvbvfqrdhpsjmcwzouv.supabase.co/rest/v1`.
- `sender_script`: 정확히 `/Users/ja/repos/55_MyLabs/asteria-web/scripts/send_booking_integrations.py`(2단 발송 래퍼) 여야 한다. 소비기는 이 경로 외의 프로그램을 실행하지 않는다.
- `keychain_bin`: 정확히 `/Users/ja/.local/bin/hermes-keychain`. 소비기는 `hermes-keychain get asteria.supabase.service-role wbvbvfqrdhpsjmcwzouv`로 서비스 롤을 읽는다.
- `batch_size`: 1~10, 기본 5.
- 소비기는 각 레그의 대상 설정(`~/.config/asteria-google-calendar/booking-alert.json`, `~/.config/asteria-discord/booking-alert.json`)을 읽거나 수정하지 않는다. 소비기는 검증된 필드와 행의 `idempotency_key`를 래퍼 argv로만 넘기고, 래퍼가 각 레그에 같은 공통 인자와 각 레그의 `--config`를 넘긴다.

### Discord 발송기 런타임 설정

발송기 대상 설정 파일 `~/.config/asteria-discord/booking-alert.json` (현재 사용자 소유, 권한 `0600` 이하). 비밀은 넣지 않는다. 봇 토큰은 오직 Keychain에서만 읽는다.

```json
{
  "guild_id": "1513551188082294834",
  "channel_id": "1530999174110121985",
  "keychain_bin": "/Users/ja/.local/bin/hermes-keychain",
  "keychain_service": "asteria.discord.bot-token",
  "keychain_account": "default"
}
```

- `guild_id`: 정확히 `1513551188082294834` 여야 한다. `channel_id`: 정확히 `1530999174110121985` 여야 한다. 둘 중 하나라도 불일치하면 어떤 네트워크 호출도 하기 전에 거부한다.
- 봇 토큰은 `keychain_bin get keychain_service keychain_account`로만 읽으며 로그·argv에 남지 않는다.
- 발송기는 `POST /api/v10/channels/{channel_id}/messages`에 `allowed_mentions={"parse": []}`, 행 `idempotency_key`에서 유도한 결정적 `nonce`와 `enforce_nonce=true`로 보낸다. 그 뒤 생성된 메시지를 id로 `GET`해 채널·본문을 검증한 뒤에만 성공(exit 0)한다.
- 고정 템플릿은 created/updated/cancelled만 지원하며 예약번호·예약일·KST 운항 시간·항로·예약자·예상 인원만 담는다. 전화번호·취소코드·탑승자 상세·토큰·비밀은 담지 않는다.

발송기 템플릿을 실제 발송 없이 렌더링만 확인:

```bash
python3 scripts/send_discord_booking_alert.py --render-only \
  --event created --booking-id VER-260815 --date 2026-08-15 \
  --start-time 13:00 --end-time 16:00 --route "아라마리나 → 팔미도" \
  --name 예약자 --party-size 4 --idempotency-key preview-only
```

### Google Calendar 발송기 런타임 설정

발송기 대상 설정 파일 `~/.config/asteria-google-calendar/booking-alert.json` (현재 사용자 소유, 권한 `0600` 이하). 비밀은 넣지 않는다. OAuth 자격증명은 오직 고정된 0600 자격증명 파일에서만 읽는다.

```json
{
  "user_email": "asteriayachtclub@gmail.com",
  "calendar_id": "7f48e83938cf52b65ba95144dd9786efcbcc8862063af4846af22341ab8eac81@group.calendar.google.com",
  "credentials_file": "/Users/ja/.google_workspace_mcp/credentials/asteriayachtclub@gmail.com.json"
}
```

```bash
mkdir -p ~/.config/asteria-google-calendar
# booking-alert.json 을 위 형식으로 작성한 뒤:
chmod 600 ~/.config/asteria-google-calendar/booking-alert.json
```

- `user_email`: 정확히 `asteriayachtclub@gmail.com`. `calendar_id`: 정확히 위 값. 둘 중 하나라도 불일치하면 어떤 네트워크 호출도 하기 전에 거부한다.
- `credentials_file`: 정확히 `/Users/ja/.google_workspace_mcp/credentials/asteriayachtclub@gmail.com.json`(realpath 기준 일치)여야 한다. 파일은 현재 사용자 소유이고 group/world 권한 비트가 없어야 한다. `client_id`·`client_secret`·`refresh_token`·`token_uri`(HTTPS) 키를 포함하며, 발송기는 `token_uri`로 refresh token을 access token으로 교환해 메모리에서만 사용한다. 토큰·자격증명·응답 본문은 로그·argv에 남지 않는다.
- 이벤트 id는 booking id의 결정적 소문자 hex sha256(유효한 `[a-v0-9]` Calendar id)이다. created는 insert(409면 GET+검증), updated는 PUT(404면 insert), cancelled는 DELETE 후 GET 결과가 404/410이거나 같은 이벤트 id의 `status=cancelled` tombstone인지 검증한다. 모든 호출은 `sendUpdates=none`. 쓰기 후 이벤트 id·start·end·status·내용을 재조회 검증한 뒤에만 성공(exit 0)한다.
- 이벤트는 Asia/Seoul dateTime(24:00 종료는 다음날 자정), summary `베로니카 차터 예약 · {예약자}`, description은 예약번호·항로·예상 인원·source 마커만, location은 출발지, `visibility: private`, `extendedProperties.private`에 source·booking id·idempotency key. 참석자·전화번호·취소코드·탑승자 상세는 담지 않는다.
- exit 0=검증 완료, exit 2=쓰기 전 안전/재시도 가능 또는 변경 없이 거부, exit 3=쓰기 후 재조회 불확정(자동 재시도 금지).

발송기 이벤트 본문을 실제 발송 없이 렌더링만 확인(설정·자격증명·네트워크 미접촉):

```bash
python3 scripts/send_google_calendar_booking.py --render-only \
  --event created --booking-id VER-260815 --date 2026-08-15 \
  --start-time 13:00 --end-time 16:00 --route "아라마리나 → 팔미도" \
  --name 예약자 --party-size 4 --idempotency-key preview-only
```

### 2단 발송 래퍼

래퍼 `scripts/send_booking_integrations.py`는 공통 예약 인자와 각 레그 `--config`를 받아 Calendar를 먼저, 성공 시에만 Discord를 실행한다. 자식 레그의 출력은 캡처만 하고 재출력하지 않으며, 안정적 상태 코드만 자신의 로그로 낸다. exit 0=두 레그 모두 검증, exit 2=재시도 가능 자식 실패(또는 자식 기동 실패), exit 3=불확정 자식(exit 3·예상 밖 코드·타임아웃). Calendar가 결정적·멱등이라 Calendar 성공 후 래퍼 전체를 재시도해도 안전하다.

### 활성화 컷오프 (과거 재생 금지)

`activated_at`은 RFC3339 타임스탬프이며 소비기는 `created_at >= activated_at`인 행만 선택한다. 과거 테스트·이벤트 행은 절대 발송되지 않는다. 활성화 시각을 미래·현재로 설정한 뒤 그 이후 생성된 실제 예약만 처리되게 한다. 컷오프를 앞당겨 과거 행을 소급 발송하지 않는다.

### dry-run 검증

실제 발송·선점·변경 없이 설정·Keychain 가용성·발송기 존재를 확인하고 처리 대상 수만 집계한다.

```bash
python3 scripts/consume_booking_outbox.py --dry-run \
  --config ~/.config/asteria-discord/outbox-consumer.json
```

`--once` 또는 `--dry-run` 중 하나는 반드시 지정해야 한다. dry-run은 어떤 행도 선점하거나 변경하지 않는다.

### launchd 부트스트랩

30초 간격 LaunchAgent 템플릿: `ops/club.asteria.veronica-discord-outbox.plist`. 비밀은 들어 있지 않고 최소 `PATH`(`/usr/bin:/bin`)만 설정한다. dry-run이 깨끗하고 발송기 수동 테스트를 통과한 뒤에만 올린다.

```bash
cp ops/club.asteria.veronica-discord-outbox.plist \
   ~/Library/LaunchAgents/club.asteria.veronica-discord-outbox.plist
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/club.asteria.veronica-discord-outbox.plist
launchctl kickstart -k gui/$(id -u)/club.asteria.veronica-discord-outbox
launchctl print gui/$(id -u)/club.asteria.veronica-discord-outbox
# 중지·해제:
launchctl bootout gui/$(id -u)/club.asteria.veronica-discord-outbox
```

로그는 `~/.local/state/asteria-discord/consumer.out.log`, `consumer.err.log`에 쌓인다(집계 수치·코드·행 id만). 중복 실행은 `~/.local/state/asteria-discord/consume.lock` fcntl 잠금으로 방지되며 겹치는 실행은 작업 없이 종료한다.

### stuck processing / 수동 검토

- `stuck_processing`: 발송 직후 크래시 등으로 `processing`에 남은 행. 재발송 시 중복 위험이 있어 자동으로 회수하지 않고 dry-run·once 요약에 개수만 보고한다. 사람이 Discord 스레드와 Veronica 캘린더를 확인한 뒤 DB에서 직접 상태를 정정한다.
- 래퍼 exit 3 (`sent_but_unverified_manual_review`): 어느 한 레그가 쓰기를 했을 수 있으나 재조회 검증에 실패한 모호한 결과(Discord POST 후 미검증, 또는 Calendar 쓰기 후 미검증). 소비기는 `retry_count`를 `max_retries`로 고정하고 `next_attempt_at`을 비워 절대 자동 재발송하지 않는다. 사람이 Discord 스레드와 Veronica 캘린더의 해당 이벤트를 확인한다.
- `payload_validation_failed`: 허용 목록 위반·금지 키·KST 날짜 불일치·`idempotency_key` 누락 등. 해당 행은 종단 처리되어 재시도되지 않는다.
- 래퍼 exit 2·프로세스 예외: 발송 전 안전 실패(설정·Keychain·자격증명·OAuth·검증 오류, Discord/Google이 변경 없이 거부한 요청, 자식 기동 실패)로 재시도 가능. `retry_count`를 1 증가시키고 상한(1시간)까지 지수 백오프로 `next_attempt_at`을 미룬다. `retry_count`가 `max_retries`에 도달하면 더 이상 선택되지 않는다. 두 레그가 결정적·멱등(같은 Discord `nonce`+`enforce_nonce`, 같은 Calendar 이벤트 id)이라 같은 행의 재시도는 양쪽에서 중복 제거된다.

### 로컬 검증

```bash
python3 -m py_compile \
  scripts/consume_booking_outbox.py \
  scripts/send_discord_booking_alert.py \
  scripts/send_google_calendar_booking.py \
  scripts/send_booking_integrations.py
python3 -m unittest tests.test_booking_outbox_consumer
python3 -m unittest tests.test_discord_booking_alert
python3 -m unittest tests.test_google_calendar_booking
python3 -m unittest tests.test_booking_integrations
plutil -lint ops/club.asteria.veronica-discord-outbox.plist
```

단위 테스트는 네트워크·Discord·Google·Keychain을 호출하지 않는다.

## 롤백

1. 문제가 있으면 `/veronica/` 링크를 공개 내비게이션에 추가하지 않고 Cloudflare 배포를 직전 정상 버전으로 되돌린다.
2. 긴급 차단 시 `VERONICA_UPSTREAM_BASE`를 비 HTTPS 값으로 설정하면 프록시는 `503 proxy_not_configured`로 닫힌다.
3. Function을 이전 배포로 되돌리거나 중지한다. 공용 로그인 긴급 차단은 `ASTERIA_SESSION_VERSION`을 증가시켜 기존 세션을 무효화한다.
4. 마이그레이션은 기존 테이블 데이터를 파괴하지 않는 추가형이다. 운영 데이터가 생긴 뒤에는 임의 down migration을 실행하지 말고 새 교정 migration을 작성한다.
