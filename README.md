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

## Planned install (not yet wired)

```bash
# Primary: Nix
nix profile install github:stevengonsalvez/reflect-kb

# Fallback: pipx
pipx install reflect-kb

# Per-harness adapter (run once per harness you use)
reflect adapter install claude-code
reflect adapter install codex
reflect adapter install copilot
```

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
