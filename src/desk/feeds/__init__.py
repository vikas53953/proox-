"""Market-data adapters. Every feed (fixture now; NSE public, then broker later) plugs in
behind the same FeedAdapter interface, so lenses never know which one is in use."""
