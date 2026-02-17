# routine_app Test Specification

## 1. Scope
- Target app: `https://mercury/routine_app/`
- Target API prefix: `/routine_app/api.py/*`
- Main coverage:
- Authentication redirect/login flow
- Routine registration/update/completion
- Parent task registration/update/completion
- Master API (current user, departments, employees)
- HTTP to HTTPS redirect on IIS

## 2. Preconditions
- IIS app `/routine_app` points to `D:\asp\routine_app`
- Python venv exists: `D:\asp\routine_app\.venv`
- `.env` exists in `D:\asp\routine_app\.env`
- Entra redirect URI contains:
- `https://mercury/routine_app/auth/redirect`
- SQL Server is reachable from app host
- Test account can sign in to Entra and access DB data

## 3. Test Data Guidelines
- Use a unique title prefix per run, e.g. `TEST_YYYYMMDD_HHMM`
- Frequency variants to test:
- `スポット`
- `月次`
- `四半期`
- Assignee variants to test:
- single user (`個人`)
- multiple users (`グループ`)

## 4. Test Cases

### AUTH-001: Base URL opens login flow
- Purpose: Verify app boots and unauthenticated access redirects to Entra sign-in
- Steps:
1. Open `https://mercury/routine_app/`
2. Confirm browser moves to Microsoft sign-in page
- Expected:
- HTTP status is successful in browser (page rendered)
- Sign-in page title appears

### AUTH-002: Callback returns to app
- Purpose: Verify redirect URI and session creation
- Steps:
1. Sign in on Entra page
2. Wait for redirect back to app
- Expected:
- Redirect target is under `/routine_app/`
- No `AADSTS50011` error

### INFRA-001: HTTP is redirected to HTTPS
- Purpose: Verify IIS rewrite
- Steps:
1. Access `http://mercury/routine_app/`
- Expected:
- Browser redirects to `https://mercury/routine_app/`

### API-001: Current user retrieval
- Endpoint: `GET /routine_app/api.py/current-user`
- Steps:
1. Call API after login
- Expected:
- Status `200`
- Response includes keys: `name`, `department_cd`, `department_name`

### API-002: Departments retrieval
- Endpoint: `GET /routine_app/api.py/departments`
- Expected:
- Status `200`
- Response has `departments` array

### API-003: Employees retrieval
- Endpoint: `GET /routine_app/api.py/employees?department=<code>`
- Expected:
- Status `200`
- Response has `employees` and `employees_only`

### CRUD-001: Create routine (monthly)
- Endpoint: `POST /routine_app/api.py/routines`
- Sample body:
```json
{
  "frequency": "月次",
  "start_month": "2026-02",
  "end_month": "2026-04",
  "week": 1,
  "title": "TEST_20260216_001",
  "assignee": "user1",
  "task_kind": "個人",
  "status": "未着手"
}
```
- Expected:
- Status `201`
- Response includes `task_count` > 0

### CRUD-002: Create routine (spot)
- Endpoint: `POST /routine_app/api.py/routines`
- Sample body:
```json
{
  "frequency": "スポット",
  "due_date": "2026-03-15",
  "title": "TEST_20260216_SPOT",
  "assignee": "user1",
  "task_kind": "個人",
  "status": "未着手"
}
```
- Expected:
- Status `201`

### CRUD-003: Validation check (required fields)
- Endpoint: `POST /routine_app/api.py/routines`
- Body: omit `title` or omit `start_month/end_month` for non-spot
- Expected:
- Status `400`
- Error message returned

### CRUD-004: Validation check (group assignee count)
- Endpoint: `POST /routine_app/api.py/routines`
- Body: `task_kind = "グループ"` with one assignee only
- Expected:
- Status `400`
- Message indicates 2+ assignees required

### LIST-001: Routine list pagination
- Endpoint: `GET /routine_app/api.py/routines?page=1&page_size=20`
- Expected:
- Status `200`
- Response has `routines` and `pagination`
- `pagination.page == 1`

### LIST-002: Parent list filtering
- Endpoint: `GET /routine_app/api.py/parents?task_kind=個人&page=1&page_size=20`
- Expected:
- Status `200`
- Response has `parents` and `pagination`

### UPDATE-001: Parent update
- Endpoint: `PATCH /routine_app/api.py/parent/{task_no}`
- Body example:
```json
{
  "title": "TEST_UPDATED_TITLE",
  "status": "進行中"
}
```
- Expected:
- Status `204`

### UPDATE-002: Child update
- Endpoint: `PATCH /routine_app/api.py/child/{record_no}`
- Body example:
```json
{
  "status": "進行中",
  "summary": "updated by test"
}
```
- Expected:
- Status `204`

### COMPLETE-001: Child completion
- Endpoint: `POST /routine_app/api.py/child/{record_no}/complete`
- Expected:
- Status `204`

### COMPLETE-002: Parent completion
- Endpoint: `POST /routine_app/api.py/parent/{task_no}/complete`
- Expected:
- Status `204`
- Related child rows become completed/deleted in logic

## 5. DB Verification (optional but recommended)
- Table: `dbo.routine_task`
- Check columns: `is_deleted`, `deleted_at`, `status`, `updated_at`
- Table: `dbo.routine_task_child`
- Check columns: `is_deleted`, `deleted_at`, `status`, `updated_at`

## 6. Log and Failure Investigation
- App log: `D:\asp\routine_app\logs\wfastcgi.log`
- IIS log: `C:\inetpub\logs\LogFiles\W3SVC*`
- Check for status/substatus/win32-status when API fails

## 7. Exit Criteria
- All AUTH/INFRA tests pass
- CRUD create/update/complete tests pass
- Required validation tests return expected `400`
- No unresolved `500` in IIS/app logs for tested flows
