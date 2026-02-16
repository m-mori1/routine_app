"""Simple configuration helper for the routine task API.

Put your database credentials (or toggle to use the environment variable) before
starting the Flask server. The helper defaults to the `mercury` host and
`routine` database so you only need to supply the user/password you want to test.
"""

import os

DB_DRIVER = "ODBC Driver 18 for SQL Server"
DB_SERVER = "mercury"
DB_DATABASE = "routine"
DB_USER = "bi"
DB_PASSWORD = "bi"
TRUST_SERVER_CERTIFICATE = True


def get_connection_string():
    """Return a connection string that prefers explicit config but allows an env var."""
    env_conn = os.environ.get("ROUTINE_DB_CONN")
    if env_conn:
        return env_conn
    if not DB_USER or not DB_PASSWORD:
        raise RuntimeError(
            "Fill db_config.DB_USER and DB_PASSWORD or set ROUTINE_DB_CONN before starting the app."
        )
    trust_flag = "yes" if TRUST_SERVER_CERTIFICATE else "no"
    return (
        f"Driver={DB_DRIVER};"
        f"Server={DB_SERVER};"
        f"Database={DB_DATABASE};"
        f"Uid={DB_USER};"
        f"Pwd={DB_PASSWORD};"
        f"TrustServerCertificate={trust_flag};"
    )
