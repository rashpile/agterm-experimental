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
	"slices"
	"strconv"
	"strings"
	"time"
)

// The locator finds the Claude running in an agterm pane and its transcript, with no hooks:
// Claude Code writes ~/.claude/sessions/<pid>.json for every live process, and the claude in a
// pane carries AGTERM_PANE_ID=<pane id> in its environment, which ps -E shows.

var (
	// errNoClaude: no live claude process in the pane.
	errNoClaude = errors.New("no live claude in the pane")
	// errNoTranscript: the pane's claude was found but its transcript is not on disk.
	errNoTranscript = errors.New("no transcript on disk")
	// errNoPaneID: the agterm tree gave the pane no pane id, so its claude cannot be told apart.
	errNoPaneID = errors.New("agterm gave the pane no pane id")
)

// claudeLocator maps an agterm pane id to the transcript of the claude running in it.
type claudeLocator struct {
	sessionsDir string // ~/.claude/sessions: one <pid>.json per live Claude process
	projectsDir string // ~/.claude/projects: <project>/<sessionId>.jsonl transcripts
	// ps returns the command line plus environment of each live pid among pids; dead pids are
	// absent. It is never called with an empty list.
	ps func(ctx context.Context, pids []int) (map[int]string, error)
}

func newClaudeLocator() claudeLocator {
	home, err := os.UserHomeDir()
	if err != nil {
		home = os.TempDir()
	}
	dir := filepath.Join(home, ".claude")
	return claudeLocator{sessionsDir: filepath.Join(dir, "sessions"), projectsDir: filepath.Join(dir, "projects"), ps: runPS}
}

// claudeRecord is the part of ~/.claude/sessions/<pid>.json the locator reads.
type claudeRecord struct {
	pid       int    // from the file name
	SessionID string `json:"sessionId"`
	UpdatedAt int64  `json:"updatedAt"` // Unix ms
}

// records reads every <digits>.json in sessionsDir. Other names, files that do not parse and
// records without a session id are skipped; a missing dir means no records.
func (l claudeLocator) records() ([]claudeRecord, error) {
	entries, err := os.ReadDir(l.sessionsDir)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("cannot list Claude sessions: %w", err)
	}
	var recs []claudeRecord
	for _, e := range entries {
		stem, ok := strings.CutSuffix(e.Name(), ".json")
		if !ok || !isDigits(stem) || e.IsDir() {
			continue
		}
		pid, err := strconv.Atoi(stem)
		if err != nil || pid <= 0 {
			continue
		}
		data, err := os.ReadFile(filepath.Join(l.sessionsDir, e.Name()))
		if err != nil {
			continue
		}
		var r claudeRecord
		if json.Unmarshal(data, &r) != nil || r.SessionID == "" {
			continue
		}
		r.pid = pid
		recs = append(recs, r)
	}
	return recs, nil
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range []byte(s) {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

// transcriptFor finds the claude whose environment has AGTERM_PANE_ID=<paneID> and returns its
// session id and transcript path. A claude started from inside another claude (a revmux
// reviewer, claude -p, an Agent SDK tool) inherits the pane id but also has CLAUDECODE=1, and is
// skipped. When several match, the newest updatedAt wins. Errors: errNoPaneID, errNoClaude, a
// wrapped errNoTranscript that names the session id (returned too), or ps failing.
func (l claudeLocator) transcriptFor(ctx context.Context, paneID string) (sessionID, path string, err error) {
	if paneID == "" {
		return "", "", errNoPaneID
	}
	recs, err := l.records()
	if err != nil {
		return "", "", err
	}
	if len(recs) == 0 {
		return "", "", errNoClaude
	}
	pids := make([]int, len(recs))
	for i, r := range recs {
		pids[i] = r.pid
	}
	live, err := l.ps(ctx, pids)
	if err != nil {
		return "", "", err
	}
	var best *claudeRecord
	for i, r := range recs {
		line, ok := live[r.pid]
		if !ok || !hasToken(line, "AGTERM_PANE_ID="+paneID) || hasToken(line, "CLAUDECODE=1") {
			continue
		}
		if best == nil || r.UpdatedAt > best.UpdatedAt {
			best = &recs[i]
		}
	}
	if best == nil {
		return "", "", errNoClaude
	}
	matches, _ := filepath.Glob(filepath.Join(l.projectsDir, "*", best.SessionID+".jsonl"))
	if len(matches) == 0 {
		return best.SessionID, "", fmt.Errorf("%w for Claude session %s", errNoTranscript, best.SessionID)
	}
	slices.Sort(matches)
	return best.SessionID, matches[0], nil
}

// hasToken reports whether tok occurs in s as a whole space-separated token: preceded by the
// start or a space, followed by a space or the end. AGTERM_PANE_ID=ABC does not match
// AGTERM_PANE_ID=ABCD or X_AGTERM_PANE_ID=ABC.
func hasToken(s, tok string) bool {
	for i := 0; i <= len(s)-len(tok); {
		j := strings.Index(s[i:], tok)
		if j < 0 {
			return false
		}
		j += i
		end := j + len(tok)
		if (j == 0 || s[j-1] == ' ') && (end == len(s) || s[end] == ' ') {
			return true
		}
		i = j + 1
	}
	return false
}

// runPS runs /bin/ps -E over pids and returns each live pid's command line plus environment.
// /bin/ps is named absolutely: the daemon runs under launchd with a bare PATH. Measured on
// macOS: a dead pid among live ones exits 0; only dead pids exit 1 with empty stdout and stderr,
// which means no live pids, not an error. ps shows no environment for Apple platform binaries
// (/bin/sleep, /bin/zsh); claude is not one.
func runPS(ctx context.Context, pids []int) (map[int]string, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	list := make([]string, len(pids))
	for i, p := range pids {
		list[i] = strconv.Itoa(p)
	}
	cmd := exec.CommandContext(ctx, "/bin/ps", "-E", "-o", "pid=,command=", "-p", strings.Join(list, ","))
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ctx.Err() != nil {
		return nil, fmt.Errorf("ps: %w", ctx.Err())
	}
	if msg := strings.TrimSpace(stderr.String()); msg != "" {
		return nil, fmt.Errorf("ps: %s", msg)
	}
	if err != nil {
		var exit *exec.ExitError
		if !errors.As(err, &exit) || exit.ExitCode() != 1 {
			return nil, fmt.Errorf("ps: %w", err)
		}
	}
	return parsePS(stdout.Bytes()), nil
}

// parsePS reads `ps -o pid=,command=` output: a left-padded pid, a space, the rest of the line.
// Lines without a pid are skipped.
func parsePS(out []byte) map[int]string {
	m := map[int]string{}
	for _, line := range bytes.Split(out, []byte("\n")) {
		s := strings.TrimLeft(string(line), " ")
		head, rest, _ := strings.Cut(s, " ")
		pid, err := strconv.Atoi(head)
		if err != nil || pid <= 0 {
			continue
		}
		m[pid] = rest
	}
	return m
}
