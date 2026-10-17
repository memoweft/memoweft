"""Pass new recall language only to implementations declaring that contract."""
from __future__ import annotations

from inspect import Parameter, signature
from typing import Any, Callable, TypeVar

from ..types import Lang


_T = TypeVar('_T')


def call_with_recall_language(
    operation: Callable[..., _T], *args: Any, lang: Lang, **kwargs: Any,
) -> _T:
    if lang == 'en':
        try:
            parameter = signature(operation).parameters.get('lang')
        except (TypeError, ValueError):
            parameter = None
        # **kwargs alone is not an explicit language capability: an old
        # forwarding adapter may pass it on to a legacy three-argument reader.
        if parameter is not None and parameter.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY):
            return operation(*args, lang='en', **kwargs)
    # Preserve the old call shape for default/Chinese, opaque adapters and
    # old readers. Do not catch errors from inside the implementation or retry.
    return operation(*args, **kwargs)
