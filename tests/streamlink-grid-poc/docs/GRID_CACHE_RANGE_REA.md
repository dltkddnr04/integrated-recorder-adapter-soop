# SOOP Grid Cache Range-End Control — REA Findings

**Date:** 2026-10-25 UTC

**Scope:** Native `0xCB30` Cache Data Request serialization, its Cache Header caller, and the related Cache Stop control path. No Grid receiver behavior was changed. A bounded live probe was prepared but did not reach the network because the active Python lacked `pycryptodome`.

## Executive result

The analyzed client has a `0xCB30` request path carrying a 16-byte body. Its sender has no separate `end_frame` or `frame_count` argument. The saved Ghidra decompilation shows a session-derived 64-bit value and a caller-provided 32-bit state word in that body; the semantics of those state fields are not fully resolved. The client therefore provides no statically demonstrated explicit range-end field in this request.

A distinct native function named `FeatureSocketHandler::handleReqCacheStop(void)` sends a 16-byte header-only `0xCB2B` packet. The binary's nearby string labels it `V2S_REQ_CACHE_STOP`, and `handlePushFrame` conditionally invokes that function. This is evidence for a Cache Stop control message. It is **not** evidence that the command stops all media at an arbitrary Frame ID: the existing live capture sent `0xCB2B` after the empty `0xC742` notification and continued receiving media in the live-push flow.

**Verdict for the requested behavior (server sends through frame M, then sends no later media): `D — CLIENT_SIDE_STOP_ONLY` for the analyzed client path.** This is scoped to arbitrary end-bounded media retrieval. A cache-phase stop/transition command exists, but no frame-specific end/count request or global media-stop behavior was demonstrated. This does not prove that no undocumented server extension exists.

## REA target and provenance

| Item | Observation |
|---|---|
| Product/artifact | `SOOPStreamer.exe`, an x86-64 PE32+ executable; official-host update candidate, not a verified signed release |
| SHA-256 | `98a1741b2368be33e25958a46443760663ff8da225c742aabac16d625297709b` |
| Version/build | No reliable product version identified. PE timestamp is 2026-10-22 01:25:24 UTC; this is not a product-version claim. |
| Image base | `0x140000000` |
| REA provider | Ghidra 12.1.4, headless, JDK 21; prior saved REA decompilation evidence is tied to the SHA above |
| Provenance limitation | The artifact inventory reports official update-host retrieval, but the update metadata was stale and Authenticode verification failed. Treat it as an official-host candidate only. The binary was analyzed as data, not executed. |
| Fresh-session limitation | A fresh REA query in this task timed out after 300 seconds while importing/analyzing the target. The report therefore reuses the retained REA/Ghidra pseudocode and string inventory for this exact hash. It does not claim a fresh successful Ghidra xref dossier for this task. The REA session was closed cleanly. |

Retained REA evidence:

* `rea-research/evidence/SOOPStreamer-functions/p2p-send-cache-data.mcp.json` — evidence `ev_c85a10bd84dee21220e398791fef9d39b9ab9aa2d7bfcbd26f79633f11e12372`, procedure `0x1402f6050`.
* `rea-research/evidence/SOOPStreamer-functions/p2p-handle-cache-header.mcp.json` — evidence `ev_e3f9fcb99e62f98ae1b681c67a34183bcf525f5465fd613ec8299ca3d18188ae`, procedure `0x1402f32e0`.
* `rea-research/evidence/SOOPStreamer-functions/p2p-handle-push-frame.mcp.json` — evidence `ev_6c5e3d1f9e4e6dbf4704d81e0b2248a8451ac34f6d080ed37e51f7ab0289f526`, procedure `0x1402f54f0`.
* Corresponding readable Ghidra output is in the `.decomp.txt` files in that directory. `rea-research/evidence/SOOPStreamer-focused-strings.txt` records `V2S_REQ_CACHE_STOP` at file offset `0x850fe8` and the following `handleReqCacheStop` diagnostic strings.

The `handleReqCacheStop` wire construction below was checked against the same on-disk PE with bounded `objdump` disassembly and PE section mapping. That corroboration is not represented as a fresh REA pseudocode result.

## `0xCB30` request construction

### Function and call path

* `FeatureSocketHandler::handleCacheHeaderVer2(const unsigned char *, unsigned __int64)` — VA `0x1402f32e0`, RVA `0x2f32e0`.
* `FeatureSocketHandler::sendReqCacheDataVer2(int)` — VA `0x1402f6050`, RVA `0x2f6050`.
* In the retained Ghidra pseudocode, `handleCacheHeaderVer2` accepts the cache-header path only after a minimum-size check (`param_3 >= 0x24`), passes the response into `FUN_14019e2f0`, updates state, and calls `sendReqCacheDataVer2` with a 32-bit value read from session state at nested offset `+0x70`.
* The sender constructs the packet and passes its buffer to `FUN_14012c770`.

### Serialized packet

The Ghidra pseudocode writes a 32-byte packet: 16-byte header followed by a 16-byte body. The observed header bytes are little-endian:

| Packet offset | Size | Observed construction |
|---:|---:|---|
| `0x00` | 4 | `0xCB300202` stored little-endian (`02 02 30 CB`) |
| `0x04` | 4 | Body size `0x10` |
| `0x08` | 8 | `0xC922` in low word, remaining bytes zero |
| `0x10` | 4 | `FUN_1401921a0(uVar11 & 0xffffffff)` |
| `0x14` | 8 | `lVar4 + 1` when nonzero, else zero; `lVar4` is loaded from the session's `+0x90` object at offset `+0x80` |
| `0x1c` | 4 | Sender argument `param_2`, supplied by the cache-header handler from the session's `+0x90` object at offset `+0x70` |

The body layout follows the 64-bit stores in the decompilation (`puVar13[2]` and `[3]`) and little-endian x86-64 representation. No distinct store sourced from an end-frame argument or frame-count argument appears in this sender. Its function signature itself has only the handler object and one 32-bit argument.

**Semantic caution:** the second body field is position-like and the existing live cache probe establishes that the Go/Python `0xCB30` form can use a chosen start Frame ID. However, the native candidate's nested `+0x80` field has not been fully traced to its original writer/type. The first transformed word and final state word also remain semantically opaque in this binary. Do not infer a formal field name solely from offsets or the old public protocol description.

### What this establishes

For the analyzed sender, the request contains one 64-bit session-derived value, not independently evidenced start and end/count values. The existing live experiment separately verified that a recent requested start was honored, but the stream continued beyond that start during the bounded observation. No end-bound completion was observed.

## Cache Stop control path

### Native code evidence

* `FeatureSocketHandler::handlePushFrame(int)` — VA `0x1402f54f0`, RVA `0x2f54f0`. Its retained Ghidra pseudocode conditionally calls `FUN_1402f5c70(param_1)` when the state at `param_1 + 0x94` is zero and the state at `param_1 + 0x90` equals one.
* PE exception metadata places the callee in function range RVA `0x2f5c70–0x2f6048`.
* The function's embedded diagnostic strings identify it as `FeatureSocketHandler::handleReqCacheStop(void)` and include `V2S_REQ_CACHE_STOP`.
* Bounded disassembly at `0x1402f5c70` constructs `0xCB2B0202`, body length zero, checksum word `0xC929`, and sends the resulting 16-byte header through `FUN_14012c770`. No body carrying a target frame number is constructed.

This is a real client-to-Parent control path, not just a string. Its observed meaning is **Cache Stop** as named by the binary. The code and retained evidence do not show a frame argument, count, or target threshold on this function.

### Runtime interpretation and limits

In the existing successful live capture, the receiver sent one empty `0xCB2B` after an empty `0xC742` notification and subsequently received media through the live-push flow. That observation is consistent with stopping/completing the cache phase while entering or continuing a live phase. It contradicts treating `0xCB2B` as a command that ends all Parent media delivery. The capture was not a dedicated byte-after-stop benchmark, so it does not quantify the exact cache bytes suppressed by this command.

No new live request was sent for this report. The existing bounded Cache experiment intentionally did not send `0xCB2B`; it saw continued `0xC748` after `0xCB30`, while the separate normal capture exercised `0xCB2B` on the native-style transition.

## Cache state machine reconstructed from available evidence

The evidence supports this **candidate client flow**, not a fully recovered dispatcher or universal server state machine:

```text
Parent connection
  → 0xCB2E stream request
  → stream response handling / 0xCB2F follow-up in current candidate
  → 0xC747 cache-header handler
  → 0xCB30 Cache Data Request
  → 0xC748 media
  → 0xC742 push notification / callback path
  → 0xCB2B Cache Stop control
  → continued live-push media observed in the successful capture
```

The exact current-binary dispatcher edges for every response opcode are not in the retained focused dossiers. The binary contains response-name strings for `0xC746`, `0xC747`, and `0xC742`, while the live receiver has also observed the older response IDs on its tested path. Therefore the diagram must not be read as proof that every deployed server/version follows the same numeric sequence.

* **Cache completion:** no cache-end packet or field was identified in the analyzed sender/handler path. The empty `0xC742` was handled as a control notification in the live receiver, not treated as a media frame or a verified cache-range terminator.
* **Repeated `0xCB30`:** the retained handler sends one request after its cache-header path. Existing live trials saw one `0xC747` per connection and no server-issued second request invitation. Whether the native client can issue a second request under another state or connection is unresolved.
* **Connection reuse:** reuse of the same Parent socket for the subsequent live-push flow is observed in the successful capture. Reusing one socket for successive independently bounded cache ranges is not established.
* **`0xCB2B`:** a header-only control is emitted on the callback/state path. Existing runtime behavior continues media afterward; it is not verified as a global stream unsubscribe.

## Prior live evidence relevant to range feasibility

The existing report `tests/streamlink-grid-poc/docs/MULTIPARENT_PROTOCOL_FEASIBILITY.md` records two bounded start-position trials. For a recent known Frame ID, the first returned frame equaled the requested start, with no earlier frame delivered; one trial began in 19.05 ms. Both observations continued through the experiment's client-controlled time windows (367 and 483 frames), and neither contained an observed server-side range-completion marker. The experiment did not send a second unsolicited `0xCB30` and did not fuzz opaque fields.

The same report records two simultaneous sockets to the same server-issued endpoint delivering identical payload hashes for 1,363 frames. This doubled observed application transfer; Center provided no second distinct Parent. It is evidence for duplicate full-stream reception, not network striping.

The existing successful capture report `rea-research/SOOP_GRID_VIDEO_RESULT.md` records a 35-second live reception in which `0xCB2B` followed the empty `0xC742` notification and live-push media continued. This is evidence that the stop command does not stop all media in that tested flow, but it is not a measurement of the command's cache-stop byte savings.

## Capability matrix

| Capability | Result | Evidence / limit |
|---|---|---|
| Start Frame ID control | **VERIFIED** | Two bounded live trials in `MULTIPARENT_PROTOCOL_FEASIBILITY.md`; recent requested start was the first returned frame. |
| Explicit end Frame ID in `0xCB30` | **NOT OBSERVED in analyzed sender** | 16-byte body construction and sender signature at `0x1402f6050`; no distinct end input/store. Unknown extensions remain possible. |
| Frame-count field in `0xCB30` | **NOT OBSERVED in analyzed sender** | Same body/data-flow evidence; final 32-bit word comes from session state and is not proven to be a count. |
| Cache Stop control message | **VERIFIED in client construction** | `handleReqCacheStop` at `0x1402f5c70` emits header-only `0xCB2B`; binary strings name it `V2S_REQ_CACHE_STOP`. |
| Stop after a caller-selected Frame ID | **NOT VERIFIED** | No target-frame argument in the sender or shown caller condition. |
| Stop all server media while keeping the connection open | **NOT SUPPORTED by observed flow** | `0xCB2B` was followed by continued live media in the existing capture. No separate global stop opcode was demonstrated. |
| Finite cache batch/end marker | **NOT VERIFIED** | No end marker in the focused handler evidence; live trials continued until the client observation deadline. |
| Cache-only while connection remains idle after stop | **NOT VERIFIED** | Existing stop path proceeds into live-push flow in the tested capture. |
| Repeated bounded Cache Requests on one socket | **INCONCLUSIVE** | No second `0xC747` was observed; unsolicited repeated requests were deliberately not sent. |
| Server-side byte saving for disjoint frame ranges | **NOT VERIFIED** | No finite end/count request; two sockets duplicate the full observed stream. |

## Final verdict

```text
RANGE_END_CONTROL: NOT OBSERVED in analyzed 0xCB30 sender
FRAME_COUNT_CONTROL: NOT OBSERVED in analyzed 0xCB30 sender
SERVER_SIDE_STOP_CONTROL: CACHE_STOP_ONLY; no arbitrary-frame/global-media stop demonstrated
FINITE_BATCH_CONTROL: NOT VERIFIED
REUSABLE_PARENT_CONNECTION: VERIFIED for cache-to-live continuation; INCONCLUSIVE for successive bounded cache ranges
NETWORK_STRIPING_FEASIBILITY: NOT DEMONSTRATED
OVERALL_VERDICT: D — CLIENT_SIDE_STOP_ONLY for arbitrary end-bounded media retrieval
```

The `D` verdict is deliberately narrow: it describes the analyzed client path for retrieving a chosen finite media interval. A native Cache Stop control exists, but its demonstrated path ends or advances the cache phase rather than stopping all later media at an arbitrary frame. It does not prove protocol-wide nonexistence of undocumented commands or server-side extensions.

### Answers to the task's key questions

1. **Does `0xCB30` carry an end Frame ID or count?** No such field was found in its analyzed 16-byte body. The body includes a 64-bit session-derived value and a final opaque state word; their complete semantics are unresolved.
2. **Is there a separate stop command?** Yes: the analyzed client constructs a header-only `0xCB2B`, with an embedded `V2S_REQ_CACHE_STOP` label. It is a Cache Stop control, not a proven global media stop.
3. **Can the stop be issued at arbitrary frame M?** Not shown. The native call is parameterless and the retained caller's condition uses state flags, not a frame comparison.
4. **Does this enable traffic-saving frame striping?** Not on current evidence. The stop path does not provide disjoint range allocation, repeated finite cache batches are unverified, and the live Multi-Connection test duplicated the full stream.

## Evidence gaps and next bounded step

The local packet-construction check passed: the existing `parent_packet(0xCB2B, b"")` produces a 16-byte packet with command word `0xCB2B0202`, body length zero, and checksum word `0xC929`. A bounded real-server probe was then attempted, but the selected interpreter failed importing `Crypto.Cipher.AES` (`ModuleNotFoundError: Crypto`) before Bootstrap and before any request was sent. No suitable pre-existing isolated Python environment or cached PyCryptodome wheel was present, so no dependency was installed for this one-off report.

The remaining decisive runtime test—if later needed—is a single authorized, short Parent session using only the native-observed `0xCB2B` packet: measure all TCP application bytes and received `0xC748` bytes before and after it, with the same connection kept open. This would quantify whether Cache bytes stop and whether delivery transitions to Live; it would still not establish arbitrary Frame-M range control unless a caller-side target condition or another documented field is found. Do not vary the opaque `0xCB30` word or invent opcodes.

No production code changes, live network requests, Git commit, or push were made for this report. Pre-existing working-tree changes were preserved.
