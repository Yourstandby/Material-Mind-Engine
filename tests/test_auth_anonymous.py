"""只读接口匿名开关（MME_ALLOW_ANONYMOUS / MME_ANONYMOUS_IPS）的单元测试。"""

import pytest

from core import auth


def _env(monkeypatch, values):
    monkeypatch.setattr(auth, "env", lambda key, default="": values.get(key, default))


def test_disabled_by_default(monkeypatch):
    _env(monkeypatch, {})
    for path in auth.READONLY_PATHS:
        assert auth.anonymous_allowed(path, "172.16.1.121") is False


def test_switch_opens_readonly_paths_only(monkeypatch):
    _env(monkeypatch, {"MME_ALLOW_ANONYMOUS": "1"})
    assert auth.anonymous_allowed("/api/chat", "") is True
    assert auth.anonymous_allowed("/api/search", "10.0.0.9") is True
    assert auth.anonymous_allowed("/api/bootstrap", "10.0.0.9") is True
    # 写接口 / 账号接口永远要登录，不受开关影响
    for path in ("/api/ingest", "/api/sources", "/api/files/x", "/api/auth/me", "/api/users"):
        assert auth.anonymous_allowed(path, "10.0.0.9") is False


def test_falsey_switch_value_keeps_login(monkeypatch):
    _env(monkeypatch, {"MME_ALLOW_ANONYMOUS": "0"})
    assert auth.anonymous_allowed("/api/chat", "172.16.1.121") is False


def test_ip_whitelist_allows_only_listed(monkeypatch):
    _env(monkeypatch, {"MME_ANONYMOUS_IPS": "172.16.1.121, 10.0.0.5"})
    assert auth.anonymous_allowed("/api/chat", "172.16.1.121") is True
    assert auth.anonymous_allowed("/api/search", "10.0.0.5") is True
    assert auth.anonymous_allowed("/api/chat", "172.16.1.122") is False
    assert auth.anonymous_allowed("/api/chat", "") is False


def test_ip_whitelist_does_not_open_writes(monkeypatch):
    _env(monkeypatch, {"MME_ANONYMOUS_IPS": "172.16.1.121"})
    assert auth.anonymous_allowed("/api/sources", "172.16.1.121") is False


def test_semicolon_separated_ips(monkeypatch):
    _env(monkeypatch, {"MME_ANONYMOUS_IPS": "172.16.1.121;10.0.0.5"})
    assert auth.anonymous_allowed("/api/bootstrap", "10.0.0.5") is True
