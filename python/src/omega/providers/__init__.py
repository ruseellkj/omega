"""Provider adapters — the only place in omega that knows a vendor exists.

Each module here implements `omega.provider.ModelProvider`. Nothing in this
package is imported by the loop; the loop only ever sees the Protocol.

A useful check that the layering held:

    grep -ri anthropic src/omega/ --include='*.py'

should match `providers/anthropic.py` and nothing else.
"""
