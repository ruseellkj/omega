# cli-agent

Building a terminal coding agent from scratch — twice, in Python and TypeScript — to understand
how modern coding agents actually work.

**Status: work in progress.** The research and documentation are done; the implementations are
next. There is no working agent in this repo yet.

## What's here

Everyting so far lives in [`docs/`](docs/). It's a study of two minimal, MIT-licensed coding
agents, written up so the concepts are usable rather than just described.

Suggested reading order:

| Document | What it is |
|---|---|
| [`03-architecture/01-plain.md`](docs/03-architecture/01-plain.md) | How a coding agent works, zero jargon |
| [`03-architecture/02-beginner.md`](docs/03-architecture/02-beginner.md) | A working 70-line agent, and the 9 ways it breaks |
| [`00-concepts/anatomy.md`](docs/00-concepts/anatomy.md) | 42 components a coding agent can have, tiered |
| [`00-concepts/security.md`](docs/00-concepts/security.md) | It runs shell commands, so this is first-class |
| [`03-architecture/03-production.md`](docs/03-architecture/03-production.md) | The real architecture: 5 layers, 4 boundaries |
| [`01-teardown/`](docs/01-teardown/) | Layer-by-layer teardown of both references (reference material) |
| [`04-glossary.md`](docs/04-glossary.md) | Every term, with an everyday analogy |
| [`04-folder-trees.md`](docs/04-folder-trees.md) | How the architecture maps onto directories |
| [`06-product-roadmap.md`](docs/06-product-roadmap.md) | What it would take to go from working to product |

`./scripts/build-pdf.sh` renders it all into a single PDF (requires `pandoc` and one of
`typst`/`tectonic`/`pdflatex`).

## Planned

- `python/` — implementation in Python, in three tiers: working → usable → survives long tasks
- `typescript/` — the same architecture again, independently, to separate what's architecture
  from what's language

## References

Both MIT-licensed, and both excellent:

- **[Pi](https://github.com/earendil-works/pi)** — TypeScript. The production-grade version.
- **[Tau](https://github.com/huggingface/tau)** — Python, a port of Pi. The readable one, with
  design notes worth reading on their own.

The implementations here are written independently, not forked. Where the two references agree,
that's treated as the architecture; where they differ, the docs record the choice and why.
