"""Pipeline progress: every stage is logged and, when someone listens, announced."""

from __future__ import annotations

from pytestgen.log import get_logger
from pytestgen.ports import ProgressListener, Stage

_log = get_logger(__name__)


class StageAnnouncer:
    """Callable ``(stage, message)`` shared by the use case and its collaborators."""

    def __init__(self, listener: ProgressListener | None = None) -> None:
        self._listener = listener

    @property
    def listener(self) -> ProgressListener | None:
        return self._listener

    def __call__(self, stage: Stage, message: str) -> None:
        _log.info("stage", stage=stage.value, message=message)
        if self._listener is not None:
            self._listener.stage(stage, message)
