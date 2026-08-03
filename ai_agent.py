import asyncio
import os
from pathlib import Path

from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None
_system_prompt: str | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.getenv('DEEPSEEK_API_KEY'),
            base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com'),
        )
    return _client


def _build_system_prompt() -> str:
    base = Path(__file__).parent
    soul = (base / 'soul.md').read_text(encoding='utf-8')
    skills = (base / 'skills.md').read_text(encoding='utf-8')
    return f"# 角色人设\n{soul}\n\n# 技能与限制\n{skills}"


def reload_prompt() -> None:
    global _system_prompt
    _system_prompt = _build_system_prompt()


async def ask(user_message: str, item_info: str = '') -> str:
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = _build_system_prompt()

    messages = [{"role": "system", "content": _system_prompt}]
    if item_info:
        messages.append({"role": "system", "content": f"当前商品信息：{item_info}"})
    messages.append({"role": "user", "content": user_message})

    resp = await asyncio.wait_for(
        _get_client().chat.completions.create(
            model=os.getenv('DEEPSEEK_MODEL', 'deepseek-chat'),
            messages=messages,
            max_tokens=500,
        ),
        timeout=20,
    )
    return resp.choices[0].message.content.strip()
