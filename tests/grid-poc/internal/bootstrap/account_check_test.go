package bootstrap

import (
	"context"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"testing"
)

func TestAccountSessionCheckUsesJarAndRejectsWrongIdentity(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		want       bool
	}{
		{"matching", `{"CHANNEL":{"LOGIN_ID":"fixture_account"}}`, true},
		{"different", `{"CHANNEL":{"LOGIN_ID":"other_fixture"}}`, false},
		{"anonymous", `{"CHANNEL":{"LOGIN_ID":""}}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			jar, _ := cookiejar.New(nil)
			u, _ := url.Parse("https://login.sooplive.com/")
			jar.SetCookies(u, []*http.Cookie{{Name: "member", Value: "fixture", Domain: ".sooplive.com", Path: "/"}})
			client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
				if r.Method != "GET" || r.URL.Hostname() != "afevent2.sooplive.com" || r.URL.Path != "/api/get_private_info.php" {
					t.Fatal("unexpected session-check request")
				}
				if c, e := r.Cookie("member"); e != nil || c.Value != "fixture" {
					t.Fatal("session cookie was not applied")
				}
				return accountTestResponse(r, 200, make(http.Header), tc.body), nil
			})
			e, err := CheckAccountSession(context.Background(), client, "fixture_account", "fixture_channel", "12345")
			if (err == nil) != tc.want || e.RequestedAccountMatches != tc.want {
				t.Fatalf("wrong authentication classification: %+v", e)
			}
		})
	}
}
