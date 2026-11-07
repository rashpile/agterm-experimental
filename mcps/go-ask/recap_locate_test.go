package main

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// locatorFixture is a temp ~/.claude with a fake ps that answers from env: pid → its command
// line plus environment; a pid missing from env is dead.
type locatorFixture struct {
	loc     claudeLocator
	env     map[int]string
	psCalls [][]int
	psErr   error
}

func newLocatorFixture(t *testing.T) *locatorFixture {
	t.Helper()
	root := t.TempDir()
	f := &locatorFixture{env: map[int]string{}}
	f.loc = claudeLocator{
		sessionsDir: filepath.Join(root, "sessions"),
		projectsDir: filepath.Join(root, "projects"),
		ps: func(_ context.Context, pids []int) (map[int]string, error) {
			f.psCalls = append(f.psCalls, slices.Clone(pids))
			if f.psErr != nil {
				return nil, f.psErr
			}
			out := map[int]string{}
			for _, p := range pids {
				if line, ok := f.env[p]; ok {
					out[p] = line
				}
			}
			return out, nil
		},
	}
	for _, d := range []string{f.loc.sessionsDir, f.loc.projectsDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return f
}

// claude writes the session record of a claude process and, when env is not "", makes the
// process live with that environment.
func (f *locatorFixture) claude(t *testing.T, pid int, sessionID string, updatedAt int64, env string) {
	t.Helper()
	body := fmt.Sprintf(`{"pid":%d,"sessionId":%q,"cwd":"/x","kind":"interactive","updatedAt":%d,"status":"idle"}`, pid, sessionID, updatedAt)
	f.file(t, fmt.Sprintf("%d.json", pid), body)
	if env != "" {
		f.env[pid] = "claude --resume x " + env
	}
}

func (f *locatorFixture) file(t *testing.T, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(f.loc.sessionsDir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// transcript creates projects/<project>/<sessionID>.jsonl and returns its path.
func (f *locatorFixture) transcript(t *testing.T, project, sessionID string) string {
	t.Helper()
	dir := filepath.Join(f.loc.projectsDir, project)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, sessionID+".jsonl")
	if err := os.WriteFile(p, []byte("{}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func paneEnv(paneID string, extra ...string) string {
	return strings.Join(append([]string{"AGTERM_ENABLED=1", "AGTERM_PANE=left", "AGTERM_PANE_ID=" + paneID,
		"AGTERM_SOCKET=/Users/u/Library/Application Support/agterm/agterm.sock"}, extra...), " ")
}

func TestTranscriptForPicksThePane(t *testing.T) {
	f := newLocatorFixture(t)
	// one agterm session split in two: a claude in each pane, distinct pane ids
	f.claude(t, 100, "sess-left", 1000, paneEnv("PANE-LEFT"))
	f.claude(t, 200, "sess-right", 2000, paneEnv("PANE-RIGHT", "AGTERM_PANE=right"))
	left := f.transcript(t, "-Users-u-proj", "sess-left")
	right := f.transcript(t, "-Users-u-proj", "sess-right")

	for _, tc := range []struct{ pane, id, path string }{
		{"PANE-LEFT", "sess-left", left},
		{"PANE-RIGHT", "sess-right", right},
	} {
		id, path, err := f.loc.transcriptFor(context.Background(), tc.pane)
		if err != nil {
			t.Fatalf("%s: %v", tc.pane, err)
		}
		if id != tc.id || path != tc.path {
			t.Errorf("%s: got %s %s, want %s %s", tc.pane, id, path, tc.id, tc.path)
		}
	}
	if len(f.psCalls) != 2 || !slices.Equal(sorted(f.psCalls[0]), []int{100, 200}) {
		t.Errorf("ps calls = %v, want one call over [100 200] per lookup", f.psCalls)
	}
}

func TestTranscriptForWholeToken(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "longer-id", 3000, paneEnv("ABCD"))
	f.claude(t, 101, "prefixed-name", 3000, "X_AGTERM_PANE_ID=ABC HOME=/u")
	f.claude(t, 102, "in-value", 3000, "NOTE=AGTERM_PANE_ID=ABC HOME=/u")
	f.transcript(t, "p", "longer-id")
	f.transcript(t, "p", "prefixed-name")
	f.transcript(t, "p", "in-value")
	if _, _, err := f.loc.transcriptFor(context.Background(), "ABC"); !errors.Is(err, errNoClaude) {
		t.Fatalf("pane ABC: err = %v, want errNoClaude", err)
	}

	f.claude(t, 103, "exact", 1000, "AGTERM_PANE_ID=ABC") // the whole line, start and end
	path := f.transcript(t, "p", "exact")
	id, got, err := f.loc.transcriptFor(context.Background(), "ABC")
	if err != nil || id != "exact" || got != path {
		t.Fatalf("pane ABC: got %s %s %v, want exact %s", id, got, err, path)
	}
}

func TestTranscriptForSkipsChildClaude(t *testing.T) {
	// a claude started from the pane's claude: newer, same pane id, and the markers it inherits
	for _, tc := range []struct {
		name  string
		extra []string
	}{
		// revmux strips CLAUDECODE before it starts a reviewer (measured: claude --print, pid
		// under the pane's claude -> zsh -> revmux) and keeps CLAUDE_CODE_CHILD_SESSION
		{"revmux reviewer", []string{"CLAUDE_CODE_CHILD_SESSION=1", "CLAUDE_CODE_ENTRYPOINT=cli"}},
		{"CLAUDECODE only", []string{"CLAUDECODE=1", "CLAUDE_CODE_ENTRYPOINT=sdk-ts"}},
		{"both markers", []string{"CLAUDECODE=1", "CLAUDE_CODE_CHILD_SESSION=1", "CLAUDE_CODE_ENTRYPOINT=cli"}},
	} {
		f := newLocatorFixture(t)
		f.claude(t, 100, "parent", 1000, paneEnv("PANE-1"))
		f.claude(t, 300, "child", 9000, paneEnv("PANE-1", tc.extra...))
		parent := f.transcript(t, "p", "parent")
		f.transcript(t, "p", "child")
		id, path, err := f.loc.transcriptFor(context.Background(), "PANE-1")
		if err != nil || id != "parent" || path != parent {
			t.Errorf("%s: got %s %s %v, want the parent %s", tc.name, id, path, err, parent)
		}

		// a pane with only a child live (its parent exited): no claude of its own
		f = newLocatorFixture(t)
		f.claude(t, 300, "child", 9000, paneEnv("PANE-1", tc.extra...))
		f.transcript(t, "p", "child")
		if id, _, err := f.loc.transcriptFor(context.Background(), "PANE-1"); !errors.Is(err, errNoClaude) {
			t.Errorf("%s alone: got %s %v, want errNoClaude", tc.name, id, err)
		}
	}

	// a marker only counts with the value 1 as a whole token: such a claude is still the pane's own
	for _, extra := range []string{"CLAUDECODE=10", "CLAUDE_CODE_CHILD_SESSION=10", "X_CLAUDE_CODE_CHILD_SESSION=1", "NOTE=CLAUDECODE=1"} {
		f := newLocatorFixture(t)
		f.claude(t, 100, "parent", 1000, paneEnv("PANE-1"))
		f.claude(t, 301, "not-child", 9500, paneEnv("PANE-1", extra))
		f.transcript(t, "p", "parent")
		f.transcript(t, "p", "not-child")
		if id, _, err := f.loc.transcriptFor(context.Background(), "PANE-1"); err != nil || id != "not-child" {
			t.Errorf("%s: got %s %v, want not-child", extra, id, err)
		}
	}
}

func TestIsChildClaude(t *testing.T) {
	for _, tc := range []struct {
		line string
		want bool
	}{
		{"", false},
		{"claude " + paneEnv("P"), false},
		{"claude " + paneEnv("P", "CLAUDECODE=1"), true},
		{"claude --print " + paneEnv("P", "CLAUDE_CODE_CHILD_SESSION=1"), true},
		{"claude CLAUDECODE=1 CLAUDE_CODE_CHILD_SESSION=1", true},
		{"claude CLAUDE_CODE_CHILD_SESSION=1", true}, // at the line's end
		{"claude CLAUDECODE=10 CLAUDE_CODE_CHILD_SESSION=10", false},
		{"claude CLAUDECODE=0 CLAUDE_CODE_CHILD_SESSION=", false},
		{"claude X_CLAUDE_CODE_CHILD_SESSION=1 NOTE=CLAUDECODE=1", false},
	} {
		if got := isChildClaude(tc.line); got != tc.want {
			t.Errorf("isChildClaude(%q) = %v, want %v", tc.line, got, tc.want)
		}
	}
}

func TestTranscriptForNewestLive(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "old", 1000, paneEnv("PANE-1"))
	f.claude(t, 101, "new", 2000, paneEnv("PANE-1"))
	f.claude(t, 102, "dead", 9000, "") // a stale record: the process is gone
	f.claude(t, 103, "other-pane", 9000, paneEnv("PANE-2"))
	for _, s := range []string{"old", "new", "dead", "other-pane"} {
		f.transcript(t, "p", s)
	}
	id, _, err := f.loc.transcriptFor(context.Background(), "PANE-1")
	if err != nil || id != "new" {
		t.Fatalf("got %s %v, want new", id, err)
	}
	if got := sorted(f.psCalls[0]); !slices.Equal(got, []int{100, 101, 102, 103}) {
		t.Errorf("ps pids = %v, want every record's pid", got)
	}
}

func TestTranscriptForSkipsBadRecords(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "good", 1000, paneEnv("PANE-1"))
	f.transcript(t, "p", "good")
	// newer records for the same pane that must not count
	f.file(t, "101.json", `{"pid":101,"sessionId":"","updatedAt":5000}`)
	f.file(t, "102.json", `{"pid":102,"updatedAt":5000}`)
	f.file(t, "103.json", `{"pid":103,"sessionId":"broken","updatedAt":`)
	f.file(t, "104.json", `{"pid":104,"sessionId":"bad-type","updatedAt":"soon"}`)
	f.file(t, ".json", `{"pid":110,"sessionId":"no-stem","updatedAt":5000}`)
	f.file(t, "abc.json", `{"pid":105,"sessionId":"named","updatedAt":5000}`)
	f.file(t, "106.json.tmp", `{"pid":106,"sessionId":"tmp","updatedAt":5000}`)
	f.file(t, "107.4fa5aaf0.key", "key")
	f.file(t, "-108.json", `{"pid":108,"sessionId":"negative","updatedAt":5000}`)
	f.file(t, "0.json", `{"pid":0,"sessionId":"zero","updatedAt":5000}`)
	f.file(t, "99999999999999999999.json", `{"sessionId":"overflow","updatedAt":5000}`)
	if err := os.Mkdir(filepath.Join(f.loc.sessionsDir, "109.json"), 0o755); err != nil {
		t.Fatal(err)
	}
	// listed but gone when read: a claude that exited between the listing and the read
	if err := os.Symlink(filepath.Join(f.loc.sessionsDir, "gone"), filepath.Join(f.loc.sessionsDir, "111.json")); err != nil {
		t.Fatal(err)
	}
	for _, p := range []int{101, 102, 103, 104, 105, 106, 107, 108, 109, 111} {
		f.env[p] = paneEnv("PANE-1")
	}

	id, _, err := f.loc.transcriptFor(context.Background(), "PANE-1")
	if err != nil || id != "good" {
		t.Fatalf("got %s %v, want good", id, err)
	}
	if len(f.psCalls) != 1 || !slices.Equal(f.psCalls[0], []int{100}) {
		t.Errorf("ps calls = %v, want only pid 100", f.psCalls)
	}
}

func TestTranscriptForNoRecords(t *testing.T) {
	f := newLocatorFixture(t)
	f.file(t, "abc.json", `{"sessionId":"named","updatedAt":5000}`)
	if _, _, err := f.loc.transcriptFor(context.Background(), "PANE-1"); !errors.Is(err, errNoClaude) {
		t.Errorf("no records: err = %v, want errNoClaude", err)
	}
	f.loc.sessionsDir = filepath.Join(t.TempDir(), "missing")
	if _, _, err := f.loc.transcriptFor(context.Background(), "PANE-1"); !errors.Is(err, errNoClaude) {
		t.Errorf("missing dir: err = %v, want errNoClaude", err)
	}
	if len(f.psCalls) != 0 {
		t.Errorf("ps called %v, want no call without records", f.psCalls)
	}
}

func TestTranscriptForNoMatch(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "other", 1000, paneEnv("PANE-2"))
	f.claude(t, 101, "dead", 1000, "")
	f.transcript(t, "p", "other")
	_, _, err := f.loc.transcriptFor(context.Background(), "PANE-1")
	if !errors.Is(err, errNoClaude) {
		t.Errorf("err = %v, want errNoClaude", err)
	}
}

func TestTranscriptForNoTranscript(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "0963d564-3e60-4fb2-8633-614a43eb94f2", 1000, paneEnv("PANE-1"))
	f.transcript(t, "p", "some-other-session")
	id, path, err := f.loc.transcriptFor(context.Background(), "PANE-1")
	if !errors.Is(err, errNoTranscript) {
		t.Fatalf("err = %v, want errNoTranscript", err)
	}
	want := "no transcript on disk for Claude session 0963d564-3e60-4fb2-8633-614a43eb94f2"
	if err.Error() != want || id != "0963d564-3e60-4fb2-8633-614a43eb94f2" || path != "" {
		t.Errorf("got %q, %q, %q; want %q, the session id, no path", err, id, path, want)
	}
}

func TestTranscriptForFirstSortedHit(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "sess", 1000, paneEnv("PANE-1"))
	f.transcript(t, "-Users-u-zeta", "sess")
	first := f.transcript(t, "-Users-u-alpha", "sess")
	_, path, err := f.loc.transcriptFor(context.Background(), "PANE-1")
	if err != nil || path != first {
		t.Errorf("got %s %v, want %s", path, err, first)
	}
}

func TestTranscriptForErrors(t *testing.T) {
	f := newLocatorFixture(t)
	f.claude(t, 100, "sess", 1000, paneEnv("PANE-1"))
	f.transcript(t, "p", "sess")

	if _, _, err := f.loc.transcriptFor(context.Background(), ""); !errors.Is(err, errNoPaneID) {
		t.Errorf("empty pane id: err = %v, want errNoPaneID", err)
	}
	if len(f.psCalls) != 0 {
		t.Errorf("ps called %v for an empty pane id", f.psCalls)
	}

	f.psErr = errors.New("ps: boom")
	if _, _, err := f.loc.transcriptFor(context.Background(), "PANE-1"); err == nil || err.Error() != "ps: boom" {
		t.Errorf("ps failing: err = %v, want ps: boom", err)
	}

	f.loc.sessionsDir = filepath.Join(f.loc.sessionsDir, "100.json") // a file, not a dir
	_, _, err := f.loc.transcriptFor(context.Background(), "PANE-1")
	if err == nil || errors.Is(err, errNoClaude) || !strings.Contains(err.Error(), "cannot list Claude sessions") {
		t.Errorf("unreadable sessions dir: err = %v", err)
	}
}

func TestNewClaudeLocator(t *testing.T) {
	t.Setenv("HOME", "/Users/someone")
	l := newClaudeLocator()
	if l.sessionsDir != "/Users/someone/.claude/sessions" || l.projectsDir != "/Users/someone/.claude/projects" || l.ps == nil {
		t.Errorf("newClaudeLocator() = %+v", l)
	}
}

func TestHasToken(t *testing.T) {
	for _, tc := range []struct {
		s, tok string
		want   bool
	}{
		{"AGTERM_PANE_ID=ABC", "AGTERM_PANE_ID=ABC", true},
		{"claude AGTERM_PANE_ID=ABC HOME=/u", "AGTERM_PANE_ID=ABC", true},
		{"claude HOME=/u AGTERM_PANE_ID=ABC", "AGTERM_PANE_ID=ABC", true},
		{"claude AGTERM_PANE_ID=ABCD", "AGTERM_PANE_ID=ABC", false},
		{"claude X_AGTERM_PANE_ID=ABC", "AGTERM_PANE_ID=ABC", false},
		{"claude X_AGTERM_PANE_ID=ABC AGTERM_PANE_ID=ABC", "AGTERM_PANE_ID=ABC", true},
		{"claude AGTERM_PANE_ID=ABCD AGTERM_PANE_ID=ABC", "AGTERM_PANE_ID=ABC", true},
		{"CLAUDECODE=10 CLAUDECODE=1", "CLAUDECODE=1", true},
		{"CLAUDECODE=10", "CLAUDECODE=1", false},
		{"AGTERM_PANE_ID=AB", "AGTERM_PANE_ID=ABC", false},
		{"", "CLAUDECODE=1", false},
	} {
		if got := hasToken(tc.s, tc.tok); got != tc.want {
			t.Errorf("hasToken(%q, %q) = %v, want %v", tc.s, tc.tok, got, tc.want)
		}
	}
}

func TestParsePS(t *testing.T) {
	for _, tc := range []struct {
		name, out string
		want      map[int]string
	}{
		{"empty", "", map[int]string{}},
		{"padded pids", "    1 /sbin/launchd\n  412 claude AGTERM_PANE_ID=A HOME=/u\n40596 claude --resume x AGTERM_PANE_ID=B\n",
			map[int]string{1: "/sbin/launchd", 412: "claude AGTERM_PANE_ID=A HOME=/u", 40596: "claude --resume x AGTERM_PANE_ID=B"}},
		{"no env and blank lines", "\n  99 /bin/sleep 30\n\n   \n", map[int]string{99: "/bin/sleep 30"}},
		{"value with spaces", "7 claude AGTERM_SOCKET=/Users/u/Library/Application Support/agterm.sock AGTERM_PANE_ID=C",
			map[int]string{7: "claude AGTERM_SOCKET=/Users/u/Library/Application Support/agterm.sock AGTERM_PANE_ID=C"}},
		{"lines without a pid", "ps: something\n  12\nabc def\n  13 x\n", map[int]string{12: "", 13: "x"}},
	} {
		if got := parsePS([]byte(tc.out)); !maps.Equal(got, tc.want) {
			t.Errorf("%s: parsePS = %v, want %v", tc.name, got, tc.want)
		}
	}
}

// TestPSHelperProcess is not a test: TestRunPS starts the test binary again with
// GO_ASK_PS_HELPER=1 to get a live process with a known environment. /bin/sleep cannot serve:
// ps shows no environment for Apple platform binaries.
func TestPSHelperProcess(t *testing.T) {
	if os.Getenv("GO_ASK_PS_HELPER") != "1" {
		return
	}
	time.Sleep(time.Minute)
	os.Exit(0)
}

func TestRunPS(t *testing.T) {
	live := exec.Command(os.Args[0], "-test.run=^TestPSHelperProcess$")
	live.Env = []string{"GO_ASK_PS_HELPER=1", "AGTERM_PANE_ID=TEST"}
	if err := live.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		live.Process.Kill()
		live.Wait()
	})
	done := exec.Command("/usr/bin/true")
	if err := done.Run(); err != nil {
		t.Fatal(err)
	}
	dead := done.ProcessState.Pid()

	got, err := runPS(context.Background(), []int{live.Process.Pid, dead})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || !hasToken(got[live.Process.Pid], "AGTERM_PANE_ID=TEST") {
		t.Errorf("runPS(live, dead) = %v, want only pid %d with AGTERM_PANE_ID=TEST", got, live.Process.Pid)
	}

	got, err = runPS(context.Background(), []int{dead})
	if err != nil || len(got) != 0 {
		t.Errorf("runPS(dead) = %v, %v; want an empty map and no error", got, err)
	}
}

func TestRunPSErrors(t *testing.T) {
	// ps refuses a pid above the system's limit with a message on stderr
	if _, err := runPS(context.Background(), []int{999999999}); err == nil || !strings.HasPrefix(err.Error(), "ps: ") {
		t.Errorf("pid too large: err = %v, want ps's own message", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := runPS(ctx, []int{os.Getpid()}); !errors.Is(err, context.Canceled) {
		t.Errorf("cancelled ctx: err = %v, want context.Canceled", err)
	}
}

func sorted(s []int) []int {
	s = slices.Clone(s)
	slices.Sort(s)
	return s
}
