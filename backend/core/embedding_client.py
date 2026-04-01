"""Bedrock Titan v2 embedding client with graceful fallback.

Embeds text via Amazon Bedrock Titan Embedding v2 (1024 dimensions).
All failures (timeout, auth, network) return None — the caller falls
back to keyword-only search. This module never raises.

Public symbols:

- ``EmbeddingClient``  — Bedrock Titan v2 client with timeout + fallback
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Bedrock model ID for Titan Embeddings v2
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIM = 1024
DEFAULT_TIMEOUT = 3.0  # seconds


class EmbeddingClient:
    """Bedrock Titan v2 embedding client.

    Every method returns None on failure — never raises. The caller
    can use None as a signal to fall back to keyword-only search.

    Usage::

        client = EmbeddingClient()
        vec = client.embed_text("deployment pipeline issues")
        if vec is None:
            # Bedrock down — use keyword-only
            ...
    """

    def __init__(
        self,
        region: str = "us-west-2",
        timeout: float = DEFAULT_TIMEOUT,
        model_id: str = TITAN_MODEL_ID,
    ):
        self._region = region
        self._timeout = timeout
        self._model_id = model_id
        self._client = None

    def _get_client(self):
        """Lazy-init boto3 client."""
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config

                config = Config(
                    read_timeout=self._timeout,
                    connect_timeout=self._timeout,
                    retries={"max_attempts": 1},
                )
                self._client = boto3.client(
                    "bedrock-runtime",
                    region_name=self._region,
                    config=config,
                )
            except Exception as exc:
                logger.warning("Failed to create Bedrock client: %s", exc)
                return None
        return self._client

    def embed_text(self, text: str) -> Optional[list[float]]:
        """Embed a single text string. Returns None on any failure.

        Args:
            text: Text to embed (will be truncated to 8192 tokens by Titan).

        Returns:
            List of 1024 floats, or None if embedding failed.
        """
        client = self._get_client()
        if client is None:
            return None

        try:
            body = json.dumps({
                "inputText": text,
                "dimensions": EMBEDDING_DIM,
                "normalize": True,
            })

            response = client.invoke_model(
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )

            result = json.loads(response["body"].read())
            embedding = result.get("embedding")

            if embedding and len(embedding) == EMBEDDING_DIM:
                return embedding

            logger.warning(
                "Unexpected embedding response: dim=%s",
                len(embedding) if embedding else "None",
            )
            return None

        except Exception as exc:
            logger.warning("Bedrock embedding failed: %s", exc)
            return None

    def embed_batch(self, texts: list[str]) -> list[Optional[list[float]]]:
        """Embed multiple texts. Returns list of embeddings (None for failures).

        Titan v2 doesn't support batch API, so this calls embed_text in a loop.
        """
        return [self.embed_text(t) for t in texts]
