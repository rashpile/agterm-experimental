package main

import (
	"context"
	"crypto/subtle"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// Headers a client sends so the server knows which pane is its own. Claude Code fills them
// from its env: --header 'X-Agterm-Session-Id: ${AGTERM_SESSION_ID:-}'.
const (
	headerSessionID = "X-Agterm-Session-Id"
	headerPane      = "X-Agterm-Pane"
)

func selfFromHeaders(h http.Header) selfAddr {
	return newSelf(h.Get(headerSessionID), h.Get(headerPane))
}

// httpHandler serves MCP at /mcp. The SDK calls getServer once per new client session, so
// every session gets its own self pane, read from that session's first request. A non-empty
// token makes every request carry it as a bearer token. dir is the state dir every session's
// server keeps its requests in.
func httpHandler(ctl agtermctl, token, dir string) http.Handler {
	mcpHandler := mcp.NewStreamableHTTPHandler(func(r *http.Request) *mcp.Server {
		self := selfFromHeaders(r.Header)
		if self.SessionID == "" {
			log.Printf("new session from %s without %s: no pane is self", r.RemoteAddr, headerSessionID)
		} else {
			log.Printf("new session from %s, self %s:%s", r.RemoteAddr, self.SessionID, self.Pane)
		}
		return newMCPServer(&server{ctl: ctl, self: self, dir: dir})
	}, &mcp.StreamableHTTPOptions{SessionTimeout: 24 * time.Hour})

	mux := http.NewServeMux()
	// A web page in a browser must not be able to type into a terminal. The SDK already
	// rejects a non-localhost Host on a localhost listener (DNS rebinding); this rejects
	// cross-origin browser requests. Clients outside a browser send neither header and pass.
	mux.Handle("/mcp", requireBearer(token, http.NewCrossOriginProtection().Handler(mcpHandler)))
	return mux
}

// requireBearer rejects requests without "Authorization: Bearer <token>". An empty token
// turns the check off.
func requireBearer(token string, next http.Handler) http.Handler {
	if token == "" {
		return next
	}
	want := []byte("Bearer " + token)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), want) != 1 {
			log.Printf("401 from %s", r.RemoteAddr)
			w.Header().Set("WWW-Authenticate", `Bearer realm="go-ask"`)
			http.Error(w, "missing or wrong bearer token", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// readToken reads the token file. Like ssh with a private key, it refuses a file that other
// users can read.
func readToken(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if perm := info.Mode().Perm(); perm&0o077 != 0 {
		return "", fmt.Errorf("token file %s has mode %#o; run chmod 600 %s", path, perm, path)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(data))
	if len(token) < 16 {
		return "", fmt.Errorf("token in %s is shorter than 16 characters; generate one with: openssl rand -hex 32", path)
	}
	return token, nil
}

// isLoopback reports whether addr only accepts connections from this machine. An empty host
// listens on every interface, and a hostname other than localhost may resolve anywhere.
func isLoopback(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func serveHTTP(addr, tokenFile, dir string, ctl agtermctl) error {
	var token string
	if tokenFile != "" {
		var err error
		if token, err = readToken(tokenFile); err != nil {
			return err
		}
	}
	if token == "" && !isLoopback(addr) {
		return fmt.Errorf("%s accepts connections from other machines, and anyone who connects can type into your sessions; pass -token-file", addr)
	}
	srv := &http.Server{Addr: addr, Handler: httpHandler(ctl, token, dir), ReadHeaderTimeout: 10 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	errc := make(chan error, 1)
	go func() { errc <- srv.ListenAndServe() }()
	auth := "no token"
	if token != "" {
		auth = "bearer token required"
	}
	log.Printf("listening on http://%s/mcp, %s", addr, auth)

	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	// Open SSE streams can outlive the grace period; cut them then.
	if err := srv.Shutdown(shutdownCtx); err != nil && !errors.Is(err, http.ErrServerClosed) {
		srv.Close()
	}
	return nil
}
