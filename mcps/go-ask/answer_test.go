package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The request every state test answers: testRequest asks "say ALPHA" of a claude pane, sent at
// sentAt. at(n) is n ms after the send.
const (
	sentAt  = 1793987000000
	mainSID = "f1637954-e014-4317-99a6-a9c9c6a07f69"
	nestSID = "0c7e5a12-3b44-4d1e-8f90-nested000001"
)

func at(n int64) int64 { return sentAt + n }

func promptRec(t int64, session, text string) turnRecord {
	return turnRecord{Event: "prompt", T: t, Session: session, Hash: promptHash(text)}
}

func stopRec(t int64, session, reply string) turnRecord {
	return turnRecord{Event: "stop", T: t, Session: session, Reply: strp(reply)}
}

type stateCase struct {
	name   string
	req    func(*request) // changes to testRequest; nil keeps it
	recs   []turnRecord
	status string     // the target pane's agterm status
	tree   treeLookup // default paneInTree
	want   requestResult
	detail []string // substrings of Detail; none means Detail must be empty
}

func runStateCases(t *testing.T, cases []stateCase) {
	t.Helper()
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := testRequest("0123456789ab")
			if r.SentAt != sentAt {
				t.Fatalf("testRequest sent_at %d, want %d", r.SentAt, sentAt)
			}
			if tc.req != nil {
				tc.req(&r)
			}
			target := Pane{Target: r.Target, Name: r.Name, Agent: r.Agent, Status: tc.status, paneID: r.PaneID}
			got := requestState(r, tc.recs, target, tc.tree)
			detail := got.Detail
			got.Detail = ""
			if got != tc.want {
				t.Errorf("state\n got %+v\nwant %+v", got, tc.want)
			}
			if len(tc.detail) == 0 && detail != "" {
				t.Errorf("detail %q, want none", detail)
			}
			for _, s := range tc.detail {
				if !strings.Contains(detail, s) {
					t.Errorf("detail %q lacks %q", detail, s)
				}
			}
		})
	}
}

// TestRequestStateTable has one case per row of the state table in the plan, in its order.
func TestRequestStateTable(t *testing.T) {
	doneTurn := []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "ALPHA")}
	runStateCases(t, []stateCase{
		{name: "stored state wins over a newer matching turn",
			req:  func(r *request) { r.State, r.Reply, r.FinishedAt = stateDone, "ALPHA", at(20) },
			recs: append(doneTurn, promptRec(at(30), mainSID, "say ALPHA"), stopRec(at(40), mainSID, "NEWER")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "stored interrupted",
			req:  func(r *request) { r.State, r.FinishedAt = stateInterrupted, at(15) },
			recs: doneTurn,
			want: requestResult{State: stateInterrupted, FinishedAt: at(15)}},
		{name: "unsupported agent",
			req:    func(r *request) { r.Agent = "codex" },
			recs:   doneTurn,
			want:   requestResult{State: stateUnsupported},
			detail: []string{"Claude only", "codex"}},
		{name: "unsupported without an agent",
			req:    func(r *request) { r.Agent = "" },
			recs:   doneTurn,
			want:   requestResult{State: stateUnsupported},
			detail: []string{"Claude only", "no agent was detected"}},
		{name: "done",
			recs: doneTurn,
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "interrupted by an empty stop",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "")},
			want: requestResult{State: stateInterrupted, FinishedAt: at(20)}},
		{name: "interrupted by a later prompt of the same session",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), promptRec(at(30), mainSID, "never mind"),
				stopRec(at(40), mainSID, "OK")},
			want: requestResult{State: stateInterrupted, FinishedAt: at(30)}},
		{name: "blocked",
			recs:   []turnRecord{promptRec(at(10), mainSID, "say ALPHA")},
			status: "blocked",
			want:   requestResult{State: stateBlocked},
			detail: []string{"answer it in agterm"}},
		{name: "running",
			recs:   []turnRecord{promptRec(at(10), mainSID, "say ALPHA")},
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "lost: a turn started before the send finished after it",
			recs: []turnRecord{promptRec(at(-500), mainSID, "long job"), stopRec(at(20), mainSID, "job done")},
			want: requestResult{State: stateLost}, detail: []string{"folded into the turn"}},
		{name: "pending: empty journal",
			want:   requestResult{State: statePending},
			detail: []string{"hooks may not be installed", "Claude changed the text"}},
		{name: "pending: only a finished turn of another text",
			recs:   []turnRecord{promptRec(at(10), mainSID, "say BETA"), stopRec(at(20), mainSID, "BETA")},
			want:   requestResult{State: statePending},
			detail: []string{"hooks may not be installed"}},
	})
}

func TestRequestStateMatching(t *testing.T) {
	runStateCases(t, []stateCase{
		{name: "records before sent_at are ignored",
			recs: []turnRecord{promptRec(at(-50), mainSID, "say ALPHA"), stopRec(at(-40), mainSID, "OLD")},
			want: requestResult{State: statePending}, detail: []string{"hooks may not be installed"}},
		{name: "a matching prompt before sent_at does not make its stop the reply",
			recs: []turnRecord{promptRec(at(-50), mainSID, "say ALPHA"), stopRec(at(5), mainSID, "OLD")},
			want: requestResult{State: stateLost}, detail: []string{"folded"}},
		{name: "a record at exactly sent_at counts",
			recs: []turnRecord{promptRec(at(0), mainSID, "say ALPHA"), stopRec(at(0), mainSID, "ALPHA")},
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(0)}},
		{name: "the prompt matches after normalization",
			recs: []turnRecord{
				{Event: "prompt", T: at(10), Session: mainSID, Hash: promptHash(wrapPasted("f033", "say ALPHA  "))},
				stopRec(at(20), mainSID, "ALPHA")},
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "two matching turns: the last one wins",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "FIRST"),
				promptRec(at(30), mainSID, "say ALPHA"), stopRec(at(40), mainSID, "SECOND")},
			want: requestResult{State: stateDone, Reply: "SECOND", FinishedAt: at(40)}},
		{name: "two matching turns: the last one is still running",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "FIRST"),
				promptRec(at(30), mainSID, "say ALPHA")},
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "stop-hook continuation: the last stop gives the reply",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "draft"),
				stopRec(at(30), mainSID, "final")},
			want: requestResult{State: stateDone, Reply: "final", FinishedAt: at(30)}},
		{name: "a stop after the turn's next prompt is not its reply",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "ALPHA"),
				promptRec(at(30), mainSID, "next"), stopRec(at(40), mainSID, "NEXT")},
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "a nested session inside the matching turn neither splits it nor gives its reply",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"),
				promptRec(at(12), nestSID, "summarize the file"), stopRec(at(15), nestSID, "NESTED"),
				stopRec(at(20), mainSID, "ALPHA")},
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "a finished nested session inside a running matching turn",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"),
				promptRec(at(12), nestSID, "summarize the file"), stopRec(at(15), nestSID, "NESTED")},
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "a later prompt of another session does not interrupt the turn",
			recs:   []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), promptRec(at(20), nestSID, "other")},
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "the matching turn takes its reply from its own session only",
			recs: []turnRecord{promptRec(at(5), mainSID, "run claude -p"),
				promptRec(at(10), nestSID, "say ALPHA"), stopRec(at(15), nestSID, "NESTED ALPHA"),
				stopRec(at(20), mainSID, "outer done")},
			want: requestResult{State: stateDone, Reply: "NESTED ALPHA", FinishedAt: at(15)}},
		{name: "an orphan stop is per session",
			recs: []turnRecord{promptRec(at(5), nestSID, "other"), stopRec(at(10), mainSID, "earlier turn")},
			want: requestResult{State: stateLost}, detail: []string{"folded"}},
		{name: "a stop whose prompt is in the window is no orphan",
			recs: []turnRecord{promptRec(at(5), nestSID, "other"), stopRec(at(10), nestSID, "x")},
			want: requestResult{State: statePending}, detail: []string{"hooks may not be installed"}},
		{name: "unknown events are ignored",
			recs: []turnRecord{{Event: "note", T: at(5), Session: mainSID},
				promptRec(at(10), mainSID, "say ALPHA"), {Event: "note", T: at(15), Session: mainSID},
				stopRec(at(20), mainSID, "ALPHA")},
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "a stop record without a reply field counts as empty",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), {Event: "stop", T: at(20), Session: mainSID}},
			want: requestResult{State: stateInterrupted, FinishedAt: at(20)}},
	})
}

// TestRequestStateTree: a target missing from the tree, or no tree at all, skips the blocked
// check, never makes a request lost, and detail says which.
func TestRequestStateTree(t *testing.T) {
	running := []turnRecord{promptRec(at(10), mainSID, "say ALPHA")}
	runStateCases(t, []stateCase{
		{name: "not in tree: blocked status not checked",
			recs: running, status: "blocked", tree: paneNotInTree,
			want: requestResult{State: stateRunning}, detail: []string{"not in agterm's tree", "frontmost window"}},
		{name: "tree unavailable: blocked status not checked",
			recs: running, status: "blocked", tree: treeUnavailable,
			want: requestResult{State: stateRunning}, detail: []string{"tree is unavailable"}},
		{name: "not in tree: no turn is pending, not lost",
			tree: paneNotInTree,
			want: requestResult{State: statePending}, detail: []string{"hooks may not be installed", "not in agterm's tree"}},
		{name: "tree unavailable: no turn is pending, not lost",
			tree: treeUnavailable,
			want: requestResult{State: statePending}, detail: []string{"hooks may not be installed", "tree is unavailable"}},
		{name: "not in tree: an orphan stop is still lost",
			recs: []turnRecord{stopRec(at(10), mainSID, "earlier turn")}, tree: paneNotInTree,
			want: requestResult{State: stateLost}, detail: []string{"folded", "not in agterm's tree"}},
		{name: "not in tree: done carries no note",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "ALPHA")},
			tree: paneNotInTree,
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "tree unavailable: interrupted carries no note",
			recs: []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "")},
			tree: treeUnavailable,
			want: requestResult{State: stateInterrupted, FinishedAt: at(20)}},
	})
}

// writeTurns appends recs to the journal at path, as the hook would.
func writeTurns(t *testing.T, path string, recs ...turnRecord) {
	t.Helper()
	for _, rec := range recs {
		if err := appendTurn(path, rec); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAnswerAfterAsk(t *testing.T) {
	dir := t.TempDir()
	s := &server{ctl: fakeAgtermctl(t), dir: dir}
	ctx := context.Background()
	_, sent, err := s.ask(ctx, nil, askIn{Text: "say ALPHA", SessionID: "AAAA1111-0000"})
	if err != nil {
		t.Fatal(err)
	}
	r, err := loadRequest(dir, sent.RequestID)
	if err != nil {
		t.Fatal(err)
	}
	journal := journalPath(dir, "PANE-A1-LEFT")
	answer := func(want answerOut, detail string) {
		t.Helper()
		_, got, err := s.answer(ctx, nil, answerIn{})
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(got.Detail, detail) {
			t.Errorf("detail %q lacks %q", got.Detail, detail)
		}
		if detail == "" && got.Detail != "" {
			t.Errorf("detail %q, want none", got.Detail)
		}
		got.Detail = ""
		if got != want {
			t.Errorf("answer\n got %+v\nwant %+v", got, want)
		}
	}
	base := answerOut{RequestID: sent.RequestID, Target: "AAAA1111-0000", Name: "agent", SentAt: r.SentAt}
	with := func(state, reply string, finishedAt int64) answerOut {
		o := base
		o.State, o.Reply, o.FinishedAt = state, reply, finishedAt
		return o
	}

	answer(with(statePending, "", 0), "hooks may not be installed")
	writeTurns(t, journal, promptRec(r.SentAt+10, mainSID, "say ALPHA"))
	answer(with(stateRunning, "", 0), "")
	if got, _ := loadRequest(dir, sent.RequestID); got.State != "" {
		t.Errorf("running stored state %q, want none", got.State)
	}

	writeTurns(t, journal, stopRec(r.SentAt+20, mainSID, "ALPHA"))
	done := with(stateDone, "ALPHA", r.SentAt+20)
	answer(done, "")
	stored, err := loadRequest(dir, sent.RequestID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.State != stateDone || stored.Reply != "ALPHA" || stored.FinishedAt != r.SentAt+20 {
		t.Errorf("stored request %+v, want done ALPHA", stored)
	}

	// Delivered: a newer matching turn no longer rebinds the request.
	writeTurns(t, journal, promptRec(r.SentAt+30, mainSID, "say ALPHA"), stopRec(r.SentAt+40, mainSID, "NEWER"))
	answer(done, "")

	// The stored result needs neither the journal nor the tree.
	if err := os.Remove(journal); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(journal, 0o700); err != nil {
		t.Fatal(err)
	}
	answer(done, "")
}

func TestAnswerStoresInterrupted(t *testing.T) {
	dir := t.TempDir()
	s := &server{ctl: fakeAgtermctl(t), dir: dir}
	r := testRequest("0123456789ab")
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	writeTurns(t, journalPath(dir, r.PaneID), promptRec(at(10), mainSID, "say ALPHA"), promptRec(at(30), mainSID, "stop"))
	_, out, err := s.answer(context.Background(), nil, answerIn{RequestID: r.ID})
	if err != nil || out.State != stateInterrupted || out.FinishedAt != at(30) {
		t.Fatalf("answer = %+v, %v; want interrupted at %d", out, err, at(30))
	}
	if got, _ := loadRequest(dir, r.ID); got.State != stateInterrupted || got.FinishedAt != at(30) {
		t.Errorf("stored request %+v, want interrupted", got)
	}
}

func TestAnswerErrors(t *testing.T) {
	dir := t.TempDir()
	s := &server{ctl: fakeAgtermctl(t), dir: dir}
	ctx := context.Background()

	_, _, err := s.answer(ctx, nil, answerIn{})
	if err == nil || err.Error() != "no request_id and no ask in this session; pass the request_id ask returned" {
		t.Errorf("no id and no ask: %v", err)
	}
	if _, _, err := s.answer(ctx, nil, answerIn{RequestID: "0123456789ab"}); err == nil ||
		!strings.Contains(err.Error(), `unknown request_id "0123456789ab"`) {
		t.Errorf("unknown id: %v", err)
	}
	if _, _, err := s.answer(ctx, nil, answerIn{RequestID: "../x"}); err == nil || !strings.Contains(err.Error(), "invalid request_id") {
		t.Errorf("bad id: %v", err)
	}

	// A directory in the journal's place: the journal exists but cannot be read.
	r := testRequest("0123456789ab")
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(journalPath(dir, r.PaneID), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.answer(ctx, nil, answerIn{RequestID: r.ID}); err == nil || !strings.Contains(err.Error(), "read the turn journal") {
		t.Errorf("unreadable journal: %v", err)
	}
}

// TestAnswerUnsupportedReadsNothing: a non-Claude target is answered from the request alone,
// without the journal or the tree.
func TestAnswerUnsupportedReadsNothing(t *testing.T) {
	dir := t.TempDir()
	ctl, calls := fakeAgtermctlTree(t, treeJSON)
	s := &server{ctl: ctl, dir: dir}
	r := testRequest("0123456789ab")
	r.Agent = "codex"
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(journalPath(dir, r.PaneID), 0o700); err != nil {
		t.Fatal(err)
	}
	_, out, err := s.answer(context.Background(), nil, answerIn{RequestID: r.ID})
	if err != nil || out.State != stateUnsupported || !strings.Contains(out.Detail, "Claude only") {
		t.Errorf("answer = %+v, %v; want unsupported", out, err)
	}
	if c := calls(); c != "" {
		t.Errorf("agtermctl called:\n%s", c)
	}
	if got, _ := loadRequest(dir, r.ID); got.State != "" {
		t.Errorf("unsupported stored state %q, want none", got.State)
	}
}

// answerTree has the test request's pane as the right split of AAAA1111-0000, with the given
// status and pane id.
func answerTree(status, paneID string) string {
	return fmt.Sprintf(`{"workspaces":[{"name":"w","active":true,"sessions":[
 {"id":"AAAA1111-0000","name":"agent","status":%q,"statusPane":"right","foreground":["zsh"],"splitForeground":["claude"],
  "surfaces":[{"kind":"left","paneID":"PANE-A1-LEFT"},{"kind":"right","paneID":%q}]}]}]}`, status, paneID)
}

// TestAnswerTree: the handler looks the target up in the tree by pane id; a pane missing from
// the tree or a failing tree still answers from the journal.
func TestAnswerTree(t *testing.T) {
	running := []turnRecord{promptRec(at(10), mainSID, "say ALPHA")}
	finished := []turnRecord{promptRec(at(10), mainSID, "say ALPHA"), stopRec(at(20), mainSID, "ALPHA")}
	failing := agtermctl{bin: filepath.Join(t.TempDir(), "no-agtermctl")}
	treeCtl := func(status, paneID string) agtermctl {
		ctl, _ := fakeAgtermctlTree(t, answerTree(status, paneID))
		return ctl
	}
	for _, tc := range []struct {
		name   string
		ctl    agtermctl
		recs   []turnRecord
		state  string
		reply  string
		detail string // substring of Detail; "" means none
	}{
		{"in tree, active", treeCtl("active", "PANE-A1-RIGHT"), running, stateRunning, "", ""},
		{"in tree, blocked", treeCtl("blocked", "PANE-A1-RIGHT"), running, stateBlocked, "", "answer it in agterm"},
		{"not in tree, blocked elsewhere", treeCtl("blocked", "PANE-OTHER"), running, stateRunning, "", "not in agterm's tree"},
		{"tree fails, running", failing, running, stateRunning, "", "tree is unavailable"},
		{"tree fails, done from the journal", failing, finished, stateDone, "ALPHA", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			s := &server{ctl: tc.ctl, dir: dir}
			r := testRequest("0123456789ab")
			if err := saveRequest(dir, r); err != nil {
				t.Fatal(err)
			}
			writeTurns(t, journalPath(dir, r.PaneID), tc.recs...)
			_, out, err := s.answer(context.Background(), nil, answerIn{RequestID: r.ID})
			if err != nil {
				t.Fatal(err)
			}
			if out.State != tc.state || out.Reply != tc.reply {
				t.Errorf("answer %+v, want %s %q", out, tc.state, tc.reply)
			}
			if tc.detail == "" && out.Detail != "" || !strings.Contains(out.Detail, tc.detail) {
				t.Errorf("detail %q, want %q", out.Detail, tc.detail)
			}
		})
	}
}
