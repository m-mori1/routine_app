"""Send task notifications to a configured space or legacy recipient DMs."""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit


CHAT_SCOPES = [
    "https://www.googleapis.com/auth/chat.spaces.create",
    "https://www.googleapis.com/auth/chat.messages.create",
]
DIRECTORY_SCOPE = "https://www.googleapis.com/auth/directory.readonly"
IAM_SCOPE = "https://www.googleapis.com/auth/iam"
KEYLESS_BOT_SCOPES = [IAM_SCOPE, DIRECTORY_SCOPE]


def bot_source_token_path() -> Path:
    return Path(os.environ.get(
        "GOOGLE_CHAT_BOT_SOURCE_TOKEN_FILE",
        r"D:\task\approval_connections\routine_app_bot_source_token.dat",
    ))


def directory_token_path() -> Path:
    return Path(os.environ.get(
        "GOOGLE_CHAT_DIRECTORY_TOKEN_FILE",
        r"D:\task\approval_connections\routine_app_directory_token.dat",
    ))


def bot_credentials_path() -> Path | None:
    value = os.environ.get("GOOGLE_CHAT_BOT_CREDENTIALS_FILE", "").strip()
    return Path(value) if value else None


def chat_token_path() -> Path:
    return Path(os.environ.get(
        "GOOGLE_CHAT_TOKEN_FILE",
        r"\\mercury\task\approval_connections\routine_app_chat_token.dat",
    ))


def _authorized_session(credentials: Any) -> Any:
    from google.auth.transport.requests import AuthorizedSession, Request
    # Bound token refresh as well as the Chat API requests.
    auth_request = Request()

    def bounded_auth_request(*args, **kwargs):
        kwargs["timeout"] = 10
        return auth_request(*args, **kwargs)

    return AuthorizedSession(
        credentials, auth_request=bounded_auth_request, refresh_timeout=10
    )


def _protected_credentials(path: Path) -> dict:
    import win32crypt

    encrypted = path.read_bytes()
    token_info = json.loads(win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode("utf-8"))
    if not isinstance(token_info, dict):
        raise ValueError("Google credentials must be an object")
    return token_info


def _oauth_credentials(path: Path, scopes: list[str]) -> Any:
    from google.oauth2.credentials import Credentials

    token_info = _protected_credentials(path)
    if not set(scopes).issubset(token_info.get("scopes", [])):
        raise ValueError("Google authorization scopes are missing")
    return Credentials.from_authorized_user_info(token_info, scopes=scopes)


def _oauth_session(path: Path, scopes: list[str]) -> Any:
    return _authorized_session(_oauth_credentials(path, scopes))


def _chat_session() -> Any:
    if os.environ.get("GOOGLE_CHAT_AUTH_MODE", "user") != "bot":
        return _oauth_session(chat_token_path(), CHAT_SCOPES)
    target = os.environ.get("GOOGLE_CHAT_BOT_SERVICE_ACCOUNT", "").strip()
    if target:
        from google.auth import impersonated_credentials

        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*@[a-z0-9-]+\.iam\.gserviceaccount\.com", target):
            raise ValueError("Invalid bot service account email")
        source = _oauth_credentials(bot_source_token_path(), [IAM_SCOPE])
        return _authorized_session(impersonated_credentials.Credentials(
            source_credentials=source, target_principal=target,
            target_scopes=["https://www.googleapis.com/auth/chat.bot"], lifetime=300,
        ))
    from google.oauth2 import service_account

    path = bot_credentials_path()
    if path is None:
        raise ValueError("Google Chat bot credentials are missing")
    if path.suffix.lower() == ".dat":
        return _authorized_session(service_account.Credentials.from_service_account_info(
            _protected_credentials(path), scopes=["https://www.googleapis.com/auth/chat.bot"],
        ))
    return _authorized_session(service_account.Credentials.from_service_account_file(
        str(path), scopes=["https://www.googleapis.com/auth/chat.bot"],
    ))


def _directory_session() -> Any:
    return _oauth_session(directory_token_path(), [DIRECTORY_SCOPE])


def _directory_user_id(client: Any, email: str) -> str:
    response = client.get(
        "https://people.googleapis.com/v1/people:searchDirectoryPeople",
        params={"query": email, "readMask": "emailAddresses",
                "sources": "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"},
        timeout=10,
    )
    response.raise_for_status()
    matches = {
        str(person.get("resourceName", "")).removeprefix("people/")
        for person in response.json().get("people", [])
        if any(str(address.get("value", "")).strip().lower() == email
               for address in person.get("emailAddresses", []))
    }
    if len(matches) != 1:
        raise LookupError("Google directory recipient was not uniquely identified")
    user_id = matches.pop()
    if not re.fullmatch(r"[0-9]+", user_id):
        raise ValueError("Google directory returned an invalid user ID")
    return user_id


def _notification_failure(exc: Exception, stage: str) -> str:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    payloads = []
    if response is not None:
        try:
            payloads.append(response.json())
        except (ValueError, AttributeError):
            pass
    for item in exc.args:
        if isinstance(item, dict):
            payloads.append(item)
        elif isinstance(item, str):
            try:
                payloads.append(json.loads(item))
            except ValueError:
                pass
    error = next((payload.get("error") for payload in payloads
                  if isinstance(payload, dict) and "error" in payload), {})
    if error == "invalid_grant":
        return "authorization_expired"
    if not isinstance(error, dict):
        error = {}
    reasons = {detail.get("reason") for detail in error.get("details", [])
               if isinstance(detail, dict)}
    if "SERVICE_DISABLED" in reasons:
        return "service_disabled"
    status = status or error.get("code")
    if status == 404 and "google chat app not found" in error.get("message", "").lower():
        return "bot_not_configured"
    if stage == "directory" and isinstance(exc, LookupError):
        return "recipient_not_found"
    if status == 404 and stage == "dm":
        return "bot_not_installed"
    if status == 403:
        return "permission_denied"
    if status == 429:
        return "rate_limited"
    if status == 400:
        if error.get("status") == "INVALID_ARGUMENT" and "calling user as a membership" in error.get("message", "").lower():
            return "sender_is_recipient"
    return "request_failed"


def _task_created_text(task_no: int, parent: dict, children: list) -> str:
    lines = [
        "\u30bf\u30b9\u30af\u304c\u4f5c\u6210\u3055\u308c\u307e\u3057\u305f\u3002",
        f"\u30bf\u30b9\u30afNo: {task_no}",
        f"\u30bf\u30b9\u30af\u540d: {parent.get('title', '')}",
        f"\u983b\u5ea6: {parent.get('frequency', '')}",
        f"\u62c5\u5f53\u8005: {parent.get('assignee', '')}",
        f"\u767b\u9332\u8005: {parent.get('registrant', '')}",
    ]
    due_date = parent.get("due_date") or (
        children[0].get("due_date") if children else None
    )
    if due_date:
        lines.append(f"\u671f\u65e5: {due_date}")
    if parent.get("start_month"):
        lines.append(
            f"\u671f\u9593: {parent['start_month']} - {parent.get('end_month', '')}"
        )
    if parent.get("summary"):
        lines.append(f"\u5099\u8003: {parent['summary']}")
    app_url = os.environ.get("ROUTINE_PUBLIC_URL", "").rstrip("/")
    if app_url.startswith("https://"):
        lines.append(f"{app_url}/#parents")
    return "\n".join(lines)


def _space_webhook_url() -> str:
    value = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL", "").strip()
    config_file = os.environ.get("GOOGLE_CHAT_WEBHOOK_CONFIG_FILE", "").strip()
    if not value and config_file:
        path = Path(config_file)
        config = _protected_credentials(path) if path.suffix.lower() == ".dat" else json.loads(
            path.read_text(encoding="utf-8")
        )
        value = str(config.get("GOOGLE_CHAT_WEBHOOK_URL", "")).strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    space = re.fullmatch(r"/v1/spaces/([A-Za-z0-9_-]+)/messages", parsed.path)
    expected = os.environ.get("GOOGLE_CHAT_SPACE_ID", "").strip()
    if (
        parsed.scheme != "https" or parsed.netloc != "chat.googleapis.com"
        or parsed.fragment or not space or not expected or space[1] != expected
        or set(query) != {"key", "token"}
        or any(len(values) != 1 or not values[0] for values in query.values())
    ):
        raise ValueError("Invalid or unconfirmed task notification webhook")
    return value


def _webhook_session() -> Any:
    from requests import Session

    return Session()


def _notify_space_created(
    task_no: int, parent: dict, children: list, recipients: list[str]
) -> dict:
    result = {"status": "failed", "sent": 0, "total": len(recipients),
              "delivery": "space", "posted": False}
    failures = []
    mentions = []
    resolved = 0
    logger = logging.getLogger(__name__)
    stage = "webhook_configuration"
    try:
        url = _space_webhook_url()
        if not url or not directory_token_path().is_file():
            result["status"] = "disabled"
            return result
        stage = "directory"
        with _directory_session() as directory:
            for email in recipients:
                try:
                    if not re.fullmatch(r"[^\s/@]+@[^\s/@]+\.[^\s/@]+", email):
                        raise LookupError("Invalid notification recipient")
                    user_id = _directory_user_id(directory, email)
                    mention = f"<users/{user_id}>"
                    if mention not in mentions:
                        mentions.append(mention)
                    resolved += 1
                except Exception as exc:
                    reason = _notification_failure(exc, "directory")
                    failures.append({"recipient": email, "reason": reason})
                    logger.warning(
                        "Google Chat space mention failed task=%s recipient=%s reason=%s",
                        task_no, email, reason,
                    )
        # Task text must not inject @all or mentions outside the assignee list.
        body = _task_created_text(task_no, parent, children).replace("<", "\uff1c").replace(">", "\uff1e")
        text = "\n".join([" ".join(mentions), body]).lstrip("\n")
        stage = "webhook_message"
        with _webhook_session() as client:
            response = client.post(
                url, params={"messageId": f"client-routine-task-{task_no}"},
                json={"text": text}, timeout=10, allow_redirects=False,
            )
            if response.status_code != 409:
                response.raise_for_status()
                if response.status_code not in {200, 201}:
                    raise ValueError("Unexpected webhook response")
        result.update(status="partial" if failures else "sent", sent=resolved, posted=True)
    except Exception as exc:
        if stage == "webhook_configuration":
            reason = "webhook_invalid"
        elif stage == "webhook_message" and getattr(getattr(exc, "response", None), "status_code", None) == 404:
            reason = "webhook_not_found"
        else:
            reason = _notification_failure(exc, stage)
        failures.append({"reason": reason})
        # HTTP exceptions can contain the webhook's secret query parameters.
        logger.error("Google Chat space failed task=%s stage=%s reason=%s error=%s",
                     task_no, stage, reason, type(exc).__name__)
    if failures:
        result["failures"] = failures
    return result


def notify_task_created(
    task_no: int, parent: dict, children: list, recipients: list[str]
) -> dict:
    recipients = list(dict.fromkeys(email.strip().lower() for email in recipients))
    if not recipients:
        return {"status": "skipped", "reason": "no_recipients",
                "sent": 0, "total": 0, "posted": False,
                "delivery": "space" if os.environ.get("GOOGLE_CHAT_AUTH_MODE") == "webhook" else "dm"}
    if os.environ.get("GOOGLE_CHAT_AUTH_MODE", "user") == "webhook":
        return _notify_space_created(task_no, parent, children, recipients)
    is_bot = os.environ.get("GOOGLE_CHAT_AUTH_MODE", "user") == "bot"
    if is_bot:
        target = os.environ.get("GOOGLE_CHAT_BOT_SERVICE_ACCOUNT", "").strip()
        path = bot_source_token_path() if target else bot_credentials_path()
        if path is None or not path.is_file() or not directory_token_path().is_file():
            return {"status": "disabled", "sent": 0, "total": len(recipients)}
    elif not chat_token_path().is_file():
        return {"status": "disabled", "sent": 0, "total": len(recipients)}
    result = {"status": "failed", "sent": 0, "total": len(recipients)}
    failures = []
    delivered = set()
    logger = logging.getLogger(__name__)
    try:
        text = _task_created_text(task_no, parent, children)
        with _chat_session() as client:
            from contextlib import ExitStack
            with ExitStack() as sessions:
                directory = sessions.enter_context(_directory_session()) if is_bot else None
                for email in recipients:
                    stage = "directory" if is_bot else "dm"
                    try:
                        if not re.fullmatch(r"[^\s/@]+@[^\s/@]+\.[^\s/@]+", email):
                            raise LookupError("Invalid Google Chat recipient email")
                        if is_bot:
                            user_id = _directory_user_id(directory, email)
                            stage = "dm"
                            response = client.get(
                                "https://chat.googleapis.com/v1/spaces:findDirectMessage?"
                                + urlencode({"name": f"users/{user_id}"}), timeout=10,
                            )
                        else:
                            response = client.post(
                                "https://chat.googleapis.com/v1/spaces:setup",
                                json={
                                    "space": {"spaceType": "DIRECT_MESSAGE", "singleUserBotDm": False},
                                    "memberships": [{"member": {"name": f"users/{email}", "type": "HUMAN"}}],
                                }, timeout=10,
                            )
                        response.raise_for_status()
                        space = response.json().get("name", "")
                        if not re.fullmatch(r"spaces/[A-Za-z0-9_-]+", space):
                            raise ValueError("Invalid direct-message space name")
                        stage = "message"
                        message_text = f"<users/{user_id}>\n{text}" if is_bot else text
                        response = client.post(
                            f"https://chat.googleapis.com/v1/{space}/messages",
                            params={"messageId": f"client-routine-task-{task_no}"},
                            json={"text": message_text}, timeout=10,
                        )
                        # Retrying the same task must not send a duplicate DM.
                        if response.status_code != 409:
                            response.raise_for_status()
                        result["sent"] += 1
                        delivered.add(email)
                    except Exception as exc:
                        reason = _notification_failure(exc, stage)
                        failures.append({"recipient": email, "reason": reason})
                        logger.warning(
                            "Google Chat DM failed task=%s recipient=%s stage=%s reason=%s status=%s",
                            task_no, email, stage, reason,
                            getattr(getattr(exc, "response", None), "status_code", None),
                        )
    except Exception as exc:
        # Never expose credential contents or token-bearing HTTP errors in logs.
        logger.error("Google Chat setup failed task=%s error=%s", task_no, type(exc).__name__)
        failures.extend({"recipient": email, "reason": "configuration_error"}
                        for email in recipients if email not in delivered
                        and email not in {failure["recipient"] for failure in failures})
    if result["sent"] == result["total"] and result["total"]:
        result["status"] = "sent"
    elif result["sent"]:
        result["status"] = "partial"
    if failures:
        result["failures"] = failures
    return result
