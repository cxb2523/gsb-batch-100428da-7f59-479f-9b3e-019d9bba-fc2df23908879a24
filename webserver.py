import os
from datetime import datetime, timedelta, timezone

from flask import Flask, render_template, request
from sqlalchemy import func
from sqlmodel import Session, col, select
from werkzeug.middleware.proxy_fix import ProxyFix

from database import create_db_and_tables, engine
from models import Project, RunOutcome
from version import __version__

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.wsgi_app = ProxyFix(app.wsgi_app)

create_db_and_tables()


@app.context_processor
def inject_version():
    return {"version": __version__}


SORT_COLUMNS = {
    "score": col(Project.match_score),
    "date": col(Project.created_date),
}


@app.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    source = request.args.get("source", "")
    sort = request.args.get("sort", "score")
    per_page = 20
    offset = (page - 1) * per_page

    with Session(engine) as session:
        query = select(Project)
        count_query = select(func.count(col(Project.id)))
        if source:
            query = query.where(col(Project.source) == source)
            count_query = count_query.where(col(Project.source) == source)

        total_matches = session.exec(count_query).one()
        total_pages = (total_matches + per_page - 1) // per_page

        order_column = SORT_COLUMNS.get(sort, SORT_COLUMNS["score"])
        rows = session.exec(
            query.order_by(order_column.desc()).offset(offset).limit(per_page)
        ).all()

    matches = [m.model_dump() for m in rows]
    return render_template(
        "index.html",
        matches=matches,
        page=page,
        total_pages=total_pages,
        total_matches=total_matches,
        source=source,
        sort=sort,
    )


@app.route("/project/<int:project_id>")
def project_detail(project_id):
    with Session(engine) as session:
        project = session.exec(
            select(Project).where(col(Project.id) == project_id)
        ).first()

    if not project:
        return "Project not found", 404

    return render_template("project_detail.html", project=project.model_dump())


@app.route("/statistics")
def statistics():
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    with Session(engine) as session:
        from sqlalchemy import select as sa_select
        stats_row = session.execute(
            sa_select(
                func.avg(col(Project.match_score)).label("avg_score"),
                func.count(col(Project.id)).label("total_matches"),
                func.max(col(Project.scrape_date)).label("latest_match"),
                func.min(col(Project.created_date)).label("oldest_project"),
                func.count(func.distinct(col(Project.company))).label("unique_companies"),
            ).where(col(Project.scrape_date) >= seven_days_ago)
        ).one()
        stats = dict(stats_row._mapping)

        all_scores = session.exec(select(col(Project.match_score))).all()
        score_distribution = [
            {
                "score_range": "30-50",
                "count": sum(1 for s in all_scores if 30 <= s < 50),
            },
            {
                "score_range": "50-70",
                "count": sum(1 for s in all_scores if 50 <= s < 70),
            },
            {"score_range": "70-100", "count": sum(1 for s in all_scores if s >= 70)},
        ]

        top_rows = session.execute(
            sa_select(
                col(Project.company),
                func.count(col(Project.id)).label("project_count"),
                func.avg(col(Project.match_score)).label("avg_match_score"),
            )
            .where(col(Project.company) != "N/A")
            .group_by(col(Project.company))
            .order_by(func.count(col(Project.id)).desc())
            .limit(10)
        ).all()
        top_companies = [
            {"company": r[0], "project_count": r[1], "avg_match_score": r[2]}
            for r in top_rows
        ]

        source_rows = session.execute(
            sa_select(
                col(Project.source),
                func.count(col(Project.id)).label("count"),
            ).group_by(col(Project.source))
        ).all()
        by_source = [
            {"source": r[0] or "Unbekannt", "count": r[1]} for r in source_rows
        ]

        # Outcomes of the most recent collection run, one row per source.
        # Empty when no run has been recorded yet — the template handles that.
        latest_run_id = session.exec(
            select(col(RunOutcome.run_id))
            .order_by(col(RunOutcome.id).desc())
            .limit(1)
        ).first()
        run_outcomes = []
        if latest_run_id:
            outcome_rows = session.exec(
                select(RunOutcome)
                .where(col(RunOutcome.run_id) == latest_run_id)
                .order_by(col(RunOutcome.id))
            ).all()
            run_outcomes = [r.model_dump() for r in outcome_rows]

    return render_template(
        "statistics.html",
        stats=stats,
        score_distribution=score_distribution,
        by_source=by_source,
        top_companies=top_companies,
        latest_run_id=latest_run_id,
        run_outcomes=run_outcomes,
    )


if __name__ == "__main__":
    print(f"Database: {engine.url}")
    app.run(debug=True, port=5050)
