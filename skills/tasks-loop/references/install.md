In reference/config.md example of config.

Install create file .tasks-loop/config.md

AskUserQuestions:

1. what issue tracker to use (from task-trackers folder)
  - gitea
  - markdown
  - custom
Copy selected workflow from references/task-trackers/ to .tasks-loop/<tracker>.md and add config paremeter 'task-tracker'
Automatically resolve status  for selected tracker copy references/task-trackers/<tracker>-status.md

2. selected tracker can have install section follow it to ask and config

3. what agent-driver to use
  - bash-claude-agterm
  - go-claude-agterm
  - custom URL to agent dirver (SKILL download it)

OOB agent drivers:
bash-claude-agterm - download from https://gitea.rashpile.net/pkoptilin/agent-driver/src/branch/main/bash-claude-agterm
go-claude-agterm - download from  https://gitea.rashpile.net/pkoptilin/agent-driver/src/branch/main/go-claude-agterm

add parameter to config 'agent-driver' with agent driver name and command 'agent-driver-cmd' to run skill instruction

Each agent driver has skill verb with instruction how to use it

4. select workflow
 - standart workflow (use references/workflows/workflow.md)
 - custom
Copy selected workflow to .tasks-loop/workflow.md and add config paremeter 'task-workflow'
