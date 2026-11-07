// go-ask is an MCP server (stdio) that types a message into another agterm session and, from a
// Claude agent, gets the reply later.
//
// Tools:
//
//	sessions(agents?)      list panes that can be addressed
//	ask(text, session_id?) paste text into a pane's input and press Enter; returns a request_id at once
//	answer(request_id?)    the reply to an ask, or the state saying why there is none yet; never waits
//	recap(session_id?, turns?, model?)
//	                       a recap of a Claude pane's last turns, written by a local Ollama model
//
// `go-ask hook` is Claude's UserPromptSubmit and Stop hook: it records each turn of the pane the
// agent runs in to the journal answer reads.
//
// Without flags it serves one client over stdio. With -http it serves many over Streamable HTTP
// at /mcp; each client names its own pane in the X-Agterm-Session-Id and X-Agterm-Pane headers.
// With -token-file every HTTP request must carry "Authorization: Bearer <token>"; an address
// other than loopback refuses to start without one.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const version = "0.3.0"

type server struct {
	ctl  agtermctl
	self selfAddr
	dir  string    // state dir: turn journals and requests
	deps recapDeps // what recap needs: Ollama, the transcript locator, the default model

	mu            sync.Mutex
	lastRequestID string // the request_id this MCP session's last ask returned
}

func (s *server) setLastRequest(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastRequestID = id
}

func (s *server) lastRequest() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.lastRequestID
}

type sessionsIn struct {
	Agents bool `json:"agents,omitempty" jsonschema:"only panes running an agent CLI (claude, codex, pi, ...)"`
}

type sessionsOut struct {
	Sessions []Pane `json:"sessions"`
}

type askIn struct {
	Text      string `json:"text" jsonschema:"message to type into the session and submit"`
	SessionID string `json:"session_id,omitempty" jsonschema:"focused for the pane the user has focus on in agterm; or a session id, unique id prefix or exact session name, with :right appended for the split pane. Omitted: the focused pane"`
}

type askOut struct {
	Sent      bool   `json:"sent"`
	Target    string `json:"target"`
	Name      string `json:"name"`
	Agent     string `json:"agent,omitempty"`
	RequestID string `json:"request_id" jsonschema:"pass to answer to get the reply to this message"`
}

type answerIn struct {
	RequestID string `json:"request_id,omitempty" jsonschema:"the request_id ask returned. Omitted: the last ask of this MCP session"`
}

type answerOut struct {
	RequestID  string `json:"request_id"`
	State      string `json:"state" jsonschema:"done, interrupted, running, blocked, pending, lost or unsupported"`
	Reply      string `json:"reply,omitempty" jsonschema:"the agent's final text for the turn, when state is done"`
	Final      bool   `json:"final" jsonschema:"true when the result can no longer change: the message opened a turn of its own, that turn's Stop is in the journal, the agent's next turn has started, and no message was queued in that Claude session. Otherwise a later call can return another result"`
	Target     string `json:"target"`
	Name       string `json:"name"`
	SentAt     int64  `json:"sent_at" jsonschema:"when ask sent the message, Unix milliseconds"`
	FinishedAt int64  `json:"finished_at,omitempty" jsonschema:"when the turn ended, Unix milliseconds"`
	Detail     string `json:"detail,omitempty" jsonschema:"why there is no reply yet, or what could not be checked"`
}

func (s *server) sessions(ctx context.Context, _ *mcp.CallToolRequest, in sessionsIn) (*mcp.CallToolResult, sessionsOut, error) {
	panes, err := s.ctl.panes(ctx, s.self)
	if err != nil {
		return nil, sessionsOut{}, err
	}
	out := sessionsOut{Sessions: []Pane{}}
	for _, p := range panes {
		if in.Agents && p.Agent == "" {
			continue
		}
		out.Sessions = append(out.Sessions, p)
	}
	return nil, out, nil
}

func (s *server) ask(ctx context.Context, _ *mcp.CallToolRequest, in askIn) (*mcp.CallToolResult, askOut, error) {
	if in.Text == "" {
		return nil, askOut{}, errors.New("text is empty")
	}
	target := in.SessionID
	if target == "" {
		target = "focused"
	}
	panes, err := s.ctl.panes(ctx, s.self)
	if err != nil {
		return nil, askOut{}, err
	}
	p, err := resolveTarget(panes, target)
	if err != nil {
		return nil, askOut{}, err
	}
	if err := checkSendable(p); err != nil {
		return nil, askOut{}, err
	}
	id, err := newRequestID()
	if err != nil {
		return nil, askOut{}, fmt.Errorf("new request_id: %w", err)
	}
	// The request is saved before the send: a reply must never arrive for a request that answer
	// cannot find. The hash is of the text pastePayload types, which has ESC removed.
	req := request{
		ID:     id,
		Target: p.Target,
		PaneID: p.paneID,
		Name:   p.Name,
		Agent:  p.Agent,
		Hash:   promptHash(strings.ReplaceAll(in.Text, "\x1b", "")),
		SentAt: time.Now().UnixMilli(),
	}
	if err := saveRequest(s.dir, req); err != nil {
		return nil, askOut{}, fmt.Errorf("save request: %w. Nothing was sent", err)
	}
	if err := s.ctl.send(ctx, p, in.Text); err != nil {
		return nil, askOut{}, fmt.Errorf("send to %s: %w", p.Target, err)
	}
	s.setLastRequest(id)
	return nil, askOut{Sent: true, Target: p.Target, Name: p.Name, Agent: p.Agent, RequestID: id}, nil
}

func (s *server) answer(ctx context.Context, _ *mcp.CallToolRequest, in answerIn) (*mcp.CallToolResult, answerOut, error) {
	id := in.RequestID
	if id == "" {
		id = s.lastRequest()
	}
	if id == "" {
		return nil, answerOut{}, errors.New("no request_id and no ask in this session; pass the request_id ask returned")
	}
	r, err := loadRequest(s.dir, id)
	if err != nil {
		return nil, answerOut{}, err
	}
	var recs []turnRecord
	target, tree := Pane{}, treeUnavailable
	// A final result and a non-Claude target are answered from the request alone.
	if !r.Final && r.Agent == "claude" {
		if recs, err = readTurns(journalPath(s.dir, r.PaneID)); err != nil {
			return nil, answerOut{}, fmt.Errorf("read the turn journal of %s: %w", r.Target, err)
		}
		target, tree = s.findTarget(ctx, r.PaneID)
	}
	res := requestState(r, recs, target, tree)
	if !r.Final && res.Final && (res.State == stateDone || res.State == stateInterrupted) {
		// Only a final result is stored; later calls return it without reading the journal. The
		// caller gets the result even when it cannot be stored; the next call computes it again.
		next := r
		next.State, next.Reply, next.FinishedAt, next.Final = res.State, res.Reply, res.FinishedAt, true
		if err := saveRequest(s.dir, next); err != nil {
			log.Printf("answer %s: store the result: %v", r.ID, err)
		}
	}
	return nil, answerOut{
		RequestID:  r.ID,
		State:      res.State,
		Reply:      res.Reply,
		Final:      res.Final,
		Target:     r.Target,
		Name:       r.Name,
		SentAt:     r.SentAt,
		FinishedAt: res.FinishedAt,
		Detail:     res.Detail,
	}, nil
}

// findTarget looks up the pane with agterm pane id paneID in the tree. A failing tree is not an
// error: answer still answers from the journal, without the blocked check.
func (s *server) findTarget(ctx context.Context, paneID string) (Pane, treeLookup) {
	panes, err := s.ctl.panes(ctx, s.self)
	if err != nil {
		return Pane{}, treeUnavailable
	}
	if p, ok := findPaneByID(panes, paneID); ok {
		return p, paneInTree
	}
	return Pane{}, paneNotInTree
}

// newMCPServer builds one MCP server around s. stdio runs one for the process; HTTP runs one per
// client session, so each caller keeps its own self pane.
func newMCPServer(s *server) *mcp.Server {
	srv := mcp.NewServer(&mcp.Implementation{Name: "go-ask", Version: version}, nil)
	mcp.AddTool(srv, &mcp.Tool{
		Name: "sessions",
		Description: "List agterm panes that ask can send to, one entry per pane: a session's primary pane, " +
			"plus its right split when open. Pass an entry's target to ask. self marks the caller's own pane, " +
			"focused the pane the user has focus on. " +
			"agents=true keeps only panes running an agent CLI.",
	}, s.sessions)
	mcp.AddTool(srv, &mcp.Tool{
		Name: "ask",
		Description: "Type text into another agterm session's input and press Enter, as if a user sent it. " +
			"Returns as soon as the text is submitted, with a request_id; it does not wait for the reply. " +
			"Pass the request_id to answer later to get the reply. " +
			"Without session_id it goes to the pane the user has focus on in agterm. " +
			"Whatever was already typed in the target's input is cleared first. " +
			"Refuses the caller's own pane and any pane at a bare shell, where the text would run as commands.",
	}, s.ask)
	mcp.AddTool(srv, &mcp.Tool{
		Name: "answer",
		Description: "Get the reply to a message sent with ask. Returns at once and never waits: state done with the reply, " +
			"or a state saying why there is none yet (running, blocked, pending, lost, interrupted, unsupported) " +
			"with a detail sentence. Call it once when the user asks for the answer, not in a loop. " +
			"Without request_id it answers the last ask of this MCP session. " +
			"final says the result can no longer change; once any message reached the agent while it was busy, " +
			"no result from that Claude session is final, and the reply is the best guess. " +
			"Supports Claude agents only for now.",
	}, s.answer)
	mcp.AddTool(srv, &mcp.Tool{
		Name: "recap",
		Description: "Recap what the Claude agent in an agterm pane did in its last turns: a markdown summary " +
			"of at most about 250 words with four sections (what was done, the user's decisions, the current state " +
			"including what is not pushed or merged, what is left), written by a local Ollama model from that " +
			"Claude session's transcript. Takes about 40-50 s with the default model: call it once and wait. " +
			"Without session_id it recaps the pane the user has focus on in agterm. turns is how many of the " +
			"user's last prompts to cover (default 6, at most 50). language is the language to write it in, such as " +
			"English or Russian (default English); pass the language you answer the user in. Types nothing into the pane, so the caller's " +
			"own pane can be recapped too. Supports Claude panes only and needs Ollama running on this machine.",
	}, s.recap)
	return srv
}

const defaultAddr = "127.0.0.1:7275"

func main() {
	// `go-ask hook` is Claude's hook entry point; it takes no flags.
	if len(os.Args) > 1 && os.Args[1] == "hook" {
		os.Exit(hookMain(os.Stdin, os.Stderr, os.Getenv, stateDir(), time.Now))
	}
	httpMode := flag.Bool("http", false, "serve MCP over Streamable HTTP instead of stdio")
	addr := flag.String("addr", defaultAddr, "listen address in -http mode")
	tokenFile := flag.String("token-file", "", "file with the bearer token clients must send in -http mode; required off loopback")
	showVersion := flag.Bool("version", false, "print the version and exit")
	flag.Parse()
	if *showVersion {
		fmt.Println("go-ask", version)
		return
	}
	log.SetFlags(0)
	log.SetPrefix("go-ask: ")
	ctl := agtermctl{bin: findAgtermctl()}
	dir := stateDir()
	deps := newRecapDeps(os.Getenv)

	if *httpMode {
		if err := serveHTTP(*addr, *tokenFile, dir, ctl, deps); err != nil {
			log.Fatal(err)
		}
		return
	}
	s := &server{ctl: ctl, self: selfFromEnv(), dir: dir, deps: deps}
	if err := newMCPServer(s).Run(context.Background(), &mcp.StdioTransport{}); err != nil {
		log.Fatal(err)
	}
}
