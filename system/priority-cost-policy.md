# Priority and Cost Policy

Use `.agents/priority-queue.jsonl` for append-only cross-project task ranking. Score tasks using user priority, business value, urgency, dependency blocking, effort, risk, cost estimate, expected revenue/impact, unblock value, safe parallelism, human-help availability, free/local-model suitability, paid-review requirement, and human-approval requirement. Run `~/.hermes/scripts/project_agent_priority.py review --root /home/ubuntu --write-report --format text` for weekly priority review.
