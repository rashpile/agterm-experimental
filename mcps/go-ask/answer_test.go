package main

import (
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
