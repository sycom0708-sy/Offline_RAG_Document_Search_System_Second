"""레거시 `.doc` 캡션 번호 재계산 테스트 (T10.52)."""

from __future__ import annotations

from parser.utils.captions import recompute_legacy_captions


def test_broken_global_counter_is_recomputed_per_chapter():
    """조사 문서의 실측 패턴(2-1,2-2,3-3,4-4,...)을 축약 재현한다."""
    paragraphs = [
        (0, True, "1장 개요"),
        (1, True, "2장 통신"),
        (2, False, "[표 2-1] 요청 패킷"),
        (3, False, "[표 2-2] 응답 패킷"),
        (4, True, "3장 진단"),
        (5, False, "[표 3-3] 진단 코드"),  # 원래는 3-1이어야 함
        (6, True, "4장 배터리"),
        (7, False, "[표 4-4] 배터리 상태 표"),  # 원래는 4-1이어야 함
    ]

    fixes = recompute_legacy_captions(paragraphs)

    assert fixes[5] == "[표 3-1] 진단 코드"
    assert fixes[7] == "[표 4-1] 배터리 상태 표"
    # 이미 맞는 값(2-1, 2-2)은 고칠 필요가 없으니 결과에 안 나온다.
    assert 2 not in fixes
    assert 3 not in fixes


def test_toc_entries_do_not_block_the_recompute():
    """표차례(TOC) 항목이 본문 캡션과 같은 라벨로 섞이면 안전 진단이 오염돼
    아무것도 안 고쳐진다 — 실제 레거시 doc 문서 검증에서 재현된 문제(탭+쪽번호로
    걸러낸다)."""
    paragraphs = [
        # 표차례 — 이미 챕터별로 정상 리셋된 값, 끝에 탭+쪽번호가 붙는다.
        (0, False, "[표 2-1] 요청 패킷\t5"),
        (1, False, "[표 2-2] 응답 패킷\t5"),
        (2, False, "[표 3-1] 진단 코드\t7"),
        (3, False, "[표 4-1] 배터리 상태 표\t8"),
        # 본문 — 전역 카운터로 깨짐(조사 문서 §3.3의 실측 패턴과 동일한 모양).
        (4, True, "1장 개요"),
        (5, True, "2장 통신"),
        (6, False, "[표 2-1] 요청 패킷"),
        (7, False, "[표 2-2] 응답 패킷"),
        (8, True, "3장 진단"),
        (9, False, "[표 3-3] 진단 코드"),  # 원래는 3-1
        (10, True, "4장 배터리"),
        (11, False, "[표 4-4] 배터리 상태 표"),  # 원래는 4-1
    ]

    fixes = recompute_legacy_captions(paragraphs)

    assert fixes[9] == "[표 3-1] 진단 코드"
    assert fixes[11] == "[표 4-1] 배터리 상태 표"
    # 표차례 항목은 관측 대상에서 빠지므로 고칠 대상에도 나오지 않는다.
    assert 0 not in fixes
    assert 1 not in fixes
    assert 2 not in fixes
    assert 3 not in fixes


def test_already_chapter_scoped_captions_are_left_untouched():
    """정상 작동 중인 필드(또는 고정 텍스트)는 전역 1,2,3,... 패턴이 아니다."""
    paragraphs = [
        (0, True, "1장"),
        (1, False, "[표 1-1] A"),
        (2, True, "2장"),
        (3, False, "[표 2-1] B"),
    ]

    assert recompute_legacy_captions(paragraphs) == {}


def test_captions_with_a_gap_are_left_untouched():
    """번호에 빈틈이 있으면(4-1, 4-3만 있고 4-2가 없음) 고정 텍스트 신호 — 손대지 않는다."""
    paragraphs = [
        (0, False, "[표 4-1] A"),
        (1, False, "[표 4-3] B"),
    ]

    assert recompute_legacy_captions(paragraphs) == {}


def test_out_of_order_captions_are_left_untouched():
    """물리적 순서와 번호 순서가 어긋나면 고정 텍스트 신호 — 손대지 않는다."""
    paragraphs = [
        (0, False, "[표 4-2] A"),
        (1, False, "[표 4-1] B"),
    ]

    assert recompute_legacy_captions(paragraphs) == {}


def test_single_occurrence_label_is_left_untouched():
    """라벨당 한 번뿐이면 연속 여부를 판단할 근거가 없다 — 손대지 않는다."""
    paragraphs = [
        (0, True, "1장"),
        (1, True, "2장"),
        (2, False, "[그림 1-1] 배치도"),
    ]

    assert recompute_legacy_captions(paragraphs) == {}


def test_labels_are_judged_independently():
    """'표'는 전역 카운터로 깨졌고 '그림'은 챕터마다 정상 리셋되는 문서에서
    표만 고친다 — 두 라벨을 같은 문단들 사이에 섞어도 서로 간섭하지 않는다."""
    paragraphs = [
        (0, True, "1장"),
        (1, True, "2장"),
        (2, False, "[표 2-1] T1"),
        (3, False, "[그림 1-1] F1"),
        (4, True, "3장"),
        (5, False, "[표 3-2] T2"),  # 표: 전역 카운터로 깨짐 (원래 3-1)
        (6, False, "[그림 1-1] F2"),  # 그림: 챕터마다 1로 정상 리셋(전역 아님)
    ]

    fixes = recompute_legacy_captions(paragraphs)

    assert fixes == {5: "[표 3-1] T2"}


def test_no_captions_returns_empty():
    paragraphs = [(0, True, "1장"), (1, False, "그냥 본문 문단")]
    assert recompute_legacy_captions(paragraphs) == {}


def test_preserves_label_spacing_and_trailing_text():
    paragraphs = [
        (0, True, "1장"),
        (1, True, "2장"),
        (2, False, "[표2-1] 붙어있는 라벨"),
        (3, True, "3장"),
        (4, False, "[표3-2] 뒤 설명까지 그대로"),  # 전역 카운터로 깨짐(원래 3-1)
    ]

    fixes = recompute_legacy_captions(paragraphs)

    assert fixes == {4: "[표3-1] 뒤 설명까지 그대로"}
