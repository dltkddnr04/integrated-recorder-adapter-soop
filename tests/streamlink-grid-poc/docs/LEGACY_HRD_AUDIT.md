# 오프라인 HRD 감사 API

이 도구는 역사적 HRD 시간축 결과를 재현하기 위한 오프라인 Python API다. 생산 receiver나 배포 플러그인이 import하지 않는다. Experimental/Beta의 지원 범위를 확대하지 않는다.

프로젝트 디렉터리에서 다음 형태로 사용한다. 입력은 보존된 Annex-B access unit과 100ns Grid timestamp이며, 실제 방송·인증정보를 예제에 넣지 않는다.

```python
from tools.legacy_h264_audit import LegacyH264Clock

clock = LegacyH264Clock()
# capture_reader는 사용자가 준비한 읽기 전용 오프라인 입력이다.
for annex_b_au, timestamp_100ns in capture_reader:
    observation = clock.inspect(annex_b_au, timestamp_100ns)
    if observation is not None:
        print(observation["timing_model"], observation["dts"], observation["pts"])
```

반환값은 분석 후보 DTS/PTS(90kHz), POC, SEI 및 모델 이름이다. 시작 전 pre-roll에서는 None을 반환하고, 구문 오류나 순서 충돌에서는 GridError를 발생시킨다. 출력 payload나 전체 반환 객체를 로그에 덤프하지 않는다.

과거 HRD 모델은 `PTS = DTS + rescale(dpb_output_delay * clock_tick)`이며, HRD가 없고 zero-reorder가 입증된 경우에만 과거 `PTS = DTS` 비교 모델을 사용한다. 이것은 현재 Grid composition 모델의 대체 경로나 사용자 fallback 옵션이 아니다.

생산 API는 `H264Clock.inspect(payload, timestamp, composition_offset)`로 composition을 반드시 요구한다. 공통 SPS/PPS/SEI/slice/POC 파서를 감사 도구가 재사용하지만, 생산 코드가 감사 도구를 참조하지 않는다. 따라서 이 감사 클래스 자체는 독립 디코더가 아니다. FFmpeg raw H.264 표시 순서, 원본 Grid AU 대응 및 출력 PES identity 검사를 함께 사용해야 한다.

역사적 계산 및 오류 회귀는 다음 명령으로 재현한다.

```sh
python -m pytest tests/test_h264clock.py tests/test_h264_audit.py
python tools/test_bundle.py -q
```

같은 테스트 파일 안에서도 생산 구문·POC 검사는 생산 clock을 직접 검사하고, 과거 HRD/no-reorder 계산과 미지원 구조의 연구 재현만 LegacyH264Clock을 사용한다. 생산 clock은 POC type 1 및 Grid MMCO5 등 기존 미검증 구조를 계속 거부한다.
