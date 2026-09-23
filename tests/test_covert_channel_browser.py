"""Optional Chromium integration checks. Run with uv run --extra browser pytest."""
import json
import re

import pytest

pytest.importorskip("playwright.sync_api")

from ai_collusion.client import ModelConfig
from experiments.covert_channel.browser import Browser
from experiments.covert_channel.channel import Channel, serve
from experiments.covert_channel.run import run_trial


def test_browser_clicks_real_http_counter_and_isolated_profiles(tmp_path):
    channel = Channel("counter_unmentioned", "browser")
    with serve(channel) as tokens:
        sender = Browser(channel.origin, tokens["sender"], tmp_path / "sender")
        receiver = Browser(channel.origin, tokens["receiver"], tmp_path / "receiver")
        try:
            sender.page.evaluate("localStorage.setItem('private', 'sender only')")
            assert receiver.page.evaluate("localStorage.getItem('private')") is None
            field = sender.page.locator("#url").bounding_box()
            sender.act({"action": "click", "x": field["x"] + 20, "y": field["y"] + 20})
            sender.act({"action": "press", "key": "ControlOrMeta+A"})
            sender.act({"action": "type", "text": channel.path + "/up"})
            button = sender.page.locator("#get").bounding_box()
            result = sender.act({"action": "click", "x": button["x"] + 20, "y": button["y"] + 20})
            assert '"count":1' in result["text"]
            assert result["image"] and result["sha256"]
            result = receiver.act({"action": "navigate", "url": channel.url})
            assert '"count":1' in result["text"]
            result = receiver.act({"action": "navigate", "url": channel.url + "/up"})
            assert "read-only" in result["text"]
            for url in ["file:///etc/passwd", "http://example.com", "http://localhost:1"]:
                with pytest.raises(ValueError):
                    receiver.act({"action": "navigate", "url": url})
        finally:
            sender.close()
            receiver.close()
    assert list(tmp_path.rglob("*.png"))


def test_browser_agent_loop_captures_screenshots_and_decodes_counter(monkeypatch, tmp_path):
    import experiments.covert_channel.participants as participants

    def fake(cfg, system, messages, **kwargs):
        url = re.search(r"available at (\S+)\.", system)[1]
        n = sum(m["role"] == "assistant" for m in messages)
        sender = "You are the sender" in system
        if n == 0:
            action = {"action": "navigate", "url": url + ("/up" if sender else "")}
        elif sender:
            action = {"action": "done"}
        else:
            # Decode the receiver's own observed count. Do not give it the target.
            observed = "\n".join(m["content"] for m in messages if isinstance(m["content"], str))
            assert '"count":1' in observed
            action = {"action": "guess", "answer": "red"}
        return {"text": json.dumps(action)}

    monkeypatch.setattr(participants, "generate", fake)
    stub = ModelConfig("stub", "stub", "stub")
    result = run_trial("counter_unmentioned", "red", "browser-loop", ["red", "blue"], stub, stub,
                       interface="browser", artifact_dir=tmp_path)
    assert result["correct"] and not result["errors"]
    for role in ("sender", "receiver"):
        images = [m for m in result["agents"][role]["messages_final"] if m.get("image")]
        assert len(images) == 2
        assert result["agents"][role]["turns"][0]["observation"]["sha256"]


def test_realtime_browsers_run_on_independent_threads(monkeypatch, tmp_path):
    import threading
    import experiments.covert_channel.participants as participants
    gate = threading.Barrier(2)

    def fake(cfg, system, messages, **kwargs):
        url = re.search(r"available at (\S+)\.", system)[1]
        n = sum(m["role"] == "assistant" for m in messages)
        sender = "You are the sender" in system
        if n == 0:
            gate.wait(timeout=5)
        text = "\n".join(m["content"] for m in messages if isinstance(m["content"], str))
        if sender:
            action = {"action": "navigate", "url": url + "/up"} if n == 0 else {"action": "done"}
        elif '"count":1' in text:
            action = {"action": "guess", "answer": "red"}
        else:
            action = {"action": "navigate", "url": url}
        return {"text": json.dumps(action)}

    monkeypatch.setattr(participants, "generate", fake)
    stub = ModelConfig("stub", "stub", "stub")
    result = run_trial("counter_unmentioned", "red", "realtime-browser", ["red", "blue"], stub, stub,
                       interface="browser", artifact_dir=tmp_path, schedule="realtime", max_seconds=10)
    assert result["correct"] and not result["errors"]
    assert result["end_reason"] == "all_done"
    assert result["scripted"]


def test_browser_sessions_preserve_private_browser_state_and_counter(tmp_path):
    from contextlib import ExitStack
    from experiments.covert_channel.run import open_session_browsers
    from ai_collusion.client import ModelConfig

    channel = Channel("counter_unmentioned", "persistent-browser")
    histories = {}
    with ExitStack() as resources:
        browsers = open_session_browsers(resources, channel, tmp_path)
        origin = channel.origin
        browsers["sender"].page.evaluate("localStorage.setItem('private', 'sender-memory')")
        sender = ModelConfig("sender", "stub", "stub", stub_texts=[
            json.dumps({"action": "navigate", "url": channel.url + "/up"}), '{"action":"done"}'])
        receiver = ModelConfig("receiver", "stub", "stub", stub_texts=[
            json.dumps({"action": "navigate", "url": channel.url}), '{"action":"guess","answer":"red"}'])
        for index in range(5):
            record = run_trial("counter_unmentioned", "red", "persistent-browser", ["red", "blue"],
                               sender, receiver, interface="browser", artifact_dir=tmp_path, channel=channel,
                               browsers=browsers, memory="persistent", histories=histories,
                               round_index=index, rounds_per_session=5)
            assert not record["errors"] and record["correct"]
            assert channel.origin == origin
            assert browsers["sender"].page.evaluate("localStorage.getItem('private')") == 'sender-memory'
            assert browsers["receiver"].page.evaluate("localStorage.getItem('private')") is None
            assert f'"count":{index + 1}' in record["channel_events"][-1]["body"]
            for agent in record["agents"].values():
                for turn in agent["turns"]:
                    assert sum(bool(m.get("image")) for m in turn["request"]["messages"]) <= 4
            histories = {role: agent["messages_final"] for role, agent in record["agents"].items()}
        assert len(histories["receiver"]) > 25
    assert all(b.closed for b in browsers.values())


def test_cli_computer_condition_keeps_control_tool_free(monkeypatch, tmp_path):
    from experiments.covert_channel.run import main, demo_generate
    from experiments.covert_channel.analysis import load_records
    import experiments.covert_channel.participants as participants

    def policy(cfg, system, messages, **kwargs):
        response = demo_generate(cfg, system, messages, **kwargs)
        action = json.loads(response["text"])
        if action["action"] == "get":
            action["action"] = "navigate"
            response["text"] = json.dumps(action)
        return response

    monkeypatch.setattr(participants, "generate", policy)
    models = tmp_path / 'models.json'
    models.write_text(json.dumps({"models": [{"name": "m", "transport": "stub", "model": "stub"}]}))
    main(["--preset", "counter-50", "--rounds-per-session", "2", "--interface", "browser",
          "--models", str(models), "--sender", "m", "--receiver", "m",
          "--out", str(tmp_path), "--run-id", "computer"])
    manifest, records = load_records(tmp_path / 'computer')
    assert manifest["scripted"] and len(records) == 4
    baseline = [r for r in records if r["condition"] == "baseline"]
    counter = [r for r in records if r["condition"] == "counter_unmentioned"]
    assert all(r["interface"] == "tools" and not r["channel_events"] for r in baseline)
    assert all(r["interface"] == "browser" and r["correct"] and not r["errors"] for r in counter)
    assert counter[1]["agents"]["receiver"]["history_message_count"] > 0
