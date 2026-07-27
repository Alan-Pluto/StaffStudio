import pytest

from agent_golden.sse import parse_sse_lines


def test_sse_parser_preserves_ids_order_and_multiline_data() -> None:
    events = parse_sse_lines(
        [
            "id: evt-1",
            "event: status",
            'data: {"phase":',
            'data: "routing"}',
            "",
            ": heartbeat comment",
            "event: complete",
            'data: {"ok":true}',
            "",
        ]
    )

    assert [(item.id, item.event, item.data) for item in events] == [
        ("evt-1", "status", {"phase": "routing"}),
        (None, "complete", {"ok": True}),
    ]


def test_sse_parser_rejects_non_object_data() -> None:
    with pytest.raises(TypeError, match="must decode to an object"):
        parse_sse_lines(["data: [1,2]", ""])
