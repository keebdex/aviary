"""What the job summaries of every magpie command share."""

from aviary.magpie.geekhack import topic_url

# Rows listed in the log and job summary; the rest are only counted.
MAX_LISTED = 50


def topic_link(topic_id: int | str) -> str:
    return f"[{topic_id}]({topic_url(int(topic_id))})"
