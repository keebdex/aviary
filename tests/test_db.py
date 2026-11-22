import pytest

from aviary.common.db import revision_source


def test_revision_source_joins_job_and_origin():
    assert revision_source("bowerbird", "gdoc") == "bowerbird.gdoc"
    assert revision_source("bowerbird", "alpha-keycaps") == "bowerbird.alpha-keycaps"
    assert revision_source("pelican", "cloudflare-images") == (
        "pelican.cloudflare-images"
    )


@pytest.mark.parametrize(
    ("job", "origin"),
    [("Bowerbird", "gdoc"), ("magpie", "geek.hack"), ("magpie", ""), ("a", "b" * 70)],
)
def test_revision_source_refuses_what_keebdex_would_ignore(job, origin):
    with pytest.raises(ValueError):
        revision_source(job, origin)
