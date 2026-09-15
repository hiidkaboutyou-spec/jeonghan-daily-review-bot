# Audited agent capabilities

This document records the 2026-09-15 review of six external projects proposed for Daily Hani. The production bot remains Python-first and no capability in this review adds a runtime dependency, new secret, database migration, daemon, MCP server, or external API requirement.

The machine-verifiable source pins and decisions live in `config/agent_capabilities.json`. Project-local skills live under `.agents/skills/` and are development instructions only.

## Adopted safely

### Vercel Labs `skills`

Daily Hani adopts the open Agent Skills project-local convention (`.agents/skills/`) and keeps the CLI itself optional and development-only. Codex can consume the project-local skill files directly, so Node/npm is not added to the production environment. Any future CLI use should use the audited version/revision rather than an unbounded latest release.

### Book-to-Skill

The useful idea is retained: convert durable reference material into compact, load-on-demand skills. Hani uses a native safety-adapted workflow in `.agents/skills/hani-book-to-skill/SKILL.md` rather than vendoring the upstream converter and parser stack. It cannot auto-install parsers or write to global agent directories. If an unusual file format ever justifies the upstream converter, evaluate the pinned upstream revision in scratch space with dependency auto-install disabled, then review output before promotion.

### Grounded research pattern from DeepTutor

DeepTutor's source-grounded knowledge/research patterns are useful, but its full application would add a large, overlapping stack including LlamaIndex, FAISS, web/server components, parsing packages, provider SDKs, and optional RAG engines. Hani therefore keeps only a small instruction-level grounded-research workflow. Existing code/docs/archive evidence comes first; external research is bounded and treated as untrusted data.

## Reference only

### Reverse Skill

The raw security router is not installed. Its skills include immediate-action/bootstrap semantics and broad pentest/reverse-engineering tooling that are unnecessary for this bot. Hani retains only the useful supply-chain lesson: inspect external skills, scripts, dependencies, global writes, credential access, and network behavior before adoption. The safe local replacement is `.agents/skills/third-party-capability-audit/SKILL.md`.

## Rejected for the current project

### OpenSEO

OpenSEO targets keyword research, rank tracking, backlinks, site audits, competitor analysis, and AI visibility, and self-hosting still requires a DataForSEO API key. Those workflows do not address a current X/AO3/Telegram/translation/archive requirement, so no code, MCP server, secret, or DataForSEO dependency is added.

### Orca

The reviewed Orca is a Rust/tmux worktree orchestration daemon for parallel coding agents. Its upstream README explicitly characterizes it as a hobby/vibe-coded project with rough edges and no stability/backward-compatibility guarantee, and the repository exposed no explicit license at audit time. Hani does not vendor its binary, skill, hooks, or daemon. Parallel development orchestration can be reconsidered outside production if a licensed, stable option is needed later.

## Supply-chain guard

Run:

```bash
python -m tools.validate_agent_capabilities
```

The validator is offline and checks that:

- every external source is pinned to a full commit SHA and has a recorded license state;
- all active skills stay inside `.agents/skills/`;
- rejected sources cannot back active skills;
- active skills contain expected frontmatter;
- risky auto-install/bootstrap patterns are not introduced into active skills;
- the capability pack continues to declare zero production runtime dependencies.

The same validator has focused unit tests in `tests/test_agent_capabilities.py` and is also exercised by the repository's normal test discovery.

## Rollback

This capability pack does not change production data or configuration. Rollback is deletion of the three project-local skill directories plus `config/agent_capabilities.json`, `tools/validate_agent_capabilities.py`, `tests/test_agent_capabilities.py`, and this document. No Telegram state, archive, database, cache, or encrypted backup migration is involved.
