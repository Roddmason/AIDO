from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "local-control-center" / "scripts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_local_dns_scripts_exist_and_target_windows_hosts() -> None:
    register = SCRIPTS / "register-local-dns.ps1"
    unregister = SCRIPTS / "unregister-local-dns.ps1"

    assert register.exists()
    assert unregister.exists()

    register_source = read(register)
    unregister_source = read(unregister)

    for source in (register_source, unregister_source):
        assert "System32\\drivers\\etc\\hosts" in source
        assert "local-control-center.test" in source
        assert "ipconfig /flushdns" in source
        assert "WindowsBuiltInRole]::Administrator" in source
        assert "Start-Process" in source
        assert "-Verb RunAs" in source


def test_local_dns_registration_is_idempotent_and_scoped_by_marker() -> None:
    register_source = read(SCRIPTS / "register-local-dns.ps1")
    unregister_source = read(SCRIPTS / "unregister-local-dns.ps1")

    assert "# BEGIN Local Control Center DNS" in register_source
    assert "# END Local Control Center DNS" in register_source
    assert "already registered" in register_source
    assert "127.0.0.1" in register_source
    assert "::1" in register_source

    assert "# BEGIN Local Control Center DNS" in unregister_source
    assert "# END Local Control Center DNS" in unregister_source
    assert "Set-Content" in unregister_source


def test_start_script_documents_default_dns_hostname() -> None:
    start_source = read(SCRIPTS / "start-control-center.ps1")
    docs_source = read(ROOT / "docs" / "python-control-center.md")

    assert "local-control-center.test" in start_source
    assert "local-control-center.test" in docs_source
    assert "register-local-dns.ps1" in docs_source
    assert "unregister-local-dns.ps1" in docs_source
