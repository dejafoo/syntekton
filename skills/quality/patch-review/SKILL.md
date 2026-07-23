# Patch Review
Cite concrete `evidence_path` values that appear in the patch (for example
`src/app/cache.py`). Prefer file paths over vague references.

Rules:
- Do not invent evidence or files that are not in the patch/worktree.
- Blocking findings require high confidence (≥0.7) and a resolvable path.
- Style-only or naming nits are `minor` / `maintainability`, never `blocking`.
- Mark uncertain findings with lower confidence; they must not block.
- Map each finding to a clear recommended action.
