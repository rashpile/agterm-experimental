package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const (
	defaultOllamaHost = "http://127.0.0.1:11434"
	ollamaCeiling     = 10 * time.Minute // the longest one recap call waits for the model
	ollamaKeepAlive   = "5m"             // how long Ollama keeps the model loaded after the call
	ollamaTemperature = 0.2
	ollamaBodyCap     = 300 // runes of an error body quoted in the error
)

// ollamaClient writes the recap with a local Ollama model: one non-streaming chat request, the
// same request recap.py streams.
type ollamaClient struct {
	host string // scheme and host, no trailing slash
	http *http.Client
	// ceiling bounds one call on top of the caller's ctx; 0 leaves only the ctx. It is a ctx
	// deadline, not http.Client.Timeout, so that hitting it is told apart from a network timeout.
	ceiling time.Duration
}

// newOllamaClient reads the host from OLLAMA_HOST, as recap.py does.
func newOllamaClient(getenv func(string) string) ollamaClient {
	return ollamaClient{host: ollamaHost(getenv("OLLAMA_HOST")), http: &http.Client{}, ceiling: ollamaCeiling}
}

// ollamaHost normalizes an OLLAMA_HOST value: empty is the default host, a value without a
// scheme gets http://, trailing slashes are trimmed.
func ollamaHost(v string) string {
	h := strings.TrimSpace(v)
	if h == "" {
		return defaultOllamaHost
	}
	if !strings.Contains(h, "://") {
		h = "http://" + h
	}
	return strings.TrimRight(h, "/")
}

// recapSystemFor and recapPromptFor are recap.py's SYSTEM and PROMPT in English, with the
// language the recap is written in as a parameter. The digest they frame is recap.py's, Russian
// cut marker included.
func recapSystemFor(language string) string {
	return "You help a developer recall what happened in a Claude Code agent session. " +
		"Write in " + language + ". Rely only on the journal and invent nothing: " +
		"if something is not in the journal, do not write about it."
}

func recapPromptFor(language string) string {
	return "Below is the journal of the session's last turns: the user's messages (### USER), the user's " +
		"answers to the agent's questions (USER ANSWER), the assistant's text (ASSISTANT), tool calls " +
		"(- tool) and subagent reports. Write a summary of the session's state in " + language +
		", section titles included, at most 250 words, with these sections:\n" +
		"1. What was done (as bullet points, with the names of commits, branches and files when the journal has them).\n" +
		"2. The user's decisions.\n" +
		"3. Current state: what is running, what is changed, what is merged, what is not pushed.\n" +
		"4. What is left to do or check.\n\n" +
		"JOURNAL:\n"
}

type ollamaMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ollamaOptions struct {
	NumCtx      int     `json:"num_ctx"`
	Temperature float64 `json:"temperature"`
}

type ollamaChatRequest struct {
	Model     string          `json:"model"`
	Stream    bool            `json:"stream"`
	KeepAlive string          `json:"keep_alive"`
	Options   ollamaOptions   `json:"options"`
	Messages  []ollamaMessage `json:"messages"`
}

type ollamaChatResponse struct {
	Message         ollamaMessage `json:"message"`
	PromptEvalCount int           `json:"prompt_eval_count"`
	EvalCount       int           `json:"eval_count"`
	Error           string        `json:"error"`
}

// errOllamaCeiling is the cause of the ctx chat derives when the client's ceiling passes.
var errOllamaCeiling = errors.New("ollama ceiling passed")

// modelNotFound matches an Ollama error body about a model it does not have, such as
// `{"error":"model \"x\" not found, try pulling it first"}`.
var modelNotFound = regexp.MustCompile(`(?i)model.*not found`)

// chat sends the digest to model with the system prompt and prompt for language and returns the
// trimmed answer with the prompt and output token counts. The call ends when ctx ends or the
// client's ceiling passes, whichever is first; a cancelled ctx is returned as ctx's error.
func (c ollamaClient) chat(ctx context.Context, model, language, digest string) (text string, promptTokens, outputTokens int, err error) {
	callCtx := ctx
	if c.ceiling > 0 {
		var cancel context.CancelFunc
		callCtx, cancel = context.WithTimeoutCause(ctx, c.ceiling, errOllamaCeiling)
		defer cancel()
	}
	hc := c.http
	if hc == nil {
		hc = http.DefaultClient
	}
	body, err := json.Marshal(ollamaChatRequest{
		Model:     model,
		Stream:    false,
		KeepAlive: ollamaKeepAlive,
		Options:   ollamaOptions{NumCtx: numCtxFor(digest), Temperature: ollamaTemperature},
		Messages: []ollamaMessage{
			{Role: "system", Content: recapSystemFor(language)},
			{Role: "user", Content: recapPromptFor(language) + digest},
		},
	})
	if err != nil {
		return "", 0, 0, fmt.Errorf("encode the ollama request: %w", err)
	}
	req, err := http.NewRequestWithContext(callCtx, http.MethodPost, c.host+"/api/chat", bytes.NewReader(body))
	if err != nil {
		return "", 0, 0, fmt.Errorf("ollama host %q: %w", c.host, err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := hc.Do(req)
	if err != nil {
		if err := c.endedErr(ctx, callCtx, model); err != nil {
			return "", 0, 0, err
		}
		var ue *url.Error
		if errors.As(err, &ue) {
			err = ue.Err
		}
		return "", 0, 0, fmt.Errorf("ollama is not reachable at %s (%v); start it with: ollama serve", c.host, err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		if err := c.endedErr(ctx, callCtx, model); err != nil {
			return "", 0, 0, err
		}
		return "", 0, 0, fmt.Errorf("read the ollama answer: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		msg := fmt.Sprintf("ollama answered %d", resp.StatusCode)
		if b := strings.TrimSpace(string(raw)); b != "" {
			msg += ": " + headRunes(b, ollamaBodyCap)
		}
		if resp.StatusCode == http.StatusNotFound || modelNotFound.Match(raw) {
			msg += "; pull it with: ollama pull " + model
		}
		return "", 0, 0, errors.New(msg)
	}
	var out ollamaChatResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		return "", 0, 0, fmt.Errorf("ollama answered something other than a chat response (%v): %s", err, headRunes(strings.TrimSpace(string(raw)), ollamaBodyCap))
	}
	if out.Error != "" {
		return "", 0, 0, fmt.Errorf("ollama: %s", out.Error)
	}
	text = strings.TrimSpace(out.Message.Content)
	if text == "" {
		return "", 0, 0, errors.New("ollama returned an empty answer")
	}
	return text, out.PromptEvalCount, out.EvalCount, nil
}

// endedErr is the error chat returns for a failed request or read when the cause is not the
// transport: the caller's ctx ended (its own error) or the ceiling passed. nil means neither.
func (c ollamaClient) endedErr(ctx, callCtx context.Context, model string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if errors.Is(context.Cause(callCtx), errOllamaCeiling) {
		return fmt.Errorf("ollama did not answer within %s; try fewer turns or a smaller model than %s", c.ceiling, model)
	}
	return nil
}
