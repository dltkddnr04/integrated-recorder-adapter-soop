package bootstrap

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestDiscoverSelectedUsesOnlyTheExactPublicTarget(t *testing.T) {
	requests := 0
	client := accountTestClient(nil, func(r *http.Request) (*http.Response, error) {
		requests++
		switch requests {
		case 1:
			if r.Method != http.MethodGet || r.URL.Hostname() != "live.sooplive.com" {
				t.Fatal("unexpected public listing request")
			}
			return accountTestResponse(r, 200, make(http.Header), `{"broad":[{"broad_no":"12345","user_id":"fixture_channel","is_password":"N","subscription_only":"0"}]}`), nil
		case 2:
			if r.Method != http.MethodPost || r.URL.Path != "/afreeca/player_live_api.php" {
				t.Fatal("unexpected live metadata request")
			}
			return accountTestResponse(r, 200, make(http.Header), `{"CHANNEL":{"RESULT":1,"BNO":"12345","BJID":"fixture_channel","BPWD":"N","CTIP":"8.8.8.8","CTPT":"10000","GWIP":"1.1.1.1","GWPT":"10001","FTK":"fixture-ticket","TK":"fixture-cookie","CATE":"game","ACHK":1}}`), nil
		default:
			t.Fatal("unexpected extra request")
			return nil, context.Canceled
		}
	})
	session, evidence, err := DiscoverSelected(context.Background(), client, time.Unix(0, 0), "fixture_channel", "12345")
	if err != nil {
		t.Fatalf("exact public target was rejected: %v", err)
	}
	if requests != 2 || !evidence.PublicCandidateSelected || !evidence.PasswordFree || !evidence.NotSubscriptionOnly || !evidence.CenterEndpointUsable || !evidence.GatewayEndpointUsable {
		t.Fatalf("unexpected sanitized evidence: %+v", evidence)
	}
	if session.BroadcastNo != "12345" || session.LoginID != "fixture_channel" || session.CenterAddr != "8.8.8.8:10000" || session.GatewayAddr != "1.1.1.1:10001" || session.GatewayFTK != "fixture-ticket" || !session.AdultConfirmed {
		t.Fatal("live session fields were not mapped")
	}
	serialized, _ := json.Marshal(session)
	for _, value := range []string{"fixture-ticket", "fixture-cookie", "8.8.8.8", "fixture_channel"} {
		if strings.Contains(string(serialized), value) {
			t.Fatal("private session field was serializable")
		}
	}
	session.Scrub()
	if session.GatewayFTK != "" || session.CenterAddr != "" {
		t.Fatal("session secrets and routes were not scrubbed")
	}
}

func TestDiscoverSelectedRejectsProtectedAndChangedTargets(t *testing.T) {
	for _, tc := range []struct {
		name, item string
	}{
		{"password-protected", `{"broad_no":"12345","user_id":"fixture_channel","is_password":"Y","subscription_only":"0"}`},
		{"subscription-only", `{"broad_no":"12345","user_id":"fixture_channel","is_password":"N","subscription_only":"1"}`},
		{"different-target", `{"broad_no":"54321","user_id":"other_channel","is_password":"N","subscription_only":"0"}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			requests := 0
			client := accountTestClient(nil, func(r *http.Request) (*http.Response, error) {
				requests++
				return accountTestResponse(r, 200, make(http.Header), `{"broad":[`+tc.item+`]}`), nil
			})
			_, evidence, err := DiscoverSelected(context.Background(), client, time.Unix(0, 0), "fixture_channel", "12345")
			if err == nil || evidence.PublicCandidateSelected || requests != 1 {
				t.Fatal("restricted or changed target was accepted or queried further")
			}
		})
	}
}
