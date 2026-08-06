"""임시 CLI 테스트 (T2.9)."""

from __future__ import annotations

import shutil

from indexer.cli import build_parser, main


def test_index_then_search_end_to_end(tmp_path, sample_txt, capsys):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "doc.txt")
    db_path = tmp_path / "index.sqlite3"

    exit_code = main(["--db", str(db_path), "index", str(work)])
    assert exit_code == 0
    index_output = capsys.readouterr().out
    assert "인덱싱 완료" in index_output
    assert "문서 1개" in index_output

    exit_code = main(["--db", str(db_path), "search", "오프라인"])
    assert exit_code == 0
    search_output = capsys.readouterr().out
    assert "doc.txt" in search_output


def test_search_with_no_results_prints_message(tmp_path, sample_txt, capsys):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "doc.txt")
    db_path = tmp_path / "index.sqlite3"

    main(["--db", str(db_path), "index", str(work)])
    capsys.readouterr()

    main(["--db", str(db_path), "search", "존재하지않는단어XYZ"])
    output = capsys.readouterr().out
    assert "검색 결과가 없습니다" in output


def test_search_type_filter_flag(tmp_path, sample_docx, capsys):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_docx, work / "doc.docx")
    db_path = tmp_path / "index.sqlite3"

    main(["--db", str(db_path), "index", str(work)])
    capsys.readouterr()

    main(["--db", str(db_path), "search", "구분", "--types", "text"])
    output = capsys.readouterr().out
    assert "table" not in output


def test_build_parser_flags():
    parser = build_parser()
    args = parser.parse_args(["search", "질의", "--case-sensitive", "--exact-word", "--types", "text,table"])
    assert args.case_sensitive is True
    assert args.exact_word is True
    assert args.types == "text,table"

    args = parser.parse_args(["search", "질의"])
    assert args.case_sensitive is False
    assert args.exact_word is False
