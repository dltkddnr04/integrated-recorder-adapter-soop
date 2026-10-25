package bootstrap

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
)

type AccountSessionEvidence struct {
	HTTPStatus              int    `json:"http_status"`
	LoggedIn                bool   `json:"logged_in"`
	RequestedAccountMatches bool   `json:"requested_account_matches"`
	Outcome                 string `json:"outcome"`
}

// CheckAccountSession mirrors the official Streamlink _check_auth request.
// The returned account identifier is compared only in memory.
func CheckAccountSession(ctx context.Context, client *http.Client, accountID, broadcaster, broadcast string) (AccountSessionEvidence, error) {
	e := AccountSessionEvidence{Outcome: "not_attempted"}
	if client == nil || client.Jar == nil || !safeLoginID(accountID) || !safeLoginID(broadcaster) || !decimalID(broadcast) {
		return e, errors.New("invalid session-check inputs")
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "https://afevent2.sooplive.com/api/get_private_info.php", nil)
	req.Header.Set("Origin", accountPageOrigin)
	req.Header.Set("Referer", accountPageOrigin+"/"+url.PathEscape(broadcaster)+"/"+broadcast)
	req.Header.Set("User-Agent", accountUA)
	resp, err := client.Do(req)
	if err != nil {
		e.Outcome = "session_check_network_error"
		return e, errors.New(e.Outcome)
	}
	defer resp.Body.Close()
	e.HTTPStatus = resp.StatusCode
	b, err := io.ReadAll(io.LimitReader(resp.Body, (1<<20)+1))
	defer clear(b)
	if err != nil || len(b) > 1<<20 || resp.StatusCode != 200 {
		e.Outcome = "session_check_http_or_body_error"
		return e, errors.New(e.Outcome)
	}
	var r struct {
		Channel struct {
			LoginID string `json:"LOGIN_ID"`
		} `json:"CHANNEL"`
	}
	if json.Unmarshal(b, &r) != nil {
		e.Outcome = "session_check_schema_error"
		return e, errors.New(e.Outcome)
	}
	e.LoggedIn = r.Channel.LoginID != ""
	e.RequestedAccountMatches = r.Channel.LoginID == accountID
	r.Channel.LoginID = ""
	if !e.LoggedIn || !e.RequestedAccountMatches {
		e.Outcome = "session_check_rejected"
		return e, errors.New(e.Outcome)
	}
	e.Outcome = "session_confirmed"
	return e, nil
}
