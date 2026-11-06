package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// fakeAgtermctl writes a script that answers `tree` with the test tree and every other call
// with an ok envelope.
func fakeAgtermctl(t *testing.T) agtermctl {
	t.Helper()
	ctl, _ := fakeAgtermctlTree(t, treeJSON)
	return ctl
}

// fakeAgtermctlTree is fakeAgtermctl with a given tree. calls returns the argument lines the
// script has received so far.
func fakeAgtermctlTree(t *testing.T, tree string) (ctl agtermctl, calls func() string) {
	t.Helper()
	dir := t.TempDir()
	treeFile := filepath.Join(dir, "tree.json")
	logFile := filepath.Join(dir, "calls.log")
	if err := os.WriteFile(treeFile, []byte(`{"result":{"tree":`+tree+`}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	script := "#!/bin/sh\necho \"$*\" >> '" + logFile + "'\n" +
		"case \"$1\" in tree) cat '" + treeFile + "' ;; *) echo '{\"ok\":true,\"result\":{}}' ;; esac\n"
	bin := filepath.Join(dir, "agtermctl")
	if err := os.WriteFile(bin, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	return agtermctl{bin: bin}, func() string {
		b, _ := os.ReadFile(logFile)
		return string(b)
	}
}

type headerRT struct {
	h http.Header
}

func (rt headerRT) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	for k, v := range rt.h {
		r.Header[k] = v
	}
	return http.DefaultTransport.RoundTrip(r)
}

func connect(t *testing.T, url string, h http.Header) *mcp.ClientSession {
	t.Helper()
	c := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "0"}, nil)
	cs, err := c.Connect(context.Background(), &mcp.StreamableClientTransport{
		Endpoint:   url,
		HTTPClient: &http.Client{Transport: headerRT{h}},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

func selfTargets(t *testing.T, cs *mcp.ClientSession) []string {
	t.Helper()
	res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "sessions", Arguments: map[string]any{}})
	if err != nil || res.IsError {
		t.Fatalf("sessions: %v %+v", err, res)
	}
	raw, _ := json.Marshal(res.StructuredContent)
	var out sessionsOut
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	var self []string
	for _, p := range out.Sessions {
		if p.Self {
			self = append(self, p.Target)
		}
	}
	return self
}

func TestHTTPSelfPerSession(t *testing.T) {
	ts := httptest.NewServer(httpHandler(fakeAgtermctl(t), "", t.TempDir()))
	t.Cleanup(ts.Close) // registered first, so it runs after the clients' Close
	url := ts.URL + "/mcp"

	a := connect(t, url, http.Header{headerSessionID: {"CCCC4444-0000"}})
	b := connect(t, url, http.Header{headerSessionID: {"BBBB2222-0000"}, headerPane: {"right"}})
	none := connect(t, url, nil)

	if got := selfTargets(t, a); len(got) != 1 || got[0] != "CCCC4444-0000" {
		t.Errorf("client a self = %v", got)
	}
	if got := selfTargets(t, b); len(got) != 1 || got[0] != "BBBB2222-0000:right" {
		t.Errorf("client b self = %v", got)
	}
	if got := selfTargets(t, none); len(got) != 0 {
		t.Errorf("client without headers self = %v", got)
	}

	res, err := a.CallTool(context.Background(), &mcp.CallToolParams{Name: "ask",
		Arguments: map[string]any{"text": "hi", "session_id": "CCCC4444-0000"}})
	if err != nil || !res.IsError || !strings.Contains(res.Content[0].(*mcp.TextContent).Text, "your own pane") {
		t.Errorf("ask to own pane over HTTP: %v %+v", err, res)
	}
}

func TestHTTPRejectsBrowserAndRebinding(t *testing.T) {
	ts := httptest.NewServer(httpHandler(fakeAgtermctl(t), "", t.TempDir()))
	defer ts.Close()
	body := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}`

	post := func(mod func(*http.Request)) int {
		req, _ := http.NewRequest("POST", ts.URL+"/mcp", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json, text/event-stream")
		mod(req)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		return resp.StatusCode
	}
	if c := post(func(*http.Request) {}); c != http.StatusOK {
		t.Errorf("plain client: status %d, want 200", c)
	}
	if c := post(func(r *http.Request) { r.Header.Set("Sec-Fetch-Site", "cross-site") }); c != http.StatusForbidden {
		t.Errorf("cross-site browser request: status %d, want 403", c)
	}
	if c := post(func(r *http.Request) { r.Host = "evil.example:7275" }); c != http.StatusForbidden {
		t.Errorf("rebinding Host: status %d, want 403", c)
	}
}

func TestHTTPBearerToken(t *testing.T) {
	const token = "0123456789abcdef0123"
	ts := httptest.NewServer(httpHandler(fakeAgtermctl(t), token, t.TempDir()))
	t.Cleanup(ts.Close)
	url := ts.URL + "/mcp"

	for name, auth := range map[string]string{
		"none":      "",
		"wrong":     "Bearer nope",
		"no scheme": token,
		"prefix":    "Bearer " + token[:10],
	} {
		req, _ := http.NewRequest("POST", url, strings.NewReader("{}"))
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized || resp.Header.Get("WWW-Authenticate") == "" {
			t.Errorf("%s: status %d, want 401 with WWW-Authenticate", name, resp.StatusCode)
		}
	}

	cs := connect(t, url, http.Header{"Authorization": {"Bearer " + token}, headerSessionID: {"CCCC4444-0000"}})
	if got := selfTargets(t, cs); len(got) != 1 || got[0] != "CCCC4444-0000" {
		t.Errorf("authorized client self = %v", got)
	}
}

func TestReadToken(t *testing.T) {
	dir := t.TempDir()
	write := func(name, body string, mode os.FileMode) string {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte(body), mode); err != nil {
			t.Fatal(err)
		}
		if err := os.Chmod(p, mode); err != nil {
			t.Fatal(err)
		}
		return p
	}
	if tok, err := readToken(write("ok", "0123456789abcdef0123\n", 0o600)); err != nil || tok != "0123456789abcdef0123" {
		t.Errorf("good file: %q, %v", tok, err)
	}
	if _, err := readToken(write("open", "0123456789abcdef0123", 0o644)); err == nil || !strings.Contains(err.Error(), "chmod 600") {
		t.Errorf("group-readable file accepted: %v", err)
	}
	if _, err := readToken(write("short", "abc", 0o600)); err == nil || !strings.Contains(err.Error(), "shorter") {
		t.Errorf("short token accepted: %v", err)
	}
	if _, err := readToken(filepath.Join(dir, "missing")); err == nil {
		t.Error("missing file accepted")
	}
}

func TestIsLoopback(t *testing.T) {
	for addr, want := range map[string]bool{
		"127.0.0.1:7275": true,
		"[::1]:7275":     true,
		"localhost:7275": true,
		"10.0.0.2:7275":  false,
		"0.0.0.0:7275":   false,
		":7275":          false,
		"mac.lan:7275":   false,
		"garbage":        false,
	} {
		if got := isLoopback(addr); got != want {
			t.Errorf("isLoopback(%q) = %v, want %v", addr, got, want)
		}
	}
}

func TestServeHTTPRefusesOpenAddrWithoutToken(t *testing.T) {
	err := serveHTTP("10.0.0.2:0", "", t.TempDir(), agtermctl{})
	if err == nil || !strings.Contains(err.Error(), "-token-file") {
		t.Errorf("serveHTTP without token on 10.0.0.2: %v", err)
	}
}

func TestAskDefaultsToFocused(t *testing.T) {
	s := &server{ctl: fakeAgtermctl(t), dir: t.TempDir()}
	_, out, err := s.ask(context.Background(), nil, askIn{Text: "hi"})
	if err != nil || out.Target != "BBBB2222-0000:right" {
		t.Errorf("ask without session_id = %+v, %v; want the focused pane BBBB2222-0000:right", out, err)
	}

	s.self = selfAddr{SessionID: "BBBB2222-0000", Pane: "right"}
	if _, _, err := s.ask(context.Background(), nil, askIn{Text: "hi"}); err == nil || !strings.Contains(err.Error(), "your own pane") {
		t.Errorf("focused pane is the caller's own, want a refusal: %v", err)
	}
}

// blockedTreeJSON has two agents waiting on a prompt: the focused one, and one on the right
// split of a session whose left pane is free.
const blockedTreeJSON = `{"workspaces":[{"name":"w","active":true,"sessions":[
 {"id":"DDDD5555-0000","name":"waiting","status":"blocked","active":true,
  "foreground":["claude"],"surfaces":[{"kind":"left","active":true}]},
 {"id":"EEEE6666-0000","name":"split","status":"blocked","statusPane":"right",
  "foreground":["claude"],"splitForeground":["codex"],"surfaces":[{"kind":"left"},{"kind":"right"}]}
]}]}`

func TestAskRefusesBlocked(t *testing.T) {
	ctl, calls := fakeAgtermctlTree(t, blockedTreeJSON)
	s := &server{ctl: ctl, dir: t.TempDir()}
	for _, target := range []string{"", "focused", "DDDD5555-0000", "waiting", "EEEE6666-0000:right"} {
		_, _, err := s.ask(context.Background(), nil, askIn{Text: "yes", SessionID: target})
		if err == nil || !strings.Contains(err.Error(), "is blocked") || !strings.Contains(err.Error(), "Nothing was sent") {
			t.Errorf("ask to %q: err %v, want a blocked refusal", target, err)
		}
	}
	if strings.Contains(calls(), "session type") {
		t.Fatalf("typed into a blocked session:\n%s", calls())
	}

	// The status belongs to the right pane only, so the left pane of the same session is free.
	if _, out, err := s.ask(context.Background(), nil, askIn{Text: "hi", SessionID: "EEEE6666-0000"}); err != nil || out.Target != "EEEE6666-0000" {
		t.Errorf("ask to the free left pane: %+v, %v", out, err)
	}
	if !strings.Contains(calls(), "session type --stdin --target EEEE6666-0000") {
		t.Errorf("the free left pane got nothing typed:\n%s", calls())
	}
}

func TestAskSavesRequest(t *testing.T) {
	ctl, calls := fakeAgtermctlTree(t, treeJSON)
	dir := t.TempDir()
	s := &server{ctl: ctl, dir: dir}
	text := "say ALPHA\n\tthen \x1bstop"
	before := time.Now().UnixMilli()
	_, out, err := s.ask(context.Background(), nil, askIn{Text: text, SessionID: "AAAA1111-0000"})
	after := time.Now().UnixMilli()
	if err != nil {
		t.Fatal(err)
	}
	if !requestIDPattern.MatchString(out.RequestID) {
		t.Fatalf("request_id %q, want 12 lowercase hex", out.RequestID)
	}
	if got := s.lastRequest(); got != out.RequestID {
		t.Errorf("last request_id %q, want %q", got, out.RequestID)
	}
	r, err := loadRequest(dir, out.RequestID)
	if err != nil {
		t.Fatal(err)
	}
	if r.SentAt < before || r.SentAt > after {
		t.Errorf("sent_at %d outside [%d, %d]", r.SentAt, before, after)
	}
	want := request{
		ID:     out.RequestID,
		Target: "AAAA1111-0000",
		PaneID: "PANE-A1-LEFT",
		Name:   "agent",
		Agent:  "claude",
		Hash:   promptHash("say ALPHA\n\tthen stop"), // pastePayload types the text without ESC
		SentAt: r.SentAt,
	}
	if r != want {
		t.Errorf("request\n got %+v\nwant %+v", r, want)
	}
	if !strings.Contains(calls(), "session type --stdin --target AAAA1111-0000") {
		t.Errorf("nothing typed:\n%s", calls())
	}

	// A right split gets its own pane id, and a second ask becomes the session's last.
	_, out2, err := s.ask(context.Background(), nil, askIn{Text: "hi", SessionID: "BBBB2222-0000:right"})
	if err != nil {
		t.Fatal(err)
	}
	if out2.RequestID == out.RequestID || s.lastRequest() != out2.RequestID {
		t.Errorf("second ask: request_id %q, last %q, first %q", out2.RequestID, s.lastRequest(), out.RequestID)
	}
	if r2, err := loadRequest(dir, out2.RequestID); err != nil || r2.PaneID != "PANE-B2-RIGHT" || r2.Agent != "codex" {
		t.Errorf("right split request: %+v, %v", r2, err)
	}
}

func TestAskUnsavableRequestSendsNothing(t *testing.T) {
	ctl, calls := fakeAgtermctlTree(t, treeJSON)
	// A file where the state dir should be: the requests dir cannot be created.
	dir := filepath.Join(t.TempDir(), "state")
	if err := os.WriteFile(dir, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	for name, d := range map[string]string{"unwritable state dir": dir, "no state dir": ""} {
		t.Run(name, func(t *testing.T) {
			s := &server{ctl: ctl, dir: d}
			_, out, err := s.ask(context.Background(), nil, askIn{Text: "hi", SessionID: "AAAA1111-0000"})
			if err == nil || !strings.Contains(err.Error(), "save request") || !strings.Contains(err.Error(), "Nothing was sent") {
				t.Errorf("err %v, want a save request error", err)
			}
			if out.RequestID != "" || s.lastRequest() != "" {
				t.Errorf("request_id %q, last %q; want none", out.RequestID, s.lastRequest())
			}
		})
	}
	if strings.Contains(calls(), "session type") {
		t.Fatalf("typed after a failed save:\n%s", calls())
	}
}

func TestAskFailedSendIsNotLast(t *testing.T) {
	dir := t.TempDir()
	bin := filepath.Join(dir, "agtermctl")
	// tree works, session type fails
	script := "#!/bin/sh\ncase \"$1\" in tree) echo '{\"result\":{\"tree\":" + strings.ReplaceAll(treeJSON, "\n", "") +
		"}}' ;; *) echo '{\"ok\":false,\"error\":\"no such session\"}' ;; esac\n"
	if err := os.WriteFile(bin, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	s := &server{ctl: agtermctl{bin: bin}, dir: t.TempDir()}
	_, out, err := s.ask(context.Background(), nil, askIn{Text: "hi", SessionID: "AAAA1111-0000"})
	if err == nil || !strings.Contains(err.Error(), "no such session") {
		t.Fatalf("err %v, want the send error", err)
	}
	if out.RequestID != "" || s.lastRequest() != "" {
		t.Errorf("request_id %q, last %q after a failed send; want none", out.RequestID, s.lastRequest())
	}
}
