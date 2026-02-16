from datetime import date, datetime
import calendar
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import logging
import msal
import pyodbc
from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for

from db_config import get_connection_string

BASE_DIR = Path(__file__).resolve().parent
base_dir = BASE_DIR
load_dotenv(BASE_DIR / ".env")

ACCOUNT_SCHEMA = os.environ.get("ACCOUNT_SCHEMA", "")
ACCOUNT_DATABASE = os.environ.get("ACCOUNT_DATABASE", "")

ENTRA_CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "")
ENTRA_CLIENT_SECRET = os.environ.get("ENTRA_CLIENT_SECRET", "")
ENTRA_TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "")
ENTRA_SCOPES = os.environ.get("ENTRA_SCOPES", "User.Read").split()
ENTRA_AUTHORITY = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}" if ENTRA_TENANT_ID else ""
FALLBACK_DEPARTMENT_CD = os.environ.get("FALLBACK_DEPARTMENT_CD", "D000013")
FALLBACK_DEPARTMENT_NAME = os.environ.get("FALLBACK_DEPARTMENT_NAME", "システム")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
_ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN = None
_ROUTINE_CHILD_HAS_TITLE_COLUMN = None

def _get_db_connection():
    return pyodbc.connect(get_connection_string())


def _routine_child_has_assignee_column():
    global _ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN
    if _ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN is not None:
        return _ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN
    query = "SELECT COL_LENGTH('dbo.routine_task_child', 'assignee')"
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            row = cursor.fetchone()
            _ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN = bool(row and row[0] is not None)
    except pyodbc.Error:
        _ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN = False
    return _ROUTINE_CHILD_HAS_ASSIGNEE_COLUMN


def _routine_child_has_title_column():
    global _ROUTINE_CHILD_HAS_TITLE_COLUMN
    if _ROUTINE_CHILD_HAS_TITLE_COLUMN is not None:
        return _ROUTINE_CHILD_HAS_TITLE_COLUMN
    query = "SELECT COL_LENGTH('dbo.routine_task_child', 'title')"
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            row = cursor.fetchone()
            _ROUTINE_CHILD_HAS_TITLE_COLUMN = bool(row and row[0] is not None)
    except pyodbc.Error:
        _ROUTINE_CHILD_HAS_TITLE_COLUMN = False
    return _ROUTINE_CHILD_HAS_TITLE_COLUMN


def _parse_pagination_params(query_params, default_page_size):
    try:
        page = int(query_params.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(query_params.get("page_size", default_page_size))
    except (TypeError, ValueError):
        page_size = default_page_size
    if page < 1:
        page = 1
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    return page, page_size

def _normalize_upn(upn):
    if not upn:
        return None
    return upn.split("@", 1)[0]


def _qualified_table(table_name):
    if ACCOUNT_SCHEMA:
        if "." in ACCOUNT_SCHEMA:
            return f"{ACCOUNT_SCHEMA}.{table_name}"
        if ACCOUNT_DATABASE:
            return f"{ACCOUNT_DATABASE}.{ACCOUNT_SCHEMA}.{table_name}"
        return f"{ACCOUNT_SCHEMA}.{table_name}"
    if ACCOUNT_DATABASE:
        return f"{ACCOUNT_DATABASE}.dbo.{table_name}"
    return table_name


def _fetch_employee_profile(upn):
    upn_short = _normalize_upn(upn)
    if not upn_short:
        return None
    employee_table = _qualified_table("Employee")
    department_table = _qualified_table("Department")
    query = f"""
        SELECT
            e.UserID,
            e.EmployeeName,
            e.AD,
            e.DepartmentCD,
            d.DepartmentName,
            d.IsApprovalDept
        FROM {employee_table} e
        LEFT JOIN {department_table} d ON e.DepartmentCD = d.DepartmentCD
        WHERE UPPER(e.AD) = UPPER(?)
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, [upn_short])
            row = cursor.fetchone()
            logging.getLogger(__name__).debug(
                "fetched employee profile row=%s columns=%s for upn=%s",
                row,
                [column[0] for column in cursor.description] if cursor.description else None,
                upn_short,
            )
            if not row:
                return {
                    "UserID": None,
                    "EmployeeName": upn_short,
                    "AD": upn_short,
                    "DepartmentCD": FALLBACK_DEPARTMENT_CD,
                    "DepartmentName": FALLBACK_DEPARTMENT_NAME,
                    "IsApprovalDept": False,
                }
            columns = [column[0] for column in cursor.description]
            return dict(zip(columns, row))
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to fetch employee profile") from exc


def _fetch_departments():
    department_table = _qualified_table("Department")
    query = f"""
        SELECT DepartmentCD, DepartmentName, IsApprovalDept
        FROM {department_table}
        WHERE DeleteDt IS NULL
        ORDER BY DepartmentCD
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to fetch departments") from exc


def _current_user_context():
    claims = session.get("user") or {}
    upn = (
        claims.get("preferred_username")
        or claims.get("upn")
        or claims.get("email")
        or claims.get("name")
    )
    profile = None
    try:
        profile = _fetch_employee_profile(upn)
    except RuntimeError:
        profile = None
    profile_data = profile or {}
    employee_name = (
        profile_data.get("EmployeeName")
        or claims.get("name")
        or claims.get("preferred_username")
        or ""
    )
    department = profile_data.get("DepartmentName") or FALLBACK_DEPARTMENT_NAME
    return {
        "upn": upn,
        "name": employee_name,
        "employee_name": employee_name,
        "department_name": department,
        "department_cd": profile_data.get("DepartmentCD") or FALLBACK_DEPARTMENT_CD,
        "is_approval_dept": bool(profile_data.get("IsApprovalDept")),
        "employee_id": profile_data.get("UserID"),
    }


def _build_msal_app(cache=None):
    if not ENTRA_CLIENT_ID or not ENTRA_TENANT_ID or not ENTRA_CLIENT_SECRET:
        raise RuntimeError("Entra ID configuration is incomplete")
    return msal.ConfidentialClientApplication(
        ENTRA_CLIENT_ID,
        client_credential=ENTRA_CLIENT_SECRET,
        authority=ENTRA_AUTHORITY,
        token_cache=cache,
    )

def _build_auth_flow():
    msal_app = _build_msal_app()
    redirect_uri = url_for("auth_redirect", _external=True)
    return msal_app.initiate_auth_code_flow(ENTRA_SCOPES, redirect_uri=redirect_uri)


def _parse_ym(value):
    if not value:
        raise ValueError("start_month and end_month must be in YYYY-MM format")
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError("start_month and end_month must match YYYY-MM")
    year = int(parts[0])
    month = int(parts[1])
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    return year, month


def _next_month(year, month, delta):
    month += delta
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return year, month


def _month_leq(a_year, a_month, b_year, b_month):
    return (a_year < b_year) or (a_year == b_year and a_month <= b_month)


def _generate_months(start_year, start_month, end_year, end_month, step):
    year, month = start_year, start_month
    months = []
    while _month_leq(year, month, end_year, end_month):
        months.append((year, month))
        year, month = _next_month(year, month, step)
    return months


def _nth_friday(year, month, week_num):
    week_num = max(1, min(week_num, 4))
    first_day = date(year, month, 1)
    first_weekday = first_day.weekday()
    delta = (4 - first_weekday + 7) % 7
    day = 1 + delta + 7 * (week_num - 1)
    last_day = calendar.monthrange(year, month)[1]
    if day > last_day:
        day = last_day
    return date(year, month, day)


def _parse_assignees(value):
    if not value:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(";")]
    elif isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value]
    else:
        return []
    return [part for part in parts if part]


def _format_assignees(value):
    entries = _parse_assignees(value)
    return "; ".join(entries) if entries else None


def _half_year_from_quarter(quarter_value):
    try:
        quarter_num = int(quarter_value)
    except (TypeError, ValueError):
        return None
    if quarter_num < 1 or quarter_num > 4:
        return None
    return 1 if quarter_num <= 2 else 2


def _normalize_task_kind(value, assignee_value=None):
    normalized = str(value).strip() if value is not None else ""
    if normalized in {"グループ", "group", "Group"}:
        return "グループ"
    if normalized in {"個人", "individual", "Individual"}:
        return "個人"
    assignees = _parse_assignees(assignee_value)
    return "グループ" if len(assignees) > 1 else "個人"


def _validate_task_kind_assignees(task_kind, assignee_value):
    assignees = _parse_assignees(assignee_value)
    if task_kind == "グループ" and len(assignees) < 2:
        raise ValueError("タスク区分がグループの場合、担当者は2名以上を選択してください")


def _fetch_parent_task_kind(task_no):
    query = "SELECT task_kind FROM dbo.routine_task WHERE task_no = ?"
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, [task_no])
            row = cursor.fetchone()
            if not row:
                return None
            return _normalize_task_kind(row[0])
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to fetch parent task kind") from exc


def _build_entries(data, registrant, department_cd=None):
    frequency = data.get("frequency", "").strip()
    if not frequency:
        raise ValueError("frequency is required")

    normalized_freq = frequency.strip()
    is_spot = normalized_freq in {"スポット", "spot", "Spot"}

    week_str = data.get("week")
    week_num = None
    week_optional_freqs = {
        "スポット",
        "spot",
        "Spot",
        "週次",
        "weekly",
        "Weekly",
    }
    needs_week = normalized_freq not in week_optional_freqs
    if needs_week:
        if week_str is None or week_str == "":
            raise ValueError("week number is required when not a spot task")
        week_num = int(week_str)
        if week_num < 1 or week_num > 4:
            raise ValueError("week number must be between 1 and 4")
    else:
        if week_str:
            week_num = int(week_str)
            if week_num < 1 or week_num > 4:
                raise ValueError("week number must be between 1 and 4")
        else:
            week_num = 1

    summary_value = data.get("summary")
    status_value = data.get("status") or "未着手"
    assignee_value = _format_assignees(data.get("assignee"))

    def _create_child(seq, due_date):
        return {
            "routine_no": seq,
            "due_date": due_date,
            "title": data.get("title"),
            "assignee": assignee_value,
            "status": status_value,
            "summary": summary_value,
        }

    task_kind_value = _normalize_task_kind(data.get("task_kind"), assignee_value)
    _validate_task_kind_assignees(task_kind_value, assignee_value)

    if is_spot:
        due_str = data.get("due_date")
        if not due_str:
            raise ValueError("due_date is required for spot tasks")
        due_date = date.fromisoformat(due_str)
        due_year = due_date.year
        due_month = due_date.month
        due_week = ((due_date.day - 1) // 7) + 1
        start_month_str = f"{due_year:04d}-{due_month:02d}"
        quarter_value = str((due_month - 1) // 3 + 1)
        parent_entry = {
            "frequency": frequency,
            "half_year": None,
            "due_date": due_date,
            "start_month": start_month_str,
            "department_cd": department_cd,
            "end_month": start_month_str,
            "year": due_year,
            "quarter": quarter_value,
            "month": due_month,
            "week_num": due_week,
            "assignee": assignee_value,
            "task_kind": task_kind_value,
            "registrant": registrant,
            "status": status_value,
            "title": data.get("title"),
            "attachment_link": data.get("attachment_link"),
            "summary": summary_value,
        }
        child_entry = _create_child(1, due_date)
        return parent_entry, [child_entry]

    start_year, start_month = _parse_ym(data.get("start_month"))
    end_year, end_month = _parse_ym(data.get("end_month"))
    if not _month_leq(start_year, start_month, end_year, end_month):
        raise ValueError("end_month must be the same or after start_month")

    quarter_value = data.get("quarter")
    if quarter_value:
        quarter_value = str(int(quarter_value))
    else:
        quarter_value = str((start_month - 1) // 3 + 1)
    month_value = data.get("month")
    if month_value:
        month_value = int(month_value)
    else:
        month_value = start_month
        if normalized_freq in {"四半期", "quarterly"}:
            month_value = ((month_value - 1) % 3) + 1

    half_value = _half_year_from_quarter(quarter_value)
    if half_value is None:
        half_raw = data.get("half_year")
        half_value = int(half_raw) if half_raw else None

    parent_entry = {
        "frequency": frequency,
        "half_year": half_value,
        "due_date": None,
        "start_month": f"{start_year:04d}-{start_month:02d}",
        "department_cd": department_cd,
        "end_month": f"{end_year:04d}-{end_month:02d}",
        "year": start_year,
        "quarter": quarter_value,
        "month": month_value,
        "week_num": week_num,
        "assignee": assignee_value,
        "task_kind": task_kind_value,
        "registrant": registrant,
        "status": status_value,
        "title": data.get("title"),
        "attachment_link": data.get("attachment_link"),
        "summary": summary_value,
    }

    generation_start_year = start_year
    generation_start_month = start_month
    if normalized_freq in {"四半期", "quarterly"} and month_value:
        if month_value < 1 or month_value > 3:
            month_value = ((month_value - 1) % 3) + 1
        gen_year = start_year
        gen_month = month_value
        while gen_year < start_year or (
            gen_year == start_year and gen_month < start_month
        ):
            gen_month += 3
            if gen_month > 12:
                gen_month -= 12
                gen_year += 1
        generation_start_year = gen_year
        generation_start_month = gen_month

    step_map = {
        "週次": 1,
        "月次": 1,
        "四半期": 3,
        "半期": 6,
        "年次": 12,
        "weekly": 1,
        "monthly": 1,
        "quarterly": 3,
        "half-year": 6,
        "halfyear": 6,
        "yearly": 12,
    }
    step = step_map.get(normalized_freq)
    if step is None:
        raise ValueError("unknown frequency")

    months = _generate_months(
        generation_start_year, generation_start_month, end_year, end_month, step
    )
    entries = []
    for seq, (year, month) in enumerate(months, start=1):
        due_date = _nth_friday(year, month, week_num)
        entries.append(_create_child(seq, due_date))
    return parent_entry, entries
def _insert_entries(parent_entry, child_entries):
    parent_columns = [
        "frequency",
        "half_year",
        "due_date",
        "start_month",
            "department_cd",
        "end_month",
        "year",
        "quarter",
        "month",
        "week_num",
        "assignee",
        "task_kind",
        "registrant",
        "status",
        "title",
        "attachment_link",
        "summary",
    ]
    child_columns = [
        "task_no",
        "routine_no",
        "due_date",
        "status",
        "summary",
    ]
    if _routine_child_has_title_column():
        child_columns.insert(3, "title")
    if _routine_child_has_assignee_column():
        child_columns.insert(3, "assignee")
    parent_placeholders = ", ".join("?" for _ in parent_columns)
    parent_columns_sql = ", ".join(parent_columns)
    child_placeholders = ", ".join("?" for _ in child_columns)
    child_columns_sql = ", ".join(child_columns)
    parent_query = (
        f"INSERT INTO dbo.routine_task ({parent_columns_sql}) "
        f"OUTPUT INSERTED.task_no "
        f"VALUES ({parent_placeholders})"
    )
    child_query = f"INSERT INTO dbo.routine_task_child ({child_columns_sql}) VALUES ({child_placeholders})"
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(parent_query, [parent_entry[col] for col in parent_columns])
            parent_row = cursor.fetchone()
            if not parent_row or parent_row[0] is None:
                raise RuntimeError("Failed to retrieve parent task ID")
            parent_id = parent_row[0]
            if child_entries:
                for seq, entry in enumerate(child_entries, start=1):
                    entry["task_no"] = parent_id
                    entry.setdefault("routine_no", seq)
                cursor.executemany(
                    child_query,
                    [
                        [entry[col] for col in child_columns]
                        for entry in child_entries
                    ],
                )
            conn.commit()
    except pyodbc.Error as exc:
        raise RuntimeError(f"Failed to insert tasks into the database: {exc}") from exc


def _fetch_tasks(page=1, page_size=DEFAULT_PAGE_SIZE, task_kind=None):
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (page - 1) * page_size
    limit = page_size + 1
    conds = [
        "p.is_deleted = 0",
        "c.is_deleted = 0",
        "(p.deleted_at IS NULL OR p.deleted_at > SYSUTCDATETIME())",
        "(c.deleted_at IS NULL OR c.deleted_at > SYSUTCDATETIME())",
    ]
    params = []
    if task_kind:
        normalized_kind = _normalize_task_kind(task_kind)
        conds.append("p.task_kind = ?")
        params.append(normalized_kind)
    child_assignee_sql = "c.assignee" if _routine_child_has_assignee_column() else "p.assignee"
    child_title_sql = "c.title" if _routine_child_has_title_column() else "p.title"
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    c.record_no,
                    c.task_no,
                    c.routine_no,
                    p.frequency,
                    p.half_year,
                    p.start_month,
                    p.end_month,
                    p.year AS parent_year,
                    p.quarter AS parent_quarter,
                    p.month AS parent_month,
                    p.week_num AS parent_week_num,
                    c.due_date,
                    {child_assignee_sql} AS assignee,
                    p.task_kind,
                    p.registrant,
                    c.status,
                    {child_title_sql} AS title,
                    p.attachment_link,
                    p.summary AS parent_summary,
                    c.summary AS child_summary
                FROM dbo.routine_task_child c
                INNER JOIN dbo.routine_task p ON c.task_no = p.task_no
                WHERE {" AND ".join(conds)}
                ORDER BY p.start_month DESC, p.task_no DESC, c.routine_no
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
                """,
                params + [offset, limit],
            )
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            has_next = len(rows) > page_size
            rows = rows[:page_size]
            tasks = []
            for row in rows:
                record = dict(zip(columns, row))
                due_date_value = record.get("due_date")
                if due_date_value:
                    record["due_date"] = due_date_value.isoformat()
                    parsed_due = due_date_value
                    record["year"] = parsed_due.year
                    record["quarter"] = str((parsed_due.month - 1) // 3 + 1)
                    record["half_year"] = 1 if parsed_due.month <= 6 else 2
                    record["month"] = parsed_due.month
                    record["week_num"] = ((parsed_due.day - 1) // 7) + 1
                else:
                    record["year"] = record.get("parent_year")
                    record["quarter"] = record.get("parent_quarter")
                    if record.get("half_year") is None:
                        record["half_year"] = _half_year_from_quarter(record.get("parent_quarter"))
                    record["month"] = record.get("parent_month")
                    record["week_num"] = record.get("parent_week_num")
                record["summary"] = record.get("child_summary") or record.get("parent_summary")
                for cleanup_key in (
                    "parent_year",
                    "parent_quarter",
                    "parent_month",
                    "parent_week_num",
                    "child_summary",
                    "parent_summary",
                ):
                    record.pop(cleanup_key, None)
                record["assignees"] = _parse_assignees(record.get("assignee"))
                tasks.append(record)
            return tasks, has_next
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to fetch routines from the database") from exc


def _fetch_parent_tasks(filters, page=1, page_size=DEFAULT_PAGE_SIZE):
    conds = ["is_deleted = 0"]
    params = []
    assignee = filters.get("assignee")
    if assignee:
        conds.append("assignee LIKE ?")
        params.append(f"%{assignee}%")
    registrant = filters.get("registrant")
    if registrant:
        conds.append("registrant LIKE ?")
        params.append(f"%{registrant}%")
    start_from = filters.get("start_from")
    if start_from:
        conds.append("start_month >= ?")
        params.append(start_from)
    end_to = filters.get("end_to")
    if end_to:
        conds.append("end_month <= ?")
        params.append(end_to)
    department_cd = filters.get("department")
    if department_cd:
        conds.append("department_cd = ?")
        params.append(department_cd)
    task_kind = _normalize_task_kind(filters.get("task_kind"))
    if filters.get("task_kind"):
        conds.append("task_kind = ?")
        params.append(task_kind)
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    offset = (page - 1) * page_size
    fetch_limit = page_size + 1
    query = f"""
        SELECT
            task_no,
            frequency,
            half_year,
            start_month,
            department_cd,
            end_month,
            due_date,
            [year],
            quarter,
            [month],
            week_num,
            assignee,
            task_kind,
            registrant,
            status,
            title,
            summary
        FROM dbo.routine_task p
        WHERE {" AND ".join(conds)}
        ORDER BY start_month DESC, task_no DESC
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params + [offset, fetch_limit])
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            has_next = len(rows) > page_size
            rows = rows[:page_size]
            parents = []
            for row in rows:
                parent = dict(zip(columns, row))
                due_date_value = parent.get("due_date")
                if due_date_value:
                    parent["due_date"] = due_date_value.isoformat()
                parent["assignees"] = _parse_assignees(parent.get("assignee"))
                parents.append(parent)
            return parents, has_next
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to fetch parent tasks") from exc


def _update_parent(task_no, data):
    allowed = [
        "frequency",
        "half_year",
        "due_date",
        "start_month",
        "end_month",
        "year",
        "quarter",
        "month",
        "week_num",
        "assignee",
        "task_kind",
        "registrant",
        "status",
        "title",
        "summary",
    ]
    updates = []
    params = []
    assignee_for_validation = None
    task_kind_for_validation = None
    derived_half_year = None
    apply_assignee_to_routines = bool(data.get("apply_assignee_to_routines", True))
    for key in allowed:
        if key in data and data[key] is not None:
            if key == "half_year" and "quarter" in data and data.get("quarter") is not None:
                continue
            value = data[key]
            if key == "assignee":
                value = _format_assignees(value)
                assignee_for_validation = value
            if key == "task_kind":
                value = _normalize_task_kind(value, data.get("assignee"))
                task_kind_for_validation = value
            updates.append(f"{key} = ?")
            params.append(value)
    if "quarter" in data and data.get("quarter") is not None:
        derived_half_year = _half_year_from_quarter(data.get("quarter"))
    if derived_half_year is not None:
        updates.append("half_year = ?")
        params.append(derived_half_year)
    if "assignee" in data and data.get("assignee") is not None and "task_kind" not in data:
        task_kind_for_validation = _fetch_parent_task_kind(task_no) or "個人"
        updates.append("task_kind = ?")
        params.append(task_kind_for_validation)
    if assignee_for_validation is not None:
        task_kind_to_check = task_kind_for_validation or _fetch_parent_task_kind(task_no) or "個人"
        _validate_task_kind_assignees(task_kind_to_check, assignee_for_validation)
    if not updates:
        return
    params.append(task_no)
    query = f"""
        UPDATE dbo.routine_task
        SET {', '.join(updates)}, updated_at = SYSUTCDATETIME()
        WHERE task_no = ?
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            if assignee_for_validation is not None:
                cursor.execute(
                    "SELECT assignee FROM dbo.routine_task WHERE task_no = ?",
                    [task_no],
                )
                row = cursor.fetchone()
                old_assignee = row[0] if row else None
                if _routine_child_has_assignee_column():
                    if apply_assignee_to_routines:
                        cursor.execute(
                            """
                            UPDATE dbo.routine_task_child
                            SET assignee = ?, updated_at = SYSUTCDATETIME()
                            WHERE task_no = ?
                              AND is_deleted = 0
                            """,
                            [assignee_for_validation, task_no],
                        )
                    else:
                        cursor.execute(
                            """
                            UPDATE dbo.routine_task_child
                            SET assignee = COALESCE(assignee, ?),
                                updated_at = SYSUTCDATETIME()
                            WHERE task_no = ?
                              AND is_deleted = 0
                            """,
                            [old_assignee, task_no],
                        )
            cursor.execute(query, params)
            conn.commit()
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to update parent task") from exc


def _complete_task(task_no):
    parent_query = """
        UPDATE dbo.routine_task
        SET is_deleted = 1,
            deleted_at = SYSUTCDATETIME(),
            status = '完了'
        WHERE task_no = ?
          AND is_deleted = 0
    """
    child_query = """
        UPDATE dbo.routine_task_child
        SET is_deleted = 1,
            deleted_at = SYSUTCDATETIME()
        WHERE task_no = ?
          AND is_deleted = 0
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(parent_query, [task_no])
            cursor.execute(child_query, [task_no])
            conn.commit()
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to complete routine task") from exc


def _update_child(record_no, data):
    allowed = ["due_date", "status", "summary"]
    has_child_title = _routine_child_has_title_column()
    if "title" in data and not has_child_title:
        raise ValueError(
            "ルーチンタイトルの個別更新にはDB列が必要です。routine_task_child.title を追加してください"
        )
    if has_child_title:
        allowed.insert(1, "title")
    has_child_assignee = _routine_child_has_assignee_column()
    if "assignee" in data and not has_child_assignee:
        raise ValueError(
            "ルーチン担当者の個別更新にはDB列が必要です。routine_task_child.assignee を追加してください"
        )
    if has_child_assignee:
        allowed.insert(1, "assignee")
    updates = []
    params = []
    for key in allowed:
        if key in data and data[key] is not None:
            value = data[key]
            if key == "assignee":
                value = _format_assignees(value)
            updates.append(f"{key} = ?")
            params.append(value)
    if not updates:
        return
    params.append(record_no)
    query = f"""
        UPDATE dbo.routine_task_child
        SET {', '.join(updates)}, updated_at = SYSUTCDATETIME()
        WHERE record_no = ?
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to update routine task") from exc


def _complete_routine(record_no):
    query = """
        UPDATE dbo.routine_task_child
        SET is_deleted = 1,
            deleted_at = SYSUTCDATETIME(),
            status = '完了'
        WHERE record_no = ?
          AND is_deleted = 0
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, [record_no])
            if cursor.rowcount == 0:
                raise RuntimeError("Routine already completed or not found")
            conn.commit()
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to complete routine") from exc


def _fetch_department_users(department_cd, only_employees=False):
    if not department_cd:
        return []
    employee_table = _qualified_table("Employee")
    filters = [
        "DepartmentCD = ?",
        "RetirementDate IS NULL",
        "LastWorkDate IS NULL",
    ]
    params = [department_cd]
    if only_employees:
        filters.append("EmployeeType = 0")
    query = f"""
        SELECT
            UserID,
            EmployeeName,
            AD,
            DepartmentCD
        FROM {employee_table}
        WHERE {" AND ".join(filters)}
        ORDER BY EmployeeName
    """
    try:
        with _get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
    except pyodbc.Error as exc:
        raise RuntimeError("Failed to fetch department users") from exc


def _extract_user():
    context = _current_user_context()
    return context.get("name") or ""


def create_app():
    app = Flask(__name__, static_folder=None)
    app.logger.setLevel(logging.DEBUG)
    logging.basicConfig(level=logging.DEBUG)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "change-me")

    @app.after_request
    def allow_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @app.before_request
    def require_login():
        public_endpoints = {
            "login",
            "auth_redirect",
            "logout",
            "not_found",
            "static",
        }
        if request.endpoint in public_endpoints:
            return
        if session.get("user"):
            return
        return redirect(url_for("login"))

    @app.route("/login")
    def login():
        session["auth_flow"] = _build_auth_flow()
        return redirect(session["auth_flow"]["auth_uri"])

    @app.route("/auth/redirect")
    def auth_redirect():
        flow = session.get("auth_flow")
        if not flow:
            return redirect(url_for("login"))
        cache = msal.SerializableTokenCache()
        msal_app = _build_msal_app(cache=cache)
        try:
            result = msal_app.acquire_token_by_auth_code_flow(flow, request.args)
        except ValueError as exc:
            app.logger.error("Auth flow failed", exc_info=exc)
            return jsonify({"message": "認証フローに失敗しました"}), 400
        session.pop("auth_flow", None)
        if "error" in result:
            app.logger.error("Auth error", result)
            return jsonify({"message": result.get('error_description')}), 401
        session["user"] = result.get("id_token_claims")
        session["access_token"] = result.get("access_token")
        return redirect(url_for("serve_index"))

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))


    def _build_routines_payload(page=1, page_size=DEFAULT_PAGE_SIZE, task_kind=None):
        routines, has_next = _fetch_tasks(page=page, page_size=page_size, task_kind=task_kind)
        pagination = {
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": has_next,
        }
        return {
            "routines": routines,
            "pagination": pagination,
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "fetched_at": datetime.utcnow().isoformat() + "Z",
        }

    def _handle_create():
        data = request.get_json(silent=True) or {}
        frequency = (data.get("frequency") or "").strip()
        if not frequency:
            return jsonify({"message": "frequency is required"}), 400
        is_spot = frequency in {"スポット", "spot", "Spot"}
        if not is_spot and (not data.get("start_month") or not data.get("end_month")):
            return jsonify({"message": "start_month and end_month are required"}), 400
        if not data.get("title"):
            return jsonify({"message": "title is required"}), 400
        registrant = _extract_user() or "system"
        user_context = _current_user_context()
        parent_entry, entries = _build_entries(data, registrant, user_context.get("department_cd"))
        _insert_entries(parent_entry, entries)
        return jsonify({"message": "登録しました", "task_count": len(entries)}), 201
    @app.route("/api.py/parents", methods=["GET"])
    @app.route("/routine_app/api.py/parents", methods=["GET"])
    def parent_tasks_route():
        try:
            filters = {
                "assignee": request.args.get("assignee"),
                "registrant": request.args.get("registrant"),
                "start_from": request.args.get("start_from"),
                "end_to": request.args.get("end_to"),
                "task_kind": request.args.get("task_kind"),
            }
            page, page_size = _parse_pagination_params(request.args, DEFAULT_PAGE_SIZE)
            parents, has_next = _fetch_parent_tasks(filters, page, page_size)
            pagination = {
                "page": page,
                "page_size": page_size,
                "has_prev": page > 1,
                "has_next": has_next,
            }
            return jsonify({"parents": parents, "pagination": pagination})
        except RuntimeError as exc:
            app.logger.exception("Parent fetch failed")
            return jsonify({"message": str(exc)}), 500

    @app.route("/api.py/parent/<int:task_no>", methods=["PATCH"])
    @app.route("/routine_app/api.py/parent/<int:task_no>", methods=["PATCH"])
    def parent_update_route(task_no):
        try:
            data = request.get_json(silent=True) or {}
            if not data:
                return jsonify({"message": "no data provided"}), 400
            _update_parent(task_no, data)
            return "", 204
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except RuntimeError as exc:
            app.logger.exception("Parent update failed")
            return jsonify({"message": str(exc)}), 500

    @app.route("/api.py/parent/<int:task_no>/complete", methods=["POST"])
    @app.route("/routine_app/api.py/parent/<int:task_no>/complete", methods=["POST"])
    def parent_complete_route(task_no):
        try:
            _complete_task(task_no)
            return "", 204
        except RuntimeError as exc:
            app.logger.exception("Parent completion failed")
            return jsonify({"message": str(exc)}), 500

    @app.route("/api.py/child/<int:record_no>/complete", methods=["POST"])
    @app.route("/routine_app/api.py/child/<int:record_no>/complete", methods=["POST"])
    def routine_complete_route(record_no):
        try:
            _complete_routine(record_no)
            return "", 204
        except RuntimeError as exc:
            app.logger.exception("Routine completion failed")
            return jsonify({"message": str(exc)}), 500

    @app.route("/api.py/child/<int:record_no>", methods=["PATCH"])
    @app.route("/routine_app/api.py/child/<int:record_no>", methods=["PATCH"])
    def routine_update_route(record_no):
        try:
            data = request.get_json(silent=True) or {}
            if not data:
                return jsonify({"message": "no data provided"}), 400
            _update_child(record_no, data)
            return "", 204
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except RuntimeError as exc:
            app.logger.exception("Routine update failed")
            return jsonify({"message": str(exc)}), 500
    @app.route("/api.py/routines", methods=["GET"])
    @app.route("/routine_app/api.py/routines", methods=["GET"])
    def get_routines_route():
        try:
            page, page_size = _parse_pagination_params(request.args, DEFAULT_PAGE_SIZE)
            task_kind = request.args.get("task_kind")
            return jsonify(_build_routines_payload(page, page_size, task_kind=task_kind))
        except RuntimeError as exc:
            app.logger.exception("DB retrieval failed")
            return jsonify({"message": str(exc)}), 500

    @app.route("/api.py/routines", methods=["POST"])
    @app.route("/routine_app/api.py/routines", methods=["POST"])
    def post_routines_route():
        try:
            return _handle_create()
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400
        except RuntimeError as exc:
            app.logger.exception("DB write error")
            return jsonify({"message": str(exc)}), 500
        except pyodbc.Error:
            app.logger.exception("DB write error")
            return jsonify({"message": "DBへの登録に失敗しました"}), 500

    @app.route("/api.py/current-user", methods=["GET"])
    @app.route("/routine_app/api.py/current-user", methods=["GET"])
    def current_user_route():
        try:
            return jsonify(_current_user_context())
        except RuntimeError as exc:
            app.logger.exception("User context failed")
            return jsonify({"name": _extract_user()}), 500

    @app.route("/api.py/departments", methods=["GET"])
    @app.route("/routine_app/api.py/departments", methods=["GET"])
    def departments_route():
        try:
            return jsonify({"departments": _fetch_departments()})
        except RuntimeError as exc:
            app.logger.exception("Department fetch failed")
            return jsonify({"message": str(exc)}), 500

    @app.route("/api.py/employees", methods=["GET"])
    @app.route("/routine_app/api.py/employees", methods=["GET"])
    def employees_route():
        try:
            user_context = _current_user_context()
            department_cd = (
                request.args.get("department")
                or user_context.get("department_cd")
                or FALLBACK_DEPARTMENT_CD
            )
            employees = _fetch_department_users(department_cd, only_employees=False)
            employees_only = _fetch_department_users(department_cd, only_employees=True)
            return jsonify({"employees": employees, "employees_only": employees_only})
        except RuntimeError as exc:
            app.logger.exception("Employee fetch failed")
            return jsonify({"message": str(exc)}), 500

    @app.errorhandler(404)
    def not_found(e):
        return (
            jsonify(
                {
                    "marker": "ROUTINE_APP_404",
                    "path": request.path,
                    "path_info": request.environ.get("PATH_INFO"),
                    "script_name": request.environ.get("SCRIPT_NAME"),
                }
            ),
            404,
        )

    @app.route("/")
    @app.route("/index.html")
    @app.route("/routine_app")
    @app.route("/routine_app/")
    @app.route("/routine_app/index.html")
    def serve_index():
        return send_from_directory(base_dir, "index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)

