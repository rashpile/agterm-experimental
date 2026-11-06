package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
)

func TestStateDir(t *testing.T) {
	t.Setenv("GO_ASK_STATE_DIR", "/tmp/elsewhere")
	if got := stateDir(); got != "/tmp/elsewhere" {
		t.Errorf("with GO_ASK_STATE_DIR: %q", got)
	}
	t.Setenv("GO_ASK_STATE_DIR", "")
	t.Setenv("HOME", "/Users/someone")
	if got := stateDir(); got != "/Users/someone/.local/state/go-ask" {
		t.Errorf("default: %q", got)
	}
}

// wrapPasted is how Claude delivers a long paste (measured on Claude Code 2.1.319).
func wrapPasted(id, text string) string {
	return "\n\n<pasted_content id=\"" + id + "\">\n" + text + "\n</pasted_content id=\"" + id + "\">\n"
}

func TestNormalizePrompt(t *testing.T) {
	tests := []struct {
		name, in, want string
	}{
		{"measured long-paste wrapper", wrapPasted("f033", "first\n    second"), "first\n    second"},
		{"two wrapped blocks",
			"intro\n<pasted_content id=\"a1\">\none\n</pasted_content id=\"a1\">\nmiddle\n" +
				"<pasted_content id=\"b2\">\ntwo\n</pasted_content id=\"b2\">",
			"intro\none\nmiddle\ntwo"},
		{"opening tag without closing tag stays",
			"<pasted_content id=\"zz\">\nbody without end", "<pasted_content id=\"zz\">\nbody without end"},
		{"closing tag of another id stays",
			"<pasted_content id=\"a\">\nx\n</pasted_content id=\"b\">", "<pasted_content id=\"a\">\nx\n</pasted_content id=\"b\">"},
		{"unclosed tag before a closed block",
			"<pasted_content id=\"q\">\nkeep\n<pasted_content id=\"r\">\nbody\n</pasted_content id=\"r\">",
			"<pasted_content id=\"q\">\nkeep\nbody"},
		{"block inside a wrapped paste",
			wrapPasted("out", "<pasted_content id=\"in\">\ninner\n</pasted_content id=\"in\">"), "inner"},
		{"tabs become 4 spaces", "a\tb\n\tc", "a    b\n    c"},
		{"carriage returns dropped", "a\r\nb\rc\r", "a\nbc"},
		{"trailing spaces and tabs trimmed per line", "a  \nb \t\nc", "a\nb\nc"},
		{"whole text trimmed", "\n\n  hi there \n\n", "hi there"},
		{"decomposed e-acute kept", "café", "café"},
		{"short multi-line text unchanged", "first line\n  indented second\n\nthird", "first line\n  indented second\n\nthird"},
		{"empty", "", ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := normalizePrompt(tc.in); got != tc.want {
				t.Errorf("normalizePrompt(%q)\n got %q\nwant %q", tc.in, got, tc.want)
			}
		})
	}
}

// TestPromptHashMatchesClaude: the text ask sends and the prompt Claude's hook receives for it
// hash the same.
func TestPromptHashMatchesClaude(t *testing.T) {
	sent := "Review this:\r\n\tfunc main() {}  \n" + strings.Repeat("long line of text ", 40)
	claude := wrapPasted("f033", "Review this:\n    func main() {}  \n"+strings.Repeat("long line of text ", 40))
	if promptHash(sent) != promptHash(claude) {
		t.Errorf("hash differs:\n sent   %q\n claude %q", normalizePrompt(sent), normalizePrompt(claude))
	}
	short := "say ALPHA\n\tand stop"
	if promptHash(short) != promptHash("say ALPHA\n    and stop") {
		t.Error("short text with a tab: hash differs from Claude's form")
	}
}

func TestPromptHash(t *testing.T) {
	// sha256 of the empty string.
	if got := promptHash("  \n"); got != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" {
		t.Errorf("hash of blank text: %s", got)
	}
	if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(promptHash("hello")) {
		t.Errorf("not 64 lowercase hex: %s", promptHash("hello"))
	}
	if promptHash("say ALPHA") == promptHash("say BETA") {
		t.Error("two different texts hash the same")
	}
	if promptHash("café") == promptHash("café") {
		t.Error("decomposed and precomposed e-acute hash the same; no Unicode normalization is expected")
	}
}

func TestJournalPath(t *testing.T) {
	tests := []struct{ id, want string }{
		{"8F2C1A90-77AB-4C1D-9E21-0B5F3D6A1C44", "8F2C1A90-77AB-4C1D-9E21-0B5F3D6A1C44.jsonl"},
		{"AB-12:right/../x", "AB-12-right----x.jsonl"},
		{"pé id", "p--id.jsonl"},
	}
	for _, tc := range tests {
		if got := journalPath("/state", tc.id); got != filepath.Join("/state", "turns", tc.want) {
			t.Errorf("journalPath(%q) = %q", tc.id, got)
		}
	}
}

func strp(s string) *string { return &s }

func TestJournalRoundTrip(t *testing.T) {
	path := journalPath(t.TempDir(), "pane-1")
	recs := []turnRecord{
		{Event: "prompt", T: 1000, Session: "s1", PromptID: "p1", Hash: promptHash("say ALPHA")},
		{Event: "stop", T: 2000, Session: "s1", PromptID: "p1", Reply: strp("ALPHA")},
		{Event: "prompt", T: 3000, Session: "s1", Hash: promptHash("again")},
		{Event: "stop", T: 4000, Session: "s1", Reply: strp(""), StopHookActive: true},
	}
	for _, r := range recs {
		if err := appendTurn(path, r); err != nil {
			t.Fatal(err)
		}
	}
	got, err := readTurns(path)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, recs) {
		t.Fatalf("read back\n got %+v\nwant %+v", got, recs)
	}
	if got[3].Reply == nil || *got[3].Reply != "" {
		t.Errorf("empty reply not kept: %+v", got[3])
	}
	if got[0].Reply != nil {
		t.Errorf("prompt record gained a reply: %+v", got[0])
	}
	raw, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(lines) != 4 {
		t.Fatalf("want 4 lines, got %q", raw)
	}
	if strings.Contains(lines[0], `"reply"`) || !strings.Contains(lines[3], `"reply":""`) {
		t.Errorf("reply field on the wrong records:\n%s", raw)
	}
	if !strings.Contains(lines[0], `"prompt_id":"p1"`) || strings.Contains(lines[2], `"prompt_id"`) {
		t.Errorf("prompt_id field on the wrong records:\n%s", raw)
	}
	if strings.Contains(lines[1], `"stop_hook_active"`) || !strings.Contains(lines[3], `"stop_hook_active":true`) {
		t.Errorf("stop_hook_active field on the wrong records:\n%s", raw)
	}
}

func TestJournalInterleavedSessions(t *testing.T) {
	path := journalPath(t.TempDir(), "pane-1")
	recs := []turnRecord{
		{Event: "prompt", T: 1, Session: "outer", Hash: promptHash("outer")},
		{Event: "prompt", T: 2, Session: "nested", Hash: promptHash("nested")},
		{Event: "stop", T: 3, Session: "nested", Reply: strp("nested reply")},
		{Event: "stop", T: 4, Session: "outer", Reply: strp("outer reply")},
	}
	for _, r := range recs {
		if err := appendTurn(path, r); err != nil {
			t.Fatal(err)
		}
	}
	got, err := readTurns(path)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, recs) {
		t.Fatalf("read back\n got %+v\nwant %+v", got, recs)
	}
}

func TestReadTurnsSkipsBadLines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "j.jsonl")
	data := `{"event":"prompt","t":1,"session":"s","hash":"h"}` + "\n" +
		"not json\n" +
		`{"event":"stop","t":` + "\n" +
		"\n" +
		`[1,2]` + "\n" +
		`{"event":"stop","t":2,"session":"s","reply":"ok"}`
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := readTurns(path)
	if err != nil {
		t.Fatal(err)
	}
	want := []turnRecord{
		{Event: "prompt", T: 1, Session: "s", Hash: "h"},
		{Event: "stop", T: 2, Session: "s", Reply: strp("ok")},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v, want %+v", got, want)
	}
}

func TestReadTurnsMissingAndUnreadable(t *testing.T) {
	dir := t.TempDir()
	got, err := readTurns(filepath.Join(dir, "turns", "none.jsonl"))
	if err != nil || got != nil {
		t.Errorf("missing file: %+v, %v", got, err)
	}
	if _, err := readTurns(dir); err == nil {
		t.Error("a directory in the journal's place: want an error")
	}
}

func TestJournalModes(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "state")
	path := journalPath(dir, "pane-1")
	if err := appendTurn(path, turnRecord{Event: "prompt", T: 1, Session: "s", Hash: "h"}); err != nil {
		t.Fatal(err)
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("journal mode %v, want 0600", fi.Mode().Perm())
	}
	for _, d := range []string{dir, filepath.Dir(path)} {
		di, err := os.Stat(d)
		if err != nil {
			t.Fatal(err)
		}
		if di.Mode().Perm() != 0o700 {
			t.Errorf("%s mode %v, want 0700", d, di.Mode().Perm())
		}
	}
}

func TestJournalNoTrimAtLimit(t *testing.T) {
	path := journalPath(t.TempDir(), "pane-1")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	line := strings.Repeat("x", 1023) + "\n"
	old := strings.Repeat(line, journalMaxSize/len(line))
	if len(old) != journalMaxSize {
		t.Fatalf("fixture size %d", len(old))
	}
	if err := os.WriteFile(path, []byte(old), 0o600); err != nil {
		t.Fatal(err)
	}
	rec := turnRecord{Event: "prompt", T: 1, Session: "s", Hash: "h"}
	if err := appendTurn(path, rec); err != nil {
		t.Fatal(err)
	}
	fi, _ := os.Stat(path)
	if fi.Size() <= journalMaxSize {
		t.Fatalf("a journal of exactly %d bytes was trimmed: now %d", journalMaxSize, fi.Size())
	}
	// Now past the limit: the next append trims.
	if err := appendTurn(path, rec); err != nil {
		t.Fatal(err)
	}
	fi, _ = os.Stat(path)
	if fi.Size() > journalKeepSize+1024 {
		t.Errorf("journal past the limit was not trimmed: %d bytes", fi.Size())
	}
}

func TestJournalTrim(t *testing.T) {
	path := journalPath(t.TempDir(), "pane-1")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	// Lines of uneven length, past 1 MiB in total.
	var old bytes.Buffer
	var lines [][]byte
	for i := 0; old.Len() <= journalMaxSize; i++ {
		b, _ := json.Marshal(turnRecord{Event: "stop", T: int64(i), Session: "s", Reply: strp(strings.Repeat("r", 600+i%7*137))})
		b = append(b, '\n')
		lines = append(lines, b)
		old.Write(b)
	}
	if err := os.WriteFile(path, old.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	// The newest whole lines that fit in journalKeepSize.
	keep, size := len(lines), 0
	for keep > 0 && size+len(lines[keep-1]) <= journalKeepSize {
		keep--
		size += len(lines[keep])
	}
	newRec := turnRecord{Event: "prompt", T: 999999, Session: "s", Hash: "h"}
	if err := appendTurn(path, newRec); err != nil {
		t.Fatal(err)
	}

	newLine, _ := json.Marshal(newRec)
	want := append(bytes.Join(lines[keep:], nil), append(newLine, '\n')...)
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("trimmed journal: got %d bytes, want %d (newest %d of %d lines plus the new one)", len(got), len(want), len(lines)-keep, len(lines))
	}
	if size > journalKeepSize || size+len(lines[keep-1]) <= journalKeepSize {
		t.Fatalf("fixture: kept %d bytes is not the largest whole-line suffix within %d", size, journalKeepSize)
	}
	recs, err := readTurns(path)
	if err != nil {
		t.Fatal(err)
	}
	if first, last := recs[0], recs[len(recs)-1]; first.T != int64(keep) || last != newRec {
		t.Errorf("records after trim: first t=%d (want %d), last %+v", first.T, keep, last)
	}
	fi, _ := os.Stat(path)
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("trimmed journal mode %v, want 0600", fi.Mode().Perm())
	}
	entries, _ := os.ReadDir(filepath.Dir(path))
	if len(entries) != 1 {
		t.Errorf("temp file left behind: %v", entries)
	}
}

func TestAppendTurnUnwritable(t *testing.T) {
	dir := t.TempDir()
	// A file where the turns dir should be.
	if err := os.WriteFile(filepath.Join(dir, "turns"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := appendTurn(journalPath(dir, "pane-1"), turnRecord{Event: "prompt", T: 1, Session: "s"}); err == nil {
		t.Error("want an error when the turns dir cannot be created")
	}
}
