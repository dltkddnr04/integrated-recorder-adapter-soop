package bootstrap

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestSafeResolution(t *testing.T) {
	for input, want := range map[string]string{
		"1920x1080": "1920x1080",
		"1280X720":  "1280x720",
		"0x1080":    "",
		"99999x1":   "",
		"unknown":   "",
	} {
		if got := safeResolution(input); got != want {
			t.Errorf("safeResolution(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestSafeCodec(t *testing.T) {
	for input, want := range map[string]string{
		"H264":     "h264",
		"avc1":     "avc1",
		"hevc":     "hevc",
		"unknown":  "",
		"h264;key": "",
	} {
		if got := safeCodec(input); got != want {
			t.Errorf("safeCodec(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestClassifyWatchAccessReasonReturnsOnlyFixedCategories(t *testing.T) {
	for _, tc := range []struct {
		name, message, want string
	}{
		{"adult", `"성인 인증이 필요합니다"`, "adult_verification_required"},
		{"login", `"login required"`, "login_required"},
		{"membership", `"subscription required"`, "membership_required"},
		{"password", `"비밀번호 확인"`, "broadcast_password_required"},
		{"region", `"region restricted"`, "region_restricted"},
		{"generic access", `"permission denied"`, "access_denied"},
		{"unknown", `"user private text"`, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := classifyWatchAccessReason(map[string]json.RawMessage{"message": json.RawMessage(tc.message)})
			if got != tc.want {
				t.Fatalf("category=%q, want %q", got, tc.want)
			}
		})
	}
}

func TestJSONBool(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want bool
	}{
		{`true`, true}, {`1`, true}, {`"yes"`, true}, {`false`, false}, {`0`, false}, {`"no"`, false},
	} {
		if got := jsonBool(json.RawMessage(tc.raw)); got != tc.want {
			t.Errorf("jsonBool(%s) = %t, want %t", tc.raw, got, tc.want)
		}
	}
}

func TestWatchUsesOnlyScopedAUFromFirstPartyJarAndRecordsShape(t *testing.T) {
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	playURL, _ := url.Parse("https://play.sooplive.com/")
	jar.SetCookies(playURL, []*http.Cookie{
		{Name: "_au", Value: "synthetic-au", Domain: ".sooplive.com", Path: "/"},
		{Name: "member", Value: "synthetic-member", Domain: ".sooplive.com", Path: "/"},
	})
	client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
		switch {
		case r.Method == http.MethodGet && r.URL.Hostname() == playHost:
			if cookie, err := r.Cookie("_au"); err != nil || cookie.Value != "synthetic-au" {
				t.Error("first-party play request did not receive its scoped _au cookie")
			}
			return accountTestResponse(r, http.StatusOK, make(http.Header), ""), nil
		case r.Method == http.MethodPost && r.URL.Hostname() == "api.m.sooplive.co.kr":
			if got := r.Header.Get("Cookie"); got != "_au=synthetic-au" {
				t.Errorf("Watch API Cookie header = %q; want only the explicitly supported _au handoff", got)
			}
			body, _ := io.ReadAll(r.Body)
			_ = r.Body.Close()
			form, parseErr := url.ParseQuery(string(body))
			clear(body)
			if parseErr != nil || form.Get("bj_id") != "fixture_channel" || form.Get("broad_no") != "12345" {
				t.Error("Watch API request did not preserve the exact test target")
			}
			headers := make(http.Header)
			headers.Set("Content-Type", "application/json")
			return accountTestResponse(r, http.StatusOK, headers, `{"result":0,"data":{"error_code":"login_required","status":"adult_access_required"}}`), nil
		default:
			t.Errorf("unexpected Watch flow request: %s %s", r.Method, r.URL.Host)
			return nil, context.Canceled
		}
	})
	client.Jar = jar
	session := &Session{LoginID: "fixture_channel", BroadcastNo: "12345"}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	evidence, err := EnrichWithWatchClient(ctx, client, session)
	if err != nil {
		t.Fatalf("Watch flow returned error: %v", err)
	}
	if evidence.AUCookieSource != "play_first_party_cookie_jar" || !evidence.AUCookiePresent {
		t.Fatalf("unexpected sanitized cookie evidence: %+v", evidence)
	}
	if !evidence.ResponseResultKnown || evidence.ResponseResult != 0 {
		t.Fatalf("unexpected response result evidence: %+v", evidence)
	}
	if got := strings.Join(evidence.DataKeys, ","); got != "error_code,status" {
		t.Fatalf("redacted data keys = %q", got)
	}
	if evidence.FanTicketPresent || session.FanTicket != "" {
		t.Fatal("synthetic denial response must not produce a ticket")
	}
}
