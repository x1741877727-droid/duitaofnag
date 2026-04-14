import pytest
import json
import os


SAMPLE_CALLBACK = (
    '_Callback( {"ret":0, '
    '"url":"auth://www.qq.com/oauth2.0/show?which=Login&'
    'openid=11446279220371186239&'
    'access_token=03FE61DCBC55C86C18B9455D5D91AA89&'
    'pay_token=396A1198D087D987641CD8F6EFFE7D68&'
    'pf=desktop_m_qq-10000144-android-2002-&'
    'pfkey=abcdef1234567890&'
    'expires_in=7776000"} )'
)


def test_parse_callback_extracts_tokens():
    from token_capture import parse_qq_callback
    result = parse_qq_callback(SAMPLE_CALLBACK)
    assert result is not None
    assert result["openid"] == "11446279220371186239"
    assert result["access_token"] == "03FE61DCBC55C86C18B9455D5D91AA89"
    assert result["pay_token"] == "396A1198D087D987641CD8F6EFFE7D68"
    assert result["pf"] == "desktop_m_qq-10000144-android-2002-"


def test_parse_callback_returns_none_on_error():
    from token_capture import parse_qq_callback
    assert parse_qq_callback("not a callback") is None
    assert parse_qq_callback('_Callback( {"ret":1, "msg":"error"} )') is None


def test_parse_callback_from_http_body():
    from token_capture import extract_callback_from_body
    body = f"some prefix\r\n{SAMPLE_CALLBACK}\r\nsome suffix"
    result = extract_callback_from_body(body)
    assert result is not None
    assert result["openid"] == "11446279220371186239"


def test_token_store_save_and_load():
    from token_capture import TokenStore
    path = "/tmp/test_tokens_task2.json"
    store = TokenStore(path)
    store.save("user1", {
        "openid": "123",
        "access_token": "AAA",
        "pay_token": "BBB",
        "pf": "desktop_m_qq-10000144-android-2002-",
    })
    tokens = store.get("user1")
    assert tokens["openid"] == "123"
    assert tokens["access_token"] == "AAA"
    if os.path.exists(path):
        os.unlink(path)


def test_token_store_list_all():
    from token_capture import TokenStore
    path = "/tmp/test_tokens_task2b.json"
    store = TokenStore(path)
    store.save("user1", {"openid": "111", "access_token": "A", "pay_token": "B", "pf": ""})
    store.save("user2", {"openid": "222", "access_token": "C", "pay_token": "D", "pf": ""})
    all_tokens = store.list_all()
    assert len(all_tokens) == 2
    if os.path.exists(path):
        os.unlink(path)
