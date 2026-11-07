package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"
)

const recapFixture = "testdata/recap-transcript.jsonl"

// --- row builders: tests write transcript lines as JSON, as Claude does -----------------------

func jsonLine(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func tsAt(hhmm string) string { return "2026-11-05T" + hhmm + ":00.000Z" }

func userLine(t *testing.T, hhmm string, content any) string {
	return jsonLine(t, map[string]any{"type": "user", "timestamp": tsAt(hhmm), "message": map[string]any{"role": "user", "content": content}})
}

func metaLine(t *testing.T, content any) string {
	return jsonLine(t, map[string]any{"type": "user", "isMeta": true, "timestamp": tsAt("08:00"), "message": map[string]any{"content": content}})
}

func asstLine(t *testing.T, blocks ...any) string {
	return jsonLine(t, map[string]any{"type": "assistant", "timestamp": tsAt("08:00"), "message": map[string]any{"role": "assistant", "content": blocks}})
}

func queuedLine(t *testing.T, hhmm string, prompt any, kind string) string {
	return jsonLine(t, map[string]any{"type": "attachment", "timestamp": tsAt(hhmm),
		"attachment": map[string]any{"type": "queued_command", "prompt": prompt, "origin": map[string]any{"kind": kind}}})
}

func textBlock(s string) map[string]any { return map[string]any{"type": "text", "text": s} }

func toolUse(id, name string, input map[string]any) map[string]any {
	return map[string]any{"type": "tool_use", "id": id, "name": name, "input": input}
}

func toolResult(id string, content any) map[string]any {
	return map[string]any{"type": "tool_result", "tool_use_id": id, "content": content}
}

// rowsOf decodes transcript lines the way readTranscript does.
func rowsOf(t *testing.T, lines ...string) []transcriptRow {
	t.Helper()
	rows := make([]transcriptRow, 0, len(lines))
	for _, l := range lines {
		var r transcriptRow
		if err := json.Unmarshal([]byte(l), &r); err != nil {
			t.Fatalf("bad test line %s: %v", l, err)
		}
		rows = append(rows, r)
	}
	return rows
}

func writeTranscript(t *testing.T, lines ...string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "session.jsonl")
	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

// --- buildDigest --------------------------------------------------------------------------------

func TestBuildDigestTurnCut(t *testing.T) {
	rows := rowsOf(t,
		asstLine(t, textBlock("before")),
		userLine(t, "09:00", "first"),
		asstLine(t, textBlock("one")),
		userLine(t, "09:10", "second"),
		asstLine(t, textBlock("two")),
		userLine(t, "09:20", "third"),
		asstLine(t, textBlock("three")),
	)
	third := "\n### USER 09:20 UTC\nthird\nASSISTANT: three"
	second := "\n### USER 09:10 UTC\nsecond\nASSISTANT: two" + "\n" + third
	all := "ASSISTANT: before\n\n### USER 09:00 UTC\nfirst\nASSISTANT: one\n" + second
	tests := []struct {
		turns int
		want  string
	}{
		{1, third},
		{2, second},
		{3, "\n### USER 09:00 UTC\nfirst\nASSISTANT: one\n" + second},
		{4, all}, // more turns than prompts: from the first row, as recap.py does
		{50, all},
		{0, third}, // below 1 counts as 1
		{-3, third},
	}
	for _, tc := range tests {
		if got := buildDigest(rows, tc.turns); got != tc.want {
			t.Errorf("turns=%d\n got %q\nwant %q", tc.turns, got, tc.want)
		}
	}
}

func TestBuildDigestNoRows(t *testing.T) {
	if got := buildDigest(nil, 6); got != "" {
		t.Errorf("no rows: %q", got)
	}
	rows := rowsOf(t, metaLine(t, "meta only"), jsonLine(t, map[string]any{"type": "summary", "summary": "x"}))
	if got := buildDigest(rows, 6); got != "" {
		t.Errorf("rows without items: %q", got)
	}
}

func TestBuildDigestAnswers(t *testing.T) {
	rows := rowsOf(t,
		userLine(t, "09:00", "go"),
		asstLine(t, toolUse("ask1", "AskUserQuestion", map[string]any{"questions": []any{map[string]any{"question": "Который?"}}})),
		userLine(t, "09:01", []any{toolResult("ask1", []any{textBlock("  \"Который?\"=\"Второй\"  ")})}),
		asstLine(t, toolUse("mt1", "Bash", map[string]any{"command": "mt ask \"Влить?\" --options да,нет"})),
		userLine(t, "09:02", []any{toolResult("mt1", "да\n")}),
		asstLine(t,
			toolUse("smt", "Bash", map[string]any{"command": "smt ask x", "description": "smt"}),
			toolUse("asking", "Bash", map[string]any{"command": "mt asking", "description": "asking"}),
			toolUse("newline", "Bash", map[string]any{"command": "mt\nask", "description": "newline"}),
			toolUse("other", "Read", map[string]any{"file_path": "/f"})),
		userLine(t, "09:03", []any{
			toolResult("smt", "no"), toolResult("asking", "no"), toolResult("newline", "no"),
			toolResult("other", "no"), toolResult("unknown", "no"),
		}),
	)
	want := strings.Join([]string{
		"\n### USER 09:00 UTC\ngo",
		"  - tool AskUserQuestion: ",
		"  - USER ANSWER: \"Который?\"=\"Второй\"",
		"  - tool Bash: mt ask \"Влить?\" --options да,нет",
		"  - USER ANSWER: да",
		"  - tool Bash: smt",
		"  - tool Bash: asking",
		"  - tool Bash: newline",
		"  - tool Read: /f",
	}, "\n")
	if got := buildDigest(rows, 6); got != want {
		t.Errorf("\n got %q\nwant %q", got, want)
	}
}

func TestBuildDigestToolDetail(t *testing.T) {
	rows := rowsOf(t,
		userLine(t, "09:00", "go"),
		asstLine(t,
			toolUse("a", "Bash", map[string]any{"description": "Show log", "command": "git log"}),
			toolUse("b", "Bash", map[string]any{"command": "git status"}),
			toolUse("c", "Edit", map[string]any{"file_path": "/x.go", "old_string": "a"}),
			toolUse("d", "WebSearch", map[string]any{"query": "ollama chat"}),
			toolUse("e", "Grep", map[string]any{"pattern": "x"}),
			toolUse("f", "Bash", map[string]any{"description": 5, "command": "echo non-string description"}),
			map[string]any{"type": "tool_use", "id": "g", "name": "Bash", "input": "not an object"},
			map[string]any{"type": "tool_use", "id": "h", "input": map[string]any{"query": "no name"}}),
	)
	want := strings.Join([]string{
		"\n### USER 09:00 UTC\ngo",
		"  - tool Bash: Show log",
		"  - tool Bash: git status",
		"  - tool Edit: /x.go",
		"  - tool WebSearch: ollama chat",
		"  - tool Grep: ",
		"  - tool Bash: echo non-string description",
		"  - tool Bash: ",
		"  - tool : no name",
	}, "\n")
	if got := buildDigest(rows, 6); got != want {
		t.Errorf("\n got %q\nwant %q", got, want)
	}
}

func TestBuildDigestCaps(t *testing.T) {
	ru := func(n int) string { return strings.Repeat("ж", n) }
	rows := rowsOf(t,
		userLine(t, "09:00", ru(recapUserCap+100)),
		asstLine(t,
			textBlock(ru(recapTextCap+1)),  // mid-turn
			textBlock(ru(recapFinalCap+1)), // final
			toolUse("q", "AskUserQuestion", map[string]any{}),
			toolUse("t", "Agent", map[string]any{"description": ru(recapToolCap + 30)}),
			toolUse("c", "Bash", map[string]any{"command": ru(recapCommandCap + 30)})),
		userLine(t, "09:01", []any{toolResult("q", "конец"+ru(recapAnswerTail+50)+"щ")}),
		queuedLine(t, "09:02", "<task-notification>"+ru(recapSubagentCap)+"</task-notification>", "task-notification"),
		queuedLine(t, "09:03", ru(recapUserCap+1), "human"),
		asstLine(t, textBlock(ru(recapTextCap)), textBlock(ru(recapFinalCap))), // at the caps: whole
	)
	want := strings.Join([]string{
		"\n### USER 09:00 UTC\n" + ru(recapUserCap), // a prompt is cut without " …"
		"ASSISTANT: " + ru(recapTextCap) + " …",
		"ASSISTANT: " + ru(recapFinalCap) + " …",
		"  - tool AskUserQuestion: ",
		"  - tool Agent: " + ru(recapToolCap),
		"  - tool Bash: " + ru(recapCommandCap),
		"  - USER ANSWER: " + ru(recapAnswerTail-1) + "щ", // the answer's end
		"  - subagent report arrived: <task-notification>" + ru(recapSubagentCap-len("<task-notification>")),
		"\n### USER (sent mid-turn) 09:03 UTC\n" + ru(recapUserCap),
		"ASSISTANT: " + ru(recapTextCap),
		"ASSISTANT: " + ru(recapFinalCap),
	}, "\n")
	got := buildDigest(rows, 6)
	if got != want {
		t.Errorf("caps differ\n got %s\nwant %s", summarizeRunes(got), summarizeRunes(want))
	}
	if !utf8.ValidString(got) {
		t.Error("a cap split a rune")
	}
}

// summarizeRunes shows each line as its rune count and ends, for readable cap failures.
func summarizeRunes(s string) string {
	var b strings.Builder
	for _, l := range strings.Split(s, "\n") {
		r := []rune(l)
		if len(r) > 40 {
			l = string(r[:20]) + "…" + string(r[len(r)-20:])
		}
		fmt.Fprintf(&b, "\n  [%d] %q", len(r), l)
	}
	return b.String()
}

func TestBuildDigestFinalRule(t *testing.T) {
	long := strings.Repeat("ы", recapTextCap+10)
	capped := "ASSISTANT: " + strings.Repeat("ы", recapTextCap) + " …"
	whole := "ASSISTANT: " + long
	rows := rowsOf(t,
		userLine(t, "09:00", "a"),
		asstLine(t, textBlock(long), textBlock(long)), // first mid, second final (a user item follows)
		queuedLine(t, "09:01", "mid-turn", "human"),
		asstLine(t, textBlock(long), toolUse("x", "Read", map[string]any{"file_path": "/f"})), // final: a tool line is not a text
		queuedLine(t, "09:02", "report", "task-notification"),
		userLine(t, "09:03", "b"),
		asstLine(t, textBlock(long), textBlock(long)), // the last text of the digest is final
	)
	want := strings.Join([]string{
		"\n### USER 09:00 UTC\na", capped, whole,
		"\n### USER (sent mid-turn) 09:01 UTC\nmid-turn", whole,
		"  - tool Read: /f", "  - subagent report arrived: report",
		"\n### USER 09:03 UTC\nb", capped, whole,
	}, "\n")
	if got := buildDigest(rows, 6); got != want {
		t.Errorf("final rule differs\n got %s\nwant %s", summarizeRunes(got), summarizeRunes(want))
	}
}

func TestBuildDigestCleansUser(t *testing.T) {
	tests := []struct {
		name    string
		content any
		want    string
	}{
		{"skill invocation with its body",
			[]any{
				textBlock("<command-message>make-task is running…</command-message>\n<command-name>/make-task</command-name>\n<command-args></command-args>"),
				textBlock("Base directory for this skill: /s\n\n# body\nmore"),
			},
			"<command-name>/make-task</command-name>"},
		{"args kept when not empty",
			"<command-message>x</command-message>\n<command-name>/x</command-name>\n<command-args>go</command-args>",
			"<command-name>/x</command-name>\n<command-args>go</command-args>"},
		{"whitespace stripped like str.strip", "  текст　\x1f", "текст"},
		{"text blocks joined by a space, image dropped",
			[]any{textBlock("смотри"), map[string]any{"type": "image", "source": map[string]any{"data": "AAAA"}}, textBlock("сюда")},
			"смотри сюда"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			want := "\n### USER 09:00 UTC\n" + tc.want
			if got := buildDigest(rowsOf(t, userLine(t, "09:00", tc.content)), 1); got != want {
				t.Errorf("\n got %q\nwant %q", got, want)
			}
		})
	}
}

func TestBuildDigestSkipsRows(t *testing.T) {
	rows := rowsOf(t,
		userLine(t, "09:00", "real"),
		metaLine(t, "Base directory for this skill: /s"),
		userLine(t, "09:01", "<local-command-stdout>ok</local-command-stdout>"),
		userLine(t, "09:02", "  \n<local-command-caveat>x</local-command-caveat>"),
		userLine(t, "09:03", "<command-name>/remote-control</command-name>"),
		userLine(t, "09:04", []any{toolResult("t1", "result")}),
		userLine(t, "09:05", "   "),
		userLine(t, "09:06", []any{}),
		userLine(t, "09:07", nil),
		userLine(t, "09:08", []any{"not a block", 5, nil}),
		jsonLine(t, map[string]any{"type": "attachment", "attachment": map[string]any{"type": "file", "prompt": "x"}}),
		jsonLine(t, map[string]any{"type": "system", "message": map[string]any{"content": "sys"}}),
		asstLine(t, textBlock("  \n "), map[string]any{"type": "thinking", "thinking": "hm"}),
		jsonLine(t, map[string]any{"type": "assistant", "message": map[string]any{"content": "a string content"}}),
	)
	if got, want := buildDigest(rows, 1), "\n### USER 09:00 UTC\nreal"; got != want {
		t.Errorf("\n got %q\nwant %q", got, want)
	}
}

// TestBuildDigestMalformedRows: rows of shapes Claude does not write, where recap.py raises or
// reads on (see the top of recap_digest.go). Go never stops: it reads the odd value as empty or
// drops the line or block that does not decode.
func TestBuildDigestMalformedRows(t *testing.T) {
	ask := asstLine(t, toolUse("a1", "AskUserQuestion", map[string]any{}))
	for _, tc := range []struct {
		name  string
		lines []string
		want  []string // digest lines after the 09:00 prompt
	}{
		// read as empty where recap.py raises
		{"user message null", []string{jsonLine(t, map[string]any{"type": "user", "message": nil})}, nil},
		{"user message missing", []string{jsonLine(t, map[string]any{"type": "user"})}, nil},
		{"user content null", []string{userLine(t, "09:01", nil)}, nil},
		{"user content a number", []string{userLine(t, "09:01", 5)}, nil},
		{"user text null", []string{userLine(t, "09:01", []any{map[string]any{"type": "text", "text": nil}})}, nil},
		{"assistant content a number", []string{jsonLine(t, map[string]any{"type": "assistant", "message": map[string]any{"content": 5}})}, nil},
		{"assistant text null", []string{asstLine(t, map[string]any{"type": "text", "text": nil}, textBlock("kept"))}, []string{"ASSISTANT: kept"}},
		{"answer content a number", []string{ask, userLine(t, "09:01", []any{toolResult("a1", 5)})},
			[]string{"  - tool AskUserQuestion: ", "  - USER ANSWER: "}},
		{"tool input not an object", []string{asstLine(t,
			map[string]any{"type": "tool_use", "id": "s", "name": "Bash", "input": "git status"},
			map[string]any{"type": "tool_use", "id": "l", "name": "Bash", "input": []any{"git status"}},
			map[string]any{"type": "tool_use", "id": "n", "name": "Bash", "input": 5})},
			[]string{"  - tool Bash: ", "  - tool Bash: ", "  - tool Bash: "}},
		{"command a number", []string{asstLine(t, toolUse("c", "Bash", map[string]any{"command": 5, "file_path": "/f"}))},
			[]string{"  - tool Bash: /f"}},
		{"queued prompt null, a number, an object", []string{
			queuedLine(t, "09:01", nil, "human"), queuedLine(t, "09:02", 5, "task-notification"),
			queuedLine(t, "09:03", map[string]any{"text": "x"}, "human")},
			[]string{"\n### USER (sent mid-turn) 09:01 UTC\n", "  - subagent report arrived: ", "\n### USER (sent mid-turn) 09:03 UTC\n"}},
		// the line does not decode and is dropped
		{"timestamp a number", []string{jsonLine(t, map[string]any{"type": "user", "timestamp": 5, "message": map[string]any{"content": "dropped"}})}, nil},
		{"isMeta not a bool", []string{ask, jsonLine(t, map[string]any{"type": "user", "isMeta": "yes",
			"message": map[string]any{"content": []any{toolResult("a1", "dropped")}}})}, []string{"  - tool AskUserQuestion: "}},
		{"message a string", []string{jsonLine(t, map[string]any{"type": "assistant", "message": "dropped"})}, nil},
		{"attachment a string", []string{jsonLine(t, map[string]any{"type": "attachment", "attachment": "dropped"})}, nil},
		{"origin a string", []string{jsonLine(t, map[string]any{"type": "attachment", "timestamp": tsAt("09:01"),
			"attachment": map[string]any{"type": "queued_command", "prompt": "dropped", "origin": "human"}})}, nil},
		// the block does not decode and is dropped
		{"text a number", []string{asstLine(t, map[string]any{"type": "text", "text": 5}, textBlock("kept"))}, []string{"ASSISTANT: kept"}},
		{"tool name a number", []string{asstLine(t, map[string]any{"type": "tool_use", "id": "x", "name": 5, "input": map[string]any{"query": "q"}})}, nil},
		{"tool id a number", []string{asstLine(t, map[string]any{"type": "tool_use", "id": 5, "name": "Read", "input": map[string]any{"file_path": "/f"}})}, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rows, err := readTranscript(writeTranscript(t, append([]string{userLine(t, "09:00", "go")}, tc.lines...)...), 6)
			if err != nil {
				t.Fatal(err)
			}
			want := strings.Join(append([]string{"\n### USER 09:00 UTC\ngo"}, tc.want...), "\n")
			if got := buildDigest(rows, 6); got != want {
				t.Errorf("\n got %q\nwant %q", got, want)
			}
		})
	}
}

func TestBuildDigestQueued(t *testing.T) {
	rows := rowsOf(t,
		userLine(t, "09:00", "go"),
		queuedLine(t, "09:01", "и ещё", "human"),
		queuedLine(t, "09:02", "<task-notification>done</task-notification>", "task-notification"),
		queuedLine(t, "09:03", []any{textBlock("на скрине"), map[string]any{"type": "image", "source": map[string]any{"data": "iVBORw0KGgo="}}}, "human"),
		jsonLine(t, map[string]any{"type": "attachment", "timestamp": tsAt("09:04"), "attachment": map[string]any{"type": "queued_command", "prompt": "no origin"}}),
	)
	want := strings.Join([]string{
		"\n### USER 09:00 UTC\ngo",
		"\n### USER (sent mid-turn) 09:01 UTC\nи ещё",
		"  - subagent report arrived: <task-notification>done</task-notification>",
		"\n### USER (sent mid-turn) 09:03 UTC\nна скрине", // the image's base64 stays out
		"  - subagent report arrived: no origin",
	}, "\n")
	if got := buildDigest(rows, 6); got != want {
		t.Errorf("\n got %q\nwant %q", got, want)
	}
}

func TestBuildDigestTruncates(t *testing.T) {
	var lines []string
	for i := range 40 {
		lines = append(lines, userLine(t, "09:00", fmt.Sprintf("ход %d", i)), asstLine(t, textBlock(strings.Repeat("щ", 3000))))
	}
	got := buildDigest(rowsOf(t, lines...), 50)
	body, ok := strings.CutPrefix(got, recapCutPrefix)
	if !ok {
		t.Fatalf("no truncation prefix: %q", got[:80])
	}
	if n := utf8.RuneCountInString(body); n != recapMaxChars {
		t.Errorf("kept %d runes, want %d", n, recapMaxChars)
	}
	if !strings.HasSuffix(body, "\n### USER 09:00 UTC\nход 39\nASSISTANT: "+strings.Repeat("щ", 3000)) {
		t.Error("the newest turn is not at the end")
	}
	if strings.Contains(body, "ход 0\n") {
		t.Error("the oldest turn was kept")
	}
	// a short digest is not cut
	short := buildDigest(rowsOf(t, lines[:2]...), 1)
	if strings.HasPrefix(short, recapCutPrefix) {
		t.Errorf("a short digest was cut")
	}
}

func TestNumCtxFor(t *testing.T) {
	tests := []struct {
		runes, want int
	}{
		{0, 8192},
		{17078, 8192}, // 17078/3 + 2500 = 8192
		{17079, 16384},
		{41654, 16384},
		{41655, 32768},
		{90806, 32768},
		{90807, 65536},
		{189110, 65536},
		{300000, 65536}, // past the largest: the largest
	}
	for _, tc := range tests {
		if got := numCtxFor(strings.Repeat("д", tc.runes)); got != tc.want { // runes, not bytes
			t.Errorf("numCtxFor(%d runes) = %d, want %d", tc.runes, got, tc.want)
		}
	}
}

func TestRuneHelpers(t *testing.T) {
	s := "абвгд"
	if got := headRunes(s, 2); got != "аб" {
		t.Errorf("headRunes 2: %q", got)
	}
	if got := headRunes(s, 0); got != "" {
		t.Errorf("headRunes 0: %q", got)
	}
	if got := headRunes(s, 9); got != s {
		t.Errorf("headRunes past the end: %q", got)
	}
	if got := tailRunes(s, 2); got != "гд" {
		t.Errorf("tailRunes 2: %q", got)
	}
	if got := tailRunes(s, 9); got != s {
		t.Errorf("tailRunes past the start: %q", got)
	}
	if got := sliceRunes("2026-11-05T09:07:00.000Z", 11, 16); got != "09:07" {
		t.Errorf("sliceRunes timestamp: %q", got)
	}
	if got := sliceRunes("short", 11, 16); got != "" {
		t.Errorf("sliceRunes past the end: %q", got)
	}
	if got := capRunes(s, 5); got != s {
		t.Errorf("capRunes at the limit: %q", got)
	}
	if got := capRunes(s, 4); got != "абвг …" {
		t.Errorf("capRunes over the limit: %q", got)
	}
	if got := pyStrip("\x1c  x​ 　\x1f"); got != "x​" {
		t.Errorf("pyStrip: %q", got) // U+200B is not whitespace in Python either
	}
}

// --- readTranscript -----------------------------------------------------------------------------

func TestReadTranscriptTail(t *testing.T) {
	path := writeTranscript(t,
		asstLine(t, textBlock("before")),
		userLine(t, "09:00", "first"),
		asstLine(t, textBlock("one")),
		userLine(t, "09:10", "second"),
		userLine(t, "09:11", []any{toolResult("x", "r")}),
		asstLine(t, textBlock("two")),
		userLine(t, "09:20", "third"),
		asstLine(t, textBlock("three")),
	)
	tests := []struct {
		turns, rows int
		first       string
	}{
		{1, 2, "third"},
		{2, 5, "second"},
		{3, 7, "first"},
		{4, 8, "before"}, // fewer prompts than turns: from row 0
		{0, 2, "third"},
	}
	for _, tc := range tests {
		rows, err := readTranscript(path, tc.turns)
		if err != nil {
			t.Fatal(err)
		}
		if len(rows) != tc.rows {
			t.Errorf("turns=%d: %d rows, want %d", tc.turns, len(rows), tc.rows)
			continue
		}
		if got := textOf(rows[0].Message.Content); got != tc.first {
			t.Errorf("turns=%d: tail starts at %q, want %q", tc.turns, got, tc.first)
		}
	}
}

func TestReadTranscriptLongLineAndBadLines(t *testing.T) {
	long := strings.Repeat("ю", 70_000) // 140 KB: past bufio.Scanner's 64 KB default
	path := writeTranscript(t,
		userLine(t, "09:00", "first"),
		"{not json",
		"",
		"   ",
		asstLine(t, textBlock(long)),
		`["an array line"]`,
		userLine(t, "09:10", "second"),
	)
	rows, err := readTranscript(path, 5)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 {
		t.Fatalf("%d rows, want 3 (bad and blank lines skipped)", len(rows))
	}
	got := buildDigest(rows, 5)
	want := "\n### USER 09:00 UTC\nfirst\nASSISTANT: " + strings.Repeat("ю", recapFinalCap) + " …\n\n### USER 09:10 UTC\nsecond"
	if got != want {
		t.Errorf("digest of the long line differs: %s", summarizeRunes(got))
	}
}

func TestReadTranscriptErrors(t *testing.T) {
	if _, err := readTranscript(filepath.Join(t.TempDir(), "missing.jsonl"), 6); err == nil ||
		!strings.Contains(err.Error(), "cannot read the transcript") {
		t.Errorf("missing file: %v", err)
	}
	if _, err := readTranscript(t.TempDir(), 6); err == nil {
		t.Error("a directory read as a transcript gave no error")
	}
}

func TestReadTranscriptFixture(t *testing.T) {
	rows, err := readTranscript(recapFixture, 50)
	if err != nil {
		t.Fatal(err)
	}
	prompts := 0
	for i := range rows {
		if isRealUser(&rows[i]) {
			prompts++
		}
	}
	if prompts < 8 {
		t.Errorf("the fixture has %d real prompts, want at least 8", prompts)
	}
	d := buildDigest(rows, 50)
	for _, s := range []string{"  - USER ANSWER: да", "### USER (sent mid-turn)", "  - subagent report arrived: ",
		"\n### USER 09:10 UTC\n<command-name>/make-task</command-name>\n", " …\n"} {
		if !strings.Contains(d, s) {
			t.Errorf("fixture digest lacks %q", s)
		}
	}
	for _, s := range []string{"Тело навыка", "remote-control", "local-command", "Base directory", "smt"} {
		if strings.Contains(d, s) {
			t.Errorf("fixture digest has %q", s)
		}
	}
}

// --- parity with recap.py -----------------------------------------------------------------------

// pythonDigest runs recap.py --digest on path; t.Skip when python3 is not on PATH.
func pythonDigest(t *testing.T, path string, turns int) string {
	t.Helper()
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is not on PATH")
	}
	cmd := exec.Command(py, "../../commands/recap/recap.py", path, "--digest", "--turns", strconv.Itoa(turns))
	cmd.Env = append(os.Environ(), "PYTHONUTF8=1")
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("recap.py --digest --turns %d: %v: %s", turns, err, stderr.String())
	}
	return strings.TrimSuffix(string(out), "\n")
}

func goDigest(t *testing.T, path string, turns int) string {
	t.Helper()
	rows, err := readTranscript(path, turns)
	if err != nil {
		t.Fatal(err)
	}
	return buildDigest(rows, turns)
}

func assertSameDigest(t *testing.T, path string, turns int) {
	t.Helper()
	want := pythonDigest(t, path, turns)
	got := goDigest(t, path, turns)
	if got == want {
		return
	}
	g, w := strings.Split(got, "\n"), strings.Split(want, "\n")
	for i := 0; i < max(len(g), len(w)); i++ {
		var gl, wl string
		if i < len(g) {
			gl = g[i]
		}
		if i < len(w) {
			wl = w[i]
		}
		if gl != wl {
			t.Fatalf("turns=%d: digests differ at line %d\n  go %q\n  py %q", turns, i+1, gl, wl)
		}
	}
	t.Fatalf("turns=%d: digests differ", turns)
}

func TestDigestParityWithRecapPy(t *testing.T) {
	for _, turns := range []int{2, 6, 20} { // 20 is past the fixture's prompts
		t.Run(fmt.Sprintf("turns=%d", turns), func(t *testing.T) {
			assertSameDigest(t, recapFixture, turns)
		})
	}
}

func TestDigestParityTruncated(t *testing.T) {
	var lines []string
	for i := range 30 {
		lines = append(lines,
			userLine(t, fmt.Sprintf("09:%02d", i), fmt.Sprintf("ход %d: что дальше?", i)),
			asstLine(t, textBlock(strings.Repeat("Промежуточный текст. ", 40)), textBlock(strings.Repeat("Итог хода, всё зелёное. ", 200))))
	}
	path := writeTranscript(t, lines...)
	if d := goDigest(t, path, 30); !strings.HasPrefix(d, recapCutPrefix) {
		t.Fatal("the generated transcript does not reach the cut")
	}
	assertSameDigest(t, path, 30)
}
