"""The terminal UI — Tier 3's "and has a face".

Three modules, split the way Tau splits its: `state.py` holds what the screen
should show, `adapter.py` turns agent events into state changes, `app.py` draws
it. Nothing in the first two imports Textual, which is what lets every
behavioural test run without a terminal.
"""

from omega_coding.tui.adapter import TuiEventAdapter
from omega_coding.tui.app import OmegaApp, run_tui
from omega_coding.tui.state import Row, TuiState

__all__ = ["OmegaApp", "Row", "TuiEventAdapter", "TuiState", "run_tui"]
