# HTML Route Contracts: Backtest Detail View

**Feature Branch**: `007-backtest-detail-view`
**Date**: 2025-11-15

## Overview

This document specifies the HTML routes for the Backtest Detail View feature. These are server-rendered endpoints using FastAPI + Jinja2.

---

## Routes

### 1. GET /backtests/{run_id}

**Purpose**: Display comprehensive detail page for a single backtest run.

**Path Parameters**:
- `run_id` (UUID): Business identifier for backtest run

**Query Parameters**: None

**Request Headers**:
```
Accept: text/html
```

**Response (200 OK)**:
- Content-Type: `text/html`
- Template: `backtests/detail.html`
- Status: 200

**Response Context**:
```python
{
    "request": Request,
    "view": BacktestDetailView,
    "nav_items": list[NavItem],  # Shared navigation
    "active_page": "backtests"
}
```

**Response (404 Not Found)**:
- Content-Type: `text/html`
- Template: `errors/404.html`
- Status: 404

**Error Context**:
```python
{
    "request": Request,
    "message": "Backtest {run_id} not found",
    "suggestion": "Return to backtest list",
    "nav_items": list[NavItem]
}
```

**Example Request**:
```
GET /backtests/a1b2c3d4-e5f6-7890-1234-567890abcdef HTTP/1.1
Host: localhost:8000
Accept: text/html
```

**Performance Requirements**:
- Response time: <1 second
- Database query: Single SELECT with JOIN to performance_metrics

---

### 2. DELETE /backtests/{run_id}

**Purpose**: Delete a backtest run and its associated metrics.

**Path Parameters**:
- `run_id` (UUID): Business identifier for backtest run

**Request Headers**:
```
HX-Request: true  # HTMX indicator
```

**Response (200 OK)**:
- Content-Type: `text/html`
- Status: 200
- Headers:
  - `HX-Redirect: /backtests` (redirect to list page)

**Response Body** (if not using HX-Redirect):
```html
<div class="notification success">
  Backtest deleted successfully.
</div>
```

**Response (404 Not Found)**:
- Content-Type: `text/html`
- Status: 404

```html
<div class="notification error">
  Backtest not found.
</div>
```

**Example Request**:
```
DELETE /backtests/a1b2c3d4-e5f6-7890-1234-567890abcdef HTTP/1.1
Host: localhost:8000
HX-Request: true
```

**Side Effects**:
- Deletes backtest_runs record
- Cascades to performance_metrics (ON DELETE CASCADE)
- Logs deletion event

---

### 3. POST /backtests/{run_id}/rerun

**Purpose**: Trigger re-execution of backtest with same configuration.

**Path Parameters**:
- `run_id` (UUID): Business identifier for original backtest

**Request Headers**:
```
HX-Request: true
```

**Response (202 Accepted)**:
- Content-Type: `text/html`
- Status: 202
- Headers:
  - `HX-Redirect: /backtests/{new_run_id}` (redirect to new run)

**Response Body**:
```html
<div class="notification info">
  Backtest re-run initiated. Redirecting to new run...
</div>
```

**Response (404 Not Found)**:
- Status: 404

```html
<div class="notification error">
  Original backtest not found.
</div>
```

**Response (500 Error)**:
- Status: 500

```html
<div class="notification error">
  Failed to start backtest. Please try again.
</div>
```

**Example Request**:
```
POST /backtests/a1b2c3d4-e5f6-7890-1234-567890abcdef/rerun HTTP/1.1
Host: localhost:8000
HX-Request: true
```

**Side Effects**:
- Creates new BacktestRun record with reproduced_from_run_id set
- Triggers backtest execution (async)
- New run gets fresh run_id

**Note**: Full async execution with progress tracking is out of MVP scope. Initial implementation will be synchronous or return immediately with pending status.

---

### 4. GET /backtests/{run_id}/export

**Purpose**: Download HTML report for backtest.

**Path Parameters**:
- `run_id` (UUID): Business identifier for backtest run

**Query Parameters**:
- `format` (optional): "html" (default), "csv", "json"

**Request Headers**:
```
Accept: text/html, application/octet-stream
```

**Response (200 OK)**:
- Content-Type: `text/html` (for HTML report)
- Content-Disposition: `attachment; filename="backtest_report_{run_id}.html"`
- Status: 200

**Response Body**: Complete HTML report (same format as CLI export)

**Response (404 Not Found)**:
- Status: 404
- Redirect to error page

**Example Request**:
```
GET /backtests/a1b2c3d4-e5f6-7890-1234-567890abcdef/export HTTP/1.1
Host: localhost:8000
Accept: application/octet-stream
```

**Performance Requirements**:
- Response time: <2 seconds
- File size: Depends on backtest complexity

---

## HTMX Integration

### Confirmation Dialogs

**Delete Button**:
```html
<button hx-delete="/backtests/{{ run_id }}"
        hx-confirm="Are you sure you want to delete this backtest? This action cannot be undone."
        hx-target="body"
        hx-push-url="/backtests"
        class="bg-red-600 hover:bg-red-700 px-4 py-2 rounded">
  Delete Backtest
</button>
```

**Re-run Button**:
```html
<button hx-post="/backtests/{{ run_id }}/rerun"
        hx-indicator="#rerun-spinner"
        hx-disabled-elt="this"
        class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded">
  <span>Re-run Backtest</span>
  <span id="rerun-spinner" class="htmx-indicator ml-2">
    <svg class="animate-spin h-4 w-4 inline">...</svg>
  </span>
</button>
```

### Toast Notifications

```html
<div id="notifications"
     hx-swap-oob="afterbegin"
     class="fixed top-4 right-4 z-50">
  <!-- Notifications appear here -->
</div>
```

---

## Error Handling

### Invalid UUID Format
- Status: 422 Unprocessable Entity
- Response: Error page with validation message

### Database Connection Error
- Status: 503 Service Unavailable
- Response: Error page with retry suggestion

### Unauthorized Access (Future)
- Status: 401 Unauthorized
- Response: Redirect to login page

---

## Performance Contracts

| Endpoint | Max Response Time | Database Queries |
|----------|-------------------|------------------|
| GET /backtests/{run_id} | <1000ms | 1 (SELECT with JOIN) |
| DELETE /backtests/{run_id} | <500ms | 1 (DELETE CASCADE) |
| POST /backtests/{run_id}/rerun | <2000ms | 2 (SELECT + INSERT) |
| GET /backtests/{run_id}/export | <2000ms | 1-2 (SELECT + generate) |

---

## Security Considerations

1. **CSRF Protection**: HTMX requests include CSRF token
2. **Input Validation**: UUID format validated by FastAPI/Pydantic
3. **SQL Injection**: Parameterized queries via SQLAlchemy ORM
4. **XSS Prevention**: Jinja2 auto-escaping enabled
5. **Rate Limiting**: Apply 100 requests/minute limit (future enhancement)

---

## Testing Contracts

Each route must have corresponding tests:

```python
# test_backtest_detail.py

@pytest.mark.asyncio
async def test_detail_page_returns_200_for_valid_run_id(client, sample_backtest):
    """GET /backtests/{run_id} returns 200 with valid UUID."""
    response = await client.get(f"/backtests/{sample_backtest.run_id}")
    assert response.status_code == 200
    assert "Run Details" in response.text

@pytest.mark.asyncio
async def test_detail_page_returns_404_for_invalid_run_id(client):
    """GET /backtests/{run_id} returns 404 for non-existent UUID."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/backtests/{fake_id}")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_delete_removes_backtest_and_redirects(client, sample_backtest):
    """DELETE /backtests/{run_id} removes record and redirects to list."""
    response = await client.delete(
        f"/backtests/{sample_backtest.run_id}",
        headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert "HX-Redirect" in response.headers

@pytest.mark.asyncio
async def test_export_returns_html_file(client, sample_backtest):
    """GET /backtests/{run_id}/export returns downloadable HTML."""
    response = await client.get(f"/backtests/{sample_backtest.run_id}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "")
```
