"""Whether a model call failed because the model could not be reached.

One judgment, read by every CDF lane that calls a model: the session review
(``s2_jobs`` / ``worker.S2JobExecutor``, ``s7_review._relations``, health check C2) and
the month-summary lane (``summary_lane.SummaryLane``). Such a failure (credentials not
loaded, an endpoint not resolved or refusing the connection, a call refused for the
region it comes from) says nothing about the work: a lane postpones it instead of
counting it. Any other error is a failure of the work itself.

``model_unreachable`` reads the two shapes a failure reaches a lane in:

- **text** (an exception's text, or an error recorded on a job): only the Claude CLI's
  own error result, as the review raises it, counts (``_CLI_UNREACHABLE_RE``);
- **an exception** (the Bedrock SDK the summary lane calls through ``jobs.bedrock``): the
  exception or any error it was raised from is a credentials error
  (``llm.failure_reason``), an endpoint that could not be reached
  (``aws_account.endpoint_unreachable``), a refused connection (``ECONNREFUSED``), a
  botocore ``ClientError`` whose message (``aws_account.client_error_message``) is the
  region block, or a link whose text is the CLI's error result above. The SDK shapes are
  read through ``core.aws_account``: this module imports no AWS SDK itself
  (``tests/test_aws_account_integrity.py``).
"""
from __future__ import annotations

import errno
import re

# The CLI's own error result saying the model could not be reached: credentials it
# could not load ("API Error: Could not load AWS credentials"), an API server it could
# not reach ("API Error: Can't reach the API server ... (ENOTFOUND)") or connect to
# ("API Error: Connection refused ... (ECONNREFUSED)"), a call refused for the region it
# came from ("API Error: 400 Access to Anthropic models is not allowed from unsupported
# countries, regions, or territories": the egress region, not the work). Anchored on the
# start of the text the review raises for an error result (s7_review.sdk_runner:
# "review failed: <subtype>: <what the CLI said>", recorded with its exception type in
# front): what the model writes (a reply without structured output, raised as
# "reply missing: ...") can never match, so a quoted error never postpones a job.
_CLI_UNREACHABLE_RE = re.compile(
    r"(?:\w+: )?review failed: [^:\s]*: API Error:\s*"
    r"(?:Could not load (?:AWS )?credentials|Can['’]t reach the API server"
    r"|[^\n]{0,300}?\b(?:ENOTFOUND|ECONNREFUSED)\b"
    r"|(?:\d{3} )?Access to Anthropic models is not allowed from unsupported countries)",
    re.IGNORECASE)

# The region block as the Bedrock API states it in a ClientError's message (Converse
# raises it as a ValidationException): the message comes from the service, not from a
# model reply, so it is matched anywhere in that message.
_REGION_BLOCK = "access to anthropic models is not allowed from unsupported countries"


def model_unreachable(error: "str | BaseException | None") -> bool:
    """Whether *error* says the model could not be reached: credentials that could not
    be loaded, an endpoint that could not be resolved or refused the connection, a call
    refused for its region. *error* is text (an exception's text or a recorded error:
    only the CLI's own error result counts) or an exception (it, or any error it was
    raised from, in the SDK's form or as the CLI's error result). Such a failure says
    nothing about the work; any other error does."""
    if not error:
        return False
    if isinstance(error, str):
        return _CLI_UNREACHABLE_RE.match(error) is not None
    if not isinstance(error, BaseException):
        return False
    from .llm import failure_reason
    if failure_reason(error):
        return True
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _exception_unreachable(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def _exception_unreachable(exc: BaseException) -> bool:
    """Whether *exc* itself (not its chain) says the model could not be reached."""
    from core.aws_account import client_error_message, endpoint_unreachable
    if endpoint_unreachable(exc):
        return True
    if isinstance(exc, ConnectionRefusedError) or (
            isinstance(exc, OSError) and exc.errno == errno.ECONNREFUSED):
        return True
    message = client_error_message(exc)
    if message is not None and _REGION_BLOCK in message.lower():
        return True
    return _CLI_UNREACHABLE_RE.match(str(exc)) is not None
