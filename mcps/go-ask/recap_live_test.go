//go:build live

// The live check of recap: the real binary over stdio, real agterm, the real Claude processes
// and transcripts under ~/.claude and the real Ollama. go test ./... does not build it; run it
// by hand from a pane whose session runs Claude:
//
//	GO_ASK_LIVE_SESSION=$AGTERM_SESSION_ID go test -tags live -run TestRecapLive -v -timeout 10m
//
// GO_ASK_LIVE_LANGUAGE passes a language; without it the recap is in English.

package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// recapSections are words from the four section titles recapPromptFor asks for, in English.
var recapSections = []string{"done", "decision", "state", "left"}

func TestRecapLive(t *testing.T) {
	target := os.Getenv("GO_ASK_LIVE_SESSION")
	if target == "" {
		t.Skip("GO_ASK_LIVE_SESSION is not set: give it the session id of a pane running Claude")
	}
	cs := liveConnect(t)

	t.Run("session", func(t *testing.T) {
		liveRecap(t, cs, map[string]any{"session_id": target})
	})
	t.Run("focused", func(t *testing.T) {
		res, err := cs.CallTool(context.Background(), &mcp.CallToolParams{Name: "sessions", Arguments: map[string]any{}})
		if err != nil || res.IsError {
			t.Fatalf("sessions: %v %+v", err, res)
		}
		raw, _ := json.Marshal(res.StructuredContent)
		var out sessionsOut
		if err := json.Unmarshal(raw, &out); err != nil {
			t.Fatal(err)
		}
		for _, p := range out.Sessions {
			if p.Focused && p.Agent == "claude" {
				t.Logf("focused pane: %s (%s)", p.Target, p.Name)
				liveRecap(t, cs, map[string]any{})
				return
			}
		}
		t.Skip("the focused pane does not run claude")
	})
}

// liveConnect builds go-ask into a temp dir and connects to it over stdio. The binary inherits
// the environment, so it sees the caller's pane, agtermctl and RECAP_MODEL/OLLAMA_HOST; only its
// state dir is a temp dir.
func liveConnect(t *testing.T) *mcp.ClientSession {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "go-ask")
	if out, err := exec.Command("go", "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	cmd := exec.Command(bin)
	cmd.Env = append(os.Environ(), "GO_ASK_STATE_DIR="+t.TempDir())
	cmd.Stderr = os.Stderr
	c := mcp.NewClient(&mcp.Implementation{Name: "live-test", Version: "0"}, nil)
	cs, err := c.Connect(context.Background(), &mcp.CommandTransport{Command: cmd}, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cs.Close() })
	return cs
}

// liveRecap calls recap with args and the default model, prints what came back and checks what
// does not depend on the model's wording.
func liveRecap(t *testing.T, cs *mcp.ClientSession, args map[string]any) {
	t.Helper()
	wantLang := recapDefaultLanguage
	if lang := os.Getenv("GO_ASK_LIVE_LANGUAGE"); lang != "" {
		args["language"] = lang
		wantLang = lang
	}
	ctx, cancel := context.WithTimeout(context.Background(), 9*time.Minute)
	defer cancel()
	start := time.Now()
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: "recap", Arguments: args})
	if err != nil {
		t.Fatalf("recap: %v", err)
	}
	if res.IsError {
		t.Fatalf("recap: %s", res.Content[0].(*mcp.TextContent).Text)
	}
	raw, _ := json.Marshal(res.StructuredContent)
	var out recapOut
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	lower := strings.ToLower(out.Recap)
	var found []string
	for _, s := range recapSections {
		if strings.Contains(lower, s) {
			found = append(found, s)
		}
	}
	t.Logf("target %s (%s)\ntranscript %s\nmodel %s, language %s, turns %d, tokens %d -> %d, %.1f s (tool %d ms)\nEnglish section words %d/%d %v, %d words\n\n%s",
		out.Target, out.Name, out.Transcript, out.Model, out.Language, out.Turns, out.PromptTokens, out.OutputTokens,
		time.Since(start).Seconds(), out.DurationMs, len(found), len(recapSections), found,
		len(strings.Fields(out.Recap)), out.Recap)

	if out.Turns != recapDefaultTurns {
		t.Errorf("turns = %d, want %d", out.Turns, recapDefaultTurns)
	}
	if out.Model == "" {
		t.Error("model is empty")
	}
	if !strings.HasSuffix(out.Transcript, ".jsonl") {
		t.Errorf("transcript %q is not a .jsonl file", out.Transcript)
	}
	if out.Language != wantLang {
		t.Errorf("language = %q, want %q", out.Language, wantLang)
	}
}
