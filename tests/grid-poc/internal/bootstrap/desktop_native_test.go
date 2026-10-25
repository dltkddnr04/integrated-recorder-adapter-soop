package bootstrap

import (
	"encoding/json"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"testing"
)

func TestDesktopNativeCookieUsesOfficialAccountTicketSources(t *testing.T) {
	jar, _ := cookiejar.New(nil)
	play, _ := url.Parse(accountPageOrigin + "/")
	jar.SetCookies(play, []*http.Cookie{{Name: "_au", Value: "fixture-anonymous", Path: "/"}, {Name: "AuthTicket", Value: "fixture-account", Path: "/"}})
	cookie, source := desktopNativeCookie(map[string]json.RawMessage{"TK": json.RawMessage(`"fixture-live-ticket"`)}, jar)
	if cookie != "fixture-live-ticket" || source != "live_api_TK" {
		t.Fatal("CHANNEL.TK did not take precedence")
	}
	cookie, source = desktopNativeCookie(nil, jar)
	if cookie != "fixture-account" || source != "scoped_AuthTicket_cookie" {
		t.Fatal("normal AuthTicket scope was not used")
	}
	jar.SetCookies(play, []*http.Cookie{{Name: "AuthTicket", Value: "", Path: "/", MaxAge: -1}})
	if cookie, _ = desktopNativeCookie(nil, jar); cookie != "" {
		t.Fatal("anonymous _au was substituted as an account ticket")
	}
	other, _ := url.Parse("https://api.m.sooplive.co.kr/")
	jar.SetCookies(other, []*http.Cookie{{Name: "AuthTicket", Value: "fixture-other-domain", Path: "/"}})
	if cookie, _ = desktopNativeCookie(nil, jar); cookie != "" {
		t.Fatal("cross-domain cookie was copied")
	}
	if cookie, _ = desktopNativeCookie(map[string]json.RawMessage{"TK": json.RawMessage(`"bad\u0012field"`)}, jar); cookie != "" {
		t.Fatal("native field delimiter accepted")
	}
}

func TestDesktopNativeBootstrapDoesNotAuthorizeMissingAdultCheckOrPersistSecrets(t *testing.T) {
	s := Session{GatewayFTK: "fixture-ftk", GatewayCookie: "fixture-auth", GatewayCookieSource: "live_api_TK", GatewayAddr: "fixture", CenterAddr: "fixture"}
	if _, err := ValidateDesktopNativeSession(&s); err == nil {
		t.Fatal("missing authenticated adult check accepted")
	}
	s.AdultConfirmed = true
	e, err := ValidateDesktopNativeSession(&s)
	if err != nil || e.Outcome != "official_native_live_fields_available" {
		t.Fatal("complete sourced session rejected")
	}
	b, _ := json.Marshal(e)
	if strings.Contains(string(b), s.GatewayFTK) || strings.Contains(string(b), s.GatewayCookie) {
		t.Fatal("secret value entered public evidence")
	}
	b, _ = json.Marshal(s)
	if strings.Contains(string(b), "fixture-auth") {
		t.Fatal("account ticket leaked into Session serialization")
	}
	s.Scrub()
	if s.GatewayCookie != "" || s.GatewayFTK != "" || s.GatewayCookieSource != "" {
		t.Fatal("native credential state not scrubbed")
	}
}
