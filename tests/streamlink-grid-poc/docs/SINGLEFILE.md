# 단일 파일 배포 안내

[한국어 사용자 설명서](COMMUNITY_GUIDE_KO.md) | [프로젝트 README](../README.md)

## 사용자 설치

Git 저장소의 생성 배포 산출물은 `dist/soop_grid.py`입니다. 이를 Streamlink 사용자 플러그인 디렉터리에 **`soop_grid.py`**라는 이름으로 저장합니다. Streamlink가 인식하는 ID와 내장 플러그인 ID는 서로 다릅니다.

| 구분 | 이름 |
| --- | --- |
| 저장소 배포 산출물 | `dist/soop_grid.py` |
| 사용자 설치 파일명 | `soop_grid.py` |
| Streamlink 외부 플러그인 ID | `soop_grid` |
| Streamlink 내장 SOOP HLS 플러그인 ID | `soop` |

인증 입력은 `--soop-grid-username`, `--soop-grid-password`, `--soop-grid-stream-password` 또는 정식 환경변수 `SOOP_GRID_USERNAME`, `SOOP_GRID_PASSWORD`, `SOOP_GRID_STREAM_PASSWORD`를 사용합니다. `SOOP_ACCOUNT_ID`, `SOOP_ACCOUNT_PASSWORD`, `SOOP_STREAM_PASSWORD`는 호환 별칭입니다. 내장 플러그인의 `--soop-username` 계열 옵션은 외부 플러그인 인스턴스에 전달되지 않습니다. 계정 없이 방송 비밀번호만 지정하는 구성은 `hls`에서만 허용됩니다. 상세 내용은 [인증 호환성 메모](AUTH_COMPATIBILITY.md)를 참고하세요.

내장 `soop` 플러그인을 덮어쓰거나 수정하지 않습니다. 운영체제별 설치 경로와 실행법은 [사용자 설명서](COMMUNITY_GUIDE_KO.md#1-설치-환경)를 참고하세요. Windows/macOS 런타임은 검증되지 않았습니다.

배포 파일은 게시된 저장소 커밋에서 내려받을 수 있습니다. 공개 배포 링크는 특정 커밋에 고정하고, 같은 커밋의 `dist/SHA256SUMS`와 대조하는 것을 권장합니다. 개발 중에는 현재 checkout에서 생성한 파일과 체크섬을 사용하세요.

## 동작과 사용

기본 transport는 Grid 우선 `auto`입니다.

`--soop-grid-purge-credentials`는 HLS 위임 시 내장 플러그인에 삭제를 요청합니다. Grid 로그인 쿠키는 저장하지 않으므로 Grid 모드에서 삭제할 Grid 쿠키 캐시는 없습니다.

Native Grid는 하나의 `original` stream만 노출합니다. Streamlink 8.6.2의 `best`와 `worst` 별칭도 이 단일 stream을 선택합니다. 별도 Grid 화질 선택을 의미하지 않습니다. HLS에서는 내장 `soop`의 품질 목록과 선택을 유지합니다.

```sh
# Native Grid만
streamlink --soop-grid-transport grid \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts

# 내장 Streamlink SOOP HLS 플러그인에 위임
streamlink --soop-grid-transport hls \
  'https://play.sooplive.com/CHANNEL_ID/BNO' best -o recording.ts

# Grid 우선, 허용된 초기 전송 장애에서만 HLS 시도 (기본값)
streamlink --soop-grid-transport auto \
  'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

인증 거부나 프로토콜·미디어 무결성 오류에서는 자동 전환하지 않습니다. Grid 출력이 시작된 뒤 HLS를 이어 붙이지 않으며 서로 다른 타임라인을 합치지 않습니다. 접근 권한이 있는 방송에만 사용하세요.

## 업데이트와 제거

업데이트는 새 배포본의 내용을 사용자 플러그인 디렉터리 `soop_grid.py`에 덮어쓰면 됩니다. 제거할 때는 그 사용자 플러그인 파일만 삭제합니다. Streamlink 내장 `soop` 플러그인과 Streamlink 설치 디렉터리는 변경하지 마세요.

## 개발자: 정본과 생성 파일

`soop_grid/*.py`와 `plugins/soop_grid.py`가 개발용 정본입니다. `dist/soop_grid.py`는 정적 AST 기반 Bundler가 생성하므로 직접 편집하지 않습니다.

```sh
python tools/build_singlefile.py
python tools/build_singlefile.py --check
python -m pytest -q
python tools/test_bundle.py -q
```

Bundler는 알려진 내부 모듈 그래프를 분석하고 내부 전역 심볼을 분리하며 dataclass 정체성과 지연 import를 보존합니다. 지원하지 않는 순환·구문은 생성 단계에서 실패합니다. 런타임 `exec`/`eval`, import hook, 압축 소스, ZIP 모듈 로딩, 네트워크 코드 다운로드는 사용하지 않습니다.

격리 CLI 테스트는 별도 가상환경에 공식 Streamlink와 그 의존성만 설치하고, 생성 파일 하나를 `soop_grid.py`로 복사해 프로젝트와 무관한 작업 디렉터리에서 실행합니다. 프로젝트 패키지를 설치하거나 `PYTHONPATH`를 설정하지 않습니다. 모듈식 및 단일 파일 회귀 테스트는 같은 합성 입력에서 동작을 비교합니다.

## 라이선스

이 저장소의 구현과 생성 파일은 **AGPL-3.0-only**입니다. Streamlink와 의존성은 별도 패키지이며 번들에 복사하거나 재라이선스하지 않습니다. SPDX 및 저작권 표시는 생성 파일에 포함됩니다.
