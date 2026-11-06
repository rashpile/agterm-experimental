package main

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"time"
)

// hookInput is the part of a Claude Code hook's stdin that go-ask reads.
type hookInput struct {
	Event                string `json:"hook_event_name"`
	SessionID            string `json:"session_id"`
	Prompt               string `json:"prompt"`
	LastAssistantMessage string `json:"last_assistant_message"`
}

// hookMain is `go-ask hook`, run by Claude's UserPromptSubmit and Stop hooks with the hook input
// on stdin. It appends one record to the journal of the pane named by AGTERM_PANE_ID under dir:
// a prompt record with the prompt's hash, or a stop record with the turn's final text. Any other
// event, or no AGTERM_PANE_ID, writes nothing.
//
// It never writes to stdout, because UserPromptSubmit stdout becomes prompt context. A problem is
// one "go-ask hook: ..." line on stderr. The result is always 0, so the journal never blocks
// Claude.
func hookMain(stdin io.Reader, stderr io.Writer, getenv func(string) string, dir string, now func() time.Time) int {
	// Read all of stdin first, so Claude never writes into a closed pipe.
	data, err := io.ReadAll(stdin)
	if err != nil {
		hookError(stderr, "read input: %v", err)
		return 0
	}
	paneID := getenv("AGTERM_PANE_ID")
	if paneID == "" {
		return 0
	}
	var in hookInput
	if err := json.Unmarshal(data, &in); err != nil {
		hookError(stderr, "parse input: %v", err)
		return 0
	}
	rec := turnRecord{T: now().UnixMilli(), Session: in.SessionID}
	switch in.Event {
	case "UserPromptSubmit":
		rec.Event = "prompt"
		rec.Hash = promptHash(in.Prompt)
	case "Stop":
		reply := in.LastAssistantMessage
		rec.Event = "stop"
		rec.Reply = &reply
	default:
		return 0
	}
	if err := appendTurn(journalPath(dir, paneID), rec); err != nil {
		hookError(stderr, "journal: %v", err)
	}
	return 0
}

// hookError writes one "go-ask hook: ..." line to stderr.
func hookError(stderr io.Writer, format string, args ...any) {
	msg := strings.ReplaceAll(fmt.Sprintf(format, args...), "\n", " ")
	fmt.Fprintln(stderr, "go-ask hook: "+msg)
}
