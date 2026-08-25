""""원문 열기" 딥링크 — Office COM / Edge PDF 뷰어로 정확한 위치로 이동 (T10.1, T10.50).

docx/doc/pptx/xlsx/xls/pdf만 다룬다(hwp/hwpx는 이 PC에 한글이 없어 실측
검증이 불가능해 이번 범위에서 제외 — 사용자 확정, ppt는 요청 범위 밖이라
미포함). doc/xls는 신버전과 같은 Word/Excel COM이 `Documents.Open()`/
`Workbooks.Open()`으로 포맷을 자동 인식해 직접 여는 것뿐이라 docx/xlsx와
완전히 같은 스크립트·로직을 재사용한다(실측 확인 — LibreOffice 변환은
**인덱싱 파싱** 전용이고 이 딥링크 경로와는 무관하다). Office 계열은
pywin32 없이 PowerShell 서브프로세스로 COM을 구동한다(2026-08-07 실측
확인, LibreOffice 변환과 같은 "관리자 권한 불필요" 원칙과 결이 같다).
pdf는 COM이 아니라 Edge를 `--single-argument`로 직접 실행해 `#page=`
URL 프래그먼트로 페이지 이동한다(기본 PDF 뷰어가 Edge일 때만 — T10.50,
상세는 `_open_pdf_at_page` 참고).

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
    # 구버전 포맷도 같은 앱이 COM으로 직접 연다(포맷만 다를 뿐 — 실측 확인).
    ".doc": "Word.Application",
    ".xls": "Excel.Application",
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

    if ext == ".pdf":
        # 텍스트/표/이미지 청크 전부 정확한 페이지 번호를 갖고 있다(pdf_parser.py) —
        # Edge PDF 뷰어의 #page= URL 프래그먼트로 페이지 이동만 하면 충분하다.
        return OpenPlan(page_or_slide=hybrid_result.page_or_slide)

    if ext == ".pptx":
        # 텍스트/표/이미지 청크 전부 정확한 slide_index를 갖고 있다(pptx_parser.py) —
        # 텍스트 검색 없이 슬라이드 이동만으로 충분하다.
        return OpenPlan(page_or_slide=hybrid_result.page_or_slide)

    if ext in (".xlsx", ".xls") and hybrid_result.type is ChunkType.TABLE:
        # .xls는 Excel COM이 신버전과 똑같이 `Workbooks.Open()`으로 직접
        # 여는 구버전 포맷일 뿐이라 xlsx와 같은 로직을 그대로 쓴다(실측 확인).
        table = parse_table_data(result)
        if table is None:
            return OpenPlan()
        needle = _longest_cell(table.rows, table.header_row)
        return OpenPlan(sheet_name=table.caption or None, needles=[needle] if needle else [])

    if ext in (".docx", ".doc"):
        # .doc도 Word COM이 `Documents.Open()`으로 직접 열 수 있는 구버전
        # 포맷이라 docx와 같은 needle ladder 로직을 그대로 쓴다(실측 확인).
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
    """확장자에 대응하는 딥링크 실행기가 이 PC에 있는지.

    docx/pptx/xlsx는 Office COM ProgID 등록 여부, pdf는 Edge PDF 뷰어
    (`#page=` 프래그먼트를 지원하는 것을 실측 확인)가 기본 뷰어인지 + Edge
    실행 파일 존재 여부로 판단한다.
    """
    ext = ext.lower()
    if ext == ".pdf":
        return _pdf_default_progid() in _PDF_JUMP_PROGIDS and _find_msedge_exe() is not None
    prog_id = _PROGID_BY_EXT.get(ext)
    if prog_id is None:
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id):
            return True
    except OSError:
        return False


# PDF는 Office COM이 아니라 Edge의 PDF 뷰어(Chromium PDFium, #page= URL
# 프래그먼트 지원 — 실측 확인)를 쓴다. 다른 기본 뷰어(Adobe Reader 등)는
# 페이지 이동 문법이 달라 이번 범위에서는 지원하지 않고 조용히 폴백한다.
_PDF_JUMP_PROGIDS = {"MSEdgePDF"}


def _pdf_default_progid() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice",
        ) as key:
            return winreg.QueryValueEx(key, "ProgId")[0]
    except OSError:
        return None


def _find_msedge_exe() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        ) as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


def _open_pdf_at_page(file_path: str, plan: OpenPlan) -> None:
    """Edge를 직접 실행해 `#page=` 프래그먼트로 페이지 이동을 요청한다.

    🔴 로컬 file:// URL에 `#page=`를 붙여 `ShellExecute`(Qt의
    `QDesktopServices.openUrl`, PowerShell `Start-Process` 포함)로 열면
    "지정된 파일을 찾을 수 없습니다" 오류로 아예 열리지 않는다(실측 확인) —
    셸이 프래그먼트까지 파일 경로의 일부로 보고 그런 파일을 찾으려 하기
    때문. Edge 실행 파일을 `--single-argument`로 직접 호출해야
    ShellExecute의 파일 조회 단계를 건너뛰고 그 URL을 그대로 탐색 대상으로
    받는다(실측 확인).
    """
    if plan.page_or_slide is None:
        raise OfficeAutomationFailedError("이동할 페이지 정보가 없습니다.")
    msedge = _find_msedge_exe()
    if not msedge:
        raise OfficeAutomationFailedError("Edge 실행 파일을 찾을 수 없습니다.")

    url = Path(file_path).resolve().as_uri() + f"#page={plan.page_or_slide}"
    try:
        subprocess.Popen([msedge, "--single-argument", url])
    except OSError as exc:
        raise OfficeAutomationFailedError(f"Edge 실행 실패: {exc}") from exc


def _write_temp_script(content: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as fp:
        fp.write(content)
        return Path(fp.name)


def open_and_locate(file_path: str, plan: OpenPlan, timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
    """딥링크로 문서를 열고 계획된 위치로 이동한다. 실패하면 예외를 던진다."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        _open_pdf_at_page(file_path, plan)
        return

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
                command, capture_output=True, timeout=timeout, check=False, text=True,
                # PowerShell은 콘솔 프로그램이라, 콘솔 없는(--windowed) 이 앱에서
                # 그냥 부르면 "원문 열기"마다 콘솔 창이 잠깐 떴다 사라진다.
                creationflags=subprocess.CREATE_NO_WINDOW,
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

# 🔴 세 스크립트 전부 "파일 열기"와 "위치로 이동"을 분리해 서로 다른
# try/catch에 둔다(T10.51). 원래는 하나의 try/catch로 묶여 있어, 파일은
# 이미 정상적으로 열렸는데 위치 이동(Find/GotoSlide/Worksheets.Item)만
# 실패해도 스크립트 전체가 실패로 처리됐다 — 그러면 파이썬 쪽
# `open_source_file()`이 "COM 자동화 실패"로 보고 기존 폴백(OS 기본
# 프로그램으로 다시 열기)을 실행하는데, 이 폴백이 **새 Excel/Word/PowerPoint
# 프로세스**를 하나 더 띄워 같은 파일이 창 두 개로 뜬다(실측 재현 확인:
# 시트명이 살짝 어긋난 xlsx 하나로 PID 두 개가 동시에 떴다). "파일이
# 열리기는 한다"는 원래 보장(T10.1)은 위치 이동 실패와 무관하게 이미
# `.Open()` 시점에 충족되므로, 위치 이동 실패는 조용히 넘기고(창은 이미
# 열려 있으니 그대로 둔다) `.Open()` 자체가 실패했을 때만(파일 손상,
# COM 미설치 등 — 이때는 창이 하나도 안 떴으므로 폴백이 안전하다) 실패로
# 본다.

_DOCX_SCRIPT = _COMMON_PARAMS + """
$word = New-Object -ComObject Word.Application
$word.Visible = $true
$doc = $word.Documents.Open($Path, $false, $true)
try {
    foreach ($needle in $needles) {
        $range = $doc.Content
        if ($range.Find.Execute($needle)) {
            $range.Select()
            $word.ActiveWindow.ScrollIntoView($range) | Out-Null
            break
        }
    }
} catch {
    # 위치 이동 실패는 무시한다 — 파일은 이미 열렸다.
}
"""

_PPTX_SCRIPT = _COMMON_PARAMS + """
$ppt = New-Object -ComObject PowerPoint.Application
# 🔴 Word/Excel의 Visible은 순수 Boolean이지만 PowerPoint는 MsoTriState
# 열거형이다 — 이 서브프로세스 컨텍스트에서는 `$true`가 자동으로
# MsoTriState로 안 바뀌어 캐스팅 예외가 난다(실측 확인). -1은
# MsoTriState.msoTrue의 실제 값이라 어떤 컨텍스트에서도 안전하다.
$ppt.Visible = -1
$pres = $ppt.Presentations.Open($Path, $false, $false, $true)
try {
    if ($PageOrSlide) {
        $pres.Windows.Item(1).View.GotoSlide([int]$PageOrSlide) | Out-Null
    }
} catch {
    # 위치 이동 실패는 무시한다 — 파일은 이미 열렸다.
}
"""

_XLSX_SCRIPT = _COMMON_PARAMS + """
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $true
$wb = $excel.Workbooks.Open($Path, $false, $true)
try {
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
    # 위치 이동 실패는 무시한다 — 파일은 이미 열렸다.
}
"""

_SCRIPT_BY_EXT = {
    ".docx": _DOCX_SCRIPT,
    ".pptx": _PPTX_SCRIPT,
    ".xlsx": _XLSX_SCRIPT,
    # `Documents.Open()`/`Workbooks.Open()`은 포맷을 자동 인식해 구버전도
    # 그대로 여는 COM 호출이라 스크립트를 따로 만들 필요가 없다(실측 확인).
    ".doc": _DOCX_SCRIPT,
    ".xls": _XLSX_SCRIPT,
}
