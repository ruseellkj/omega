"""omega_agent — Layer 2, the portable core.

The loop, the harness, the two event vocabularies, the message model, and the
provider *contract*. What it knows about: messages, events, tools, turns.

What it does not know about, and must never learn: **files, shells, terminals,
and vendors.** Those are `omega_coding` and `omega_ai` respectively, and both of
them import this package rather than the other way round.

`provider.py` living here is the point of the whole arrangement. The consumer
defines the interface and adapters conform to it — reverse that and Anthropic's
shape becomes the shape of the system. `omega_ai/provider.py` is a re-export, so
adapters can import from their own package without owning the contract, which is
exactly what Tau does.

`tests/test_layers.py` enforces all of this rather than trusting it.
"""

#: The same string as `version` in `pyproject.toml`, which is the source;
#: `tests/test_version.py` fails when the two differ. It said "0.2.0" from the
#: three-package split until 0.1.0 had already shipped.
__version__ = "0.1.0"
