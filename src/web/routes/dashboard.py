"""
Dashboard routes — HTML page and JSON data endpoints.

The HTML endpoint returns a Jinja2 shell. The browser then calls the JSON
endpoints to load chart data, KPIs, and the ad table separately.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_redirect(request: Request):
    """Redirect to the first account's dashboard, or show an empty state."""
    raise NotImplementedError("Proprietary implementation")


@router.get("/dashboard/{account_id}", response_class=HTMLResponse)
async def dashboard_page(request: Request, account_id: str):
    """Render the dashboard HTML shell for a specific ad account."""
    raise NotImplementedError("Proprietary implementation")


@router.get("/api/chart-data/{account_id}")
async def chart_data(request: Request, account_id: str):
    """
    Return JSON: KPI summary, daily trend, campaign breakdown, funnel,
    heatmap, scatter, platform breakdown, and rule flag summary.

    Query params: start, end (YYYY-MM-DD), business_type, metric.

    Note:
        Data aggregation and rules evaluation logic is proprietary and
        not included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


@router.post("/api/dashboard/{account_id}/data")
async def dashboard_data(request: Request, account_id: str):
    """
    Return JSON: full dashboard payload with filtered ad table.

    Accepts filters, sort, pagination, and date range in request body.

    Note:
        Filter application and pagination logic is proprietary and not
        included in this public version.
    """
    raise NotImplementedError("Proprietary implementation")


@router.get("/api/table-data/{account_id}")
async def table_data(request: Request, account_id: str):
    """
    Return JSON: paginated, sorted, and filtered ad-level table rows.

    Query params: page, per_page, sort_by, sort_dir, plus filter params.
    """
    raise NotImplementedError("Proprietary implementation")
