"""LibreOffice 헤드리스 변환 wrapper (T1.9).

구버전 포맷(doc/xls/ppt) 변환과, docx/pptx의 벡터 도형 페이지 캡처(PDF 경유)에 함께 쓴다.
"""

from __future__ import annotations

import locale
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from parser.base import ParserError

DEFAULT_TIMEOUT_SEC = 120

# 앱이 자동으로 내려받아 설치하지는 않는다 — PRD 4장의 완전 오프라인·무관리자권한
# 전제와 배치된다(앱 내 다운로드는 인터넷을, 정식 설치본은 관리자 권한을 요구한다).
# 직접 설치 안내만 보여준다(사용자 확정, PLAN Phase 10 T10.2).
INSTALL_HINT = "LibreOffice 포터블을 내려받아 vendor/LibreOfficePortable/ 폴더에 넣으세요."

# PATH에 없을 때 확인할 Windows 기본 설치 경로 + 포터블 배포 시의 상대 경로 (TECH 9.1절).
_CANDIDATE_PATHS = (
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    Path(__file__).resolve().parents[2] / "vendor" / "LibreOfficePortable" / "App"
    / "libreoffice" / "program" / "soffice.exe",
)


class LibreOfficeError(ParserError):
    """LibreOffice 변환 계열 예외의 기반."""


class LibreOfficeNotFoundError(LibreOfficeError):
    """soffice 실행 파일을 찾지 못함."""


class ConversionTimeoutError(LibreOfficeError):
    """변환이 제한 시간 내에 끝나지 않음."""


class ConversionFailedError(LibreOfficeError):
    """soffice가 비정상 종료했거나 결과 파일이 생성되지 않음."""


def find_soffice() -> Path | None:
    """soffice 경로를 반환. 환경변수 SOFFICE_PATH > PATH > 기본 설치 경로 순."""
    env_path = os.environ.get("SOFFICE_PATH")
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    for name in ("soffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)

    for candidate in _CANDIDATE_PATHS:
        if candidate.is_file():
            return candidate
    return None


def is_available() -> bool:
    return find_soffice() is not None


def is_missing_libreoffice_error(message: str) -> bool:
    """오류 문자열이 "LibreOffice를 못 찾음"인지 판별한다.

    `LegacyOfficeParser`는 예외를 문자열로만 `document.errors`에 남기므로,
    변환 실패(soffice는 있는데 이 파일에서 오류)와 미설치(soffice 자체가 없음)를
    UI 단에서 되짚으려면 문자열로 구분해야 한다. `INSTALL_HINT`는
    `LibreOfficeNotFoundError`의 메시지에만 실리므로 이 부분 문자열이 있는지만
    보면 된다 — 안내 문구를 바꿔도 이 판별 로직은 그대로 따라간다.
    """
    return INSTALL_HINT in message


def convert(
    source: str | Path,
    target_ext: str,
    output_dir: str | Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Path:
    """source를 target_ext 형식으로 변환하고 결과 경로를 반환한다.

    실패 사유는 LibreOfficeNotFoundError / ConversionTimeoutError / ConversionFailedError로 구분된다.
    """
    soffice = find_soffice()
    if soffice is None:
        raise LibreOfficeNotFoundError(
            f"LibreOffice(soffice)를 찾을 수 없습니다. {INSTALL_HINT} "
            "(또는 SOFFICE_PATH 환경변수로 실행 파일 경로를 지정하세요)"
        )

    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise ConversionFailedError(f"변환 대상 파일이 없습니다: {source_path}")

    out_dir = Path(output_dir).resolve() if output_dir else source_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # 동시 실행 시 프로필 충돌로 변환이 조용히 실패하므로 호출마다 별도 프로필을 쓴다.
    with tempfile.TemporaryDirectory(prefix="lo_profile_") as profile_dir:
        command = [
            str(soffice),
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation={Path(profile_dir).as_uri()}",
            "--convert-to",
            target_ext,
            "--outdir",
            str(out_dir),
            str(source_path),
        ]
        try:
            # 한국어 Windows에서 soffice는 CP949로 메시지를 출력한다.
            # text=True(UTF-8 고정)로 받으면 디코딩이 깨져 진단 정보를 잃으므로 바이트로 받는다.
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionTimeoutError(
                f"LibreOffice 변환 시간 초과({timeout}초): {source_path.name}"
            ) from exc
        except OSError as exc:
            raise ConversionFailedError(
                f"LibreOffice 실행에 실패했습니다: {source_path.name} ({exc})"
            ) from exc

    # target_ext에 필터 지정이 붙는 경우("pdf:writer_pdf_Export")를 고려해 확장자만 취한다.
    suffix = target_ext.split(":")[0]
    produced = out_dir / f"{source_path.stem}.{suffix}"
    if not produced.is_file():
        detail = _decode_output(result.stderr) or _decode_output(result.stdout)
        raise ConversionFailedError(
            f"변환 결과 파일이 생성되지 않았습니다: {source_path.name} → .{suffix}"
            + (f" | soffice: {detail}" if detail else "")
        )
    return produced


def _decode_output(raw: bytes | None) -> str:
    """soffice 출력의 인코딩은 로케일에 따라 다르므로 순차적으로 시도한다."""
    if not raw:
        return ""
    for encoding in ("utf-8", locale.getpreferredencoding(False), "cp949"):
        try:
            return raw.decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace").strip()
