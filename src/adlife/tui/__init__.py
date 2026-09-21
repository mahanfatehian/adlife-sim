"""The live Textual dashboard as a read-only adapter over committed events.

Nothing in this package mutates engine state: :class:`~adlife.tui.event_bus.TuiEventBus`
folds events the runner has already persisted, :class:`~adlife.tui.controller.RunController`
exposes the pause/step/speed/stop controls, and :class:`~adlife.tui.app.AdLifeTui`
renders the four-panel view. The core never imports this package.
"""

from __future__ import annotations
