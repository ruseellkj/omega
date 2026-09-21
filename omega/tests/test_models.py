"""The model catalog, and `/model` switching without losing the conversation.

**The trap this file is shaped around**: `/model` could set a field, report
success, and change nothing that reaches the provider. That exact shape has bitten
this project three times — a gate that checked one value and passed another, a
`source()` that reported "environment" while `resolve()` used `auth.json`, and a
start event emitted before the request. In every case a test asserting on the
*intent* passed while the behaviour was wrong.

So the switching tests assert on **the model name the provider actually
received**, never on a context field.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omega_agent.harness import Harness
from omega_ai.fake import FakeProvider, text_turn
from omega_coding import models
from omega_coding.builtin_tools import build_tools
from omega_coding.commands import CommandContext, dispatch
from omega_coding.compact import Compactor


class Recording(FakeProvider):
    """Remembers the model each request asked for. That is the whole assertion."""

    def __init__(self, turns: list[Any]) -> None:
        super().__init__(turns)
        self.models: list[str] = []

    def stream_response(self, *, model: str, **rest: Any) -> Any:
        self.models.append(model)
        return super().stream_response(model=model, **rest)


def _context(
    harness: Harness,
    *,
    provider: str = "anthropic",
    compactor: Compactor | None = None,
    window_pinned: bool = False,
    emit: Any = None,
) -> CommandContext:
    def switch(name: str) -> None:
        harness.model = name
        if compactor is not None and not window_pinned:
            compactor.window = models.window_for(name)

    return CommandContext(
        harness=harness,
        store=None,
        tracker=None,  # type: ignore[arg-type]
        model=harness.model,
        system="s",
        tools=[],
        hooks=harness.hooks,
        compactor=compactor,
        provider=provider,
        set_model=switch,
        emit=emit or (lambda _line: None),
    )


def _harness(tmp_path: Path, provider: Recording, model: str = "claude-sonnet-5") -> Harness:
    return Harness(
        provider=provider, model=model, system="s", tools=build_tools(tmp_path)
    )


# --------------------------------------------------------------- the catalog


def test_the_catalog_and_the_adapters_agree_on_every_default() -> None:
    """**Four places named a default before this existed**, and nothing checked.

    The adapters keep their own constant so each is usable standalone; the
    catalog is what `/model` and the composition root read. Two sources of truth
    are fine exactly as long as something fails when they diverge.
    """
    from omega_ai.anthropic import DEFAULT_MODEL as ANTHROPIC
    from omega_ai.openai import DEFAULT_MODEL as OPENAI
    from omega_ai.openai_codex import DEFAULT_MODEL as CODEX

    assert models.default_for("anthropic") == ANTHROPIC
    assert models.default_for("openai") == OPENAI
    assert models.default_for("openai-codex") == CODEX


def test_every_default_is_a_model_the_catalog_lists() -> None:
    """A default nobody could pick from the menu is a typo waiting to happen."""
    for provider, name in models.DEFAULTS.items():
        listed = [entry.name for entry in models.models_for(provider)]
        assert name in listed, f"{provider} defaults to {name}, which it does not list"


def test_the_longest_prefix_wins_for_windows() -> None:
    """`claude-opus-5[1m]` must not be shadowed by `claude-opus-5`.

    They differ by a factor of five, so matching the shorter prefix would budget
    a million-token conversation against 200k — compaction would fire constantly
    and throw away context nobody needed to lose.

    **The pair is written by the test, not borrowed from the catalog**, and it
    took two rewrites to get here. First it asserted on `claude-sonnet-5` (200k)
    versus `claude-sonnet-5[1m]` (1m). The models.dev audit corrected sonnet-5
    to 1,000,000, so that pair stopped differing. It was re-pointed at the opus
    pair — and the same audit then corrected opus-5 to 1,000,000 too.

    After both corrections **no pair in the shipped catalog differs at all**:
    every `[1m]` id now matches its base, because the suffix selects a route
    rather than a size. So a test that reads the catalog for its fixture is a
    test that silently becomes a tautology the next time a number is corrected.

    The overlay is the fix. Two entries, one a prefix of the other, windows
    deliberately far apart, owned by this test. It cannot rot.
    """
    _overlay(
        '{"anthropic": ['
        '{"name": "prefix-demo", "window": 200000},'
        '{"name": "prefix-demo-long", "window": 1000000}]}'
    )

    assert models.window_for("prefix-demo") == 200_000
    assert models.window_for("prefix-demo-long") == 1_000_000, (
        "the shorter entry shadowed the longer one"
    )
    # The case that matters: a name extending the *longer* entry must inherit
    # from it, not from the shorter prefix it also technically starts with.
    assert models.window_for("prefix-demo-long-20260101") == 1_000_000
    assert models.window_for("something-nobody-listed") == models.DEFAULT_CONTEXT_WINDOW


def test_context_and_the_catalog_are_the_same_table() -> None:
    """`context.py` had a second window table. Two copies is how a compactor
    ends up budgeting against a number the provider disagrees with."""
    from omega_coding import context

    assert context.window_for("claude-opus-5[1m]") == models.window_for("claude-opus-5[1m]")
    assert context.DEFAULT_CONTEXT_WINDOW == models.DEFAULT_CONTEXT_WINDOW


# ------------------------------------------------------------ switching models


async def test_the_switch_reaches_the_provider(tmp_path: Path) -> None:
    """**The assertion that matters**, and the only one that could catch a
    `/model` which sets a field and sends the old name anyway."""
    provider = Recording([text_turn("one"), text_turn("two")])
    harness = _harness(tmp_path, provider)

    async for _ in harness.run("first"):
        pass
    await dispatch("/model claude-opus-5", _context(harness))
    async for _ in harness.run("second"):
        pass

    assert provider.models == ["claude-sonnet-5", "claude-opus-5"]


async def test_the_conversation_survives_the_switch(tmp_path: Path) -> None:
    """The whole point of switching rather than restarting.

    It works because the transcript lives on the harness and the model is a
    separate attribute read at request time — nothing is rebuilt, so nothing can
    be lost. Asserted rather than assumed.
    """
    provider = Recording([text_turn("one"), text_turn("two")])
    harness = _harness(tmp_path, provider)

    async for _ in harness.run("remember this"):
        pass
    before = len(harness.messages)
    await dispatch("/model claude-opus-5", _context(harness))

    assert len(harness.messages) == before, "the switch itself must not touch history"

    async for _ in harness.run("and this"):
        pass
    assert len(harness.messages) > before
    assert any("remember this" in str(m) for m in harness.messages), "the first turn is gone"


async def test_the_compaction_window_moves_with_the_model(tmp_path: Path) -> None:
    """**The half of the switch whose failure looks unrelated.**

    Compaction budgets against a number fixed when the `Compactor` was built.
    Switch from a million-token model to a 200k one without moving it and the
    transcript is instantly over budget — but the error lands on the *next*
    request, three screens after the `/model` that caused it.
    """
    # The pair comes from an overlay this test writes, for the reason spelled
    # out in `test_the_longest_prefix_wins_for_windows`: after the models.dev
    # audit every window in the shipped anthropic catalog is 1,000,000 except
    # haiku, so borrowing a pair from it is borrowing a fixture that the next
    # correction can flatten. The guard assertion below caught exactly that
    # when this test read `claude-opus-5[1m]` and `claude-opus-5`.
    _overlay(
        '{"anthropic": ['
        '{"name": "switch-big", "window": 1000000},'
        '{"name": "switch-small", "window": 200000}]}'
    )
    big, small = "switch-big", "switch-small"
    assert models.window_for(big) != models.window_for(small), (
        "this test needs two models with different windows"
    )

    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider, model=big)
    compactor = Compactor(model=big, system="s", tools=[])
    assert compactor.window == models.window_for(big)

    await dispatch(f"/model {small}", _context(harness, compactor=compactor))

    assert compactor.window == models.window_for(small)


async def test_an_explicitly_pinned_window_is_not_overwritten(tmp_path: Path) -> None:
    """`--context-window` is someone saying they meant a smaller number."""
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    compactor = Compactor(model="claude-sonnet-5", system="s", tools=[], window=50_000)

    await dispatch(
        "/model claude-opus-5[1m]",
        _context(harness, compactor=compactor, window_pinned=True),
    )

    assert compactor.window == 50_000


async def test_another_providers_model_is_refused(tmp_path: Path) -> None:
    """Sending `gpt-5` to Anthropic is a 404 on the next turn, by which point
    the `/model` that caused it is well out of sight."""
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    said: list[str] = []

    await dispatch("/model gpt-5", _context(harness, emit=said.append))

    assert harness.model == "claude-sonnet-5", "it switched to another provider's model"
    spoken = " ".join(said)
    assert "openai" in spoken and "/login" in spoken


async def test_an_unlisted_model_is_passed_through_with_a_warning(tmp_path: Path) -> None:
    """Deliberately permissive. The catalog is a snapshot, and a hard-coded list
    that refuses a model released last week is worse than no list at all."""
    provider = Recording([text_turn("one"), text_turn("two")])
    harness = _harness(tmp_path, provider)
    said: list[str] = []

    await dispatch("/model claude-sonnet-9-unreleased", _context(harness, emit=said.append))
    async for _ in harness.run("hi"):
        pass

    assert provider.models[-1] == "claude-sonnet-9-unreleased", "it was not passed through"
    assert "Not in omega's list" in " ".join(said), "it switched without saying so"


async def test_switching_to_the_current_model_says_so(tmp_path: Path) -> None:
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    said: list[str] = []

    await dispatch("/model claude-sonnet-5", _context(harness, emit=said.append))

    assert "Already on" in " ".join(said)


async def test_a_second_switch_compares_against_the_live_model(tmp_path: Path) -> None:
    """`CommandContext` is frozen and built once, so its `model` is the value
    omega *started* with. A `/model` reading that would tell you "already on
    claude-sonnet-5" after you had switched away from it."""
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    said: list[str] = []
    # **The same context object both times.** Rebuilding it between switches
    # would refresh the snapshot and hide exactly the bug this guards, which is
    # what the first version of this test did — and it passed against code
    # reading the stale field.
    context = _context(harness, emit=said.append)

    await dispatch("/model claude-opus-5", context)
    await dispatch("/model claude-opus-5", context)

    assert context.model == "claude-sonnet-5", "precondition: the snapshot is stale"
    assert harness.model == "claude-opus-5", "the live value moved"
    assert "Already on" in " ".join(said), "it compared against the startup model"


async def test_with_no_argument_it_lists_what_is_available(tmp_path: Path) -> None:
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    said: list[str] = []

    await dispatch("/model", _context(harness, emit=said.append))

    spoken = " ".join(said)
    assert "claude-opus-5" in spoken
    assert "* claude-sonnet-5" in spoken, "the current model is not marked"


async def test_with_a_picker_it_asks(tmp_path: Path) -> None:
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    asked: list[tuple[str, list[str]]] = []

    async def choose(prompt: str, options: list[str]) -> str:
        asked.append((prompt, options))
        return options[2]

    context = _context(harness)
    context = CommandContext(
        **{
            **{f.name: getattr(context, f.name) for f in context.__dataclass_fields__.values()},
            "ask_choice": choose,
        }
    )
    await dispatch("/model", context)

    assert asked, "no picker was opened"
    assert harness.model == "claude-opus-5", "the picked model was not applied"


# ------------------------------------------------- the user overlay, layer two
#
# Everything below is about one question: *a model shipped today and omega's
# list predates it — am I stuck?* The answer is `~/.omega/models.json`, and the
# shape is Tau's (`catalog_loader.py:229`): a **union**, the user's entries
# first, the built-ins kept.
#
# `conftest.py` points `models.overlay_path()` at `tmp_path` for every test in
# the suite, so `_overlay(...)` below writes to a temporary file and "no
# overlay" is the default state.


def _overlay(text: str) -> None:
    """Write the overlay file the fixture redirected `overlay_path()` to."""
    models.overlay_path().write_text(text, encoding="utf-8")


def test_with_no_overlay_the_builtins_stand_alone() -> None:
    """Absent is the normal state, and it is not a complaint.

    Guards against the natural-looking mistake of treating a missing file as an
    error — `oauth.py:179` documents the same call for the same reason.
    """
    assert not models.overlay_path().exists()
    assert models.catalog() == dict(models.BUILTIN)
    assert models.overlay_problem() is None


def test_an_added_model_is_offered_and_the_builtins_survive() -> None:
    """The union, which is the whole point.

    "Your models go on top of the built-in ones, not instead of them" has to be
    *true*, not merely claimed: adding one Claude must leave the other five
    selectable.
    """
    _overlay('{"anthropic": [{"name": "claude-opus-6", "window": 900000}]}')

    names = [entry.name for entry in models.models_for("anthropic")]
    assert names[0] == "claude-opus-6", "the user's entry should come first"
    for built_in in models.BUILTIN["anthropic"]:
        assert built_in.name in names, f"{built_in.name} was dropped by the overlay"
    assert [entry.name for entry in models.models_for("openai")] == [
        entry.name for entry in models.BUILTIN["openai"]
    ], "an anthropic overlay must not touch another provider"


def test_an_overlay_entry_replaces_a_builtin_of_the_same_name() -> None:
    """Same name, once, with the user's figure.

    The correction case: omega ships a window that turned out to be wrong, and
    the user fixes it without waiting for a release. A union that appended
    blindly would list the model twice and let prefix matching pick either one.
    """
    _overlay('{"anthropic": [{"name": "claude-opus-5", "window": 500000}]}')

    matches = [e for e in models.models_for("anthropic") if e.name == "claude-opus-5"]
    assert len(matches) == 1, "the model is listed twice"
    assert matches[0].window == 500_000
    assert models.window_for("claude-opus-5") == 500_000


def test_the_overlay_window_is_what_compaction_budgets_against() -> None:
    """**The reason this file exists at all.**

    Being able to *type* an unknown model already worked. What did not is being
    believed about its size: a million-token model budgeted at the 200k fallback
    compacts at a fifth of its capacity, throws away context nobody needed to
    lose, and prints nothing that explains why.
    """
    assert models.window_for("mystery-model-1") == models.DEFAULT_CONTEXT_WINDOW
    assert models.window_is_guess("mystery-model-1")

    _overlay('{"anthropic": [{"name": "mystery-model-1", "window": 1000000}]}')

    assert models.window_for("mystery-model-1") == 1_000_000
    assert not models.window_is_guess("mystery-model-1")


def test_window_is_guess_is_false_for_a_prefix_match() -> None:
    """A dated variant is known, not guessed.

    `claude-sonnet-5-20260101` has no entry of its own and must still count as
    known, or `/model` would tell a user to correct a window that is already
    right.
    """
    assert not models.window_is_guess("claude-sonnet-5-20260101")
    # Equal to its parent entry, not to a number typed here. Same lesson as the
    # switch test: a literal makes this fail when the catalog is corrected,
    # which reads as "prefix matching broke" when nothing of the sort happened.
    assert models.window_for("claude-sonnet-5-20260101") == models.window_for(
        "claude-sonnet-5"
    )


def test_an_overlay_model_resolves_to_its_provider() -> None:
    """So the cross-provider refusal covers custom models too.

    Without this, `/model` would let an overlay-added OpenAI model be selected
    while signed in to Anthropic — a 404 on the next turn, three screens after
    the command that caused it.
    """
    _overlay('{"openai": [{"name": "gpt-6", "window": 400000}]}')
    assert models.provider_of("gpt-6") == "openai"


def test_an_edit_takes_effect_without_a_restart() -> None:
    """No caching, and that is a decision rather than an oversight.

    Tau caches only its *built-in* catalog (`@cache` on `_builtin_raw`,
    `catalog_loader.py:176`) and re-reads the overlay every call. Same here: the
    file is the one part a human edits, so caching it would mean the fix does
    not land until omega is restarted, which is exactly when they would conclude
    the file does nothing.
    """
    _overlay('{"anthropic": [{"name": "shifting", "window": 300000}]}')
    assert models.window_for("shifting") == 300_000

    _overlay('{"anthropic": [{"name": "shifting", "window": 700000}]}')
    assert models.window_for("shifting") == 700_000, "the first read was cached"


# ------------------------------------------------- a broken overlay, reported


def test_invalid_json_is_reported_and_the_builtins_still_load() -> None:
    """Neither of the two easy answers.

    Tau raises (`catalog_loader.py:185`), which would stop omega starting over a
    convenience file. `oauth.py:194` returns `{}` in silence, which leaves the
    user staring at a list their file should have changed. This reports.
    """
    _overlay('{"anthropic": [{"name": "half-written"')

    assert models.catalog() == dict(models.BUILTIN), "a bad file cost the built-ins"
    problem = models.overlay_problem()
    assert problem is not None, "a broken file was ignored silently"
    assert "not valid JSON" in problem
    assert str(models.overlay_path()) in problem, "the message must name the file"


def test_a_top_level_list_is_reported() -> None:
    """The likeliest format mistake: writing the models without their provider."""
    _overlay('[{"name": "claude-opus-6", "window": 900000}]')

    problem = models.overlay_problem()
    assert problem is not None
    assert "keyed by provider" in problem, "the message should say what was expected"


def test_an_unknown_provider_is_reported_rather_than_honoured() -> None:
    """**Models, not providers** — the one place omega departs from Tau.

    Tau's overlay can introduce a provider because its catalog carries the base
    URL and wire-format flags. omega's providers are adapters in code, so a key
    it does not know would fill the picker with models nothing can send a
    request to.
    """
    _overlay('{"my-provider": [{"name": "anything", "window": 100000}]}')

    assert models.models_for("my-provider") == (), "an unroutable provider was offered"
    problem = models.overlay_problem()
    assert problem is not None
    assert "my-provider" in problem, "the message must name the offending key"
    assert "anthropic" in problem, "and list what is actually available"


def test_a_true_window_is_rejected_not_read_as_one_token() -> None:
    """`isinstance(True, int)` is True in Python.

    So `"window": true` would sail through an `isinstance(window, int)` check
    and become a **one-token** window — compaction on every single turn, from a
    typo, with the file looking accepted.
    """
    _overlay('{"anthropic": [{"name": "boolish", "window": true}]}')

    assert models.window_for("boolish") == models.DEFAULT_CONTEXT_WINDOW
    problem = models.overlay_problem()
    assert problem is not None
    assert "boolish" in problem


def test_a_zero_or_negative_window_is_rejected() -> None:
    """A zero window divides by zero in the usage fraction; a negative one is
    meaningless. Neither is a number to carry into the compactor."""
    _overlay('{"anthropic": [{"name": "zeroed", "window": 0}]}')
    assert models.window_for("zeroed") == models.DEFAULT_CONTEXT_WINDOW
    assert "greater than zero" in (models.overlay_problem() or "")

    _overlay('{"anthropic": [{"name": "negative", "window": -5}]}')
    assert models.window_for("negative") == models.DEFAULT_CONTEXT_WINDOW


def test_a_missing_name_is_rejected() -> None:
    """An entry with no name cannot be selected, so it is not a model."""
    _overlay('{"anthropic": [{"window": 900000}]}')
    assert "name is required" in (models.overlay_problem() or "")


def test_one_bad_entry_does_not_hide_the_good_ones() -> None:
    """`oauth.py:213` makes the same call, and this one also names the entry.

    A file with three models and one typo should install two models and a
    complaint — not nothing, which would make the typo look like a file that
    was never read.
    """
    _overlay(
        '{"anthropic": ['
        '{"name": "good-one", "window": 900000},'
        '{"name": "bad-one", "window": "lots"},'
        '{"name": "good-two", "window": 800000}]}'
    )

    names = [entry.name for entry in models.models_for("anthropic")]
    assert "good-one" in names and "good-two" in names, "good entries were lost"
    assert "bad-one" not in names
    problem = models.overlay_problem()
    assert problem is not None
    assert "anthropic[1]" in problem, "the message should locate the bad entry"


# ------------------------------------------- what `/model` says about all this


async def test_model_admits_when_it_is_guessing_the_window(tmp_path: Path) -> None:
    """The dishonesty this replaced.

    `/model something-new` used to print `Context window: 200,000 tokens.` flat,
    which is a fallback wearing the clothes of a fact. Now it says it is
    assuming, and gives the JSON that fixes it.
    """
    lines: list[str] = []
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    await dispatch(
        "/model claude-opus-6",
        _context(harness, emit=lines.append),
    )
    said = "\n".join(lines)

    assert "does not know this model's window" in said
    assert "assuming" in said
    assert str(models.overlay_path()) in said, "it should say where to fix it"
    assert '"window"' in said, "and show the shape, not just the path"
    # The shown figure must be a placeholder, never the guess. Printing the
    # fallback here invites pasting it straight back, which silences the warning
    # while leaving the budget just as wrong and nothing left to reveal it.
    assert '"window": <tokens>' in said
    assert '"window": 200000' not in said and '"window": 200,000' not in said


async def test_model_states_the_window_plainly_when_it_knows(tmp_path: Path) -> None:
    """The other half: a known model must not be hedged about."""
    lines: list[str] = []
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    await dispatch("/model claude-opus-5[1m]", _context(harness, emit=lines.append))
    said = "\n".join(lines)

    assert "Context window: 1,000,000 tokens" in said
    assert "assuming" not in said, "a known window was reported as a guess"


async def test_the_model_list_reports_a_broken_overlay(tmp_path: Path) -> None:
    """Where the complaint has to surface.

    A message nothing prints is the silent failure with extra steps, and `/model`
    with no argument is exactly the moment someone is looking for the model they
    just added.
    """
    _overlay("{ not json")
    lines: list[str] = []
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    await dispatch("/model", _context(harness, emit=lines.append))
    said = "\n".join(lines)

    assert "Ignored part of your model list" in said
    assert "not valid JSON" in said


def test_a_compactor_built_at_startup_honours_the_overlay() -> None:
    """**The path users hit first, and the one the switch tests miss.**

    "A model shipped today" usually means *starting* omega on it, not switching
    to it mid-session — and that is different code: `cli.py:681` builds
    `Compactor(model=..., system=..., tools=...)` with no explicit window, so
    `Compactor.__init__` (`compact.py:221`) calls `window_for` itself rather
    than going anywhere near `set_model`.

    Every other test here drives `/model`. All of them would still pass if
    startup read `BUILTIN` directly and the overlay only took effect after a
    switch — which is the shape this asserts against.
    """
    _overlay('{"anthropic": [{"name": "claude-opus-6", "window": 1000000}]}')

    # Constructed exactly as the composition root does it.
    compactor = Compactor(model="claude-opus-6", system="s", tools=[])
    assert compactor.window == 1_000_000, "startup ignored the overlay"

    # The control: a name nothing lists still gets the conservative fallback,
    # so the overlay is additive rather than a blanket override.
    assert (
        Compactor(model="never-heard-of-it", system="s", tools=[]).window
        == models.DEFAULT_CONTEXT_WINDOW
    )


async def test_context_reports_the_window_of_the_model_now_in_use(tmp_path: Path) -> None:
    """**A red test for a real bug**, found while explaining the feature.

    `_context` read `context.model` — the snapshot frozen when the command
    context was built at startup — so after `/model claude-opus-5[1m]` it went
    on reporting the window of the model omega *started* with. Measured:

        live model      : claude-opus-6
        compactor budget: 1,000,000
        /context says   : ~0/200,000 tokens (0%)

    Which is the worst possible shape for this: the number on screen is the one
    the user checks to decide whether to compact, and it disagreed with the
    budget compaction was actually using. `_model` already had this exact bug
    fixed at `commands.py:637`; `_context` was the instance nobody had looked
    at.
    """
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    compactor = Compactor(model=harness.model, system="s", tools=[])
    lines: list[str] = []

    # `model=` stays at the startup value, which is how both frontends build it.
    def context_for(args: str) -> CommandContext:
        base = _context(harness, compactor=compactor, emit=lines.append)
        return CommandContext(
            **{
                **{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()},
                "model": "claude-sonnet-5",
                "args": args,
            }
        )

    await dispatch("/model claude-opus-5[1m]", context_for("claude-opus-5[1m]"))
    assert harness.model == "claude-opus-5[1m]"
    assert compactor.window == 1_000_000, "the compactor should have moved"

    lines.clear()
    await dispatch("/context", context_for(""))
    said = " ".join(lines)

    assert "1,000,000" in said, f"/context reported a stale window: {said!r}"
    assert "200,000" not in said, f"/context still shows the old window: {said!r}"


# ------------------------------------- the breakdown, and the window's source


def test_the_total_is_the_sum_of_its_parts() -> None:
    """The invariant that makes a breakdown trustworthy.

    `estimated_tokens` is a property over the three fields precisely so this
    cannot fail — but a later refactor that makes it a stored field again would
    reintroduce the drift, and this is what would notice.
    """
    from omega_coding.context import ContextUsage

    usage = ContextUsage(
        system_tokens=1_000, message_tokens=2_000, tool_tokens=500, window=200_000
    )
    assert usage.estimated_tokens == 3_500
    assert usage.message_tokens + usage.tool_tokens + usage.system_tokens == 3_500


def test_the_breakdown_measures_the_same_request_as_the_total() -> None:
    """Three numbers that add up to the one printed above them.

    The failure this rules out is subtle and was live in the old code's shape:
    `estimate_request_tokens` walked system, messages and tools in one pass and
    returned a sum, so any second function computing the parts separately could
    disagree with it. `estimate_parts` is now the single walk and the total is
    its sum, so a breakdown that does not reconcile is impossible rather than
    merely unlikely.
    """
    from omega_agent.types import UserMessage
    from omega_coding.builtin_tools import build_tools
    from omega_coding.context import estimate_request_tokens, measure

    tools = build_tools(Path.cwd())
    messages = [UserMessage(content="a question long enough to count")]
    usage = measure(
        model="claude-sonnet-5", system="a system prompt", messages=messages, tools=tools
    )

    assert usage.tool_tokens > 0, "tool schemas must be counted - see context.py's docstring"
    assert usage.message_tokens > 0
    assert usage.system_tokens > 0
    assert (
        usage.estimated_tokens
        == estimate_request_tokens(
            system="a system prompt", messages=messages, tools=tools
        )
    ), "the breakdown and the standalone total disagree"


def test_the_breakdown_is_ordered_widest_first() -> None:
    """Because the reason to read it is to find what to trim."""
    from omega_coding.context import ContextUsage

    # **system is deliberately the largest here.** `parts()` builds its rows in
    # the order messages, tools, system — so a fixture where messages happens to
    # be biggest passes whether the sort runs or not. Break-testing caught
    # exactly that: deleting the `rows.sort` line failed nothing at all. This
    # fixture needs the sort to reorder, so removing it fails.
    usage = ContextUsage(
        system_tokens=9_000, message_tokens=5_000, tool_tokens=900, window=200_000
    )
    names = [line.split()[0] for line in usage.parts()]
    assert names == ["system", "messages", "tools", "free"], names


def test_a_known_window_carries_no_note_and_a_fallback_does() -> None:
    """**The second half of "never guess".**

    `~0/200,000 tokens` renders identically whether 200,000 is the provider's
    real figure or omega's stand-in. `window_is_guess` has known the difference
    since the overlay landed; nothing displayed it, so the distinction existed
    in the code and not on the screen.
    """
    from omega_coding.context import measure

    known = measure(model="claude-sonnet-5", system="s", messages=[], tools=[])
    assert known.window == 1_000_000
    assert known.window_is_known
    assert known.window_note() is None, "a real figure needs no explanation"

    unknown = measure(model="not-a-model-anyone-listed", system="s", messages=[], tools=[])
    assert unknown.window == models.DEFAULT_CONTEXT_WINDOW
    assert not unknown.window_is_known
    note = unknown.window_note()
    assert note is not None
    assert "fallback" in note
    assert "models.json" in note, "it should say where to fix it"


def test_the_overlay_turns_the_fallback_note_off() -> None:
    """Supplying the figure must actually retire the warning.

    Otherwise the note becomes noise that appears forever regardless of what
    the user does, which trains people to ignore it.
    """
    from omega_coding.context import measure

    before = measure(model="brand-new-model", system="s", messages=[], tools=[])
    assert before.window_note() is not None

    _overlay('{"anthropic": [{"name": "brand-new-model", "window": 700000}]}')

    after = measure(model="brand-new-model", system="s", messages=[], tools=[])
    assert after.window == 700_000
    assert after.window_note() is None, "the note survived the user fixing it"


async def test_context_prints_the_breakdown_and_the_note(tmp_path: Path) -> None:
    """End to end through the command, which is where a user meets it."""
    lines: list[str] = []
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider, model="unlisted-model-xyz")
    await dispatch("/context", _context(harness, emit=lines.append))
    said = "\n".join(lines)

    assert "messages" in said and "tools" in said and "system" in said
    assert "free" in said, "free space is the number people actually look for"
    assert "fallback" in said, "an assumed window must say so here too"

    lines.clear()
    harness.model = "claude-sonnet-5"
    await dispatch("/context", _context(harness, emit=lines.append))
    said = "\n".join(lines)
    assert "1,000,000" in said
    assert "fallback" not in said, "a known window was labelled a guess"


# ------------------------------------------ refreshing the list from models.dev
#
# The extraction is pure, so almost everything below runs against a fixture
# shaped like the real payload. Only the last two tests touch a transport, and
# they use `httpx.MockTransport` rather than the network.


def _cache(text: str) -> None:
    """Write the models.dev cache layer."""
    models.cache_path().write_text(text, encoding="utf-8")


#: Trimmed from the real `https://models.dev/api.json` — same nesting, same
#: field names, four models instead of thousands. Copied rather than invented so
#: a schema this does not actually match cannot pass.
MODELS_DEV_FIXTURE: dict[str, object] = {
    "anthropic": {
        "id": "anthropic",
        "models": {
            "claude-opus-5": {
                "id": "claude-opus-5",
                "limit": {"context": 1000000, "output": 128000},
            },
            "claude-brand-new": {"id": "claude-brand-new", "limit": {"context": 2000000}},
            "claude-no-limit-block": {"id": "claude-no-limit-block"},
        },
    },
    "openai": {
        "id": "openai",
        "models": {"gpt-5": {"id": "gpt-5", "limit": {"context": 400000}}},
    },
    "some-other-vendor": {"id": "some-other-vendor", "models": {"x": {"limit": {"context": 1}}}},
}


def test_extraction_reads_windows_out_of_a_models_dev_payload() -> None:
    """The pure half, which is where all the logic lives."""
    found, uncovered = models.models_from_models_dev(MODELS_DEV_FIXTURE)

    windows = {e.name: e.window for e in found["anthropic"]}
    assert windows["claude-opus-5"] == 1_000_000
    assert windows["claude-brand-new"] == 2_000_000
    assert {e.name: e.window for e in found["openai"]} == {"gpt-5": 400_000}
    assert "some-other-vendor" not in found, "only providers omega has an adapter for"


def test_a_model_with_no_window_is_skipped_not_defaulted() -> None:
    """**models.dev not knowing is information, not a blank to fill.**

    `claude-no-limit-block` has no `limit` at all. Giving it
    `DEFAULT_CONTEXT_WINDOW` would make `window_is_guess` report False for a
    figure nobody stated — a guess laundered into a fact by passing through a
    refresh, which is precisely what this whole feature exists to stop.
    """
    found, _ = models.models_from_models_dev(MODELS_DEV_FIXTURE)
    names = [e.name for e in found["anthropic"]]
    assert "claude-no-limit-block" not in names


def test_providers_models_dev_cannot_cover_are_named() -> None:
    """`openai-codex` has no models.dev counterpart, and silence would hide it.

    A refresh that prints two providers and says nothing about the third reads
    as "all done". Tau reads the real Codex list from `/codex/models` at runtime
    (`openai_codex.py:975-1010`); omega does not, so the honest move is to say
    the row was not covered.
    """
    _, uncovered = models.models_from_models_dev(MODELS_DEV_FIXTURE)
    assert "openai-codex" in uncovered


def test_junk_payloads_produce_no_models_rather_than_an_exception() -> None:
    """A schema change upstream must not be a traceback."""
    for payload in (None, [], "nope", 42, {"anthropic": "not-a-dict"}, {"anthropic": {}}):
        found, uncovered = models.models_from_models_dev(payload)
        assert found == {}, payload
        assert uncovered, "and it should say it covered nothing"


def test_the_cache_layer_sits_above_the_builtins() -> None:
    """A refresh teaches omega models that were never compiled in."""
    assert models.window_is_guess("claude-brand-new")

    _cache('{"anthropic": [{"name": "claude-brand-new", "window": 2000000}]}')

    assert models.window_for("claude-brand-new") == 2_000_000
    assert not models.window_is_guess("claude-brand-new")
    # and it did not cost the built-ins
    assert models.window_for("claude-haiku-4-5-20251001") == 200_000


def test_a_hand_written_figure_beats_a_refresh() -> None:
    """**The precedence that would otherwise silently destroy someone's work.**

    Both files hold the same shape and can name the same model. If the cache won,
    the next `/model refresh` would quietly undo a correction the user typed
    deliberately — and nothing on screen would say it had happened.
    """
    _cache('{"anthropic": [{"name": "contested", "window": 1000000}]}')
    assert models.window_for("contested") == 1_000_000

    _overlay('{"anthropic": [{"name": "contested", "window": 12345}]}')

    assert models.window_for("contested") == 12_345, "the refresh overrode a hand-written figure"
    listed = [e for e in models.models_for("anthropic") if e.name == "contested"]
    assert len(listed) == 1, "the same model is listed twice"
    assert listed[0].window == 12_345


def test_a_broken_cache_does_not_take_the_overlay_down_with_it() -> None:
    """Three layers means a bad one must not poison the other two."""
    _cache("{ this is not json")
    _overlay('{"anthropic": [{"name": "still-here", "window": 700000}]}')

    assert models.window_for("still-here") == 700_000
    assert models.window_for("claude-haiku-4-5-20251001") == 200_000
    problem = models.overlay_problem()
    assert problem is not None and "not valid JSON" in problem


async def test_refresh_reports_a_failure_instead_of_raising() -> None:
    """Offline is the common case and it must read as a sentence."""
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    real = httpx.AsyncClient

    class Offline(real):  # type: ignore[misc,valid-type]
        def __init__(self, **kw: object) -> None:
            super().__init__(transport=httpx.MockTransport(refuse))

    httpx.AsyncClient = Offline  # type: ignore[misc]
    try:
        report = await models.refresh_from_models_dev()
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]

    assert report.error is not None
    assert "models.dev" in report.error
    assert report.written is None, "nothing may be written when the fetch failed"
    assert not models.cache_path().exists()


async def test_refresh_writes_a_cache_the_catalog_then_reads() -> None:
    """The round trip: fetch, write, and the next lookup sees it.

    Asserting on `window_for` rather than on the report is the point — a report
    saying "43 models" while the catalog knows none of them is exactly the shape
    of bug this project keeps finding.
    """
    import json as _json

    import httpx

    def serve(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == models.MODELS_DEV_URL
        return httpx.Response(200, content=_json.dumps(MODELS_DEV_FIXTURE))

    real = httpx.AsyncClient

    class Served(real):  # type: ignore[misc,valid-type]
        def __init__(self, **kw: object) -> None:
            super().__init__(transport=httpx.MockTransport(serve))

    httpx.AsyncClient = Served  # type: ignore[misc]
    try:
        report = await models.refresh_from_models_dev()
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]

    assert report.error is None
    assert report.counts == {"anthropic": 2, "openai": 1}
    assert "claude-brand-new" in report.added
    assert "openai-codex" in report.uncovered
    assert report.written == models.cache_path()

    assert models.window_for("claude-brand-new") == 2_000_000
    assert not models.window_is_guess("claude-brand-new")


def test_every_home_path_in_models_is_isolated() -> None:
    """**The guard that replaces remembering.**

    `conftest.py` redirects the functions in `models` that reach `Path.home()`
    so the suite never writes where a user keeps state. That worked until
    `cache_path` was added and the fixture was not extended — at which point the
    refresh tests wrote into the real `~/.omega/models-cache.json` and nothing
    complained, because a test that pollutes a home directory still passes.

    So the fixture's coverage is checked against the module's own source rather
    than against anyone's memory. Add a third home-reaching function and this
    fails naming it, which is the only version of this rule that holds.
    """
    import ast
    import inspect

    from conftest import ISOLATED_MODEL_PATHS  # the suite's own conftest

    tree = ast.parse(inspect.getsource(models))
    reaches_home: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            # matches `Path.home()`
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "home"
            ):
                reaches_home.add(node.name)

    assert reaches_home, "the AST walk found nothing — it has stopped matching"
    missing = reaches_home - set(ISOLATED_MODEL_PATHS)
    assert not missing, (
        f"these read Path.home() but conftest does not redirect them: {sorted(missing)}."
        " Add them to ISOLATED_MODEL_PATHS and to _isolate_model_overlay."
    )
    stale = set(ISOLATED_MODEL_PATHS) - reaches_home
    assert not stale, f"ISOLATED_MODEL_PATHS names functions that no longer exist: {sorted(stale)}"


async def test_model_refresh_routes_to_the_refresh_and_not_to_a_switch(tmp_path: Path) -> None:
    """**The routing, which nothing checked until break-testing said so.**

    `/model` is permissive by design: an unrecognised name is passed through to
    the provider with a warning. That makes a broken `refresh` branch silently
    destructive rather than loudly broken — omega would switch the session to a
    model literally called "refresh" and report success. Deleting the branch
    failed no test at all until this one existed.
    """
    import json as _json

    import httpx

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_json.dumps(MODELS_DEV_FIXTURE))

    real = httpx.AsyncClient

    class Served(real):  # type: ignore[misc,valid-type]
        def __init__(self, **kw: object) -> None:
            super().__init__(transport=httpx.MockTransport(serve))

    lines: list[str] = []
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)
    before = harness.model

    httpx.AsyncClient = Served  # type: ignore[misc]
    try:
        await dispatch("/model refresh", _context(harness, emit=lines.append))
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]

    said = "\n".join(lines)
    assert harness.model == before, "refresh switched the model instead of refreshing"
    assert "models.dev" in said
    assert "anthropic" in said, "it should report what it read"
    assert models.cache_path().exists(), "and it should have written the cache"


async def test_a_failed_refresh_says_the_builtins_are_intact(tmp_path: Path) -> None:
    """Because the first thing you wonder after an error is what it broke."""
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    real = httpx.AsyncClient

    class Offline(real):  # type: ignore[misc,valid-type]
        def __init__(self, **kw: object) -> None:
            super().__init__(transport=httpx.MockTransport(refuse))

    lines: list[str] = []
    provider = Recording([text_turn("one")])
    harness = _harness(tmp_path, provider)

    httpx.AsyncClient = Offline  # type: ignore[misc]
    try:
        await dispatch("/model refresh", _context(harness, emit=lines.append))
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]

    said = "\n".join(lines)
    assert "could not reach models.dev" in said
    assert "untouched" in said, "say what survived, not just what failed"
    assert models.window_for("claude-sonnet-5") == 1_000_000, "the built-ins must still work"
