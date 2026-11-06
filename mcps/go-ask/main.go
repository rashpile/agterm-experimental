// go-ask is an MCP server (stdio) that types a message into another agterm session.
//
// Tools:
//
//	sessions(agents?)      list panes that can be addressed
//	ask(text, session_id?) paste text into a pane's input and press Enter; no reply is awaited
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
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const version = "0.1.0"

type server struct {
	ctl  agtermctl
	self selfAddr
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
	Sent   bool   `json:"sent"`
	Target string `json:"target"`
	Name   string `json:"name"`
	Agent  string `json:"agent,omitempty"`
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
	if err := s.ctl.send(ctx, p, in.Text); err != nil {
		return nil, askOut{}, fmt.Errorf("send to %s: %w", p.Target, err)
	}
	return nil, askOut{Sent: true, Target: p.Target, Name: p.Name, Agent: p.Agent}, nil
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
			"Returns as soon as the text is submitted; it does not wait for or return a reply. " +
			"Without session_id it goes to the pane the user has focus on in agterm. " +
			"Whatever was already typed in the target's input is cleared first. " +
			"Refuses the caller's own pane and any pane at a bare shell, where the text would run as commands.",
	}, s.ask)
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

	if *httpMode {
		if err := serveHTTP(*addr, *tokenFile, ctl); err != nil {
			log.Fatal(err)
		}
		return
	}
	s := &server{ctl: ctl, self: selfFromEnv()}
	if err := newMCPServer(s).Run(context.Background(), &mcp.StdioTransport{}); err != nil {
		log.Fatal(err)
	}
}
