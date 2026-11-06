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

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// fakeAgtermctl writes a script that answers every call with the test tree.
func fakeAgtermctl(t *testing.T) agtermctl {
	t.Helper()
	dir := t.TempDir()
	tree := filepath.Join(dir, "tree.json")
	if err := os.WriteFile(tree, []byte(`{"result":{"tree":`+treeJSON+`}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(dir, "agtermctl")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\ncat '"+tree+"'\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	return agtermctl{bin: bin}
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
	ts := httptest.NewServer(httpHandler(fakeAgtermctl(t), ""))
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
	ts := httptest.NewServer(httpHandler(fakeAgtermctl(t), ""))
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
	ts := httptest.NewServer(httpHandler(fakeAgtermctl(t), token))
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
	err := serveHTTP("10.0.0.2:0", "", agtermctl{})
	if err == nil || !strings.Contains(err.Error(), "-token-file") {
		t.Errorf("serveHTTP without token on 10.0.0.2: %v", err)
	}
}

func TestAskDefaultsToFocused(t *testing.T) {
	s := &server{ctl: fakeAgtermctl(t)}
	_, out, err := s.ask(context.Background(), nil, askIn{Text: "hi"})
	if err != nil || out.Target != "BBBB2222-0000:right" {
		t.Errorf("ask without session_id = %+v, %v; want the focused pane BBBB2222-0000:right", out, err)
	}

	s.self = selfAddr{SessionID: "BBBB2222-0000", Pane: "right"}
	if _, _, err := s.ask(context.Background(), nil, askIn{Text: "hi"}); err == nil || !strings.Contains(err.Error(), "your own pane") {
		t.Errorf("focused pane is the caller's own, want a refusal: %v", err)
	}
}
