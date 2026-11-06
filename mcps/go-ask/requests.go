package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
)

// request is one ask, kept in requests/<id>.json so answer can find its reply later, after a
// daemon restart too. State stays "" until a final state is stored: a non-empty State means the
// caller has received it.
type request struct {
	ID         string `json:"id"`
	Target     string `json:"target"`
	PaneID     string `json:"pane_id"`
	Name       string `json:"name"`
	Agent      string `json:"agent"`
	Hash       string `json:"hash"`    // promptHash of the text as typed, ESC removed
	SentAt     int64  `json:"sent_at"` // Unix milliseconds, taken right before the send
	State      string `json:"state"`
	Reply      string `json:"reply"`
	FinishedAt int64  `json:"finished_at"`
}

var requestIDPattern = regexp.MustCompile(`^[0-9a-f]{12}$`)

// newRequestID returns 12 random lowercase hex characters.
func newRequestID() (string, error) {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// requestPath is requests/<id>.json under dir. Callers check id first, so it cannot leave the
// requests dir.
func requestPath(dir, id string) string {
	return filepath.Join(dir, "requests", id+".json")
}

func checkRequestID(id string) error {
	if !requestIDPattern.MatchString(id) {
		return fmt.Errorf("invalid request_id %q: want the 12 lowercase hex characters ask returned", id)
	}
	return nil
}

// saveRequest writes r to requests/<id>.json under dir (dir 0700, file 0600) through a temp file
// and a rename, so a reader never sees half a request.
func saveRequest(dir string, r request) error {
	if dir == "" {
		return errors.New("no state dir")
	}
	if err := checkRequestID(r.ID); err != nil {
		return err
	}
	data, err := json.Marshal(r)
	if err != nil {
		return err
	}
	path := requestPath(dir, r.ID)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return writeFileAtomic(path, append(data, '\n'))
}

// loadRequest reads the request with the given id from dir. An id that is not 12 lowercase hex
// characters is refused before any file is opened.
func loadRequest(dir, id string) (request, error) {
	if err := checkRequestID(id); err != nil {
		return request{}, err
	}
	data, err := os.ReadFile(requestPath(dir, id))
	if errors.Is(err, fs.ErrNotExist) {
		return request{}, fmt.Errorf("unknown request_id %q", id)
	}
	if err != nil {
		return request{}, fmt.Errorf("read request %s: %w", id, err)
	}
	var r request
	if err := json.Unmarshal(data, &r); err != nil {
		return request{}, fmt.Errorf("parse request %s: %w", id, err)
	}
	return r, nil
}
