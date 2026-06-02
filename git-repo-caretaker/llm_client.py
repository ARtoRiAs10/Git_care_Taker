"""
llm_client.py – OpenRouter LLM client with function-calling support.
Uses openai/gpt-oss-120b:free or fallback free models.
"""

import os
import json
import requests
from typing import Optional, Union
from loguru import logger

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model priority list (free models on OpenRouter)
FREE_MODELS = [
    "openai/gpt-4o-mini-search-preview",   # primary choice (free tier)
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-7b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
]

DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")


def call_llm(
    prompt: str,
    system: str = "You are a helpful AI assistant for GitHub repository management.",
    model: Optional[str] = None,
    json_mode: bool = False,
    tools: Optional[list] = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> Union[str, dict]:
    """
    Call the OpenRouter LLM API.

    Args:
        prompt:      User message / task description.
        system:      System prompt.
        model:       Model ID (defaults to DEFAULT_MODEL).
        json_mode:   If True, instruct the model to return raw JSON only.
        tools:       Optional list of OpenAI-style tool definitions.
        max_tokens:  Max tokens to generate.
        temperature: Sampling temperature.

    Returns:
        str  – plain text response.
        dict – parsed JSON when json_mode=True.
    """
    if not OPENROUTER_API_KEY:
        raise EnvironmentError("OPENROUTER_API_KEY is not set.")

    chosen_model = model or DEFAULT_MODEL

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/git-repo-caretaker",
        "X-Title": "Git-Repo-Caretaker",
    }

    if json_mode:
        system += "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown fences, no explanation."

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]

    payload = {
        "model": chosen_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # Try with the chosen model, then fall back to alternatives
    models_to_try = [chosen_model] + [m for m in FREE_MODELS if m != chosen_model]

    for attempt_model in models_to_try:
        payload["model"] = attempt_model
        try:
            logger.debug(f"Calling LLM: {attempt_model}")
            response = requests.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()

            # Check for rate-limit / model-not-available errors
            if "error" in data:
                logger.warning(f"Model {attempt_model} error: {data['error']}. Trying next.")
                continue

            message = data["choices"][0]["message"]

            # Handle tool calls
            if tools and message.get("tool_calls"):
                return message  # caller handles tool execution

            content = message.get("content", "").strip()

            if json_mode:
                # Strip accidental markdown fences
                clean = content.strip("` \n")
                if clean.startswith("json"):
                    clean = clean[4:].strip()
                return json.loads(clean)

            return content

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error with {attempt_model}: {e}")
            if json_mode:
                # Last resort: return raw text and let caller handle it
                return content  # type: ignore
        except requests.HTTPError as e:
            logger.warning(f"HTTP error with {attempt_model}: {e}. Trying next.")
            continue
        except Exception as e:
            logger.error(f"Unexpected error with {attempt_model}: {e}")
            continue

    raise RuntimeError("All models failed. Check your OPENROUTER_API_KEY and model availability.")


def call_llm_with_tools(prompt: str, tools: list, system: str = "", **kwargs) -> dict:
    """Convenience wrapper for tool-use calls. Returns the full message object."""
    return call_llm(prompt, system=system or "You are a GitHub automation agent.", tools=tools, **kwargs)


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result = call_llm("Say hello and tell me what model you are.")
    print(result)
