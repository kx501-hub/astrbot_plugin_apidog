# -*- coding: utf-8 -*-
"""ApiDog AstrBot entry. Plugin class must live in main.py per AstrBot docs."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, List

from astrbot.api import logger as _ab_logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.message_components import Image, Plain, Record, Video

from .api import PageAPI
from .core import CallContext, CallResult, run
from .core.loader import load_apis, load_config
from .core.log_helper import set_apidog_logger
from .core.command_gen import block_content_is_pass, inject_commands_into_main
from .core.tool_gen import (
    apis_for_llm_tools,
    execute_apidog_llm_tool,
    inject_llm_tools_into_main,
    llm_tool_block_content_is_pass,
)
from .runtime import start_scheduler, stop_scheduler


class ApiDogStar(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)
        set_apidog_logger(_ab_logger)
        self._data_dir = Path(StarTools.get_data_dir(None))
        start_scheduler(self._data_dir, send_message=self._send_scheduled_result)
        self._page_api = PageAPI(context, self._data_dir)
        main_path = Path(__file__).resolve()
        try:
            apis = load_apis(self._data_dir)
            _ = load_config(self._data_dir)
        except Exception:
            apis = []
        if block_content_is_pass(main_path):
            try:
                inject_commands_into_main(main_path, apis)
                enabled_cmd = [
                    a for a in apis
                    if a.get("enabled", True) is not False and a.get("as_cmd", False) is True
                ]
                if enabled_cmd:
                    self._pending_reload_after_inject = True
                    _ab_logger.info("已根据配置写回独立指令到 main.py，重载插件后生效")
            except Exception:
                _ab_logger.exception("首次加载写回独立指令失败")
        if llm_tool_block_content_is_pass(main_path):
            try:
                inject_llm_tools_into_main(main_path, apis)
                if apis_for_llm_tools(apis):
                    self._pending_reload_after_inject = True
                    _ab_logger.info("已根据配置写回 LLM 工具（@filter.llm_tool）到 main.py，重载插件后生效")
            except Exception:
                _ab_logger.exception("首次加载写回 LLM 工具失败")

    async def initialize(self) -> None:
        """注册保存后自动重载当前插件的回调（供配置页保存后调用）。"""
        try:
            pm = getattr(self.context, "_star_manager", None)
            if pm is not None and getattr(self, "name", None):
                loop = asyncio.get_running_loop()
                self._page_api.reload_trigger = (pm, self.name)
                if getattr(self, "_pending_reload_after_inject", False):
                    try:
                        plugin_name = self.name

                        async def _reload_self() -> None:
                            await pm.reload(plugin_name)

                        loop.call_soon_threadsafe(
                            lambda: asyncio.ensure_future(_reload_self(), loop=loop)
                        )
                    except Exception:
                        _ab_logger.exception("调度插件自重载失败")
                    finally:
                        if hasattr(self, "_pending_reload_after_inject"):
                            del self._pending_reload_after_inject
        except Exception:
            _ab_logger.debug("ApiDog 未设置自动重载回调: %s", exc_info=True)

    async def terminate(self) -> None:
        """Stop scheduling and pending page-triggered reloads."""
        stop_scheduler()
        await self._page_api.close()
        _ab_logger.info("ApiDog 服务已停止")

    def _result_to_chain(self, result: CallResult) -> tuple[List[Any], List[str]]:
        """Build AstrBot message chain from CallResult; second return is list of temp file paths to delete after send."""
        if result.result_type == "text":
            return [Plain(result.message or "")], []
        if result.result_type == "image" and result.media_url:
            return [Image.fromURL(url=result.media_url)], []
        if result.result_type == "video" and result.media_url:
            return [Video.fromURL(url=result.media_url)], []
        if result.result_type == "audio" and result.media_url:
            return [Record(url=result.media_url)], []
        if result.media_bytes and result.result_type in ("image", "video", "audio"):
            suffix = ".jpg"
            if result.media_content_type:
                if "png" in result.media_content_type:
                    suffix = ".png"
                elif "gif" in result.media_content_type:
                    suffix = ".gif"
                elif "video" in result.media_content_type or result.result_type == "video":
                    suffix = ".mp4"
                elif "audio" in result.media_content_type or result.result_type == "audio":
                    suffix = ".wav"
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                    f.write(result.media_bytes)
                    tmp_path = f.name
                if result.result_type == "image":
                    return [Image.fromFileSystem(path=tmp_path)], [tmp_path]
                return [Plain(f"（媒体已收到，{result.result_type}）")], [tmp_path]
            except Exception:
                return [Plain("媒体已收到，发送暂不支持。")], []
        return [Plain(result.message or "")], []

    async def _send_scheduled_result(self, target_session: str, result: CallResult) -> None:
        """Send scheduled task result to target session (AstrBot: unified_msg_origin)."""
        components, tmp_paths = self._result_to_chain(result)
        message_chain = MessageChain(chain=components)
        try:
            await self.context.send_message(target_session, message_chain)
        finally:
            for p in tmp_paths:
                Path(p).unlink(missing_ok=True)

    async def _run_and_send(
        self,
        event: AstrMessageEvent,
        raw_args: str,
        ctx: CallContext,
    ):
        """Run API with raw_args and yield message results to event. Shared by /api and generated commands."""
        result = await run(self._data_dir, raw_args, ctx)
        if not result.success:
            yield event.plain_result(result.message)
            return
        if result.result_type == "text":
            yield event.plain_result(result.message)
            return
        if result.media_url:
            if result.result_type == "image":
                yield event.image_result(result.media_url)
                return
            if result.result_type == "video":
                try:
                    yield event.chain_result([Video.fromURL(url=result.media_url)])
                except Exception:
                    yield event.plain_result(f"视频链接: {result.media_url}")
                return
            if result.result_type == "audio":
                try:
                    yield event.chain_result([Record(url=result.media_url)])
                except Exception:
                    yield event.plain_result(f"音频链接: {result.media_url}")
                return
        if result.media_bytes and result.result_type in ("image", "video", "audio"):
            suffix = ".jpg"
            if result.media_content_type:
                if "png" in result.media_content_type:
                    suffix = ".png"
                elif "gif" in result.media_content_type:
                    suffix = ".gif"
                elif "video" in result.media_content_type or result.result_type == "video":
                    suffix = ".mp4"
                elif "audio" in result.media_content_type or result.result_type == "audio":
                    suffix = ".wav"
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                    f.write(result.media_bytes)
                    tmp_path = f.name
                try:
                    if result.result_type == "image":
                        yield event.chain_result([Image.fromFileSystem(path=tmp_path)])
                    else:
                        yield event.plain_result(f"（媒体已收到，{result.result_type} 从字节发送暂用链接或文件）")
                finally:
                    Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                yield event.plain_result("媒体内容已收到，但当前平台暂不支持从字节发送。")
            return
        yield event.plain_result(result.message)

    @filter.command("api")
    async def cmd_api(self, event: AstrMessageEvent) -> None:
        """通过接口名调用配置的 API。用法: /api <接口名> [键=值 ...]，例如 /api 天气 city=北京"""
        raw = event.message_str.strip()
        for prefix in ("/api ", "/api\t", "api ", "api\t"):
            if raw.startswith(prefix):
                raw = raw[len(prefix):].strip()
                break
        if not raw:
            yield event.plain_result("用法: /api <接口名> [键=值 ...]，例如 /api 天气 city=北京")
            return
        try:
            user_id = str(event.get_sender_id())
        except Exception:
            user_id = None
        try:
            gid = event.get_group_id()
            group_id = str(gid) if gid is not None else None
        except Exception:
            group_id = None
        ctx = CallContext(user_id=user_id, group_id=group_id)
        async for x in self._run_and_send(event, raw, ctx):
            yield x

    # --- BEGIN GENERATED COMMANDS ---
    pass
    # --- END GENERATED COMMANDS ---

    # --- BEGIN GENERATED LLM TOOLS ---
    pass
    # --- END GENERATED LLM TOOLS ---
