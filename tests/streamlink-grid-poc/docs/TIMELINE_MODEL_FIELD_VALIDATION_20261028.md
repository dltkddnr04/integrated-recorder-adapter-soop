# SOOP Grid 시간축 모델 — 현장 검증 추가 기록 (2026-10-28)

## 결론 요약

기존 `h264_hrd_grid_clock_conflict`를 발생시킨 Grid 시각과 HRD 시각의 1 ms 이내 일치 조건은 수신기가 추가한 가정이다. SOOP 프로토콜이나 H.264가 요구하는 조건이라는 증거가 없어 제거했다. 이 오류 자체는 미디어 손상의 증거가 아니었다.

그 검사를 제거했다고 모든 타이밍 문제가 해결된 것은 아니다. 수신기는 현재 Grid timestamp를 임시 DTS 후보로 사용하고, HRD `dpb_output_delay`와 SPS/VUI clock tick으로 상대 PTS를 만든다. POC는 표시 순서 검증에만 쓴다. 이전 번들 `03b31e…`의 30분 Grid 녹화는 B-picture가 포함된 H.264/AAC 스트림을 끝까지 처리해 독립 검사에 통과했다. 이후 정확한 `400f…` 번들도 30분 Grid-only 녹화와 전체 검증을 통과했지만, 그 방송에는 B-slice가 없었다. 각 AAC 및 프레임 통계는 해당 실행의 해시로 구분한다. 두 실행은 별도로 보고한다. Grid 시각의 공식 의미, 모든 방송에서의 Grid와 HRD clock 관계, 인지 가능한 A/V 동기화까지 증명한 것은 아니다.

최종 상태는 **PARTIAL**이다. 이전 `03b…`의 실방송 B-frame 검증과 exact `400f…`의 별도 30분 실방송 검증은 각각 확보했으나, exact `400f…` 녹화에는 B-slice가 없었다. 별도의 POC/HRD 충돌 스트림은 여전히 안전하게 거부되며 그 충돌의 원천을 판별할 공식 시간 기준도 없다.

## 1. 기존 1 ms 검사의 타당성

해당 조건은 Grid timestamp를 DTS로 정한 뒤 HRD CPB removal 경과와 같은 시계라고 간주해 도입한 구현 정책이었다. SOOP 공식 플레이어가 이를 비교한다는 증거도, H.264 규격이 서로 다른 출처의 시계가 1 ms 안에 일치해야 한다고 요구한다는 근거도 없다. 따라서 잘못된 입력 검증 조건이었다. 임계값을 넓히지 않고 직접 비교 게이트를 제거했다.

과거 기록의 `8.333333 ms` 대 `8.200 ms`는 각각 1/120초 VUI tick과 관측된 82,000 × 100 ns Grid 간격이다. 차이는 **0.133333 ms**(약 1,333.333 × 100 ns)다. 이는 인접 간격 비교이며 epoch 오프셋이 아니다. 다른 캡처의 16 HRD ticks 대 135 ms Grid 간격은 133.333 ms 대 135 ms로, 약 1.667 ms 차이다. 이 차이만으로 한쪽 시계가 잘못됐다고 결론낼 수 없다.

## 2. 후보 시간축 모델 비교

| 모델 | 정의 | 현재 증거와 판정 |
|---|---|---|
| A | Grid timestamp를 DTS로 쓰고 PTS=DTS로 둠 | Grid 시각의 증가성은 실제 수신에서 관측된다. 그러나 B-picture가 있는 30분 표본에는 B-slice 80,179개와 ffprobe PTS reorder step 27,027개가 있었다. PTS=DTS는 이 표본의 표시 재정렬 정보를 표현하지 못하므로 채택하지 않는다. |
| B | Grid 시각으로 DTS 후보를 만들고 HRD DPB output delay를 상대 PTS로 적용 | 채택된 제한 모델. 실제 30분 표본에서 DTS 회귀, PTS<DTS, 오디오 겹침 및 전체 디코딩 오류가 없었다. 이 성공은 그 스트림 구성에 대한 실증이지 Grid timestamp의 표준적 의미 증명은 아니다. |
| C | Grid와 HRD 사이의 오프셋/선형 변환을 추정 | 과거 15초 I/P 전용 자료의 진단 회귀는 기울기 약 0.9999904, RMS 약 0.8926 ms, 최대 잔차 약 5.8544 ms였다. 표본에 B-picture가 없고 오프셋은 의미가 확인되지 않았다. 독립 시간 기준도 없어 보정식으로 채택할 수 없다. |
| D | Grid를 전송·버퍼 시계로 보고 별도의 미디어 시계를 복원 | 원시 관측과 맞을 수 있는 가설이나, 현재 확보한 Grid 자료만으로 독립 시각의 epoch/rate를 유일하게 복원할 수 없다. 검증 가능한 대체 시계가 없어 구현하지 않았다. |

HRD는 bitstream에 신호된 CPB/DPB timing syntax다. 그 값은 Grid packet의 도착 시각이나 proprietary timestamp와 자동으로 같은 시계가 되지 않는다. 반대로 Grid timestamp가 decode time이라는 공식 확인도 없다. 구현은 이를 empirical mapping으로 제한하고, HRD/POC 표시 순서가 모순되면 fail-closed 한다. 기존 성인 공개 방송 표본의 POC/HRD 충돌은 여전히 미지원 사례다.

## 3. `03b…` 30분 Grid 캡처의 AAC 묶음 분석

이 절의 카운트는 B-frame을 포함한 `03b…` 실행 자료다. 비공개 원본 `grid-wire.bin`을 패킷 원문 공개 없이 순차 판독했다. Parent framing과 `0xC748` media body 경계를 검증하고 AAC payload만 `parse_media`/ADTS parser로 집계했다. exact `400f…` 실행은 오디오 메시지 3,600개, AAC AU 84,375개, 채널당 86,400,000 PCM sample(48 kHz)을 기록했으며 별도 보고서에 적었다.

* 총 Grid media messages 111,626개: 비디오 108,107개, 오디오 3,519개.
* 오디오 메시지당 24개의 ADTS/AAC access unit, 각 AU 1,024 core samples.
* 총 84,456 AU, 86,482,944 samples/channel, 48 kHz stereo.
* 인접 오디오 Grid timestamp는 모두 5,120,000 × 100 ns, 즉 512 ms였다. 24 × 1,024 / 48,000초와 정확히 일치했다. 표본에서 관측된 timestamp는 오디오 묶음 시작 시각으로 sample clock을 전개하는 해석과 부합한다.
* 인접 chunk timestamp와 직전 chunk의 샘플 수로 계산한 차이는 이 표본에서 0 × 100 ns였다. 이는 측정된 표본의 정확한 정합이지 프로토콜 전체 보장은 아니다.
* 첫 오디오 chunk timestamp는 첫 비디오 timestamp보다 238 ms 빨랐다. 전체 audio sample duration 1,801.728 s와 video Grid span 1,801.766 s를 공통 원점으로 단순 비교하면 마지막 coverage 차이는 약 −276 ms다. 이는 timestamp·샘플 범위의 coverage 계산이며 지각 동기 측정이 아니다.

각 오디오 Grid 메시지의 한 timestamp를 내부 AU마다 복제하지 않는다. 메시지 timestamp를 첫 AU의 관측 기준으로 취하고 나머지 AU는 1,024-sample 단위의 정확한 유리수 sample clock으로 전개한다. 후속 chunk timestamp는 그 누적 sample clock과 비교한다. 실제 수신 도착 시각은 미디어 표시 시각으로 취급하지 않는다.

## 4. 최종 제한 모델과 MPEG-TS 검증

* 비디오: Grid timestamp의 단조 증가값으로 내부 DTS 후보를 만들고, HRD가 있는 지원 구조에서는 각 picture의 DPB output delay로 상대 PTS를 계산한다. POC는 decode/presentation order 모순과 재정렬 한도를 검증하며 시간 단위로 변환하지 않는다.
* HRD 없는 영상: 재정렬 없음이 구조적으로 증명되는 제한 subset만 PTS=DTS를 허용한다. 임의 FPS로 타임라인을 보간하지 않는다.
* 오디오: 첫 Grid audio timestamp와 AAC sample count/rate를 이용한다. 비디오 재정렬 여유를 오디오에 임의로 더하지 않는다.
* 공통 A/V 원점과 Grid epoch 의미는 구현 가정/관측 모델이며, 공식 프로토콜 의미로 단정하지 않는다.
* TS의 33-bit PTS/DTS/PCR 직렬화는 wrap 도메인에 맞춘다. 독립 검증기는 TS 패킷, PSI CRC/구조, 선언된 PCR PID 진행, packet timestamp와 duration을 검사한다.

## 5. 30분 실제 녹화 증거

먼저 실행한 B-frame 30분 시험은 번들 SHA-256 `03b31e3712160cc5bdcc398e63aeb602c510994d144d56c079b8f8998f9d8ff1`로 Grid-only 수행됐다. 이후 번들 `400f028ed99467ad644d61525f8a59f85477221dbba28708710ff06d77682cd0`의 변경은 StreamIO 진단용 accepted/discarded/drain accounting이며 media, timestamp, mux 경로는 변경되지 않았다. 다만 두 번들의 라이브 실증 범위는 분리해 기록한다.

30분 증거는 다음과 같다.

* Streamlink 출력 1,662,461,064 bytes, SHA-256 `5c275e5d1f307d53431b891da4c0f714467d43c37d92e848b9b7cdb5abbbba82`; private MediaSink 출력과 바이트 단위 일치.
* 1,804.456 s process runtime, 약 1,800.794 s 설정 녹화 구간, `duration_limit`, exit 0.
* 1920×1080 H.264, VUI 60 fps, POC type 0, HRD/SEI. 108,107 video frames: 80,179 B, 27,027 P, 901 I. B-slice 증거는 실제 수신 분석과 ffprobe의 27,027 PTS reorder step으로 확인.
* 3,519 AAC chunks / 84,456 ADTS AUs / 86,482,944 decoded PCM samples per channel at 48 kHz stereo. Audio PCM duration 1,801.728 s; video decoded duration 1,801.8167 s.
* 독립 verifier: 8,842,878 TS packets; sync, continuity, transport, adaptation, PSI CRC/structure, duplicate packet, PCR regression, DTS regression, PTS-before-DTS, AAC overlap, missing timestamp/duration 모두 0. PAT/PMT 각각 1,802회, PCR 108,107 sample, 최대 PCR value 간격 17 ms.
* 전체 H.264/AAC `ffmpeg -xerror -err_detect explode` decode 성공. Raw H.264/AAC payload와 TS PES payload hashes가 일치; AAC trailing bytes 0.
* Frame gap, duplicate output, identity conflict, recovery gap 0. 이번 30분 세션에서는 Parent 장애/재연결은 발생하지 않았다.

독립 verifier JSON과 녹화 및 raw 캡처는 사용자 승인 NFS의 비공개 검증 보관소에 보존되어 있다. 개인 경로와 원본 미디어는 저장소에 포함하지 않는다.

## 6. `400f…` 번들의 앞선 60초 실방송

번들 SHA `400f028ed99467ad644d61525f8a59f85477221dbba28708710ff06d77682cd0`를 격리된 Streamlink plugin 디렉터리에서 `soop_grid.py`로 로드하고 Grid-only로 60초 실방송했다. `duration_limit`, exit 0, HLS 미사용. 3,695 video frames와 2,904 AAC AUs를 수신했다. full decode 통과; 302,353 TS packets의 sync/continuity/PCR/PSI 오류 0, DTS regression/PTS-before-DTS/AAC overlap 0. 최종 TS SHA-256 `2a9f3ff11acac901a35956bf59972091b803d7d098bea8e17ddcb74264d288f9`. 이 60초 실행에서는 Video B-slice 및 HRD/SEI가 관측됐다. 이후 같은 번들의 30분 run은 별도 결과이며 B-slice를 포함하지 않았다. A/V coverage 경계 변화 335.3 ms는 source drift나 지각 동기로 해석하지 않는다.

실행 요약과 검증 JSON은 NFS의 `live-final-400f02-smoke-20261028/`에 보존됐다. final bundle의 추가 StreamIO 회계 변경은 출력 수신·배출 상태의 진단이며 timing/media 알고리즘은 30분 실행 이후 바뀌지 않았다.

## 7. 미해결 사항과 판정

1. 과거의 `h264_hrd_grid_clock_conflict`는 1 ms equality gate 때문에 발생한 자체 검증 오류였다. 제거 후에도 별도의 실제 HRD DPB delay 대 POC order 충돌이 있었고, 해당 stream은 여전히 거부된다. encoder signaling과 Grid→DTS mapping 중 무엇이 원인인지는 원본 기준 clock 없이 분리할 수 없다.
2. `03b…`의 30분 B-frame/HRD stream은 내부 일관성, B-frame reorder, 전체 decode 면에서 통과했다. `400f…`의 별도 30분 녹화도 전체 decode·TS 검증에 통과했지만 B-slice는 포함하지 않았다. 어느 결과도 공식 player가 동일한 mapping을 쓴다는 것을 입증하지 않는다.
3. AAC chunk-start/sample-clock 일치와 24 AU 묶음 결과는 `03b…` 표본에 대한 것이다. `400f…`에서는 총 AAC AU와 PCM sample 수를 검증했지만 동일한 chunk 크기 분포를 가정하지 않는다. 방송 전반의 clock/epoch 불변조건은 검증되지 않았다.
4. A/V coverage 경계와 TS timestamp 정합성은 지각적 lip-sync 검증과 다르다. 독립적인 영상/음성 sync marker 또는 동시 기준 녹화가 없어 실제 인지 동기화는 **미입증**이다.
5. `400f…` exact-hash 30분 실행은 완료됐지만 이 방송에는 B-slice가 없었다. `03b…` 30분 run은 B-frame을 포함했으나 이전 artifact다. 두 결과를 하나의 동일 빌드 실증처럼 합산하지 않는다. exact `400f…` 수치는 [30분 실행 보고서](LIVE_VALIDATION_30M_20261028.md)에 있다.
6. 이전 HRD/POC 충돌 sample, 여러 encoder 변형, 자연 장애 복구, multi-hour endurance, 모든 OS/codec/방송 형식은 별도 미검증 또는 fail-closed 제한이다.

정리된 시점의 상태는 **PARTIAL / Experimental-Beta 제한 유지**다. exact `400f…` 30분 결과는 [별도 실행 보고서](LIVE_VALIDATION_30M_20261028.md)에 기록한다. 네트워크 저장 공간으로 검증 증거는 보존되지만, 그 사실은 지원 범위나 timeline 의미론의 불확실성을 없애지 않는다.
