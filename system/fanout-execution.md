# Fan-out Execution

Use fan-out for independent competing workstreams only when it improves speed, quality, or risk reduction. Create one branch/worktree per worker using `agent/<project>/<task-slug>/<worker-name>`, record parent/child job-ledger rows, write worker briefs under `.agents/fanout/<task_id>/workers/`, and record reconciliation before merging or updating project memory.
