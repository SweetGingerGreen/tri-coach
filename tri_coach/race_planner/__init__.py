"""
Race planner, P0: collect Chinese triathlon race listings, entry fees and prize
tables into one local SQLite database, plus the athlete's own race history.

Sources (each has its own module under ``sources/``):

- zuicool.com: the triathlon list and each event's categories and entry fees.
- chinatriathlon.com: the same catalogue as zuicool (one operator), read from
  its sitemap; adds event type and the date range.
- wildto.com: CTSA-partner listing whose events carry the full competition
  rules as HTML, including the prize tables.

Data lives outside the repository (see ``store.default_data_dir``): the
database, raw page snapshots and the athlete's private seed file. Nothing
personal or scraped is committed.
"""
