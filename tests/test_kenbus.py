import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scraper'))

import kenbus  # noqa: E402

FIXTURE = (Path(__file__).parent / 'fixture.html').read_text(encoding='utf-8')
TODAY = dt.date(2026, 12, 30)
NOW = '2026-12-30T06:00:00+09:00'


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.stops = kenbus.parse(FIXTURE, TODAY)

    def test_rows(self):
        self.assertEqual([s['venue'] for s in self.stops],
                         ['東西線西葛西駅 南口', 'イオンモール日の出', 'アリオ北砂店'])

    def test_year_comes_from_the_date_picker(self):
        self.assertEqual([s['date'] for s in self.stops], ['2026-12-31', '2026-12-31', '2027-01-01'])

    def test_venue_details(self):
        stop = self.stops[0]
        self.assertEqual(stop['city'], '江戸川区')
        self.assertEqual(stop['slug'], 'edogawa')
        self.assertEqual(stop['address'], '東京都江戸川区西葛西6-14')
        self.assertEqual(stop['map'], 'https://maps.app.goo.gl/example1')
        self.assertEqual(stop['reserve'],
                         'https://www.kenketsu.jp:443/ReservationByTypeConfirm?placeMst=abc&sy=2026')
        self.assertEqual(stop['sessions'], [['10:00', '11:30'], ['13:00', '16:00']])

    def test_county_prefix_and_notes(self):
        stop = self.stops[1]
        self.assertEqual(stop['city'], '日の出町')
        self.assertEqual(stop['slug'], 'hinode')
        self.assertEqual(stop['notes'], ['400mL献血のみ'])
        self.assertEqual(stop['sessions'], [['11:00', '15:30']])

    def test_unparseable_hours(self):
        self.assertEqual(self.stops[2]['hours'], ['未定'])
        self.assertEqual(self.stops[2]['sessions'], [])


class GuessYearTest(unittest.TestCase):
    def test_wraps_around_new_year(self):
        self.assertEqual(kenbus.guess_year(1, 5, dt.date(2026, 12, 30)), 2027)
        self.assertEqual(kenbus.guess_year(12, 30, dt.date(2027, 1, 2)), 2026)
        self.assertEqual(kenbus.guess_year(11, 1, dt.date(2026, 10, 9)), 2026)


class MergeTest(unittest.TestCase):
    def stop(self, id, date, first_seen=None):
        return {'id': id, 'date': date, 'city': '江戸川区', 'venue': id, 'first_seen': first_seen}

    def test_page_replaces_its_own_dates_and_history_keeps_older_ones(self):
        previous = [
            self.stop('too-old', '2026-09-01', NOW),
            self.stop('past', '2026-12-29', NOW),
            self.stop('kept', '2026-12-31', '2026-12-01T06:00:00+09:00'),
            self.stop('cancelled', '2026-12-31', NOW),
        ]
        scraped = [self.stop('kept', '2026-12-31'), self.stop('new', '2027-01-01')]
        merged = kenbus.merge(previous, scraped, TODAY, NOW)
        self.assertEqual([s['id'] for s in merged], ['past', 'kept', 'new'])
        self.assertEqual(merged[1]['first_seen'], '2026-12-01T06:00:00+09:00')
        self.assertEqual(merged[2]['first_seen'], NOW)


class CalendarTest(unittest.TestCase):
    def test_lines_fold_at_75_octets_without_splitting_characters(self):
        folded = kenbus.fold('SUMMARY:' + '献' * 60)
        for line in folded.split('\r\n'):
            self.assertLessEqual(len(line.encode()), 75)
        self.assertEqual(folded.replace('\r\n ', ''), 'SUMMARY:' + '献' * 60)

    def test_event_times_are_utc(self):
        stops = kenbus.merge([], kenbus.parse(FIXTURE, TODAY), TODAY, NOW)
        ics = kenbus.calendar('test', stops).replace('\r\n ', '')
        self.assertIn('DTSTART:20261231T010000Z', ics)
        self.assertIn('DTEND:20261231T070000Z', ics)
        self.assertIn('DTSTART;VALUE=DATE:20270101', ics)
        self.assertIn('LOCATION:東京都江戸川区西葛西6-14', ics)
        self.assertEqual(ics.count('BEGIN:VEVENT'), 3)

    def test_text_is_escaped(self):
        self.assertEqual(kenbus.ics_text('a,b;c\\d\ne'), 'a\\,b\\;c\\\\d\\ne')


if __name__ == '__main__':
    unittest.main()
