# Grid composition 시간축 적용 및 오프라인 검증

상태: Experimental/Beta. 이 문서는 기존 HRD 기반 역사 보고서와 구분되는 현재 구현을 설명한다. 신규 실방송은 수행하지 않았다.

## 입력과 계산

검증한 Native Parent `0xC748` body version 1, header length 69에서 frame number(+12), kind(+40), startTime(+41), 의미 미확정 u64(+49), composition raw u32(+57)를 파싱한다. 확장부는 payload 앞에 별도로 경계 검증한다. +49는 endTime으로 사용하지 않으며 +61의 wall-clock 후보는 시간 계산에 사용하지 않는다. 다른 body version/header는 안전하게 거부한다.

현재 지원하는 composition 범위는 0..2^31−1이다. 상위 bit가 설정된 미관측 음수/unsigned 확장 패턴은 `grid_composition_offset_unsupported`로 거부한다. raw start+composition의 u64 overflow도 거부한다.

```
origin = min(first accepted audio start, first output-capable IDR start)
DTS = round((startTime - origin) * 90000 / 10000000) + 90000
PTS = round((startTime + composition - origin) * 90000 / 10000000) + 90000
```

합산 후 Fraction으로 한 번만 rescale한다. origin은 IDR 이후 고정하며 복구와 다음 IDR에서 재설정하지 않는다. 생산 receiver는 처음 양 트랙을 관측할 때까지 최대 600개 frame/32MiB를 보류해 패킷 도착 순서가 원점을 결정하지 않게 한다. 기존 영상 pre-roll 및 오디오 sample-clock 검증은 이후 그대로 실행한다. 시작 버퍼가 출력되지 않은 채 종료되면 성공적인 A/V 녹화로 판정하지 않는다. 저수준 MediaSink 테스트/분석 API는 단일 트랙의 직접 검사를 위해 이 시작 보류를 선택적으로 사용할 수 있다.

## H.264와 AAC 불변조건

HRD Picture Timing은 구조적으로 파싱하지만 생산 Grid 경로에서는 DPB delay로 PTS를 생성하지 않는다. SPS/PPS/SEI/slice/Access Unit, POC wrap, POC와 PTS 순서, 재정렬 한도, IDR epoch 순서, 엄격한 DTS 증가와 MPEG-TS 검증은 유지한다. 동일 picture epoch의 중복 PTS도 거부한다.

현재 생산 Grid 경로는 검증된 progressive POC type 0/2를 지원한다. POC type 1, Grid MMCO5, low_delay_hrd, interlace, 미지원 slice 구조 및 midstream codec 변경은 여전히 거부한다. composition 입력이 없는 H264Clock 분석 API는 역사 비교를 위한 기존 HRD 경로를 유지하되 한 clock에서 시간 소스를 전환할 수 없다. 사용자가 선택하는 fallback 옵션이 아니다.

AAC-LC의 AU sample 수와 샘플레이트로 정확하게 누적 시간을 계산한다. chunk timestamp의 bounded residual 검사는 그대로 유지하며 매 묶음에서 원점을 바꾸지 않는다. AU 삽입/제거, 무음 생성, resampling이나 임의 A/V 보정은 하지 않는다.

Cache identity는 frame number로 조회한 뒤 kind, startTime, composition raw 값, payload SHA-256을 모두 비교한다. +49/+61 변화만으로 중복 미디어를 거부하지 않는다. overlap을 입증하기 전 신규 프레임 수락 금지와 오류 전달은 유지한다.

## 독립 근거와 한계

4개 보존 원본에서 wire framing/체크섬, payload 경계 및 FFprobe raw-H264 표시 순서를 대조했다. Grid 종류 P에도 실제 B-picture가 포함된다. 아래 B-picture 수는 실제 비트스트림을 디코더가 분류한 값이다.

| 캡처 | 출력 picture | 실제 B-picture | 원본 AAC AU | 디코딩 PCM samples/채널 |
|---|---:|---:|---:|---:|
| A | 2998 | 2229 | 2352 | 2408448 |
| B | 2161 | 0 | 1687 | 1727488 |
| C | 2209 | 1209 | 1704 | 1744896 |
| D | 2668 | 0 | 2109 | 2159616 |

D의 SPS/PPS/IDR 이전 23개 영상 pre-roll은 출력하지 않는다. 10036개 검증 가능 picture에서 startTime 단독 표시 순서 역전 2453건은 composition 적용 시 0건이며 중복 candidate PTS도 없었다. 전체 원본 미디어 framing은 10390개였다.

최종 번들로 4개를 재생했다. 각각 전체 H.264/AAC 디코딩, PSI/CRC, TS sync/continuity, PCR 진행, DTS 증가, PTS-before-DTS 및 AAC overlap 검사를 통과했다. 원본 H.264(의도적 pre-roll 제외)→내부 출력→독립 PES 추출의 SHA-256이 일치했다. AAC 원본→내부 출력→PES도 일치했다. 원본 미디어·접속정보는 저장소에 포함하지 않는다.

기존 실패 표본의 POC0 IDR/POC2 P picture, DPB18/2, Grid15ms, composition0/0도 재검증했다. 새 PTS 차이는 +15ms이며 두 picture가 통과한다. 이를 전체 해당 방송의 수신 성공으로 확대하지 않는다.

보존된 공식 웹 자산 LivePlayer.js SHA-256 `1f3cc2f27c3827c8d16f2a3abed2ff61565bc9defefcf423049c739501f7b652`에서는 LiveCache 공통 원점과 AAC sample index, video composition의 MP4 전달을 확인했다. Native TCP→브라우저 LiveCache 변환 경계 및 모든 방송의 공식 clock 의미는 미확정이다. 공식 플레이어의 무음 삽입/프레임 제거 정책은 녹화기에 복사하지 않았다.

B/D의 AAC 묶음 timestamp 잔차는 각각 0..20ms와 −12..8ms로 bounded한 packetization 변동에 부합한다. coverage 경계 차이는 지각적 lip-sync나 장기 clock drift의 증명이 아니다. 신규 모델의 장시간 실방송, 모든 인코더/OS 조합, 지각적 A/V 동기는 미검증이다. 과거 30분 녹화는 과거 artifact의 결과로 유지한다.

## 재현 검증

- 전체 모듈 pytest 및 단일 파일 bundle 회귀, FFmpeg 의존 검증 포함.
- Ruff check/format, compileall, 결정적 bundle 재생성, manifest 비교, clean Streamlink 로딩 테스트.
- 새 경계 테스트: composition sign/overflow, 한번만 rescale, 시간 소스 전환 금지, Cache composition 충돌, 도착 순서와 무관한 원점, 시작 버퍼 상한, extension 경계, 미확인 version 거부.
- 기존 synthetic B-frame fixture의 Grid 헤더 composition은 독립 FFprobe 표시 순서로 생성한다. 실제 4개 캡처 검증과 구분한다.

구현 전 기준 HEAD: `6d8a0095038a9171e9daf647cfd995ba00c9212d`.

최종 검증: 모듈 pytest **614 passed, 0 skipped**, bundle **605 passed, 0 skipped**. FFmpeg/FFprobe `N-128155-g8e362b7b51-20261028`을 사용했다. Ruff check/format, compileall, 결정적 재생성 및 체크섬 검사를 통과했다. 독립 읽기 전용 반증 리뷰에서 검토 범위 내 차단 결함은 발견되지 않았다.

배포 번들 SHA-256: `9e08c19e2737b1436da4c439cabd2742a659eca93b1c671c3ce100d940ad591d`.
