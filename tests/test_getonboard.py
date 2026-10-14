import unittest
import urllib.error
import urllib.parse
from datetime import timedelta

from jobbot import repo
from jobbot.db import Database
from jobbot.models import SearchQuery
from jobbot.pipeline import collect
from jobbot.sources.getonboard import (
    MAX_DESCRIPTION_CHARS,
    GetOnBoardSource,
    html_to_text,
    to_posting,
)

# Oferta con la misma forma que devolvió la API real (texto reducido y neutral).
SAMPLE = {
    "id": "semi-senior-java-developer-etl-23people-remote-ebb4",
    "type": "job",
    "attributes": {
        "title": "Semi Senior Java Developer / ETL",
        "remote": True,
        "remote_modality": "remote_local",
        "remote_zone": None,
        "countries": ["Remoto"],
        "published_at": 1791892927,
        "lang": "lang_not_specified",
        "min_salary": 2000,
        "max_salary": 2200,
        "description": "<div><strong>Buscamos</strong> dev<br>con Java &amp; SQL</div><ul><li>ETL</li><li>AWS</li></ul>",
        "functions": "<p>Diseñar pipelines</p>",
        "benefits": "",
        "desirable": None,
        "company": {"data": {"id": "23people-cl", "type": "company",
                             "attributes": {"name": "23people"}}},
    },
    "links": {"public_url": "https://www.getonbrd.com/jobs/semi-senior-java-developer-etl-23people-remote-ebb4"},
}


def make_job(job_id, countries=(), **attrs):
    job = {
        "id": f"job-{job_id}",
        "type": "job",
        "attributes": {"title": f"Dev {job_id}", "countries": list(countries), "remote": False},
        "links": {"public_url": f"https://example.com/jobs/{job_id}"},
    }
    job["attributes"].update(attrs)
    return job


def page_of(url):
    return int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["page"][0])


QUERY = SearchQuery(keywords=("python",), countries=("CR",), max_age_days=14)


class HtmlToTextTests(unittest.TestCase):
    def test_strips_tags_and_keeps_line_breaks(self):
        text = html_to_text("<div><strong>Hola</strong><br>mundo</div><ul><li>uno</li><li>dos</li></ul>")
        self.assertEqual(text, "Hola\nmundo\nuno\ndos")

    def test_unescapes_entities(self):
        self.assertEqual(html_to_text("Java &amp; SQL &lt;3"), "Java & SQL <3")

    def test_empty_values(self):
        self.assertEqual(html_to_text(None), "")
        self.assertEqual(html_to_text(""), "")


class ToPostingTests(unittest.TestCase):
    def test_maps_real_sample(self):
        p = to_posting(SAMPLE)
        self.assertEqual(p.source, "getonboard")
        self.assertEqual(p.external_id, "semi-senior-java-developer-etl-23people-remote-ebb4")
        self.assertEqual(p.title, "Semi Senior Java Developer / ETL")
        self.assertEqual(p.company, "23people")
        self.assertTrue(p.url.endswith("23people-remote-ebb4"))
        self.assertIs(p.remote, True)
        self.assertEqual(p.location, "Remoto")
        self.assertIsNone(p.country)  # "Remoto" no es un país
        self.assertEqual(p.posted_at.date().isoformat(), "2026-10-13")
        self.assertIn("Buscamos dev", p.description)
        self.assertIn("Diseñar pipelines", p.description)
        self.assertNotIn("<", p.description)
        self.assertEqual(p.raw["min_salary"], 2000)
        self.assertEqual(p.raw["countries"], ["Remoto"])

    def test_single_country_is_mapped_to_iso_code(self):
        casos = {"Costa Rica": "CR", "México": "MX", "Perú": "PE",
                 "República Dominicana": "DO", "Brasil": "BR", "Chile": "CL"}
        for nombre, codigo in casos.items():
            with self.subTest(pais=nombre):
                self.assertEqual(to_posting(make_job(1, [nombre])).country, codigo)

    def test_multiple_countries_leave_country_empty(self):
        p = to_posting(make_job(1, ["Chile", "Perú"]))
        self.assertIsNone(p.country)
        self.assertEqual(p.location, "Chile, Perú")

    def test_unknown_country_leaves_country_empty(self):
        self.assertIsNone(to_posting(make_job(1, ["Narnia"])).country)

    def test_missing_url_or_title_returns_none(self):
        self.assertIsNone(to_posting({"id": "x", "attributes": {"title": "t"}}))
        self.assertIsNone(to_posting({"id": "x", "attributes": {}, "links": {"public_url": "u"}}))

    def test_absurd_date_is_ignored(self):
        self.assertIsNone(to_posting(make_job(1, [], published_at=10**20)).posted_at)

    def test_collapsed_company_gives_empty_name(self):
        collapsed = {"data": {"id": "c", "type": "company"}}
        self.assertEqual(to_posting(make_job(1, [], company=collapsed)).company, "")

    def test_description_is_truncated(self):
        p = to_posting(make_job(1, [], description="x" * (MAX_DESCRIPTION_CHARS + 500)))
        self.assertEqual(len(p.description), MAX_DESCRIPTION_CHARS)


class FetchTests(unittest.TestCase):
    def test_follows_meta_total_pages_and_pauses_between_requests(self):
        urls, sleeps = [], []

        def fake(url):
            urls.append(url)
            n = page_of(url)
            return {"data": [make_job(f"{n}a", ["Chile"]), make_job(f"{n}b", ["Chile"])],
                    "meta": {"page": n, "per_page": 100, "total_pages": 2}}

        source = GetOnBoardSource(fetch_json=fake, sleep=sleeps.append, pause=0.5)
        postings = source.fetch(QUERY)
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(postings), 4)
        self.assertEqual(sleeps, [0.5])

    def test_stops_on_partial_page_when_there_is_no_meta(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": [make_job(1)]}

        postings = GetOnBoardSource(fetch_json=fake, sleep=lambda s: None).fetch(QUERY)
        self.assertEqual(len(urls), 1)
        self.assertEqual(len(postings), 1)

    def test_stops_on_empty_page(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": [], "meta": {"page": 1, "per_page": 100, "total_pages": 5}}

        postings = GetOnBoardSource(fetch_json=fake, sleep=lambda s: None).fetch(QUERY)
        self.assertEqual(len(urls), 1)
        self.assertEqual(postings, [])

    def test_deduplicates_across_keywords(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": [make_job(1, ["Chile"])]}

        query = SearchQuery(keywords=("python", "java", "qa"), countries=())
        postings = GetOnBoardSource(fetch_json=fake, sleep=lambda s: None).fetch(query)
        self.assertEqual(len(urls), 3)   # una petición por palabra clave
        self.assertEqual(len(postings), 1)  # pero la oferta cuenta una sola vez

    def test_request_url_has_expected_parameters(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": []}

        GetOnBoardSource(fetch_json=fake, sleep=lambda s: None).fetch(
            SearchQuery(keywords=("soporte técnico",), countries=()))
        params = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
        self.assertEqual(params["query"], ["soporte técnico"])
        self.assertEqual(params["per_page"], ["100"])
        self.assertEqual(params["page"], ["1"])
        self.assertEqual(params["expand[]"], ["company"])

    def test_no_keywords_makes_no_requests(self):
        def fake(url):
            raise AssertionError("no debería llamar a la API")

        postings = GetOnBoardSource(fetch_json=fake, sleep=lambda s: None).fetch(
            SearchQuery(keywords=(), countries=()))
        self.assertEqual(postings, [])

    def test_retries_on_429_then_succeeds(self):
        attempts, sleeps = [], []

        def flaky(url):
            attempts.append(url)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            return {"data": [make_job(1)]}

        with self.assertLogs("jobbot", level="WARNING"):
            postings = GetOnBoardSource(fetch_json=flaky, sleep=sleeps.append).fetch(QUERY)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(sleeps, [2])
        self.assertEqual(len(postings), 1)

    def test_gives_up_after_retries(self):
        attempts, sleeps = [], []

        def always_429(url):
            attempts.append(url)
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        with self.assertLogs("jobbot", level="WARNING"):
            with self.assertRaises(urllib.error.HTTPError):
                GetOnBoardSource(fetch_json=always_429, sleep=sleeps.append).fetch(QUERY)
        self.assertEqual(len(attempts), 3)  # 1 intento + 2 reintentos
        self.assertEqual(sleeps, [2, 4])

    def test_404_is_not_retried(self):
        attempts, sleeps = [], []

        def not_found(url):
            attempts.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            GetOnBoardSource(fetch_json=not_found, sleep=sleeps.append).fetch(QUERY)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sleeps, [])


class PipelineIntegrationTests(unittest.TestCase):
    def test_stores_recent_offers_and_drops_old_ones(self):
        fresh = int((repo.now() - timedelta(days=1)).timestamp())
        old = int((repo.now() - timedelta(days=60)).timestamp())
        company = {"data": {"id": "a", "type": "company", "attributes": {"name": "Acme"}}}

        def fake(url):
            return {"data": [
                make_job("new", ["Costa Rica"], published_at=fresh, company=company),
                make_job("old", ["Costa Rica"], published_at=old),
            ]}

        db = Database("sqlite:///:memory:")
        db.init_schema()
        source = GetOnBoardSource(fetch_json=fake, sleep=lambda s: None)
        (summary,) = collect(db, [source], QUERY)
        self.assertEqual((summary.found, summary.inserted, summary.error), (1, 1, None))
        (stored,) = repo.list_jobs(db)
        self.assertEqual((stored["company"], stored["country"], stored["source"]),
                         ("Acme", "CR", "getonboard"))


if __name__ == "__main__":
    unittest.main()