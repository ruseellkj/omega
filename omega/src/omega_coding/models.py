"""Which models each provider offers, and how big their windows are.

## Why this file exists

It did not, and the absence showed in three places at once:

* `DEFAULT_MODEL` was a constant in each of the three adapters, so "what does
  omega default to" had three answers and nothing checked they agreed.
* `context.py` held a `MODEL_WINDOWS` table of its own — model metadata, living
  in the file that does window *accounting*.
* Nothing could answer "what else could I switch to?", which is precisely what
  `/model` needs.

## Two layers, because a hard-coded list ages badly

`BUILTIN` is a snapshot. Providers ship models continuously and a snapshot
learns about them when someone edits it and cuts a release — which is no use to
whoever wants the model that shipped this morning. So the answer is not a better
snapshot, it is a second layer the user owns: `~/.omega/models.json`, read at
every call, merged on top.

**Both references face this and answer it differently, and the difference is the
reason omega copies Tau:**

* **Pi generates it.** `packages/ai/src/providers/data/`, built from
  `models.dev`. A fresh clone has **nothing**: that path is `.gitignore`d
  (`pi/.gitignore:11`), so the data only appears after a Node build with
  network access. `ls research/pi/packages/ai/src/providers/data/` on this
  machine returns "No such file or directory".
* **Tau commits it.** `src/tau_coding/data/catalog.toml` is in the repository,
  and `~/.tau/catalog.toml` layers on top. A fresh clone has the full list,
  offline, immediately.

Pi's is the better *pipeline* — 721 models stay current without anyone typing
them. It is the wrong shape for omega, which has twelve: a generator and a
network dependency bought to maintain twelve rows, and a checkout that cannot
name a single model until the generator has run.

Tau's merge is the one copied here, down to the semantics:
`catalog_loader.py:229` does `list(dict.fromkeys([*overlay_models, *base_models]))`
— a **union**, overlay first. Your entries go on top of the built-ins, not
instead of them, so adding one model keeps the other eleven.

## What the overlay may add: models, not providers

Tau's overlay can introduce a whole provider, because a Tau catalog entry
carries the base URL, headers and wire-format compatibility flags — everything
needed to talk to it. omega's adapters are *code*, and only `cli.py` and
`evals.py` may name a provider. A `"my-provider"` key here would put models in
the picker for something nothing can send a request to, so an unknown provider
key is reported as a mistake rather than honoured.

A model needs a name and a window. A provider needs an adapter. That is the
whole reason the two are treated differently.

## A malformed overlay is reported, not fatal, and not silent

Three behaviours were available and the middle one is the only defensible one:

* **Tau raises** (`catalog_loader.py:185` — `CatalogError` on bad TOML). A typo
  in a convenience file should not stop the agent from starting.
* **`oauth.py:194` swallows** the error and returns `{}`. Right for a
  registration nobody has to write; wrong here, because a silently-ignored file
  looks identical to a file that worked, and the user is left wondering why
  their model is not listed.
* **This file reports.** `catalog()` returns the built-ins, and
  `overlay_problem()` carries a message `/model` prints. One bad entry is
  skipped and named; the good ones still load.

## Where every number comes from

Every window below was read from **https://models.dev/api.json** on 2026-09-22
— `curl` returned HTTP 200, 4,752,160 bytes, 222 providers — and parsed out of
the raw JSON rather than summarised, because a paraphrased number is a guess
wearing a citation.

| model | window | models.dev says |
|---|---|---|
| `claude-sonnet-5` | 1,000,000 | `anthropic.models["claude-sonnet-5"].limit.context` |
| `claude-opus-5` | 1,000,000 | `anthropic.models["claude-opus-5"].limit.context` |
| `claude-haiku-4-5-20251001` | 200,000 | `anthropic.models[...].limit.context` |
| `gpt-5` | 400,000 | `openai.models["gpt-5"].limit.context` |
| `gpt-5-mini` | 400,000 | `openai.models[...].limit.context` |
| `gpt-5-nano` | 400,000 | `openai.models[...].limit.context` |
| `o3` | 200,000 | `openai.models["o3"].limit.context` |
| `o4-mini` | 200,000 | `openai.models["o4-mini"].limit.context` |
| `gpt-5-codex` | 400,000 | not under `openai`; **6 providers list it, all 400000** |

**Two rows models.dev does not carry**, and why they are still here:
`claude-sonnet-5[1m]` and `claude-opus-5[1m]`. The `[1m]` suffix is a Claude
Code routing id, not a models.dev model. Both are set to 1,000,000, which is
what their base ids measure — so the suffix selects a route, not a different
window.

## Two corrections this audit produced, both in the same direction

Recorded because the reasoning that produced them was wrong in a way worth
remembering, not just the numbers:

* `claude-sonnet-5` shipped as **200,000**. It is 1,000,000.
* `claude-opus-5` shipped as **200,000**. It is 1,000,000.

Both were under-reported by a factor of five, which means compaction fired at a
fifth of the real capacity and threw away context nobody needed to lose — the
exact failure `DEFAULT_CONTEXT_WINDOW` is written to avoid, arriving instead
through a wrong entry.

The `opus-5` row survived one audit pass before this one. The argument for
keeping it at 200,000 was that Claude Code ships the million-token variant as
`claude-opus-5[1m]`, so the untagged id "must" be smaller. That is inference
about a naming scheme, presented where a measurement belonged, and models.dev
contradicts it outright. **The `[1m]` suffix distinguishes routes, not sizes.**

## What "authoritative" means here

`DEFAULTS` is the single source for a provider's default model, and
`tests/test_models.py` asserts each adapter's `DEFAULT_MODEL` still agrees with
it. Four places naming a default is four places to drift; the test is what keeps
the adapter constants honest rather than merely conventional.

## The file format, which the user has to be told

```json
{
  "anthropic": [
    {"name": "claude-opus-6", "window": 1000000, "note": "shipped today"}
  ]
}
```

A provider key from `BUILTIN`, a list of objects, `name` and `window` required,
`note` optional. `window` is **tokens** and it is the number compaction budgets
against — the one field worth getting right, and the reason a model omega has
never heard of is still better described by hand than guessed at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Used when a model is not listed. Deliberately the *smaller* common window:
#: over-reporting how full you are is safe, under-reporting is not.
DEFAULT_CONTEXT_WINDOW = 200_000


@dataclass(frozen=True, slots=True)
class Model:
    """One selectable model.

    `window` is what compaction budgets against, which is why a switch has to
    move it — going from a million-token model to a 200k one leaves a transcript
    instantly over budget, and the failure lands on the *next* request rather
    than on the switch that caused it.
    """

    name: str
    window: int
    note: str = ""


#: The snapshot. Matched by prefix, so dated variants and `[1m]` suffixes
#: resolve without an entry each. `~/.omega/models.json` layers on top of this
#: rather than replacing it — see the module docstring.
BUILTIN: dict[str, tuple[Model, ...]] = {
    "anthropic": (
        Model("claude-sonnet-5", 1_000_000, "balanced — the default"),
        Model("claude-sonnet-5[1m]", 1_000_000, "same model, million-token window"),
        Model("claude-opus-5", 1_000_000, "most capable"),
        Model("claude-opus-5[1m]", 1_000_000, "most capable, million-token window"),
        Model("claude-haiku-4-5-20251001", 200_000, "fastest, cheapest"),
    ),
    "openai": (
        Model("gpt-5", 400_000, "the default"),
        Model("gpt-5-mini", 400_000, "cheaper"),
        Model("gpt-5-nano", 400_000, "cheapest"),
        Model("o3", 200_000, "reasoning"),
        Model("o4-mini", 200_000, "reasoning, cheaper"),
    ),
    # Shortest on purpose. A ChatGPT subscription's real list is genuinely
    # dynamic and Tau reads it from `/codex/models` at runtime
    # (`openai_codex.py:975-1010`); omega does not, so this is the provider the
    # overlay matters most for.
    "openai-codex": (
        Model("gpt-5-codex", 400_000, "the default"),
        Model("gpt-5", 400_000, ""),
    ),
}

#: The one place a provider's default is decided. The adapters keep a
#: `DEFAULT_MODEL` constant for standalone use and `tests/test_models.py` asserts
#: the two never drift.
DEFAULTS: dict[str, str] = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-5",
    "openai-codex": "gpt-5-codex",
}


def overlay_path() -> Path:
    """Where the user's additions live. Absent is the normal state."""
    return Path.home() / ".omega" / "models.json"


def cache_path() -> Path:
    """Where `/model refresh` stores what models.dev said.

    A **separate file from the overlay**, and that separation is the point: a
    refresh must never overwrite a figure someone typed by hand. Two files, two
    owners, no arbitration needed.
    """
    return Path.home() / ".omega" / "models-cache.json"


def catalog() -> dict[str, tuple[Model, ...]]:
    """The built-ins with `~/.omega/models.json` merged on top.

    Read on every call rather than cached, which is what lets an edit take
    effect without restarting omega. Every caller is a cold path — startup,
    `/model`, `/context`, once per turn at most — so a `stat` and a read of a
    file this size is not worth a cache and the staleness it would buy.
    """
    return _merged()[0]


def overlay_problem() -> str | None:
    """What was wrong with the overlay, if anything. `/model` prints this.

    Separate from `catalog()` so the common path stays a plain dict, and so a
    broken file cannot break resolution — only its own reporting.
    """
    return _merged()[1]


def models_for(provider: str) -> tuple[Model, ...]:
    """What `/model` offers. Empty for a provider nobody catalogued."""
    return catalog().get(provider, ())


def default_for(provider: str) -> str | None:
    return DEFAULTS.get(provider)


def window_for(model: str) -> int:
    """The context window for a model, or a conservative default."""
    return _best_window(model)[0]


def window_is_guess(model: str) -> bool:
    """True when `window_for` fell back rather than matching an entry.

    `/model` asks this so it never prints the 200k fallback as though it were
    the provider's real figure — which is exactly the mistake that makes a
    million-token model compact at a fifth of its capacity, silently.
    """
    return _best_window(model)[1] == ""


def provider_of(model: str) -> str | None:
    """Which provider lists this model, if any. Used to keep `/model` honest
    about switching *within* a provider rather than silently across one."""
    for provider, entries in catalog().items():
        if any(entry.name == model for entry in entries):
            return provider
    return None


def _best_window(model: str) -> tuple[int, str]:
    """The window and the entry name that produced it, `""` if none did.

    Longest prefix wins, so `claude-sonnet-5[1m]` is not shadowed by
    `claude-sonnet-5` — the two differ by a factor of five, and matching the
    shorter one would budget a million-token conversation against 200k.
    """
    best = DEFAULT_CONTEXT_WINDOW
    matched = ""
    for entries in catalog().values():
        for entry in entries:
            if model.startswith(entry.name) and len(entry.name) > len(matched):
                matched, best = entry.name, entry.window
    return best, matched


def _merged() -> tuple[dict[str, tuple[Model, ...]], str | None]:
    """The three layers folded into one catalog, plus anything wrong with them.

    Lowest precedence first:

    1. `BUILTIN` — compiled in, always present, works with no network ever.
    2. `~/.omega/models-cache.json` — what `/model refresh` last read from
       models.dev. Fresher than the built-ins by construction.
    3. `~/.omega/models.json` — hand-written. **Wins**, because someone typing a
       figure into a file is making a deliberate correction, and a later refresh
       silently overruling it would undo their work without telling them.
    """
    cached, cache_problem = _read_layer(cache_path())
    overlay, overlay_problem = _read_layer(overlay_path())

    merged = dict(BUILTIN)
    for provider in {*cached, *overlay}:
        # Highest precedence first, `setdefault`, so the first writer of a name
        # wins. That is Tau's `dict.fromkeys([*overlay_models, *base_models])`
        # (`catalog_loader.py:229`) extended to three sources instead of two:
        # a union that keeps everything and orders it by who is most likely to
        # be right.
        by_name: dict[str, Model] = {}
        for entry in (
            *overlay.get(provider, ()),
            *cached.get(provider, ()),
            *BUILTIN.get(provider, ()),
        ):
            by_name.setdefault(entry.name, entry)
        merged[provider] = tuple(by_name.values())

    problems = [p for p in (overlay_problem, cache_problem) if p is not None]
    return merged, "; ".join(problems) if problems else None


def _read_layer(path: Path) -> tuple[dict[str, tuple[Model, ...]], str | None]:
    """Parse one layer file. Returns what loaded and what did not.

    Shared by the hand-written overlay and the models.dev cache because they
    hold the same shape on purpose — one parser, one set of error messages, and
    no chance of the two disagreeing about what a valid entry is.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # The normal state. Not a problem, and not worth mentioning.
        return {}, None
    except (OSError, UnicodeDecodeError) as error:
        return {}, f"{path} could not be read: {error}"

    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return {}, f"{path} is not valid JSON: {error}"
    if not isinstance(data, dict):
        return {}, f"{path}: expected an object keyed by provider name."

    found: dict[str, tuple[Model, ...]] = {}
    complaints: list[str] = []
    for provider, listed in data.items():
        if provider not in BUILTIN:
            complaints.append(
                f"{provider!r} is not a provider omega has an adapter for"
                f" (known: {', '.join(sorted(BUILTIN))})"
            )
            continue
        if not isinstance(listed, list):
            complaints.append(f"{provider!r} should map to a list of models")
            continue
        entries: list[Model] = []
        for index, raw in enumerate(listed):
            parsed = _model_from(raw)
            if isinstance(parsed, str):
                # One bad entry must not hide the good ones - `oauth.py:213`
                # makes the same call. Unlike that one, it is also named.
                complaints.append(f"{provider}[{index}]: {parsed}")
                continue
            entries.append(parsed)
        if entries:
            found[provider] = tuple(entries)

    if complaints:
        return found, f"{path}: " + "; ".join(complaints)
    return found, None


def _model_from(raw: object) -> Model | str:
    """A `Model`, or the reason this entry is not one."""
    if not isinstance(raw, dict):
        return "expected an object with a name and a window"

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return "name is required and must be a non-empty string"
    name = name.strip()

    window = raw.get("window")
    # `isinstance(True, int)` is True in Python, so `"window": true` would
    # otherwise become a one-token window and compact every single turn.
    if isinstance(window, bool) or not isinstance(window, int):
        return f"{name}: window is required, in tokens, as a whole number"
    if window <= 0:
        return f"{name}: window must be greater than zero"

    note = raw.get("note", "")
    if not isinstance(note, str):
        return f"{name}: note must be a string"

    return Model(name, window, note)


# ------------------------------------------------------------- models.dev
#
# The gap this closes: omega's built-in list is a snapshot, and the honest
# answer to "a model shipped today, how do I learn its window?" was *go look it
# up on models.dev and type it in*. Steps two and three of that are work omega
# can do itself with one request.
#
# **Pi has the right idea and the wrong mechanism.** Pi generates its catalog
# from models.dev at build time into a `.gitignore`d directory
# (`pi/.gitignore:11`), so a fresh clone knows no models until someone runs a
# Node build with network. Here the fetch is a command the user invokes, the
# built-ins still work offline forever, and the result is cached so it is read
# once and reused.

#: models.dev publishes its whole catalog as one JSON document.
MODELS_DEV_URL = "https://models.dev/api.json"

#: omega's provider name -> the key models.dev files it under. Only providers
#: with a direct counterpart are refreshed; see `RefreshReport.uncovered` for
#: why `openai-codex` is deliberately not in here.
MODELS_DEV_PROVIDERS: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
}


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """What a refresh did, in enough detail to print without guessing.

    `error` set means nothing was written. Every other field is only meaningful
    when `error is None`, which is why they default to empty rather than to
    plausible-looking zeros.
    """

    error: str | None = None
    #: provider -> how many models were read for it
    counts: dict[str, int] = field(default_factory=dict)
    #: providers omega has that models.dev does not cover, named rather than
    #: skipped in silence - a gap you cannot see is a gap you cannot fix.
    uncovered: tuple[str, ...] = ()
    #: models that are new relative to `BUILTIN`, which is what someone running
    #: this actually wants to know.
    added: tuple[str, ...] = ()
    written: Path | None = None


def models_from_models_dev(
    payload: object,
) -> tuple[dict[str, tuple[Model, ...]], tuple[str, ...]]:
    """Extract `(models per provider, providers we could not cover)`.

    **Pure — no network, no disk.** Takes the already-parsed document, so the
    whole of the interesting logic is testable against a fixture and the HTTP
    call above it stays four lines with nothing to get wrong. This is the same
    split as `openai_codex.sse_objects`, which takes lines rather than a
    response for the same reason.

    A model with no integer `limit.context` is skipped rather than defaulted.
    models.dev not knowing a window is information; inventing one to fill the
    gap would be exactly the guess this whole feature exists to remove.
    """
    if not isinstance(payload, dict):
        return {}, tuple(sorted(MODELS_DEV_PROVIDERS))

    found: dict[str, tuple[Model, ...]] = {}
    uncovered: list[str] = []
    for ours, theirs in MODELS_DEV_PROVIDERS.items():
        provider = payload.get(theirs)
        models = provider.get("models") if isinstance(provider, dict) else None
        if not isinstance(models, dict):
            uncovered.append(ours)
            continue

        entries: list[Model] = []
        for name, model in sorted(models.items()):
            if not isinstance(model, dict):
                continue
            limit = model.get("limit")
            window = limit.get("context") if isinstance(limit, dict) else None
            if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
                continue
            entries.append(Model(str(name), window, "models.dev"))
        if entries:
            found[ours] = tuple(entries)
        else:
            uncovered.append(ours)

    # Providers omega has that were never even candidates. `openai-codex` is
    # the live case: models.dev has no ChatGPT-subscription provider, and its
    # model list is genuinely dynamic - Tau reads it from `/codex/models` at
    # runtime (`openai_codex.py:975-1010`). Naming it beats pretending the
    # refresh covered everything.
    uncovered.extend(name for name in BUILTIN if name not in MODELS_DEV_PROVIDERS)
    return found, tuple(sorted(set(uncovered)))


async def refresh_from_models_dev(*, timeout: float = 20.0) -> RefreshReport:
    """Fetch models.dev, write the cache, and report. **Never raises.**

    A refresh is a convenience. Every failure here - offline, DNS, a 500, a
    changed schema - has to come back as a sentence the user can read, because
    the alternative is a traceback over a feature they could simply not use.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(MODELS_DEV_URL)
    except Exception as error:  # noqa: BLE001 - see the docstring
        return RefreshReport(error=f"could not reach models.dev: {error}")

    if response.status_code >= 400:
        return RefreshReport(
            error=f"models.dev answered {response.status_code}, so nothing was changed"
        )

    try:
        payload = response.json()
    except ValueError as error:
        return RefreshReport(error=f"models.dev sent something that is not JSON: {error}")

    found, uncovered = models_from_models_dev(payload)
    if not found:
        return RefreshReport(
            error="models.dev had no usable entries for omega's providers",
            uncovered=uncovered,
        )

    known = {entry.name for entries in BUILTIN.values() for entry in entries}
    added = tuple(
        sorted(
            entry.name
            for entries in found.values()
            for entry in entries
            if entry.name not in known
        )
    )

    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    provider: [
                        {"name": e.name, "window": e.window, "note": e.note}
                        for e in entries
                    ]
                    for provider, entries in sorted(found.items())
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        return RefreshReport(error=f"could not write {path}: {error}")

    return RefreshReport(
        counts={p: len(e) for p, e in sorted(found.items())},
        uncovered=uncovered,
        added=added,
        written=path,
    )
