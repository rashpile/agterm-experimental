package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// recapTreeJSON: one session with claude in both panes, the right one focused and blocked on a
// prompt; a codex pane; a claude pane with no live claude behind it; a bare shell; a claude whose
// transcript has no turns yet; a claude pane agterm gives no pane id.
const recapTreeJSON = `{"workspaces":[{"name":"w","active":true,"sessions":[
 {"id":"RRRR1111-0000","name":"pair","cwd":"/r","active":true,"status":"blocked","statusPane":"right",
  "foreground":["claude"],"splitForeground":["claude","--resume","x"],
  "surfaces":[{"kind":"left","paneID":"PANE-R1-LEFT"},{"kind":"right","active":true,"paneID":"PANE-R1-RIGHT"}]},
 {"id":"SSSS2222-0000","name":"coder","foreground":["codex"],"surfaces":[{"kind":"left","paneID":"PANE-S2-LEFT"}]},
 {"id":"TTTT3333-0000","name":"lonely","foreground":["claude"],"surfaces":[{"kind":"left","paneID":"PANE-T3-LEFT"}]},
 {"id":"UUUU4444-0000","name":"shell","surfaces":[{"kind":"left","paneID":"PANE-U4-LEFT"}]},
 {"id":"VVVV5555-0000","name":"fresh","foreground":["claude"],"surfaces":[{"kind":"left","paneID":"PANE-V5-LEFT"}]},
 {"id":"WWWW6666-0000","name":"old","foreground":["claude"],"surfaces":[{"kind":"left"}]},
 {"id":"XXXX7777-0000","name":"lost","foreground":["claude"],"surfaces":[{"kind":"left","paneID":"PANE-X7-LEFT"}]}
]}]}`

// recapOllama is a fake Ollama that records the digest of every chat request and answers with
// a fixed recap and token counts.
type recapOllama struct {
	mu      sync.Mutex
	models  []string
	digests []string
	delay   time.Duration
}

func (o *recapOllama) handle(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req ollamaChatRequest
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, &req); err != nil || len(req.Messages) != 2 || req.Messages[0].Content != recapSystem ||
			!strings.HasPrefix(req.Messages[1].Content, recapPrompt) {
			t.Errorf("unexpected Ollama request: %v %.200s", err, raw)
		}
		digest := ""
		if len(req.Messages) == 2 {
			digest = strings.TrimPrefix(req.Messages[1].Content, recapPrompt)
		}
		o.mu.Lock()
		o.models = append(o.models, req.Model)
		o.digests = append(o.digests, digest)
		o.mu.Unlock()
		time.Sleep(o.delay)
		io.WriteString(w, `{"message":{"role":"assistant","content":"\n## 1. Что сделано\n- всё\n"},"done":true,"prompt_eval_count":1234,"eval_count":210}`)
	}
}

func (o *recapOllama) last(t *testing.T) (model, digest string) {
	t.Helper()
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.digests) == 0 {
		t.Fatal("Ollama got no request")
	}
	return o.models[len(o.models)-1], o.digests[len(o.digests)-1]
}

func (o *recapOllama) calls() int {
	o.mu.Lock()
	defer o.mu.Unlock()
	return len(o.digests)
}

// recapEnv is a go-ask HTTP server over recapTreeJSON with a temp ~/.claude, a fake ps and a
// fake Ollama: the left pane of RRRR1111-0000 runs the fixture session, its right pane a short
// one, VVVV5555-0000 a session without turns, XXXX7777-0000 one whose transcript is gone.
type recapEnv struct {
	url      string
	loc      *locatorFixture
	ollama   *recapOllama
	calls    func() string // agtermctl calls
	left     string        // transcript paths
	right    string
	rightLog []string // the right pane's transcript lines
}

func newRecapEnv(t *testing.T) *recapEnv {
	t.Helper()
	ctl, calls := fakeAgtermctlTree(t, recapTreeJSON)
	e := &recapEnv{loc: newLocatorFixture(t), ollama: &recapOllama{}, calls: calls}

	e.loc.claude(t, 100, "sess-left", 1000, paneEnv("PANE-R1-LEFT"))
	e.loc.claude(t, 200, "sess-right", 2000, paneEnv("PANE-R1-RIGHT", "AGTERM_PANE=right"))
	// children of the right pane's claude, newer, same pane id, one marker each: a revmux
	// reviewer (CLAUDECODE stripped, no transcript saved) and a child that saves its transcript
	e.loc.claude(t, 201, "sess-child", 9000, paneEnv("PANE-R1-RIGHT", "CLAUDE_CODE_CHILD_SESSION=1"))
	e.loc.claude(t, 202, "sess-sdk-child", 9500, paneEnv("PANE-R1-RIGHT", "CLAUDECODE=1"))
	e.loc.claude(t, 300, "sess-codex-pane", 1000, "")                    // dead: nothing live in the codex pane
	e.loc.claude(t, 500, "sess-fresh", 1000, paneEnv("PANE-V5-LEFT"))    // transcript without turns
	e.loc.claude(t, 700, "0963d564-lost", 1000, paneEnv("PANE-X7-LEFT")) // no transcript on disk

	fixture, err := os.ReadFile(recapFixture)
	if err != nil {
		t.Fatal(err)
	}
	e.left = e.writeTranscript(t, "-Users-u-left", "sess-left", string(fixture))
	e.rightLog = []string{
		userLine(t, "10:00", "Правая панель: проверь тесты."),
		asstLine(t, textBlock("Тесты правой панели зелёные.")),
	}
	e.right = e.writeTranscript(t, "-Users-u-right", "sess-right", strings.Join(e.rightLog, "\n")+"\n")
	e.writeTranscript(t, "-Users-u-right", "sess-sdk-child", userLine(t, "11:00", "child prompt")+"\n")
	e.writeTranscript(t, "-Users-u-fresh", "sess-fresh",
		jsonLine(t, map[string]any{"type": "summary", "summary": "x"})+"\n"+metaLine(t, "meta only")+"\n")

	deps := recapDeps{ollama: fakeOllama(t, e.ollama.handle(t)), locator: e.loc.loc, model: "gemma4:e4b"}
	ts := httptest.NewServer(httpHandler(ctl, "", t.TempDir(), deps))
	t.Cleanup(ts.Close)
	e.url = ts.URL + "/mcp"
	return e
}

func (e *recapEnv) writeTranscript(t *testing.T, project, sessionID, body string) string {
	t.Helper()
	dir := filepath.Join(e.loc.loc.projectsDir, project)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(dir, sessionID+".jsonl")
	if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

// recapErr calls recap, which must fail, and returns its error text.
func recapErr(t *testing.T, cs *mcp.ClientSession, args map[string]any) string {
	t.Helper()
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "recap", Arguments: args})
	if err != nil {
		t.Fatalf("recap %v: %v", args, err)
	}
	if !res.IsError {
		t.Fatalf("recap %v succeeded: %+v", args, res.StructuredContent)
	}
	return res.Content[0].(*mcp.TextContent).Text
}

func TestRecapListed(t *testing.T) {
	e := newRecapEnv(t)
	cs := connect(t, e.url, nil)
	res, err := cs.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	var recap *mcp.Tool
	for _, tool := range res.Tools {
		names = append(names, tool.Name)
		if tool.Name == "recap" {
			recap = tool
		}
	}
	slices.Sort(names)
	if !slices.Equal(names, []string{"answer", "ask", "recap", "sessions"}) {
		t.Errorf("tools = %v", names)
	}
	if recap == nil {
		t.Fatal("recap is not listed")
	}
	if !strings.Contains(recap.Description, "Ollama") || !strings.Contains(recap.Description, "Claude panes only") {
		t.Errorf("description %q", recap.Description)
	}
	var schema struct {
		Properties map[string]struct {
			Type        any    `json:"type"`
			Description string `json:"description"`
		} `json:"properties"`
		Required []string `json:"required"`
	}
	raw, _ := json.Marshal(recap.InputSchema)
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"session_id", "turns", "model"} {
		if p, ok := schema.Properties[k]; !ok || p.Description == "" {
			t.Errorf("input schema property %s = %+v, %v; want it with a description", k, p, ok)
		}
	}
	if len(schema.Properties) != 3 || len(schema.Required) != 0 {
		t.Errorf("input schema %s, want three optional properties", raw)
	}
	out, _ := json.Marshal(recap.OutputSchema)
	for _, k := range []string{`"recap"`, `"transcript"`, `"duration_ms"`, `"prompt_tokens"`, `"output_tokens"`} {
		if !strings.Contains(string(out), k) {
			t.Errorf("output schema lacks %s: %s", k, out)
		}
	}
}

func TestRecapExplicitSession(t *testing.T) {
	e := newRecapEnv(t)
	e.ollama.delay = 20 * time.Millisecond
	cs := connect(t, e.url, nil)
	var out recapOut
	callTool(t, cs, "recap", map[string]any{"session_id": "RRRR1111-0000"}, &out)

	want := recapOut{Recap: "## 1. Что сделано\n- всё", Target: "RRRR1111-0000", Name: "pair", Model: "gemma4:e4b",
		Turns: 6, Transcript: e.left, DurationMs: out.DurationMs, PromptTokens: 1234, OutputTokens: 210}
	if out != want {
		t.Errorf("recap\n got %+v\nwant %+v", out, want)
	}
	if out.DurationMs < 20 {
		t.Errorf("duration_ms %d, want at least the model's 20 ms", out.DurationMs)
	}
	model, digest := e.ollama.last(t)
	if model != "gemma4:e4b" || digest != goDigest(t, recapFixture, 6) {
		t.Errorf("Ollama got model %q and digest\n%s\nwant the fixture's 6-turn digest", model, digest)
	}
	if strings.Contains(e.calls(), "session type") {
		t.Errorf("recap typed into a pane:\n%s", e.calls())
	}
}

// TestRecapFocusedAndRight: the right pane is blocked on a prompt, so ask refuses it; recap only
// reads and recaps it.
func TestRecapFocusedAndRight(t *testing.T) {
	e := newRecapEnv(t)
	cs := connect(t, e.url, nil)
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "ask",
		Arguments: map[string]any{"session_id": "RRRR1111-0000:right", "text": "hi"}})
	if err != nil || !res.IsError || !strings.Contains(res.Content[0].(*mcp.TextContent).Text, "is blocked") {
		t.Fatalf("ask to the blocked right pane: %v %+v; want a blocked refusal, or this test checks nothing", err, res)
	}
	wantDigest := buildDigest(rowsOf(t, e.rightLog...), 6)
	for _, args := range []map[string]any{
		{},                                    // the focused pane: RRRR1111-0000's right split
		{"session_id": "focused"},             // the same, by name
		{"session_id": "RRRR1111-0000:right"}, // the right split by address
		{"session_id": "pair:right", "turns": 3, "model": " qwen3:8b "},
	} {
		var out recapOut
		callTool(t, cs, "recap", args, &out)
		wantModel, wantTurns := "gemma4:e4b", 6
		if args["model"] != nil {
			wantModel, wantTurns = "qwen3:8b", 3
		}
		if out.Target != "RRRR1111-0000:right" || out.Name != "pair" || out.Transcript != e.right ||
			out.Model != wantModel || out.Turns != wantTurns {
			t.Errorf("recap %v = %+v, want the right pane's claude, not its child", args, out)
		}
		model, digest := e.ollama.last(t)
		if model != wantModel || digest != wantDigest {
			t.Errorf("recap %v: Ollama got %q %q, want %q %q", args, model, digest, wantModel, wantDigest)
		}
	}
	if strings.Contains(e.calls(), "session type") {
		t.Errorf("typed into the blocked pane:\n%s", e.calls())
	}
}

// TestRecapOwnPane: recap types nothing, so the caller's own pane is allowed where ask refuses.
func TestRecapOwnPane(t *testing.T) {
	e := newRecapEnv(t)
	cs := connect(t, e.url, http.Header{headerSessionID: {"RRRR1111-0000"}, headerPane: {"right"}})
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "ask", Arguments: map[string]any{"text": "hi"}})
	if err != nil || !res.IsError || !strings.Contains(res.Content[0].(*mcp.TextContent).Text, "your own pane") {
		t.Fatalf("ask to the own focused pane: %v %+v; want a refusal, or this test checks nothing", err, res)
	}
	var out recapOut
	callTool(t, cs, "recap", map[string]any{}, &out)
	if out.Target != "RRRR1111-0000:right" || out.Transcript != e.right {
		t.Errorf("recap of the own pane = %+v", out)
	}
	if strings.Contains(e.calls(), "session type") {
		t.Errorf("typed into a pane:\n%s", e.calls())
	}
}

func TestRecapErrors(t *testing.T) {
	e := newRecapEnv(t)
	cs := connect(t, e.url, nil)
	for _, tc := range []struct {
		name, session, want string
	}{
		{"pane running codex", "SSSS2222-0000", "recap supports Claude only; SSSS2222-0000 runs codex"},
		{"no live claude", "TTTT3333-0000", "no live claude in TTTT3333-0000 (lonely)"},
		{"bare shell", "shell", "no live claude in UUUU4444-0000 (shell)"},
		{"no turns", "VVVV5555-0000", "the transcript of VVVV5555-0000 has no turns yet"},
		{"no transcript", "XXXX7777-0000", "no transcript on disk for Claude session 0963d564-lost"},
		{"no pane id", "WWWW6666-0000", "agterm's tree gives WWWW6666-0000 (old) no pane id, so the claude in it cannot be found"},
		{"unknown session", "ZZZZ", `no session matches "ZZZZ"; call sessions to list them`},
		{"no right split", "TTTT3333-0000:right", "session TTTT3333-0000 has no right pane (is the split open?)"},
	} {
		if got := recapErr(t, cs, map[string]any{"session_id": tc.session}); got != tc.want {
			t.Errorf("%s:\n got %q\nwant %q", tc.name, got, tc.want)
		}
	}
	if n := e.ollama.calls(); n != 0 {
		t.Errorf("Ollama called %d times for failing recaps", n)
	}
	// codex, unknown session, no right split and no pane id never reach ps
	if n := len(e.loc.psCalls); n != 4 {
		t.Errorf("ps called %d times, want 4 (lonely, shell, fresh, lost)", n)
	}

	e.loc.psErr = fmt.Errorf("ps: boom")
	if got, want := recapErr(t, cs, map[string]any{"session_id": "RRRR1111-0000"}), "find the claude in RRRR1111-0000: ps: boom"; got != want {
		t.Errorf("ps failing: %q, want %q", got, want)
	}
}

func TestRecapOllamaErrors(t *testing.T) {
	ctl, _ := fakeAgtermctlTree(t, recapTreeJSON)
	f := newLocatorFixture(t)
	f.claude(t, 100, "sess-left", 1000, paneEnv("PANE-R1-LEFT"))
	fixture, err := os.ReadFile(recapFixture)
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(f.loc.projectsDir, "p")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "sess-left.jsonl"), fixture, 0o600); err != nil {
		t.Fatal(err)
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	closed := ln.Addr().String()
	ln.Close()
	for _, tc := range []struct {
		name   string
		ollama ollamaClient
		want   string // prefix
		suffix string
	}{
		{"not reachable", ollamaClient{host: ollamaHost(closed), http: &http.Client{}},
			"ollama is not reachable at http://" + closed + " (", "); start it with: ollama serve"},
		{"unknown model", fakeOllama(t, replyWith(404, `{"error":"model \"gemma4:e4b\" not found, try pulling it first"}`)),
			"ollama answered 404: ", "; pull it with: ollama pull gemma4:e4b"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ts := httptest.NewServer(httpHandler(ctl, "", t.TempDir(), recapDeps{ollama: tc.ollama, locator: f.loc}))
			t.Cleanup(ts.Close)
			cs := connect(t, ts.URL+"/mcp", nil)
			got := recapErr(t, cs, map[string]any{"session_id": "RRRR1111-0000"})
			if !strings.HasPrefix(got, tc.want) || !strings.HasSuffix(got, tc.suffix) {
				t.Errorf("error %q, want %q…%q", got, tc.want, tc.suffix)
			}
		})
	}
}

func TestRecapTurnsLimits(t *testing.T) {
	e := newRecapEnv(t)
	// 55 prompts in the left pane's transcript, more than the 50 a recap covers
	var lines []string
	for i := 1; i <= 55; i++ {
		lines = append(lines, userLine(t, "09:00", fmt.Sprintf("prompt %d", i)), asstLine(t, textBlock(fmt.Sprintf("answer %d", i))))
	}
	e.writeTranscript(t, "-Users-u-left", "sess-left", strings.Join(lines, "\n")+"\n")
	rows := rowsOf(t, lines...)
	cs := connect(t, e.url, nil)
	for _, tc := range []struct {
		turns any
		want  int
		first string // the oldest prompt in the digest
	}{
		{nil, 6, "prompt 50"},
		{0, 6, "prompt 50"},
		{-3, 1, "prompt 55"},
		{1, 1, "prompt 55"},
		{2, 2, "prompt 54"},
		{50, 50, "prompt 6"},
		{51, 50, "prompt 6"},
		{1000, 50, "prompt 6"},
	} {
		args := map[string]any{"session_id": "RRRR1111-0000"}
		if tc.turns != nil {
			args["turns"] = tc.turns
		}
		var out recapOut
		callTool(t, cs, "recap", args, &out)
		_, digest := e.ollama.last(t)
		if out.Turns != tc.want || digest != buildDigest(rows, tc.want) || strings.Count(digest, "\n### USER ") != tc.want ||
			!strings.HasPrefix(digest, "\n### USER 09:00 UTC\n"+tc.first+"\n") {
			t.Errorf("turns %v: out.turns %d, digest starts %.40q with %d prompts; want %d from %q",
				tc.turns, out.Turns, digest, strings.Count(digest, "\n### USER "), tc.want, tc.first)
		}
	}
}

func TestRecapTurns(t *testing.T) {
	for in, want := range map[int]int{0: 6, -1: 1, -100: 1, 1: 1, 6: 6, 49: 49, 50: 50, 51: 50, 1 << 40: 50} {
		if got := recapTurns(in); got != want {
			t.Errorf("recapTurns(%d) = %d, want %d", in, got, want)
		}
	}
}

func TestRecapModel(t *testing.T) {
	e := newRecapEnv(t)
	cs := connect(t, e.url, nil)
	var out recapOut
	callTool(t, cs, "recap", map[string]any{"session_id": "RRRR1111-0000", "model": "llama3.2:3b"}, &out)
	if m, _ := e.ollama.last(t); out.Model != "llama3.2:3b" || m != "llama3.2:3b" {
		t.Errorf("model argument: out %q, Ollama %q", out.Model, m)
	}

	// a server built without a default model falls back to gemma4:e4b
	s := &server{ctl: mustCtl(t), deps: recapDeps{ollama: fakeOllama(t, e.ollama.handle(t)), locator: e.loc.loc}}
	_, out, err := s.recap(context.Background(), nil, recapIn{SessionID: "RRRR1111-0000"})
	if err != nil || out.Model != "gemma4:e4b" {
		t.Errorf("no default model: %+v, %v", out, err)
	}
}

func mustCtl(t *testing.T) agtermctl {
	t.Helper()
	ctl, _ := fakeAgtermctlTree(t, recapTreeJSON)
	return ctl
}

func TestNewRecapDeps(t *testing.T) {
	t.Setenv("HOME", "/Users/someone")
	env := map[string]string{"RECAP_MODEL": " qwen3:8b ", "OLLAMA_HOST": "127.0.0.1:9999"}
	d := newRecapDeps(func(k string) string { return env[k] })
	if d.model != "qwen3:8b" || d.ollama.host != "http://127.0.0.1:9999" || d.ollama.ceiling != ollamaCeiling ||
		d.locator.sessionsDir != "/Users/someone/.claude/sessions" || d.locator.ps == nil {
		t.Errorf("newRecapDeps = %+v", d)
	}
	if d := newRecapDeps(func(string) string { return "" }); d.model != "gemma4:e4b" || d.ollama.host != defaultOllamaHost {
		t.Errorf("defaults: model %q, host %q", d.model, d.ollama.host)
	}
}

func TestRecapTreeAndTranscriptFailures(t *testing.T) {
	e := newRecapEnv(t)
	s := &server{ctl: agtermctl{bin: filepath.Join(t.TempDir(), "no-agtermctl")}, deps: recapDeps{locator: e.loc.loc}}
	if _, _, err := s.recap(context.Background(), nil, recapIn{}); err == nil || !strings.HasPrefix(err.Error(), "agtermctl tree: ") {
		t.Errorf("tree failing: %v, want the agtermctl error", err)
	}

	// the transcript path is a directory: found, but not readable as a file
	if err := os.Remove(e.left); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(e.left, 0o755); err != nil {
		t.Fatal(err)
	}
	cs := connect(t, e.url, nil)
	got := recapErr(t, cs, map[string]any{"session_id": "RRRR1111-0000"})
	if !strings.HasPrefix(got, "cannot read the transcript: ") || !strings.Contains(got, e.left) {
		t.Errorf("unreadable transcript: %q, want the read error naming %s", got, e.left)
	}
}
