""""원문 열기" 딥링크 — Office COM으로 정확한 위치로 이동 (T10.1).

docx/pptx/xlsx만 다룬다(hwp/hwpx는 이 PC에 한글이 없어 실측 검증이
불가능해 이번 범위에서 제외 — 사용자 확정). pywin32 없이 PowerShell
서브프로세스로 Word/PowerPoint/Excel COM을 구동한다(2026-08-07 실측
확인, LibreOffice 변환과 같은 "관리자 권한 불필요" 원칙과 결이 같다).

**순수 점진적 개선이다.** COM이 없거나 실패해도 오늘의 "그냥 열기"가
그대로 보장된다 — 이 모듈은 실패하면 예외를 던지기만 하고, 실제 폴백
판단은 `ui/widgets/card_common.py`가 한다.

이 모듈은 Qt에 묶이지 않는다(`search/chunk_view.py`와 같은 계층 — sLM
등 다른 곳에서도 재사용 가능하도록).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from parser.schema import ChunkType
from search.chunk_view import parse_image_data, parse_table_data

DEFAULT_TIMEOUT_SEC = 15  # Word 프로세스 기동 ~1초 + 검색 ~1초(실측) 대비 여유

# needle ladder — 점점 짧게 재시도한다. Word 단락 구분자(\r)가 청커의 "\n"
# 이어붙이기와 달라, 청크 전체 텍스트를 그대로 검색하면 실패한다.
_NEEDLE_LADDER_SIZES = (180, 90, 40, 20)

_PROGID_BY_EXT = {
    ".docx": "Word.Application",
    ".pptx": "PowerPoint.Application",
    ".xlsx": "Excel.Application",
}


class OfficeComError(Exception):
    """Office COM 딥링크 계열 예외의 기반."""


class OfficeAutomationTimeoutError(OfficeComError):
    """제한 시간 내에 끝나지 않음."""


class OfficeAutomationFailedError(OfficeComError):
    """PowerShell/COM 실행이 비정상 종료하거나 오류를 보고함."""


@dataclass
class OpenPlan:
    """어디로 이동할지에 대한 계획. 전부 비어 있으면 "그냥 열기"만 한다."""

    page_or_slide: int | None = None
    needles: list[str] = field(default_factory=list)
    sheet_name: str | None = None

    def is_empty(self) -> bool:
        return self.page_or_slide is None and not self.needles and self.sheet_name is None


def _build_needle_ladder(text: str, sizes: tuple[int, ...] = _NEEDLE_LADDER_SIZES) -> list[str]:
    """길이를 점점 줄여가는 검색어 후보 목록을 만든다.

    🔴 **문단 경계를 반드시 지켜야 한다.** 청커가 여러 Word 문단을 `"\n"`으로
    이어붙여 하나의 청크 `content`로 만드는데, Word의 문단 구분자는 `\r`이라
    `\n`이 낀 문자열은 Word 안에 그 형태로 존재하지 않는다 — 실측 확인(2026
    실사용 검증): 문단 경계를 무시하고 전체를 글자 수로만 자르면 20자짜리도
    실패했다. 그래서 문단 단위로 먼저 쪼갠 뒤, **문단 안에서만** 사다리를
    만들고, 첫 문단에서 못 찾을 경우를 대비해 다음 문단의 사다리도 이어붙인다.
    """
    seen: set[str] = set()
    ladder: list[str] = []
    for paragraph in text.split("\n"):
        cleaned = paragraph.strip()
        if not cleaned:
            continue

        candidates = [cleaned[:n] for n in sizes if len(cleaned) > n]
        candidates.append(cleaned if len(cleaned) <= sizes[-1] else cleaned[: sizes[-1]])

        for candidate in candidates:
            candidate = candidate.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                ladder.append(candidate)
    return ladder


def _longest_cell(rows: list[list[str]], header_row: list[str]) -> str | None:
    """표 안에서 가장 긴 셀 값을 돌려준다 — 셀 하나만 검색하면 되므로 구분자 불일치 문제가 없다."""
    best = ""
    for row in (header_row, *rows):
        for cell in row:
            cell = cell.strip()
            if len(cell) > len(best):
                best = cell
    return best or None


def plan_open(hybrid_result) -> OpenPlan:
    """청크 정보로 Office COM 딥링크 계획을 만든다.

    사용자가 입력한 검색어가 아니라 **그 청크 자신의 내용**을 검색어로
    쓴다 — 질의어는 문서 안 여러 곳에 걸릴 수 있지만, 청크 내용은 정확히
    그 위치를 가리킨다.
    """
    result = hybrid_result.result
    ext = Path(result.file_path).suffix.lower()

    if ext == ".pptx":
        # 텍스트/표/이미지 청크 전부 정확한 slide_index를 갖고 있다(pptx_parser.py) —
        # 텍스트 검색 없이 슬라이드 이동만으로 충분하다.
        return OpenPlan(page_or_slide=hybrid_result.page_or_slide)

    if ext == ".xlsx" and hybrid_result.type is ChunkType.TABLE:
        table = parse_table_data(result)
        if table is None:
            return OpenPlan()
        needle = _longest_cell(table.rows, table.header_row)
        return OpenPlan(sheet_name=table.caption or None, needles=[needle] if needle else [])

    if ext == ".docx":
        if hybrid_result.type is ChunkType.TEXT:
            return OpenPlan(needles=_build_needle_ladder(hybrid_result.content))
        if hybrid_result.type is ChunkType.TABLE:
            table = parse_table_data(result)
            if table is None:
                return OpenPlan()
            needle = _longest_cell(table.rows, table.header_row)
            return OpenPlan(needles=[needle] if needle else [])
        if hybrid_result.type is ChunkType.IMAGE:
            # docx 이미지 청크는 page_or_slide가 없다(Phase 1부터의 제약) —
            # 캡션이 있으면 성공률 낮은 보너스로 그것만 검색한다.
            image = parse_image_data(result)
            if image and image.caption:
                return OpenPlan(needles=_build_needle_ladder(image.caption))
            return OpenPlan()

    return OpenPlan()


def is_office_available(ext: str) -> bool:
    """확장자에 대응하는 Office 앱이 이 PC에 설치돼 있는지(COM ProgID 등록 여부)."""
    prog_id = _PROGID_BY_EXT.get(ext.lower())
    if prog_id is None:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id):
            return True
    except OSError:
        return False


def _write_temp_script(content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as fp:
        fp.write(content)
        return Path(fp.name)


def open_and_locate(file_path: str, plan: OpenPlan, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
    """COM으로 문서를 열고 계획된 위치로 이동한다. 실패하면 예외를 던진다."""
    ext = Path(file_path).suffix.lower()
    script_template = _SCRIPT_BY_EXT.get(ext)
    if script_template is None:
        raise OfficeAutomationFailedError(f"지원하지 않는 형식입니다: {ext}")

    script_path = _write_temp_script(script_template)
    try:
        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Path",
            str(Path(file_path).resolve()),
        ]
        if plan.page_or_slide is not None:
            command += ["-PageOrSlide", str(plan.page_or_slide)]
        if plan.sheet_name:
            command += ["-SheetName", plan.sheet_name]
        for index, needle in enumerate(plan.needles[:4], start=1):
            command += [f"-Needle{index}", needle]

        try:
            result = subprocess.run(
                command, capture_output=True, timeout=timeout, check=False, text=True
            )
        except subprocess.TimeoutExpired as exc:
            raise OfficeAutomationTimeoutError(
                f"응답 시간 초과({timeout}초): {Path(file_path).name}"
            ) from exc
        except OSError as exc:
            raise OfficeAutomationFailedError(f"PowerShell 실행 실패: {exc}") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise OfficeAutomationFailedError(
                f"Office 자동화 실패: {Path(file_path).name}" + (f" | {detail}" if detail else "")
            )
    finally:
        script_path.unlink(missing_ok=True)


# --- PowerShell 스크립트 (문자열 상수 — 파일로 두지 않아 패키징 시 별도 리소스가 안 늘어난다) ---

_COMMON_PARAMS = """
param(
    [Parameter(Mandatory=$true)][string]$Path,
    [string]$PageOrSlide,
    [string]$SheetName,
    [string]$Needle1,
    [string]$Needle2,
    [string]$Needle3,
    [string]$Needle4
)
$ErrorActionPreference = "Stop"
$needles = @($Needle1, $Needle2, $Needle3, $Needle4) | Where-Object { $_ }
"""

_DOCX_SCRIPT = _COMMON_PARAMS + """
$word = New-Object -ComObject Word.Application
$word.Visible = $true
try {
    $doc = $word.Documents.Open($Path, $false, $true)
    $found = $false
    foreach ($needle in $needles) {
        $range = $doc.Content
        if ($range.Find.Execute($needle)) {
            $range.Select()
            $word.ActiveWindow.ScrollIntoView($range) | Out-Null
            $found = $true
            break
        }
    }
    if ($needles.Count -gt 0 -and -not $found) {
        exit 1
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
"""

_PPTX_SCRIPT = _COMMON_PARAMS + """
$ppt = New-Object -ComObject PowerPoint.Application
$ppt.Visible = $true
try {
    $pres = $ppt.Presentations.Open($Path, $false, $false, $true)
    if ($PageOrSlide) {
        $pres.Windows.Item(1).View.GotoSlide([int]$PageOrSlide) | Out-Null
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
"""

_XLSX_SCRIPT = _COMMON_PARAMS + """
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $true
try {
    $wb = $excel.Workbooks.Open($Path, $false, $true)
    if ($SheetName) {
        $ws = $wb.Worksheets.Item($SheetName)
        $ws.Activate()
        foreach ($needle in $needles) {
            $cell = $ws.Cells.Find($needle)
            if ($cell) {
                $cell.Select()
                break
            }
        }
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
"""

_SCRIPT_BY_EXT = {
    ".docx": _DOCX_SCRIPT,
    ".pptx": _PPTX_SCRIPT,
    ".xlsx": _XLSX_SCRIPT,
}
