# SOOP Native Grid — Streamlink 플러그인 사용자 설명서

[프로젝트 소개 및 빠른 시작](../README.md) | [English](../README.en.md)

이 문서는 `soop_grid.py` 설치, Streamlink 실행, 오류 확인 방법을 설명합니다. 플러그인은 **Experimental/Beta**입니다. 실제 검증 범위와 미검증 항목을 확인한 뒤 사용하세요.

## 1. 설치 환경

- Streamlink 8.6.2를 권장합니다. Linux에서 Python 3.12.3 및 Streamlink 8.6.2로 플러그인 로딩과 수신을 확인했습니다.
- 별도 Grid 패키지, Go 실행 파일, SOOP Grid Agent, FFmpeg 수신 프로세스는 필요하지 않습니다.
- HLS 모드는 Streamlink 설치본에 포함된 공식 `soop` 플러그인을 사용합니다.
- 녹화 파일 크기만큼의 디스크 공간을 준비하세요.
- Windows/macOS의 사용자 플러그인 경로는 안내하지만 해당 운영체제에서 실행 검증하지 않았습니다.

저장소에 있는 `dist/soop_grid.py` 한 파일을 사용자 플러그인 디렉터리에 **`soop_grid.py`**로 복사합니다. 외부 플러그인 ID는 `soop_grid`이고, 내장 HLS 플러그인 ID는 `soop`입니다. 내장 플러그인을 덮어쓰지 마세요.

> 공개 게시물에는 배포한 커밋에 고정된 다운로드 주소를 사용하는 것을 권장합니다. 그러면 안내한 체크섬과 설치 파일의 버전이 일치하는지 확인하기 쉽습니다.

### macOS

터미널에서 Streamlink를 설치하고 플러그인을 복사합니다.

```sh
brew install streamlink
mkdir -p "$HOME/Library/Application Support/streamlink/plugins"
curl -fL 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop_grid.py' \
  -o "$HOME/Library/Application Support/streamlink/plugins/soop_grid.py"
```

### Linux

Streamlink를 설치한 뒤 사용자 데이터 디렉터리에 파일을 저장합니다. `XDG_DATA_HOME`이 설정되어 있으면 그 경로를 사용합니다.

```sh
plugin_dir="${XDG_DATA_HOME:-$HOME/.local/share}/streamlink/plugins"
mkdir -p "$plugin_dir"
curl -fL 'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop_grid.py' \
  -o "$plugin_dir/soop_grid.py"
```

### Windows PowerShell

PowerShell에서 Streamlink를 설치한 뒤 다음을 실행합니다.

```powershell
$dir = Join-Path $env:APPDATA 'streamlink\plugins'
New-Item -ItemType Directory -Force $dir | Out-Null
Invoke-WebRequest `
  'https://raw.githubusercontent.com/integrated-recorder/source.soop/main/tests/streamlink-grid-poc/dist/soop_grid.py' `
  -OutFile (Join-Path $dir 'soop_grid.py')
```

각 OS의 실제 경로는 설치 방법에 따라 달라질 수 있습니다. 설치 후 Streamlink가 외부 플러그인을 실제로 선택하는지 확인하세요.

## 2. 플러그인 인식 확인

다음 명령은 URL을 처리할 수 있는 플러그인이 있는지만 확인합니다. 단독으로는 어느 플러그인이 선택됐는지 증명하지 않습니다.

```sh
streamlink --can-handle-url 'https://play.sooplive.com/CHANNEL_ID/BNO'
```

기본 stream 열거 결과에는 외부 플러그인 ID와 `original`이 표시되어야 합니다.

```sh
streamlink --loglevel debug --json \
  'https://play.sooplive.com/CHANNEL_ID/BNO'
```

출력이나 디버그 로그에서 `soop_grid` 플러그인이 로드되고 `original` stream을 제공하는지 확인합니다. 실행 환경의 로깅 형식은 Streamlink 버전에 따라 달라질 수 있습니다. 이 명령은 실제 방송 접속을 시도할 수 있습니다.

## 3. 기본 녹화

시청 및 저장 권한이 있는 방송 URL을 사용합니다.

```sh
streamlink \
  'https://play.sooplive.com/CHANNEL_ID/BNO' \
  original -o recording.ts
```

전송 옵션의 기본값은 Grid 우선 `auto`입니다. `original`은 Streamlink stream 선택 이름이며 실제 해상도를 보장하지 않습니다.

## 4. 전송 방식 선택

### Grid 전용

```sh
streamlink --soop-grid-transport grid \
  'https://play.sooplive.com/CHANNEL_ID/BNO' \
  original -o recording.ts
```

Native Grid만 사용합니다. 연결에 실패해도 HLS로 전환하지 않습니다.

### 공식 HLS 전용

```sh
streamlink --soop-grid-transport hls \
  'https://play.sooplive.com/CHANNEL_ID/BNO' \
  best -o recording.ts
```

Streamlink 내장 `soop` 플러그인에 위임합니다. 기존 HLS 옵션과 인증 처리는 해당 플러그인이 담당합니다.

### Grid 우선 Auto

```sh
streamlink --soop-grid-transport auto \
  'https://play.sooplive.com/CHANNEL_ID/BNO' \
  original -o recording.ts
```

`auto`는 다음 정책을 따릅니다.

1. Native Grid를 먼저 시도합니다.
2. Grid 미디어 출력 전의 허용된 초기 전송 장애에서만 공식 HLS를 시도할 수 있습니다.
3. 인증 거부, 잘못된 프로토콜·미디어, 프레임 무결성 오류에서는 전환하지 않습니다.
4. Grid 출력이 시작된 뒤에는 HLS를 이어 붙이지 않습니다.
5. Grid와 HLS의 미디어 타임라인을 섞지 않습니다.

따라서 `auto`는 모든 Grid 오류를 숨기는 범용 대체 기능이 아닙니다.

## 5. 계정 인증과 방송 비밀번호

인증이 필요한 방송은 계정에 정상적으로 부여된 시청 권한으로 접근하세요. 외부 플러그인의 계정 옵션은 Streamlink 내장 `soop` 플러그인의 옵션과 별도입니다. 다음 옵션을 사용하거나, 아래처럼 기존 환경변수를 사용할 수 있습니다.

```sh
streamlink --soop-grid-username USER --soop-grid-password PASS \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

위 명령행 입력은 셸 기록이나 프로세스 목록에 남을 수 있으므로 환경변수 방식을 권장합니다. Streamlink 8.6.2에서는 `--soop-username`과 `--soop-password`가 내장 `soop` 플러그인 옵션 공간에만 전달되어 외부 `soop_grid`에서 읽을 수 없습니다. 중복 옵션 등록은 CLI 충돌을 일으키므로 지원하지 않습니다. 상세 근거는 [인증 호환성 메모](AUTH_COMPATIBILITY.md)를 확인하세요.

방송 비밀번호는 계정 비밀번호와 별개이며, `--soop-grid-stream-password` 또는 `SOOP_STREAM_PASSWORD`로 전달합니다.

```sh
export SOOP_ACCOUNT_ID='ACCOUNT_ID'
read -r -s -p 'SOOP account password: ' SOOP_ACCOUNT_PASSWORD
printf '\n'
export SOOP_ACCOUNT_PASSWORD
streamlink --soop-grid-transport grid --soop-grid-require-adult \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
unset SOOP_ACCOUNT_ID SOOP_ACCOUNT_PASSWORD
```

`--soop-grid-username`, `--soop-grid-password`, `--soop-grid-stream-password`를 지정하면 각 명시 옵션이 대응 환경변수보다 우선합니다. `--soop-grid-account`는 username의 별칭이며 둘 다 지정하면 오류입니다. 계정 ID/비밀번호는 함께 제공해야 합니다.

플러그인은 Grid 계정 로그인을 위해 first-party HTTP Cookie Jar를 일시 사용하고 요청 종료 후 폐기합니다. Grid 자격 쿠키를 저장하거나 내장 `soop` 플러그인 캐시와 공유하지 않습니다. `--soop-grid-purge-credentials`는 `hls` 또는 Auto fallback으로 내장 플러그인에 위임될 때만 해당 플러그인의 저장 인증정보 삭제를 요청합니다. Grid 모드에서 삭제할 저장 쿠키는 없습니다.

플러그인은 방송 비밀번호를 정상 Center JOIN 경로로 전달합니다.

Native Grid에서는 검증된 `original`만 선택할 수 있습니다. `best` 등 실제 Grid 화질 매핑이 확인되지 않은 선택을 지원한다고 표시하지 않습니다. HLS 모드에서는 내장 `soop` 플러그인이 제공하는 화질 목록과 선택 동작을 그대로 사용합니다.

이 플러그인은 성인 인증, 구독·유료 권한, 방송 비밀번호, CAPTCHA 또는 서비스의 접근 제한을 우회하지 않습니다. 계정 인증 및 보호 방송 호환성은 방송과 서버 상태에 따라 달라질 수 있습니다.

## 6. 녹화 시간과 파일 확인

기본적으로 별도 시간 제한이 없습니다. 사용자가 중단하거나 수신이 종료·오류 처리될 때까지 동작합니다. 예를 들어 30분 제한은 다음과 같이 설정합니다.

```sh
streamlink --soop-grid-transport grid --soop-grid-duration 1800 \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

`--soop-grid-max-bytes BYTES`로 Grid wire 수신량 상한도 설정할 수 있습니다. 시간 제한은 부트스트랩 이후 Parent 수신 구간에 적용됩니다. 장시간 저장 전 디스크 여유를 확인하고, 정상 중단에는 `Ctrl+C`를 사용하세요. 강제 종료나 전원 차단 후 파일 마무리는 보장하지 않습니다.

녹화 후 선택적으로 FFmpeg 도구로 스트림 정보를 검사할 수 있습니다.

```sh
ffprobe -v error -show_streams -show_format -of json recording.ts
ffmpeg -v warning -xerror -i recording.ts \
  -map 0:v:0 -map 0:a:0 -f null -
```

해상도와 코덱은 실제 출력 파일에서 확인하세요. preset 이름만으로 품질을 단정하지 마세요.

## 7. 업데이트와 제거

업데이트할 때 같은 공식 배포본의 `dist/soop_grid.py`를 기존 사용자 플러그인 디렉터리의 `soop_grid.py`에 덮어씁니다. 체크섬은 해당 소스 리비전의 `dist/SHA256SUMS`와 비교합니다.

제거할 때는 사용자 플러그인 디렉터리에서 `soop_grid.py`만 삭제합니다. Streamlink 설치 디렉터리나 내장 `soop` 플러그인은 변경하지 마세요.

## 8. 자주 발생하는 오류

| 증상 또는 코드 | 의미와 확인할 점 |
| --- | --- |
| 플러그인을 찾지 못함 | 파일명 `soop_grid.py`, 사용자 플러그인 경로, Streamlink 버전을 확인합니다. |
| `live_metadata_denied_or_offline`, `public_watch_access_denied` | 방송이 오프라인이거나 정확한 방송 접근을 서버가 허용하지 않을 수 있습니다. URL의 채널과 방송 번호를 확인하세요. |
| `gateway_*`, `center_*`, `parent_*` | 해당 Grid 연결 단계의 오류입니다. 일시적인 전송 실패는 제한적으로 복구하지만 모든 오류를 재시도하지 않습니다. |
| `cache_overlap_missing`, `cache_identity_conflict`, frame gap | 연속성을 증명할 수 없어 안전하게 중단한 상태입니다. 불완전한 파일을 완전 녹화로 취급하지 마세요. |
| `h264_*`, `media_*` | 코덱 구조 또는 타이밍이 현재 지원 범위를 벗어날 수 있습니다. |
| `buffer_*`, 출력 쓰기 오류 | Streamlink 소비 속도, 저장 장치, 사용 가능한 메모리와 디스크를 확인하세요. |

오류가 반복되면 먼저 Streamlink 버전과 일반 로그의 단계 코드만 확인합니다. 인증 또는 미디어 진단 수집은 선택 기능이며, 원본 진단에는 실제 방송 데이터가 포함될 수 있습니다.

## 9. 문제 보고 시 개인정보 보호

공개 게시물에 계정 ID, 방송 URL/BNO, IP 주소, Cookie, Ticket, Node Key, 방송 비밀번호, 인증 응답, 원본 Grid Payload, 진단 캡처를 올리지 마세요. 원본 캡처는 방송 콘텐츠이므로 비공개로 보관해야 합니다. 공유 전 로그와 파일을 직접 검토하고 민감정보를 제거하세요.

## 10. 지원 범위와 알려진 제약

- 프로젝트는 Experimental/Beta입니다. 최신 `400f028e…` 번들로 30분 Grid-only 녹화를 완료하고 전체 H.264/AAC 디코딩과 TS·타임스탬프 검사를 통과했습니다. 해당 방송은 B-frame이 없었습니다. 이전 번들의 별도 30분 녹화에서는 B-frame이 포함된 스트림의 재정렬을 검증했지만, 이를 최신 번들의 동일 빌드 실증으로 표현하지 않습니다. 별도의 HRD/POC 순서 충돌은 안전하게 거부되며, 지각적 A/V 동기와 반복적인 자연 장애 복구는 입증되지 않았습니다. 상세 수치는 [최신 30분 검증 보고서](LIVE_VALIDATION_30M_20261028.md)와 [시간축 분석](TIMELINE_MODEL_FIELD_VALIDATION_20261028.md)을 참고하세요.
- 약 600초 실제 녹화에서 1080p60 H.264/AAC 전체 디코딩을 확인했습니다. 이는 모든 방송의 해상도·프레임률 보장이 아닙니다.
- 별도의 한 세션에서 클라이언트가 소유한 Parent TCP 소켓을 세 차례 닫고, 서로 다른 Parent Endpoint로 재연결하여 매번 Cache Overlap의 SHA-256 일치와 출력 연속성을 확인했습니다. 자연 발생 원격 장애의 장기간 반복 검증이나 독립 장애 도메인을 보장하지 않습니다.
- 복구는 단일 활성 Parent를 사용합니다. Multi-Parent 동시 수신은 지원하지 않습니다.
- Cache에서 정확한 Overlap을 확인하지 못하거나 프레임 간격·정체성을 검증할 수 없으면 안전하게 실패합니다.
- 미디어가 3초 동안 도착하지 않으면 정체로 판정해 복구를 시도할 수 있습니다. 정상적인 긴 정체 구간에서도 재연결이 발생할 수 있습니다.
- 자동 세션 재인증, 방송 종료와 네트워크 장애의 완전한 구분, 방송 전환 및 장시간 A/V drift는 검증되지 않았습니다.
- 기존 내장 플러그인의 `--soop-username`, `--soop-password`, `--soop-stream-password`는 외부 `soop_grid`에 전달되지 않습니다. 외부 플러그인에서는 대응하는 `--soop-grid-*` 옵션 또는 환경변수를 사용하세요.
- 일부 progressive Annex-B H.264 timing/POC 구조와 ADTS AAC만 지원합니다. POC 값의 차이를 시간으로 간주하지 않습니다. 재정렬 영상에는 일관된 HRD/Picture Timing과 POC가 필요합니다. 보존된 36.8초 Grid B-frame 캡처 한 건은 현재 소스에서 성공적으로 재생했지만 장시간 실방송 검증은 아닙니다. 첫 HRD/POC 충돌이 고정 VUI·POC type 2의 IDR/P 순서에서 관측되면 제한적으로 Grid 시계 기반 no-reorder 모드를 선택하고, 이후 B-frame 또는 POC 역행은 오류로 처리합니다. 이는 공식 Grid 시계 의미를 증명하지 않으며, 현재 성인 방송의 동일한 조합은 다시 실방송 검증되지 않았습니다. 다른 타이밍 모순이나 불확실성은 계속 fail-closed이며 HLS로 우회하지 않습니다. 다중 Parent, 모든 코덱, 모든 방송 조건을 지원하지 않습니다.
- 선택적 진단 JSONL은 최대 16 MiB이며, 한도에 도달하면 이벤트 기록을 중단하고 요약에 잘림 여부를 표시합니다. 진단 캡처는 수신 스레드에 직렬화·파일 기록 부담을 추가하므로 문제 재현에 필요한 경우에만 사용하세요.
- Native Grid의 Gateway/Center/Parent 연결은 SOOP 프로토콜의 일반 TCP를 사용하며 플러그인이 TLS를 추가하지 않습니다. 일부 인증 관련 값이 프로토콜 패킷에 포함되므로 신뢰할 수 있는 네트워크에서만 사용하세요. HTTPS Bootstrap과 Native Grid TCP 연결의 전송 보안은 서로 다릅니다.
- Linux에서만 직접 실행을 확인했습니다. Windows/macOS 및 다른 Streamlink/Python 조합은 미검증입니다.
- 시청·저장 권한 및 서비스 이용 조건을 준수하세요. 코드는 AGPL-3.0-only입니다.

검증 이력은 [종합 강화·현장 검증](COMPREHENSIVE_HARDENING.md), [실제 제어 장애 검증](LIVE_FAILOVER_VALIDATION.md), [정적 감사](PRE_RELEASE_STATIC_AUDIT.md), [전체 검증 기록](VALIDATION.md)에서 확인할 수 있습니다.

### 검증 문서의 범위

- [실제 제어 장애 검증](LIVE_FAILOVER_VALIDATION.md)은 현재 안내에서 인용한 600초 녹화와 한 세션의 연속 3회 복구 결과를 기록한 핵심 실증 자료입니다. 자연 발생 서버 장애나 장기 운용 보증은 아닙니다.
- [정적 사전 감사](PRE_RELEASE_STATIC_AUDIT.md)는 오프라인 코드 검토와 배포 판정을 기록합니다. 라이브 시험 보고서가 아닙니다.
- [Python 검증 이력](VALIDATION.md), [구현 결과](PRODUCTION_READINESS_RESULT.md), [Reliability 결과](RELIABILITY_RESULT.md)는 서로 다른 시점·리비전의 결과를 보존합니다. 오래된 수치나 당시 미검증 판정을 현재 빌드의 재시험 결과로 읽지 마세요.
- [Failover 최적화 보고서](FAST_FAILOVER_OPTIMIZATION.md)는 구현 과정과 합성 벤치마크, 당시의 상태를 기록합니다. 실제 장애 복구의 최신 증거는 위 제어 장애 보고서를 기준으로 합니다.
- [Multi-Parent 조사](MULTIPARENT_PROTOCOL_FEASIBILITY.md)는 동일한 서버 제공 Endpoint의 동시 연결·프레임 비교를 다룹니다. 서로 다른 Parent 간 Active-Active 지원을 입증하지 않습니다.
- [Cache 범위 REA 분석](GRID_CACHE_RANGE_REA.md)은 분석된 네이티브 클라이언트의 Cache 명령 구조와 종료 제어 한계를 설명합니다. 이 기능은 일반 사용자 옵션이 아닙니다.
- [아키텍처](ARCHITECTURE.md), [복구 정책](RELIABILITY.md), [제한사항](LIMITATIONS.md)은 현재 설계와 지원 경계를 설명합니다. [H.264 타이밍 진단](H264_TIMING_DIAGNOSIS.md)은 지원되는 POC/타이밍 조합과 안전한 거부 조건을 다룹니다. [진단 문서](DIAGNOSTICS.md)는 선택형 진단 파일의 내용·개인정보 위험을 다룹니다.
