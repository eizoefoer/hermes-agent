# SDLC, IaC, CI and Human Collaboration

Use feature/fix branches or external git worktrees for repo work. Pull `development` if present, otherwise `main`, before starting. Prefer vertical slices, small logical commits, focused PRs, existing CI first, lint/test/type/build/security checks before handoff, IaC/config/scripts for VM/app/service/cron/tunnel/deploy changes, and explicit handoff capsules/job-ledger rows so a human can continue safely.
