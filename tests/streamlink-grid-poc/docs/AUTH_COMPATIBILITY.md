# 인증 인터페이스 호환성 메모

이 문서는 Streamlink 8.6.2의 공개 CLI 동작과 `soop_grid` 외부 플러그인의 인증 입력 경계를 기록합니다.
내장 구현 비교 기준은 [Streamlink 8.6.2 `plugins/soop.py`](https://github.com/streamlink/streamlink/blob/8.6.2/src/streamlink/plugins/soop.py)입니다.

## 옵션 전달 차이

| 항목 | 내장 `soop` | 외부 `soop_grid` | 상태 |
| --- | --- | --- | --- |
| 계정 ID | `--soop-username` | `--soop-grid-username` (`--soop-grid-account` 별칭) | 별도 namespace |
| 계정 비밀번호 | `--soop-password` | `--soop-grid-password` | 별도 namespace |
| 방송 비밀번호 | `--soop-stream-password` | `--soop-grid-stream-password` | 별도 namespace |
| 계정 ID 환경변수 | 없음 | `SOOP_GRID_USERNAME` (`SOOP_ACCOUNT_ID` 호환 별칭) | 외부 계약 |
| 계정 비밀번호 환경변수 | 없음 | `SOOP_GRID_PASSWORD` (`SOOP_ACCOUNT_PASSWORD` 호환 별칭) | 외부 계약 |
| 방송 비밀번호 환경변수 | 없음 | `SOOP_GRID_STREAM_PASSWORD` (`SOOP_STREAM_PASSWORD` 호환 별칭) | 외부 계약 |
| 인증 쿠키 저장 | 내장 플러그인 캐시를 통해 저장·재사용 | 저장하지 않음. 로그인 세션은 Grid HTTP 세션 안에서만 사용 후 폐기 | 다름 |
| 자격정보 삭제 | `--soop-purge-credentials` | `--soop-grid-purge-credentials`를 HLS 위임 시 내장 플러그인에 전달 | 외부 명시 옵션 |

Streamlink 8.6.2의 `streamlink_cli.argparser.setup_plugin_args()`는 플러그인 ID를 포함해 각 플러그인의 옵션을 등록합니다. `Argument.argument_name(plugin)`과 `namespace_dest(plugin)`도 해당 ID를 옵션명과 argparse destination에 반영합니다. `setup_plugin_options()`는 URL에 매칭된 `pluginname`과 클래스의 arguments만 읽으며, `streamlink_cli.main.handle_url()`은 그 결과를 매칭된 인스턴스에 전달합니다.

따라서 `--soop-username`은 `soop` namespace에만 들어갑니다. 같은 옵션명을 `soop_grid`에 등록하면 내장 플러그인 옵션과 argparse 충돌이 발생하며, 외부 플러그인에는 공개 확장 API를 통한 전달 경로가 없습니다. 외부 플러그인은 argparse namespace, `sys.argv`, 또는 내장 플러그인 상태를 읽지 않습니다. 공식 옵션과 동일한 CLI 호환성은 **지원되지 않습니다**.

## 외부 플러그인에서 지원하는 인증 입력

```sh
streamlink \
  --soop-grid-username USER \
  --soop-grid-password PASS \
  --soop-grid-stream-password STREAM_PASS \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original
```

비밀번호를 명령행에 넣으면 셸 기록이나 시스템 프로세스 목록에 노출될 수 있습니다. 가능한 경우 다음 환경변수를 사용하세요.

```sh
export SOOP_GRID_USERNAME='ACCOUNT'
export SOOP_GRID_PASSWORD='PASSWORD'
export SOOP_GRID_STREAM_PASSWORD='BROADCAST_PASSWORD'
```

환경변수는 내장 Streamlink `soop` 플러그인의 인터페이스가 아닙니다. 외부 Grid 플러그인과 기존 자동 녹화기 환경의 호환 계약입니다. `SOOP_ACCOUNT_ID`, `SOOP_ACCOUNT_PASSWORD`, `SOOP_STREAM_PASSWORD`는 각각 위 정식 변수의 하위 호환 별칭입니다.

각 값의 우선순위는 명시적인 `--soop-grid-*` CLI 입력 → 해당 `SOOP_GRID_*` 변수 → 이전 환경변수 별칭 → 미설정입니다. 존재하지만 빈 정식 환경변수는 호환 별칭으로 대체되지 않습니다. CLI username/password와 CLI 별칭 `--soop-grid-account`는 동시에 지정할 수 없습니다. 최종 ID와 계정 비밀번호는 둘 다 있거나 둘 다 없어야 하며, 서로 다른 우선순위 단계에서 온 값이어도 완전한 쌍이면 쓸 수 있습니다. 조회된 세션 계정이 요청한 계정과 다르거나 서버가 인증을 거부하면 진행하지 않습니다.

계정 인증 없이 공개 Grid 방송을 수신하는 것은 지원됩니다. 계정 없이 방송 비밀번호만 지정하는 구성은 Native Grid 프로토콜 근거가 없어 지원하지 않습니다. `grid` 및 `auto`는 이를 `grid_stream_password_requires_account` 오류로 거부합니다. Streamlink 내장 `soop`은 방송 비밀번호 단독 입력을 허용하므로 `--soop-grid-transport hls --soop-grid-stream-password ...`를 사용하면 그 옵션을 그대로 위임합니다. Auto는 Grid 인증 거부를 전송 장애로 취급해 HLS로 우회하지 않습니다.

## 로그인 요청 비교

로그인은 `https://login.sooplive.com/app/LoginAction.php`로 form-urlencoded POST를 보냅니다. 필드(`szWork`, `szType`, `szUid`, `szPassword`, `isSaveId`, `isSavePw`, `isSaveJoin`, `isLoginRetain`)와 성공 조건(`RESULT == 1`)은 Streamlink 8.6.2 구현과 같습니다. 그 뒤 `https://afevent2.sooplive.com/api/get_private_info.php`를 조회하고, 응답의 `CHANNEL.LOGIN_ID`가 지정 계정과 정확히 일치해야 인증을 인정합니다. 이 계정 대조는 내장 플러그인의 비어 있지 않은 LOGIN_ID 확인보다 엄격합니다.

요청에는 Streamlink 세션에서 가져온 User-Agent, `Origin: https://play.sooplive.com`, 입력한 방송 URL의 Referer를 사용합니다. Grid 로그인은 격리된 first-party HTTP Cookie Jar를 사용하고 요청 종료 시 쿠키를 폐기하며 로그인 쿠키를 플러그인 캐시에 저장하지 않습니다. 내장 플러그인의 저장 쿠키나 캐시를 읽거나 수정하지 않습니다. 자동 세션 재인증은 지원하지 않습니다.

## HLS 위임 및 자격정보

`hls` 모드와 `auto`의 허용된 초기 Grid 전송 실패 시, 같은 ID·계정 비밀번호·방송 비밀번호를 Streamlink 내장 `soop` 클래스의 공개 생성자 옵션으로 전달합니다. 비밀번호 단독 HLS 구성에서도 username/password는 빈 값으로 유지되고 `stream-password`만 위임됩니다. 내장 플러그인은 자체 쿠키 재사용, 로그인 및 세션 검증 정책을 적용합니다. `--soop-grid-purge-credentials`도 위임 시 내장 `purge-credentials` 옵션으로 전달됩니다.

Purge 동작은 transport별로 다릅니다. `hls`에서는 내장 SOOP 플러그인의 생성 시 쿠키 캐시 삭제가 실행됩니다. `grid`에서는 지울 영속 Grid 쿠키가 없어 아무것도 삭제하지 않습니다. `auto`에서는 Grid가 먼저 실행되며, 허용된 초기 전송 장애로 실제 HLS 위임이 생성될 때만 내장 플러그인의 purge가 적용됩니다. Auto의 Grid 성공·인증 거부·프로토콜 오류에는 내장 쿠키 캐시를 건드리지 않습니다.

인증 거부, 프로토콜/미디어 무결성 오류, Grid 출력이 시작된 뒤에는 HLS fallback을 하지 않습니다. Grid와 HLS 바이트를 하나의 녹화 흐름에 이어 붙이지 않습니다.

## 확정된 사용자 인터페이스

외부 플러그인의 정식 인증 인터페이스는 `--soop-grid-username`, `--soop-grid-password`, `--soop-grid-stream-password` 및 `SOOP_GRID_USERNAME`, `SOOP_GRID_PASSWORD`, `SOOP_GRID_STREAM_PASSWORD`입니다. 앞선 `SOOP_ACCOUNT_ID`, `SOOP_ACCOUNT_PASSWORD`, `SOOP_STREAM_PASSWORD` 환경변수는 호환 별칭입니다. 내장 `--soop-*` 옵션을 외부 플러그인으로 전달하는 기능과 영속 쿠키 캐시 공유는 지원하지 않습니다. 이는 Streamlink의 플러그인별 옵션 경계에 따른 의도된 인터페이스입니다. argparse 조작, 코어 수정, 내장 플러그인 교체 또는 중복 옵션 등록은 사용하지 않습니다.

Native Grid는 현재 단일 `original` stream을 제공합니다. Streamlink 8.6.2의 `best`/`worst` 별칭은 이 하나뿐인 Grid stream으로 해석됩니다. 이는 Grid에서 추가 화질을 선택할 수 있음을 뜻하지 않습니다. HLS 위임은 내장 `soop`의 실제 품질 목록을 유지하며, 자동 fallback은 Grid `original`에 대응하는 HLS `original`을 우선 열고, `original`이 없을 때만 내장 `best` 별칭을 사용합니다.
