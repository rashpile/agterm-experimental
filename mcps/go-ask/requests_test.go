package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testRequest(id string) request {
	return request{
		ID:     id,
		Target: "AAAA1111-0000:right",
		PaneID: "PANE-A1-RIGHT",
		Name:   "agent",
		Agent:  "claude",
		Hash:   promptHash("say ALPHA"),
		SentAt: 1793987000000,
	}
}

func TestNewRequestID(t *testing.T) {
	seen := map[string]bool{}
	for range 100 {
		id, err := newRequestID()
		if err != nil {
			t.Fatal(err)
		}
		if !requestIDPattern.MatchString(id) {
			t.Fatalf("id %q is not 12 lowercase hex", id)
		}
		if seen[id] {
			t.Fatalf("id %q repeated", id)
		}
		seen[id] = true
	}
}

func TestRequestRoundTrip(t *testing.T) {
	dir := t.TempDir()
	r := testRequest("0123456789ab")
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	got, err := loadRequest(dir, r.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got != r {
		t.Errorf("round trip\n got %+v\nwant %+v", got, r)
	}

	// Storing a final state overwrites the file in place.
	r.State, r.Reply, r.FinishedAt = "done", "ALPHA", 1793987004567
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	if got, err := loadRequest(dir, r.ID); err != nil || got != r {
		t.Errorf("after storing the result: %+v, %v", got, err)
	}

	entries, err := os.ReadDir(filepath.Join(dir, "requests"))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != "0123456789ab.json" {
		t.Errorf("requests dir holds %v, want only 0123456789ab.json (no temp files)", entries)
	}
}

func TestRequestFileFields(t *testing.T) {
	dir := t.TempDir()
	if err := saveRequest(dir, testRequest("0123456789ab")); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(dir, "requests", "0123456789ab.json"))
	if err != nil {
		t.Fatal(err)
	}
	// An undelivered request writes its empty state, reply and finished_at too.
	for _, field := range []string{`"id":"0123456789ab"`, `"target":"AAAA1111-0000:right"`, `"pane_id":"PANE-A1-RIGHT"`,
		`"name":"agent"`, `"agent":"claude"`, `"hash":"` + promptHash("say ALPHA") + `"`, `"sent_at":1793987000000`,
		`"state":""`, `"reply":""`, `"finished_at":0`} {
		if !strings.Contains(string(data), field) {
			t.Errorf("request file lacks %s:\n%s", field, data)
		}
	}
}

func TestRequestModes(t *testing.T) {
	dir := t.TempDir()
	if err := saveRequest(dir, testRequest("0123456789ab")); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(filepath.Join(dir, "requests", "0123456789ab.json"))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("request file mode %v, want 0600", fi.Mode().Perm())
	}
	di, err := os.Stat(filepath.Join(dir, "requests"))
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode().Perm() != 0o700 {
		t.Errorf("requests dir mode %v, want 0700", di.Mode().Perm())
	}
}

func TestRequestBadID(t *testing.T) {
	dir := t.TempDir()
	// A loadable request one level up: "../x" must not reach it.
	if err := os.WriteFile(filepath.Join(dir, "x.json"), []byte(`{"id":"x","state":"done"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"../x", "ABCDEF012345", "0123456789a", "0123456789abc", "0123456789ag", "", "0123456789ab/"} {
		if _, err := loadRequest(dir, id); err == nil || !strings.Contains(err.Error(), "invalid request_id") {
			t.Errorf("loadRequest(%q): %v, want an invalid request_id error", id, err)
		}
		if err := saveRequest(dir, testRequest(id)); err == nil || !strings.Contains(err.Error(), "invalid request_id") {
			t.Errorf("saveRequest(%q): %v, want an invalid request_id error", id, err)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "requests")); err == nil {
		t.Error("a refused save created the requests dir")
	}
}

func TestLoadRequestErrors(t *testing.T) {
	dir := t.TempDir()
	if _, err := loadRequest(dir, "0123456789ab"); err == nil || !strings.Contains(err.Error(), `unknown request_id "0123456789ab"`) {
		t.Errorf("unknown id: %v", err)
	}
	reqDir := filepath.Join(dir, "requests")
	if err := os.MkdirAll(filepath.Join(reqDir, "aaaaaaaaaaaa.json"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := loadRequest(dir, "aaaaaaaaaaaa"); err == nil || !strings.Contains(err.Error(), "read request aaaaaaaaaaaa") {
		t.Errorf("unreadable request: %v", err)
	}
	if err := os.WriteFile(filepath.Join(reqDir, "bbbbbbbbbbbb.json"), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadRequest(dir, "bbbbbbbbbbbb"); err == nil || !strings.Contains(err.Error(), "parse request bbbbbbbbbbbb") {
		t.Errorf("corrupt request: %v", err)
	}
}

func TestSaveRequestErrors(t *testing.T) {
	// A file where the state dir should be: the requests dir cannot be created.
	fileDir := filepath.Join(t.TempDir(), "state")
	if err := os.WriteFile(fileDir, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := saveRequest(fileDir, testRequest("0123456789ab")); err == nil {
		t.Error("save under a file succeeded")
	}
	if err := saveRequest("", testRequest("0123456789ab")); err == nil || !strings.Contains(err.Error(), "no state dir") {
		t.Errorf("save without a state dir: %v", err)
	}
}
