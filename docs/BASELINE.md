# Frozen baseline: `baseline-v0.3.0`

| Item | Value |
|------|-------|
| Commit | `de08d4691e7b6c02969371f8c8c99f451eca156e` |
| Content | Phase 1 audio inspector, Phase 2 feature engine, Phase 3 constrained DTW matcher |
| Annotated tag | `baseline-v0.3.0` (created locally on this commit) |

Later results must name this commit (or a descendant) and say whether any matcher/feature code
changed since. To reproduce the baseline: `git checkout de08d46 && make test`.

> Status note: pushing the tag from the cloud session was refused by the git proxy ("remote end
> hung up"). If `git ls-remote --tags origin` does not list `baseline-v0.3.0`, push it from a
> machine with tag-push rights: `git tag -a baseline-v0.3.0 de08d46 -m "Sound2Word baseline: feature engine and constrained DTW" && git push origin baseline-v0.3.0`.
> The commit hash above is the authoritative reference either way.
