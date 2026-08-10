"""omega — a terminal coding agent built in layers.

Tier 1 contains two layers:

    Layer 1  provider   types.py, events.py, provider.py, providers/
    Layer 2  agent core tools.py, loop.py

Nothing above Layer 2 exists yet. `cli.py` is a print-based REPL, deliberately
not a terminal UI: at this tier a UI would hide whether the loop works.

The rule that shapes the package: **nothing points upward**. `providers/` knows
nothing about the loop, and the loop knows nothing about Anthropic.
"""

__version__ = "0.1.0"
