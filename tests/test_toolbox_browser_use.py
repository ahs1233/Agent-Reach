import ast
import json
import shutil
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
        assert "goto_url" in kwargs["input"]
        assert "new_tab" not in kwargs["input"]
        assert "wait_for_element" in kwargs["input"]
        assert "ytInitialPlayerResponse" in kwargs["input"]
        assert "ytd-comment-thread-renderer" in kwargs["input"]
        assert "window.scrollBy" in kwargs["input"]
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


def test_script_uses_embedded_video_metadata_fallback():
    script = browser_use._build_script(
        "https://www.youtube.com/watch?v=abc",
        include_comments=False,
        max_comments=0,
    )

    assert "goto_url" in script
    assert "wait_for_element" in script
    assert "videoDetails" in script
    assert "shortDescription" in script
    assert "meta[name=\"description\"]" in script


def test_comment_script_retries_lazy_loading():
    script = browser_use._build_script(
        "https://www.youtube.com/watch?v=abc",
        include_comments=True,
        max_comments=5,
    )

    assert "for _ in range(6)" in script
    assert "scrollIntoView" in script
    assert "window.scrollBy" in script
    assert "ytd-comment-thread-renderer #content-text" in script


@pytest.mark.parametrize("rendered, expected", [
    ("The real video description", "The real video description"),
    ("", ""),
])
def test_description_rejects_generic_youtube_metadata(rendered, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the browser JavaScript fixture")
    script = browser_use._build_script("https://youtu.be/abc", include_comments=False, max_comments=0)
    assignment = next(n for n in ast.parse(script).body
                      if isinstance(n, ast.Assign) and n.targets[0].id == "description")
    expression = assignment.value.values[0].args[0].value
    fixture = """
const rendered = JSON.parse(process.argv[1]);
global.window = {ytInitialPlayerResponse: {playabilityStatus: {
  reason: "Sign in to confirm you're not a bot"
}}};
global.document = {querySelector(selector) {
  if (selector.includes('meta[')) return {content:
    'Enjoy the videos and music you love, upload original content, and share it all with friends, family, and the world on YouTube.'};
  return {innerText: rendered};
}};
"""
    result = subprocess.run([node, "-e", fixture + "console.log(JSON.stringify(" + expression + "));",
                             json.dumps(rendered)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == expected


def test_script_accumulates_delayed_comments_and_preserves_description(capsys):
    batches = iter([
        [{"author": "A", "text": "first"}],
        [{"author": "A", "text": "first"}, {"author": "B", "text": "second"}],
        [{"author": "C", "text": "third"}],
    ])
    calls = {"batches": 0}

    def js(expression):
        if "querySelectorAll" in expression:
            calls["batches"] += 1
            return next(batches)
        if "shortDescription" in expression:
            if "document.body" in expression:
                return "Sign in to confirm you're not a bot"
            return "Real description" if not calls["batches"] else ""
        if "details?.title" in expression:
            return "Video title"
        if "location.href" in expression:
            return "https://www.youtube.com/watch?v=abc"
        return True

    namespace = {"js": js}
    for name in ("ensure_real_tab", "goto_url", "wait_for_load", "wait_for_element", "wait"):
        namespace[name] = lambda *args, **kwargs: None
    exec(browser_use._build_script("https://youtu.be/abc", include_comments=True, max_comments=3), namespace)
    payload = json.loads(capsys.readouterr().out.split(browser_use._RESULT_MARKER)[1])
    assert payload["description"] == "Real description"
    assert payload["comments"] == ["first", "second", "third"]
    assert calls["batches"] == 3


def test_comments_disabled_never_queries_comment_dom():
    script = browser_use._build_script("https://youtu.be/abc", include_comments=False, max_comments=5)
    assert "querySelectorAll" not in script
