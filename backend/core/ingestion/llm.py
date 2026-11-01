"""The base of CDF's model clients (the source extractor, sources/extract_llm).

- ``BedrockJudge``: the model id and the reproducibility fields (``version_vector``) every
  call records; the calls themselves are the subclass's (``structured()``).
- ``JudgeResult``: a typed answer with its version vector and raw reply.
- ``failure_reason``: ``AWS_CREDENTIALS_REASON`` for a call that failed because no AWS
  credentials resolve or AWS rejected them. A subclass's ``extract`` raises such a
  failure as ``JudgeCredentialsError``, whose text starts with that reason, so every
  place that records the error (the extraction attempt, the session job) records it.
"""

from __future__ import annotations

import functools
import platform
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from jobs.bedrock import get_model_id

from .contracts import CONTRACT_VERSION, NORMALIZER_VERSION, PROMPT_VERSION, RULES_VERSION
from .prompts import prompt_hash

T = TypeVar("T")

# The reason recorded when background cognition cannot reach Bedrock for lack of AWS
# credentials; the attention overlay turns it into one "needs the account" item.
AWS_CREDENTIALS_REASON = "judge_unavailable:aws_credentials"

# AWS error codes that mean "these credentials are missing, expired or not accepted"
_CREDENTIAL_CODES = (
    "ExpiredToken", "UnrecognizedClient", "InvalidSignature", "InvalidClientTokenId",
    "SignatureDoesNotMatch", "MissingAuthenticationToken", "IncompleteSignature",
    "AuthFailure", "InvalidAccessKeyId",
)
# Words in an AccessDenied message that point to the sign-in, not to a model or a
# permission of the account (a model not enabled is also AccessDenied)
_AUTH_WORDS = ("credential", "security token", "api key", "authenticat", "signature",
               "bearer", "token is expired", "invalid token")


class JudgeCredentialsError(RuntimeError):
    """A model call failed for lack of AWS credentials; ``str()`` starts with
    ``AWS_CREDENTIALS_REASON`` and ``__cause__`` is the original error."""


def _is_credentials_error(exc: BaseException) -> bool:
    try:
        from botocore import exceptions as bx
    except ImportError:  # pragma: no cover - botocore ships with the backend
        return False
    missing = tuple(getattr(bx, name) for name in (
        "NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError",
        "NoAuthTokenError", "TokenRetrievalError", "SSOTokenLoadError",
        "UnauthorizedSSOTokenError") if hasattr(bx, name))
    if isinstance(exc, missing):
        return True
    try:
        from core.aws_account import AccountError
        if isinstance(exc, AccountError):  # the pinned account could not be resolved
            return True
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, bx.ClientError):
        error = (exc.response or {}).get("Error") or {}
        code = str(error.get("Code") or "")
        if any(code.startswith(c) for c in _CREDENTIAL_CODES):
            return True
        if code.startswith("AccessDenied"):
            message = str(error.get("Message") or "").lower()
            return any(word in message for word in _AUTH_WORDS)
    return False


def failure_reason(exc: BaseException) -> str | None:
    """``AWS_CREDENTIALS_REASON`` when *exc*, or an error it was raised from, is a
    credentials failure; None for any other failure."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, JudgeCredentialsError) or _is_credentials_error(current):
            return AWS_CREDENTIALS_REASON
        current = current.__cause__ or current.__context__
    return None


def _records_credentials(call: Callable[..., Any]) -> Callable[..., Any]:
    """*call*, with a credentials failure raised as ``JudgeCredentialsError``."""
    @functools.wraps(call)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return call(*args, **kwargs)
        except JudgeCredentialsError:
            raise
        except Exception as exc:
            if failure_reason(exc) is None:
                raise
            raise JudgeCredentialsError(
                f"{AWS_CREDENTIALS_REASON}: {type(exc).__name__}") from exc
    wrapper._records_credentials = True  # type: ignore[attr-defined]
    return wrapper

@dataclass(frozen=True)
class JudgeResult(Generic[T]):
    """Typed judge result and invocation-specific version fields."""

    value: T
    vector: dict[str, Any]
    raw: str


class BedrockJudge:
    """The model a CDF call runs on, and what it records about each call.

    A subclass's ``extract`` (its model call) raises a credentials failure as
    ``JudgeCredentialsError`` (``_records_credentials``)."""

    provider = "aws-bedrock"
    temperature = 0.0
    # Claude 4.5+ on Bedrock rejects temperature and top_p together; send only temperature.
    top_p = None
    seed = None

    max_tokens = 4096
    read_timeout = 25

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        call = cls.__dict__.get("extract")
        if callable(call) and not getattr(call, "_records_credentials", False):
            cls.extract = _records_credentials(call)

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or get_model_id("claude-sonnet-4-6")

    def version_vector(self, prompt: str) -> dict[str, Any]:
        """Return all fixed and invocation-derived Phase A version fields."""
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "provider_version": "bedrock-2023-05-31",
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
            "prompt_hash": prompt_hash(prompt),
            "contract_version": CONTRACT_VERSION,
            "rules_version": RULES_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "prompt_version": PROMPT_VERSION,
            "runtime": f"python-{platform.python_version()}",
        }
