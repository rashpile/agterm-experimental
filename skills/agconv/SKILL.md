---
name: agconv
description: Use to start a two-agent conversation — this agent debates a topic with another agent in its own agterm session, turn by turn, until the goal is reached. Triggers - agconv, talk to another agent, debate this with an agent, second opinion from codex, discuss with claude, two-agent discussion, argue this out with another model.
argument-hint: "<companion-address|codex|claude> <topic>"
---

# agconv — start a conversation with another agent

You are the **starter**. The other agent is the **companion**. The protocol lives in the script,
not here: read it once and follow it.

The script is `agent-conversation`, in this skill's own directory.

## Start

1. **Get the companion's address.**
   - First argument is `codex` or `claude` → `<script> spawn codex|claude`. It prints one
     address on stdout. It takes the right pane when this session is not split, and a new
     session when it is.
   - Anything else → that argument **is** the address. Do not spawn.
2. **Read `<script> info`.** It is the whole protocol — turn taking, waiting, ending, safety.
   Everything below assumes you have read it.
3. **Pick your name** — one word, lowercase, not `claude` or `codex`.
4. **Decide the stance you assign the companion**, and make it oppose yours. The user gave you a
   topic, not a stance; inventing an opposed one is your job. Two agents with the same view agree
   in two turns and produce nothing.
5. **Send the opening turn — and assert nothing in it.**

   ```bash
   printf '%s' "topic: <the user's topic, sharpened into a question>
   my stance: <what you will argue>
   your stance: <the opposing stance you assign>" | <script> hello <your-name> <address>
   ```

   **State the topic without smuggling in your premises.** Anything you assert as background,
   the companion inherits — and then its agreement is something you manufactured, not evidence.
   Name the load-bearing claims as claims, point at the files or commands that would settle
   them, and say you would rather be checked than agreed with. A companion handed a frame can
   only argue inside it.

## Then

Follow `info`. In particular: **ending your turn is how you wait** — the companion's reply
arrives as a new turn in this session. Never poll its screen, never sleep-loop.

Report to the user after each exchange in two or three lines: what the companion argued, what you
answered. They are watching a debate, not reading a transcript.

## Ending

You are the starter, so **only you declare `DONE`** — the companion may only propose it. Declare
it when the goal is reached or at your tenth message, whichever comes first.

Then report: what was decided, what stayed open, how many turns it took, and the log path the
script printed.

**A session the script spawned is left open** so the user can read the companion's side. Say
where it is; do not close it.

## What this skill does not do

- **It does not carry the protocol.** `info` does, and it is read at runtime so a fix reaches
  every conversation. Never restate its rules here.
- **It does not make the companion agree with you.** A conversation that converges because both
  sides drifted to the middle produced nothing; say so in the report rather than dressing it up.
- **It does not decide anything for the user.** Two agents agreeing is not a decision made — the
  outcome is a recommendation, and it says so.
