import unittest
import errno
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
            patch.object(cli, "_ensure_port_available"),
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

    def test_cold_frontend_can_become_ready_after_thirty_seconds(self):
        process = FakeProcess()
        with patch.object(cli.time, "monotonic", side_effect=[0, 1, 40]), patch.object(cli.time, "sleep"), patch.object(cli.urllib.request, "urlopen", side_effect=[TimeoutError(), unittest.mock.MagicMock()]):
            cli._wait_for_service("http://127.0.0.1:5173", process, "El frontend", timeout_seconds=90)

    def test_occupied_frontend_port_does_not_launch_api_or_browser(self):
        def occupied(port, name):
            if port == 5173:
                raise RuntimeError("El puerto 5173 está ocupado")
        with patch.object(cli, "_ensure_port_available", side_effect=occupied), patch.object(cli.shutil, "which", return_value="node.exe"), patch.object(cli.subprocess, "Popen") as popen, patch.object(cli.webbrowser, "open_new_tab") as browser:
            with self.assertRaisesRegex(RuntimeError, "5173"):
                cli._serve()
            popen.assert_not_called()
            browser.assert_not_called()

    def test_port_check_reports_busy_port_without_killing_existing_server(self):
        with patch.object(cli.socket, "socket") as socket:
            socket.return_value.__enter__.return_value.bind.side_effect = OSError(errno.EADDRINUSE, "busy")
            with self.assertRaisesRegex(RuntimeError, r"5173.*Ctrl\+C"):
                cli._ensure_port_available(5173, "el frontend")

    def test_successful_http_response_does_not_hide_child_process_exit(self):
        process = FakeProcess()
        with patch.object(process, "poll", side_effect=[None, 1]), patch.object(cli.urllib.request, "urlopen"):
            with self.assertRaisesRegex(RuntimeError, "terminó durante el inicio"):
                cli._wait_for_service("http://127.0.0.1:5173", process, "El frontend")


if __name__ == "__main__":
    unittest.main()
