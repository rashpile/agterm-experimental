package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"testing/iotest"
	"time"
)

const hookPaneID = "8F2C1A90-77AB-4C1D-9E21-0B5F3D6A1C44"

func hookEnv(paneID string) func(string) string {
	return func(k string) string {
		if k == "AGTERM_PANE_ID" {
			return paneID
		}
		return ""
	}
}

func fixedNow(ms int64) func() time.Time {
	return func() time.Time { return time.UnixMilli(ms) }
}

// runHook runs hookMain with stdin and the pane id, and fails the test unless it returns 0.
func runHook(t *testing.T, dir, paneID, stdin string, ms int64) string {
	t.Helper()
	var stderr bytes.Buffer
	if code := hookMain(strings.NewReader(stdin), &stderr, hookEnv(paneID), dir, fixedNow(ms)); code != 0 {
		t.Fatalf("hookMain returned %d", code)
	}
	return stderr.String()
}

func TestHookPromptAndStop(t *testing.T) {
	dir := t.TempDir()
	prompt := "review this\n\tfunc main() {}  \r\n"
	in := []string{
		`{"hook_event_name":"UserPromptSubmit","session_id":"s-1","prompt":` + jsonString(prompt) + `,"cwd":"/x"}`,
		`{"hook_event_name":"Stop","session_id":"s-1","last_assistant_message":"Done.\nAll good.","stop_hook_active":false}`,
	}
	for i, stdin := range in {
		if stderr := runHook(t, dir, hookPaneID, stdin, int64(1000+i)); stderr != "" {
			t.Errorf("stderr: %q", stderr)
		}
	}
	got, err := readTurns(filepath.Join(dir, "turns", hookPaneID+".jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	want := []turnRecord{
		{Event: "prompt", T: 1000, Session: "s-1", Hash: promptHash("review this\n    func main() {}")},
		{Event: "stop", T: 1001, Session: "s-1", Reply: strp("Done.\nAll good.")},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("journal\n got %+v\nwant %+v", got, want)
	}
	if got[0].Hash != promptHash(prompt) {
		t.Error("prompt record hash is not promptHash of the prompt")
	}
}

func TestHookStopWithoutMessage(t *testing.T) {
	dir := t.TempDir()
	runHook(t, dir, hookPaneID, `{"hook_event_name":"Stop","session_id":"s-1"}`, 5)
	got, err := readTurns(journalPath(dir, hookPaneID))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Event != "stop" || got[0].Reply == nil || *got[0].Reply != "" {
		t.Fatalf("want one stop record with an empty reply, got %+v", got)
	}
}

func TestHookSanitizesPaneID(t *testing.T) {
	dir := t.TempDir()
	runHook(t, dir, "AB:12/../x", `{"hook_event_name":"UserPromptSubmit","session_id":"s","prompt":"hi"}`, 1)
	if _, err := os.Stat(filepath.Join(dir, "turns", "AB-12----x.jsonl")); err != nil {
		t.Errorf("journal not at the sanitized path: %v", err)
	}
}

func TestHookWritesNothing(t *testing.T) {
	tests := []struct {
		name, paneID, stdin string
	}{
		{"unknown event", hookPaneID, `{"hook_event_name":"PreToolUse","session_id":"s","tool_name":"Bash"}`},
		{"no event name", hookPaneID, `{"session_id":"s","prompt":"hi"}`},
		{"no AGTERM_PANE_ID", "", `{"hook_event_name":"UserPromptSubmit","session_id":"s","prompt":"hi"}`},
		{"no AGTERM_PANE_ID, malformed input", "", `not json`},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			if stderr := runHook(t, dir, tc.paneID, tc.stdin, 1); stderr != "" {
				t.Errorf("stderr: %q", stderr)
			}
			if _, err := os.Stat(filepath.Join(dir, "turns")); !errors.Is(err, os.ErrNotExist) {
				t.Errorf("turns dir created: %v", err)
			}
		})
	}
}

func TestHookErrors(t *testing.T) {
	fileDir := filepath.Join(t.TempDir(), "state")
	// A file where the state dir should be: the turns dir cannot be created.
	if err := os.WriteFile(fileDir, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	prompt := `{"hook_event_name":"UserPromptSubmit","session_id":"s","prompt":"hi"}`
	tests := []struct {
		name, dir, stdin string
	}{
		{name: "malformed stdin", stdin: `{"hook_event_name":"Stop",`},
		{name: "not an object", stdin: `["Stop"]`},
		{name: "empty stdin", stdin: ""},
		{name: "unwritable state dir", dir: fileDir, stdin: prompt},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			dir := tc.dir
			if dir == "" {
				dir = t.TempDir()
			}
			var stderr bytes.Buffer
			if code := hookMain(strings.NewReader(tc.stdin), &stderr, hookEnv(hookPaneID), dir, fixedNow(1)); code != 0 {
				t.Errorf("result %d, want 0", code)
			}
			assertOneHookLine(t, stderr.String())
			if tc.dir == "" {
				if _, err := os.Stat(filepath.Join(dir, "turns")); !errors.Is(err, os.ErrNotExist) {
					t.Errorf("turns dir created: %v", err)
				}
			}
		})
	}
}

func TestHookStdinReadError(t *testing.T) {
	dir := t.TempDir()
	var stderr bytes.Buffer
	stdin := iotest.ErrReader(errors.New("broken\npipe"))
	if code := hookMain(stdin, &stderr, hookEnv(hookPaneID), dir, fixedNow(1)); code != 0 {
		t.Errorf("result %d, want 0", code)
	}
	assertOneHookLine(t, stderr.String())
	if _, err := os.Stat(filepath.Join(dir, "turns")); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("turns dir created: %v", err)
	}
}

func assertOneHookLine(t *testing.T, stderr string) {
	t.Helper()
	if !strings.HasPrefix(stderr, "go-ask hook: ") || !strings.HasSuffix(stderr, "\n") || strings.Count(stderr, "\n") != 1 {
		t.Errorf("want one \"go-ask hook: ...\" line on stderr, got %q", stderr)
	}
}

func jsonString(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		panic(err)
	}
	return string(b)
}
