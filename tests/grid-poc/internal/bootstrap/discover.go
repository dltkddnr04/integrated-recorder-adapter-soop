package bootstrap

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const (
	listingURL  = "https://live.sooplive.com/api/main_broad_list_api.php?lang=ko_KR&orderType=view_cnt&pageNo=1&selectType=action"
	playerURL   = "https://live.sooplive.com/afreeca/player_live_api.php"
	maxBodySize = 2 << 20
)

// Evidence contains only status and field-presence facts. It never carries
// ticket values, endpoint values, titles, or authentication cookies.
type Evidence struct {
	StartedUTC               string `json:"started_utc"`
	ListingHTTP              int    `json:"listing_http,omitempty"`
	LiveAPIHTTP              int    `json:"live_api_http,omitempty"`
	LiveAPIResult            int    `json:"live_api_result,omitempty"`
	PublicCandidateSelected  bool   `json:"public_candidate_selected"`
	PasswordFree             bool   `json:"password_free"`
	NotSubscriptionOnly      bool   `json:"not_subscription_only"`
	CenterEndpointUsable     bool   `json:"center_endpoint_usable"`
	GatewayEndpointUsable    bool   `json:"gateway_endpoint_usable"`
	AuthenticatedFieldsFound bool   `json:"live_session_fields_found"`
	CredentialValuesRedacted bool   `json:"credential_values_redacted"`
	Outcome                  string `json:"outcome"`
}

// Session is short-lived in-memory routing state. Secret and routing fields
// are excluded from JSON serialization; callers must call Scrub when finished.
type Session struct {
	BroadcastNo         string `json:"-"`
	LoginID             string `json:"-"`
	BJID                string `json:"-"`
	CenterAddr          string `json:"-"`
	GatewayAddr         string `json:"-"`
	Category            string `json:"-"`
	GatewayFTK          string `json:"-"`
	GatewayCookie       string `json:"-"`
	GatewayCookieSource string `json:"-"`
	FanTicket           string `json:"-"`
	AU                  string `json:"-"`
	AU3rd               string `json:"-"`
	AdultConfirmed      bool   `json:"-"`
}

func (s *Session) Scrub() {
	if s == nil {
		return
	}
	s.BroadcastNo, s.LoginID, s.BJID = "", "", ""
	s.CenterAddr, s.GatewayAddr, s.Category = "", "", ""
	s.GatewayFTK, s.GatewayCookie, s.GatewayCookieSource = "", "", ""
	s.FanTicket, s.AU, s.AU3rd = "", "", ""
	s.AdultConfirmed = false
}

func NewHTTPClient() *http.Client {
	return &http.Client{
		Timeout: 12 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 3 || req.URL.Scheme != "https" || !strings.EqualFold(req.URL.Hostname(), "live.sooplive.com") {
				return errors.New("redirect rejected")
			}
			return nil
		},
	}
}

// DiscoverSelected checks one exact broadcast against the public live list
// and the first-party live metadata API. It never searches beyond that page or
// switches to another target. Watch authorization is performed separately.
func DiscoverSelected(ctx context.Context, client *http.Client, started time.Time, broadcaster, broadcast string) (Session, Evidence, error) {
	e := Evidence{StartedUTC: started.UTC().Format(time.RFC3339), CredentialValuesRedacted: true, Outcome: "not_attempted"}
	if !safeLoginID(broadcaster) || !decimalID(broadcast) {
		e.Outcome = "invalid_exact_target"
		return Session{}, e, errors.New(e.Outcome)
	}
	if client == nil {
		client = NewHTTPClient()
	}
	listingBody, status, err := getBounded(ctx, client, listingURL)
	e.ListingHTTP = status
	if err != nil || status != http.StatusOK {
		e.Outcome = "public_listing_unavailable"
		return Session{}, e, errors.New(e.Outcome)
	}
	var listing struct {
		Broad []struct {
			BNO          string `json:"broad_no"`
			ID           string `json:"user_id"`
			Password     string `json:"is_password"`
			Subscription string `json:"subscription_only"`
		} `json:"broad"`
	}
	if json.Unmarshal(listingBody, &listing) != nil {
		clear(listingBody)
		e.Outcome = "public_listing_invalid_json"
		return Session{}, e, errors.New(e.Outcome)
	}
	clear(listingBody)
	selected := false
	for i := range listing.Broad {
		item := &listing.Broad[i]
		if item.ID == broadcaster && item.BNO == broadcast {
			selected = affirmativeNo(item.Password) && affirmativeNo(item.Subscription)
			e.PasswordFree = affirmativeNo(item.Password)
			e.NotSubscriptionOnly = affirmativeNo(item.Subscription)
			break
		}
	}
	if !selected {
		e.Outcome = "exact_target_not_public_password_free_listing_item"
		return Session{}, e, errors.New(e.Outcome)
	}
	e.PublicCandidateSelected = true

	form := url.Values{
		"from_api": {"0"}, "mode": {"landing"}, "player_type": {"html5"},
		"stream_type": {"common"}, "type": {"live"}, "bid": {broadcaster},
		"bno": {broadcast}, "pwd": {""},
	}
	body, liveStatus, err := postForm(ctx, client, playerURL, "https://play.sooplive.com/"+url.PathEscape(broadcaster)+"/"+url.PathEscape(broadcast), form)
	e.LiveAPIHTTP = liveStatus
	if err != nil {
		e.Outcome = "public_live_api_unavailable"
		return Session{}, e, errors.New(e.Outcome)
	}
	defer clear(body)
	var root struct {
		Result  json.RawMessage            `json:"RESULT"`
		Channel map[string]json.RawMessage `json:"CHANNEL"`
	}
	if json.Unmarshal(body, &root) != nil || root.Channel == nil {
		e.Outcome = "public_live_api_invalid_json"
		return Session{}, e, errors.New(e.Outcome)
	}
	resultRaw := root.Channel["RESULT"]
	if len(resultRaw) == 0 {
		resultRaw = root.Result
	}
	e.LiveAPIResult = integer(resultRaw)
	if e.LiveAPIResult != 1 {
		e.Outcome = "public_live_api_denied_or_offline"
		return Session{}, e, errors.New(e.Outcome)
	}
	actualBNO, _ := fieldText(root.Channel, "BNO")
	actualBJID, _ := fieldText(root.Channel, "BJID")
	if actualBNO != broadcast || !strings.EqualFold(actualBJID, broadcaster) {
		e.Outcome = "public_live_api_target_mismatch"
		return Session{}, e, errors.New(e.Outcome)
	}
	bpwd, bpwdPresent := fieldText(root.Channel, "BPWD")
	if !bpwdPresent || !affirmativeNo(bpwd) {
		e.Outcome = "public_live_api_password_state_unknown_or_protected"
		return Session{}, e, errors.New(e.Outcome)
	}
	center, centerOK := publicEndpoint(rawText(root.Channel["CTIP"]), rawText(root.Channel["CTPT"]))
	gate, gateOK := publicEndpoint(rawText(root.Channel["GWIP"]), rawText(root.Channel["GWPT"]))
	e.CenterEndpointUsable, e.GatewayEndpointUsable = centerOK, gateOK
	category, _ := fieldText(root.Channel, "CATE")
	ftk, hasFTK := fieldText(root.Channel, "FTK")
	bjid, _ := fieldText(root.Channel, "BJID")
	e.AuthenticatedFieldsFound = hasFTK
	if !centerOK || !gateOK || bjid == "" {
		e.Outcome = "public_live_api_missing_grid_fields"
		return Session{}, e, errors.New(e.Outcome)
	}
	achk, adultCheckKnown := accountResultCode(root.Channel["ACHK"])
	e.Outcome = "exact_public_live_metadata_ready"
	return Session{
		BroadcastNo: broadcast, LoginID: broadcaster, BJID: bjid,
		CenterAddr: center, GatewayAddr: gate, Category: category,
		GatewayFTK: ftk, AdultConfirmed: adultCheckKnown && achk == 1,
	}, e, nil
}

func getBounded(ctx context.Context, client *http.Client, target string) ([]byte, int, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36")
	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, err := readBounded(resp.Body, maxBodySize)
	return body, resp.StatusCode, err
}

func postForm(ctx context.Context, client *http.Client, endpoint, referer string, form url.Values) ([]byte, int, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || !strings.EqualFold(u.Hostname(), "live.sooplive.com") {
		return nil, 0, errors.New("live API destination invalid")
	}
	body := []byte(form.Encode())
	defer clear(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Referer", referer)
	req.Header.Set("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36")
	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	if resp.Request == nil || !strings.EqualFold(resp.Request.URL.Hostname(), "live.sooplive.com") || resp.StatusCode != http.StatusOK {
		return nil, resp.StatusCode, errors.New("live API rejected or changed destination")
	}
	response, err := readBounded(resp.Body, maxBodySize)
	return response, resp.StatusCode, err
}

func readBounded(r io.Reader, limit int64) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(b)) > limit {
		clear(b)
		return nil, errors.New("response too large")
	}
	return b, nil
}

func affirmativeNo(s string) bool {
	s = strings.TrimSpace(strings.ToUpper(s))
	return s == "N" || s == "0" || s == "FALSE"
}

func fieldText(m map[string]json.RawMessage, key string) (string, bool) {
	raw, ok := m[key]
	if !ok || len(raw) == 0 || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return "", false
	}
	var value string
	if json.Unmarshal(raw, &value) == nil {
		return value, value != ""
	}
	return strings.TrimSpace(string(raw)), true
}

func integer(raw json.RawMessage) int {
	n, ok := accountResultCode(raw)
	if !ok {
		return 0
	}
	return n
}

func publicEndpoint(ipText, portText string) (string, bool) {
	ip := net.ParseIP(strings.TrimSpace(ipText))
	port, err := strconv.Atoi(strings.TrimSpace(portText))
	if ip == nil || err != nil || port < 1 || port > 65535 || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return "", false
	}
	return net.JoinHostPort(ip.String(), strconv.Itoa(port)), true
}
