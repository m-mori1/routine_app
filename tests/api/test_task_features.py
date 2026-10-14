import io
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import api
import google_chat


@pytest.fixture(autouse=True)
def isolated_keyless_configuration(monkeypatch):
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "user")
    monkeypatch.delenv("GOOGLE_CHAT_BOT_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("GOOGLE_CHAT_BOT_SOURCE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_CONFIG_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_CHAT_SPACE_ID", raising=False)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "E2E_AUTH_BYPASS", False)
    monkeypatch.setattr(api, "_fetch_employee_by_name", lambda name: None)
    monkeypatch.setattr(api, "_current_user_context", lambda: {
        "employee_name": "tester", "name": "tester", "department_cd": "D000013"
    })
    monkeypatch.setattr(api, "_notify_slack_on_create", lambda *args: None)
    app = api.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-only")
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["user"] = {"name": "tester"}
    return test_client


def task_data():
    return {
        "title": "attachment-test", "frequency": "\u30b9\u30dd\u30c3\u30c8",
        "due_date": "2026-10-16", "assignee": ["one@example.com", "two@example.com"],
        "task_kind": "\u30b0\u30eb\u30fc\u30d7", "summary": "test summary",
    }


def test_creation_notifies_all_assignees_and_returns_id(client, monkeypatch):
    insert = Mock(return_value=123)
    notify = Mock(return_value={"status": "sent", "sent": 2, "total": 2})
    monkeypatch.setattr(api, "_insert_entries", insert)
    monkeypatch.setattr(api, "notify_task_created", notify)
    response = client.post("/api.py/routines", json=task_data())
    assert response.status_code == 201
    assert response.json["task_no"] == 123
    assert notify.call_args.args[3] == ["one@example.com", "two@example.com"]


@pytest.mark.parametrize("assignees, expected", [
    (["A", "B"], ["b@example.com"]),
    (["B", "C"], ["b@example.com", "c@example.com"]),
    (["A"], []),
    ([" A@EXAMPLE.COM ", "B"], ["b@example.com"]),
    (["a", "B"], ["b@example.com"]),
    (["a@signin.example.com", "B"], ["b@example.com"]),
])
def test_creation_excludes_creator_without_changing_assignees(client, monkeypatch, assignees, expected):
    monkeypatch.setattr(api, "_current_user_context", lambda: {
        "name": "A", "employee_name": "A", "upn": "a@signin.example.com",
        "department_cd": "D000013",
    })
    def email_for(name):
        return {"a": "a@example.com", "b": "b@example.com", "c": "c@example.com"}.get(
            name.strip().lower(), name.strip() if "@" in name else None)
    monkeypatch.setattr(api, "_assignee_to_email", email_for)
    insert = Mock(return_value=123)
    notify = Mock(return_value={"status": "sent", "sent": len(expected), "total": len(expected)})
    monkeypatch.setattr(api, "_insert_entries", insert)
    monkeypatch.setattr(api, "notify_task_created", notify)
    data = task_data()
    data["assignee"] = assignees
    data["registrant"] = "B"  # Request data cannot override the authenticated creator.
    response = client.post("/api.py/routines", json=data)
    assert response.status_code == 201
    assert insert.call_args.args[0]["assignee"] == api._format_assignees(assignees)
    assert insert.call_args.args[0]["registrant"] == "A"
    if expected:
        assert notify.call_args.args[3] == expected
        assert response.json["google_chat"]["status"] == "sent"
    else:
        notify.assert_not_called()
        assert response.json["google_chat"]["status"] == "skipped"
        assert response.json["google_chat"]["reason"] == "no_other_assignees"


def test_self_name_without_email_is_not_a_resolution_failure(client, monkeypatch):
    monkeypatch.setattr(api, "_insert_entries", Mock(return_value=123))
    monkeypatch.setattr(api, "_assignee_to_email", lambda name: None)
    notify = Mock()
    monkeypatch.setattr(api, "notify_task_created", notify)
    data = task_data()
    data["assignee"] = ["tester"]
    response = client.post("/api.py/routines", json=data)
    assert response.status_code == 201
    assert response.json["google_chat"]["status"] == "skipped"
    assert "failures" not in response.json["google_chat"]
    notify.assert_not_called()


def test_dm_failure_does_not_fail_task_creation(client, monkeypatch):
    monkeypatch.setattr(api, "_insert_entries", Mock(return_value=123))
    monkeypatch.setattr(api, "notify_task_created", Mock(side_effect=RuntimeError("offline")))
    response = client.post("/api.py/routines", json=task_data())
    assert response.status_code == 201
    assert response.json["google_chat"]["status"] == "failed"


def test_multipart_create_preserves_bytes_and_unicode_filename(client, monkeypatch):
    insert = Mock(return_value=123)
    monkeypatch.setattr(api, "_insert_entries", insert)
    monkeypatch.setattr(api, "notify_task_created", Mock(return_value={"status": "disabled"}))
    response = client.post("/api.py/routines", data={
        "data": json.dumps(task_data()),
        "attachments": (io.BytesIO(b"\x00\xffhello"), "C:\\tmp\\\u8cc7\u6599.pdf"),
    })
    assert response.status_code == 201
    assert insert.call_args.args[2] == [{"filename": "\u8cc7\u6599.pdf", "content": b"\x00\xffhello"}]


@pytest.mark.parametrize("content", [b"", b"x" * (10 * 1024 * 1024 + 1)], ids=["empty", "oversize"])
def test_bad_file_rejected_before_task_insert(client, monkeypatch, content):
    insert = Mock()
    monkeypatch.setattr(api, "_insert_entries", insert)
    response = client.post("/api.py/routines", data={
        "data": json.dumps(task_data()), "attachments": (io.BytesIO(content), "bad.pdf"),
    })
    assert response.status_code == 400
    insert.assert_not_called()


def test_too_many_attachments(client):
    response = client.post("/api.py/routines", data={
        "data": json.dumps(task_data()),
        "attachments": [(io.BytesIO(b"x"), f"{i}.txt") for i in range(11)],
    })
    assert response.status_code == 400


def test_total_attachment_limit(client):
    response = client.post("/api.py/routines", data={
        "data": json.dumps(task_data()),
        "attachments": [(io.BytesIO(b"x" * (9 * 1024 * 1024)), f"{i}.txt") for i in range(3)],
    })
    assert response.status_code == 413


def test_multipart_parent_update(client, monkeypatch):
    update = Mock()
    monkeypatch.setattr(api, "_update_parent", update)
    response = client.patch("/api.py/parent/123", data={
        "data": json.dumps({"title": "renamed"}),
        "attachments": (io.BytesIO(b"abc"), "memo.txt"),
    })
    assert response.status_code == 204
    assert update.call_args.args == (123, {"title": "renamed"}, [{"filename": "memo.txt", "content": b"abc"}])


def test_files_require_login_and_same_origin(client):
    response = client.patch("/api.py/parent/123", headers={"Origin": "https://other.example"},
                            data={"data": "{}", "attachments": (io.BytesIO(b"abc"), "memo.txt")})
    assert response.status_code == 403
    with client.session_transaction() as session:
        session.clear()
    assert client.get("/api.py/parent/123/attachments").status_code == 302


def test_download_is_scoped_and_forced_attachment(client, monkeypatch):
    cursor = Mock()
    cursor.fetchone.return_value = ("\u8cc7\u6599.html", b"<script>test</script>")
    connection = Mock()
    connection.cursor.return_value = cursor

    @contextmanager
    def get_connection():
        yield connection

    monkeypatch.setattr(api, "_get_db_connection", get_connection)
    response = client.get("/api.py/parent/12/attachments/34")
    assert response.data == b"<script>test</script>"
    assert response.headers["Content-Type"] == "application/octet-stream"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert cursor.execute.call_args.args[1] == [12, 34]
    cursor.fetchone.return_value = None
    assert client.get("/api.py/parent/13/attachments/34").status_code == 404


@pytest.fixture
def configured_webhook(monkeypatch, tmp_path):
    token = tmp_path / "directory.dat"
    token.write_bytes(b"unused-test-token")
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "webhook")
    monkeypatch.setenv("GOOGLE_CHAT_SPACE_ID", "TEST_SPACE")
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL",
                       "https://chat.googleapis.com/v1/spaces/TEST_SPACE/messages?key=test-key&token=test-token")
    monkeypatch.setenv("GOOGLE_CHAT_DIRECTORY_TOKEN_FILE", str(token))
    transport = Mock()
    response = Mock(status_code=200)
    response.json.return_value = {"name": "spaces/TEST_SPACE/messages/example"}
    transport.post.return_value = response
    session = Mock(__enter__=Mock(return_value=transport), __exit__=Mock(return_value=False))
    monkeypatch.setattr(google_chat, "_webhook_session", lambda: session)
    directory = Mock(__enter__=Mock(), __exit__=Mock(return_value=False))
    monkeypatch.setattr(google_chat, "_directory_session", lambda: directory)
    return transport


@pytest.mark.parametrize("status", [200, 201, 409])
def test_space_sends_once_mentions_all_and_never_uses_dm(configured_webhook, monkeypatch, status):
    configured_webhook.post.return_value.status_code = status
    lookup = Mock(side_effect=["111", "222"])
    monkeypatch.setattr(google_chat, "_directory_user_id", lookup)
    dm = Mock(side_effect=AssertionError("DM must not be used"))
    monkeypatch.setattr(google_chat, "_chat_session", dm)
    result = google_chat.notify_task_created(123, {
        "title": "\u6bce\u6708\u306e\u78ba\u8a8d", "assignee": "one, two",
        "summary": "<users/all> <users/999> must remain task text",
    }, [], ["ONE@example.com", "one@example.com", "two@example.com"])
    assert result == {"status": "sent", "sent": 2, "total": 2,
                      "delivery": "space", "posted": True}
    assert configured_webhook.post.call_count == 1
    call = configured_webhook.post.call_args
    assert call.args[0].startswith("https://chat.googleapis.com/v1/spaces/TEST_SPACE/messages?")
    assert call.kwargs["params"] == {"messageId": "client-routine-task-123"}
    assert call.kwargs["timeout"] == 10
    assert call.kwargs["allow_redirects"] is False
    text = call.kwargs["json"]["text"]
    assert text.startswith("<users/111> <users/222>\n")
    assert "\u6bce\u6708\u306e\u78ba\u8a8d" in text
    assert "<users/all>" not in text
    assert "<users/999>" not in text
    assert lookup.call_count == 2
    dm.assert_not_called()


def test_space_partial_mentions_still_posts_once(configured_webhook, monkeypatch):
    monkeypatch.setattr(google_chat, "_directory_user_id", Mock(side_effect=["111", LookupError("missing")]))
    result = google_chat.notify_task_created(123, {}, [], ["one@example.com", "missing@example.com"])
    assert result["status"] == "partial"
    assert result["posted"] is True
    assert result["sent"] == 1
    assert result["failures"] == [{"recipient": "missing@example.com", "reason": "recipient_not_found"}]
    assert configured_webhook.post.call_count == 1


@pytest.mark.parametrize("url", [
    "https://example.com/v1/spaces/TEST_SPACE/messages?key=test&token=test",
    "http://chat.googleapis.com/v1/spaces/TEST_SPACE/messages?key=test&token=test",
    "https://chat.googleapis.com/v1/spaces/APPROVAL_SPACE/messages?key=test&token=test",
    "https://chat.googleapis.com/v1/spaces/TEST_SPACE/messages?key=test",
    "https://chat.googleapis.com/v1/spaces/TEST_SPACE/messages?key=test&token=",
    "https://chat.googleapis.com/v1/spaces/TEST_SPACE/messages?key=test&token=test&token=other",
])
def test_space_rejects_unconfirmed_or_invalid_target(configured_webhook, monkeypatch, url):
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_URL", url)
    result = google_chat.notify_task_created(123, {}, [], ["one@example.com"])
    assert result["failures"] == [{"reason": "webhook_invalid"}]
    assert result["posted"] is False
    configured_webhook.post.assert_not_called()


def test_space_requires_explicit_space_id(configured_webhook, monkeypatch):
    monkeypatch.delenv("GOOGLE_CHAT_SPACE_ID")
    assert google_chat.notify_task_created(123, {}, [], ["one@example.com"])["status"] == "failed"
    configured_webhook.post.assert_not_called()


def test_space_missing_webhook_does_not_fall_back_to_dm(configured_webhook, monkeypatch):
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL")
    dm = Mock(side_effect=AssertionError("DM fallback"))
    monkeypatch.setattr(google_chat, "_chat_session", dm)
    assert google_chat.notify_task_created(123, {}, [], ["one@example.com"])["status"] == "disabled"
    dm.assert_not_called()
    configured_webhook.post.assert_not_called()


def test_empty_recipients_never_posts_a_space_message(configured_webhook, monkeypatch):
    directory = Mock(side_effect=AssertionError("No directory request expected"))
    monkeypatch.setattr(google_chat, "_directory_session", directory)
    result = google_chat.notify_task_created(123, {}, [], [])
    assert result["status"] == "skipped"
    assert result["posted"] is False
    configured_webhook.post.assert_not_called()
    directory.assert_not_called()


@pytest.mark.parametrize("assignees, expected_ids", [
    (["tester", "b@example.com"], ["222"]),
    (["b@example.com", "c@example.com"], ["222", "333"]),
    (["tester"], []),
])
def test_creation_space_mentions_only_other_assignees(client, configured_webhook, monkeypatch, assignees, expected_ids):
    monkeypatch.setattr(api, "_insert_entries", Mock(return_value=123))
    lookup = Mock(side_effect=lambda directory, email: {
        "b@example.com": "222", "c@example.com": "333",
    }[email])
    monkeypatch.setattr(google_chat, "_directory_user_id", lookup)
    data = task_data()
    data["assignee"] = assignees
    response = client.post("/api.py/routines", json=data)
    assert response.status_code == 201
    if expected_ids:
        assert response.json["google_chat"]["status"] == "sent"
        assert configured_webhook.post.call_count == 1
        text = configured_webhook.post.call_args.kwargs["json"]["text"]
        assert text.splitlines()[0] == " ".join(f"<users/{uid}>" for uid in expected_ids)
        assert response.json["google_chat"]["total"] == len(expected_ids)
    else:
        assert response.json["google_chat"]["status"] == "skipped"
        configured_webhook.post.assert_not_called()
        lookup.assert_not_called()


def test_unresolved_other_assignee_is_not_skipped(client, configured_webhook, monkeypatch):
    monkeypatch.setattr(api, "_insert_entries", Mock(return_value=123))
    monkeypatch.setattr(api, "_assignee_to_email", lambda name: None)
    data = task_data()
    data["assignee"] = ["tester", "unknown"]
    response = client.post("/api.py/routines", json=data)
    assert response.status_code == 201
    result = response.json["google_chat"]
    assert result["status"] == "failed"
    assert result["total"] == 1
    assert result["failures"] == [{"recipient": "unknown", "reason": "recipient_not_found"}]
    configured_webhook.post.assert_not_called()


def test_space_reads_protected_webhook_configuration(configured_webhook, monkeypatch, tmp_path):
    url = os.environ["GOOGLE_CHAT_WEBHOOK_URL"]
    monkeypatch.delenv("GOOGLE_CHAT_WEBHOOK_URL")
    path = tmp_path / "webhook.dat"
    path.write_bytes(b"unused-encrypted-webhook")
    monkeypatch.setenv("GOOGLE_CHAT_WEBHOOK_CONFIG_FILE", str(path))
    protected = Mock(return_value={"GOOGLE_CHAT_WEBHOOK_URL": url})
    monkeypatch.setattr(google_chat, "_protected_credentials", protected)
    assert google_chat._space_webhook_url() == url
    protected.assert_called_once_with(path)


@pytest.mark.parametrize("status, reason", [(403, "permission_denied"), (404, "webhook_not_found"), (429, "rate_limited")])
def test_space_failure_hides_webhook_secrets(configured_webhook, monkeypatch, caplog, status, reason):
    import requests
    monkeypatch.setattr(google_chat, "_directory_user_id", Mock(return_value="111"))
    response = configured_webhook.post.return_value
    response.status_code = status
    response.json.return_value = {"error": {"code": status}}
    response.raise_for_status.side_effect = requests.HTTPError(
        "secret URL: " + os.environ["GOOGLE_CHAT_WEBHOOK_URL"], response=response)
    result = google_chat.notify_task_created(123, {}, [], ["one@example.com"])
    assert result["status"] == "failed"
    assert result["posted"] is False
    assert result["failures"] == [{"reason": reason}]
    assert "test-key" not in caplog.text
    assert "test-token" not in caplog.text
    assert "test-token" not in json.dumps(result)


def test_space_redirect_is_not_success(configured_webhook, monkeypatch):
    monkeypatch.setattr(google_chat, "_directory_user_id", Mock(return_value="111"))
    configured_webhook.post.return_value.status_code = 302
    assert google_chat.notify_task_created(123, {}, [], ["one@example.com"])["posted"] is False


def test_unresolved_assignee_warns_after_space_post(client, monkeypatch):
    monkeypatch.setattr(api, "_insert_entries", Mock(return_value=123))
    monkeypatch.setattr(api, "_assignee_to_email", lambda name: None)
    monkeypatch.setattr(api, "notify_task_created", Mock(return_value={
        "status": "sent", "sent": 0, "total": 0, "delivery": "space", "posted": True,
    }))
    response = client.post("/api.py/routines", json=task_data())
    assert response.status_code == 201
    notification = response.json["google_chat"]
    assert notification["status"] == "partial"
    assert notification["total"] == 2
    assert len(notification["failures"]) == 2


def test_chat_disabled_without_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "user")
    monkeypatch.setenv("GOOGLE_CHAT_TOKEN_FILE", str(tmp_path / "missing.dat"))
    assert google_chat.notify_task_created(1, {}, [], ["a@example.com"])["status"] == "disabled"


def test_chat_all_recipients_deduplication_and_partial_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "user")
    token = tmp_path / "token.dat"
    token.write_bytes(b"unused-encrypted-token")
    monkeypatch.setenv("GOOGLE_CHAT_TOKEN_FILE", str(token))
    transport = Mock()
    session = Mock()
    session.__enter__ = Mock(return_value=transport)
    session.__exit__ = Mock(return_value=False)
    response = Mock(status_code=200)
    response.json.return_value = {"name": "spaces/DM123"}
    transport.post.side_effect = [response, response, RuntimeError("offline")]
    monkeypatch.setattr(google_chat, "_chat_session", lambda: session)
    result = google_chat.notify_task_created(123, {"title": "\u65e5\u672c\u8a9e"}, [],
                                             ["A@example.com", "a@example.com", "b@example.com"])
    assert result == {"status": "partial", "sent": 1, "total": 2,
                      "failures": [{"recipient": "b@example.com", "reason": "request_failed"}]}
    assert transport.post.call_count == 3
    calls = transport.post.call_args_list
    assert calls[0].kwargs["json"]["memberships"][0]["member"]["name"] == "users/a@example.com"
    assert "\u65e5\u672c\u8a9e" in calls[1].kwargs["json"]["text"]
    assert calls[1].kwargs["params"]["messageId"] == "client-routine-task-123"


def test_chat_oauth_dpapi_and_session_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "user")
    import win32crypt
    import win32cryptcon
    token = tmp_path / "token.dat"
    token_info = {"client_id": "test", "client_secret": "test", "refresh_token": "test",
                  "token_uri": "https://oauth2.googleapis.com/token", "scopes": google_chat.CHAT_SCOPES}
    token.write_bytes(win32crypt.CryptProtectData(json.dumps(token_info).encode("utf-8"),
                      "test", None, None, None, win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE))
    monkeypatch.setenv("GOOGLE_CHAT_TOKEN_FILE", str(token))
    with google_chat._chat_session() as session:
        assert session.credentials.scopes == google_chat.CHAT_SCOPES


def test_self_dm_error_reports_cause():
    import requests
    response = requests.Response()
    response.status_code = 400
    response._content = json.dumps({"error": {"status": "INVALID_ARGUMENT", "message":
        "Don't specify the calling user as a membership in request. Calling user will be automatically included as a membership."}}).encode("utf-8")
    error = requests.HTTPError(response=response)
    assert google_chat._notification_failure(error, "dm") == "sender_is_recipient"


@pytest.mark.parametrize("stage", ["dm", "message"])
def test_missing_chat_app_is_not_reported_as_missing_user_installation(stage):
    import requests
    response = requests.Response()
    response.status_code = 404
    response._content = json.dumps({"error": {
        "code": 404, "status": "NOT_FOUND",
        "message": "Google Chat app not found. To create a Chat app, you must turn on the Chat API and configure the app in the Google Cloud console.",
    }}).encode("utf-8")
    error = requests.HTTPError(response=response)
    assert google_chat._notification_failure(error, stage) == "bot_not_configured"


def test_directory_lookup_requires_exact_email_match():
    directory = Mock()
    directory.get.return_value.json.return_value = {"people": [
        {"resourceName": "people/111", "emailAddresses": [{"value": "other@example.com"}]},
        {"resourceName": "people/222", "emailAddresses": [{"value": "M-MORI@example.com"}]},
    ]}
    assert google_chat._directory_user_id(directory, "m-mori@example.com") == "222"
    with pytest.raises(LookupError):
        google_chat._directory_user_id(directory, "missing@example.com")


def test_bot_notifies_authenticated_directory_user_without_self_dm(monkeypatch, tmp_path):
    key = tmp_path / "key.json"
    token = tmp_path / "directory.dat"
    key.write_text("{}", encoding="utf-8")
    token.write_bytes(b"unused-encrypted-token")
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", str(key))
    monkeypatch.setenv("GOOGLE_CHAT_DIRECTORY_TOKEN_FILE", str(token))
    bot = Mock()
    directory = Mock()
    directory.get.return_value.json.return_value = {"people": [
        {"resourceName": "people/222", "emailAddresses": [{"value": "m-mori@example.com"}]},
    ]}
    bot.get.return_value.json.return_value = {"name": "spaces/BOT_DM"}
    bot.post.return_value.status_code = 200

    @contextmanager
    def bot_session():
        yield bot

    @contextmanager
    def directory_session():
        yield directory

    monkeypatch.setattr(google_chat, "_chat_session", bot_session)
    monkeypatch.setattr(google_chat, "_directory_session", directory_session)
    result = google_chat.notify_task_created(321, {"title": "test"}, [], ["m-mori@example.com"])
    assert result == {"status": "sent", "sent": 1, "total": 1}
    assert "users%2F222" in bot.get.call_args.args[0]
    assert bot.post.call_args.args[0] == "https://chat.googleapis.com/v1/spaces/BOT_DM/messages"
    assert bot.post.call_args.kwargs["json"]["text"].startswith("<users/222>\n")
    assert bot.post.call_count == 1
    assert directory.post.call_count == 0


@pytest.mark.parametrize("message_status", [200, 409])
def test_bot_mentions_each_recipient_only_in_their_own_dm(monkeypatch, tmp_path, message_status):
    key = tmp_path / "key.json"
    token = tmp_path / "directory.dat"
    key.write_text("{}", encoding="utf-8")
    token.write_bytes(b"unused-encrypted-token")
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", str(key))
    monkeypatch.setenv("GOOGLE_CHAT_DIRECTORY_TOKEN_FILE", str(token))
    bot = Mock()
    bot.get.side_effect = [
        Mock(json=Mock(return_value={"name": "spaces/FIRST_DM"})),
        Mock(json=Mock(return_value={"name": "spaces/SECOND_DM"})),
    ]
    bot.post.return_value.status_code = message_status
    user_ids = {"one@example.com": "111", "two@example.com": "222"}

    @contextmanager
    def bot_session():
        yield bot

    @contextmanager
    def directory_session():
        yield Mock()

    monkeypatch.setattr(google_chat, "_chat_session", bot_session)
    monkeypatch.setattr(google_chat, "_directory_session", directory_session)
    monkeypatch.setattr(google_chat, "_directory_user_id", lambda client, email: user_ids[email])
    result = google_chat.notify_task_created(321, {"title": "assigned task"}, [],
        [" ONE@example.com ", "two@example.com", "one@example.com"])
    assert result == {"status": "sent", "sent": 2, "total": 2}
    first, second = bot.post.call_args_list
    assert first.args[0].endswith("spaces/FIRST_DM/messages")
    assert second.args[0].endswith("spaces/SECOND_DM/messages")
    assert first.kwargs["json"]["text"].startswith("<users/111>\n")
    assert second.kwargs["json"]["text"].startswith("<users/222>\n")
    assert "<users/222>" not in first.kwargs["json"]["text"]
    assert "<users/111>" not in second.kwargs["json"]["text"]
    assert first.kwargs["params"] == second.kwargs["params"] == {
        "messageId": "client-routine-task-321",
    }


def test_missing_bot_dm_reports_installation_failure(monkeypatch, tmp_path):
    import requests
    key = tmp_path / "key.json"
    token = tmp_path / "directory.dat"
    key.write_text("{}", encoding="utf-8")
    token.write_bytes(b"test")
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", str(key))
    monkeypatch.setenv("GOOGLE_CHAT_DIRECTORY_TOKEN_FILE", str(token))
    bot = Mock()
    response = requests.Response()
    response.status_code = 404
    bot.get.side_effect = requests.HTTPError(response=response)

    @contextmanager
    def bot_session():
        yield bot

    @contextmanager
    def directory_session():
        yield Mock()

    monkeypatch.setattr(google_chat, "_chat_session", bot_session)
    monkeypatch.setattr(google_chat, "_directory_session", directory_session)
    monkeypatch.setattr(google_chat, "_directory_user_id", lambda client, email: "222")
    result = google_chat.notify_task_created(321, {}, [], ["m-mori@example.com"])
    assert result["status"] == "failed"
    assert result["failures"] == [{"recipient": "m-mori@example.com", "reason": "bot_not_installed"}]
    bot.post.assert_not_called()


def test_encrypted_bot_key_constructs_app_credentials(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.oauth2 import service_account
    import win32crypt
    import win32cryptcon
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_info = {
        "type": "service_account", "project_id": "test-project",
        "client_email": "test-bot@test-project.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token",
        "private_key": key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode("ascii"),
    }
    path = tmp_path / "bot.dat"
    path.write_bytes(win32crypt.CryptProtectData(json.dumps(key_info).encode("utf-8"),
        "test-bot", None, None, None, win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE))
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", str(path))
    with google_chat._chat_session() as session:
        assert isinstance(session.credentials, service_account.Credentials)
        assert session.credentials.scopes == ["https://www.googleapis.com/auth/chat.bot"]


def test_bot_not_configured_does_not_fall_back_to_user_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.delenv("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("GOOGLE_CHAT_TOKEN_FILE", str(tmp_path / "user.dat"))
    session = Mock()
    monkeypatch.setattr(google_chat, "_chat_session", session)
    result = google_chat.notify_task_created(321, {}, [], ["m-mori@example.com"])
    assert result["status"] == "disabled"
    session.assert_not_called()


def test_keyless_bot_uses_short_lived_app_token_without_a_private_key(monkeypatch, tmp_path):
    from google.auth import impersonated_credentials
    import win32crypt
    import win32cryptcon
    path = tmp_path / "source.dat"
    source_info = {
        "client_id": "test", "client_secret": "test", "refresh_token": "test",
        "token": "test-source-access-token", "expiry": "2099-01-01T00:00:00Z",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": google_chat.KEYLESS_BOT_SCOPES,
    }
    path.write_bytes(win32crypt.CryptProtectData(json.dumps(source_info).encode("utf-8"),
        "test-source", None, None, None, win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE))
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_SERVICE_ACCOUNT", "sys-notice@sys-notice.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_SOURCE_TOKEN_FILE", str(path))
    monkeypatch.delenv("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", raising=False)
    request = Mock(return_value=Mock(status=200, data=json.dumps({
        "accessToken": "test-bot-access-token", "expireTime": "2099-01-01T00:00:00Z",
    }).encode("utf-8")))
    with google_chat._chat_session() as session:
        credentials = session.credentials
        assert isinstance(credentials, impersonated_credentials.Credentials)
        credentials.refresh(request)
        assert credentials.token == "test-bot-access-token"
    call = request.call_args
    assert "/sys-notice@sys-notice.iam.gserviceaccount.com:generateAccessToken" in call.kwargs["url"]
    body = json.loads(call.kwargs["body"])
    assert body["scope"] == ["https://www.googleapis.com/auth/chat.bot"]
    assert body["lifetime"] == "300s"
    assert "private_key" not in source_info


def test_keyless_bot_missing_iam_scope_is_rejected(monkeypatch, tmp_path):
    import win32crypt
    import win32cryptcon
    path = tmp_path / "directory-only.dat"
    token = {"scopes": [google_chat.DIRECTORY_SCOPE]}
    path.write_bytes(win32crypt.CryptProtectData(json.dumps(token).encode("utf-8"),
        "test", None, None, None, win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE))
    monkeypatch.setenv("GOOGLE_CHAT_AUTH_MODE", "bot")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_SERVICE_ACCOUNT", "sys-notice@sys-notice.iam.gserviceaccount.com")
    monkeypatch.setenv("GOOGLE_CHAT_BOT_SOURCE_TOKEN_FILE", str(path))
    with pytest.raises(ValueError, match="scopes are missing"):
        google_chat._chat_session()


@pytest.mark.parametrize("reason,expected", [
    ("SERVICE_DISABLED", "service_disabled"),
    ("IAM_PERMISSION_DENIED", "permission_denied"),
])
def test_keyless_refresh_errors_report_required_configuration(reason, expected):
    from google.auth.exceptions import RefreshError
    body = json.dumps({"error": {"code": 403, "status": "PERMISSION_DENIED",
                                 "details": [{"reason": reason}]}})
    error = RefreshError("Unable to acquire impersonated credentials", body)
    assert google_chat._notification_failure(error, "dm") == expected


def test_authorization_token_has_restricted_acl(tmp_path):
    import win32security
    from authorize_google_chat import _secure_token
    token = tmp_path / "secured.dat"
    _secure_token(token, b"encrypted-test-data", "SYSTEM")
    assert token.read_bytes() == b"encrypted-test-data"
    descriptor = win32security.GetFileSecurity(str(token), win32security.DACL_SECURITY_INFORMATION)
    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    with pytest.raises(Exception):
        _secure_token(token, b"never-overwrite", "SYSTEM")
    assert token.read_bytes() == b"encrypted-test-data"


def test_remote_authorization_keeps_credentials_out_of_command_arguments(monkeypatch):
    import authorize_google_chat
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(authorize_google_chat.subprocess, "run", run)
    authorize_google_chat._on_mercury({"credentials": {"refresh_token": "test-sensitive-value"}})
    assert "test-sensitive-value" not in str(run.call_args.args)
    assert "test-sensitive-value" in run.call_args.kwargs["input"]


@pytest.mark.skipif(os.environ.get("ROUTINE_TEST_DATABASE") != "1", reason="database opt-in")
def test_db_attachment_lifecycle_rollback(client, monkeypatch):
    connection = api._get_db_connection()

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return connection.cursor()

        def commit(self):
            pass

    monkeypatch.setattr(api, "_get_db_connection", lambda: Transaction())
    monkeypatch.setattr(api, "notify_task_created", Mock(return_value={"status": "disabled"}))
    try:
        created = client.post("/api.py/routines", data={
            "data": json.dumps(task_data()),
            "attachments": (io.BytesIO(b"\x00\xffhello"), "\u8cc7\u6599.pdf"),
        })
        assert created.status_code == 201, created.json
        task_no = created.json["task_no"]
        base = f"/api.py/parent/{task_no}/attachments"
        listing = client.get(base)
        assert listing.status_code == 200
        file = listing.json["attachments"][0]
        assert file["filename"] == "\u8cc7\u6599.pdf"
        assert client.get(f"{base}/{file['attachment_id']}").data == b"\x00\xffhello"
        assert client.get(f"/api.py/parent/0/attachments/{file['attachment_id']}").status_code == 404
        updated = client.patch(f"/api.py/parent/{task_no}", data={
            "data": json.dumps({"title": "renamed"}),
            "attachments": (io.BytesIO(b"new"), "memo.txt"),
        })
        assert updated.status_code == 204, updated.json
        assert len(client.get(base).json["attachments"]) == 2
        attachment_only = client.patch(f"/api.py/parent/{task_no}", data={
            "data": "{}", "attachments": (io.BytesIO(b"only-file"), "file-only.txt"),
        })
        assert attachment_only.status_code == 204, attachment_only.json
        assert len(client.get(base).json["attachments"]) == 3
        assert client.delete(f"{base}/{file['attachment_id']}").status_code == 204
        assert client.get(f"{base}/{file['attachment_id']}").status_code == 404
        assert len(client.get(base).json["attachments"]) == 2
        assert client.post(f"/api.py/parent/{task_no}/complete").status_code == 204
        assert client.get(base).status_code == 404
    finally:
        connection.rollback()
        connection.close()
