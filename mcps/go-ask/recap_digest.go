package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"
)

// The digest is a port of build_digest in commands/recap/recap.py: the same rows kept, the same
// caps, the same text, so the local model sees what recap.py would send. Every cap and slice
// counts runes, as Python slices code points. A parity test runs recap.py on a shared fixture
// and compares the two digests. Where Go cannot match Python exactly, a comment says so.
//
// Parity holds for rows of the shapes Claude writes. On a row of another shape recap.py often
// raises and stops; Go never stops on a row:
//   - Go reads as empty what makes recap.py raise, such as: a user prompt row whose message or
//     content is null or missing, a user or assistant content that is a number, a text block's
//     text null, a tool result's content a number, a tool input that is a non-empty string or
//     list or a number, a Bash command that is a number, a queued prompt that is null, a number
//     or an object.
//   - A field whose JSON type differs from its Go field does not decode, and Go drops what holds
//     it: the line (a timestamp, isMeta, message, attachment or origin of another type) or the
//     content block (a text, name, id or tool_use_id that is not a string). recap.py raises on
//     most of these but reads a few on: a numeric tool name comes out as its str(), an isMeta of
//     "yes" counts as true.
//   - A line that is not JSON: recap.py stops with an error, Go skips it.

const (
	recapTextCap     = 600   // an assistant text block mid-turn
	recapFinalCap    = 4000  // the last assistant text of a turn: that is where the state is reported
	recapUserCap     = 1500  // a user prompt
	recapMaxChars    = 60000 // the digest keeps its newest runes within this
	recapAnswerTail  = 400   // the end of the user's answer to the agent's question
	recapSubagentCap = 300   // a queued subagent report
	recapToolCap     = 120   // a tool call's detail
	recapCommandCap  = 80    // a Bash command used as the detail when there is no description
	recapCutPrefix   = "(начало журнала обрезано)\n"
)

// transcriptRow is the part of one Claude transcript line the digest reads. It is narrow on
// purpose: a 170 MB transcript decoded as maps is a large spike in a long-lived daemon.
// Message.Content stays raw: a string or a list of blocks, decoded only for rows the digest uses.
type transcriptRow struct {
	Type      string `json:"type"`
	IsMeta    bool   `json:"isMeta"`
	Timestamp string `json:"timestamp"`
	Message   struct {
		Content json.RawMessage `json:"content"`
	} `json:"message"`
	Attachment struct {
		Type string `json:"type"`
		// Prompt is a string, or a list of blocks when a queued message carries an image.
		Prompt json.RawMessage `json:"prompt"`
		Origin struct {
			Kind string `json:"kind"`
		} `json:"origin"`
	} `json:"attachment"`
}

// contentBlock is one block of a message content list: text, tool_use or tool_result.
type contentBlock struct {
	Type      string          `json:"type"`
	Text      string          `json:"text"`
	ID        string          `json:"id"`
	Name      string          `json:"name"`
	Input     json.RawMessage `json:"input"`
	ToolUseID string          `json:"tool_use_id"`
	Content   json.RawMessage `json:"content"`
}

// readTranscript reads a Claude transcript and returns its rows from the turns-th real user
// prompt counted from the end, or all rows when there are fewer prompts (turns below 1 counts as
// 1). Lines are decoded from the end backwards and decoding stops at that prompt, so a long
// transcript is never decoded whole. A line that does not decode into transcriptRow is skipped
// (recap.py stops on one that is not JSON; the top of the file says what it does with the rest);
// a missing or unreadable file is an error.
func readTranscript(path string, turns int) ([]transcriptRow, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read the transcript: %w", err)
	}
	turns = max(turns, 1)
	lines := bytes.Split(data, []byte("\n"))
	var rows []transcriptRow // newest first until the reverse below
	for i := len(lines) - 1; i >= 0 && turns > 0; i-- {
		if len(bytes.TrimSpace(lines[i])) == 0 {
			continue
		}
		var r transcriptRow
		if json.Unmarshal(lines[i], &r) != nil {
			continue
		}
		rows = append(rows, r)
		if isRealUser(&r) {
			turns--
		}
	}
	slices.Reverse(rows)
	return rows, nil
}

type digestKind int

const (
	itemLine digestKind = iota // a tool call, an answer, a subagent report: kept as written
	itemUser                   // a user prompt: closes the assistant's turn before it
	itemText                   // an assistant text block: capped by the final-text rule
)

type digestItem struct {
	kind digestKind
	text string
}

var (
	// commandEcho and skillBody are clean_user's patterns.
	commandEcho = regexp.MustCompile(`(?s)<command-message>.*?</command-message>|<command-args></command-args>`)
	skillBody   = regexp.MustCompile(`(?s)Base directory for this skill.*`)
	// askBash marks a Bash call that asks the user through mt. Go's \b is ASCII (RE2) where
	// Python's is Unicode: they differ only when mt or ask touches a non-ASCII letter or digit
	// (Python sees no word boundary there, Go does). Accepted: mt commands are ASCII.
	askBash = regexp.MustCompile(`\bmt\b.*\bask\b`)
)

// buildDigest is build_digest of recap.py: the rows from the turns-th real user prompt from the
// end (all rows when there are fewer prompts; turns below 1 counts as 1, as recap.py's main
// passes max(turns, 1)), one item per prompt, answer, tool call, queued message and assistant
// text, the final-text rule, and the newest-60k cut. No rows give "".
func buildDigest(rows []transcriptRow, turns int) string {
	turns = max(turns, 1)
	var starts []int
	for i := range rows {
		if isRealUser(&rows[i]) {
			starts = append(starts, i)
		}
	}
	start := 0
	if len(starts) >= turns {
		start = starts[len(starts)-turns]
	}

	var items []digestItem
	asks := map[string]bool{} // ids of the tool calls whose result is the user's answer
	for i := start; i < len(rows); i++ {
		e := &rows[i]
		ts := sliceRunes(e.Timestamp, 11, 16)
		switch e.Type {
		case "user":
			c := e.Message.Content
			blocks, _ := blocksOf(c)
			for _, b := range blocks {
				if b.Type == "tool_result" && asks[b.ToolUseID] {
					r, ok := rawString(b.Content)
					if !ok {
						r = textOf(b.Content)
					}
					items = append(items, digestItem{itemLine, "  - USER ANSWER: " + tailRunes(pyStrip(r), recapAnswerTail)})
				}
			}
			if isRealUser(e) {
				items = append(items, digestItem{itemUser,
					"\n### USER " + ts + " UTC\n" + headRunes(cleanUser(textOf(c)), recapUserCap)})
			}
		case "attachment":
			a := &e.Attachment
			if a.Type != "queued_command" {
				continue
			}
			// recap.py slices the prompt as is: a list prompt (a queued message with an image)
			// comes out as a Python list repr, base64 image data included. Go takes the
			// prompt's text blocks instead, which is what the user typed.
			prompt := textOf(a.Prompt)
			if a.Origin.Kind == "human" {
				items = append(items, digestItem{itemUser,
					"\n### USER (sent mid-turn) " + ts + " UTC\n" + headRunes(prompt, recapUserCap)})
			} else {
				items = append(items, digestItem{itemLine, "  - subagent report arrived: " + headRunes(prompt, recapSubagentCap)})
			}
		case "assistant":
			blocks, _ := blocksOf(e.Message.Content)
			for _, b := range blocks {
				switch b.Type {
				case "text":
					if t := pyStrip(b.Text); t != "" {
						items = append(items, digestItem{itemText, t})
					}
				case "tool_use":
					items = append(items, toolItem(b, asks))
				}
			}
		}
	}

	// The last assistant text before each user message, and at the end, is the turn's report:
	// that is where the state is written down, so it is kept nearly whole.
	final := map[int]bool{}
	last := -1
	for idx, it := range items {
		switch it.kind {
		case itemText:
			last = idx
		case itemUser:
			if last >= 0 {
				final[last] = true
				last = -1
			}
		}
	}
	if last >= 0 {
		final[last] = true
	}
	out := make([]string, len(items))
	for idx, it := range items {
		if it.kind != itemText {
			out[idx] = it.text
			continue
		}
		limit := recapTextCap
		if final[idx] {
			limit = recapFinalCap
		}
		out[idx] = "ASSISTANT: " + capRunes(it.text, limit)
	}
	digest := strings.Join(out, "\n")
	if utf8.RuneCountInString(digest) > recapMaxChars {
		digest = recapCutPrefix + tailRunes(digest, recapMaxChars)
	}
	return digest
}

// toolItem is the one line of a tool call, and records the call in asks when its result is the
// user's answer (AskUserQuestion, or Bash running `mt … ask`). The detail is the first of
// description, command (its first 80 runes), file_path, query. recap.py str()s any other truthy
// value, but raises on a command that is a number when it reads it; Go takes strings only and
// skips any other type. recap.py writes a missing name as "None"; Go writes it empty. Claude always sends string
// details and a name.
func toolItem(b contentBlock, asks map[string]bool) digestItem {
	var in map[string]json.RawMessage
	_ = json.Unmarshal(b.Input, &in) // input that is not an object leaves no fields; recap.py raises
	field := func(k string) string {
		s, _ := rawString(in[k])
		return s
	}
	d := field("description")
	if d == "" {
		d = headRunes(field("command"), recapCommandCap)
	}
	if d == "" {
		d = field("file_path")
	}
	if d == "" {
		d = field("query")
	}
	if b.Name == "AskUserQuestion" || (b.Name == "Bash" && askBash.MatchString(field("command"))) {
		asks[b.ID] = true
	}
	return digestItem{itemLine, "  - tool " + b.Name + ": " + headRunes(d, recapToolCap)}
}

// isRealUser is recap.py's is_real_user: a prompt the user typed, not a tool result, not a meta
// row, not a local-command or remote-control echo.
func isRealUser(e *transcriptRow) bool {
	if e.Type != "user" || e.IsMeta {
		return false
	}
	c := e.Message.Content
	if blocks, ok := blocksOf(c); ok && !slices.ContainsFunc(blocks, func(b contentBlock) bool { return b.Type == "text" }) {
		return false
	}
	t := pyLStrip(textOf(c))
	return t != "" && !strings.HasPrefix(t, "<local-command") && !strings.HasPrefix(t, "<command-name>/remote-control")
}

// cleanUser is recap.py's clean_user: drops the skill-invocation echo and the skill body.
func cleanUser(t string) string {
	t = commandEcho.ReplaceAllLiteralString(t, "")
	return pyStrip(skillBody.ReplaceAllLiteralString(t, ""))
}

// textOf is recap.py's text_of: a string content as is, the text blocks of a list joined by a
// space, anything else "". text_of gives "" only for an object: it raises on null and on a number,
// and on a text block whose text is null.
func textOf(raw json.RawMessage) string {
	if s, ok := rawString(raw); ok {
		return s
	}
	blocks, _ := blocksOf(raw)
	var texts []string
	for _, b := range blocks {
		if b.Type == "text" {
			texts = append(texts, b.Text)
		}
	}
	return strings.Join(texts, " ")
}

// blocksOf decodes a content that is a JSON list into its blocks; ok is false for any other
// content. An element that is not an object is dropped, as recap.py drops non-dict blocks, and
// so is one that does not decode into contentBlock, which recap.py reads on or raises on (see the
// top of the file).
func blocksOf(raw json.RawMessage) (blocks []contentBlock, ok bool) {
	var elems []json.RawMessage
	if jsonKind(raw) != '[' || json.Unmarshal(raw, &elems) != nil {
		return nil, false
	}
	for _, el := range elems {
		var b contentBlock
		if jsonKind(el) != '{' || json.Unmarshal(el, &b) != nil {
			continue
		}
		blocks = append(blocks, b)
	}
	return blocks, true
}

// rawString returns raw's value when raw is a JSON string.
func rawString(raw json.RawMessage) (string, bool) {
	var s string
	if jsonKind(raw) != '"' || json.Unmarshal(raw, &s) != nil {
		return "", false
	}
	return s, true
}

// jsonKind is the first byte of a JSON value: '"', '[', '{', 'n'… or 0 when there is none.
func jsonKind(raw json.RawMessage) byte {
	raw = bytes.TrimLeft(raw, " \t\r\n")
	if len(raw) == 0 {
		return 0
	}
	return raw[0]
}

// numCtxFor is recap.py's num_ctx_for: the smallest Ollama context that holds the digest
// (about 3 runes a token) plus the prompt and the answer.
func numCtxFor(digest string) int {
	need := utf8.RuneCountInString(digest)/3 + 2500
	for _, n := range []int{8192, 16384, 32768, 65536} {
		if need <= n {
			return n
		}
	}
	return 65536
}

// pyIsSpace is Python's str.isspace for one rune. Go's unicode.IsSpace lacks U+001C..U+001F,
// which Python counts as whitespace (bidi class B or S); with them added the two sets are equal
// (checked over every code point against Python 3.15), so pyStrip trims what str.strip() trims.
func pyIsSpace(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }

// pyStrip is Python's str.strip().
func pyStrip(s string) string { return strings.TrimFunc(s, pyIsSpace) }

// pyLStrip is Python's str.lstrip().
func pyLStrip(s string) string { return strings.TrimLeftFunc(s, pyIsSpace) }

// capRunes is recap.py's cap: s when it has at most limit runes, else its first limit runes
// and " …".
func capRunes(s string, limit int) string {
	if utf8.RuneCountInString(s) <= limit {
		return s
	}
	return headRunes(s, limit) + " …"
}

// headRunes is Python's s[:n] for n ≥ 0.
func headRunes(s string, n int) string {
	i := 0
	for j := range s {
		if i == n {
			return s[:j]
		}
		i++
	}
	return s
}

// tailRunes is Python's s[-n:] for n > 0.
func tailRunes(s string, n int) string {
	i := len(s)
	for k := 0; k < n && i > 0; k++ {
		_, size := utf8.DecodeLastRuneInString(s[:i])
		i -= size
	}
	return s[i:]
}

// sliceRunes is Python's s[from:to] for 0 ≤ from ≤ to.
func sliceRunes(s string, from, to int) string {
	return headRunes(s[len(headRunes(s, from)):], to-from)
}
