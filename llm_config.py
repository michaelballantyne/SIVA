"""Single source of truth for LLM access.

Every LLM call in VisLang goes through here, so the model, endpoint, and key
loading live in exactly one place. We talk to the LANL OpenAI-compatible
gateway through dspy/litellm (the "openai/" model prefix + an api_base). The
key is read from MY_API_KEY, loaded from a .env beside this file if present.

To point at a different gateway/model/key without touching code, set
VISLANG_LLM_MODEL / VISLANG_LLM_API_BASE / VISLANG_LLM_API_KEY_ENV.
"""

import os

LLM_MODEL = os.environ.get(
    "VISLANG_LLM_MODEL", "openai/anthropic.claude-sonnet-4-5-20250929-v1:0")
LLM_API_BASE = os.environ.get(
    "VISLANG_LLM_API_BASE", "https://aiportal-api.aws.lanl.gov/v1")
LLM_API_KEY_ENV = os.environ.get("VISLANG_LLM_API_KEY_ENV", "MY_API_KEY")
DEFAULT_MAX_TOKENS = 8000


def _load_env():
    """Populate the environment from a .env beside this file. No-op if
    python-dotenv isn't installed; does not override vars already set."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def get_api_key():
    """Return the gateway API key, or raise RuntimeError if it isn't set."""
    _load_env()
    key = os.environ.get(LLM_API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{LLM_API_KEY_ENV} is not set. Put it in the .env beside the code "
            f"as '{LLM_API_KEY_ENV}=<key>' (no spaces), or export it.")
    return key


def get_lm(max_tokens=DEFAULT_MAX_TOKENS):
    """Build a dspy.LM pointed at the configured gateway.

    Raises ImportError if dspy isn't installed, or RuntimeError if the key is
    missing. Callers that want graceful degradation should catch Exception.
    """
    import dspy
    return dspy.LM(LLM_MODEL, api_base=LLM_API_BASE, api_key=get_api_key(),
                   temperature=None, max_tokens=max_tokens)
