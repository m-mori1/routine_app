import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyodbc
import requests


class HttpResp:
    def __init__(self, status_code, text, json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is not None:
            return self._json
        raise ValueError('no json')


class RemoteClient:
    def __init__(self, base_url, session_cookie):
        self.base_url = base_url
        self.s = requests.Session()
        self.s.verify = False
        if session_cookie:
            self.s.cookies.set('session', session_cookie, domain='mercury', path='/')

    def get(self, path, params=None, timeout=30):
        r = self.s.get(self.base_url + path, params=params, timeout=timeout, allow_redirects=False)
        return HttpResp(r.status_code, r.text, None, dict(r.headers))

    def post(self, path, json=None, timeout=30):
        r = self.s.post(self.base_url + path, json=json, timeout=timeout, allow_redirects=False)
        return HttpResp(r.status_code, r.text, None, dict(r.headers))

    def patch(self, path, json=None, timeout=30):
        r = self.s.patch(self.base_url + path, json=json, timeout=timeout, allow_redirects=False)
        return HttpResp(r.status_code, r.text, None, dict(r.headers))


class LocalClient:
    def __init__(self):
        os.environ.setdefault('ROUTINE_E2E_BYPASS_AUTH', '1')
        os.environ.setdefault('ROUTINE_E2E_TEST_UPN', 'm-mori')
        from api import create_app
        self.client = create_app().test_client()

    def _wrap(self, r):
        txt = r.get_data(as_text=True)
        jd = None
        try:
            jd = r.get_json(silent=True)
        except Exception:
            jd = None
        return HttpResp(r.status_code, txt, jd, dict(r.headers))

    def get(self, path, params=None, timeout=30):
        r = self.client.get('/routine_app/' + path, query_string=params)
        return self._wrap(r)

    def post(self, path, json=None, timeout=30):
        r = self.client.post('/routine_app/' + path, json=json)
        return self._wrap(r)

    def patch(self, path, json=None, timeout=30):
        r = self.client.patch('/routine_app/' + path, json=json)
        return self._wrap(r)
from dotenv import load_dotenv

from db_config import get_connection_string

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / '.env')

ACCOUNT_SCHEMA = os.environ.get('ACCOUNT_SCHEMA', '')
ACCOUNT_DATABASE = os.environ.get('ACCOUNT_DATABASE', '')


def load_env_file(path: Path):
    if not path.exists():
        return
    for enc in ('utf-8-sig', 'utf-16', 'cp932'):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeError:
            continue
    else:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


load_env_file(BASE_DIR / '.e2e.env')

BASE_URL = (os.environ.get('ROUTINE_BASE_URL') or 'https://mercury/routine_app/').rstrip('/') + '/'
SESSION_COOKIE = (os.environ.get('ROUTINE_SESSION_COOKIE') or '').strip()

def qtbl(name: str) -> str:
    if ACCOUNT_SCHEMA:
        if '.' in ACCOUNT_SCHEMA:
            return f"{ACCOUNT_SCHEMA}.{name}"
        if ACCOUNT_DATABASE:
            return f"{ACCOUNT_DATABASE}.{ACCOUNT_SCHEMA}.{name}"
        return f"{ACCOUNT_SCHEMA}.{name}"
    if ACCOUNT_DATABASE:
        return f"{ACCOUNT_DATABASE}.dbo.{name}"
    return name


def get_conn():
    return pyodbc.connect(get_connection_string())


@dataclass
class Case:
    case_id: str
    frequency: str
    task_kind: str
    status: str
    start_month: str | None = None
    end_month: str | None = None
    week: int | None = None
    due_date: str | None = None
    expected_records: int = 1


CASES = [
    Case('O-01', '月次', '個人', '未対応', start_month='2026-02', end_month='2026-02', week=1, expected_records=1),
    Case('O-02', '月次', 'グループ', '処理中', start_month='2026-02', end_month='2026-04', week=4, expected_records=3),
    Case('O-03', '四半期', '個人', '処理中', start_month='2026-02', end_month='2026-04', week=1, expected_records=1),
    Case('O-04', '四半期', 'グループ', '未対応', start_month='2026-02', end_month='2026-04', week=4, expected_records=1),
    Case('O-05', '月次', '個人', '未対応', start_month='2026-02', end_month='2026-04', week=4, expected_records=3),
    Case('O-06', '月次', 'グループ', '処理中', start_month='2026-02', end_month='2026-02', week=1, expected_records=1),
    Case('O-07', 'スポット', '個人', '未対応', due_date='2026-02-20', expected_records=1),
    Case('O-08', 'スポット', 'グループ', '処理中', due_date='2026-02-27', expected_records=1),
    Case('O-09', '四半期', '個人', '未対応', start_month='2026-02', end_month='2026-04', week=4, expected_records=1),
    Case('O-10', '四半期', 'グループ', '処理中', start_month='2026-02', end_month='2026-04', week=1, expected_records=1),
    Case('O-11', '月次', '個人', '処理中', start_month='2026-02', end_month='2026-04', week=1, expected_records=3),
    Case('O-12', '月次', 'グループ', '未対応', start_month='2026-02', end_month='2026-02', week=4, expected_records=1),
    Case('O-13', '週次', '個人', '未対応', start_month='2026-02', end_month='2027-01', week=1, expected_records=12),
    Case('O-14', '年次', 'グループ', '処理中', start_month='2026-02', end_month='2027-12', week=4, expected_records=2),
]


def now_token() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S_%f')


def get_active_department_users() -> list[str]:
    emp = qtbl('Employee')
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT TOP 1 DepartmentCD FROM {emp} WHERE UPPER(AD)=UPPER(?)", ['m-mori'])
        row = cur.fetchone()
        if not row or not row[0]:
            raise RuntimeError('Current user department not found')
        dept = row[0]
        cur.execute(
            f"""
            SELECT EmployeeName
            FROM {emp}
            WHERE DepartmentCD = ?
              AND RetirementDate IS NULL
              AND LastWorkDate IS NULL
              AND EmployeeName IS NOT NULL
              AND LTRIM(RTRIM(EmployeeName)) <> ''
            ORDER BY EmployeeName
            """,
            [dept],
        )
        return [r[0].strip() for r in cur.fetchall() if r[0] and str(r[0]).strip()]


def build_case_assignees(users: list[str]) -> dict[str, list[str]]:
    if len(users) < 12:
        raise RuntimeError(f'Need >=12 users, got {len(users)}')
    return {
        'O-01': [users[7]],
        'O-02': [users[0], users[1]],
        'O-03': [users[2]],
        'O-04': [users[3], users[4]],
        'O-05': [users[5]],
        'O-06': [users[6], users[8]],
        'O-07': [users[9]],
        'O-08': [users[10], users[11]],
        'O-09': [users[0]],
        'O-10': [users[1], users[2]],
        'O-11': [users[3]],
        'O-12': [users[4], users[5]],
        'O-13': [users[6]],
        'O-14': [users[7], users[11]],
    }


def db_fetch_parent(title: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TOP 1 task_no, title, frequency, task_kind, assignee, status, is_deleted
            FROM dbo.routine_task
            WHERE title = ?
            ORDER BY created_at DESC
            """,
            [title],
        )
        return cur.fetchone()


def db_child_count(task_no: int) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dbo.routine_task_child WHERE task_no = ? AND is_deleted = 0", [task_no])
        return int(cur.fetchone()[0])


def db_get_one_active_child(task_no: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT TOP 1 record_no, status, is_deleted FROM dbo.routine_task_child WHERE task_no = ? AND is_deleted = 0 ORDER BY record_no",
            [task_no],
        )
        return cur.fetchone()


def db_verify_parent_deleted(task_no: int) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_deleted, status FROM dbo.routine_task WHERE task_no = ?", [task_no])
        row = cur.fetchone()
        return bool(row) and bool(row[0])

def db_verify_children_deleted(task_no: int) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dbo.routine_task_child WHERE task_no = ? AND is_deleted = 0", [task_no])
        return int(cur.fetchone()[0]) == 0


def assert_status(resp, expected, ctx):
    if resp.status_code != expected:
        raise RuntimeError(f"{ctx}: expected {expected}, got {resp.status_code}, body={resp.text[:500]}")


def main():
    client = RemoteClient(BASE_URL, SESSION_COOKIE)
    r = client.get('api.py/current-user', timeout=30)
    if r.status_code != 200:
        print(f'[INFO] remote current-user failed status={r.status_code}. fallback to local test_client.')
        client = LocalClient()
        r = client.get('api.py/current-user', timeout=30)
    assert_status(r, 200, 'current-user')

    users = get_active_department_users()
    case_assignees = build_case_assignees(users)

    results = []
    used_names = set()

    for c in CASES:
        title = f"AUTO_{c.case_id}_{now_token()}"
        payload = {
            'title': title,
            'frequency': c.frequency,
            'task_kind': c.task_kind,
            'status': c.status,
            'assignee': '; '.join(case_assignees[c.case_id]),
            'summary': f'auto test {c.case_id}',
        }
        used_names.update(case_assignees[c.case_id])

        if c.frequency == 'スポット':
            payload['due_date'] = c.due_date
        else:
            payload['start_month'] = c.start_month
            payload['end_month'] = c.end_month
            payload['week'] = c.week

        r = client.post('api.py/routines', json=payload, timeout=30)
        assert_status(r, 201, f'{c.case_id} create')

        r = client.get('api.py/routines', params={'page': 1, 'page_size': 100}, timeout=30)
        assert_status(r, 200, f'{c.case_id} list')

        parent = db_fetch_parent(title)
        if not parent:
            raise RuntimeError(f'{c.case_id}: parent not found in DB by title={title}')
        task_no = int(parent[0])

        child_count = db_child_count(task_no)
        if child_count != c.expected_records:
            raise RuntimeError(f'{c.case_id}: child_count expected={c.expected_records}, got={child_count}')

        updated_title = title + '_UPD'
        r = client.patch(f'api.py/parent/{task_no}', json={'title': updated_title, 'summary': f'parent updated {c.case_id}'}, timeout=30)
        assert_status(r, 204, f'{c.case_id} parent update')

        child = db_get_one_active_child(task_no)
        if not child:
            raise RuntimeError(f'{c.case_id}: no active child after create')
        record_no = int(child[0])

        r = client.patch(f'api.py/child/{record_no}', json={'status': '???', 'summary': f'child updated {c.case_id}'}, timeout=30)
        assert_status(r, 204, f'{c.case_id} child update')

        r = client.post(f'api.py/child/{record_no}/complete', timeout=30)
        assert_status(r, 204, f'{c.case_id} child complete')

        r = client.post(f'api.py/parent/{task_no}/complete', timeout=30)
        assert_status(r, 204, f'{c.case_id} parent complete')

        if not db_verify_parent_deleted(task_no):
            raise RuntimeError(f'{c.case_id}: parent not completed/deleted in DB task_no={task_no}')
        if not db_verify_children_deleted(task_no):
            raise RuntimeError(f'{c.case_id}: child rows still active in DB task_no={task_no}')

        results.append({
            'case_id': c.case_id,
            'task_no': task_no,
            'record_no': record_no,
            'title': updated_title,
            'assignees': case_assignees[c.case_id],
            'create': 201,
            'list': 200,
            'parent_update': 204,
            'child_update': 204,
            'child_complete': 204,
            'parent_complete': 204,
            'db_verified': True,
        })
        print(f"[PASS] {c.case_id} task_no={task_no} assignees={'; '.join(case_assignees[c.case_id])}")

    missing = sorted(set(users) - used_names)
    if missing:
        raise RuntimeError(f'assignee coverage failed, missing={missing}')

    out_dir = BASE_DIR / 'test-results'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / 'api-ortho-results.json'
    out_json.write_text(json.dumps({
        'executed_at': datetime.now().isoformat(),
        'base_url': BASE_URL,
        'total_cases': len(results),
        'covered_assignees': sorted(used_names),
        'all_department_users': users,
        'results': results,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'[DONE] {len(results)} cases passed')
    print(f'[RESULT] {out_json}')


if __name__ == '__main__':
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    main()
