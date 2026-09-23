import json
import subprocess

import pytest

from agent_reach.toolbox import browser_use


def test_rejects_non_youtube_url():
    with pytest.raises(ValueError):
        browser_use.inspect_youtube_page("https://example.com/watch?v=123")


def test_probe_reports_missing_cli(monkeypatch):
    monkeypatch.setattr(browser_use.shutil, "which", lambda _name: None)
    status = browser_use.probe_browser_use()
    assert status.available is False
    assert status.executable is None


def test_inspection_parses_structured_output(monkeypatch):
    monkeypatch.setattr(
        browser_use.shutil,
        "which",
        lambda _name: "/usr/bin/browser-use",
    )

    payload = {
        "resolved_url": "https://www.youtube.com/watch?v=abc",
        "title": "Example - YouTube",
        "description": "Rendered description",
        "visible_text": "Visible page text",
        "comments": ["first", "second"],
    }

    def fake_run(command, **kwargs):
        assert command == ["/usr/bin/browser-use"]
        assert "new_tab" in kwargs["input"]
        assert "ytd-comment-thread-renderer" in kwargs["input"]
        assert "window.scrollTo" in kwargs["input"]
        assert "\nscroll(" not in kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "noise\n"
                + browser_use._RESULT_MARKER
                + json.dumps(payload)
                + "\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(browser_use.subprocess, "run", fake_run)
    result = browser_use.inspect_youtube_page(
        "https://www.youtube.com/watch?v=abc",
        include_comments=True,
        max_comments=2,
    )

    assert result["retrieval_tool"] == "browser-use"
    assert result["mode"] == "read_only"
    assert result["title"] == "Example - YouTube"
    assert result["comments"] == ["first", "second"]
    assert result["provenance"]["source_kind"] == "rendered_youtube_page"


def test_missing_structured_output_is_error(monkeypatch):
    monkeypatch.setattr(browser_use.shutil, "which", lambda _name: "/usr/bin/browser-use")
    monkeypatch.setattr(
        browser_use.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="ok\n", stderr=""
        ),
    )
    with pytest.raises(browser_use.BrowserUseError):
        browser_use.inspect_youtube_page("https://youtu.be/abc")


def test_google_unusual_traffic_redirect_is_error(monkeypatch):
    monkeypatch.setattr(browser_use.shutil, "which", lambda _name: "/usr/bin/browser-use")
    payload = {
        "resolved_url": "https://www.google.com/sorry/index?continue=https://www.youtube.com/",
        "title": "YouTube",
        "description": "",
        "visible_text": "Our systems have detected unusual traffic from your computer network.",
        "comments": [],
    }
    monkeypatch.setattr(
        browser_use.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0,
            stdout=browser_use._RESULT_MARKER + json.dumps(payload) + "\n",
            stderr="",
        ),
    )
    with pytest.raises(browser_use.BrowserUseError, match="unusual-traffic"):
        browser_use.inspect_youtube_page("https://www.youtube.com/watch?v=abc")


def test_unexpected_rendered_host_is_error(monkeypatch):
    monkeypatch.setattr(browser_use.shutil, "which", lambda _name: "/usr/bin/browser-use")
    payload = {
        "resolved_url": "https://example.org/",
        "title": "Unexpected",
        "description": "",
        "visible_text": "unexpected",
        "comments": [],
    }
    monkeypatch.setattr(
        browser_use.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0,
            stdout=browser_use._RESULT_MARKER + json.dumps(payload) + "\n",
            stderr="",
        ),
    )
    with pytest.raises(browser_use.BrowserUseError, match="allowed YouTube origin"):
        browser_use.inspect_youtube_page("https://www.youtube.com/watch?v=abc")
