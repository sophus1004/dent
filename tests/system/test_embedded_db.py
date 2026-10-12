"""내장 DB 데이터 폴더 준비 테스트: Windows에서 새 폴더에만 지금 사용자 계정의 권한을 준다(관리자 터미널에서도 initdb가 다루게)."""

import subprocess

from dent.system import embedded_db


def _record_runs(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(embedded_db.subprocess, "run", run)
    return calls


def test_prepare_pgdata_grants_current_user_on_new_windows_folder(tmp_path, monkeypatch):
    # 준비
    monkeypatch.setattr(embedded_db, "IS_WINDOWS", True)
    monkeypatch.setenv("USERDOMAIN", "RUNNER")
    monkeypatch.setattr(embedded_db.getpass, "getuser", lambda: "runneradmin")
    calls = _record_runs(monkeypatch)
    pgdata = tmp_path / "storage" / "pgdata"

    # 실행
    embedded_db._prepare_pgdata(pgdata)

    # 확인: 폴더를 만들고 그 폴더에 RUNNER\\runneradmin의 모든 권한(아래로 물려줌)을 준다
    assert pgdata.is_dir()
    assert calls == [["icacls", str(pgdata), "/grant", "RUNNER\\runneradmin:(OI)(CI)F", "/T", "/Q"]]


def test_prepare_pgdata_leaves_initialized_or_non_windows_folder_alone(tmp_path, monkeypatch):
    # 준비: 이미 DB를 만든 Windows 폴더와, Windows가 아닌 새 폴더
    calls = _record_runs(monkeypatch)
    initialized = tmp_path / "initialized"
    initialized.mkdir()
    (initialized / "PG_VERSION").write_text("18", encoding="utf-8")
    fresh = tmp_path / "fresh" / "pgdata"

    # 실행
    monkeypatch.setattr(embedded_db, "IS_WINDOWS", True)
    embedded_db._prepare_pgdata(initialized)
    monkeypatch.setattr(embedded_db, "IS_WINDOWS", False)
    embedded_db._prepare_pgdata(fresh)

    # 확인: 권한을 건드리지 않고, Windows가 아니면 부모만 만든다(데이터 폴더는 패키지가 만든다)
    assert calls == []
    assert fresh.parent.is_dir()
    assert not fresh.exists()
