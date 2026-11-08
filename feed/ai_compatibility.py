"""Remember explicitly rejected API options; never cache prompts or credentials."""
import hashlib
import json
from urllib.parse import urlsplit


def option_key(name, value):
    # System content is not an API capability and must never enter this cache.
    return name if name == 'systemInstruction' else name + ':' + json.dumps(value, sort_keys=True)


def inline_system(payload):
    text = payload.pop('systemInstruction', {}).get('parts', [{}])[0].get('text', '')
    if text:
        parts = payload['contents'][0]['parts']
        parts.insert(0, {'text': '[SYSTEM INSTRUCTION]\n' + text})


class GeminiOptions:
    def __init__(self, url, model):
        from .ai_context import evidence_cache
        parts = urlsplit(url)
        identity = parts.netloc + parts.path + ':' + model
        self.key = 'gemini-options-v1-' + hashlib.sha256(identity.encode()).hexdigest()
        self.cache = evidence_cache()
        self.rejected = set(self.cache.get(self.key) or [])

    def apply(self, payload):
        config = payload['generationConfig']
        for name in ('thinkingConfig', 'responseMimeType', 'seed'):
            if name in config and option_key(name, config[name]) in self.rejected:
                config.pop(name)
        if 'systemInstruction' in self.rejected:
            inline_system(payload)

    def remember(self, name, value):
        self.rejected.add(option_key(name, value))
        self.cache.set(self.key, sorted(self.rejected), 86400)
