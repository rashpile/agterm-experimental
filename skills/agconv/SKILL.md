---
name: agconv
description: Use when unsure and a second opinion would help — asks another agent (codex or claude) in its own agterm session and keeps talking to it until the question is settled. Unlike a one-shot consult, you can correct a misunderstanding, supply what it lacks, and push back. Triggers - agconv, ask codex, ask another agent, second opinion, discuss this with codex, what would another model say, I am not sure about this, check my thinking with another agent.
argument-hint: "<companion-address|codex|claude> <what you want to ask>"
---

# agconv — ask another agent, and keep talking

A one-shot consult gives you an answer you cannot correct. This keeps the channel open: when the
answer rests on a misunderstanding, or you know something it does not, you say so and it responds.

The script is `agent-conversation`, in this skill's own directory. The protocol lives in it, not
here — read it once and follow it.

## Start

1. **Get the companion's address.**
   - First argument is `codex` or `claude` → `<script> spawn codex|claude`, which prints one
     address. It takes the right pane when this session is not split and it is free, otherwise a
     new session placed right after this one, in the same workspace.
   - Anything else → that argument **is** the address. Do not spawn.
2. **Read `<script> info`** — turn taking, waiting, ending, safety. What follows assumes you have.
3. **Pick your name** — one word, lowercase, not `claude` or `codex`.
4. **Ask the question**, on stdin:

   ```
   printf '%s' "<your question>

   what I know: ...
   what I am assuming: ...
   where to check: <paths, commands>" | <script> hello <your-name> <address>
   ```

## Asking well

This is the whole skill. A second opinion that echoes you is worth nothing, and models agree with
whatever frame the asker supplies.

- **Do not hand it your conclusion.** Ask the question, not the answer you are hoping to confirm.
- **Separate what you know from what you are assuming**, and say which is which. The assumptions
  are where you are most likely wrong and least likely to be challenged.
- **Point at what it can check** — files, commands, a live system. An opinion grounded in something
  outside both of you is the only kind that adds information. Say it may verify rather than accept.
- **Ask for what you actually want**: a critique, an alternative approach, a sanity check, a fact
  you cannot find. It is not a debate unless the question is one.

## Then

Follow `info`. In particular: **ending your turn is how you wait** — the reply arrives as a new
turn in this session. Never poll its screen, never sleep-loop.

Between turns, tell the user in two or three lines what came back and what you did with it.

**Correct it when it is wrong, and say so when it is right.** Both are useful turns. If it has
misread something, give it the missing piece rather than arguing around it.

## Ending

`info` carries the test for whether another turn is worth sending — checked evidence, an attack on
a newly proposed remedy, or a change to the governing criterion. **None of the three applies →
you are finished, however agreeable the conversation has become.**

Say `DONE` then, or by your tenth message, whichever comes first. Then
report: what you asked, what changed your mind, what stayed unresolved, and the log path.

**A session the script spawned is left open** so the user can read the other side. Say where it is.

## What this skill does not do

- **It does not carry the protocol.** `info` does, read at runtime, so a fix reaches every
  conversation. Never restate its rules here.
- **It does not turn agreement into evidence.** The companion usually shares your model and your
  blind spots; agreement is one more opinion, not corroboration. Report it as such — what it
  checked is worth more than what it concluded.
- **It does not decide for the user.** The outcome is a recommendation and it says so.
