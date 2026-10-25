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
)

func TestProtectedLiveRetainsNormalMetadataRequest(t *testing.T) {
	for _, provide := range []bool{false, true} {
		jar, _ := cookiejar.New(nil)
		u, _ := url.Parse(accountPlayerURL)
		jar.SetCookies(u, []*http.Cookie{{Name: "member", Value: "synthetic"}})
		client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
			b, _ := io.ReadAll(r.Body)
			defer clear(b)
			f, _ := url.ParseQuery(string(b))
			if f.Get("pwd") != "" || f.Get("type") != "live" {
				t.Fatal("native metadata should not send stream secret")
			}
			return accountTestResponse(r, 200, make(http.Header), `{"CHANNEL":{"RESULT":1,"BNO":"12345","BJID":"fixture_channel","BPWD":"Y","CTIP":"8.8.8.8","CTPT":"10000","GWIP":"1.1.1.1","GWPT":"10001","FTK":"synthetic","TK":"fixture-ticket","ACHK":1}}`), nil
		})
		args := []string{}
		if provide {
			args = append(args, "fixture-only")
		}
		s, e, err := discoverAuthenticatedBroadcastAt(context.Background(), client, "fixture_channel", "12345", accountPlayerURL, "live.sooplive.com", args...)
		if !provide {
			if err == nil || e.Outcome != "stream_password_required" {
				t.Fatal("legacy path must require password")
			}
			continue
		}
		if err != nil || !e.StreamPasswordRequired {
			t.Fatal("protected native metadata unavailable")
		}
		encoded, _ := json.Marshal(e)
		if strings.Contains(string(encoded), args[0]) {
			t.Fatal("secret persisted")
		}
		if _, err = ValidateDesktopNativeSessionFor(&s, false); err != nil {
			t.Fatal(err)
		}
		s.Scrub()
	}
}

func TestNativePasswordBounds(t *testing.T) {
	for _, bad := range []string{"", "long-fixture-value", "a\x11b", "한글"} {
		if ValidNativeStreamPassword(bad) {
			t.Fatal("invalid password accepted")
		}
	}
	if !ValidNativeStreamPassword("fixture-only") {
		t.Fatal("ASCII subset rejected")
	}
}

func TestAdultValidatorStillRequiresConfirmedAdultCheck(t *testing.T) {
	s := Session{GatewayFTK: "fixture", GatewayCookie: "fixture", GatewayAddr: "fixture", CenterAddr: "fixture"}
	if _, err := ValidateDesktopNativeSession(&s); err == nil {
		t.Fatal("adult verification weakened")
	}
	if _, err := ValidateDesktopNativeSessionFor(&s, false); err != nil {
		t.Fatal("ordinary password target incorrectly requires adult status")
	}
}
