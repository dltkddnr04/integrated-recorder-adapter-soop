package bootstrap

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"strings"
)

// Current first-party native bootstrap uses CHANNEL.TK when present and
// otherwise a normally scoped AuthTicket cookie. It never substitutes _au.
func desktopNativeCookie(channel map[string]json.RawMessage, jar http.CookieJar) (string, string) {
	if ticket, _ := fieldText(channel, "TK"); ticket != "" {
		if validNativeCookie(ticket) {
			return ticket, "live_api_TK"
		}
		return "", "invalid_live_api_TK"
	}
	if jar != nil {
		u, _ := url.Parse(accountPageOrigin + "/")
		for _, cookie := range jar.Cookies(u) {
			if cookie.Name == "AuthTicket" && validNativeCookie(cookie.Value) {
				return cookie.Value, "scoped_AuthTicket_cookie"
			}
		}
	}
	return "", "authenticated_gateway_cookie_unavailable"
}

func validNativeCookie(value string) bool {
	return len(value) > 0 && len(value) <= 4096 && !strings.ContainsAny(value, "\x00\x11\x12\r\n")
}

type NativeBootstrapEvidence struct {
	API               string `json:"api"`
	FTKPresent        bool   `json:"ftk_present"`
	CookiePresent     bool   `json:"native_cookie_present"`
	CookieSource      string `json:"native_cookie_source"`
	AdultConfirmed    bool   `json:"authenticated_live_achk_confirmed"`
	EndpointsFromLive bool   `json:"gateway_center_from_live_api"`
	Outcome           string `json:"outcome"`
}

func ValidateDesktopNativeSession(session *Session) (NativeBootstrapEvidence, error) {
	return ValidateDesktopNativeSessionFor(session, true)
}

func ValidateDesktopNativeSessionFor(session *Session, requireAdult bool) (NativeBootstrapEvidence, error) {
	e := NativeBootstrapEvidence{API: "authenticated_player_live_api", Outcome: "native_live_session_incomplete"}
	if session == nil {
		return e, errors.New(e.Outcome)
	}
	e.FTKPresent = len(session.GatewayFTK) > 0 && len(session.GatewayFTK) <= 4096
	e.CookiePresent = validNativeCookie(session.GatewayCookie)
	e.CookieSource = session.GatewayCookieSource
	e.AdultConfirmed = session.AdultConfirmed
	e.EndpointsFromLive = session.GatewayAddr != "" && session.CenterAddr != ""
	if !e.FTKPresent || !e.CookiePresent || (requireAdult && !e.AdultConfirmed) || !e.EndpointsFromLive {
		return e, errors.New(e.Outcome)
	}
	e.Outcome = "official_native_live_fields_available"
	return e, nil
}
