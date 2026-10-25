package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"time"

	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/bootstrap"
	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/gateway"
	"github.com/integrated-recorder/source.soop/tests/grid-poc/internal/iss"
)

type playbackResult struct {
	RemuxPassed      bool            `json:"lossless_mp4_remux_passed"`
	PlayablePath     string          `json:"playable_path,omitempty"`
	VideoCodec       string          `json:"video_codec,omitempty"`
	AudioCodec       string          `json:"audio_codec,omitempty"`
	Width            int             `json:"width,omitempty"`
	Height           int             `json:"height,omitempty"`
	FrameRate        string          `json:"frame_rate,omitempty"`
	DurationSeconds  float64         `json:"duration_seconds,omitempty"`
	FFprobeAvailable bool            `json:"ffprobe_available"`
	FFprobePassed    bool            `json:"ffprobe_passed"`
	FFmpegAvailable  bool            `json:"ffmpeg_available"`
	DecodePassed     bool            `json:"full_decode_passed"`
	Failure          string          `json:"failure,omitempty"`
	ProbeDocument    json.RawMessage `json:"probe_document,omitempty"`
}

type runResult struct {
	StartedUTC               string                               `json:"started_utc"`
	Mode                     string                               `json:"mode"`
	OS                       string                               `json:"os"`
	Architecture             string                               `json:"architecture"`
	GoVersion                string                               `json:"go_version"`
	AccountLogin             string                               `json:"account_login"`
	AccountSession           string                               `json:"account_session"`
	AccountEvidence          *bootstrap.AccountLoginEvidence      `json:"account_login_evidence,omitempty"`
	SessionEvidence          *bootstrap.AccountSessionEvidence    `json:"account_session_evidence,omitempty"`
	LiveEvidence             *bootstrap.AuthenticatedLiveEvidence `json:"authenticated_live_api,omitempty"`
	NativeEvidence           *bootstrap.NativeBootstrapEvidence   `json:"native_bootstrap,omitempty"`
	PublicEvidence           *bootstrap.Evidence                  `json:"public_live_metadata,omitempty"`
	WatchEvidence            *bootstrap.WatchEvidence             `json:"watch_api,omitempty"`
	GatewayLogin             string                               `json:"gateway_login"`
	Gateway                  *gateway.SequenceResult              `json:"gateway,omitempty"`
	Certificate              *gateway.CertificateResult           `json:"gateway_certificate,omitempty"`
	CertificateShape         *iss.GatewayCertificateShape         `json:"certificate_shape,omitempty"`
	GridMediaCapture         string                               `json:"grid_media_capture"`
	Grid                     *iss.Result                          `json:"grid,omitempty"`
	PlaybackVerification     string                               `json:"playback_verification"`
	Playback                 *playbackResult                      `json:"playback,omitempty"`
	FileSHA256               map[string]string                    `json:"file_sha256,omitempty"`
	GoalStatus               string                               `json:"goal_status"`
	FailureStage             string                               `json:"failure_stage,omitempty"`
	SensitiveValuesPersisted bool                                 `json:"sensitive_values_persisted"`
}

func main() { os.Exit(run()) }

func run() int {
	mode := flag.String("mode", "native", "bootstrap path: native or watch")
	accountID := flag.String("account-id", "", "SOOP account ID (required for native mode)")
	broadcaster := flag.String("broadcaster", "", "exact broadcaster ID; no automatic target switching")
	broadcast := flag.String("broadcast", "", "exact live broadcast number")
	passwordProtected := flag.Bool("password-protected", false, "send SOOP_STREAM_PASSWORD only in the native Center JOIN")
	requireAdult := flag.Bool("require-adult", false, "require server-confirmed ACHK for the exact authenticated live")
	duration := flag.Duration("duration", 35*time.Second, "Grid capture duration (30s..60s)")
	outputDir := flag.String("output", "", "new private capture directory (required; must not exist)")
	resultFile := flag.String("result", "", "optional sanitized JSON result path (created mode 0600)")
	flag.Parse()

	result := runResult{
		StartedUTC:               time.Now().UTC().Format(time.RFC3339),
		Mode:                     *mode,
		OS:                       runtime.GOOS,
		Architecture:             runtime.GOARCH,
		GoVersion:                runtime.Version(),
		AccountLogin:             "NOT_REQUIRED",
		AccountSession:           "NOT_REQUIRED",
		GatewayLogin:             "NOT_ATTEMPTED",
		GridMediaCapture:         "NOT_ATTEMPTED",
		PlaybackVerification:     "NOT_ATTEMPTED",
		GoalStatus:               "FAILED",
		SensitiveValuesPersisted: false,
	}
	if *mode != "native" && *mode != "watch" || !safeIdentifier(*broadcaster) || !decimalIdentifier(*broadcast) || *duration < 30*time.Second || *duration > 60*time.Second || *outputDir == "" {
		result.FailureStage = "invalid_target_mode_duration_or_output"
		return finish(result, *resultFile, 2)
	}
	if *mode == "native" && !safeIdentifier(*accountID) || *mode == "watch" && (*accountID != "" || *passwordProtected || *requireAdult) {
		result.FailureStage = "invalid_authentication_mode_options"
		return finish(result, *resultFile, 2)
	}
	if *passwordProtected && *mode != "native" {
		result.FailureStage = "stream_password_requires_native_mode"
		return finish(result, *resultFile, 2)
	}
	if pathExists(*outputDir) || (*resultFile != "" && pathExists(*resultFile)) {
		result.FailureStage = "output_path_already_exists"
		return finish(result, *resultFile, 2)
	}
	accountPassword := os.Getenv("SOOP_ACCOUNT_PASSWORD")
	_ = os.Unsetenv("SOOP_ACCOUNT_PASSWORD")
	defer func() { accountPassword = "" }()
	streamPassword := os.Getenv("SOOP_STREAM_PASSWORD")
	_ = os.Unsetenv("SOOP_STREAM_PASSWORD")
	defer func() { streamPassword = "" }()
	if *passwordProtected && !bootstrap.ValidNativeStreamPassword(streamPassword) {
		result.FailureStage = "stream_password_environment_missing_or_unsupported"
		return finish(result, *resultFile, 2)
	}
	if !*passwordProtected {
		streamPassword = ""
	}

	ctx, cancel := context.WithTimeout(context.Background(), *duration+50*time.Second)
	defer cancel()
	client := bootstrap.NewAccountHTTPClient()
	var session bootstrap.Session
	if *mode == "native" {
		if accountPassword == "" {
			result.FailureStage = "account_password_environment_missing"
			return finish(result, *resultFile, 2)
		}
		login, err := bootstrap.LoginAccount(ctx, client, *accountID, accountPassword)
		accountPassword = ""
		result.AccountEvidence = &login
		if err != nil {
			result.AccountLogin = "FAILED"
			result.FailureStage = "account_login_rejected_or_incomplete"
			return finish(result, *resultFile, 1)
		}
		result.AccountLogin = "SUCCESS"
		check, err := bootstrap.CheckAccountSession(ctx, client, *accountID, *broadcaster, *broadcast)
		result.SessionEvidence = &check
		if err != nil {
			result.AccountSession = "FAILED"
			result.FailureStage = "account_session_not_confirmed"
			return finish(result, *resultFile, 1)
		}
		result.AccountSession = "SUCCESS"

		var live bootstrap.AuthenticatedLiveEvidence
		var errLive error
		if *passwordProtected {
			session, live, errLive = bootstrap.DiscoverPasswordBroadcast(ctx, client, *broadcaster, *broadcast, streamPassword)
			if errLive == nil {
				defer session.Scrub()
			}
		} else {
			session, live, errLive = bootstrap.DiscoverAuthenticatedBroadcast(ctx, client, *broadcaster, *broadcast)
			if errLive == nil {
				defer session.Scrub()
			}
		}
		result.LiveEvidence = &live
		if errLive != nil {
			result.FailureStage = "authenticated_live_metadata_denied_or_incomplete"
			return finish(result, *resultFile, 1)
		}
		if stage := protectionModeFailure(*passwordProtected, live.StreamPasswordRequired); stage != "" {
			result.FailureStage = stage
			return finish(result, *resultFile, 1)
		}
		native, err := bootstrap.ValidateDesktopNativeSessionFor(&session, *requireAdult)
		result.NativeEvidence = &native
		if err != nil {
			result.FailureStage = "native_grid_session_fields_incomplete"
			return finish(result, *resultFile, 1)
		}
	} else {
		publicClient := bootstrap.NewHTTPClient()
		var evidence bootstrap.Evidence
		session, evidence, err := bootstrap.DiscoverSelected(ctx, publicClient, time.Now(), *broadcaster, *broadcast)
		result.PublicEvidence = &evidence
		if err != nil {
			result.FailureStage = "public_live_metadata_unavailable_or_restricted"
			return finish(result, *resultFile, 1)
		}
		defer session.Scrub()
		watch, watchErr := bootstrap.EnrichWithWatch(ctx, &session)
		result.WatchEvidence = &watch
		if watchErr != nil || !watch.FanTicketPresent || !watch.GatewayEndpointPresent || !watch.RelayEndpointPresent {
			result.FailureStage = "public_watch_ticket_or_grid_endpoint_unavailable"
			return finish(result, *resultFile, 1)
		}
	}

	broadcastNumber, err := strconv.ParseUint(session.BroadcastNo, 10, 32)
	if err != nil || broadcastNumber == 0 {
		result.FailureStage = "broadcast_number_invalid"
		return finish(result, *resultFile, 1)
	}
	guid, err := gateway.NewGUID()
	if err != nil {
		result.FailureStage = "gateway_guid_generation_failed"
		return finish(result, *resultFile, 1)
	}
	fanTicket, loginCookie, cookieSource := session.FanTicket, session.AU, ""
	if *mode == "native" {
		fanTicket, loginCookie = session.GatewayFTK, session.GatewayCookie
		cookieSource = session.GatewayCookieSource
	}
	if len(fanTicket) == 0 || len(fanTicket) > 4096 || loginCookie == "" {
		result.FailureStage = "gateway_session_credentials_missing"
		return finish(result, *resultFile, 1)
	}
	fanTicketBytes := []byte(fanTicket)
	defer clear(fanTicketBytes)
	result.GridMediaCapture = "FAILED"
	if err := os.MkdirAll(filepath.Dir(*outputDir), 0700); err != nil {
		result.FailureStage = "capture_parent_directory_create_failed"
		return finish(result, *resultFile, 1)
	}
	timestamp := uint32(time.Now().Unix())
	login, certificate := gateway.LoginAndRequestCertificate(ctx, session.GatewayAddr,
		gateway.NativeLoginFields{GUID: guid, Cookie: loginCookie, CookieSource: cookieSource, BJID: session.BJID},
		timestamp, gateway.InitialService6Counter(timestamp),
		gateway.CertTicketFields{SessionID: uint32(broadcastNumber), TicketType: 1, BJID: session.BJID, Category: session.Category, FanTicket: fanTicketBytes},
		func(body []byte) {
			shape := iss.InspectGatewayCertificate(body)
			result.CertificateShape = &shape
			credentials, parseErr := iss.ParseGatewayCredentials(body, guid, "log\x11\x06&\x06os\x06=\x06Linux\x12", uint32(broadcastNumber))
			if parseErr != nil {
				result.FailureStage = "issued_gateway_certificate_invalid"
				return
			}
			defer credentials.Close()
			if *passwordProtected {
				if credentials.SetStreamPassword(streamPassword) != nil {
					result.FailureStage = "stream_password_encoding_rejected"
					return
				}
			}
			capture := iss.Receive(ctx, session.CenterAddr, credentials, iss.ReceiveOptions{OutputDir: *outputDir, Duration: *duration, MaxWireBytes: 512 << 20})
			result.Grid = &capture
			if capture.Parent != nil && capture.Parent.Capture != nil && capture.Parent.Capture.PlayableVideoFrames > 0 && (capture.Parent.Outcome == "grid_media_saved" || capture.Parent.Outcome == "grid_media_saved_size_limit") {
				result.GridMediaCapture = "SUCCESS"
			} else {
				result.GridMediaCapture = "FAILED"
			}
		})
	result.Gateway = &login
	result.Certificate = &certificate
	if login.GatewayLoginAccepted {
		result.GatewayLogin = "SUCCESS"
	} else {
		result.GatewayLogin = "FAILED"
	}
	if result.Grid == nil && result.FailureStage == "" {
		result.FailureStage = "gateway_did_not_issue_grid_certificate"
	}
	if result.GridMediaCapture != "SUCCESS" || result.Grid == nil || result.Grid.Parent == nil || result.Grid.Parent.Capture == nil {
		return finish(result, *resultFile, 1)
	}
	result.FileSHA256 = hashCaptureFiles(*outputDir)
	playback := verifyPlayback(ctx, filepath.Join(*outputDir, "recording.ts"))
	result.Playback = &playback
	if playback.RemuxPassed && playback.FFprobePassed && playback.DecodePassed {
		result.PlaybackVerification = "PASS"
		result.GoalStatus = "SUCCESS"
	} else if !playback.FFmpegAvailable || !playback.FFprobeAvailable {
		result.PlaybackVerification = "NOT_ATTEMPTED"
		result.GoalStatus = "PARTIAL"
		result.FailureStage = "ffmpeg_or_ffprobe_unavailable"
	} else {
		result.PlaybackVerification = "FAIL"
		result.GoalStatus = "FAILED"
		result.FailureStage = "full_decode_or_remux_failed"
	}
	result.FileSHA256 = hashCaptureFiles(*outputDir)
	return finish(result, *resultFile, 0)
}

func verifyPlayback(ctx context.Context, tsPath string) playbackResult {
	var result playbackResult
	ffmpeg, err := exec.LookPath("ffmpeg")
	if err != nil {
		result.Failure = "ffmpeg_not_installed"
		return result
	}
	result.FFmpegAvailable = true
	mp4 := filepath.Join(filepath.Dir(tsPath), "playable.mp4")
	cmd := exec.CommandContext(ctx, ffmpeg, "-nostdin", "-n", "-v", "error", "-i", tsPath, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", mp4)
	output, err := cmd.CombinedOutput()
	clear(output)
	if err != nil {
		result.Failure = "lossless_mp4_remux_failed"
		return result
	}
	_ = os.Chmod(mp4, 0600)
	result.RemuxPassed, result.PlayablePath = true, filepath.Base(mp4)
	ffprobe, err := exec.LookPath("ffprobe")
	if err != nil {
		result.Failure = "ffprobe_not_installed"
		return result
	}
	result.FFprobeAvailable = true
	probe := exec.CommandContext(ctx, ffprobe, "-v", "error", "-show_entries", "stream=codec_name,width,height,avg_frame_rate", "-show_entries", "format=duration", "-of", "json", mp4)
	stdout, err := probe.Output()
	if err != nil {
		result.Failure = "ffprobe_failed"
		return result
	}
	var decoded struct {
		Streams []struct {
			Codec  string `json:"codec_name"`
			Width  int    `json:"width"`
			Height int    `json:"height"`
			FPS    string `json:"avg_frame_rate"`
		} `json:"streams"`
		Format struct {
			Duration string `json:"duration"`
		} `json:"format"`
	}
	if json.Unmarshal(stdout, &decoded) != nil {
		clear(stdout)
		result.Failure = "ffprobe_json_invalid"
		return result
	}
	result.ProbeDocument = append(json.RawMessage(nil), stdout...)
	clear(stdout)
	for _, stream := range decoded.Streams {
		if stream.Width > 0 && stream.Height > 0 {
			result.VideoCodec, result.Width, result.Height, result.FrameRate = stream.Codec, stream.Width, stream.Height, stream.FPS
		} else if stream.Codec != "" {
			result.AudioCodec = stream.Codec
		}
	}
	result.DurationSeconds, _ = strconv.ParseFloat(decoded.Format.Duration, 64)
	result.FFprobePassed = result.Width > 0 && result.Height > 0 && result.VideoCodec != ""
	if !result.FFprobePassed {
		result.Failure = "ffprobe_missing_media_stream"
		return result
	}
	decode := exec.CommandContext(ctx, ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", mp4, "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-")
	output, err = decode.CombinedOutput()
	clear(output)
	if err != nil {
		result.Failure = "ffmpeg_full_decode_failed"
		return result
	}
	result.DecodePassed = true
	return result
}

func hashCaptureFiles(directory string) map[string]string {
	files := []string{"grid-wire.bin", "grid-payload.bin", "recording.ts", "video.h264", "audio.aac", "playable.mp4", "frames.jsonl"}
	out := make(map[string]string, len(files))
	for _, name := range files {
		f, err := os.Open(filepath.Join(directory, name))
		if err != nil {
			continue
		}
		h := sha256.New()
		_, copyErr := io.Copy(h, f)
		_ = f.Close()
		if copyErr == nil {
			out[name] = hex.EncodeToString(h.Sum(nil))
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func finish(result runResult, resultPath string, exitCode int) int {
	if resultPath != "" {
		if err := os.MkdirAll(filepath.Dir(resultPath), 0700); err == nil {
			encoded, marshalErr := json.MarshalIndent(result, "", "  ")
			if marshalErr == nil {
				encoded = append(encoded, '\n')
				if file, err := os.OpenFile(resultPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600); err == nil {
					_, _ = file.Write(encoded)
					_ = file.Close()
				} else {
					result.FailureStage = "sanitized_result_file_write_failed"
				}
				clear(encoded)
			}
		}
	}
	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return exitCode
	}
	encoded = append(encoded, '\n')
	_, _ = os.Stdout.Write(encoded)
	clear(encoded)
	return exitCode
}

func safeIdentifier(value string) bool {
	if value == "" || len(value) > 64 {
		return false
	}
	for _, r := range value {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_' || r == '-') {
			return false
		}
	}
	return true
}

func decimalIdentifier(value string) bool {
	if value == "" || len(value) > 20 {
		return false
	}
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func pathExists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func protectionModeFailure(passwordProtected, serverRequiresPassword bool) string {
	switch {
	case passwordProtected && !serverRequiresPassword:
		return "target_not_password_protected"
	case !passwordProtected && serverRequiresPassword:
		return "target_requires_password"
	default:
		return ""
	}
}
