package bootstrap

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	soopLoginActionURL = "https://login.sooplive.com/app/LoginAction.php"
	accountPlayerURL   = "https://live.sooplive.com/afreeca/player_live_api.php"
	accountUA          = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
	accountPageOrigin  = "https://play.sooplive.com"
)

// AccountLoginEvidence contains only non-secret outcomes. Cookie values and
// the account identifier are deliberately excluded.
type AccountLoginEvidence struct {
	HTTPStatus              int                   `json:"login_http_status,omitempty"`
	JSONValid               bool                  `json:"login_response_json_valid"`
	Result                  int                   `json:"login_result,omitempty"`
	FirstPartySessionCookie bool                  `json:"first_party_session_cookie_present"`
	Outcome                 string                `json:"outcome"`
	CookieScopes            []CookieScopeEvidence `json:"issued_cookie_scopes,omitempty"`
}

// CookieScopeEvidence contains attributes only; cookie values never enter it.
type CookieScopeEvidence struct {
	Name                  string `json:"name"`
	Domain                string `json:"domain"`
	Path                  string `json:"path"`
	Secure                bool   `json:"secure"`
	HTTPOnly              bool   `json:"http_only"`
	AppliesToLive         bool   `json:"applies_to_live_api"`
	AppliesToCurrentWatch bool   `json:"applies_to_current_com_watch"`
	AppliesToLegacyWatch  bool   `json:"applies_to_legacy_cokr_watch"`
}

// AuthenticatedLiveEvidence records whether the account's cookie jar was
// applied to SOOP's first-party live API and whether that API returned the
// requested broadcast. It never retains the response body or field values.
type AuthenticatedLiveEvidence struct {
	HTTPStatus                 int    `json:"live_api_http_status,omitempty"`
	JSONValid                  bool   `json:"live_api_json_valid"`
	Result                     int    `json:"live_api_result,omitempty"`
	SessionCookieAvailable     bool   `json:"session_cookie_available_for_live_api"`
	ChannelPresent             bool   `json:"channel_object_present"`
	BroadcastMatches           bool   `json:"requested_broadcast_matches"`
	BroadcasterMatches         bool   `json:"requested_broadcaster_matches"`
	PasswordStateKnown         bool   `json:"stream_password_state_known"`
	StreamPasswordRequired     bool   `json:"stream_password_required"`
	AuthenticatedTargetAllowed bool   `json:"authenticated_target_metadata_allowed"`
	GatewayEndpointPresent     bool   `json:"gateway_endpoint_present"`
	CenterEndpointPresent      bool   `json:"center_endpoint_present"`
	FanTicketFieldPresent      bool   `json:"gateway_ticket_field_present"`
	AdultCheckKnown            bool   `json:"server_adult_check_known"`
	AdultCheckConfirmed        bool   `json:"server_adult_check_confirmed"`
	Outcome                    string `json:"outcome"`
}

// NewAccountHTTPClient creates an in-memory first-party cookie jar. Redirects
// are not followed, so credentials and session cookies cannot be forwarded to
// a different host. The jar is never persisted.
func NewAccountHTTPClient() *http.Client {
	jar, _ := cookiejar.New(nil)
	return &http.Client{
		Timeout: 12 * time.Second,
		Jar:     jar,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

// LoginAccount submits credentials only to SOOP's first-party LoginAction
// endpoint using the same form fields as the maintained Streamlink plugin.
// The password must never be logged, serialized, or persisted by the caller.
func LoginAccount(ctx context.Context, client *http.Client, accountID, password string) (AccountLoginEvidence, error) {
	e := AccountLoginEvidence{Outcome: "not_attempted"}
	if client == nil || client.Jar == nil || !safeLoginID(accountID) || password == "" || len(password) > 1024 || strings.ContainsRune(password, '\x00') {
		e.Outcome = "invalid_login_input_or_missing_cookie_jar"
		return e, errors.New("invalid account login input")
	}
	return loginAccountAt(ctx, client, accountID, password, soopLoginActionURL, accountPageOrigin+"/", "login.sooplive.com")
}

func loginAccountAt(ctx context.Context, client *http.Client, accountID, password, endpoint, cookieProbe, expectedHost string) (AccountLoginEvidence, error) {
	e := AccountLoginEvidence{Outcome: "request_construction_failed"}
	endpointURL, endpointErr := url.Parse(endpoint)
	if endpointErr != nil || endpointURL.Scheme != "https" || !strings.EqualFold(endpointURL.Hostname(), expectedHost) {
		e.Outcome = "login_destination_invalid"
		return e, errors.New("SOOP login endpoint is not the expected first-party host")
	}
	form := url.Values{
		"szWork":        {"login"},
		"szType":        {"json"},
		"szUid":         {accountID},
		"szPassword":    {password},
		"isSaveId":      {"true"},
		"isSavePw":      {"false"},
		"isSaveJoin":    {"false"},
		"isLoginRetain": {"Y"},
	}
	encoded := []byte(form.Encode())
	defer clear(encoded)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(encoded))
	if err != nil {
		return e, errors.New("could not construct SOOP login request")
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Origin", accountPageOrigin)
	req.Header.Set("Referer", cookieProbe)
	req.Header.Set("User-Agent", accountUA)
	resp, err := client.Do(req)
	if err != nil {
		e.Outcome = "login_network_error"
		return e, errors.New("SOOP login request failed")
	}
	e.HTTPStatus = resp.StatusCode
	e.CookieScopes = accountCookieScopes(client, resp.Cookies())
	if resp.Request != nil && !strings.EqualFold(resp.Request.URL.Hostname(), expectedHost) {
		resp.Body.Close()
		e.Outcome = "login_destination_changed"
		return e, errors.New("SOOP login destination changed")
	}
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	resp.Body.Close()
	if readErr != nil || len(body) > 1<<20 {
		clear(body)
		e.Outcome = "login_response_limit_or_read_error"
		return e, errors.New("SOOP login response could not be read within bounds")
	}
	defer clear(body)
	if resp.StatusCode != http.StatusOK {
		e.Outcome = "login_http_rejected"
		return e, errors.New("SOOP login endpoint rejected the request")
	}
	var result struct {
		Result json.RawMessage `json:"RESULT"`
	}
	if json.Unmarshal(body, &result) != nil || len(result.Result) == 0 {
		e.Outcome = "login_non_json_or_challenge_response"
		return e, errors.New("SOOP login response was not a recognized JSON result")
	}
	e.JSONValid = true
	e.Result = integer(result.Result)
	if e.Result != 1 {
		e.Outcome = "login_rejected_or_additional_verification_required"
		return e, errors.New("SOOP did not accept the account login")
	}
	probeURL, parseErr := url.Parse(cookieProbe)
	if parseErr == nil && client.Jar != nil {
		e.FirstPartySessionCookie = len(client.Jar.Cookies(probeURL)) > 0
	}
	if !e.FirstPartySessionCookie {
		e.Outcome = "login_result_ok_but_no_play_session_cookie"
		return e, errors.New("SOOP login result was positive but no usable first-party session cookie was issued")
	}
	e.Outcome = "login_result_ok_session_cookie_present"
	return e, nil
}

// DiscoverAuthenticatedBroadcast queries the exact supplied broadcast using
// the first-party account cookie jar. It does not enumerate or switch targets.
func DiscoverAuthenticatedBroadcast(ctx context.Context, client *http.Client, broadcaster, broadcast string) (Session, AuthenticatedLiveEvidence, error) {
	e := AuthenticatedLiveEvidence{Outcome: "not_attempted"}
	if client == nil || client.Jar == nil || !safeLoginID(broadcaster) || !decimalID(broadcast) {
		e.Outcome = "invalid_target_or_missing_cookie_jar"
		return Session{}, e, errors.New("invalid authenticated broadcast query input")
	}
	return discoverAuthenticatedBroadcastAt(ctx, client, broadcaster, broadcast, accountPlayerURL, "live.sooplive.com")
}

// DiscoverPasswordBroadcast retains normal desktop metadata semantics: live pwd
// remains empty; the supplied secret authorizes proceeding to the native Center
// password check, and metadata success is not password-auth success.
func DiscoverPasswordBroadcast(ctx context.Context, client *http.Client, broadcaster, broadcast, streamPassword string) (Session, AuthenticatedLiveEvidence, error) {
	if !ValidNativeStreamPassword(streamPassword) || client == nil || client.Jar == nil || !safeLoginID(broadcaster) || !decimalID(broadcast) {
		return Session{}, AuthenticatedLiveEvidence{Outcome: "invalid_password_target_input"}, errors.New("invalid password target input")
	}
	return discoverAuthenticatedBroadcastAt(ctx, client, broadcaster, broadcast, accountPlayerURL, "live.sooplive.com", streamPassword)
}

// Native candidate truncates at 12 bytes then converts UTF-8 to CP949. This
// implementation deliberately supports only lossless ASCII, without truncation.
func ValidNativeStreamPassword(secret string) bool {
	if len(secret) < 1 || len(secret) > 12 {
		return false
	}
	for _, b := range []byte(secret) {
		if b < 0x20 || b > 0x7e {
			return false
		}
	}
	return true
}

func discoverAuthenticatedBroadcastAt(ctx context.Context, client *http.Client, broadcaster, broadcast, endpoint, expectedHost string, streamPassword ...string) (Session, AuthenticatedLiveEvidence, error) {
	e := AuthenticatedLiveEvidence{Outcome: "not_attempted"}
	apiURL, err := url.Parse(endpoint)
	if err != nil || apiURL.Scheme != "https" || !strings.EqualFold(apiURL.Hostname(), expectedHost) {
		e.Outcome = "invalid_first_party_api_endpoint"
		return Session{}, e, errors.New("invalid SOOP live API endpoint")
	}
	e.SessionCookieAvailable = len(client.Jar.Cookies(apiURL)) > 0
	if !e.SessionCookieAvailable {
		e.Outcome = "no_account_cookie_for_live_api"
		return Session{}, e, errors.New("account cookie jar has no cookie applicable to the live API")
	}
	form := url.Values{
		"from_api":    {"0"},
		"mode":        {"landing"},
		"player_type": {"html5"},
		"stream_type": {"common"},
		"type":        {"live"},
		"bid":         {broadcaster},
		"bno":         {broadcast},
		"pwd":         {""},
	}
	encoded := []byte(form.Encode())
	defer clear(encoded)
	page := accountPageOrigin + "/" + url.PathEscape(broadcaster) + "/" + url.PathEscape(broadcast)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(encoded))
	if err != nil {
		e.Outcome = "live_request_construction_failed"
		return Session{}, e, errors.New("could not construct SOOP live API request")
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Origin", accountPageOrigin)
	req.Header.Set("Referer", page)
	req.Header.Set("User-Agent", accountUA)
	resp, err := client.Do(req)
	if err != nil {
		e.Outcome = "live_api_network_error"
		return Session{}, e, errors.New("authenticated SOOP live API request failed")
	}
	e.HTTPStatus = resp.StatusCode
	if resp.Request != nil && !strings.EqualFold(resp.Request.URL.Hostname(), expectedHost) {
		resp.Body.Close()
		e.Outcome = "live_api_destination_changed"
		return Session{}, e, errors.New("SOOP live API destination changed")
	}
	body, readErr := io.ReadAll(io.LimitReader(resp.Body, maxBodySize+1))
	resp.Body.Close()
	if readErr != nil || len(body) > maxBodySize {
		clear(body)
		e.Outcome = "live_api_body_limit_or_read_error"
		return Session{}, e, errors.New("SOOP live API response could not be read within bounds")
	}
	defer clear(body)
	if resp.StatusCode != http.StatusOK {
		e.Outcome = "live_api_http_rejected"
		return Session{}, e, errors.New("SOOP live API rejected the request")
	}
	var root struct {
		Result  json.RawMessage            `json:"RESULT"`
		Channel map[string]json.RawMessage `json:"CHANNEL"`
	}
	if json.Unmarshal(body, &root) != nil {
		e.Outcome = "live_api_invalid_json"
		return Session{}, e, errors.New("SOOP live API response was not valid JSON")
	}
	e.JSONValid = true
	e.ChannelPresent = root.Channel != nil
	resultRaw, resultPresent := root.Channel["RESULT"]
	if !resultPresent {
		resultRaw, resultPresent = root.Channel["result"]
	}
	if !resultPresent {
		resultRaw, resultPresent = root.Result, len(root.Result) > 0
	}
	if !resultPresent {
		e.Outcome = "live_api_result_missing"
		return Session{}, e, errors.New("SOOP live API response did not contain a result")
	}
	e.Result, resultPresent = accountResultCode(resultRaw)
	if !resultPresent {
		e.Outcome = "live_api_result_malformed"
		return Session{}, e, errors.New("SOOP live API result was not a valid integer")
	}
	if e.Result == 0 {
		e.Outcome = "broadcast_offline"
		return Session{}, e, errors.New("SOOP reports that the requested broadcast is offline")
	}
	if e.Result == -6 {
		e.Outcome = "authenticated_live_api_requires_login"
		return Session{}, e, errors.New("SOOP live API still requires an authenticated session")
	}
	if e.Result != 1 {
		e.Outcome = "authenticated_live_api_denied_or_unavailable"
		return Session{}, e, errors.New("SOOP live API did not grant access to the requested broadcast")
	}
	if root.Channel == nil {
		e.Outcome = "live_api_channel_missing"
		return Session{}, e, errors.New("SOOP live API response did not contain CHANNEL")
	}
	actualBroadcast, _ := fieldText(root.Channel, "BNO")
	actualBroadcaster, _ := fieldText(root.Channel, "BJID")
	e.BroadcastMatches = actualBroadcast == broadcast
	e.BroadcasterMatches = strings.EqualFold(actualBroadcaster, broadcaster)
	if !e.BroadcastMatches || !e.BroadcasterMatches {
		e.Outcome = "live_api_target_mismatch"
		return Session{}, e, errors.New("SOOP live API returned a different broadcast")
	}
	e.AuthenticatedTargetAllowed = true
	bpwd, bpwdPresent := fieldText(root.Channel, "BPWD")
	e.PasswordStateKnown = bpwdPresent
	e.StreamPasswordRequired = bpwdPresent && !affirmativeNo(bpwd)
	if !bpwdPresent {
		e.Outcome = "live_metadata_password_state_unknown"
		return Session{}, e, errors.New("SOOP live API did not provide stream password state")
	}
	if e.StreamPasswordRequired && (len(streamPassword) != 1 || !ValidNativeStreamPassword(streamPassword[0])) {
		e.Outcome = "stream_password_required"
		return Session{}, e, errors.New("SOOP reports that the broadcast requires a separate stream password")
	}
	center, centerOK := publicEndpoint(rawText(root.Channel["CTIP"]), rawText(root.Channel["CTPT"]))
	gate, gateOK := publicEndpoint(rawText(root.Channel["GWIP"]), rawText(root.Channel["GWPT"]))
	e.CenterEndpointPresent, e.GatewayEndpointPresent = centerOK, gateOK
	if !centerOK || !gateOK {
		e.Outcome = "authenticated_metadata_missing_grid_endpoints"
		return Session{}, e, errors.New("SOOP live API did not return usable Grid endpoints")
	}
	bjid, _ := fieldText(root.Channel, "BJID")
	category, _ := fieldText(root.Channel, "CATE")
	fanTicket, fanTicketPresent := fieldText(root.Channel, "FTK")
	e.FanTicketFieldPresent = fanTicketPresent
	if !fanTicketPresent || bjid == "" {
		e.Outcome = "authenticated_metadata_missing_session_fields"
		return Session{}, e, errors.New("SOOP live API did not return required session fields")
	}
	adultCode, adultKnown := accountResultCode(root.Channel["ACHK"])
	e.AdultCheckKnown, e.AdultCheckConfirmed = adultKnown, adultKnown && adultCode == 1
	nativeCookie, nativeCookieSource := desktopNativeCookie(root.Channel, client.Jar)
	e.Outcome = "authenticated_target_metadata_allowed"
	return Session{
		BroadcastNo:         broadcast,
		LoginID:             broadcaster,
		BJID:                bjid,
		CenterAddr:          center,
		GatewayAddr:         gate,
		Category:            category,
		GatewayFTK:          fanTicket,
		GatewayCookie:       nativeCookie,
		GatewayCookieSource: nativeCookieSource,
		AdultConfirmed:      e.AdultCheckConfirmed,
	}, e, nil
}

func accountCookieScopes(client *http.Client, cookies []*http.Cookie) []CookieScopeEvidence {
	var out []CookieScopeEvidence
	for _, c := range cookies {
		if !safeEvidenceKey(c.Name) || len(out) >= 32 {
			continue
		}
		domain := strings.TrimPrefix(strings.ToLower(c.Domain), ".")
		if domain == "" {
			domain = "login.sooplive.com"
		}
		if domain != "sooplive.com" && !strings.HasSuffix(domain, ".sooplive.com") && domain != "sooplive.co.kr" && !strings.HasSuffix(domain, ".sooplive.co.kr") {
			continue
		}
		path := c.Path
		if path == "" {
			path = "<default>"
		} else if path != "/" && path != "/app" && path != "/app/" {
			path = "<non-root-redacted>"
		}
		e := CookieScopeEvidence{Name: c.Name, Domain: c.Domain, Path: path, Secure: c.Secure, HTTPOnly: c.HttpOnly}
		applies := func(target string) bool {
			u, _ := url.Parse(target)
			if u.Hostname() != domain && !strings.HasSuffix(u.Hostname(), "."+domain) {
				return false
			}
			if client.Jar != nil {
				for _, scoped := range client.Jar.Cookies(u) {
					if scoped.Name == c.Name {
						return true
					}
				}
			}
			return false
		}
		e.AppliesToLive = applies(accountPlayerURL)
		e.AppliesToCurrentWatch = applies("https://api.m.sooplive.com/broad/a/watch")
		e.AppliesToLegacyWatch = applies(watchInfoURL)
		out = append(out, e)
	}
	return out
}

func rawText(raw json.RawMessage) string {
	if len(raw) == 0 || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return ""
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s
	}
	return strings.TrimSpace(string(raw))
}

func accountResultCode(raw json.RawMessage) (int, bool) {
	var number json.Number
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if decoder.Decode(&number) == nil {
		value, err := strconv.Atoi(number.String())
		return value, err == nil
	}
	var text string
	if json.Unmarshal(raw, &text) == nil {
		value, err := strconv.Atoi(strings.TrimSpace(text))
		return value, err == nil
	}
	return 0, false
}
