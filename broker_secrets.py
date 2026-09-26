from __future__ import annotations
from typing import Any

SERVICE = 'nifty-option-ai-live'


def _keyring():
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


def available() -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        backend = kr.get_keyring()
        return backend is not None
    except Exception:
        return False


def save(provider: str, api_key: str, access_token: str) -> dict[str, Any]:
    kr = _keyring()
    if kr is None:
        return {'saved': False, 'reason': 'Python keyring package is unavailable.'}
    try:
        prefix = provider.upper()
        kr.set_password(SERVICE, f'{prefix}:api_key', api_key)
        kr.set_password(SERVICE, f'{prefix}:access_token', access_token)
        return {'saved': True, 'backend': str(kr.get_keyring())}
    except Exception as exc:
        return {'saved': False, 'reason': f'OS keyring unavailable: {exc}'}


def load(provider: str) -> dict[str, str]:
    kr = _keyring()
    if kr is None:
        return {}
    prefix = provider.upper()
    try:
        api_key = kr.get_password(SERVICE, f'{prefix}:api_key') or ''
        access_token = kr.get_password(SERVICE, f'{prefix}:access_token') or ''
        if not api_key and not access_token:
            return {}
        return {'apiKey': api_key, 'accessToken': access_token}
    except Exception:
        return {}


def clear(provider: str) -> dict[str, Any]:
    kr = _keyring()
    if kr is None:
        return {'cleared': False, 'reason': 'Python keyring package is unavailable.'}
    prefix = provider.upper()
    deleted = 0
    errors = []
    for key in (f'{prefix}:api_key', f'{prefix}:access_token'):
        try:
            kr.delete_password(SERVICE, key)
            deleted += 1
        except Exception as exc:
            errors.append(str(exc))
    return {'cleared': deleted > 0, 'deleted': deleted, 'errors': errors}
