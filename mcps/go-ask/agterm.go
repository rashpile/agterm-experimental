package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Pane is one addressable terminal: the primary pane of a session, or its right split.
type Pane struct {
	Target     string `json:"target" jsonschema:"address to pass to ask: <session-id> or <session-id>:right"`
	Workspace  string `json:"workspace"`
	Name       string `json:"name"`
	Pane       string `json:"pane" jsonschema:"left or right"`
	Cwd        string `json:"cwd,omitempty"`
	Foreground string `json:"foreground,omitempty" jsonschema:"command running in the pane; empty means a bare shell"`
	Agent      string `json:"agent,omitempty" jsonschema:"agent CLI detected in the foreground"`
	Status     string `json:"status,omitempty" jsonschema:"agterm agent status: idle, active, blocked or completed"`
	Self       bool   `json:"self,omitempty" jsonschema:"the caller's own pane"`
	Focused    bool   `json:"focused,omitempty" jsonschema:"the pane that has focus in agterm's frontmost window"`

	sessionID string
	paneID    string // agterm's stable pane id, the AGTERM_PANE_ID the pane's shell gets
}

type rawSession struct {
	ID              string   `json:"id"`
	Name            string   `json:"name"`
	Cwd             string   `json:"cwd"`
	SplitCwd        string   `json:"splitCwd"`
	Status          string   `json:"status"`
	StatusPane      string   `json:"statusPane"`
	Foreground      []string `json:"foreground"`
	SplitForeground []string `json:"splitForeground"`
	SplitFocused    bool     `json:"splitFocused"`
	Active          bool     `json:"active"` // selected in its workspace
	Surfaces        []struct {
		Kind   string `json:"kind"`
		Active bool   `json:"active"`
		PaneID string `json:"paneID"`
	} `json:"surfaces"`
}

// rawTree is the frontmost window's tree, which is what agtermctl tree returns by default.
type rawTree struct {
	Workspaces []struct {
		Name     string       `json:"name"`
		Active   bool         `json:"active"`
		Sessions []rawSession `json:"sessions"`
	} `json:"workspaces"`
}

// focusedPane says which surface of a selected session has keyboard focus: left, right or
// scratch.
func focusedPane(s rawSession) string {
	for _, sf := range s.Surfaces {
		if sf.Active {
			return sf.Kind
		}
	}
	if s.SplitFocused {
		return "right"
	}
	return "left"
}

// selfAddr names the caller's pane, so ask can refuse to type into it.
type selfAddr struct {
	SessionID string
	Pane      string // left | right
}

func newSelf(sessionID, pane string) selfAddr {
	if pane != "right" {
		pane = "left"
	}
	return selfAddr{SessionID: strings.TrimSpace(sessionID), Pane: pane}
}

// selfFromEnv is the stdio case: the client started the server inside its own pane, and the
// server inherited the env agterm gives every shell.
func selfFromEnv() selfAddr {
	return newSelf(os.Getenv("AGTERM_SESSION_ID"), os.Getenv("AGTERM_PANE"))
}

// panesFromTree flattens the tree into one entry per primary and right pane.
func panesFromTree(t rawTree, self selfAddr) []Pane {
	var out []Pane
	for _, ws := range t.Workspaces {
		for _, s := range ws.Sessions {
			focus := ""
			if ws.Active && s.Active {
				focus = focusedPane(s)
			}
			left := makePane(ws.Name, s, "left", s.Cwd, s.Foreground, self)
			left.Focused = focus == "left"
			left.paneID = surfacePaneID(s, "left")
			out = append(out, left)
			for _, sf := range s.Surfaces {
				if sf.Kind == "right" {
					cwd := s.SplitCwd
					if cwd == "" {
						cwd = s.Cwd
					}
					right := makePane(ws.Name, s, "right", cwd, s.SplitForeground, self)
					right.Focused = focus == "right"
					right.paneID = sf.PaneID
					out = append(out, right)
					break
				}
			}
		}
	}
	return out
}

// surfacePaneID is the pane id of the session's first surface of the given kind, or "".
func surfacePaneID(s rawSession, kind string) string {
	for _, sf := range s.Surfaces {
		if sf.Kind == kind {
			return sf.PaneID
		}
	}
	return ""
}

// findPaneByID returns the pane with agterm pane id id. An empty id matches nothing, so a tree
// without pane ids never matches.
func findPaneByID(panes []Pane, id string) (Pane, bool) {
	if id == "" {
		return Pane{}, false
	}
	for _, p := range panes {
		if p.paneID == id {
			return p, true
		}
	}
	return Pane{}, false
}

func makePane(ws string, s rawSession, pane, cwd string, fg []string, self selfAddr) Pane {
	p := Pane{
		Target:     s.ID,
		Workspace:  ws,
		Name:       s.Name,
		Pane:       pane,
		Cwd:        cwd,
		Foreground: strings.TrimSpace(strings.Join(fg, " ")),
		Agent:      agentName(fg),
		Self:       s.ID != "" && s.ID == self.SessionID && pane == self.Pane,
		sessionID:  s.ID,
	}
	if pane == "right" {
		p.Target = s.ID + ":right"
	}
	statusPane := s.StatusPane
	if statusPane == "" {
		statusPane = "left"
	}
	if statusPane == pane {
		p.Status = s.Status
	}
	return p
}

var knownAgents = map[string]bool{
	"claude": true, "codex": true, "pi": true, "gemini": true, "opencode": true,
	"aider": true, "amp": true, "goose": true, "crush": true, "qwen": true, "cursor-agent": true,
}

var shells = map[string]bool{"sh": true, "bash": true, "zsh": true, "fish": true, "dash": true}
var runtimes = map[string]bool{"node": true, "bun": true, "deno": true, "python": true, "python3": true}

// agentName returns the agent CLI an argv runs, looking through `zsh -lc "VAR=x claude ..."`
// and `node /path/codex` wrappers. Empty when no known agent is found.
func agentName(argv []string) string {
	if len(argv) == 0 {
		return ""
	}
	head := filepath.Base(argv[0])
	switch {
	case knownAgents[head]:
		return head
	case runtimes[head] && len(argv) > 1:
		if b := filepath.Base(argv[1]); knownAgents[b] {
			return b
		}
	case shells[head]:
		for i := 1; i < len(argv); i++ {
			if strings.HasPrefix(argv[i], "-") && strings.Contains(argv[i], "c") && i+1 < len(argv) {
				return agentName(commandWords(argv[i+1]))
			}
		}
	}
	return ""
}

// commandWords splits a shell command string and drops leading VAR=value assignments.
func commandWords(cmd string) []string {
	words := strings.Fields(cmd)
	for len(words) > 0 && strings.Contains(words[0], "=") {
		words = words[1:]
	}
	for i, w := range words {
		words[i] = strings.Trim(w, `'"`)
	}
	return words
}

// resolveTarget finds the pane an ask goes to. arg is "focused", a session id, a unique id
// prefix or an exact session name, optionally followed by ":right" (or ":left").
func resolveTarget(panes []Pane, arg string) (Pane, error) {
	arg = strings.TrimSpace(arg)
	if strings.EqualFold(arg, "focused") {
		for _, p := range panes {
			if p.Focused {
				return p, nil
			}
		}
		return Pane{}, errors.New("no listed pane has focus: agterm has no open window, or focus is on a scratch pane")
	}
	want := "left"
	if i := strings.LastIndex(arg, ":"); i >= 0 {
		switch strings.ToLower(arg[i+1:]) {
		case "right":
			want, arg = "right", arg[:i]
		case "left":
			arg = arg[:i]
		}
	}
	if arg == "" {
		return Pane{}, errors.New("empty session id")
	}

	match := func(pred func(Pane) bool) []string {
		var ids []string
		seen := map[string]bool{}
		for _, p := range panes {
			if pred(p) && !seen[p.sessionID] {
				seen[p.sessionID] = true
				ids = append(ids, p.sessionID)
			}
		}
		return ids
	}
	ids := match(func(p Pane) bool { return strings.EqualFold(p.sessionID, arg) })
	if len(ids) == 0 {
		ids = match(func(p Pane) bool { return strings.HasPrefix(strings.ToUpper(p.sessionID), strings.ToUpper(arg)) })
	}
	if len(ids) == 0 {
		ids = match(func(p Pane) bool { return p.Name == arg })
	}
	switch len(ids) {
	case 0:
		return Pane{}, fmt.Errorf("no session matches %q; call sessions to list them", arg)
	case 1:
	default:
		return Pane{}, fmt.Errorf("%q matches %d sessions (%s); pass a full session id", arg, len(ids), strings.Join(ids, ", "))
	}
	for _, p := range panes {
		if p.sessionID == ids[0] && p.Pane == want {
			return p, nil
		}
	}
	return Pane{}, fmt.Errorf("session %s has no %s pane (is the split open?)", ids[0], want)
}

// checkSendable refuses targets where typed text would do harm. A blocked agent shows a menu
// (permission prompt, question) where the paste and the Enter would pick an answer.
func checkSendable(p Pane) error {
	if p.Self {
		return fmt.Errorf("%s is your own pane; ask another session", p.Target)
	}
	if p.Foreground == "" {
		return fmt.Errorf("%s (%s) is at a bare shell: pasted text would run as commands. Start an agent there first", p.Target, p.Name)
	}
	if p.Status == "blocked" {
		return fmt.Errorf("%s (%s) is blocked: the agent is waiting for a person to answer a permission prompt or a question, "+
			"and the pasted text and Enter could answer it. Nothing was sent. Answer it in agterm first, then ask again", p.Target, p.Name)
	}
	return nil
}

// --- agtermctl ----------------------------------------------------------------

type agtermctl struct {
	bin string
}

func findAgtermctl() string {
	if b := os.Getenv("AGTERMCTL"); b != "" {
		return b
	}
	if b, err := exec.LookPath("agtermctl"); err == nil {
		return b
	}
	for _, b := range []string{"/opt/homebrew/bin/agtermctl", "/Applications/agterm.app/Contents/MacOS/agtermctl"} {
		if _, err := os.Stat(b); err == nil {
			return b
		}
	}
	return "agtermctl"
}

// run calls agtermctl with --json and returns the "result" field. agtermctl exits 0 on
// {"ok":false,...}, so the envelope is the source of truth, not the exit code.
func (a agtermctl) run(ctx context.Context, stdin []byte, args ...string) (json.RawMessage, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, a.bin, append(args, "--json")...)
	if stdin != nil {
		cmd.Stdin = bytes.NewReader(stdin)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	label := args[0]
	if len(args) > 1 && !strings.HasPrefix(args[1], "-") {
		label += " " + args[1]
	}

	var env struct {
		OK     *bool           `json:"ok"`
		Error  string          `json:"error"`
		Result json.RawMessage `json:"result"`
	}
	if jerr := json.Unmarshal(stdout.Bytes(), &env); jerr == nil {
		if env.Error != "" || (env.OK != nil && !*env.OK) {
			return nil, fmt.Errorf("agtermctl %s: %s", label, env.Error)
		}
		if err == nil {
			return env.Result, nil
		}
	}
	msg := strings.TrimSpace(stderr.String())
	if strings.Contains(msg, "Operation not permitted") {
		return nil, fmt.Errorf("a sandbox blocks the agterm socket (agterm itself may be running fine): %s", msg)
	}
	if err == nil {
		err = errors.New("unreadable response")
	}
	if msg == "" {
		msg = strings.TrimSpace(stdout.String())
	}
	return nil, fmt.Errorf("agtermctl %s: %v: %s", label, err, msg)
}

func (a agtermctl) panes(ctx context.Context, self selfAddr) ([]Pane, error) {
	res, err := a.run(ctx, nil, "tree")
	if err != nil {
		return nil, err
	}
	var r struct {
		Tree rawTree `json:"tree"`
	}
	if err := json.Unmarshal(res, &r); err != nil {
		return nil, fmt.Errorf("parse agterm tree: %w", err)
	}
	return panesFromTree(r.Tree, self), nil
}

// submitDelay separates the paste from the Enter. Sent together, the editor can swallow
// the Enter into the paste; agconv settled on one second.
const submitDelay = time.Second

// pastePayload clears the input box and wraps text in a bracketed paste, so a multi-line
// message stays one message instead of submitting at every newline. ESC is stripped: it
// would end the paste early.
func pastePayload(text string) []byte {
	var b bytes.Buffer
	b.WriteString(strings.Repeat("\x7f", 200))
	b.WriteString("\x1b[200~")
	b.WriteString(strings.ReplaceAll(text, "\x1b", ""))
	b.WriteString("\x1b[201~")
	return b.Bytes()
}

func (a agtermctl) send(ctx context.Context, p Pane, text string) error {
	args := []string{"session", "type", "--stdin", "--target", p.sessionID}
	if p.Pane == "right" {
		args = append(args, "--pane", "right")
	}
	if _, err := a.run(ctx, pastePayload(text), args...); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(submitDelay):
	}
	_, err := a.run(ctx, []byte("\r"), args...)
	return err
}
