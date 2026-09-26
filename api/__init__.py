"""Configuration APIs hosted by AstrBot's authenticated Dashboard."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from astrbot.api.web import error_response, json_response, request

from ..core import loader
from ..core.command_gen import inject_commands_if_changed
from ..core.tool_gen import inject_llm_tools_if_changed
from ..core.log_helper import logger
from ..runtime import scheduler as scheduler_mod

PLUGIN_NAME = "astrbot_plugin_apidog"
_RESOURCES = ("config", "apis", "schedules", "groups", "auth")
_LEGACY_CONFIG_KEYS = ("api_port", "api_pwd_hash")
_MAIN_PY_PATH = Path(__file__).resolve().parent.parent / "main.py"


class PageAPI:
    def __init__(self, context, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.reload_trigger = None
        self._reload_task = None
        for resource in _RESOURCES:
            context.register_web_api(
                f"/{PLUGIN_NAME}/{resource}", self._reader(resource), ["GET"],
                f"Read ApiDog {resource}",
            )
            context.register_web_api(
                f"/{PLUGIN_NAME}/{resource}/save", self._writer(resource), ["POST"],
                f"Save ApiDog {resource}",
            )

    def _path(self, resource: str) -> Path:
        if resource not in _RESOURCES:
            raise ValueError("未知配置类型")
        path = (self.data_dir / f"{resource}.json").resolve()
        if path.parent != self.data_dir:
            raise ValueError("配置文件路径无效")
        return path

    def _read(self, resource: str):
        path = self._path(resource)
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, resource: str, body: dict) -> None:
        path = self._path(resource)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(body, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _reader(self, resource: str):
        async def read():
            try:
                raw = self._read(resource)
                if not isinstance(raw, dict):
                    raise ValueError("配置文件必须是 JSON 对象")
                if resource in ("apis", "schedules"):
                    result = raw.get(resource, [])
                elif resource == "config":
                    result = {k: v for k, v in raw.items() if k not in _LEGACY_CONFIG_KEYS}
                else:
                    result = raw
                return json_response(result)
            except (OSError, ValueError):
                logger.exception("Failed to read ApiDog %s", resource)
                return error_response("无法读取配置文件，请检查后台日志", status_code=500)
        return read

    def _writer(self, resource: str):
        async def save():
            try:
                body = await request.json(default=None)
                if not isinstance(body, dict):
                    raise ValueError("请求必须是 JSON 对象")
                if resource in ("apis", "schedules"):
                    items = body.get(resource)
                    if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
                        raise ValueError(f"{resource} 必须是对象数组")
                    body = {resource: items}
                if resource == "groups":
                    for key in ("user_groups", "group_groups"):
                        groups = body.get(key, {})
                        if not isinstance(groups, dict) or any(
                            not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)
                            for ids in groups.values()
                        ):
                            raise ValueError(f"{key} 必须是组名到 ID 字符串数组的映射")
            except (ValueError, TypeError) as exc:
                return error_response(str(exc), status_code=400)

            try:
                if resource == "config":
                    # Preserve legacy values for rollback without exposing the old login hash.
                    old = self._read(resource)
                    if not isinstance(old, dict):
                        raise ValueError("配置文件必须是 JSON 对象")
                    body = {k: v for k, v in body.items() if k not in _LEGACY_CONFIG_KEYS}
                    body.update({k: old[k] for k in _LEGACY_CONFIG_KEYS if k in old})
                self._write(resource, body)
            except (OSError, ValueError):
                logger.exception("Failed to save ApiDog %s", resource)
                return error_response("无法保存配置文件，请检查后台日志", status_code=500)

            invalidate = getattr(loader, f"invalidate_{resource}", None)
            if invalidate:
                invalidate(self.data_dir)
            try:
                if resource == "apis":
                    command_changed = inject_commands_if_changed(_MAIN_PY_PATH, body["apis"])
                    tool_changed = inject_llm_tools_if_changed(_MAIN_PY_PATH, body["apis"])
                    if command_changed or tool_changed:
                        self.schedule_reload()
                elif resource == "schedules":
                    scheduler_mod.reload_schedules(self.data_dir)
            except Exception:
                logger.exception("ApiDog configuration saved but activation failed")
                return error_response("配置已保存，但立即生效失败，请在插件管理中重载并检查日志", status_code=500)
            return json_response({"saved": True})
        return save

    def schedule_reload(self) -> None:
        if not self.reload_trigger:
            raise RuntimeError("插件自动重载不可用，请手动重载")
        if self._reload_task and not self._reload_task.done():
            return
        manager, name = self.reload_trigger

        async def reload_later():
            await asyncio.sleep(3)
            # Reload invokes terminate(); avoid cancelling the task from there.
            self._reload_task = None
            try:
                await manager.reload(name)
            except Exception:
                logger.exception("ApiDog automatic reload failed; reload the plugin manually")

        self._reload_task = asyncio.create_task(reload_later())

    async def close(self) -> None:
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None
