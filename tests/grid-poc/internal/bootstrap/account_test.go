package bootstrap

import (
	"context"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"
)

type accountTestTransport func(*http.Request) (*http.Response, error)

func (f accountTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func accountTestClient(jar http.CookieJar, transport accountTestTransport) *http.Client {
	return &http.Client{
		Transport: transport,
		Jar:       jar,
		Timeout:   2 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func accountTestResponse(request *http.Request, status int, headers http.Header, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Status:     strconv.Itoa(status) + " " + http.StatusText(status),
		Header:     headers,
		Body:       io.NopCloser(strings.NewReader(body)),
		Request:    request,
	}
}

func TestLoginAccountAtUsesFirstPartyFormAndKeepsCookieInMemory(t *testing.T) {
	jar, _ := cookiejar.New(nil)
	client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
		if r.Method != http.MethodPost || r.URL.Hostname() != "login.sooplive.com" || r.URL.Path != "/app/LoginAction.php" {
			t.Errorf("unexpected request target: %s %s", r.Method, r.URL)
		}
		if got := r.Header.Get("Origin"); got != accountPageOrigin {
			t.Errorf("Origin=%q", got)
		}
		body, _ := io.ReadAll(r.Body)
		_ = r.Body.Close()
		form, err := url.ParseQuery(string(body))
		clear(body)
		if err != nil {
			t.Errorf("parse form: %v", err)
		}
		for key, want := range map[string]string{
			"szWork": "login", "szType": "json", "szUid": "fixture_account",
			"isSaveId": "true", "isSavePw": "false", "isSaveJoin": "false", "isLoginRetain": "Y",
		} {
			if got := form.Get(key); got != want {
				t.Errorf("form[%s]=%q, want %q", key, got, want)
			}
		}
		if form.Get("szPassword") == "" {
			t.Error("password field was empty")
		}
		headers := make(http.Header)
		headers.Set("Content-Type", "application/json")
		headers.Add("Set-Cookie", "member=synthetic; Domain=.sooplive.com; Path=/; HttpOnly")
		return accountTestResponse(r, http.StatusOK, headers, `{"RESULT":1}`), nil
	})

	password := "test-only-" + strconv.FormatInt(time.Now().UnixNano(), 36)
	e, err := loginAccountAt(context.Background(), client, "fixture_account", password, soopLoginActionURL, accountPageOrigin+"/", "login.sooplive.com")
	if err != nil {
		t.Fatalf("login helper: %v", err)
	}
	if !e.JSONValid || e.Result != 1 || !e.FirstPartySessionCookie || e.Outcome != "login_result_ok_session_cookie_present" {
		t.Fatalf("unexpected sanitized login evidence: %+v", e)
	}
}

func TestLoginAccountAtClassifiesRejectedResponse(t *testing.T) {
	jar, _ := cookiejar.New(nil)
	client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
		headers := make(http.Header)
		headers.Set("Content-Type", "application/json")
		return accountTestResponse(r, http.StatusOK, headers, `{"RESULT":-6,"MESSAGE":"synthetic"}`), nil
	})
	e, err := loginAccountAt(context.Background(), client, "fixture_account", "test-only", soopLoginActionURL, accountPageOrigin+"/", "login.sooplive.com")
	if err == nil || e.Outcome != "login_rejected_or_additional_verification_required" || e.Result != -6 {
		t.Fatalf("unexpected rejection classification: evidence=%+v err=%v", e, err)
	}
}

func TestDiscoverAuthenticatedBroadcastUsesJarAndExactTarget(t *testing.T) {
	jar, _ := cookiejar.New(nil)
	apiURL, _ := url.Parse(accountPlayerURL)
	jar.SetCookies(apiURL, []*http.Cookie{{Name: "member", Value: "synthetic", Path: "/"}})
	client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
		if r.Method != http.MethodPost || r.URL.Hostname() != "live.sooplive.com" || r.URL.Path != "/afreeca/player_live_api.php" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL)
		}
		if cookie, err := r.Cookie("member"); err != nil || cookie.Value != "synthetic" {
			t.Error("authenticated cookie jar was not applied to live API")
		}
		body, _ := io.ReadAll(r.Body)
		_ = r.Body.Close()
		form, err := url.ParseQuery(string(body))
		clear(body)
		if err != nil {
			t.Errorf("parse form: %v", err)
		}
		if form.Get("bid") != "fixture_channel" || form.Get("bno") != "12345" || form.Get("type") != "live" {
			t.Errorf("request selected unexpected target")
		}
		headers := make(http.Header)
		headers.Set("Content-Type", "application/json")
		return accountTestResponse(r, http.StatusOK, headers, `{"CHANNEL":{"RESULT":1,"BNO":"12345","BJID":"fixture_channel","BPWD":"N","CTIP":"8.8.8.8","CTPT":"10000","GWIP":"1.1.1.1","GWPT":"10001","FTK":"synthetic-ticket","CATE":"adult","ACHK":1}}`), nil
	})
	s, e, err := discoverAuthenticatedBroadcastAt(context.Background(), client, "fixture_channel", "12345", accountPlayerURL, "live.sooplive.com")
	if err != nil {
		t.Fatalf("authenticated discovery: %v", err)
	}
	if !e.AuthenticatedTargetAllowed || !e.BroadcastMatches || !e.BroadcasterMatches || e.StreamPasswordRequired {
		t.Fatalf("unexpected access evidence: %+v", e)
	}
	if !e.AdultCheckKnown || !e.AdultCheckConfirmed || !s.AdultConfirmed {
		t.Fatal("confirm_adult state must be derived from the server ACHK field")
	}
	if s.BroadcastNo != "12345" || s.LoginID != "fixture_channel" || s.GatewayAddr != "1.1.1.1:10001" || s.CenterAddr != "8.8.8.8:10000" || s.GatewayFTK != "synthetic-ticket" {
		t.Fatalf("unexpected in-memory routing state: %+v", s)
	}
	s.Scrub()
	if s.AdultConfirmed {
		t.Fatal("adult confirmation remained after session scrub")
	}
}

func TestDiscoverAuthenticatedBroadcastDistinguishesOfflineAndLoginRequired(t *testing.T) {
	for _, tc := range []struct {
		name, body, outcome string
		result              int
	}{
		{name: "offline", body: `{"RESULT":0}`, outcome: "broadcast_offline", result: 0},
		{name: "login required", body: `{"RESULT":-6}`, outcome: "authenticated_live_api_requires_login", result: -6},
		{name: "malformed result", body: `{"RESULT":"offline"}`, outcome: "live_api_result_malformed", result: 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			jar, _ := cookiejar.New(nil)
			apiURL, _ := url.Parse(accountPlayerURL)
			jar.SetCookies(apiURL, []*http.Cookie{{Name: "member", Value: "synthetic", Path: "/"}})
			client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
				headers := make(http.Header)
				headers.Set("Content-Type", "application/json")
				return accountTestResponse(r, http.StatusOK, headers, tc.body), nil
			})
			_, evidence, err := discoverAuthenticatedBroadcastAt(context.Background(), client, "fixture_channel", "12345", accountPlayerURL, "live.sooplive.com")
			if err == nil || evidence.Outcome != tc.outcome || evidence.Result != tc.result {
				t.Fatalf("got evidence=%+v err=%v", evidence, err)
			}
		})
	}
}

func TestCookieJarDoesNotCopyComAccountCookieToCoKr(t *testing.T) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	playURL, _ := url.Parse("https://play.sooplive.com/")
	jar.SetCookies(playURL, []*http.Cookie{{Name: "member", Value: "synthetic", Domain: ".sooplive.com", Path: "/"}})
	liveURL, _ := url.Parse("https://live.sooplive.com/")
	watchURL, _ := url.Parse("https://api.m.sooplive.co.kr/broad/a/watch")
	if len(jar.Cookies(liveURL)) == 0 {
		t.Fatal("first-party cookie should apply to the SOOP .com live host")
	}
	if len(jar.Cookies(watchURL)) != 0 {
		t.Fatal(".sooplive.com account cookie leaked to the separate .co.kr API domain")
	}
}

func TestAuthenticatedBroadcastNeverAdoptsChangedLiveNumber(t *testing.T) {
	jar, _ := cookiejar.New(nil)
	u, _ := url.Parse(accountPlayerURL)
	jar.SetCookies(u, []*http.Cookie{{Name: "member", Value: "fixture", Path: "/"}})
	client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
		return accountTestResponse(r, 200, make(http.Header), `{"CHANNEL":{"RESULT":1,"BNO":"54321","BJID":"fixture_channel","ACHK":1}}`), nil
	})
	s, e, err := DiscoverAuthenticatedBroadcast(context.Background(), client, "fixture_channel", "12345")
	if err == nil || e.Outcome != "live_api_target_mismatch" || e.BroadcastMatches || e.AuthenticatedTargetAllowed || s.BroadcastNo != "" || s.FanTicket != "" {
		t.Fatal("changed live broadcast was not rejected before session adoption")
	}
}
