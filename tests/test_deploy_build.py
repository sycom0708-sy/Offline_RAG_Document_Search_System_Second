"""LibreOffice 미사용 자산 제거 테스트 (T9.11).

실제 LibreOffice Portable(1.5GB)은 안 쓴다 — 합성 폴더 구조로 제거 로직만
검증한다. 실제 변환 결과 회귀는 exdoc 코퍼스로 실측했다(TASK 문서 T9.11 참고,
doc/xls 10개 전수 재변환 결과 제거 전/후 동일).
"""

from __future__ import annotations

from deploy.build import _strip_libreoffice_unused_assets


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


class TestStripLibreOfficeUnusedAssets:
    def _build_tree(self, root):
        # 트리밍 대상 — 사전 3개 언어, 로케일 리소스 2개 언어.
        for lang in ("af", "ko", "zu"):
            _touch(root / "App/libreoffice/share/extensions" / f"dict-{lang}" / "dictionary.aff")
        for locale in ("en-US", "ko"):
            _touch(root / "App/libreoffice/program/resource" / locale / f"{locale}.res")
        # 트리밍 대상이 아닌 다른 확장 — 이름 패턴이 달라 글롭에 안 걸려야 한다.
        _touch(root / "App/libreoffice/share/extensions/nlpsolver/plugin.xcd")
        # 변환 필터가 실제 쓰는 것 — 절대 지워지면 안 된다.
        _touch(root / "App/libreoffice/program/soffice.bin")

    def test_dict_folders_are_removed(self, tmp_path):
        self._build_tree(tmp_path)
        _strip_libreoffice_unused_assets(tmp_path)

        extensions = tmp_path / "App/libreoffice/share/extensions"
        assert not (extensions / "dict-af").exists()
        assert not (extensions / "dict-ko").exists()
        assert not (extensions / "dict-zu").exists()

    def test_locale_resource_folder_is_removed_entirely(self, tmp_path):
        self._build_tree(tmp_path)
        _strip_libreoffice_unused_assets(tmp_path)

        assert not (tmp_path / "App/libreoffice/program/resource").exists()

    def test_other_extensions_are_left_alone(self, tmp_path):
        """이름이 "dict-"로 시작하지 않는 확장(nlpsolver 등)은 글롭에 안 걸린다."""
        self._build_tree(tmp_path)
        _strip_libreoffice_unused_assets(tmp_path)

        assert (tmp_path / "App/libreoffice/share/extensions/nlpsolver/plugin.xcd").exists()

    def test_actual_runtime_binary_survives(self, tmp_path):
        """변환 필터가 실제로 쓰는 실행 파일은 어떤 규칙에도 안 걸려야 한다."""
        self._build_tree(tmp_path)
        _strip_libreoffice_unused_assets(tmp_path)

        assert (tmp_path / "App/libreoffice/program/soffice.bin").exists()

    def test_missing_targets_do_not_raise(self, tmp_path):
        """트리밍 대상이 애초에 없어도(구버전 LibreOffice 등) 조용히 넘어간다."""
        _strip_libreoffice_unused_assets(tmp_path)  # 빈 폴더 — 예외 없이 끝나야 한다
