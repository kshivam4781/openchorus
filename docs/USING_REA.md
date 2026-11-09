# Using REA (Reverse Engineer Anything) with this project

[REA](https://github.com/morluto/rea) is an MIT-licensed MCP server that lets
coding agents (Claude Code, Codex, Cursor and others) inspect binaries,
JavaScript/Electron apps, model files and websites. Its own terms limit it to
software you own or are authorized to examine, so use it on targets like
these, not on a closed commercial service.

## Install

```bash
npx rea-agents setup        # pick your agent, approve, restart the agent
```

Only install from github.com/morluto/rea, rea.tools or the `rea-agents` npm
package. Look-alike repos exist.

## Useful prompts for OpenChorus

Inspect the ACE-Step checkpoints you downloaded (header-only, no weights
loaded):

```
Use REA to inspect ACE-Step-1.5/checkpoints/*.safetensors and tell me the
parameter count and dtype of each model, so I can pick the right one for my GPU.
```

Audit this app's own front end:

```
Use REA's JavaScript analysis on openchorus/web and list every network call
the page makes and which UI action triggers it.
```

Build an agent test environment from a recorded session of your own local
instance:

```
Use REA to build an RL environment from my OpenChorus app at
http://127.0.0.1:7777. Cover create, extend and delete. Save it in openchorus-env.
```
