package gateway

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestNativeCookieEvidenceDoesNotClaimAnonymousOrLogCallerText(t *testing.T) {
	for _, source := range []string{"live_api_TK", "scoped_AuthTicket_cookie", "fixture-secret-source"} {
		r := loginPreludeThenCertificate(context.Background(), "not-an-endpoint", NativeLoginFields{CookieSource: source}, 0, 0, nil)
		if r.CookieFromPublicAU {
			t.Fatal("authenticated or unknown source mislabeled as anonymous _au")
		}
		b, _ := json.Marshal(r)
		if strings.Contains(string(b), "fixture-secret-source") {
			t.Fatal("untrusted label persisted")
		}
	}
}
