package main

import "fmt"

// The states answer reports. done and interrupted are final: answer stores them in the request,
// and later calls return the stored result.
const (
	statePending     = "pending"
	stateRunning     = "running"
	stateBlocked     = "blocked"
	stateDone        = "done"
	stateInterrupted = "interrupted"
	stateLost        = "lost"
	stateUnsupported = "unsupported"
)

// treeLookup is what agterm's tree says about a request's target pane.
type treeLookup int

const (
	paneInTree      treeLookup = iota // the pane is in the tree; its status counts
	paneNotInTree                     // the tree lists only the frontmost window, or the session closed
	treeUnavailable                   // agtermctl tree failed
)

// requestResult is the state of a request at the time answer is called.
type requestResult struct {
	State      string
	Reply      string
	FinishedAt int64 // Unix milliseconds; 0 while the turn has not ended
	Detail     string
}

// requestState computes the state of r from the journal records of its pane and the target pane
// as the tree shows it (target counts only when tree is paneInTree). It reads nothing and writes
// nothing.
//
// Only records with t >= sent_at count. A prompt record opens a turn of its session; the turn
// holds that session's records up to its next prompt record, and its reply is its last stop
// record. Records of other sessions (a nested claude -p in the same pane) never split a turn or
// give its reply. The last turn whose prompt hash equals the request's is the one answered, so a
// later identical message rebinds the request until a final state is stored.
func requestState(r request, recs []turnRecord, target Pane, tree treeLookup) requestResult {
	if r.State != "" {
		return requestResult{State: r.State, Reply: r.Reply, FinishedAt: r.FinishedAt}
	}
	if r.Agent != "claude" {
		detail := "answer supports Claude only for now, and no agent was detected in the target."
		if r.Agent != "" {
			detail = fmt.Sprintf("answer supports Claude only for now, and the target runs %s.", r.Agent)
		}
		return requestResult{State: stateUnsupported, Detail: detail}
	}

	var window []turnRecord
	for _, rec := range recs {
		if rec.T >= r.SentAt {
			window = append(window, rec)
		}
	}

	var res requestResult
	if m := lastMatchingPrompt(window, r.Hash); m >= 0 {
		res = matchedTurnState(window, m, target, tree)
	} else if hasOrphanStop(window) {
		res = requestResult{State: stateLost,
			Detail: "The message was most likely folded into the turn that was running when it was sent, so it has no turn or reply of its own."}
	} else {
		res = requestResult{State: statePending,
			Detail: "No turn has started for this message yet; if this persists, the go-ask hooks may not be installed for this agent, or Claude changed the text."}
	}
	if res.State != stateDone && res.State != stateInterrupted {
		res.Detail = joinDetail(res.Detail, treeNote(tree))
	}
	return res
}

// lastMatchingPrompt is the index of the last prompt record whose hash is hash, or -1.
func lastMatchingPrompt(window []turnRecord, hash string) int {
	for i := len(window) - 1; i >= 0; i-- {
		if window[i].Event == "prompt" && window[i].Hash == hash {
			return i
		}
	}
	return -1
}

// matchedTurnState is the state of the turn opened by the prompt record window[m].
func matchedTurnState(window []turnRecord, m int, target Pane, tree treeLookup) requestResult {
	session := window[m].Session
	var lastStop *turnRecord
	for i := m + 1; i < len(window); i++ {
		rec := &window[i]
		if rec.Session != session {
			continue
		}
		if rec.Event == "prompt" {
			if lastStop == nil {
				// Stop does not run when the user interrupts a turn: the session's next prompt
				// is the only sign that the turn ended.
				return requestResult{State: stateInterrupted, FinishedAt: rec.T}
			}
			break
		}
		if rec.Event == "stop" {
			lastStop = rec
		}
	}
	switch {
	case lastStop != nil && lastStop.Reply != nil && *lastStop.Reply != "":
		return requestResult{State: stateDone, Reply: *lastStop.Reply, FinishedAt: lastStop.T}
	case lastStop != nil:
		return requestResult{State: stateInterrupted, FinishedAt: lastStop.T}
	case tree == paneInTree && target.Status == "blocked":
		return requestResult{State: stateBlocked,
			Detail: "The agent is waiting for a person to answer a permission prompt or a question; answer it in agterm."}
	default:
		return requestResult{State: stateRunning}
	}
}

// hasOrphanStop says whether some stop record has no prompt record of its session before it:
// a turn that started before the send finished after it.
func hasOrphanStop(window []turnRecord) bool {
	prompted := map[string]bool{}
	for _, rec := range window {
		switch rec.Event {
		case "prompt":
			prompted[rec.Session] = true
		case "stop":
			if !prompted[rec.Session] {
				return true
			}
		}
	}
	return false
}

// treeNote says why the target's status was not checked, or "" when it was.
func treeNote(tree treeLookup) string {
	switch tree {
	case paneNotInTree:
		return "The target pane is not in agterm's tree, which lists only the frontmost window, so its status was not checked."
	case treeUnavailable:
		return "agterm's tree is unavailable, so the target's status was not checked."
	}
	return ""
}

func joinDetail(a, b string) string {
	switch {
	case a == "":
		return b
	case b == "":
		return a
	}
	return a + " " + b
}
