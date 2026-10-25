# Integrated Recorder SOOP Source Plugin

**한국어** | [English](README.en.md)

SOOP live stream을 Adapter Protocol v1로 연결하는 first-party Source Plugin입니다.

> [!WARNING]
> **상태: 개발 / 실험 중. Stable Plugin Registry 배포: 아니요.**
>
> 고화질 stream 선택이 아직 안정적으로 해결되지 않았습니다. AID 발급 경로와 egress 조건에 따라 browser보다 낮은 해상도의 stream assignment가 반환될 수 있습니다. 이를 해결한 공식 동작이나 안정 배포를 주장하지 않습니다.

## 역할과 현재 동작

이 standalone executable은 channel 조회를 Core가 호출하는 개별 요청으로 처리하며 녹화기나 polling loop를 실행하지 않습니다.

- `watch`: channel이 offline인지 live인지 확인하고, 방송 단위 session reference와 HLS source를 반환합니다.
- `resolve`: live channel을 HLS media로 해석합니다.
- `metadata`: 같은 broadcast number가 유지되는 동안 현재 title을 보고합니다.
- `refresh`: HTTP 401/403 이후 새 AID와 manifest를 요청합니다.

Core가 watch 주기를 제어하며 recording, segment 보관, metadata timeline, VOD와 archive integrity를 소유합니다. 이 plugin은 이를 대신하지 않습니다.

## 고화질 선택 제한

SOOP의 browser player에서 더 높은 화질을 선택할 수 있어도, adapter가 AID를 발급받는 경로와 egress 조건에 따라 더 낮은 화질의 manifest assignment를 받을 수 있습니다. 특정 방송의 해상도를 일반적인 제한으로 단정하지 않습니다.

실험 중 AID 발급을 다른 egress로 보냈을 때 1920×1080 stream을 받은 사례가 있었지만, 이는 가능한 경로를 보여준 관찰이지 일반적인 해결책은 아닙니다. 현재 AID 발급 egress만 분리하는 방법을 조사하고 있습니다. 모든 media segment를 proxy하는 방식은 확정된 해결책이 아닙니다. 이 품질 선택 문제가 해결되기 전까지 stable official Registry 배포 대상으로 간주하지 않습니다.

## 입력과 설정

| 항목 | 위치 | 설명 |
| --- | --- | --- |
| `channel` | 필수 입력 | SOOP login ID 또는 `play.sooplive.com`, `play.sooplive.co.kr`, `play.afreecatv.com` URL. URL의 두 번째 경로 segment에 broadcast number를 포함할 수 있습니다. |
| `stream_password` | secret 입력 | 보호된 방송의 선택적 비밀번호입니다. |
| `quality` | 설정 | 기본값은 `best`; `worst` 또는 정확한 SOOP preset 이름/label을 선택할 수 있습니다. `auto`는 제공하지 않습니다. |
| `account_username` | secret 설정 | 선택적 SOOP 계정 이름입니다. `account_password`와 함께 입력합니다. |
| `account_password` | secret 설정 | 선택적 SOOP 계정 비밀번호입니다. `account_username`과 함께 입력합니다. |

인증이 필요한 경우(`RESULT=-6`) adapter는 로그인 후 요청을 재시도합니다. stream 및 계정 secret은 Protocol v1 secret state mutation으로 channel/broadcast별 namespace에 저장되어 adapter process 재시작 후 refresh에 사용할 수 있습니다. 이를 media metadata나 오류 메시지에 넣지 않습니다. 서명된 manifest URL은 sensitive로 표시됩니다.

플랫폼 오류, login 요구 또는 malformed response는 offline 상태가 아니라 오류입니다.

## 플랫폼 흐름과 보안

Adapter는 SOOP live API에서 상태, 방송 identity, title, CDN과 화질 preset을 조회합니다. Broadcast number, 선택 화질, 선택적 stream password로 AID를 요청하고, 반환된 RMD host의 `broad_stream_assign.html`에서 HLS manifest URL을 얻습니다. 동적 플랫폼 URL은 HTTPS와 SOOP/Afreeca domain을 요구합니다.

실행 파일은 stdin에서 Protocol v1 newline-delimited JSON을 받고 stdout에는 protocol frame만 씁니다. 진단은 stderr로 보냅니다. Plugin은 native executable이며 sandbox가 없습니다.

## 개발과 검증

```sh
GOWORK=off go test -race -count=1 ./...
GOWORK=off go vet ./...
GOWORK=off go build -o integrated-recorder-adapter-soop ./cmd/integrated-recorder-adapter-soop
```

이 저장소는 [Adapter SDK for Go](https://github.com/integrated-recorder/adapter-sdk-go)를 사용합니다. 배포 상태와 release는 [Plugin Registry](https://github.com/integrated-recorder/plugin-registry)에서 확인하세요. 현재 stable official Registry 배포는 없습니다.

## 실험적 Native Grid PoC

독립 Go Grid 수신 연구와 합성 fixture 테스트는 [tests/grid-poc](tests/grid-poc/README.md)에 있습니다. 기존 Adapter Protocol v1 플러그인과 분리된 실험 코드이며 운영 어댑터에 통합되지 않았습니다.

## 생태계와 라이선스

- [Integrated Recorder Core](https://github.com/integrated-recorder/core) — recording과 canonical archive
- [Adapter SDK for Go](https://github.com/integrated-recorder/adapter-sdk-go) — Source Plugin authoring API
- [Plugin Registry](https://github.com/integrated-recorder/plugin-registry) — 승인된 release artifact index
- [source.owncast](https://github.com/integrated-recorder/source.owncast) — 별도 first-party integration

라이선스는 [LICENSE](LICENSE)를 참고하세요.
