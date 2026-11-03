---
schema: 1
template: true
autonomy: default
defaults:
  mode: worktree
  leftovers: leave
phases:
  brainstorm: {companion: brainstorm, entry: skills/brainstorm/SKILL.md}
  plan: {companion: planning, entry: commands/make.md, reviewer: agents/plan-review.md}
  exec: {companion: planning, scripts: skills/exec/scripts, rules: scripts/resolve-rules.sh}
  review: {companion: revmux, entry: SKILL.md, scripts: scripts, requires: [revmux]}
---

# default profile

make-task's `default` profile: today's phases and cards, with copies of the brainstorm, planning and
revmux skills. A `## <phase>` section added below (`## brainstorm`, `## plan`, `## exec`, `## review`)
gives that phase more instructions. They take precedence over the phase's companion text, never over
make-task's own rules.
