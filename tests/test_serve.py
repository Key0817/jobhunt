import unittest
from unittest.mock import patch

from jobbot import cli


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class ServeTests(unittest.TestCase):
    def test_serve_starts_services_opens_browser_and_stops_on_interrupt(self):
        api_process = FakeProcess()
        frontend_process = FakeProcess()
        with (
            patch.object(cli.shutil, "which", return_value="node.exe"),
            patch.object(
                cli.subprocess,
                "Popen",
                side_effect=[api_process, frontend_process],
            ) as popen,
            patch.object(cli, "_wait_for_service"),
            patch.object(cli.webbrowser, "open_new_tab", return_value=True) as open_browser,
            patch.object(cli.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(cli._serve(), 0)

        self.assertEqual(popen.call_count, 2)
        self.assertEqual(
            popen.call_args_list[0].args[0][1:4],
            ["-m", "uvicorn", "jobbot.api:app"],
        )
        self.assertIn("vite.js", popen.call_args_list[1].args[0][1])
        open_browser.assert_called_once_with("http://127.0.0.1:5173")
        self.assertEqual(api_process.returncode, 0)
        self.assertEqual(frontend_process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
