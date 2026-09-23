"""Opt-in provider/recovery profiles; historical grid configuration stays frozen."""
import copy
from dataclasses import replace


def recovery_profile(model, *, retries_only=False):
    if model.name not in {'qwen3.8-27b', 'deepseek-v4.1-flash'}:
        raise ValueError(f'No recovery candidate for {model.name}')
    extra = copy.deepcopy(model.extra_body)
    if retries_only:
        return replace(model, tool_attempts=8, extra_body=extra)
    if model.name == 'qwen3.8-27b':
        extra['provider'] = {'sort': 'throughput', 'allow_fallbacks': True, 'ignore': ['alibaba']}
        tokens = 16384
    else:
        extra['provider'] = {'order': ['alibaba', 'deepinfra'], 'allow_fallbacks': True,
                             'ignore': ['together']}
        tokens = 100000
    return replace(model, max_tokens=tokens, tool_attempts=8, tool_retry_max_tokens=max(32768, tokens),
                   extra_body=extra)
