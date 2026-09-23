"""Legacy provider markup is rejected by the strict text protocol."""
from ai_collusion.env import parse_call

KIMI = ('<|open|>tools<|sep|><|open|>call tool="web_fetch" index="1"<|sep|>'
        '<|open|>argument key="url" type="string"<|sep|>https://example.org/a?x=1&y=2<|close|>argument<|sep|>'
        '<|close|>call<|sep|><|close|>tools<|sep|><|close|>message<|sep|>')


def test_kimi_markup_is_not_a_text_call():
    call = parse_call("Fetching the table now.\n" + KIMI)
    assert call is None


def test_multiple_markup_calls_are_rejected():
    two = ('<|open|>call tool="wait" index="1"<|sep|><|open|>argument key="seconds" type="string"<|sep|>5'
           '<|close|>argument<|sep|><|close|>call<|sep|>'
           '<|open|>call tool="shell" index="2"<|sep|><|open|>argument key="command" type="string"<|sep|>'
           'python3 - <<EOF\nprint(1)\nEOF<|close|>argument<|sep|><|close|>call<|sep|>')
    call = parse_call(two)
    assert call is None


def test_standalone_final_call_after_notes_is_parsed():
    call = parse_call(KIMI + '\nanswer("1, 2, 3")')
    assert (call.tool, call.arg) == ("answer", "1, 2, 3")


def test_unknown_tool_and_prose_are_not_calls():
    assert parse_call('<|open|>call tool="search" index="1"<|sep|><|close|>call<|sep|>') is None
    assert parse_call("No tool call this message.") is None


QWEN = ('\n\n<tool_call>\n<function=shell>\ncurl -s -m 30 "https://example.org/x" -o /tmp/a.json; wc -c /tmp/a.json\n'
        '</function>\n</tool_call>')


def test_qwen_xml_markup_is_not_a_text_call():
    call = parse_call(QWEN)
    assert call is None


def test_qwen_unterminated_block_is_not_a_call():
    assert parse_call("<tool_call>\n<function=shell>\ncurl https://example.org") is None
