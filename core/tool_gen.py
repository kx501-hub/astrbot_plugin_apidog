# -*- coding: utf-8 -*-
"""LLM 工具：注入 main.py 的 @filter.llm_tool 代码块 + 运行时 execute_apidog_llm_tool。"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .log_helper import logger
from .placeholders import ParamSpec, infer_tool_params
from .types import CallContext

try:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import Image, Plain, Record, Video
    _HAS_MESSAGE_COMPONENTS = True
except ImportError:
    _HAS_MESSAGE_COMPONENTS = False
    MessageChain = None  # type: ignore[misc, assignment]

_LLM_BEGIN_MARKER = "# --- BEGIN GENERATED LLM TOOLS ---"
_LLM_END_MARKER = "# --- END GENERATED LLM TOOLS ---"


def apis_for_llm_tools(apis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return APIs that are enabled and have as_tool === true (default off)."""
    return [
        a for a in apis
        if a.get("enabled", True) is not False and a.get("as_tool", False) is True
    ]


def _llm_safe_method_name(index: int) -> str:
    return f"llm_tool_{index}"


def _llm_one_line(s: str) -> str:
    return " ".join((s or "").split())


def _py_param_name(name: str) -> str:
    name = (name or "").strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return name
    s = re.sub(r"\W", "_", name)
    if not s or s[0].isdigit():
        s = f"p_{s}"
    return s


def _tool_params_desc_map(api: dict[str, Any], specs: list[ParamSpec]) -> dict[str, str]:
    raw = api.get("tool_params_desc")
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return {s.name: "" for s in specs}
    for spec in specs:
        v = raw.get(spec.name)
        out[spec.name] = str(v).strip() if v is not None else ""
    return out


def _build_llm_tool_methods(apis: list[dict[str, Any]]) -> str:
    """Build class-body lines (4-space indent) for ApiDogStar."""
    enabled = apis_for_llm_tools(apis)
    lines: list[str] = []
    for i, api in enumerate(enabled):
        api_key = api.get("id") or api.get("command") or ""
        if not api_key or not isinstance(api_key, str):
            continue
        desc = _llm_one_line(
            (api.get("description") or "").strip() or f"调用接口：{api_key}"
        )
        name_literal = json.dumps(api_key, ensure_ascii=False)
        method = _llm_safe_method_name(i)
        specs = infer_tool_params(api)
        if specs:
            desc_map = _tool_params_desc_map(api, specs)
            sig_params: list[str] = []
            doc_params: list[tuple[str, str, str]] = []  # (orig_key, ident, desc)
            raw_parts: list[str] = []

            for spec in specs:
                ident = _py_param_name(spec.name)
                sig_params.append(f"{ident}: str = ''")
                d = desc_map.get(spec.name) or spec.name
                doc_params.append((spec.name, ident, d))
                key_lit = json.dumps(spec.name, ensure_ascii=False)
                raw_parts.append(f"({ident}.strip() and f\"{spec.name}={{{ident}.strip()}}\")")

            sig = ", ".join(sig_params)
            lines.append(f"    @filter.llm_tool(name={name_literal})")
            lines.append(f"    async def {method}(self, event: AstrMessageEvent, {sig}) -> str:")
            lines.append('        """')
            lines.append(f"        {desc}")
            lines.append("")
            lines.append("        Args:")
            for orig_key, ident, d in doc_params:
                lines.append(f"            {ident}(string): {d}")
            lines.append('        """')
            lines.append(f"        raw = ' '.join([x for x in [{', '.join(raw_parts)}] if x])")
            lines.append(f"        return await execute_apidog_llm_tool(self, event, {name_literal}, raw)")
            lines.append("")
        else:
            lines.append(f"    @filter.llm_tool(name={name_literal})")
            lines.append(f"    async def {method}(self, event: AstrMessageEvent) -> str:")
            lines.append('        """')
            lines.append(f"        {desc}")
            lines.append('        """')
            lines.append(f"        return await execute_apidog_llm_tool(self, event, {name_literal}, '')")
            lines.append("")
    return "\n".join(lines) if lines else "    pass"


def _llm_current_block_inner(main_path: Path) -> str | None:
    if not main_path.is_file():
        return None
    text = main_path.read_text(encoding="utf-8")
    begin = "    " + _LLM_BEGIN_MARKER
    end = "    " + _LLM_END_MARKER
    pattern = re.compile(
        re.escape(begin) + r"(.*?)" + re.escape(end),
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return None
    return m.group(1)


def llm_tool_block_content_is_pass(main_path: Path) -> bool:
    inner = _llm_current_block_inner(main_path)
    return inner is not None and inner.strip() == "pass"


def inject_llm_tools_into_main(main_path: Path, apis: list[dict[str, Any]]) -> None:
    if not main_path.is_file():
        return
    text = main_path.read_text(encoding="utf-8")
    begin = "    " + _LLM_BEGIN_MARKER
    end = "    " + _LLM_END_MARKER
    pattern = re.compile(
        re.escape(begin) + r".*?" + re.escape(end),
        re.DOTALL,
    )
    if not pattern.search(text):
        return
    inner = _build_llm_tool_methods(apis)
    block = f"    {_LLM_BEGIN_MARKER}\n{inner}\n    {_LLM_END_MARKER}"
    new_text = pattern.sub(block, text, count=1)
    if new_text == text:
        return
    main_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = None, None
    try:
        import os
        import tempfile as tf

        fd, tmp = tf.mkstemp(dir=main_path.parent, prefix=".main.", suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp, main_path)
    except Exception:
        if fd is not None and tmp is not None:
            try:
                import os

                os.unlink(tmp)
            except OSError:
                pass
        raise
    cache_dir = main_path.parent / "__pycache__"
    if cache_dir.is_dir():
        for f in cache_dir.glob("main.*.pyc"):
            f.unlink(missing_ok=True)
    logger.info("LLM 工具已注入 main.py（@filter.llm_tool，保存配置后将自动重载）")


def inject_llm_tools_if_changed(main_path: Path, apis: list[dict[str, Any]]) -> bool:
    new_inner = _build_llm_tool_methods(apis)
    current_inner = _llm_current_block_inner(main_path)
    if current_inner is not None and current_inner.strip() == new_inner.strip():
        return False
    inject_llm_tools_into_main(main_path, apis)
    return True


async def execute_apidog_llm_tool(
    star: Any,
    event: Any,
    api_key: str,
    args: str,
) -> str:
    """供 @filter.llm_tool 生成方法调用；行为与原先 FunctionTool handler 一致。"""
    from . import run

    data_dir: Path = star._data_dir
    args_str = (args or "").strip()
    raw_args = f"{api_key} {args_str}".strip()
    user_id: str | None = None
    group_id: str | None = None
    try:
        if hasattr(event, "get_sender_id"):
            try:
                user_id = str(event.get_sender_id())
            except Exception:
                pass
        if hasattr(event, "get_group_id"):
            try:
                gid = event.get_group_id()
                group_id = str(gid) if gid is not None else None
            except Exception:
                pass
    except Exception:
        pass
    call_ctx = CallContext(user_id=user_id, group_id=group_id)
    result = await run(data_dir, raw_args, call_ctx)
    if not result.success:
        return result.message or "调用失败"
    if result.result_type == "text":
        return result.message or "(无文本返回)"
    if _HAS_MESSAGE_COMPONENTS and MessageChain is not None and hasattr(event, "send"):
        components: list[Any] = []
        temp_paths: list[str] = []
        try:
            if result.result_type == "image" and result.media_url:
                components = [Image.fromURL(url=result.media_url)]
            elif result.result_type == "video" and result.media_url:
                components = [Video.fromURL(url=result.media_url)]
            elif result.result_type == "audio" and result.media_url:
                components = [Record(url=result.media_url)]
            elif result.media_bytes and result.result_type in ("image", "video", "audio"):
                suffix = ".jpg"
                if result.media_content_type:
                    if "png" in (result.media_content_type or ""):
                        suffix = ".png"
                    elif "gif" in (result.media_content_type or ""):
                        suffix = ".gif"
                    elif "video" in (result.media_content_type or "") or result.result_type == "video":
                        suffix = ".mp4"
                    elif "audio" in (result.media_content_type or "") or result.result_type == "audio":
                        suffix = ".wav"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
                    f.write(result.media_bytes)
                    temp_paths.append(f.name)
                if result.result_type == "image":
                    components = [Image.fromFileSystem(path=temp_paths[0])]
                else:
                    components = [Plain(f"（已收到{result.result_type}媒体）")]
            if components:
                chain = MessageChain(chain=components, type="tool_direct_result")
                await event.send(chain)
                for p in temp_paths:
                    Path(p).unlink(missing_ok=True)
                desc = {"image": "图片", "video": "视频", "audio": "音频"}.get(
                    result.result_type, "媒体"
                )
                return f"已向用户发送{desc}。"
        except Exception:
            logger.exception("工具发送媒体到会话失败")
            for p in temp_paths:
                Path(p).unlink(missing_ok=True)
    if result.media_url:
        return f"媒体发送失败，链接：{result.media_url}"
    return result.message or "接口已返回媒体但发送失败，且未提供链接。"
