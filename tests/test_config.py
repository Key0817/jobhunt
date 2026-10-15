import os
import tempfile
import unittest

from jobbot.config import load_search_config


def load(text):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "search.yml")
        with open(path, "w", encoding="utf-8") as file:
            file.write(text)
        return load_search_config(path)


class SearchConfigTests(unittest.TestCase):
    def test_reads_residence_country_and_max_age(self):
        query, sources = load("sources: [getonboard]\nresidence_country: mx\nmax_age_days: 30\n")
        self.assertEqual(query.residence_country, "MX")
        self.assertEqual(query.max_age_days, 30)
        self.assertEqual(sources, ["getonboard"])

    def test_defaults_when_missing(self):
        query, sources = load("keywords: [qa]\n")
        self.assertEqual(query.residence_country, "CR")
        self.assertEqual(query.max_age_days, 14)
        self.assertEqual(sources, [])

    def test_empty_residence_country_disables_it(self):
        query, _ = load("residence_country:\n")
        self.assertEqual(query.residence_country, "")
