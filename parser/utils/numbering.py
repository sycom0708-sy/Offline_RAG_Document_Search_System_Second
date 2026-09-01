"""Word 다단계 번호 매기기(numPr) 계산 (T10.53).

Heading 스타일에 워드의 다단계 번호 매기기가 걸려 있으면, 화면에 보이는
"3.2" 같은 번호는 워드가 `numbering.xml`/`styles.xml`의 정의를 읽어
**렌더링만** 하는 것이고 문단의 실제 텍스트 런(`w:r/w:t`)에는 없다 —
python-docx는 문단 텍스트만 읽고 번호 매기기를 계산·렌더링하지 않으므로,
이 앱의 색인에는 애초에 "3.2"라는 문자가 존재하지 않았다.

실측(exdoc docx 10개, 2026-09-01): 제목 문단 108개 중 95개(88%)가 이
패턴이었다(주로 대형 기술 사양서 1건에 90개 중 88개 집중). 전부 표준
decimal 다단계(`%1.` / `%1.%2 ` / `%1.%2.%3 `)였다.

**번호를 어디에 넣을지는 새 스키마가 필요 없다** — `docx_parser.py`가 이미
"제목 문단도 본문에 그대로 남긴다"(검색에서 못 찾으면 안 되므로)로 헤딩
문단 텍스트를 본문 청크에 포함시키고 있다. 계산한 번호를 그 텍스트 앞에
붙이기만 하면 기존 FTS 색인 경로를 그대로 타고 검색된다 — T10.31이 닫아둔
"제목은 색인에 안 넣는다"(청크 표시용 `heading` 컬럼 얘기)를 다시 열 필요가
없다.

지원 범위: `numFmt` decimal/decimalZero/lowerRoman/upperRoman/lowerLetter/
upperLetter. 그 외(bullet 등)나 `numbering.xml` 자체가 없는 문서는 번호
없음으로 처리한다 — 억지로 숫자를 만들어 붙이는 것보다 안전하다.

`w:lvlOverride`(개별 인스턴스의 시작값 재정의)는 지원하지 않는다 — 실측
코퍼스에 없었고, 있어도 "시작값이 조금 어긋난 번호"라는 낮은 위험이다.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml import etree

_PLACEHOLDER = re.compile(r"%(\d+)")

_SUPPORTED_FORMATS = frozenset(
    {"decimal", "decimalZero", "lowerRoman", "upperRoman", "lowerLetter", "upperLetter"}
)

_ROMAN_TABLE = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)


def _format_number(value: int, num_fmt: str) -> str:
    if num_fmt == "decimal":
        return str(value)
    if num_fmt == "decimalZero":
        return f"{value:02d}"
    if num_fmt in ("lowerRoman", "upperRoman"):
        n, out = value, []
        for weight, symbol in _ROMAN_TABLE:
            count, n = divmod(n, weight)
            out.append(symbol * count)
        roman = "".join(out)
        return roman.lower() if num_fmt == "lowerRoman" else roman
    if num_fmt in ("lowerLetter", "upperLetter"):
        # 엑셀 열 이름과 같은 26진(0 없음) 표기 — 1=a, 26=z, 27=aa.
        letters = []
        n = value
        while n > 0:
            n, remainder = divmod(n - 1, 26)
            letters.append(chr(ord("a") + remainder))
        letter = "".join(reversed(letters)) or "a"
        return letter.upper() if num_fmt == "upperLetter" else letter
    return str(value)  # 안 쓰일 경로 — 호출부가 _SUPPORTED_FORMATS로 미리 거른다.


@dataclass(frozen=True)
class _LevelDef:
    num_fmt: str
    lvl_text: str
    start: int


def _numpr_of(ppr_el) -> tuple[str, int] | None:
    """`<w:pPr>` 아래 `<w:numPr>`에서 (numId, ilvl)을 읽는다."""
    if ppr_el is None:
        return None
    numpr = ppr_el.find(qn("w:numPr"))
    if numpr is None:
        return None
    num_id_el = numpr.find(qn("w:numId"))
    if num_id_el is None:
        return None
    num_id = num_id_el.get(qn("w:val"))
    ilvl_el = numpr.find(qn("w:ilvl"))
    ilvl = int(ilvl_el.get(qn("w:val"))) if ilvl_el is not None else 0
    return (num_id, ilvl)


def _parse_style_numpr(styles_xml: bytes) -> dict[str, tuple[str, int]]:
    """스타일 아이디 → (numId, ilvl). 스타일의 `pPr/numPr`에 정의된 것만."""
    root = etree.fromstring(styles_xml)
    result: dict[str, tuple[str, int]] = {}
    for style in root.findall(qn("w:style")):
        style_id = style.get(qn("w:styleId"))
        if not style_id:
            continue
        numpr = _numpr_of(style.find(qn("w:pPr")))
        if numpr:
            result[style_id] = numpr
    return result


def _parse_levels(numbering_xml: bytes) -> dict[tuple[str, int], _LevelDef]:
    """(numId, ilvl) → 레벨 정의. `w:num`이 가리키는 `w:abstractNum`을 따라간다."""
    root = etree.fromstring(numbering_xml)

    abstract_levels: dict[str, dict[int, _LevelDef]] = {}
    for abstract_num in root.findall(qn("w:abstractNum")):
        abstract_id = abstract_num.get(qn("w:abstractNumId"))
        levels: dict[int, _LevelDef] = {}
        for lvl in abstract_num.findall(qn("w:lvl")):
            ilvl = int(lvl.get(qn("w:ilvl")))
            num_fmt_el = lvl.find(qn("w:numFmt"))
            lvl_text_el = lvl.find(qn("w:lvlText"))
            start_el = lvl.find(qn("w:start"))
            if num_fmt_el is None or lvl_text_el is None:
                continue
            levels[ilvl] = _LevelDef(
                num_fmt=num_fmt_el.get(qn("w:val")) or "decimal",
                lvl_text=lvl_text_el.get(qn("w:val")) or "",
                start=int(start_el.get(qn("w:val"))) if start_el is not None else 1,
            )
        abstract_levels[abstract_id] = levels

    result: dict[tuple[str, int], _LevelDef] = {}
    for num in root.findall(qn("w:num")):
        num_id = num.get(qn("w:numId"))
        abstract_id_el = num.find(qn("w:abstractNumId"))
        if abstract_id_el is None:
            continue
        abstract_id = abstract_id_el.get(qn("w:val"))
        for ilvl, level_def in abstract_levels.get(abstract_id, {}).items():
            result[(num_id, ilvl)] = level_def
    return result


class NumberingResolver:
    """문서 순서대로 헤딩을 지나며 번호를 계산한다 (상태를 가진다).

    호출은 반드시 문서에 등장하는 순서와 같아야 한다 — 상위 레벨이 바뀌면
    하위 레벨 카운터가 리셋되는 워드의 기본 동작을 그대로 흉내 낸다.
    """

    def __init__(
        self,
        style_numpr: dict[str, tuple[str, int]],
        levels: dict[tuple[str, int], _LevelDef],
    ) -> None:
        self._style_numpr = style_numpr
        self._levels = levels
        self._counters: dict[str, dict[int, int]] = {}

    @classmethod
    def from_docx_path(cls, path: str | Path) -> "NumberingResolver":
        """번호 매기기 정의가 없는 문서에서도 안전하게 빈 리졸버를 돌려준다."""
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                if "word/numbering.xml" not in names or "word/styles.xml" not in names:
                    return cls({}, {})
                numbering_xml = zf.read("word/numbering.xml")
                styles_xml = zf.read("word/styles.xml")
        except (OSError, zipfile.BadZipFile, KeyError):
            return cls({}, {})

        try:
            style_numpr = _parse_style_numpr(styles_xml)
            levels = _parse_levels(numbering_xml)
        except etree.XMLSyntaxError:
            return cls({}, {})
        return cls(style_numpr, levels)

    def number_for(self, paragraph: Paragraph) -> str:
        """이 문단의 번호(예: "3.2")를 계산한다. 없으면 빈 문자열.

        직접 지정(문단 자신의 `pPr/numPr`)이 스타일 상속보다 우선한다 —
        워드의 실제 우선순위와 같다.
        """
        direct = _numpr_of(paragraph._p.find(qn("w:pPr")))
        style_id = paragraph.style.style_id if paragraph.style is not None else None
        num_id_ilvl = direct or (self._style_numpr.get(style_id) if style_id else None)
        if num_id_ilvl is None:
            return ""

        num_id, ilvl = num_id_ilvl
        level_def = self._levels.get((num_id, ilvl))
        if level_def is None or level_def.num_fmt not in _SUPPORTED_FORMATS:
            return ""

        counters = self._counters.setdefault(num_id, {})
        counters[ilvl] = counters.get(ilvl, level_def.start - 1) + 1
        # 상위 레벨이 바뀌면 그보다 깊은 레벨은 다음에 등장할 때 start부터
        # 다시 시작한다(워드 기본 동작) — 지금 지워두면 된다.
        for deeper in [lvl for lvl in counters if lvl > ilvl]:
            del counters[deeper]

        return self._render(num_id, ilvl, counters)

    def _render(self, num_id: str, ilvl: int, counters: dict[int, int]) -> str:
        level_def = self._levels[(num_id, ilvl)]

        def replace(match: re.Match) -> str:
            ref_ilvl = int(match.group(1)) - 1
            ref_level = self._levels.get((num_id, ref_ilvl))
            if ref_level is None:
                return match.group(0)
            value = counters.get(ref_ilvl, ref_level.start)
            return _format_number(value, ref_level.num_fmt)

        return _PLACEHOLDER.sub(replace, level_def.lvl_text).strip()
