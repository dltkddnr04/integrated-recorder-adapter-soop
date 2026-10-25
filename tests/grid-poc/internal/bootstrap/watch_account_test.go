package bootstrap

import (
	"context"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strconv"
	"testing"
)

func TestAuthenticatedWatchScopesAccountCookiesAndUsesServerConfirmation(t *testing.T) {
	for _, confirmed := range []bool{false, true} {
		t.Run(strconv.FormatBool(confirmed), func(t *testing.T) {
			jar, _ := cookiejar.New(nil)
			play, _ := url.Parse("https://play.sooplive.com/")
			jar.SetCookies(play, []*http.Cookie{{Name: "member", Value: "fixture-member", Domain: ".sooplive.com", Path: "/", Secure: true}, {Name: "_au", Value: "fixture-au", Domain: ".sooplive.com", Path: "/"}})
			client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
				if r.Method == http.MethodGet && r.URL.Hostname() == playHost {
					return accountTestResponse(r, 200, make(http.Header), ""), nil
				}
				if r.URL.Scheme != "https" || r.URL.Hostname() != "api.m.sooplive.com" || r.URL.Query().Get("bjid") != "fixture_channel" {
					t.Fatalf("unexpected Watch destination")
				}
				if c, e := r.Cookie("member"); e != nil || c.Value != "fixture-member" {
					t.Fatal("account cookie missing on matching .com domain")
				}
				b, _ := io.ReadAll(r.Body)
				form, _ := url.ParseQuery(string(b))
				clear(b)
				for k, w := range map[string]string{"bj_id": "fixture_channel", "broad_no": "12345", "agent": "web", "player_type": "html5", "mode": "live", "confirm_adult": strconv.FormatBool(confirmed)} {
					if form.Get(k) != w {
						t.Errorf("unexpected form field %s", k)
					}
				}
				return accountTestResponse(r, 200, make(http.Header), `{"result":1,"data":{"fan_ticket":"fixture-ticket","gateway_ip":"1.1.1.1","gateway_port":3456,"relay_ip":"8.8.8.8","relay_port":18000}}`), nil
			})
			session := Session{LoginID: "fixture_channel", BroadcastNo: "12345", AdultConfirmed: confirmed}
			e, err := EnrichAuthenticatedWatch(context.Background(), client, &session)
			if err != nil || !e.FanTicketPresent || !e.AccountCookieJarUsed || e.ConfirmAdult != confirmed || session.FanTicket != "fixture-ticket" {
				t.Fatalf("authenticated Watch flow failed: %+v %v", e, err)
			}
			session.Scrub()
		})
	}
}

func TestAuthenticatedWatchDoesNotConsumeTicketOnNegativeResult(t *testing.T) {
	jar, _ := cookiejar.New(nil)
	client := accountTestClient(jar, func(r *http.Request) (*http.Response, error) {
		if r.Method == http.MethodGet {
			return accountTestResponse(r, 200, make(http.Header), ""), nil
		}
		return accountTestResponse(r, 200, make(http.Header), `{"result":-1,"data":{"code":-3004,"message":"fixture-private-message","fan_ticket":"must-not-use"}}`), nil
	})
	session := Session{LoginID: "fixture_channel", BroadcastNo: "12345"}
	e, err := EnrichAuthenticatedWatch(context.Background(), client, &session)
	if err == nil || e.DataCode == nil || *e.DataCode != -3004 || e.AccessReason != "adult_gate_rejected" || session.FanTicket != "" || e.FanTicketPresent {
		t.Fatalf("negative result not rejected: %+v", e)
	}
}
