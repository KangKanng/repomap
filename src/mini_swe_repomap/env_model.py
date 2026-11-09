"""mini's LiteLLM backend with environment credentials, streams and server costs."""

import hashlib
import math
import os

import litellm
from minisweagent.exceptions import FormatError
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.utils.content_string import get_content_string


class EnvLitellmModel(LitellmModel):
    abort_exceptions = [*LitellmModel.abort_exceptions, ValueError]

    def query(self, messages: list[dict], **kwargs):
        # This is trajectory metadata; mini strips extra before subsequent API requests.
        snapshots = []
        for index, message in enumerate(messages):
            content = get_content_string(message)
            if content.startswith("<repository_map>"):
                snapshots.append(
                    {"message_index": index, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                )
        try:
            result = super().query(messages, **kwargs)
        except FormatError as error:
            for message in error.messages:
                message.setdefault("extra", {})["request_repository_maps"] = snapshots
            raise
        result.setdefault("extra", {})["request_repository_maps"] = snapshots
        return result

    def _query(self, messages: list[dict], **kwargs):
        defaults = {}
        explicit = self.config.model_kwargs | kwargs
        for variable, parameter in [("MODEL_BASE_URL", "api_base"), ("MODEL_API_KEY", "api_key")]:
            if parameter not in explicit and (value := os.getenv(variable)):
                defaults[parameter] = value
        if "stream" not in explicit and (stream := os.getenv("MODEL_STREAMING")) is not None:
            enabled = {"true", "1", "yes", "on"}
            disabled = {"false", "0", "no", "off"}
            if stream.strip().lower() not in enabled | disabled:
                raise ValueError("MODEL_STREAMING must be true/false, 1/0, yes/no, or on/off")
            defaults["stream"] = stream.strip().lower() in enabled
        options = defaults | kwargs
        response = super()._query(messages, **options)
        if (self.config.model_kwargs | options).get("stream"):
            response = litellm.stream_chunk_builder(list(response), messages=messages)
            if response is None:
                raise RuntimeError("The model returned an empty stream")
        return response

    def _calculate_cost(self, response) -> dict:
        """Use the provider's charge before consulting LiteLLM's price table."""
        usage = getattr(response, "usage", None)
        value = usage.get("cost") if isinstance(usage, dict) else getattr(usage, "cost", None)
        source = "usage.cost"
        if value is None:
            # OpenRouter's LiteLLM adapter also copies usage.cost into this header.
            headers = (getattr(response, "_hidden_params", None) or {}).get("additional_headers") or {}
            source = "llm_provider-x-litellm-response-cost"
            value = headers.get(source)
        if value is None:
            # response_cost can be an SDK estimate, so it is not a server charge.
            return super()._calculate_cost(response)

        error = f"Invalid server-reported cost in {source}: expected a finite non-negative number"
        try:
            cost = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(error) from exc
        if isinstance(value, bool) or not math.isfinite(cost) or cost < 0:
            raise ValueError(error)
        return {"cost": cost, "cost_source": f"server:{source}"}
