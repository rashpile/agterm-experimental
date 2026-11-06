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

func promptRec(t int64, session, promptID, text string) turnRecord {
	return turnRecord{Event: "prompt", T: t, Session: session, PromptID: promptID, Hash: promptHash(text)}
}

func stopRec(t int64, session, promptID, reply string) turnRecord {
	return turnRecord{Event: "stop", T: t, Session: session, PromptID: promptID, Reply: strp(reply)}
}

// contRec is a stop after a Stop hook made Claude carry on: Claude sends stop_hook_active.
func contRec(t int64, session, promptID, reply string) turnRecord {
	rec := stopRec(t, session, promptID, reply)
	rec.StopHookActive = true
	return rec
}

func recs(r ...turnRecord) []turnRecord { return r }

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

// Substrings of the details that say why a done or interrupted result is not final.
var (
	queuedNote  = []string{"queued while another turn ran", "folded", "never final"}
	ownTurnNote = []string{"queued while another turn ran", "most likely the message's own", "never final"}
	queueNote   = []string{"journal shows a message queued", "not final"}
	noIDNote    = []string{"without Claude's prompt_id", "not final"}
	noStopNote  = []string{"ran no Stop", "not final"}
	sameNote    = []string{"sent more than once", "not final"}
)

// TestRequestStateTable has one case per row of the state table in the plan, in its order.
func TestRequestStateTable(t *testing.T) {
	prompt := promptRec(at(10), mainSID, "p1", "say ALPHA")
	nextTurn := promptRec(at(30), mainSID, "p2", "next")
	runStateCases(t, []stateCase{
		{name: "a final stored result wins over a newer matching turn",
			req: func(r *request) {
				r.State, r.Reply, r.FinishedAt, r.Final = stateDone, "ALPHA", at(20), true
			},
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA"),
				promptRec(at(30), mainSID, "p2", "say ALPHA"), stopRec(at(40), mainSID, "p2", "NEWER")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "final stored interrupted",
			req:  func(r *request) { r.State, r.FinishedAt, r.Final = stateInterrupted, at(15), true },
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA")),
			want: requestResult{State: stateInterrupted, FinishedAt: at(15), Final: true}},
		{name: "a stored result that is not final is ignored",
			req:  func(r *request) { r.State, r.Reply, r.FinishedAt = stateDone, "OLD", at(5) },
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "unsupported agent",
			req:    func(r *request) { r.Agent = "codex" },
			recs:   recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA")),
			want:   requestResult{State: stateUnsupported, Final: true},
			detail: []string{"Claude only", "codex"}},
		{name: "unsupported without an agent",
			req:    func(r *request) { r.Agent = "" },
			recs:   recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA")),
			want:   requestResult{State: stateUnsupported, Final: true},
			detail: []string{"Claude only", "no agent was detected"}},
		{name: "done, final once the session's next turn started",
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA"), nextTurn),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "done, not final while no later turn shows the turn is over",
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", "ALPHA")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "interrupted by an empty stop",
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", ""), nextTurn),
			want: requestResult{State: stateInterrupted, FinishedAt: at(20), Final: true}},
		{name: "an empty stop with no later turn is not final",
			recs: recs(prompt, stopRec(at(20), mainSID, "p1", "")),
			want: requestResult{State: stateInterrupted, FinishedAt: at(20)}},
		{name: "interrupted by the session's next turn: no Stop, never final",
			recs:   recs(prompt, promptRec(at(30), mainSID, "p2", "never mind"), stopRec(at(40), mainSID, "p2", "OK")),
			want:   requestResult{State: stateInterrupted, FinishedAt: at(30)},
			detail: noStopNote},
		{name: "blocked",
			recs:   recs(prompt),
			status: "blocked",
			want:   requestResult{State: stateBlocked},
			detail: []string{"answer it in agterm"}},
		{name: "running",
			recs:   recs(prompt),
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "lost: a turn started before the send finished after it",
			recs: recs(promptRec(at(-500), mainSID, "p0", "long job"), stopRec(at(20), mainSID, "p0", "job done")),
			want: requestResult{State: stateLost}, detail: []string{"folded into the turn"}},
		{name: "pending: empty journal",
			want:   requestResult{State: statePending},
			detail: []string{"hooks may not be installed", "Claude changed the text"}},
		{name: "pending: only a finished turn of another text",
			recs:   recs(promptRec(at(10), mainSID, "p1", "say BETA"), stopRec(at(20), mainSID, "p1", "BETA")),
			want:   requestResult{State: statePending},
			detail: []string{"hooks may not be installed"}},
	})
}

func TestRequestStateMatching(t *testing.T) {
	alpha := promptRec(at(10), mainSID, "p1", "say ALPHA")
	runStateCases(t, []stateCase{
		{name: "records before sent_at are ignored",
			recs: recs(promptRec(at(-50), mainSID, "p1", "say ALPHA"), stopRec(at(-40), mainSID, "p1", "OLD")),
			want: requestResult{State: statePending}, detail: []string{"hooks may not be installed"}},
		{name: "a matching prompt before sent_at does not make its stop the reply",
			recs: recs(promptRec(at(-50), mainSID, "p1", "say ALPHA"), stopRec(at(5), mainSID, "p1", "OLD")),
			want: requestResult{State: stateLost}, detail: []string{"folded"}},
		{name: "a record at exactly sent_at counts",
			recs: recs(promptRec(at(0), mainSID, "p1", "say ALPHA"), stopRec(at(0), mainSID, "p1", "ALPHA")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(0)}},
		{name: "the prompt matches after normalization",
			recs: recs(
				turnRecord{Event: "prompt", T: at(10), Session: mainSID, PromptID: "p1", Hash: promptHash(wrapPasted("f033", "say ALPHA  "))},
				stopRec(at(20), mainSID, "p1", "ALPHA")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "two matching turns: the last one wins, never final",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "FIRST"),
				promptRec(at(30), mainSID, "p2", "say ALPHA"), stopRec(at(40), mainSID, "p2", "SECOND"),
				promptRec(at(50), mainSID, "p3", "next")),
			want:   requestResult{State: stateDone, Reply: "SECOND", FinishedAt: at(40)},
			detail: sameNote},
		{name: "two matching turns: the last one interrupted, never final",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "FIRST"),
				promptRec(at(30), mainSID, "p2", "say ALPHA"), stopRec(at(40), mainSID, "p2", ""),
				promptRec(at(50), mainSID, "p3", "next")),
			want:   requestResult{State: stateInterrupted, FinishedAt: at(40)},
			detail: sameNote},
		{name: "two matching turns: the last one is still running",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "FIRST"),
				promptRec(at(30), mainSID, "p2", "say ALPHA")),
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "a matching turn before sent_at does not count as a second one",
			recs: recs(promptRec(at(-50), mainSID, "p0", "say ALPHA"), stopRec(at(-40), mainSID, "p0", "OLD"),
				alpha, stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "a stop a Stop hook may still continue is not final",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "draft")),
			want: requestResult{State: stateDone, Reply: "draft", FinishedAt: at(20)}},
		{name: "stop-hook continuation: the last stop gives the reply",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "draft"), contRec(at(30), mainSID, "p1", "final")),
			want: requestResult{State: stateDone, Reply: "final", FinishedAt: at(30)}},
		{name: "stop-hook continuation with another prompt id is still the turn, never final",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "draft"), contRec(at(30), mainSID, "p9", "final"),
				promptRec(at(40), mainSID, "p2", "next")),
			want:   requestResult{State: stateDone, Reply: "final", FinishedAt: at(30)},
			detail: queueNote},
		{name: "stop-hook continuation, then the next turn: final",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "draft"), contRec(at(30), mainSID, "p1", "final"),
				promptRec(at(40), mainSID, "p2", "next")),
			want: requestResult{State: stateDone, Reply: "final", FinishedAt: at(30), Final: true}},
		{name: "a stop after the turn's next prompt is not its reply",
			recs: recs(alpha, stopRec(at(20), mainSID, "p1", "ALPHA"),
				promptRec(at(30), mainSID, "p2", "next"), stopRec(at(40), mainSID, "p2", "NEXT")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "a nested session inside the matching turn neither splits it nor gives its reply",
			recs: recs(alpha,
				promptRec(at(12), nestSID, "n1", "summarize the file"), stopRec(at(15), nestSID, "n1", "NESTED"),
				stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "a finished nested session inside a running matching turn",
			recs: recs(alpha,
				promptRec(at(12), nestSID, "n1", "summarize the file"), stopRec(at(15), nestSID, "n1", "NESTED")),
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "a later prompt of another session does not interrupt the turn",
			recs:   recs(alpha, promptRec(at(20), nestSID, "n1", "other")),
			status: "active",
			want:   requestResult{State: stateRunning}},
		{name: "the matching turn takes its reply from its own session only",
			recs: recs(promptRec(at(5), mainSID, "p0", "run claude -p"),
				promptRec(at(10), nestSID, "n1", "say ALPHA"), stopRec(at(15), nestSID, "n1", "NESTED ALPHA"),
				stopRec(at(20), mainSID, "p0", "outer done")),
			want: requestResult{State: stateDone, Reply: "NESTED ALPHA", FinishedAt: at(15)}},
		{name: "an orphan stop is per session",
			recs: recs(promptRec(at(5), nestSID, "n1", "other"), stopRec(at(10), mainSID, "p0", "earlier turn")),
			want: requestResult{State: stateLost}, detail: []string{"folded"}},
		{name: "a stop whose prompt is in the window is no orphan",
			recs: recs(promptRec(at(5), nestSID, "n1", "other"), stopRec(at(10), nestSID, "n1", "x")),
			want: requestResult{State: statePending}, detail: []string{"hooks may not be installed"}},
		{name: "unknown events are ignored",
			recs: recs(turnRecord{Event: "note", T: at(5), Session: mainSID, PromptID: "p9"},
				alpha, turnRecord{Event: "note", T: at(15), Session: mainSID, PromptID: "p9"},
				stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "a stop record without a reply field counts as empty",
			recs: recs(alpha, turnRecord{Event: "stop", T: at(20), Session: mainSID, PromptID: "p1"}),
			want: requestResult{State: stateInterrupted, FinishedAt: at(20)}},
	})
}

// TestRequestStateQueuedIntoIt: a message queued while the request's turn runs carries that
// turn's prompt id, so it does not end the turn, but the session now shows a queue and the
// request's result is never final. Shapes measured with Claude Code 2.1.319.
func TestRequestStateQueuedIntoIt(t *testing.T) {
	alpha := promptRec(at(10), mainSID, "p1", "say ALPHA")
	beta := promptRec(at(20), mainSID, "p1", "say BETA")
	next := promptRec(at(50), mainSID, "p3", "next")
	runStateCases(t, []stateCase{
		{name: "the queued message's prompt record does not interrupt the turn",
			recs: recs(alpha, beta), status: "active",
			want: requestResult{State: stateRunning}},
		{name: "folded into a turn with a tool call: its one stop, never final",
			recs: recs(alpha, beta, stopRec(at(30), mainSID, "p1", "ECHO"), next), status: "completed",
			want:   requestResult{State: stateDone, Reply: "ECHO", FinishedAt: at(30)},
			detail: queueNote},
		{name: "a turn without a tool call stops, then the queued message runs as a turn of its own: never final",
			recs:   recs(alpha, beta, stopRec(at(30), mainSID, "p1", "ESSAY"), stopRec(at(40), mainSID, "p2", "GOLF"), next),
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(30)},
			detail: queueNote},
		{name: "interrupted, then the queued message runs as a turn of its own",
			recs:   recs(alpha, beta, stopRec(at(40), mainSID, "p2", "GOLF")),
			want:   requestResult{State: stateInterrupted, FinishedAt: at(40)},
			detail: queueNote},
	})
}

// TestRequestStateQueued: the request's message was queued while turn p1 of another message ran.
// Its reply is that turn's only if Claude folded it in; otherwise Claude runs it next as a turn
// of its own, which starts with a stop of a new prompt id. Neither is ever final.
func TestRequestStateQueued(t *testing.T) {
	host := promptRec(at(-500), mainSID, "p1", "write an essay")
	queued := promptRec(at(10), mainSID, "p1", "say ALPHA")
	essay := stopRec(at(20), mainSID, "p1", "ESSAY")
	own := stopRec(at(30), mainSID, "p2", "ALPHA") // the queued message's own turn
	next := promptRec(at(40), mainSID, "p3", "next")
	runStateCases(t, []stateCase{
		{name: "the turn it was queued into runs",
			recs: recs(host, queued), status: "active",
			want: requestResult{State: stateRunning}},
		{name: "the turn it was queued into waits for a person",
			recs: recs(host, queued), status: "blocked",
			want:   requestResult{State: stateBlocked},
			detail: []string{"answer it in agterm"}},
		{name: "that turn stopped: its result",
			recs: recs(host, queued, essay), status: "completed",
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(20)},
			detail: queuedNote},
		{name: "that turn stopped and the agent is at work again: its own turn runs",
			recs: recs(host, queued, essay), status: "active",
			want: requestResult{State: stateRunning}},
		{name: "that turn stopped and the agent waits for a person",
			recs: recs(host, queued, essay), status: "blocked",
			want:   requestResult{State: stateBlocked},
			detail: []string{"answer it in agterm"}},
		{name: "that turn stopped, target not in the tree: its result",
			recs: recs(host, queued, essay), status: "active", tree: paneNotInTree,
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(20)},
			detail: queuedNote},
		{name: "that turn stopped, tree unavailable: its result",
			recs: recs(host, queued, essay), status: "blocked", tree: treeUnavailable,
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(20)},
			detail: queuedNote},
		{name: "folded, then the next turn: still never final",
			recs:   recs(host, queued, essay, next),
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(20)},
			detail: queuedNote},
		{name: "its own turn stopped: its reply",
			recs:   recs(host, queued, essay, own),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(30)},
			detail: ownTurnNote},
		{name: "its own turn ended: still never final",
			recs:   recs(host, queued, essay, own, next),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(30)},
			detail: ownTurnNote},
		{name: "a Stop hook carried its own turn on",
			recs:   recs(host, queued, essay, stopRec(at(30), mainSID, "p2", "draft"), contRec(at(35), mainSID, "p2", "ALPHA"), next),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(35)},
			detail: ownTurnNote},
		{name: "a Stop hook carried the turn it was queued into on",
			recs: recs(host, queued, essay, contRec(at(25), mainSID, "p1", "ESSAY 2")), status: "completed",
			want:   requestResult{State: stateDone, Reply: "ESSAY 2", FinishedAt: at(25)},
			detail: queuedNote},
		{name: "that turn was interrupted, then a new turn",
			recs:   recs(host, queued, next),
			want:   requestResult{State: stateInterrupted, FinishedAt: at(40)},
			detail: queuedNote},
		{name: "that turn was interrupted, then the message ran as a turn of its own",
			recs:   recs(host, queued, own, next),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(30)},
			detail: ownTurnNote},
		{name: "the turn it was queued into started after the send",
			recs: recs(promptRec(at(5), mainSID, "p1", "write an essay"), queued, essay, next), status: "completed",
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(20)},
			detail: queuedNote},
		{name: "an earlier stop with its prompt id: queued into that turn's Stop hook continuation",
			recs: recs(promptRec(at(-500), mainSID, "p1", "write an essay"), stopRec(at(-400), mainSID, "p1", "draft"),
				queued, contRec(at(20), mainSID, "p1", "ESSAY"), next),
			want:   requestResult{State: stateDone, Reply: "ESSAY", FinishedAt: at(20)},
			detail: queuedNote},
		{name: "a prompt id of another session is no queue",
			recs: recs(promptRec(at(-500), nestSID, "p1", "write an essay"), queued, stopRec(at(20), mainSID, "p1", "ALPHA"), next),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
	})
}

// TestRequestStateQueueInSession: the message's prompt_id is new, so it looks like a turn of its
// own, but the session's journal shows a queue somewhere. A message sent while a queued message
// ran as a turn of its own (which has no prompt record) looks exactly like that, so the result is
// the turn's last stop and never final. The first three cases are the review findings' journals.
func TestRequestStateQueueInSession(t *testing.T) {
	// A runs; B is queued into it (prompt id p1) and runs as its own turn p2 once A stopped.
	host := recs(promptRec(at(-500), mainSID, "p1", "write an essay"), promptRec(at(-400), mainSID, "p1", "say BETA"),
		stopRec(at(-300), mainSID, "p1", "ESSAY"))
	alpha := promptRec(at(10), mainSID, "p2", "say ALPHA")
	with := func(rs ...turnRecord) []turnRecord { return append(append([]turnRecord{}, host...), rs...) }
	runStateCases(t, []stateCase{
		{name: "two messages queued into a queued message's own turn: the later one's result",
			req: func(r *request) { r.Hash = promptHash("say DELTA") },
			recs: with(promptRec(at(5), mainSID, "p2", "say GAMMA"), promptRec(at(10), mainSID, "p2", "say DELTA"),
				stopRec(at(20), mainSID, "p2", "BETA"), stopRec(at(30), mainSID, "p3", "GAMMA"), stopRec(at(40), mainSID, "p4", "DELTA")),
			want:   requestResult{State: stateDone, Reply: "GAMMA", FinishedAt: at(30)},
			detail: ownTurnNote},
		{name: "a chain of queued messages, the next one queued into the message's own turn",
			recs: with(promptRec(at(-200), mainSID, "p2", "say GAMMA"), stopRec(at(-100), mainSID, "p2", "BETA"),
				promptRec(at(10), mainSID, "p3", "say ALPHA"), stopRec(at(20), mainSID, "p3", "GAMMA"),
				promptRec(at(30), mainSID, "p4", "say DELTA")),
			want:   requestResult{State: stateDone, Reply: "GAMMA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a chain of queued messages, every turn in",
			recs: with(promptRec(at(-200), mainSID, "p2", "say GAMMA"), stopRec(at(-100), mainSID, "p2", "BETA"),
				promptRec(at(10), mainSID, "p3", "say ALPHA"), stopRec(at(20), mainSID, "p3", "GAMMA"),
				promptRec(at(30), mainSID, "p4", "say DELTA"), stopRec(at(40), mainSID, "p4", "ALPHA"), stopRec(at(50), mainSID, "p5", "DELTA")),
			want:   requestResult{State: stateDone, Reply: "GAMMA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a turn without a prompt record after the message's turn: its own reply, never final",
			recs: recs(alpha, stopRec(at(20), mainSID, "p2", "ALPHA"), stopRec(at(30), mainSID, "p3", "WOKEN"),
				promptRec(at(40), mainSID, "p4", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "queued into a queued message's own turn: that turn's result",
			recs:   with(alpha, stopRec(at(20), mainSID, "p2", "BETA")),
			want:   requestResult{State: stateDone, Reply: "BETA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a message after a folded one looks the same: its reply, never final",
			recs:   with(alpha, stopRec(at(20), mainSID, "p2", "ALPHA"), promptRec(at(30), mainSID, "p3", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a queue long before the send still counts",
			recs: with(stopRec(at(-200), mainSID, "p2", "BETA"), promptRec(at(-150), mainSID, "p3", "x"), stopRec(at(-100), mainSID, "p3", "X"),
				promptRec(at(10), mainSID, "p4", "say ALPHA"), stopRec(at(20), mainSID, "p4", "ALPHA"), promptRec(at(30), mainSID, "p5", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a queue after the turn counts too",
			recs: recs(promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"),
				promptRec(at(30), mainSID, "p2", "x"), promptRec(at(35), mainSID, "p2", "y"), stopRec(at(40), mainSID, "p2", "X")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a stop without its prompt record at the journal's head counts",
			recs: recs(stopRec(at(-100), mainSID, "p0", "TRIMMED"),
				promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: queueNote},
		{name: "a message interrupted in a session with a queue: the queue's detail",
			recs: with(stopRec(at(-200), mainSID, "p2", "BETA"), promptRec(at(10), mainSID, "p3", "say ALPHA"),
				promptRec(at(30), mainSID, "p4", "next")),
			want:   requestResult{State: stateInterrupted, FinishedAt: at(30)},
			detail: queueNote},
		{name: "a queue in another session does not count",
			recs: recs(promptRec(at(-500), nestSID, "n1", "a"), promptRec(at(-400), nestSID, "n1", "b"), stopRec(at(-300), nestSID, "n2", "B"),
				promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
		{name: "a session of finished turns, each with its prompt record: final",
			recs: recs(promptRec(at(-500), mainSID, "p0", "x"), stopRec(at(-400), mainSID, "p0", "X"),
				contRec(at(-300), mainSID, "p0", "X2"),
				promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20), Final: true}},
	})
}

// TestRequestStateNoPromptID: records without a prompt_id (a Claude that sends none) end a turn
// at each prompt record of the session, and nothing they give is final.
func TestRequestStateNoPromptID(t *testing.T) {
	runStateCases(t, []stateCase{
		{name: "done, not final even after the next prompt",
			recs: recs(promptRec(at(10), mainSID, "", "say ALPHA"), stopRec(at(20), mainSID, "", "ALPHA"),
				promptRec(at(30), mainSID, "", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: noIDNote},
		{name: "a later prompt without a stop: interrupted, not final",
			recs:   recs(promptRec(at(10), mainSID, "", "say ALPHA"), promptRec(at(20), mainSID, "", "other")),
			want:   requestResult{State: stateInterrupted, FinishedAt: at(20)},
			detail: noIDNote},
		{name: "running",
			recs: recs(promptRec(at(10), mainSID, "", "say ALPHA")), status: "active",
			want: requestResult{State: stateRunning}},
		{name: "a later record without a prompt id makes it not final",
			recs: recs(promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"),
				promptRec(at(30), mainSID, "", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: noIDNote},
		{name: "an earlier record without a prompt id makes it not final",
			recs: recs(stopRec(at(-100), mainSID, "", "OLD"),
				promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"), promptRec(at(30), mainSID, "p2", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: noIDNote},
		{name: "a matching prompt without a prompt id is never final",
			recs: recs(promptRec(at(10), mainSID, "", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"),
				promptRec(at(30), mainSID, "p2", "next")),
			want:   requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)},
			detail: noIDNote},
	})
}

// TestRequestStateTree: a target missing from the tree, or no tree at all, skips the blocked
// check, never makes a request lost, and detail says which.
func TestRequestStateTree(t *testing.T) {
	running := recs(promptRec(at(10), mainSID, "p1", "say ALPHA"))
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
			recs: recs(stopRec(at(10), mainSID, "p0", "earlier turn")), tree: paneNotInTree,
			want: requestResult{State: stateLost}, detail: []string{"folded", "not in agterm's tree"}},
		{name: "not in tree: done carries no note",
			recs: recs(promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA")),
			tree: paneNotInTree,
			want: requestResult{State: stateDone, Reply: "ALPHA", FinishedAt: at(20)}},
		{name: "tree unavailable: interrupted carries no note",
			recs: recs(promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "")),
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
	with := func(state, reply string, finishedAt int64, final bool) answerOut {
		o := base
		o.State, o.Reply, o.FinishedAt, o.Final = state, reply, finishedAt, final
		return o
	}
	storedIs := func(state, reply string, finishedAt int64, final bool) {
		t.Helper()
		got, err := loadRequest(dir, sent.RequestID)
		if err != nil {
			t.Fatal(err)
		}
		want := r
		want.State, want.Reply, want.FinishedAt, want.Final = state, reply, finishedAt, final
		if got != want {
			t.Errorf("stored request\n got %+v\nwant %+v", got, want)
		}
	}

	answer(with(statePending, "", 0, false), "hooks may not be installed")
	writeTurns(t, journal, promptRec(r.SentAt+10, mainSID, "p1", "say ALPHA"))
	answer(with(stateRunning, "", 0, false), "")
	storedIs("", "", 0, false)

	// The turn stopped, but nothing shows it is over: a Stop hook could make Claude carry on.
	// The result is not final, so it is not stored.
	writeTurns(t, journal, stopRec(r.SentAt+20, mainSID, "p1", "ALPHA"))
	answer(with(stateDone, "ALPHA", r.SentAt+20, false), "")
	storedIs("", "", 0, false)

	// The session's next turn started: final, and stored.
	writeTurns(t, journal, promptRec(r.SentAt+30, mainSID, "p2", "next"))
	done := with(stateDone, "ALPHA", r.SentAt+20, true)
	answer(done, "")
	storedIs(stateDone, "ALPHA", r.SentAt+20, true)

	// A stored final result is no longer read from the journal: neither a newer matching turn nor
	// a journal that cannot be read changes it.
	writeTurns(t, journal, stopRec(r.SentAt+40, mainSID, "p2", "NEXT"),
		promptRec(r.SentAt+50, mainSID, "p3", "say ALPHA"), stopRec(r.SentAt+60, mainSID, "p3", "NEWER"))
	answer(done, "")
	if err := os.Remove(journal); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(journal, 0o700); err != nil {
		t.Fatal(err)
	}
	answer(done, "")
}

// TestAnswerStopHookContinuation: a Stop hook that makes Claude carry on gives the turn a second
// stop. The first one is returned but not stored, so the final text replaces it.
func TestAnswerStopHookContinuation(t *testing.T) {
	dir := t.TempDir()
	s := &server{ctl: fakeAgtermctl(t), dir: dir}
	r := testRequest("0123456789ab")
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	journal := journalPath(dir, r.PaneID)
	answer := func(reply string, final bool) {
		t.Helper()
		_, out, err := s.answer(context.Background(), nil, answerIn{RequestID: r.ID})
		if err != nil || out.State != stateDone || out.Reply != reply || out.Final != final {
			t.Fatalf("answer = %+v, %v; want done %q final %v", out, err, reply, final)
		}
		want := r
		if final {
			want.State, want.Reply, want.FinishedAt, want.Final = stateDone, reply, out.FinishedAt, true
		}
		if stored, err := loadRequest(dir, r.ID); err != nil || stored != want {
			t.Errorf("stored request %+v, %v; want %+v", stored, err, want)
		}
	}
	writeTurns(t, journal, promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "draft"))
	answer("draft", false)
	writeTurns(t, journal, contRec(at(30), mainSID, "p1", "final"))
	answer("final", false)
	writeTurns(t, journal, promptRec(at(40), mainSID, "p2", "next"), stopRec(at(50), mainSID, "p2", "NEXT"))
	answer("final", true)
}

// answerStep is one answer call against the journal as it stands: the request's state and reply,
// never final, never stored.
type answerStep struct {
	id, state, reply string
}

// replayNeverFinal appends each batch of records to the journal of testRequest's pane and checks
// every step after it: the result is the one expected, not final, and the request file still
// holds no result.
func replayNeverFinal(t *testing.T, reqs []request, batches [][]turnRecord, steps [][]answerStep) {
	t.Helper()
	dir := t.TempDir()
	ctl, _ := fakeAgtermctlTree(t, answerTree("completed", "PANE-A1-RIGHT"))
	s := &server{ctl: ctl, dir: dir}
	for _, r := range reqs {
		if err := saveRequest(dir, r); err != nil {
			t.Fatal(err)
		}
	}
	journal := journalPath(dir, reqs[0].PaneID)
	for i, batch := range batches {
		writeTurns(t, journal, batch...)
		for _, st := range steps[i] {
			_, out, err := s.answer(context.Background(), nil, answerIn{RequestID: st.id})
			if err != nil || out.State != st.state || out.Reply != st.reply || out.Final {
				t.Errorf("batch %d: answer %s = %+v, %v; want %s %q, not final", i, st.id, out, err, st.state, st.reply)
			}
			if stored, err := loadRequest(dir, st.id); err != nil || stored.State != "" || stored.Final {
				t.Errorf("batch %d: request %s stored %+v, %v; want no result", i, st.id, stored, err)
			}
		}
	}
}

// askedAt is testRequest for text, sent at sentAt.
func askedAt(id, text string, sentAt int64) request {
	r := testRequest(id)
	r.Hash, r.SentAt = promptHash(text), sentAt
	return r
}

// TestAnswerQueued replays the measured journal of a message queued while a turn without tool
// calls ran: prompt A, prompt B (A's prompt id), stop A's reply, stop B's reply (a new prompt id).
// Neither request is ever final, and neither stores a result.
func TestAnswerQueued(t *testing.T) {
	essay, golf := askedAt("aaaaaaaaaaaa", "write an essay", at(-100)), askedAt("bbbbbbbbbbbb", "say GOLF", at(0))
	replayNeverFinal(t, []request{essay, golf},
		[][]turnRecord{
			{promptRec(at(-90), mainSID, "p1", "write an essay"), promptRec(at(10), mainSID, "p1", "say GOLF")},
			{stopRec(at(20), mainSID, "p1", "ESSAY")},
			{stopRec(at(30), mainSID, "p2", "GOLF")},
			{promptRec(at(40), mainSID, "p3", "next")},
		},
		[][]answerStep{
			{{essay.ID, stateRunning, ""}, {golf.ID, stateRunning, ""}},
			{{essay.ID, stateDone, "ESSAY"}, {golf.ID, stateDone, "ESSAY"}},
			{{essay.ID, stateDone, "ESSAY"}, {golf.ID, stateDone, "GOLF"}},
			{{essay.ID, stateDone, "ESSAY"}, {golf.ID, stateDone, "GOLF"}},
		})
}

// TestAnswerQueueChain replays the review findings' journals through the handler, a record at a
// time. Each message from C on arrives while the one before it runs as a turn of its own, which
// has no prompt record, so its prompt_id looks new; no request is ever final or stored.
func TestAnswerQueueChain(t *testing.T) {
	t.Run("each message queued into the one before's own turn", func(t *testing.T) {
		reqs := []request{askedAt("aaaaaaaaaaaa", "A", at(-100)), askedAt("bbbbbbbbbbbb", "B", at(-95)),
			askedAt("cccccccccccc", "C", at(-75)), askedAt("dddddddddddd", "D", at(-55)), askedAt("eeeeeeeeeeee", "E", at(-35))}
		a, b, c, d, e := reqs[0].ID, reqs[1].ID, reqs[2].ID, reqs[3].ID, reqs[4].ID
		replayNeverFinal(t, reqs,
			[][]turnRecord{
				{promptRec(at(-90), mainSID, "p1", "A"), promptRec(at(-85), mainSID, "p1", "B"), stopRec(at(-80), mainSID, "p1", "RA"),
					promptRec(at(-70), mainSID, "p2", "C"), stopRec(at(-60), mainSID, "p2", "RB"),
					promptRec(at(-50), mainSID, "p3", "D"), stopRec(at(-40), mainSID, "p3", "RC")},
				{promptRec(at(-30), mainSID, "p4", "E")},
				{stopRec(at(-20), mainSID, "p4", "RD")},
				{stopRec(at(-10), mainSID, "p5", "RE")},
			},
			[][]answerStep{
				{{c, stateDone, "RB"}, {d, stateDone, "RC"}},
				{{a, stateDone, "RA"}, {c, stateDone, "RB"}, {d, stateDone, "RC"}},
				{{d, stateDone, "RC"}, {e, stateDone, "RD"}},
				{{b, stateDone, "RA"}, {d, stateDone, "RC"}, {e, stateDone, "RD"}},
			})
	})
	t.Run("two messages queued into a queued message's own turn", func(t *testing.T) {
		reqs := []request{askedAt("cccccccccccc", "C", at(-75)), askedAt("dddddddddddd", "D", at(-70))}
		c, d := reqs[0].ID, reqs[1].ID
		replayNeverFinal(t, reqs,
			[][]turnRecord{
				{promptRec(at(-90), mainSID, "p1", "A"), promptRec(at(-85), mainSID, "p1", "B"), stopRec(at(-80), mainSID, "p1", "RA"),
					promptRec(at(-74), mainSID, "p2", "C"), promptRec(at(-69), mainSID, "p2", "D"), stopRec(at(-60), mainSID, "p2", "RB")},
				{stopRec(at(-50), mainSID, "p3", "RC")},
				{stopRec(at(-40), mainSID, "p4", "RD")},
			},
			[][]answerStep{
				{{c, stateDone, "RB"}, {d, stateDone, "RB"}},
				{{c, stateDone, "RB"}, {d, stateDone, "RC"}},
				{{c, stateDone, "RB"}, {d, stateDone, "RC"}},
			})
	})
	t.Run("a turn without a prompt record after a fresh message", func(t *testing.T) {
		reqs := []request{askedAt("cccccccccccc", "C", at(0))}
		c := reqs[0].ID
		replayNeverFinal(t, reqs,
			[][]turnRecord{
				{promptRec(at(10), mainSID, "p2", "C"), stopRec(at(20), mainSID, "p2", "RC")},
				{stopRec(at(30), mainSID, "p3", "WOKEN")},
				{promptRec(at(40), mainSID, "p4", "user")},
			},
			[][]answerStep{
				{{c, stateDone, "RC"}},
				{{c, stateDone, "RC"}},
				{{c, stateDone, "RC"}},
			})
	})
	t.Run("the same text sent twice", func(t *testing.T) {
		reqs := []request{askedAt("aaaaaaaaaaaa", "continue", at(0))}
		a := reqs[0].ID
		replayNeverFinal(t, reqs,
			[][]turnRecord{
				{promptRec(at(10), mainSID, "p1", "continue"), stopRec(at(20), mainSID, "p1", "R1")},
				{promptRec(at(30), mainSID, "p2", "continue"), stopRec(at(40), mainSID, "p2", "R2"), promptRec(at(50), mainSID, "p3", "next")},
			},
			[][]answerStep{
				{{a, stateDone, "R1"}},
				{{a, stateDone, "R2"}},
			})
	})
}

// TestAnswerStoresInterrupted: an empty Stop ends the turn without a reply and is stored once the
// next turn starts; a turn with no Stop at all (Esc) is interrupted but never final.
func TestAnswerStoresInterrupted(t *testing.T) {
	dir := t.TempDir()
	s := &server{ctl: fakeAgtermctl(t), dir: dir}
	r := testRequest("0123456789ab")
	if err := saveRequest(dir, r); err != nil {
		t.Fatal(err)
	}
	writeTurns(t, journalPath(dir, r.PaneID), promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", ""),
		promptRec(at(30), mainSID, "p2", "next"))
	_, out, err := s.answer(context.Background(), nil, answerIn{RequestID: r.ID})
	if err != nil || out.State != stateInterrupted || out.FinishedAt != at(20) || !out.Final {
		t.Fatalf("answer = %+v, %v; want final interrupted at %d", out, err, at(20))
	}
	if got, _ := loadRequest(dir, r.ID); got.State != stateInterrupted || got.FinishedAt != at(20) || !got.Final {
		t.Errorf("stored request %+v, want final interrupted", got)
	}

	esc := askedAt("abcdefabcdef", "say BETA", at(0))
	replayNeverFinal(t, []request{esc},
		[][]turnRecord{{promptRec(at(10), mainSID, "p1", "say BETA"), promptRec(at(30), mainSID, "p2", "stop"),
			stopRec(at(40), mainSID, "p2", "OK")}},
		[][]answerStep{{{esc.ID, stateInterrupted, ""}}})
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
	if err != nil || out.State != stateUnsupported || !out.Final || !strings.Contains(out.Detail, "Claude only") {
		t.Errorf("answer = %+v, %v; want final unsupported", out, err)
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
	running := recs(promptRec(at(10), mainSID, "p1", "say ALPHA"))
	finished := recs(promptRec(at(10), mainSID, "p1", "say ALPHA"), stopRec(at(20), mainSID, "p1", "ALPHA"))
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
