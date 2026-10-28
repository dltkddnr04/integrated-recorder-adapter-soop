# SOOP Native Grid — Streamlink 플러그인

**한국어** | [English](README.en.md)

SOOP Native Grid 방송을 Streamlink에서 직접 수신하는 외부 Python 플러그인입니다. 별도의 SOOP Grid Agent나 Go 실행 파일 없이, 공식 Streamlink 플러그인 API를 사용해 H.264/AAC 미디어를 재인코딩하지 않고 MPEG-TS로 출력합니다.

## 상태

**Experimental/Beta** 플러그인입니다. Linux/Python 3.12/Streamlink 8.6.2에서 배포 단일 파일과 Native Grid 수신을 검증했습니다. 최신 번들 `400f028e…`로 Grid-only 30분 녹화를 완료하고, 전체 H.264/AAC 디코딩과 TS·타임스탬프 검사를 통과했습니다. 이 실행에는 B-frame이 없었습니다. 별도 이전 번들의 30분 실방송에는 B-frame이 포함되어 전체 검증을 통과했지만, 그것을 `400f`와 동일한 빌드 시험으로 합산하지 않습니다. 지각적 A/V 동기, 모든 HRD/POC 변형 및 장시간 자연 장애 복구는 입증되지 않았습니다. [최신 30분 검증 보고서](docs/LIVE_VALIDATION_30M_20261028.md), [시간축 분석](docs/TIMELINE_MODEL_FIELD_VALIDATION_20261028.md), [복구 검증](docs/LIVE_FAILOVER_VALIDATION.md)을 참고하세요. 모든 방송에서의 무손실 동작을 보장하지 않습니다.

## 주요 기능

- Windows Grid Agent 없이 Native TCP Grid 수신
- Streamlink를 통한 파일 출력 및 다른 Streamlink 출력 대상 사용
- H.264/AAC 원본을 재인코딩 없이 MPEG-TS로 패킷화
- 기본 Grid 우선 `auto`, Grid 전용 `grid`, 내장 SOOP HLS 위임 `hls`
- 허용된 초기 전송 실패에 한해 제한적으로 HLS fallback
- 단일 Parent 수신, Center 재탐색 및 Cache Overlap 확인을 통한 제한된 복구
- 별도 Python 패키지 설치 없이 배포 파일 하나로 설치

## 요구 환경

- Streamlink 8.6.2 권장 (Python 3.12.3 / Linux에서 검증)
- Streamlink의 정상 의존성
- 기록 파일을 저장할 디스크 공간

프로젝트 패키지, Go, FFmpeg 수신 프로세스, SOOP Agent, VPN은 필요하지 않습니다. FFmpeg는 녹화 후 선택적으로 파일을 검사할 때만 사용합니다. Windows와 macOS 설치 경로는 안내하지만 해당 OS에서 런타임 검증하지 않았습니다.

## 설치

저장소의 배포 파일 `dist/soop_grid.py`를 Streamlink 사용자 플러그인 디렉터리에 **`soop_grid.py`**라는 이름으로 저장합니다. 이 파일은 외부 플러그인 ID `soop_grid`로 로드되며, Streamlink 내장 `soop` 플러그인은 그대로 남습니다.

운영체제별 명령, 플러그인 인식 확인, 업데이트와 제거는 [한국어 사용자 설명서](docs/COMMUNITY_GUIDE_KO.md)를 참고하세요. 재현 가능한 설치를 위해 공개 안내의 다운로드 주소는 릴리스 커밋에 고정하는 것을 권장합니다.

## 가장 간단한 녹화

플러그인이 설치되고 방송을 시청·저장할 권한이 있다면 다음처럼 실행합니다.

```sh
streamlink 'https://play.sooplive.com/CHANNEL_ID/BNO' original -o recording.ts
```

전송 옵션을 생략하면 `auto`가 기본값이며 Native Grid를 먼저 시도합니다.

## Grid / HLS / Auto

| 모드 | 동작 |
| --- | --- |
| 기본값 또는 `auto` | Grid 우선. 미디어 출력 전 허용된 초기 전송 장애에서만 공식 HLS를 시도 |
| `grid` | Native Grid만 사용. HLS로 전환하지 않음 |
| `hls` | Streamlink 내장 `soop` 플러그인에 위임 |

`auto`는 인증 거부, 잘못된 프로토콜·미디어, 프레임 연속성 오류에서 fallback하지 않습니다. Grid 출력이 시작된 뒤 HLS 바이트를 이어 붙이거나 두 타임라인을 혼합하지 않습니다. 예시와 제한은 [사용자 설명서](docs/COMMUNITY_GUIDE_KO.md)에 있습니다.

## 주요 제한

- Experimental/Beta입니다. 최신 번들 `400f028e…`로 30분 Grid-only 녹화와 전체 파일 디코딩·TS 타임스탬프 검사를 통과했지만, 해당 방송에는 B-frame이 없었습니다. 이전 번들의 별도 30분 녹화에서는 B-frame 재정렬을 검증했으며, 두 결과를 동일 빌드 결과로 표현하지 않습니다. 별도의 HRD/POC 표시 순서 충돌은 여전히 안전하게 거부됩니다. 지각적 A/V 동기, 모든 코덱·방송 조합 및 장시간 자연 장애 복구는 미입증입니다. [최신 30분 검증](docs/LIVE_VALIDATION_30M_20261028.md)과 [시간축 분석](docs/TIMELINE_MODEL_FIELD_VALIDATION_20261028.md)을 참고하세요.
- 제어된 실험에서 약 600초 녹화와 한 세션의 Parent 소켓 종료 3회 후 Cache 기반 복구를 확인했습니다. 이는 모든 장애에서의 무손실 보장이 아닙니다.
- 자동 계정 재인증, 방송 종료와 네트워크 장애의 완전한 구분, 방송 번호 자동 전환은 지원하지 않습니다.
- 지원 범위는 검증된 H.264 timing/POC 구조와 ADTS AAC 일부입니다. 다중 Parent 동시 수신은 지원하지 않습니다.
- Native Grid에서는 검증된 `original` 스트림만 제공합니다. HLS 모드는 내장 `soop` 플러그인이 제공하는 화질 선택을 사용합니다.
- Cache Overlap과 연속성을 확인할 수 없으면 데이터를 추정하지 않고 오류로 종료합니다. 3초간 미디어가 없으면 장애 복구를 시도할 수 있습니다.
- 선택적 진단 JSONL은 최대 16 MiB에서 기록을 멈추며, 수신 스레드에 추가 I/O 부담을 줍니다. 기본 녹화에서는 켜지지 않습니다.
- Native Grid TCP 연결에는 플러그인이 TLS를 추가하지 않으며 일부 인증 관련 값이 프로토콜 패킷에 포함됩니다. 신뢰할 수 있는 네트워크에서만 사용하세요.
- Windows/macOS 런타임, 모든 방송 조건, 장기 A/V drift는 검증되지 않았습니다.
- 인증 방송에는 `--soop-grid-username`과 `--soop-grid-password` 또는 `SOOP_ACCOUNT_ID` / `SOOP_ACCOUNT_PASSWORD`를 사용합니다. Streamlink 내장 `--soop-username` / `--soop-password`는 외부 플러그인에 전달되지 않습니다. 방송 비밀번호는 `--soop-grid-stream-password` 또는 `SOOP_STREAM_PASSWORD`로 별도 지정합니다.

세부 상태는 [제한사항](docs/LIMITATIONS.md), [정적 사전 감사](docs/PRE_RELEASE_STATIC_AUDIT.md), [실제 장애 복구 검증](docs/LIVE_FAILOVER_VALIDATION.md), [종합 강화·현장 검증](docs/COMPREHENSIVE_HARDENING.md)을 확인하세요.

## 상세 문서와 소스

- [한국어 설치·사용 설명서](docs/COMMUNITY_GUIDE_KO.md)
- [단일 파일 설치 및 빌드 안내](docs/SINGLEFILE.md)
- [인증 옵션 호환성 및 제약](docs/AUTH_COMPATIBILITY.md)
- [아키텍처와 프로토콜](docs/ARCHITECTURE.md) 및 [제한사항](docs/LIMITATIONS.md)
- [H.264 타이밍 오류 원인과 지원 범위](docs/H264_TIMING_DIAGNOSIS.md)
- [복구 정책](docs/RELIABILITY.md)
- [실제 제어 장애 복구 결과](docs/LIVE_FAILOVER_VALIDATION.md): 실서버에서 Parent 소켓 종료 3회 및 Cache Overlap을 시험한 기록
- [검증 이력](docs/VALIDATION.md), [구현 결과 요약](docs/PRODUCTION_READINESS_RESULT.md), [Reliability 결과](docs/RELIABILITY_RESULT.md): 여러 리비전의 결과를 당시 기록으로 보존
- [Failover 최적화 기록](docs/FAST_FAILOVER_OPTIMIZATION.md): 장애 감지·Cache 복구 구현과 이전 측정치
- [Multi-Parent 가능성 조사](docs/MULTIPARENT_PROTOCOL_FEASIBILITY.md): 동일 Endpoint 동시 수신 관측 및 선택 수신의 한계; Active-Active 기능은 아님
- [Cache 범위 REA 분석](docs/GRID_CACHE_RANGE_REA.md): 분석된 클라이언트에서 명시적 종료 프레임 제어가 입증되지 않았다는 정적 분석
- [정적 사전 감사](docs/PRE_RELEASE_STATIC_AUDIT.md): Beta 배포 제한 및 오프라인 검사
- [진단 자료 안내](docs/DIAGNOSTICS.md): 선택형 진단과 개인정보 취급
- [Python 모듈 정본](soop_grid/), [Streamlink 진입점](plugins/soop_grid.py)

이 Streamlink 플러그인은 같은 저장소의 Go 기반 Integrated Recorder Source Adapter와 별도 제품입니다. 운영 Adapter나 Core에 통합되어 있지 않습니다. 저장소 루트 README는 Go Adapter를 설명합니다.

## 라이선스

이 플러그인과 생성 배포 파일은 **AGPL-3.0-only**입니다. 자세한 내용은 [LICENSE](LICENSE)를 확인하세요. 방송 접근 권한, 저장 권리 및 SOOP 이용 조건을 준수해야 합니다.
