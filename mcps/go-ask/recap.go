package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

const (
	recapDefaultTurns = 6
	recapMaxTurns     = 50
	recapDefaultModel = "gemma4:e4b"
	// recapDefaultLanguage is the language of a recap when the caller names none.
	recapDefaultLanguage = "English"
	recapMaxLanguage     = 40 // runes: a language name or code, not a sentence
)

type recapIn struct {
	SessionID string `json:"session_id,omitempty" jsonschema:"focused for the pane the user has focus on in agterm; or a session id, unique id prefix or exact session name, with :right appended for the split pane. Omitted: the focused pane"`
	Turns     int    `json:"turns,omitempty" jsonschema:"how many of the user's last prompts the recap covers, 1 to 50. Omitted or 0: 6"`
	Model     string `json:"model,omitempty" jsonschema:"Ollama model that writes the recap. Omitted: RECAP_MODEL from go-ask's environment, else gemma4:e4b"`
	Language  string `json:"language,omitempty" jsonschema:"the language to write the recap in: a name such as English, Russian or German, or a code such as de. Omitted: English"`
}

type recapOut struct {
	Recap        string `json:"recap" jsonschema:"the recap, markdown in the requested language: what was done, the user's decisions, the current state, what is left"`
	Language     string `json:"language" jsonschema:"the language the recap was asked for"`
	Target       string `json:"target"`
	Name         string `json:"name"`
	Model        string `json:"model" jsonschema:"the Ollama model that wrote the recap"`
	Turns        int    `json:"turns" jsonschema:"user prompts the recap covers, after the default and the 1 to 50 limit"`
	Transcript   string `json:"transcript" jsonschema:"path of the Claude transcript the recap was written from"`
	DurationMs   int64  `json:"duration_ms" jsonschema:"how long the call took, milliseconds"`
	PromptTokens int    `json:"prompt_tokens,omitempty" jsonschema:"tokens the model read, when Ollama reports them"`
	OutputTokens int    `json:"output_tokens,omitempty" jsonschema:"tokens the model wrote, when Ollama reports them"`
}

// recapDeps is what recap needs besides agterm: the Ollama client, the pane → transcript locator
// and the default model. main builds it once from the environment; every server shares it.
type recapDeps struct {
	ollama  ollamaClient
	locator claudeLocator
	model   string // RECAP_MODEL, else gemma4:e4b
}

func newRecapDeps(getenv func(string) string) recapDeps {
	model := strings.TrimSpace(getenv("RECAP_MODEL"))
	if model == "" {
		model = recapDefaultModel
	}
	return recapDeps{ollama: newOllamaClient(getenv), locator: newClaudeLocator(), model: model}
}

// recapTurns applies the turns rule: absent or 0 is the default 6, then the value is held within
// 1…50 (a negative value is 1, as recap.py's max(turns, 1)).
func recapTurns(n int) int {
	if n == 0 {
		return recapDefaultTurns
	}
	return min(max(n, 1), recapMaxTurns)
}

// recapLanguage applies the language rule: absent or blank is English; otherwise the value,
// trimmed, goes into the prompt as it is, so it must be one short line.
func recapLanguage(v string) (string, error) {
	v = strings.TrimSpace(v)
	if v == "" {
		return recapDefaultLanguage, nil
	}
	if utf8.RuneCountInString(v) > recapMaxLanguage || strings.IndexFunc(v, unicode.IsControl) >= 0 {
		return "", fmt.Errorf("language must be a language name or code on one line, at most %d characters", recapMaxLanguage)
	}
	return v, nil
}

func (s *server) recap(ctx context.Context, _ *mcp.CallToolRequest, in recapIn) (*mcp.CallToolResult, recapOut, error) {
	start := time.Now()
	turns := recapTurns(in.Turns)
	language, err := recapLanguage(in.Language)
	if err != nil {
		return nil, recapOut{}, err
	}
	model := strings.TrimSpace(in.Model)
	if model == "" {
		model = s.deps.model
	}
	if model == "" {
		model = recapDefaultModel
	}
	target := in.SessionID
	if target == "" {
		target = "focused"
	}
	panes, err := s.ctl.panes(ctx, s.self)
	if err != nil {
		return nil, recapOut{}, err
	}
	p, err := resolveTarget(panes, target)
	if err != nil {
		return nil, recapOut{}, err
	}
	// An unknown foreground (a wrapper script) may still run claude: the locator decides.
	if p.Agent != "" && p.Agent != "claude" {
		return nil, recapOut{}, fmt.Errorf("recap supports Claude only; %s runs %s", p.Target, p.Agent)
	}
	_, path, err := s.deps.locator.transcriptFor(ctx, p.paneID)
	switch {
	case errors.Is(err, errNoClaude):
		return nil, recapOut{}, fmt.Errorf("no live claude in %s (%s)", p.Target, p.Name)
	case errors.Is(err, errNoPaneID):
		return nil, recapOut{}, fmt.Errorf("agterm's tree gives %s (%s) no pane id, so the claude in it cannot be found", p.Target, p.Name)
	case errors.Is(err, errNoTranscript): // names the session id
		return nil, recapOut{}, err
	case err != nil:
		return nil, recapOut{}, fmt.Errorf("find the claude in %s: %w", p.Target, err)
	}
	rows, err := readTranscript(path, turns) // the error names the path
	if err != nil {
		return nil, recapOut{}, err
	}
	digest := buildDigest(rows, turns)
	if digest == "" {
		return nil, recapOut{}, fmt.Errorf("the transcript of %s has no turns yet", p.Target)
	}
	text, promptTokens, outputTokens, err := s.deps.ollama.chat(ctx, model, language, digest)
	if err != nil {
		return nil, recapOut{}, err
	}
	return nil, recapOut{
		Recap:        text,
		Language:     language,
		Target:       p.Target,
		Name:         p.Name,
		Model:        model,
		Turns:        turns,
		Transcript:   path,
		DurationMs:   time.Since(start).Milliseconds(),
		PromptTokens: promptTokens,
		OutputTokens: outputTokens,
	}, nil
}
