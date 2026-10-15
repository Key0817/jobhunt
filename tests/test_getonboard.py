import gzip
import http.client
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


def make_job(job_id, countries=(), modality=None, **attrs):
    job = {
        "id": f"job-{job_id}",
        "type": "job",
        "attributes": {"title": f"Dev {job_id}", "countries": list(countries), "remote": False},
        "links": {"public_url": f"https://example.com/jobs/{job_id}"},
    }
    if modality:
        job["attributes"]["remote_modality"] = modality
    job["attributes"].update(attrs)
    return job


def params_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def page_of(url):
    return int(params_of(url)["page"][0])


def category_of(url):
    return urllib.parse.urlparse(url).path.split("/")[-2]


def source(fake, **kwargs):
    """Fuente de pruebas: una categoría, sin pausas ni esperas reales."""
    kwargs.setdefault("categories", ("programacion",))
    kwargs.setdefault("sleep", lambda s: None)
    return GetOnBoardSource(fetch_json=fake, **kwargs)


QUERY = SearchQuery(keywords=(), countries=("CR",), max_age_days=14, residence_country="CR")
GLOBAL_ONLY = SearchQuery(keywords=(), countries=(), residence_country="")


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
    def test_decode_handles_plain_and_gzip(self):
        raw = b'{"data": [1]}'
        self.assertEqual(GetOnBoardSource._decode(raw, None), {"data": [1]})
        self.assertEqual(GetOnBoardSource._decode(gzip.compress(raw), "gzip"), {"data": [1]})
        self.assertEqual(GetOnBoardSource._decode(gzip.compress(raw), "GZIP"), {"data": [1]})

    def test_runs_residence_pass_before_global_pass(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": []}

        source(fake, categories=("a", "b")).fetch(QUERY)
        orden = [(category_of(u), params_of(u).get("country_code")) for u in urls]
        self.assertEqual(orden, [("a", ["cr"]), ("b", ["cr"]), ("a", None), ("b", None)])

    def test_request_url_has_expected_parameters(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": []}

        source(fake).fetch(QUERY)
        con_pais, sin_pais = params_of(urls[0]), params_of(urls[1])
        self.assertIn("/api/v0/categories/programacion/jobs", urls[0])
        for params in (con_pais, sin_pais):
            self.assertEqual(params["per_page"], ["100"])
            self.assertEqual(params["page"], ["1"])
            self.assertEqual(params["lang"], ["es"])
            self.assertEqual(params["expand[]"], ["company"])
        self.assertEqual(con_pais["country_code"], ["cr"])
        self.assertNotIn("country_code", sin_pais)

    def test_residence_pass_keeps_everything_the_server_returns(self):
        def fake(url):
            if "country_code" in params_of(url):
                return {"data": [make_job("local", modality="remote_local"),
                                 make_job("hybrid", modality="hybrid")]}
            return {"data": []}

        postings = source(fake).fetch(QUERY)
        self.assertEqual(sorted(p.external_id for p in postings), ["job-hybrid", "job-local"])

    def test_global_pass_keeps_only_fully_remote(self):
        def fake(url):
            if "country_code" in params_of(url):
                return {"data": []}
            return {"data": [make_job("global", modality="fully_remote"),
                             make_job("local", modality="remote_local"),
                             make_job("hybrid", modality="hybrid"),
                             make_job("onsite", modality="no_remote"),
                             make_job("unknown")]}

        postings = source(fake).fetch(QUERY)
        self.assertEqual([p.external_id for p in postings], ["job-global"])

    def test_without_residence_country_only_the_global_pass_runs(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": []}

        source(fake, categories=("a", "b")).fetch(GLOBAL_ONLY)
        self.assertEqual(len(urls), 2)
        self.assertTrue(all("country_code" not in params_of(u) for u in urls))

    def test_same_offer_in_both_passes_counts_once(self):
        def fake(url):
            return {"data": [make_job(1, modality="fully_remote")]}

        postings = source(fake).fetch(QUERY)
        self.assertEqual(len(postings), 1)

    def test_follows_meta_total_pages_and_pauses_between_requests(self):
        urls, sleeps = [], []

        def fake(url):
            urls.append(url)
            n = page_of(url)
            return {"data": [make_job(f"{n}a", modality="fully_remote"),
                             make_job(f"{n}b", modality="fully_remote")],
                    "meta": {"page": n, "per_page": 100, "total_pages": 2}}

        postings = source(fake, sleep=sleeps.append, pause=0.5).fetch(GLOBAL_ONLY)
        self.assertEqual(len(urls), 2)
        self.assertEqual(len(postings), 4)
        self.assertEqual(sleeps, [0.5])

    def test_stops_on_partial_page_when_there_is_no_meta(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": [make_job(1, modality="fully_remote")]}

        postings = source(fake).fetch(GLOBAL_ONLY)
        self.assertEqual(len(urls), 1)
        self.assertEqual(len(postings), 1)

    def test_stops_on_empty_page(self):
        urls = []

        def fake(url):
            urls.append(url)
            return {"data": [], "meta": {"page": 1, "per_page": 100, "total_pages": 5}}

        postings = source(fake).fetch(GLOBAL_ONLY)
        self.assertEqual(len(urls), 1)
        self.assertEqual(postings, [])

    def test_retries_on_429_then_succeeds(self):
        attempts, sleeps = [], []

        def flaky(url):
            attempts.append(url)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            return {"data": [make_job(1, modality="fully_remote")]}

        with self.assertLogs("jobbot", level="WARNING"):
            postings = source(flaky, sleep=sleeps.append).fetch(GLOBAL_ONLY)
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
                source(always_429, sleep=sleeps.append).fetch(GLOBAL_ONLY)
        self.assertEqual(len(attempts), 3)  # 1 intento + 2 reintentos
        self.assertEqual(sleeps, [2, 4])

    def test_404_is_not_retried(self):
        attempts, sleeps = [], []

        def not_found(url):
            attempts.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with self.assertLogs("jobbot", level="WARNING"):
            with self.assertRaises(urllib.error.HTTPError):
                source(not_found, sleep=sleeps.append).fetch(GLOBAL_ONLY)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(sleeps, [])

    def test_connection_errors_are_retried(self):
        for error in (ConnectionResetError("reset"), http.client.IncompleteRead(b""), TimeoutError("timeout")):
            with self.subTest(error=type(error).__name__):
                attempts = []

                def flaky(url, error=error):
                    attempts.append(url)
                    if len(attempts) == 1:
                        raise error
                    return {"data": [make_job(1, modality="fully_remote")]}

                with self.assertLogs("jobbot", level="WARNING"):
                    postings = source(flaky).fetch(GLOBAL_ONLY)
                self.assertEqual(len(attempts), 2)
                self.assertEqual(len(postings), 1)

    def test_failed_task_is_skipped_and_others_are_kept(self):
        def fake(url):
            category = category_of(url)
            if category == "b":
                raise TimeoutError("read timed out")
            return {"data": [make_job(category, modality="fully_remote")]}

        with self.assertLogs("jobbot", level="WARNING") as logs:
            postings = source(fake, categories=("a", "b", "c")).fetch(GLOBAL_ONLY)
        self.assertEqual(sorted(p.external_id for p in postings), ["job-a", "job-c"])
        self.assertTrue(any("b [global]" in line and "corrida parcial" in line for line in logs.output))

    def test_failure_on_second_page_keeps_first_page(self):
        def fake(url):
            if page_of(url) == 2:
                raise TimeoutError("read timed out")
            return {"data": [make_job("p1", modality="fully_remote")],
                    "meta": {"page": 1, "per_page": 100, "total_pages": 3}}

        with self.assertLogs("jobbot", level="WARNING"):
            postings = source(fake).fetch(GLOBAL_ONLY)
        self.assertEqual([p.external_id for p in postings], ["job-p1"])

    def test_all_requests_failing_raises_last_error(self):
        def fake(url):
            raise TimeoutError("read timed out")

        with self.assertLogs("jobbot", level="WARNING"):
            with self.assertRaises(TimeoutError):
                source(fake, categories=("a", "b")).fetch(GLOBAL_ONLY)

    def test_stops_after_consecutive_failures(self):
        calls = []

        def fake(url):
            calls.append(url)
            raise TimeoutError("read timed out")

        with self.assertLogs("jobbot", level="WARNING"):
            with self.assertRaises(TimeoutError):
                source(fake, categories=("a", "b", "c", "d", "e", "f")).fetch(GLOBAL_ONLY)
        self.assertEqual(len(calls), 9)  # 3 consultas x 3 intentos; las otras 3 ni se intentan

    def test_a_success_resets_the_consecutive_failure_count(self):
        def fake(url):
            category = category_of(url)
            if category in ("a", "c", "e"):
                raise TimeoutError("read timed out")
            return {"data": [make_job(category, modality="fully_remote")]}

        with self.assertLogs("jobbot", level="WARNING"):
            postings = source(fake, categories=("a", "b", "c", "d", "e", "f")).fetch(GLOBAL_ONLY)
        self.assertEqual(sorted(p.external_id for p in postings), ["job-b", "job-d", "job-f"])

    def test_stops_when_time_budget_is_exhausted(self):
        now = [0.0]
        calls = []

        def fake(url):
            calls.append(url)
            now[0] += 100  # cada petición "tarda" 100 segundos
            return {"data": [make_job(category_of(url), modality="fully_remote")]}

        with self.assertLogs("jobbot", level="WARNING") as logs:
            postings = source(fake, categories=("a", "b", "c", "d", "e"),
                              clock=lambda: now[0], max_runtime=250).fetch(GLOBAL_ONLY)
        self.assertEqual(len(calls), 3)  # a 0 s, 100 s y 200 s; a los 300 s ya no hay presupuesto
        self.assertEqual(sorted(p.external_id for p in postings), ["job-a", "job-b", "job-c"])
        self.assertTrue(any("presupuesto de tiempo" in line for line in logs.output))


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
