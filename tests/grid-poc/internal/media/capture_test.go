package media

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestExistingCaptureDirectoryIsNeverOverwritten(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "recording")
	c, e := NewCapture(dir)
	if e != nil {
		t.Fatal(e)
	}
	if e = c.Close(); e != nil {
		t.Fatal(e)
	}
	before, e := os.ReadFile(filepath.Join(dir, "grid-analysis.json"))
	if e != nil {
		t.Fatal(e)
	}
	if strings.Contains(string(before), dir) {
		t.Fatal("capture metadata persisted the local output path")
	}
	if _, e = NewCapture(dir); e == nil {
		t.Fatal("existing capture reopened")
	}
	after, _ := os.ReadFile(filepath.Join(dir, "grid-analysis.json"))
	if string(before) != string(after) {
		t.Fatal("existing saved evidence changed")
	}
}
