"""Run on Mercury to authorize task DMs without changing approval credentials."""

import argparse
import json
import logging
import socket
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from google_chat import (
    CHAT_SCOPES, DIRECTORY_SCOPE, KEYLESS_BOT_SCOPES,
    bot_source_token_path, chat_token_path, directory_token_path,
)


def _secure_token(path: Path, encrypted: bytes, reader: str) -> None:
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    # Machine DPAPI lets IIS decrypt the token; the DACL limits who can read it.
    acl = win32security.ACL()
    current_user = win32api.GetUserNameEx(win32con.NameSamCompatible)
    for identity, access in [
        (current_user, ntsecuritycon.FILE_ALL_ACCESS),
        ("SYSTEM", ntsecuritycon.FILE_ALL_ACCESS),
        ("BUILTIN\\Administrators", ntsecuritycon.FILE_ALL_ACCESS),
        (reader, ntsecuritycon.FILE_GENERIC_READ),
    ]:
        sid, _, _ = win32security.LookupAccountName(None, identity)
        acl.AddAccessAllowedAce(win32security.ACL_REVISION, access, sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    descriptor.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    import pywintypes
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    import win32file
    handle = win32file.CreateFile(
        str(path), win32con.GENERIC_WRITE, 0, attributes,
        win32con.CREATE_NEW, win32con.FILE_ATTRIBUTE_NORMAL, None,
    )
    try:
        win32file.WriteFile(handle, encrypted)
        win32file.FlushFileBuffers(handle)
    finally:
        handle.Close()


def _validate_token_destination(path: Path, reader: str) -> None:
    import win32security

    path = path.resolve()
    if path.exists():
        raise ValueError("Token file already exists. Use a new token path for reauthorization.")
    if not path.parent.is_dir() or path.is_relative_to(Path(__file__).resolve().parent):
        raise ValueError("Use an existing token directory outside the web root.")
    win32security.LookupAccountName(None, reader)


def _on_mercury(payload: dict) -> None:
    # Send credential data over encrypted WinRM stdin, never command arguments.
    command = r"""
$ErrorActionPreference = 'Stop'
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$options = New-PSSessionOption -OpenTimeout 5000 -OperationTimeout 10000
Invoke-Command -ComputerName mercury -SessionOption $options -ArgumentList ($payload | ConvertTo-Json -Depth 10 -Compress) -ScriptBlock {
    param($payloadJson)
    $payloadJson | & 'D:\asp\routine_app\.venv\Scripts\python.exe' 'D:\asp\routine_app\authorize_google_chat.py' --store-stdin
    if ($LASTEXITCODE -ne 0) { throw 'Google Chat token validation or storage failed' }
}
"""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(payload), text=True, capture_output=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Mercury token validation or storage failed; credentials were not logged")


def _store_on_mercury(payload: dict) -> None:
    import win32crypt
    import win32cryptcon

    if socket.gethostname().split(".", 1)[0].lower() != "mercury":
        raise ValueError("Token storage must run on Mercury")
    path = Path(payload["path"])
    _validate_token_destination(path, payload["reader"])
    if payload.get("validate_only"):
        return
    token_info = payload["credentials"]
    if payload.get("bot"):
        from google.oauth2 import service_account
        service_account.Credentials.from_service_account_info(
            token_info, scopes=["https://www.googleapis.com/auth/chat.bot"],
        )
    else:
        scopes = KEYLESS_BOT_SCOPES if payload.get("keyless_bot") else (
            [DIRECTORY_SCOPE] if payload.get("directory") else CHAT_SCOPES
        )
        if not set(scopes).issubset(token_info.get("scopes", [])) or not token_info.get("refresh_token"):
            raise ValueError("Offline Google Chat authorization is incomplete")
    encrypted = win32crypt.CryptProtectData(
        json.dumps(token_info).encode("utf-8"), "Routine App Google Chat OAuth",
        None, None, None, win32cryptcon.CRYPTPROTECT_LOCAL_MACHINE,
    )
    _secure_token(path, encrypted, payload["reader"])


def main() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow
    import sys

    load_dotenv(Path(__file__).resolve().parent / ".env")
    if "--store-stdin" in sys.argv:
        _store_on_mercury(json.load(sys.stdin))
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reader", required=True, help="IIS worker identity, e.g. IIS APPPOOL\\routine_app")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--directory", action="store_true", help="Authorize recipient lookup for bot delivery (no user-authenticated messages)")
    parser.add_argument("--keyless-bot", action="store_true", help="Authorize short-lived bot token creation and recipient lookup without a service account key")
    parser.add_argument("--bot-key-file", type=Path, help="Install and encrypt an existing service account key; requires --token-file")
    parser.add_argument("--client-file", type=Path,
                        default=Path(r"\\mercury\task\approval_connections\people_oauth_client.json"))
    parser.add_argument("--remote", action="store_true", help="Authorize locally and store securely on Mercury through WinRM")
    parser.add_argument("--no-browser", action="store_true", help="Print the consent link instead of opening a browser")
    args = parser.parse_args()
    if args.keyless_bot and (args.directory or args.bot_key_file):
        parser.error("Keyless bot authorization cannot use --directory or --bot-key-file")
    if args.bot_key_file and (args.directory or not args.token_file):
        parser.error("Bot key installation requires --token-file and cannot use --directory")
    if not args.remote and socket.gethostname().split(".", 1)[0].lower() != "mercury":
        parser.error("Run this command on Mercury; machine DPAPI is host-specific.")
    default_path = bot_source_token_path() if args.keyless_bot else (
        directory_token_path() if args.directory else chat_token_path()
    )
    token_path = args.token_file or default_path
    payload = {"path": str(token_path), "reader": args.reader, "directory": args.directory,
               "bot": bool(args.bot_key_file), "keyless_bot": args.keyless_bot}
    if args.remote:
        _on_mercury({**payload, "validate_only": True})
    else:
        _validate_token_destination(token_path, args.reader)
    if args.bot_key_file:
        from google.oauth2 import service_account
        with args.bot_key_file.open(encoding="utf-8-sig") as key_file:
            payload["credentials"] = json.load(key_file)
        service_account.Credentials.from_service_account_info(
            payload["credentials"], scopes=["https://www.googleapis.com/auth/chat.bot"],
        )
        if args.remote:
            _on_mercury(payload)
        else:
            _store_on_mercury(payload)
        logging.info("Google Chat bot credentials saved at %s for %s", token_path, args.reader)
        return 0
    with args.client_file.open(encoding="utf-8-sig") as client_file:
        client_config = json.load(client_file)
    scopes = KEYLESS_BOT_SCOPES if args.keyless_bot else (
        [DIRECTORY_SCOPE] if args.directory else CHAT_SCOPES
    )
    flow = InstalledAppFlow.from_client_config(client_config, scopes=scopes)
    credentials = flow.run_local_server(
        port=0, access_type="offline", prompt="consent select_account", timeout_seconds=300,
        open_browser=not args.no_browser,
    )
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return an offline refresh token")
    payload["credentials"] = json.loads(credentials.to_json())
    if args.remote:
        _on_mercury(payload)
    else:
        _store_on_mercury(payload)
    logging.info("Google Chat authorization saved at %s for %s", token_path, args.reader)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("google_auth_oauthlib.flow").setLevel(logging.WARNING)
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.error("Google Chat authorization was not completed (%s). Run again to retry.", type(exc).__name__)
        raise SystemExit(1)
