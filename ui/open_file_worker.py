"""원문 열기(딥링크 포함)를 백그라운드에서 수행 (T10.1).

Office COM 자동화는 1~3초(프로세스 기동 포함) 걸릴 수 있어 메인 스레드를
막으면 안 된다 — `SearchWorker`와 같은 최소 구조.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from search.office_link import OpenPlan


class OpenFileWorker(QThread):
    failed = Signal(str)

    def __init__(self, file_path: str, plan: OpenPlan | None, parent=None) -> None:
        super().__init__(parent)
        self._file_path = file_path
        self._plan = plan

    def run(self) -> None:
        # 지연 import — 카드 쪽에서 이미 `card_common.open_source_file`을
        # 임포트하고 있어 순환 임포트를 피한다.
        from ui.widgets.card_common import open_source_file

        error = open_source_file(self._file_path, self._plan)
        if error:
            self.failed.emit(error)
