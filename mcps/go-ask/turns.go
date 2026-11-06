package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

// stateDir is where go-ask keeps the turn journal (turns/) and the requests (requests/):
// $GO_ASK_STATE_DIR when set, else ~/.local/state/go-ask. Only main calls it; everything below
// main takes the dir as a parameter.
func stateDir() string {
	if d := os.Getenv("GO_ASK_STATE_DIR"); d != "" {
		return d
	}
	home, err := os.UserHomeDir()
	if err != nil {
		home = os.TempDir()
	}
	return filepath.Join(home, ".local", "state", "go-ask")
}

// --- normalization ------------------------------------------------------------

var pastedOpen = regexp.MustCompile(`<pasted_content id="([^"]*)">\n`)

// normalizePrompt maps the text ask sends and the prompt Claude's hook receives to the same
// string. Claude drops \r, turns a tab into 4 spaces and wraps a long paste in a
// <pasted_content> block; nothing else changes, so there is no Unicode normalization.
func normalizePrompt(s string) string {
	s = strings.ReplaceAll(s, "\r", "")
	s = unwrapPasted(s)
	s = strings.ReplaceAll(s, "\t", "    ")
	lines := strings.Split(s, "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " \t")
	}
	return strings.TrimSpace(strings.Join(lines, "\n"))
}

// unwrapPasted replaces every `<pasted_content id="X">\n<body>\n</pasted_content id="X">` block
// by its body. Go's regexp has no backreferences, so the closing tag is built from the id and
// found with strings.Index. An opening tag without its closing tag stays as text.
func unwrapPasted(s string) string {
	for start := 0; start < len(s); {
		loc := pastedOpen.FindStringSubmatchIndex(s[start:])
		if loc == nil {
			break
		}
		openStart, openEnd := start+loc[0], start+loc[1]
		closeTag := "\n</pasted_content id=\"" + s[start+loc[2]:start+loc[3]] + "\">"
		i := strings.Index(s[openEnd:], closeTag)
		if i < 0 {
			start = openEnd
			continue
		}
		s = s[:openStart] + s[openEnd:openEnd+i] + s[openEnd+i+len(closeTag):]
		start = 0
	}
	return s
}

// promptHash is the lowercase hex sha256 of the normalized prompt. The journal and the requests
// store it instead of the prompt.
func promptHash(s string) string {
	sum := sha256.Sum256([]byte(normalizePrompt(s)))
	return hex.EncodeToString(sum[:])
}

// --- journal --------------------------------------------------------------------

// turnRecord is one line of a pane's journal: a prompt (with the prompt's hash) or a stop
// (with the turn's final text). Reply is a pointer so an empty reply is written as "".
// PromptID is Claude's prompt_id: a message queued while a turn runs gets the running turn's id,
// and so does every Stop of that turn.
type turnRecord struct {
	Event          string  `json:"event"` // prompt | stop
	T              int64   `json:"t"`     // Unix milliseconds
	Session        string  `json:"session"`
	PromptID       string  `json:"prompt_id,omitempty"`
	Hash           string  `json:"hash,omitempty"`
	Reply          *string `json:"reply,omitempty"`
	StopHookActive bool    `json:"stop_hook_active,omitempty"` // a stop after a Stop hook made Claude carry on
}

const (
	journalMaxSize  = 1 << 20   // a journal past this size is trimmed before the next append
	journalKeepSize = 512 << 10 // a trim keeps the newest whole lines that fit in this
)

var unsafePaneID = regexp.MustCompile(`[^A-Za-z0-9-]`)

// journalPath is turns/<pane_id>.jsonl under dir, with every character of the pane id outside
// [A-Za-z0-9-] replaced by '-'.
func journalPath(dir, paneID string) string {
	return filepath.Join(dir, "turns", unsafePaneID.ReplaceAllString(paneID, "-")+".jsonl")
}

// appendTurn appends rec as one line, creating the dir (0700) and the file (0600) when missing.
// A journal past journalMaxSize is trimmed first. A trim racing an append from another process
// can lose that one append: a rare race on the size boundary, accepted.
func appendTurn(path string, rec turnRecord) error {
	line, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	line = append(line, '\n')
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	if fi, err := os.Stat(path); err == nil && fi.Size() > journalMaxSize {
		if err := trimJournal(path); err != nil {
			return err
		}
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(line); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

// trimJournal rewrites path keeping only the newest whole lines that fit in journalKeepSize.
func trimJournal(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if cut := len(data) - journalKeepSize; cut > 0 {
		i := bytes.IndexByte(data[cut-1:], '\n')
		if i < 0 {
			data = nil
		} else {
			data = data[cut+i:]
		}
	}
	return writeFileAtomic(path, data)
}

// writeFileAtomic writes data to a temp file next to path (os.CreateTemp makes it 0600) and
// renames it over path.
func writeFileAtomic(path string, data []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		os.Remove(tmp.Name())
		return err
	}
	return nil
}

// readTurns returns the journal's records in file order, skipping lines that do not parse.
// A missing file is no records and no error; any other read failure is an error.
func readTurns(path string) ([]turnRecord, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var recs []turnRecord
	for _, line := range bytes.Split(data, []byte("\n")) {
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		var r turnRecord
		if json.Unmarshal(line, &r) != nil {
			continue
		}
		recs = append(recs, r)
	}
	return recs, nil
}
