package main

import "testing"

func TestProtectionModeRequiresExactServerState(t *testing.T) {
	tests := []struct {
		name              string
		requestedPassword bool
		serverRequires    bool
		wantFailureStage  string
	}{
		{name: "ordinary target", wantFailureStage: ""},
		{name: "password target", requestedPassword: true, serverRequires: true, wantFailureStage: ""},
		{name: "password option against ordinary target", requestedPassword: true, wantFailureStage: "target_not_password_protected"},
		{name: "protected target without password mode", serverRequires: true, wantFailureStage: "target_requires_password"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := protectionModeFailure(test.requestedPassword, test.serverRequires); got != test.wantFailureStage {
				t.Fatalf("protectionModeFailure() = %q, want %q", got, test.wantFailureStage)
			}
		})
	}
}
