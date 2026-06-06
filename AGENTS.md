# Codex Operating Notes

## Checkpoint Commit/Push Expectation

When finishing a meaningful verified checkpoint in this repo, commit and push the intended changes without waiting for a separate prompt if a remote/upstream is available. Before committing, inspect status/diff, run the smallest meaningful verification, and exclude unrelated, generated, local-only, or ambiguous files; report any exclusions clearly. If verification fails, no remote/upstream exists, or the dirty state is ambiguous, stop and report the blocker instead of forcing a commit.
