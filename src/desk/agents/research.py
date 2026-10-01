"""Research role: collect every dataset the feed has for the day. No interpretation here."""

from datetime import date

from desk.feeds.base import Dataset, DatasetKind, FeedAdapter


def gather(feed: FeedAdapter, trading_date: date) -> dict[DatasetKind, Dataset | None]:
    return {kind: feed.fetch(kind, trading_date) for kind in DatasetKind}
