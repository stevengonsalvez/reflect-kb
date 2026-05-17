# reflect-kb has moved

> **This repository is archived.** Active development of reflect-kb now happens inside the [agents-in-a-box](https://github.com/stevengonsalvez/agents-in-a-box) monorepo.

## New location

[`github.com/stevengonsalvez/agents-in-a-box/tree/main/reflect-kb`](https://github.com/stevengonsalvez/agents-in-a-box/tree/main/reflect-kb)

## New install command

```bash
uv tool install --upgrade 'git+https://github.com/stevengonsalvez/agents-in-a-box.git#subdirectory=reflect-kb[graph]'
```

Verify:

```bash
reflect --version    # → reflect, version 0.1.x
```

## What changed

Today (2026-05-17) reflect-kb moved from its standalone repo into the agents-in-a-box monorepo as a workspace member. The Claude Code plugin that orchestrates it (`reflect@agents-in-a-box`) also moved from `toolkit/packages/plugins/reflect/` to `plugins/reflect/` at the monorepo root, alongside `reflect-kb/`.

The two were tightly coupled — most changes touched both the library and the plugin in lockstep. Keeping them in the same repo collapses the cross-repo coordination overhead. The KB content (`~/.learnings/`) stays as a separate sibling repo (it's data, not code).

## Why the move

- reflect-kb's only real consumer was the agents-in-a-box reflect plugin
- Cross-repo changes (parser fixes → SKILL.md updates → re-run ingest) used to require 2-3 coordinated PRs
- Workspace pyproject.toml at the monorepo root lets uv install the package from a subdirectory

## For existing users

The old `uv tool install --upgrade 'git+https://github.com/stevengonsalvez/reflect-kb.git[graph]'` install URL still works via GitHub's automatic repo redirect, but please migrate to the new install command above. Existing local installs continue to work — `reflect` binary on PATH is unchanged.

## Companion plugin

Claude Code plugin (orchestrator, hooks, statusline, recall):

```bash
claude plugin marketplace add stevengonsalvez/agents-in-a-box
claude plugin install reflect@agents-in-a-box
```

## License

MIT. See the monorepo at the link above.
