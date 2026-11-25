"""The trace viewer's server: the page's files and the configured directory's trace files, answered
only for requests addressed to its loopback address."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from llm_pii_proxy.viewer import __main__ as viewer_main
from llm_pii_proxy.viewer import app as app_module
from llm_pii_proxy.viewer.app import create_viewer_app

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def client(trace_dir, base=BASE, **kwargs):
    return TestClient(create_viewer_app(trace_dir, PORT), base_url=base, **kwargs)


def test_serves_the_page_without_a_directory_picker(tmp_path):
    response = client(tmp_path).get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<input" not in response.text
    script = client(tmp_path).get("/app.js")
    assert script.status_code == 200
    assert "webkitRelativePath" not in script.text
    assert "showDirectoryPicker" not in script.text


def test_serves_every_page_script_as_javascript(tmp_path):
    static = Path(app_module.__file__).parent / "static"
    scripts = sorted(p.relative_to(static).as_posix() for p in static.rglob("*.js"))
    assert "vendor/json-diff-viewer/viewer.js" in scripts
    viewer = client(tmp_path)
    for script in scripts:
        response = viewer.get(f"/{script}")
        assert response.status_code == 200, script
        assert response.headers["content-type"].startswith("text/javascript"), script


def test_paths_that_are_not_page_files_get_404(tmp_path):
    (tmp_path / "a.jsonl").write_text("secret\n")
    for path in ("/a.jsonl", "/nope.js", "/vendor", "/../pyproject.toml", "/%2e%2e/pyproject.toml"):
        assert client(tmp_path).get(path).status_code == 404, path


def test_lists_the_trace_files_of_the_directory_sorted_by_name(tmp_path):
    (tmp_path / "b.jsonl").write_text("second\n")
    (tmp_path / "a.jsonl").write_text("first\n")
    (tmp_path / "notes.txt").write_text("not a trace")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.jsonl").write_text("nested")
    response = client(tmp_path).get("/traces")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "files": [{"name": "a.jsonl", "text": "first\n"}, {"name": "b.jsonl", "text": "second\n"}]
    }


def test_a_line_cut_inside_a_character_keeps_the_rest_of_the_file(tmp_path):
    (tmp_path / "a.jsonl").write_bytes(b"whole\n" + "ü".encode()[:1])
    [entry] = client(tmp_path).get("/traces").json()["files"]
    assert entry["text"].startswith("whole\n")


def test_an_unreadable_trace_file_is_reported_by_name(tmp_path):
    (tmp_path / "a.jsonl").write_text("ok\n")
    locked = tmp_path / "b.jsonl"
    locked.write_text("secret\n")
    locked.chmod(0)
    try:
        files = client(tmp_path).get("/traces").json()["files"]
    finally:
        locked.chmod(0o600)
    assert files == [
        {"name": "a.jsonl", "text": "ok\n"},
        {"name": "b.jsonl", "error": "PermissionError"},
    ]


def test_a_directory_gone_since_startup_is_an_error_not_an_empty_list(tmp_path):
    gone = tmp_path / "gone"
    gone.mkdir()
    viewer = client(gone, raise_server_exceptions=False)
    assert viewer.get("/traces").json() == {"files": []}
    gone.rmdir()
    assert viewer.get("/traces").status_code == 500


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765"])
def test_both_loopback_names_for_the_viewer_port_are_answered(tmp_path, host):
    (tmp_path / "a.jsonl").write_text("ok\n")
    for path in ("/", "/traces"):
        assert client(tmp_path).get(path, headers={"host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.test:8765", "localhost:9999", "127.0.0.1:9999", "[::1]:8765"]
)
def test_requests_addressed_to_another_host_are_refused(tmp_path, host):
    (tmp_path / "a.jsonl").write_text("secret\n")
    for path in ("/", "/traces"):
        response = client(tmp_path).get(path, headers={"host": host})
        assert response.status_code == 421
        assert "secret" not in response.text
        assert f"http://127.0.0.1:{PORT}/" in response.text


def test_main_refuses_a_missing_trace_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_PROXY_TRACE_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(viewer_main.uvicorn, "run", pytest.fail)
    with pytest.raises(SystemExit) as exit_info:
        viewer_main.main()
    assert "missing" in str(exit_info.value)


def test_main_serves_the_configured_directory_on_loopback(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PII_PROXY_TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("PII_PROXY_VIEWER_PORT", "9123")
    (tmp_path / "a.jsonl").write_text("first\n")
    calls = []
    monkeypatch.setattr(viewer_main.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    viewer_main.main()
    [(app, kwargs)] = calls
    assert kwargs == {"host": "127.0.0.1", "port": 9123, "log_config": None}
    assert "http://127.0.0.1:9123/" in capsys.readouterr().out
    served = TestClient(app, base_url="http://127.0.0.1:9123").get("/traces").json()
    assert served == {"files": [{"name": "a.jsonl", "text": "first\n"}]}
