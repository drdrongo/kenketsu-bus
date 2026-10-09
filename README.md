# 献血バス Tokyo

When and where the Red Cross blood donation bus stops in Tokyo, in a form that is easy to read and easy to put in a calendar.

**Site:** https://drdrongo.github.io/kenketsu-bus/

The data comes from the [Tokyo Red Cross Blood Center bus schedule](https://www.bs.jrc.or.jp/ktks/tokyo/place/m1_03_kenbus.html). That page shows the rest of the current month only. Later dates appear here when the Red Cross publishes them. This is not an official Red Cross site.

## Calendar feeds

There is one iCalendar feed for each ward, city, town and village in Tokyo, plus one for all of Tokyo:

```
https://drdrongo.github.io/kenketsu-bus/feeds/edogawa.ics
https://drdrongo.github.io/kenketsu-bus/feeds/all.ics
```

The slugs are in `WARDS` in [`scraper/kenbus.py`](scraper/kenbus.py). Every feed URL exists, even for a month with no bus in that area.

- **Google Calendar:** Other calendars → + → From URL. Google refreshes a subscribed calendar every few hours, so a new date can take up to a day to appear.
- **Apple Calendar / iPhone:** open the `webcal://` link from the site, or File → New Calendar Subscription.

Turn on default notifications for the subscribed calendar if you want a reminder before each bus.

## How it works

A GitHub Actions job ([`update.yml`](.github/workflows/update.yml)) runs at 06:17 and 18:17 JST:

1. `scraper/kenbus.py` fetches the schedule page (one request) and parses it.
2. It writes `site/data/stops.json` and the feeds in `site/feeds/`. Past stops stay for 90 days, so calendar events do not disappear after the day.
3. If the schedule changed, the job commits the new files.
4. The job deploys `site/` to GitHub Pages.

If the page layout changes and the scraper finds no stops, the job fails and keeps the old data. GitHub then sends an email.

The site (`site/index.html`) is a single static page. Your areas and starred places stay in your browser's local storage only.

## Local use

The scraper uses the Python standard library only.

```bash
python3 scraper/kenbus.py
```

```bash
python3 -m unittest discover -s tests
```

```bash
python3 -m http.server 8765 --directory site
```

The python.org installer for macOS has no CA certificates by default. If the fetch fails with `CERTIFICATE_VERIFY_FAILED`, run it with `SSL_CERT_FILE=/etc/ssl/cert.pem`.
