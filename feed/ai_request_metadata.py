"""Read usage counters without estimates or cross-request database lookups."""


def parse_usage(data):
    result = {'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None}
    if not isinstance(data, dict):
        return result
    if isinstance(data.get('usageMetadata'), dict):
        usage = data['usageMetadata']
        fields = ('promptTokenCount', 'candidatesTokenCount', 'totalTokenCount')
    elif isinstance(data.get('usage'), dict):
        usage = data['usage']
        fields = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    else:
        return result
    for target, source in zip(result, fields):
        value = usage.get(source)
        # Zero is meaningful; booleans, negative and malformed values are not counters.
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[target] = value
    return result


def request_metadata(provider, model, data):
    actual_model = (data.get('model') or data.get('modelVersion')) if isinstance(data, dict) else None
    return {'provider': provider, 'model': str(actual_model or model)[:200], **parse_usage(data)}
