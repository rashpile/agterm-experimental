package main

import "fmt"

// The states answer reports. done and interrupted end a turn; answer stores them for good once
// they are final.
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
	Final      bool // the result can no longer change
}

// Details of a done or interrupted result that can never become final.
const (
	queuedDetail = "The message was queued while another turn ran, and this is that turn's result; " +
		"it answers the message only if Claude folded the message in, and a later call returns the message's own turn if Claude runs one. " +
		"A queued message's result is never final."
	ownTurnDetail = "The message was queued while another turn ran, and this is the turn Claude ran after that one, most likely the message's own. " +
		"A queued message's result is never final."
	sessionQueueDetail = "This Claude session's journal shows a message queued while a turn ran, or a turn without a prompt record, " +
		"so go-ask can't be sure this turn answers the message, and the result is not final."
	noPromptIDDetail = "The journal has records without Claude's prompt_id for this session, " +
		"so go-ask can't tell a queued message from a new turn, and the result is not final."
	noStopDetail   = "Claude ran no Stop for this turn, as when the user interrupts it, so the result is not final."
	sameTextDetail = "The same text was sent more than once since this ask, and this is the last one's turn, so the result is not final."
)

// requestState computes the state of r from the journal records of its pane and the target pane
// as the tree shows it (target counts only when tree is paneInTree). It reads nothing and writes
// nothing.
//
// Only records with t >= sent_at can answer r. The last prompt record whose hash equals the
// request's answers it, so a later identical message takes the request over until a result is
// final; a final result is stored and returned as it is.
func requestState(r request, recs []turnRecord, target Pane, tree treeLookup) requestResult {
	if r.Final {
		return requestResult{State: r.State, Reply: r.Reply, FinishedAt: r.FinishedAt, Final: true}
	}
	if r.Agent != "claude" {
		detail := "answer supports Claude only for now, and no agent was detected in the target."
		if r.Agent != "" {
			detail = fmt.Sprintf("answer supports Claude only for now, and the target runs %s.", r.Agent)
		}
		return requestResult{State: stateUnsupported, Detail: detail, Final: true}
	}

	var earlier, window []turnRecord
	for _, rec := range recs {
		if rec.T >= r.SentAt {
			window = append(window, rec)
		} else {
			earlier = append(earlier, rec)
		}
	}

	var res requestResult
	if m := lastMatchingPrompt(window, r.Hash); m >= 0 {
		res = matchedTurnState(earlier, window, m, target, tree)
		if matchingPrompts(window, r.Hash) > 1 && (res.State == stateDone || res.State == stateInterrupted) {
			res.Final = false
			res.Detail = joinDetail(res.Detail, sameTextDetail)
		}
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

// lastMatchingPrompt is the index of the last prompt record whose hash is hash; -1 when there is
// none.
func lastMatchingPrompt(window []turnRecord, hash string) int {
	for i := len(window) - 1; i >= 0; i-- {
		if window[i].Event == "prompt" && window[i].Hash == hash {
			return i
		}
	}
	return -1
}

// matchingPrompts counts the prompt records whose hash is hash.
func matchingPrompts(window []turnRecord, hash string) int {
	n := 0
	for _, rec := range window {
		if rec.Event == "prompt" && rec.Hash == hash {
			n++
		}
	}
	return n
}

// matchedTurnState is the state of the turn that answers the prompt record window[m]; earlier
// holds the journal's records before the window.
//
// A turn is the records of one Claude session with one prompt_id, and its reply is its last stop.
// A prompt record with the turn's prompt_id is a message queued into the turn, so it does not
// end it; a record with another prompt_id belongs to a later turn, which shows the turn is over.
// A stop with stop_hook_active continues the turn whatever its prompt_id. Records without a
// prompt_id end a turn at each prompt record of the session.
//
// A message whose prompt_id an earlier record of its session has was queued while that turn ran.
// Claude folds it into that turn when the turn makes a tool call after it, and otherwise runs it
// next as a turn of its own that starts with a stop, not a prompt record; the first such stop
// gives the message's reply. Which of the two happened is never certain, so the result is not
// final.
//
// The result is final only when the message opened a turn of its own, that turn's stop is in the
// journal, the session's next turn has started, and the session's records show no queue at all
// (sessionQueue).
func matchedTurnState(earlier, window []turnRecord, m int, target Pane, tree treeLookup) requestResult {
	p := window[m]
	inSession := func(rec turnRecord) bool {
		return rec.Session == p.Session && (rec.Event == "prompt" || rec.Event == "stop")
	}
	var sess []turnRecord // the session's prompt and stop records in the whole journal
	pos := 0              // p's index in sess
	for _, rec := range earlier {
		if inSession(rec) {
			sess = append(sess, rec)
		}
	}
	for i, rec := range window {
		if i == m {
			pos = len(sess)
		}
		if inSession(rec) {
			sess = append(sess, rec)
		}
	}
	queued := false // an earlier record of the session has p's prompt_id
	for _, rec := range sess[:pos] {
		queued = queued || (p.PromptID != "" && rec.PromptID == p.PromptID)
	}

	turn, ownTurn := p.PromptID, !queued // ownTurn: turn is the message's own, not the one it was queued into
	var lastStop, next *turnRecord
	for i := m + 1; i < len(window) && next == nil; i++ {
		rec := &window[i]
		if !inSession(*rec) {
			continue
		}
		switch {
		case rec.Event == "stop" && rec.StopHookActive && lastStop != nil:
			lastStop = rec
		case turn == "" || rec.PromptID == "":
			if rec.Event == "prompt" {
				next = rec
			} else {
				lastStop = rec
			}
		case rec.PromptID == turn:
			if rec.Event == "stop" {
				lastStop = rec
			}
		case !ownTurn && rec.Event == "stop":
			// Claude ran the queued message as a turn of its own.
			turn, ownTurn, lastStop = rec.PromptID, true, rec
		default:
			next = rec
		}
	}

	var res requestResult
	// The turn the message was queued into has stopped and the agent is at work again: the message
	// runs as a turn of its own.
	ownTurnRunning := lastStop != nil && !ownTurn && next == nil &&
		tree == paneInTree && (target.Status == "active" || target.Status == "blocked")
	switch {
	case lastStop == nil && next != nil:
		// Stop does not run when the user interrupts a turn: the next turn is the only sign that
		// the turn ended.
		res.State, res.FinishedAt = stateInterrupted, next.T
	case lastStop == nil || ownTurnRunning:
		res.State = stateRunning
		if tree == paneInTree && target.Status == "blocked" {
			res.State = stateBlocked
			res.Detail = "The agent is waiting for a person to answer a permission prompt or a question; answer it in agterm."
		}
		return res
	case lastStop.Reply != nil && *lastStop.Reply != "":
		res.State, res.Reply, res.FinishedAt = stateDone, *lastStop.Reply, lastStop.T
	default:
		res.State, res.FinishedAt = stateInterrupted, lastStop.T
	}
	queue := sessionQueue(sess)
	switch {
	case queued && !ownTurn:
		res.Detail = queuedDetail
	case queued:
		res.Detail = ownTurnDetail
	case queue != "":
		res.Detail = queue
	case lastStop == nil:
		res.Detail = noStopDetail
	}
	res.Final = !queued && lastStop != nil && next != nil && queue == ""
	return res
}

// sessionQueue says why one Claude session's prompt and stop records, in journal order, can't
// show which turn answers a message, or "" when they can. They can't when a record has no
// prompt_id, when a prompt record has a prompt_id an earlier record has (a message queued while a
// turn ran), or when a stop has a prompt_id no prompt record before it has (a turn without a prompt
// record, such as a queued message's own turn).
func sessionQueue(sess []turnRecord) string {
	seen, prompted := map[string]bool{}, map[string]bool{}
	queue := false
	for _, rec := range sess {
		switch {
		case rec.PromptID == "":
			return noPromptIDDetail
		case rec.Event == "prompt" && seen[rec.PromptID], rec.Event == "stop" && !prompted[rec.PromptID]:
			queue = true
		}
		seen[rec.PromptID] = true
		if rec.Event == "prompt" {
			prompted[rec.PromptID] = true
		}
	}
	if queue {
		return sessionQueueDetail
	}
	return ""
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
