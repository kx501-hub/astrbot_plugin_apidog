"""Page API regression tests; mock host/clock, exercise real files and generators.

Only the Python standard library is required. This does not start a Dashboard.
"""

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    return module


# Avoid importing AstrBot startup and the HTTP execution engine in these unit tests.
host = types.ModuleType("astrbot.api.web")
host.request = types.SimpleNamespace(json=AsyncMock())
host.json_response = lambda data: types.SimpleNamespace(status_code=200, data=data)
host.error_response = lambda message, status_code=400: types.SimpleNamespace(
    status_code=status_code, data={"message": message}
)
scheduler = types.ModuleType("page_test_plugin.runtime.scheduler")
scheduler.reload_schedules = Mock()
with patch.dict(sys.modules, {
    "astrbot": package("astrbot", ROOT / "nonexistent"),
    "astrbot.api": package("astrbot.api", ROOT / "nonexistent"),
    "astrbot.api.web": host,
    "page_test_plugin": package("page_test_plugin", ROOT),
    "page_test_plugin.core": package("page_test_plugin.core", ROOT / "core"),
    "page_test_plugin.runtime": package("page_test_plugin.runtime", ROOT / "runtime"),
    "page_test_plugin.runtime.scheduler": scheduler,
}):
    api = importlib.import_module("page_test_plugin.api")


class PageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.context = Mock()
        self.page = api.PageAPI(self.context, self.data)
        self.addAsyncCleanup(self.page.close)
        scheduler.reload_schedules.reset_mock()

    async def save(self, resource, body):
        host.request.json = AsyncMock(return_value=body)
        return await self.page._writer(resource)()

    async def test_routes_and_legacy_config_round_trip(self):
        self.assertEqual(self.context.register_web_api.call_count, 10)
        for call in self.context.register_web_api.call_args_list:
            self.assertTrue(call.args[0].startswith("/astrbot_plugin_apidog/"))
            self.assertIn(call.args[2], (["GET"], ["POST"]))
        self.page._write("config", {"api_port": 5787, "api_pwd_hash": "old", "timeout_seconds": 10})
        self.assertEqual(api.loader.load_config(self.data)["timeout_seconds"], 10)
        response = await self.page._reader("config")()
        self.assertEqual(response.data, {"timeout_seconds": 10})
        response = await self.save("config", {"timeout_seconds": 25, "api_pwd_hash": "replacement"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.loader.load_config(self.data)["timeout_seconds"], 25)
        self.assertEqual(self.page._read("config")["api_pwd_hash"], "old")
        self.assertEqual(self.page._read("config")["api_port"], 5787)

    async def test_auth_groups_and_schedule_round_trip(self):
        documents = {
            "auth": {"default": {"type": "bearer", "token": "test-token"}},
            "groups": {"user_groups": {"system": ["scheduler"]}, "group_groups": {}},
            "schedules": {"schedules": [{"api_key": "weather", "cron": "0 9 * * *"}]},
        }
        api.loader.load_auth(self.data)
        api.loader.load_groups(self.data)
        for resource, document in documents.items():
            response = await self.save(resource, document)
            self.assertEqual(response.status_code, 200)
            read = await self.page._reader(resource)()
            self.assertEqual(read.data, document[resource] if resource == "schedules" else document)
        self.assertEqual(api.loader.load_auth(self.data), documents["auth"])
        self.assertEqual(api.loader.load_groups(self.data), documents["groups"])
        scheduler.reload_schedules.assert_called_once_with(self.data)

    async def test_invalid_payload_does_not_overwrite(self):
        for resource, body in [("apis", {"apis": [1]}), ("schedules", {"schedules": {}}),
                               ("groups", {"user_groups": {"admin": "123"}}), ("auth", []),
                               ("config", None)]:
            self.page._write(resource, {"unchanged": True})
            response = await self.save(resource, body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.page._read(resource), {"unchanged": True})

    async def test_atomic_write_failure_preserves_existing_data(self):
        self.page._write("auth", {"unchanged": True})
        with patch.object(api.os, "replace", side_effect=OSError("disk failure")):
            with self.assertLogs("apidog", level="ERROR"):
                response = await self.save("auth", {"new": {}})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.page._read("auth"), {"unchanged": True})
        self.assertEqual(len(list(self.data.iterdir())), 1)

    async def test_api_save_regenerates_commands_tools_and_removal(self):
        entry = self.data / "main.py"
        entry.write_text((ROOT / "main.py").read_text(encoding="utf-8"), encoding="utf-8")
        records = [{"id": "weather", "command": "weather", "url": "https://example.test/{{city}}",
                    "method": "GET", "as_cmd": True, "as_tool": True}]
        with patch.object(api, "_MAIN_PY_PATH", entry), patch.object(self.page, "schedule_reload") as reload:
            api.loader.load_apis(self.data)
            response = await self.save("apis", {"apis": records})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(api.loader.load_apis(self.data), records)
            generated = entry.read_text(encoding="utf-8")
            self.assertIn('@filter.command("weather")', generated)
            self.assertIn("@filter.llm_tool", generated)
            compile(generated, str(entry), "exec")
            reload.assert_called_once()
            await self.save("apis", {"apis": records})
            self.assertEqual(reload.call_count, 1)
            await self.save("apis", {"apis": []})
            self.assertEqual(reload.call_count, 2)
            self.assertNotIn('@filter.command("weather")', entry.read_text(encoding="utf-8"))

    async def test_activation_failure_reports_saved_state(self):
        with patch.object(scheduler, "reload_schedules", side_effect=RuntimeError("unavailable")):
            with self.assertLogs("apidog", level="ERROR"):
                response = await self.save("schedules", {"schedules": []})
        self.assertEqual(response.status_code, 500)
        self.assertIn("已保存", response.data["message"])
        self.assertEqual(self.page._read("schedules"), {"schedules": []})

    async def test_reload_is_coalesced_and_cancelled_on_unload(self):
        manager = types.SimpleNamespace(reload=AsyncMock())
        self.page.reload_trigger = (manager, "astrbot_plugin_apidog")
        self.page.schedule_reload()
        task = self.page._reload_task
        self.page.schedule_reload()
        self.assertIs(task, self.page._reload_task)
        await self.page.close()
        self.assertTrue(task.cancelled())
        manager.reload.assert_not_awaited()

    async def test_reload_uses_plugin_manager(self):
        manager = types.SimpleNamespace(reload=AsyncMock())
        self.page.reload_trigger = (manager, "astrbot_plugin_apidog")
        with patch.object(api.asyncio, "sleep", new=AsyncMock()):
            self.page.schedule_reload()
            await self.page._reload_task
        manager.reload.assert_awaited_once_with("astrbot_plugin_apidog")
        self.assertIsNone(self.page._reload_task)

    async def test_corrupt_file_and_unknown_path(self):
        (self.data / "config.json").write_text("{", encoding="utf-8")
        with self.assertLogs("apidog", level="ERROR"):
            response = await self.page._reader("config")()
        self.assertEqual(response.status_code, 500)
        with self.assertRaises(ValueError):
            self.page._path("../outside")


if __name__ == "__main__":
    unittest.main()
