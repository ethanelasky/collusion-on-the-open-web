"""Confirmed wiki writes and deterministic reads, shared by reports and campaigns."""


def wiki_save_effects(turn: dict) -> list[dict]:
    return [e for e in (turn.get("env_call") or {}).get("effects") or [] if "wiki_save" in e]


def wiki_activity(record: dict) -> dict:
    episode = record.get("episode") or {}
    turns = episode.get("turns") or []
    return {
        "read_turns": [t.get("turn") for t in turns if t.get("source") == "wiki"],
        "write_turns": [t.get("turn") for t in turns
                        if t.get("source") == "wiki-save" or wiki_save_effects(t)],
        "shell_saves": sum(len(wiki_save_effects(t)) for t in turns),
        "posts": sum(len(posts) for posts in (episode.get("wiki_posts") or {}).values()),
    }
