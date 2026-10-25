# SOOP Native Grid Streamlink 플러그인 안내

> **Experimental/Beta** 연구용 커뮤니티 플러그인입니다. 장시간 안정성, Parent 장애 복구, 모든 방송 형식과 운영체제는 검증되지 않았습니다. 직접 시청·녹화 권한이 있는 방송에만 사용하세요.

## 기능

별도 Python 프로젝트 설치 없이 `dist/soop.py` 한 파일을 Streamlink 플러그인 디렉터리에 복사해 Native TCP Grid 수신을 시도합니다. H.264/AAC를 재인코딩하지 않고 MPEG-TS로 전달합니다. Streamlink 내장 `soop` HLS 플러그인을 보존하기 위해 설치 파일명은 `soopgrid.py`로 사용합니다.

## 설치

Streamlink 8.6.2와 Python 3.12에서 Linux 실행을 확인했습니다. Windows/macOS 경로는 문서화했지만 해당 OS에서 실제 실행 검증은 하지 않았습니다.

### macOS

```sh
brew install streamlink
mkdir -p "$HOME/Library/Application Support/streamlink/plugins"
curl -fL 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop.py' \
  -o "$HOME/Library/Application Support/streamlink/plugins/soopgrid.py"
```

### Linux

Streamlink을 먼저 설치한 뒤:

```sh
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins"
curl -fL 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop.py' \
  -o "${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins/soopgrid.py"
```

### Windows PowerShell

```powershell
$dir = Join-Path $env:APPDATA 'streamlink\plugins'
New-Item -ItemType Directory -Force $dir | Out-Null
Invoke-WebRequest 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop.py' `
  -OutFile (Join-Path $dir 'soopgrid.py')
```

설치 확인:

```sh
streamlink --can-handle-url 'https://play.sooplive.com/CHANNEL_ID/BNO'
```

## Grid / HLS / Auto 선택

### Native Grid만 사용

```sh
streamlink --soop-grid-transport grid \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

### Streamlink 공식 SOOP HLS 플러그인 사용

```sh
streamlink --soop-grid-transport hls \
  'https://play.sooplive.com/CHANNEL_ID/BNO' best -o recording.ts
```

### Grid 우선, 초기 전송 장애에서만 HLS 시도

```sh
streamlink --soop-grid-transport auto \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

`auto`는 미디어가 출력되기 전에 발생한 제한된 Grid 전송 장애에서만 공식 HLS를 시도합니다. 인증 거부, 잘못된 프로토콜·미디어, Frame Gap, 또는 Grid 출력이 시작된 뒤에는 다른 타임라인을 이어 붙이지 않습니다.

## 장시간 녹화

Grid는 기본적으로 사용자가 Streamlink를 중단하거나 수신기가 EOF/오류를 만날 때까지 읽습니다. 현재는 방송 정상 종료와 Parent/네트워크 장애를 확실히 구분하지 못하므로 자동으로 완전한 정상 종료를 보장하지 않습니다. 선택적으로 시간을 제한할 수 있습니다.

```sh
streamlink --soop-grid-transport grid --soop-grid-duration 1800 \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

`--soop-grid-max-bytes BYTES`는 선택적인 수신 Wire 상한입니다. 파일을 오래 녹화할 때는 디스크 여유 공간도 직접 확인하세요. 사용자 중단은 터미널에서 `Ctrl+C`입니다. 프로세스 강제 종료·전원 차단 시 파일 마무리는 보장되지 않습니다.

## 계정 및 방송 비밀번호

계정 접근이 필요한 방송은 정상적인 계정 권한과 서비스 인증 절차를 사용하세요. 암호는 명령행 인수 대신 환경변수에 조용히 입력할 수 있습니다.

```sh
export SOOP_ACCOUNT_ID='ACCOUNT_ID'
read -r -s -p 'SOOP account password: ' SOOP_ACCOUNT_PASSWORD
printf '\n'
export SOOP_ACCOUNT_PASSWORD
streamlink --soop-grid-transport grid --soop-grid-require-adult \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
unset SOOP_ACCOUNT_ID SOOP_ACCOUNT_PASSWORD
```

방송 비밀번호가 설정된 경우 `SOOP_STREAM_PASSWORD`를 별도로 입력합니다. 구현은 이를 정상 Center JOIN의 `passwd` 필드에만 전달합니다. 성인인증, 유료·구독 권한, CAPTCHA 또는 시청 제한을 우회하지 않습니다. Streamlink/Python 로그인 방식과 보호 방송의 실시간 호환성은 방송마다 다를 수 있습니다.

## 기존 `soop.py`에서 전환

이전 실험본을 `plugins/soop.py`로 설치했다면 그 파일은 내장 `soop` 플러그인을 대체했을 수 있습니다. 새 파일은 `soopgrid.py`로 설치합니다. 이전 파일을 자동 삭제하지 않으므로, 설치 디렉터리의 이전 실험용 `soop.py`를 직접 확인하고 제거한 뒤 새 파일을 두세요. Streamlink 설치 디렉터리의 파일은 수정하지 마세요.

## 업데이트 및 제거

업데이트는 새 `dist/soop.py`를 기존 위치의 `soopgrid.py`에 덮어 복사합니다. SHA-256은 같은 커밋의 `dist/SHA256SUMS`와 대조하세요. 제거는 사용자 플러그인 디렉터리의 `soopgrid.py`만 삭제하면 됩니다.

## 오류와 로그

일반적으로 다음 단계 코드를 확인하세요: `live_metadata_denied_or_offline`, `public_watch_access_denied`, `gateway_*`, `center_*`, `parent_*`, `h264_*`, `media_*`, `buffer_*`. 사용자 ID, 방송 URL/BNO, IP 주소, 쿠키, Ticket, Password, 원본 Payload가 들어간 로그나 캡처는 공개 이슈에 첨부하지 마세요. `SOOP_GRID_CAPTURE_DIR` 원본 파일은 미디어 콘텐츠이므로 비공개로 보관해야 합니다.

디코딩 검증 예:

```sh
ffprobe -v error -show_streams -show_format -of json recording.ts
ffmpeg -v warning -xerror -i recording.ts -map 0:v:0 -map 0:a:0 -f null -
```

해상도 프리셋 이름만으로 실제 해상도를 판단하지 말고, 결과 SPS와 디코딩을 확인하세요.

## 알려진 제약 및 이용 시 유의사항

- Multi-Parent 동시 수신은 구현되지 않았습니다. 단일 Parent와 제한된 failover/cache overlap만 있습니다.
- Cache 기반 끊김 없는 복구는 실제 Parent 장애에서 검증되지 않았고, 복구 불가능한 gap에서는 오류로 종료합니다.
- H.264 progressive Annex-B의 검증된 일부 timing/POC 구조와 ADTS AAC를 지원합니다. 지원되지 않는 codec/timing은 실패 처리합니다.
- 자동 세션 재인증, 방송 BNO 전환, crash resume, 장시간 실사용 안정성은 보장하지 않습니다.
- HLS는 Streamlink 내장 플러그인에 위임하며, `auto`의 실시간 서버 fallback은 별도로 충분히 검증되지 않았습니다.
- 현재 라이선스는 AGPL-3.0-only입니다. 서비스 이용조건과 콘텐츠 권리는 사용자가 확인해야 합니다. 허가되지 않은 저장·재배포를 하지 마세요.

상세 상태는 [Production Readiness 결과](PRODUCTION_READINESS_RESULT.md)를 참고하세요.
