package main

import (
	"encoding/json"
	"strings"
	"testing"
)

const treeJSON = `{"workspaces":[
 {"name":"ops","active":true,"sessions":[
  {"id":"AAAA1111-0000","name":"agent","cwd":"/a","status":"idle",
   "foreground":["claude","--resume","x"],"surfaces":[{"kind":"left","paneID":"PANE-A1-LEFT"}]},
  {"id":"BBBB2222-0000","name":"sh:kobo","cwd":"/b","status":"active","statusPane":"right","active":true,
   "foreground":["/bin/bash","/x/nvim-tree.sh"],"splitForeground":["zsh","-ilc","codex"],
   "surfaces":[{"kind":"left","paneID":"PANE-B2-LEFT"},{"kind":"scratch","paneID":"PANE-B2-SCRATCH"},
    {"kind":"right","active":true,"paneID":"PANE-B2-RIGHT"}]},
  {"id":"AAAA3333-0000","name":"shell","cwd":"/c","surfaces":[{"kind":"left","paneID":"PANE-A3-LEFT"}]}
 ]},
 {"name":"me","sessions":[
  {"id":"CCCC4444-0000","name":"agent","cwd":"/d","foreground":["claude"],"active":true,
   "surfaces":[{"kind":"left","active":true,"paneID":"PANE-C4-LEFT"}]}
 ]}
]}`

func testPanes(t *testing.T, self selfAddr) []Pane {
	t.Helper()
	var tr rawTree
	if err := json.Unmarshal([]byte(treeJSON), &tr); err != nil {
		t.Fatal(err)
	}
	return panesFromTree(tr, self)
}

func TestPanesFromTree(t *testing.T) {
	panes := testPanes(t, selfAddr{SessionID: "CCCC4444-0000", Pane: "left"})
	if len(panes) != 5 {
		t.Fatalf("want 5 panes, got %d: %+v", len(panes), panes)
	}
	right := panes[2]
	if right.Target != "BBBB2222-0000:right" || right.Agent != "codex" || right.Status != "active" {
		t.Errorf("right split pane: %+v", right)
	}
	if panes[1].Status != "" {
		t.Errorf("status belongs to the right pane only, left got %q", panes[1].Status)
	}
	if panes[3].Foreground != "" || panes[3].Agent != "" {
		t.Errorf("bare shell pane: %+v", panes[3])
	}
	if !panes[4].Self || panes[0].Self {
		t.Errorf("self marking wrong: %+v / %+v", panes[4], panes[0])
	}
	// CCCC4444 is selected and focused in its workspace, but that workspace is not the active one.
	for i, p := range panes {
		if p.Focused != (i == 2) {
			t.Errorf("pane %d %s focused = %v", i, p.Target, p.Focused)
		}
	}
}

func TestPanesFromTreePaneID(t *testing.T) {
	panes := testPanes(t, selfAddr{})
	want := []string{"PANE-A1-LEFT", "PANE-B2-LEFT", "PANE-B2-RIGHT", "PANE-A3-LEFT", "PANE-C4-LEFT"}
	if len(panes) != len(want) {
		t.Fatalf("want %d panes, got %d", len(want), len(panes))
	}
	for i, p := range panes {
		if p.paneID != want[i] {
			t.Errorf("pane %d %s: paneID %q, want %q", i, p.Target, p.paneID, want[i])
		}
	}

	// An agterm without pane ids in its tree gives panes without one.
	var tr rawTree
	if err := json.Unmarshal([]byte(`{"workspaces":[{"name":"w","sessions":[{"id":"X","name":"x",
		"surfaces":[{"kind":"left"},{"kind":"right"}]}]}]}`), &tr); err != nil {
		t.Fatal(err)
	}
	for _, p := range panesFromTree(tr, selfAddr{}) {
		if p.paneID != "" {
			t.Errorf("%s: paneID %q, want none", p.Target, p.paneID)
		}
	}
}

func TestFindPaneByID(t *testing.T) {
	panes := testPanes(t, selfAddr{})
	for id, target := range map[string]string{
		"PANE-A1-LEFT":  "AAAA1111-0000",
		"PANE-B2-RIGHT": "BBBB2222-0000:right",
		"PANE-C4-LEFT":  "CCCC4444-0000",
	} {
		if p, ok := findPaneByID(panes, id); !ok || p.Target != target {
			t.Errorf("findPaneByID(%q) = %q, %v; want %q", id, p.Target, ok, target)
		}
	}
	// A scratch pane is not addressable, and an empty id never matches a pane without one.
	for _, id := range []string{"PANE-B2-SCRATCH", "nope", ""} {
		if p, ok := findPaneByID(panes, id); ok {
			t.Errorf("findPaneByID(%q) found %s", id, p.Target)
		}
	}
	if _, ok := findPaneByID([]Pane{{Target: "X"}}, ""); ok {
		t.Error("empty id matched a pane without a pane id")
	}
}

func TestFocusedPane(t *testing.T) {
	var s rawSession
	if got := focusedPane(s); got != "left" {
		t.Errorf("no surfaces: %q", got)
	}
	s.SplitFocused = true
	if got := focusedPane(s); got != "right" {
		t.Errorf("splitFocused: %q", got)
	}
	if err := json.Unmarshal([]byte(`{"surfaces":[{"kind":"left"},{"kind":"scratch","active":true}]}`), &s); err != nil {
		t.Fatal(err)
	}
	if got := focusedPane(s); got != "scratch" {
		t.Errorf("scratch: %q", got)
	}
}

func TestAgentName(t *testing.T) {
	cases := []struct {
		argv []string
		want string
	}{
		{[]string{"claude", "--resume", "x"}, "claude"},
		{[]string{"/Users/u/.local/bin/claude"}, "claude"},
		{[]string{"pi", "", ""}, "pi"},
		{[]string{"zsh", "-lc", "claude"}, "claude"},
		{[]string{"zsh", "-ic", "CLAUDE_CODE_X=false claude --settings '{}'"}, "claude"},
		{[]string{"node", "/opt/homebrew/bin/codex"}, "codex"},
		{[]string{"watch", "-n", "300", "claude-usage-badge"}, ""},
		{[]string{"ssh", "host"}, ""},
		{[]string{"vim", "claude"}, ""},
		{nil, ""},
	}
	for _, c := range cases {
		if got := agentName(c.argv); got != c.want {
			t.Errorf("agentName(%q) = %q, want %q", c.argv, got, c.want)
		}
	}
}

func TestResolveTarget(t *testing.T) {
	panes := testPanes(t, selfAddr{})
	cases := []struct {
		arg, want, err string
	}{
		{"AAAA1111-0000", "AAAA1111-0000", ""},
		{"aaaa1111", "AAAA1111-0000", ""},
		{"BBBB:right", "BBBB2222-0000:right", ""},
		{"sh:kobo", "BBBB2222-0000", ""},
		{"sh:kobo:right", "BBBB2222-0000:right", ""},
		{"focused", "BBBB2222-0000:right", ""},
		{"Focused", "BBBB2222-0000:right", ""},
		{"AAAA", "", "matches 2 sessions"},
		{"agent", "", "matches 2 sessions"},
		{"AAAA1111:right", "", "no right pane"},
		{"nope", "", "no session matches"},
		{"", "", "empty"},
	}
	for _, c := range cases {
		p, err := resolveTarget(panes, c.arg)
		if c.err != "" {
			if err == nil || !strings.Contains(err.Error(), c.err) {
				t.Errorf("resolveTarget(%q): err %v, want containing %q", c.arg, err, c.err)
			}
			continue
		}
		if err != nil || p.Target != c.want {
			t.Errorf("resolveTarget(%q) = %q, %v; want %q", c.arg, p.Target, err, c.want)
		}
	}
}

func TestResolveFocusedWithoutFocus(t *testing.T) {
	var tr rawTree
	if err := json.Unmarshal([]byte(`{"workspaces":[{"name":"w","sessions":[{"id":"X","name":"x","surfaces":[{"kind":"left"}]}]}]}`), &tr); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveTarget(panesFromTree(tr, selfAddr{}), "focused"); err == nil || !strings.Contains(err.Error(), "no listed pane has focus") {
		t.Errorf("focused without focus: %v", err)
	}
}

func TestCheckSendable(t *testing.T) {
	panes := testPanes(t, selfAddr{SessionID: "CCCC4444-0000", Pane: "left"})
	if err := checkSendable(panes[0]); err != nil {
		t.Errorf("agent pane refused: %v", err)
	}
	if err := checkSendable(panes[3]); err == nil || !strings.Contains(err.Error(), "bare shell") {
		t.Errorf("bare shell accepted: %v", err)
	}
	if err := checkSendable(panes[4]); err == nil || !strings.Contains(err.Error(), "your own pane") {
		t.Errorf("self accepted: %v", err)
	}
	blocked := panes[0]
	blocked.Status = "blocked"
	if err := checkSendable(blocked); err == nil || !strings.Contains(err.Error(), "is blocked") {
		t.Errorf("blocked pane accepted: %v", err)
	}
	for _, st := range []string{"", "idle", "active", "completed"} {
		p := panes[0]
		p.Status = st
		if err := checkSendable(p); err != nil {
			t.Errorf("status %q refused: %v", st, err)
		}
	}
}

func TestPastePayload(t *testing.T) {
	got := string(pastePayload("line1\nline2\x1b[201~evil"))
	body := strings.TrimLeft(got, "\x7f")
	if len(got)-len(body) != 200 {
		t.Errorf("want 200 backspaces, got %d", len(got)-len(body))
	}
	want := "\x1b[200~line1\nline2[201~evil\x1b[201~"
	if body != want {
		t.Errorf("payload = %q, want %q", body, want)
	}
}
