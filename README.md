# reflect-kb

Universal cross-harness retrieval + learning knowledge base for AI coding agents.

This repository is the standalone, installable form of the `reflect` system
originally developed inside [ai-coder-rules](https://github.com/stevengonsalvez/ai-coder-rules).
It provides a hybrid GraphRAG + QMD retrieval pipeline plus a two-tier
(personal + team) knowledge model that works across Claude Code, Codex CLI,
and GitHub Copilot.

**Status:** Scaffold. Implementation tracks the v4 specification.

## Specification

The design and scope of this repo are driven by the v4 universal install spec:

- [`plans/reflect-v4-universal-install-spec.md`](https://github.com/stevengonsalvez/ai-coder-rules/blob/main/plans/reflect-v4-universal-install-spec.md)

Read that document first — it defines the architecture (reflect-kb = the tool,
team-kb = the content), the install UX (Nix primary, pipx fallback), the
two-KB model (`~/.learnings/` personal + configurable team path), the
confidence-gated write flow, and the cross-harness adapter story.

## Install

### Nix (primary)

The flake handles the nano-graphrag transitive-dep mess for you:

```bash
# One-shot run (no install).
nix run github:stevengonsalvez/reflect-kb -- --help

# Install into your profile.
nix profile install github:stevengonsalvez/reflect-kb
```

### Develop with Nix

```bash
nix develop
# Drops you into a shell with python311 + all reflect-kb runtime deps
# (nano-graphrag with the graspologic/hyppo/numba/llvmlite chain stripped).
# Editable overlay:
#   pip install -e . --no-deps --prefix $PWD/.venv-editable
```

### pipx (fallback)

See the Troubleshooting section below for the `--no-deps` workaround for the
nano-graphrag dep chain on Python ≥3.11.

### Per-harness adapter

```bash
reflect adapter install claude-code
reflect adapter install codex
reflect adapter install copilot
```
(Not yet wired — planned per the v4 spec.)

## Repo layout (target)

```
reflect-kb/
├── flake.nix                 # Nix package + dev shell
├── pyproject.toml            # pipx fallback
├── src/reflect_kb/           # CLI + retrieval engine
├── skills/                   # Tool-agnostic SKILL.md files
├── hooks/                    # Session-start recall hook (Claude)
├── schema/                   # YAML frontmatter JSON Schema
└── harness-adapters/         # Per-tool install scripts
    ├── claude-code/
    ├── codex/
    └── copilot/
```

The current tree only contains the top-level scaffolding. Subdirectories will
land in follow-up tasks per the v4 plan.

## Pre-commit (team-kb schema validation)

Team-kb repos gate writes on the v4 frontmatter schema. Add reflect-kb as a
hook source in your team-kb's `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/stevengonsalvez/reflect-kb
    rev: v0.1.0          # pin to a tag once published
    hooks:
      - id: reflect-kb-frontmatter
        # Default `files:` matches ^documents/.*\.md$ — override if you keep
        # learnings under a different path.
```

Then run `pre-commit install` in the team-kb checkout. Every commit that
touches `documents/*.md` will parse the YAML frontmatter and validate it
against `schemas/frontmatter.schema.json`. Invalid documents fail the commit
with a pointer to the offending field.

You can also run the validator directly (same binary pre-commit invokes):

```bash
python scripts/validate_frontmatter.py documents/ # recurse
python scripts/validate_frontmatter.py path/to/one-learning.md
```

Exit codes: `0` clean, `1` validation failure, `2` usage error.

## Troubleshooting

### `pipx install reflect-kb` fails when resolving nano-graphrag

`nano-graphrag` pulls an unusable transitive dependency chain on Python
≥3.11: `graspologic → hyppo → numba → llvmlite` — the last hop only builds
for Python <3.10. The bare `pipx install .` flow works because `nano-graphrag`
is **not** in the base `dependencies`; it lives under the `graph` extra.
Installing the `graph` extra via pip/pipx therefore fails.

The supported workaround is:

```bash
# 1. Install the CLI with its safe runtime deps.
pipx install .

# 2. Inject nano-graphrag without its broken transitive chain.
pipx inject reflect-kb nano-graphrag --pip-args="--no-deps"

# 3. Verify.
reflect --help
```

The base install covers every import path `reflect` actually uses
(sentence-transformers, nano-vectordb, networkx, tiktoken, openai, tenacity,
hnswlib, xxhash, numpy, click, rich, pyyaml). The Nix flake handles this
same `--no-deps` split out-of-band, so nix users do not hit this.

### `reflect --help` prints but subcommands crash on import

Early scaffold only — the full GraphRAG stack is gated behind the `graph`
extra. If you installed base-only (no nano-graphrag), `reflect search` and
`reflect reindex` will fail at import time. Complete the `pipx inject`
step above.

## License

MIT. See [`LICENSE`](./LICENSE).
