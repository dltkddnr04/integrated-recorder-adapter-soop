package bootstrap

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	watchInfoURL    = "https://api.m.sooplive.co.kr/broad/a/watch"
	accountWatchURL = "https://api.m.sooplive.com/broad/a/watch"
	playHost        = "play.sooplive.com"
	watchUA         = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

// WatchEvidence contains only non-secret facts from SOOP's regular public
// viewer bootstrap. Session values, cookies, IPs, titles, and tickets are never
// serialized into this type.
type WatchEvidence struct {
	PageStatus             int      `json:"public_page_http,omitempty"`
	AUCookiePresent        bool     `json:"anonymous_au_cookie_present"`
	AU3rdCookiePresent     bool     `json:"anonymous_au3rd_cookie_present"`
	AUCookieSource         string   `json:"anonymous_au_cookie_source,omitempty"`
	APIStatus              int      `json:"watch_api_http,omitempty"`
	JSONValid              bool     `json:"watch_api_json_valid"`
	ResponseResultKnown    bool     `json:"watch_api_result_code_known"`
	ResponseResult         int      `json:"watch_api_result_code,omitempty"`
	AccessReason           string   `json:"watch_access_reason,omitempty"`
	DataCode               *int     `json:"watch_data_code,omitempty"`
	RequestHost            string   `json:"request_host,omitempty"`
	AccountCookieJarUsed   bool     `json:"account_cookie_jar_used"`
	ConfirmAdult           bool     `json:"confirm_adult_from_server_achk"`
	DataPresent            bool     `json:"watch_data_present"`
	DataKeys               []string `json:"watch_data_keys,omitempty"`
	GatewaySetupFieldKeys  []string `json:"gateway_candidate_setup_field_keys_present,omitempty"`
	FanTicketPresent       bool     `json:"watch_fan_ticket_present"`
	RelayEndpointPresent   bool     `json:"relay_endpoint_present"`
	GatewayEndpointPresent bool     `json:"gateway_endpoint_present"`
	P2PAvailable           bool     `json:"p2p_available,omitempty"`
	P2PFieldPresent        bool     `json:"p2p_field_present"`
	Resolution             string   `json:"reported_resolution,omitempty"`
	Codec                  string   `json:"reported_video_codec,omitempty"`
	Outcome                string   `json:"outcome"`
}

type watchData struct {
	FanTicket   string          `json:"fan_ticket"`
	GateWayIP   string          `json:"gateway_ip"`
	GateWayPort json.Number     `json:"gateway_port"`
	RelayIP     string          `json:"relay_ip"`
	RelayPort   json.Number     `json:"relay_port"`
	P2P         json.RawMessage `json:"is_p2p"`
	Resolution  string          `json:"resolution"`
	Codec       string          `json:"supported_vcodec"`
}

// EnrichWithWatch obtains the first-party anonymous _au cookie from the public
// play page and then calls the endpoint used by the SOOP web player and ytv1.
// It overrides the older type=live gateway/center coordinates when valid.
// Other transport-specific fields are ignored.
func EnrichWithWatch(ctx context.Context, session *Session) (WatchEvidence, error) {
	return EnrichWithWatchClient(ctx, nil, session)
}

// EnrichWithWatchClient follows the ordinary first-party play-page and Watch
// API flow while preserving a caller's in-memory CookieJar. Cookie scoping is
// left to net/http; only the previously observed anonymous _au field is
// explicitly carried to the separate .co.kr Watch API host.
func EnrichWithWatchClient(ctx context.Context, client *http.Client, session *Session) (WatchEvidence, error) {
	return enrichWithWatch(ctx, client, session, false)
}

// EnrichAuthenticatedWatch mirrors the current official player: its .com
// endpoint receives the account's naturally scoped cookies, and confirm_adult
// comes only from the authenticated live API's ACHK value.
func EnrichAuthenticatedWatch(ctx context.Context, client *http.Client, session *Session) (WatchEvidence, error) {
	if client == nil || client.Jar == nil {
		return WatchEvidence{Outcome: "missing_account_cookie_jar"}, errors.New("account cookie jar required")
	}
	return enrichWithWatch(ctx, client, session, true)
}

func enrichWithWatch(ctx context.Context, client *http.Client, session *Session, authenticated bool) (WatchEvidence, error) {
	e := WatchEvidence{Outcome: "not_attempted"}
	if session == nil || !safeLoginID(session.LoginID) || !decimalID(session.BroadcastNo) {
		e.Outcome = "invalid_public_broadcast_identifiers"
		return e, errors.New("invalid public broadcast identifiers")
	}
	if client == nil {
		client = &http.Client{}
	}
	// Bound the total request duration and prohibit all redirects while keeping
	// the supplied transport and cookie jar intact.
	clientCopy := *client
	if clientCopy.Timeout == 0 || clientCopy.Timeout > 8*time.Second {
		clientCopy.Timeout = 8 * time.Second
	}
	clientCopy.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	client = &clientCopy

	var au, au3rd, auSource string
	var pageStatus int
	var pageErr error
	if authenticated && client.Jar != nil {
		playURL, _ := url.Parse(accountPageOrigin + "/")
		for _, c := range client.Jar.Cookies(playURL) {
			if len(c.Value) > 512 {
				continue
			}
			if c.Name == "_au" {
				au = c.Value
			}
			if c.Name == "_au3rd" {
				au3rd = c.Value
			}
		}
		if au != "" {
			auSource = "authenticated_first_party_cookie_jar"
		}
	}
	if au == "" {
		au, au3rd, pageStatus, auSource, pageErr = fetchAnonymousIDs(ctx, client, session.LoginID)
	}
	e.PageStatus = pageStatus
	e.AUCookiePresent, e.AU3rdCookiePresent = au != "", au3rd != ""
	e.AUCookieSource = auSource
	if au != "" {
		session.AU = au
	}
	if au3rd != "" {
		session.AU3rd = au3rd
	}
	// The public player normally sets _au before the watch call. If no scoped
	// _au exists in the page response or first-party jar, call the API once
	// without a Cookie header; never substitute another account cookie.
	_ = pageErr

	form := url.Values{}
	form.Set("bj_id", session.LoginID)
	form.Set("broad_no", session.BroadcastNo)
	form.Set("player_type", "html5")
	endpoint := watchInfoURL
	if authenticated {
		endpoint = accountWatchURL + "?bjid=" + url.QueryEscape(session.LoginID)
		form.Set("agent", "web")
		form.Set("mode", "live")
		form.Set("confirm_adult", strconv.FormatBool(session.AdultConfirmed))
		e.AccountCookieJarUsed, e.ConfirmAdult = true, session.AdultConfirmed
	}
	u, _ := url.Parse(endpoint)
	e.RequestHost = u.Hostname()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(form.Encode()))
	if err != nil {
		scrubString(&au)
		scrubString(&au3rd)
		e.Outcome = "watch_request_construction_error"
		return e, errors.New("watch request construction failed")
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("User-Agent", watchUA)
	if authenticated {
		req.Header.Set("Origin", accountPageOrigin)
		req.Header.Set("Referer", accountPageOrigin+"/"+session.LoginID+"/"+session.BroadcastNo)
	}
	if au != "" && !authenticated {
		req.Header.Set("Cookie", "_au="+au)
	}
	resp, err := client.Do(req)
	scrubString(&au)
	scrubString(&au3rd)
	if err != nil {
		e.Outcome = "watch_api_network_error"
		return e, errors.New("watch API request failed")
	}
	e.APIStatus = resp.StatusCode
	if resp.Request != nil && resp.Request.URL.Hostname() != u.Hostname() {
		resp.Body.Close()
		e.Outcome = "watch_api_destination_changed"
		return e, errors.New("watch API destination changed")
	}
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, (2<<20)+1))
	resp.Body.Close()
	if readErr != nil || len(body) > 2<<20 {
		clear(body)
		e.Outcome = "watch_api_body_limit_or_read_error"
		return e, errors.New("watch API response could not be read within bounds")
	}
	defer clear(body)
	if resp.StatusCode != http.StatusOK {
		e.Outcome = "watch_api_http_rejected"
		return e, fmt.Errorf("watch API returned HTTP %d", resp.StatusCode)
	}
	var root struct {
		Result json.RawMessage `json:"result"`
		Data   json.RawMessage `json:"data"`
	}
	if json.Unmarshal(body, &root) != nil || len(root.Data) == 0 {
		e.Outcome = "watch_api_invalid_json_or_missing_data"
		return e, errors.New("watch API JSON did not contain data")
	}
	if len(root.Result) != 0 {
		e.ResponseResult, e.ResponseResultKnown = accountResultCode(root.Result)
	}
	var data watchData
	if json.Unmarshal(root.Data, &data) != nil {
		e.Outcome = "watch_api_data_decode_error"
		return e, errors.New("watch API data could not be decoded")
	}
	var rawData map[string]json.RawMessage
	_ = json.Unmarshal(root.Data, &rawData)
	if code, ok := accountResultCode(rawData["code"]); ok {
		e.DataCode = &code
	}
	e.AccessReason = classifyWatchAccessReason(rawData)
	if e.AccessReason == "" && e.ResponseResultKnown && e.ResponseResult != 1 {
		e.AccessReason = "watch_api_rejected"
	}
	for key := range rawData {
		if len(e.DataKeys) < 64 && safeEvidenceKey(key) {
			e.DataKeys = append(e.DataKeys, key)
		}
		switch key {
		case "guid", "bjid", "joinlog", "addinfo", "category", "cookie", "fanticket", "broadno", "cli_type":
			e.GatewaySetupFieldKeys = append(e.GatewaySetupFieldKeys, key)
		}
	}
	sort.Strings(e.DataKeys)
	sort.Strings(e.GatewaySetupFieldKeys)
	e.JSONValid, e.DataPresent = true, true
	if authenticated && (!e.ResponseResultKnown || e.ResponseResult != 1) {
		e.Outcome = "watch_api_application_rejected"
		return e, errors.New("Watch API did not authorize this session")
	}
	e.FanTicketPresent = data.FanTicket != ""
	e.P2PFieldPresent = len(data.P2P) != 0 && !bytes.Equal(bytes.TrimSpace(data.P2P), []byte("null"))
	e.P2PAvailable = jsonBool(data.P2P)
	e.Resolution = safeResolution(data.Resolution)
	e.Codec = safeCodec(data.Codec)

	center, centerOK := publicEndpoint(data.RelayIP, data.RelayPort.String())
	gate, gateOK := publicEndpoint(data.GateWayIP, data.GateWayPort.String())
	e.RelayEndpointPresent = centerOK
	e.GatewayEndpointPresent = gateOK
	if centerOK {
		session.CenterAddr = center
	}
	if gateOK {
		session.GatewayAddr = gate
	}
	if data.FanTicket != "" {
		session.FanTicket = data.FanTicket
	}
	if e.FanTicketPresent && centerOK && gateOK {
		e.Outcome = "watch_metadata_ready"
	} else {
		e.Outcome = "watch_metadata_incomplete"
	}
	return e, nil
}

// classifyWatchAccessReason inspects only bounded machine-readable error
// fields and returns a fixed category. The raw message, nick, URL, and SKU are
// never retained or logged.
func classifyWatchAccessReason(fields map[string]json.RawMessage) string {
	if len(fields) == 0 {
		return ""
	}
	if code, ok := accountResultCode(fields["code"]); ok {
		switch code {
		case -3001:
			return "broadcast_offline"
		case -3002, -3003, -3004:
			return "adult_gate_rejected"
		case -3014, -3015:
			return "region_restricted"
		case -3034, -3035, -3036:
			return "membership_required"
		case -3042:
			return "gdpr_p2p_consent_required"
		}
	}
	readText := func(key string) string {
		raw := fields[key]
		if len(raw) == 0 || len(raw) > 1024 {
			return ""
		}
		var text string
		_ = json.Unmarshal(raw, &text)
		return text
	}
	code := readText("code")
	message := readText("message")
	if message == "" {
		message = readText("error")
	}
	text := strings.ToLower(code + " " + message)
	switch {
	case strings.Contains(text, "adult"), strings.Contains(text, "age"), strings.Contains(text, "성인"), strings.Contains(text, "연령"), strings.Contains(text, "본인인증"):
		return "adult_verification_required"
	case strings.Contains(text, "login"), strings.Contains(text, "auth"), strings.Contains(text, "로그인"):
		return "login_required"
	case strings.Contains(text, "subscription"), strings.Contains(text, "member_only"), strings.Contains(text, "구독"), strings.Contains(text, "유료"):
		return "membership_required"
	case strings.Contains(text, "password"), strings.Contains(text, "비밀번호"):
		return "broadcast_password_required"
	case strings.Contains(text, "geo"), strings.Contains(text, "region"), strings.Contains(text, "국가 제한"):
		return "region_restricted"
	case strings.Contains(text, "denied"), strings.Contains(text, "permission"), strings.Contains(text, "권한"):
		return "access_denied"
	default:
		return ""
	}
}

func fetchAnonymousIDs(ctx context.Context, client *http.Client, loginID string) (au, au3rd string, status int, source string, err error) {
	target := "https://" + playHost + "/" + url.PathEscape(loginID)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return "", "", 0, "", err
	}
	req.Header.Set("User-Agent", watchUA)
	resp, err := client.Do(req)
	if err != nil {
		return "", "", 0, "", err
	}
	status = resp.StatusCode
	if resp.Request != nil && resp.Request.URL.Hostname() != playHost {
		resp.Body.Close()
		return "", "", status, "", errors.New("play page destination changed")
	}
	_, bodyErr := io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	resp.Body.Close()
	if bodyErr != nil || status < 200 || status >= 300 {
		return "", "", status, "", errors.New("public play page did not return a usable response")
	}
	for _, cookie := range resp.Cookies() {
		if cookie.Value == "" || len(cookie.Value) > 512 {
			continue
		}
		switch cookie.Name {
		case "_au":
			au = cookie.Value
			source = "play_page_set_cookie"
		case "_au3rd":
			au3rd = cookie.Value
		}
	}
	// Some authenticated first-party sessions already carry the platform's _au
	// cookie in the CookieJar even when the play-page response does not issue a
	// new Set-Cookie. Forward only this named token to the documented first-party
	// Watch API; never copy the jar's other .sooplive.com cookies to .co.kr.
	if client.Jar != nil && (au == "" || au3rd == "") {
		playURL, _ := url.Parse("https://" + playHost + "/")
		for _, cookie := range client.Jar.Cookies(playURL) {
			if cookie.Value == "" || len(cookie.Value) > 512 {
				continue
			}
			switch cookie.Name {
			case "_au":
				if au == "" {
					au = cookie.Value
					source = "play_first_party_cookie_jar"
				}
			case "_au3rd":
				if au3rd == "" {
					au3rd = cookie.Value
				}
			}
		}
	}
	return au, au3rd, status, source, nil
}

func safeEvidenceKey(key string) bool {
	if key == "" || len(key) > 64 {
		return false
	}
	for _, r := range key {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_' || r == '-') {
			return false
		}
	}
	return true
}

func safeLoginID(value string) bool {
	if value == "" || len(value) > 64 {
		return false
	}
	for _, c := range value {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-') {
			return false
		}
	}
	return true
}

func decimalID(value string) bool {
	if value == "" || len(value) > 20 {
		return false
	}
	for _, c := range value {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

func jsonBool(raw json.RawMessage) bool {
	var b bool
	if json.Unmarshal(raw, &b) == nil {
		return b
	}
	var n json.Number
	if json.NewDecoder(bytes.NewReader(raw)).Decode(&n) == nil {
		return n.String() != "0"
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return strings.EqualFold(s, "true") || s == "1" || strings.EqualFold(s, "yes")
	}
	return false
}

func safeResolution(value string) string {
	parts := strings.Split(strings.ToLower(strings.TrimSpace(value)), "x")
	if len(parts) != 2 {
		return ""
	}
	w, errW := strconv.Atoi(parts[0])
	h, errH := strconv.Atoi(parts[1])
	if errW != nil || errH != nil || w < 1 || w > 16384 || h < 1 || h > 16384 {
		return ""
	}
	return fmt.Sprintf("%dx%d", w, h)
}

func safeCodec(value string) string {
	s := strings.ToLower(strings.TrimSpace(value))
	switch s {
	case "h264", "avc", "avc1", "h265", "hevc", "hev1", "av1":
		return s
	default:
		return ""
	}
}

func scrubString(s *string) {
	if s != nil {
		*s = ""
	}
}
