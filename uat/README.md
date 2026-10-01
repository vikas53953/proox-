# UAT (D07) — mock build

`mock_days.py` runs three simulated days end to end through the real code (only Meta's
server is faked, in memory). `render_html.py` turns the transcript into a phone-friendly
chat page for the owner's D07 check.

```bash
export DESK_TEST_DATABASE_URL=postgresql+psycopg://USER@localhost:5432/postgres  # admin URL
PYTHONPATH=src python uat/mock_days.py out/uat      # creates + drops its own database
python uat/render_html.py out/uat                   # writes out/uat/index.html
```

Days: Thu 1 Oct (onboarding, report, 09:12 update, TEXT, feedback, correction, an
uninvited number), Fri 2 Oct (NSE holiday: nothing sent), Mon 5 Oct (feed down, outside
the 24h window, then STOP). D07 itself is the owner's check, not something the builder
can pass.
