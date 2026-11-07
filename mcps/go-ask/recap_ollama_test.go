package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// fakeOllama serves /api/chat with handle and returns a client pointed at it.
func fakeOllama(t *testing.T, handle http.HandlerFunc) ollamaClient {
	t.Helper()
	srv := httptest.NewServer(handle)
	t.Cleanup(srv.Close)
	return ollamaClient{host: srv.URL, http: srv.Client(), ceiling: ollamaCeiling}
}

// hangUp reads the request body, without which the server does not notice the client hanging
// up, and waits until the client does.
func hangUp(r *http.Request) {
	io.Copy(io.Discard, r.Body)
	<-r.Context().Done()
}

// replyWith answers every request with status and body.
func replyWith(status int, body string) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		io.WriteString(w, body)
	}
}

func TestOllamaChatRequest(t *testing.T) {
	digest := "\n### USER 09:00 UTC\n" + strings.Repeat("ж", 30000) // 30000 runes: num_ctx 16384
	var (
		method, path, ctype string
		body                map[string]any
	)
	c := fakeOllama(t, func(w http.ResponseWriter, r *http.Request) {
		method, path, ctype = r.Method, r.URL.Path, r.Header.Get("Content-Type")
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, &body); err != nil {
			t.Errorf("request body is not JSON: %v", err)
		}
		io.WriteString(w, `{"model":"m","message":{"role":"assistant","content":"\n  ## Сводка\n1. Сделано  \n\n"},"done":true,"prompt_eval_count":1234,"eval_count":210}`)
	})
	text, pt, ot, err := c.chat(context.Background(), "gemma4:e4b", digest)
	if err != nil {
		t.Fatal(err)
	}
	if text != "## Сводка\n1. Сделано" || pt != 1234 || ot != 210 {
		t.Errorf("chat = %q, %d, %d; want the trimmed content, 1234, 210", text, pt, ot)
	}
	if method != http.MethodPost || path != "/api/chat" || ctype != "application/json" {
		t.Errorf("request %s %s (%s), want POST /api/chat (application/json)", method, path, ctype)
	}
	if body["model"] != "gemma4:e4b" || body["stream"] != false || body["keep_alive"] != "5m" {
		t.Errorf("model, stream, keep_alive = %v, %v, %v", body["model"], body["stream"], body["keep_alive"])
	}
	opts, _ := body["options"].(map[string]any)
	if opts["num_ctx"] != float64(16384) || opts["num_ctx"] != float64(numCtxFor(digest)) || opts["temperature"] != 0.2 {
		t.Errorf("options = %v, want num_ctx 16384 and temperature 0.2", opts)
	}
	msgs, _ := body["messages"].([]any)
	if len(msgs) != 2 {
		t.Fatalf("messages = %v, want system and user", msgs)
	}
	want := []map[string]any{{"role": "system", "content": recapSystem}, {"role": "user", "content": recapPrompt + digest}}
	for i, w := range want {
		m, _ := msgs[i].(map[string]any)
		if m["role"] != w["role"] || m["content"] != w["content"] {
			t.Errorf("message %d = %v %.60q…, want %v %.60q…", i, m["role"], m["content"], w["role"], w["content"])
		}
	}
}

func TestOllamaChatNoTokenCounts(t *testing.T) {
	c := fakeOllama(t, replyWith(200, `{"message":{"role":"assistant","content":"ok"},"done":true}`))
	text, pt, ot, err := c.chat(context.Background(), "m", "d")
	if err != nil || text != "ok" || pt != 0 || ot != 0 {
		t.Errorf("chat = %q, %d, %d, %v; want ok, 0, 0, nil", text, pt, ot, err)
	}
}

func TestOllamaChatErrors(t *testing.T) {
	long := strings.Repeat("щ", 400)
	cases := []struct {
		name    string
		status  int
		body    string
		want    string // the whole error
		wantPfx string // or its start, when the whole is long
	}{
		{name: "404 model not found", status: 404,
			body: `{"error":"model \"nope:1b\" not found, try pulling it first"}` + "\n",
			want: `ollama answered 404: {"error":"model \"nope:1b\" not found, try pulling it first"}; pull it with: ollama pull nope:1b`},
		{name: "404 without a body", status: 404, body: "",
			want: "ollama answered 404; pull it with: ollama pull nope:1b"},
		{name: "400 model not found in the body", status: 400, body: `{"error":"Model 'nope:1b' Not Found"}`,
			want: `ollama answered 400: {"error":"Model 'nope:1b' Not Found"}; pull it with: ollama pull nope:1b`},
		{name: "500 body", status: 500, body: `{"error":"llama runner process has terminated: signal: killed"}`,
			want: `ollama answered 500: {"error":"llama runner process has terminated: signal: killed"}`},
		{name: "500 long body cut at 300 runes", status: 500, body: long,
			want: "ollama answered 500: " + strings.Repeat("щ", 300)},
		{name: "error field in a 200", status: 200, body: `{"error":"an error was encountered while running the model"}`,
			want: "ollama: an error was encountered while running the model"},
		{name: "empty content", status: 200, body: `{"message":{"role":"assistant","content":" \n\t "},"done":true}`,
			want: "ollama returned an empty answer"},
		{name: "no message", status: 200, body: `{"done":true}`,
			want: "ollama returned an empty answer"},
		{name: "not JSON", status: 200, body: "<html>proxy</html>",
			wantPfx: "ollama answered something other than a chat response ("},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := fakeOllama(t, replyWith(tc.status, tc.body))
			_, _, _, err := c.chat(context.Background(), "nope:1b", "digest")
			switch {
			case err == nil:
				t.Fatal("chat succeeded, want an error")
			case tc.want != "" && err.Error() != tc.want:
				t.Errorf("error\n got %q\nwant %q", err, tc.want)
			case tc.wantPfx != "" && !strings.HasPrefix(err.Error(), tc.wantPfx):
				t.Errorf("error %q, want prefix %q", err, tc.wantPfx)
			}
		})
	}
}

func TestOllamaChatUnreachable(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close()
	c := ollamaClient{host: ollamaHost(addr), http: &http.Client{}, ceiling: ollamaCeiling}
	_, _, _, err = c.chat(context.Background(), "m", "d")
	if err == nil {
		t.Fatal("chat succeeded on a closed port")
	}
	msg := err.Error()
	if !strings.HasPrefix(msg, "ollama is not reachable at http://"+addr+" (") ||
		!strings.HasSuffix(msg, "); start it with: ollama serve") || strings.Contains(msg, "/api/chat") {
		t.Errorf("error = %q, want the host, the dial error without the URL, and the serve hint", msg)
	}
}

func TestOllamaChatCancelled(t *testing.T) {
	t.Run("before the call", func(t *testing.T) {
		var called atomic.Bool
		c := fakeOllama(t, func(http.ResponseWriter, *http.Request) { called.Store(true) })
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		_, _, _, err := c.chat(ctx, "m", "d")
		if !errors.Is(err, context.Canceled) || err.Error() != context.Canceled.Error() {
			t.Errorf("error = %v, want the ctx error", err)
		}
		if called.Load() {
			t.Error("the request reached Ollama")
		}
	})
	t.Run("while the model writes", func(t *testing.T) {
		got := make(chan struct{})
		c := fakeOllama(t, func(_ http.ResponseWriter, r *http.Request) {
			io.Copy(io.Discard, r.Body)
			close(got)
			<-r.Context().Done()
		})
		ctx, cancel := context.WithCancel(context.Background())
		go func() { <-got; cancel() }()
		_, _, _, err := c.chat(ctx, "m", "d")
		if !errors.Is(err, context.Canceled) || err.Error() != context.Canceled.Error() {
			t.Errorf("error = %v, want the ctx error", err)
		}
	})
	t.Run("caller deadline", func(t *testing.T) {
		c := fakeOllama(t, func(_ http.ResponseWriter, r *http.Request) { hangUp(r) })
		ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
		defer cancel()
		_, _, _, err := c.chat(ctx, "m", "d")
		if !errors.Is(err, context.DeadlineExceeded) || err.Error() != context.DeadlineExceeded.Error() {
			t.Errorf("error = %v, want the ctx error", err)
		}
	})
}

func TestOllamaChatCeiling(t *testing.T) {
	t.Run("waiting for the answer", func(t *testing.T) {
		c := fakeOllama(t, func(_ http.ResponseWriter, r *http.Request) { hangUp(r) })
		c.ceiling = 50 * time.Millisecond
		_, _, _, err := c.chat(context.Background(), "gemma4:e4b", "d")
		want := "ollama did not answer within 50ms; try fewer turns or a smaller model than gemma4:e4b"
		if err == nil || err.Error() != want {
			t.Errorf("error = %v, want %q", err, want)
		}
	})
	t.Run("reading the body", func(t *testing.T) {
		c := fakeOllama(t, func(w http.ResponseWriter, r *http.Request) {
			io.Copy(io.Discard, r.Body)
			io.WriteString(w, `{"message":`)
			w.(http.Flusher).Flush()
			<-r.Context().Done()
		})
		c.ceiling = 50 * time.Millisecond
		_, _, _, err := c.chat(context.Background(), "m", "d")
		if err == nil || !strings.HasPrefix(err.Error(), "ollama did not answer within 50ms") {
			t.Errorf("error = %v, want the ceiling error", err)
		}
	})
	t.Run("no ceiling and no http client", func(t *testing.T) {
		c := fakeOllama(t, replyWith(200, `{"message":{"content":"ok"}}`))
		c.ceiling, c.http = 0, nil
		if text, _, _, err := c.chat(context.Background(), "m", "d"); err != nil || text != "ok" {
			t.Errorf("chat = %q, %v; want ok", text, err)
		}
	})
}

func TestOllamaHost(t *testing.T) {
	cases := []struct{ in, want string }{
		{"", "http://127.0.0.1:11434"},
		{"  ", "http://127.0.0.1:11434"},
		{"127.0.0.1:11434", "http://127.0.0.1:11434"},
		{"localhost:11434/", "http://localhost:11434"},
		{"http://127.0.0.1:11434/", "http://127.0.0.1:11434"},
		{"https://ollama.example.net//", "https://ollama.example.net"},
		{"http://10.0.0.5:8080", "http://10.0.0.5:8080"},
	}
	for _, tc := range cases {
		if got := ollamaHost(tc.in); got != tc.want {
			t.Errorf("ollamaHost(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNewOllamaClient(t *testing.T) {
	env := map[string]string{"OLLAMA_HOST": "127.0.0.1:9999/"}
	c := newOllamaClient(func(k string) string { return env[k] })
	if c.host != "http://127.0.0.1:9999" || c.http == nil || c.ceiling != 10*time.Minute {
		t.Errorf("client = %+v, want host http://127.0.0.1:9999, an http client and a 10m ceiling", c)
	}
	if d := newOllamaClient(func(string) string { return "" }); d.host != defaultOllamaHost {
		t.Errorf("default host = %q", d.host)
	}
}
