import logging
import os

from app.llm.base import LLMProvider
from app.llm.fallback import FallbackProvider

logger = logging.getLogger(__name__)


def get_provider() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "pollinations").lower()

    if provider == "pollinations":
        from app.llm.pollinations import PollinationsProvider
        return PollinationsProvider()

    if provider == "groq":
        from app.llm.groq import GroqProvider
        return GroqProvider(os.environ["GROQ_API_KEY"])

    if provider == "google":
        from app.llm.google import GoogleProvider
        return GoogleProvider(os.environ["GOOGLE_AI_API_KEY"])

    logger.warning("Unknown LLM_PROVIDER=%r — using fallback", provider)
    return FallbackProvider()


def validate_llm_config():
    """Call at startup — raises SystemExit if config is invalid."""
    if os.getenv("AUTO_PICK_ENABLED", "true").lower() != "true":
        return

    provider = os.getenv("LLM_PROVIDER", "pollinations").lower()

    if provider == "groq" and not os.getenv("GROQ_API_KEY"):
        raise SystemExit("LLM_PROVIDER=groq requires GROQ_API_KEY to be set in .env")

    if provider == "google" and not os.getenv("GOOGLE_AI_API_KEY"):
        raise SystemExit("LLM_PROVIDER=google requires GOOGLE_AI_API_KEY to be set in .env")
