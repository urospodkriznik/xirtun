"""Tests for the nutrition watchlist: the risks the agent surfaces beyond what the
user asked to track, and the rotation that keeps it from becoming weekly noise."""

from datetime import datetime, timedelta, timezone

from xirtun import watchlist

NOW = datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc)


def _items():
    return [
        {"name": "iodine", "why": "vegan base; no dairy, seaweed or iodised salt logged"},
        {"name": "sodium", "why": "father's-side hypertension + frequent processed vegan foods"},
        {"name": "selenium", "why": "vegan base; brazil nuts absent from the diary"},
    ]


def test_write_and_format_lists_items_with_reasons(conn):
    out = watchlist.write(conn, _items(), now=NOW)
    assert "3 items" in out

    formatted = watchlist.format_watchlist(conn, now=NOW)
    assert "iodine" in formatted and "hypertension" in formatted
    assert "never reviewed" in formatted
    assert "DUE for in-depth review this week" in formatted


def test_empty_watchlist_tells_agent_to_derive_one(conn):
    out = watchlist.format_watchlist(conn, now=NOW)
    assert "No watchlist yet" in out
    assert "FAMILY HISTORY" in out          # the input the report otherwise never uses


def test_rotation_covers_least_recently_reviewed(conn):
    watchlist.write(conn, _items(), now=NOW)

    # Cover the first two; next week the untouched one must come up.
    watchlist.mark_reviewed(conn, ["iodine", "sodium"], now=NOW)
    due = [i["name"] for i in watchlist.due_items(watchlist.items(conn))]
    assert due[0] == "selenium"             # never reviewed sorts first

    watchlist.mark_reviewed(conn, ["selenium"], now=NOW + timedelta(days=7))
    due = [i["name"] for i in watchlist.due_items(watchlist.items(conn))]
    assert set(due) == {"iodine", "sodium"}  # oldest reviews come round again


def test_rederiving_preserves_rotation_dates(conn):
    """Re-deriving after a profile change must not reset every item to 'never
    reviewed' — that would make the whole list look due and restart the nagging."""
    watchlist.write(conn, _items(), now=NOW)
    watchlist.mark_reviewed(conn, ["iodine"], now=NOW)

    watchlist.write(conn, _items() + [{"name": "vitamin d", "why": "indoors more"}], now=NOW)
    by_name = {i["name"]: i for i in watchlist.items(conn)}
    assert by_name["iodine"]["last_reviewed"] is not None      # kept
    assert by_name["vitamin d"]["last_reviewed"] is None       # genuinely new


def test_write_rejects_items_without_reasons_and_caps_length(conn):
    assert "ERROR" in watchlist.write(conn, [{"name": "iron"}], now=NOW)   # no `why`
    assert watchlist.items(conn) == []

    many = [{"name": f"n{i}", "why": "because"} for i in range(watchlist.MAX_ITEMS + 5)]
    watchlist.write(conn, many, now=NOW)
    assert len(watchlist.items(conn)) == watchlist.MAX_ITEMS


def test_mark_reviewed_rejects_unknown_names(conn):
    watchlist.write(conn, _items(), now=NOW)
    assert "ERROR" in watchlist.mark_reviewed(conn, ["magnesium"], now=NOW)


def test_agent_tools_expose_the_watchlist(conn, tmp_path):
    from xirtun.agent.tools import ToolContext, build_dispatch

    ctx = ToolContext(conn=conn, diet_path=tmp_path / "d.md",
                      observations_path=tmp_path / "o.md", now=NOW)
    dispatch = build_dispatch(ctx)

    assert "No watchlist yet" in dispatch["get_watchlist"]({})
    dispatch["set_watchlist"]({"items": _items()})
    assert "iodine" in dispatch["get_watchlist"]({})
    assert "Marked reviewed" in dispatch["mark_watchlist_reviewed"]({"names": ["iodine"]})
