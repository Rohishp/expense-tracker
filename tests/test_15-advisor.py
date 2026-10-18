"""Tests for step 15: the Advisor (spec .claude/specs/15-advisor.md).

No network, no threads: ADVISOR_INLINE is on (conftest) and the model is a
scripted fake installed through app._advisor_caller.
"""

import html as htmllib
import json
import sqlite3
from datetime import date, timedelta

import pytest

import app as app_module
import database.queries as queries
from advisor.tools import ToolError, apply_changes, dispatch
from database.db import get_db

DEADLINE = (date.today() + timedelta(days=200)).isoformat()
QUESTION = "Can I get a camera?"
CAMERA = {
    "op": "add_goal",
    "name": "Camera",
    "target_amount": 900,
    "deadline": DEADLINE,
    "importance": "Nice-to-have",
}


# ------------------------------ helpers ------------------------------ #


def call(name, args=None, id_="c"):
    return {
        "content": None,
        "tool_calls": [{"id": id_, "name": name, "arguments": json.dumps(args or {})}],
    }


def scripted(replies):
    replies = list(replies)

    def fake(messages, tools):
        if replies:
            return replies.pop(0)
        return {"content": "thinking", "tool_calls": []}

    return fake


def answer(summary="It fits.", changes=(), verdict="yes"):
    return scripted(
        [
            call("get_finances", id_="1"),
            call(
                "final_answer",
                {"verdict": verdict, "summary": summary, "proposed_changes": list(changes)},
                id_="2",
            ),
        ]
    )


def use_model(monkeypatch, fake):
    monkeypatch.setattr(app_module, "_advisor_caller", lambda: fake)


def login(client, email="demo@sawings.com", password="demo123"):
    r = client.post("/login", data={"email": email, "password": password})
    assert r.status_code == 302


def register_and_login(client, email="other@example.com"):
    client.post(
        "/register",
        data={"name": "Other", "email": email, "password": "pw12345", "confirm_password": "pw12345"},
    )
    login(client, email, "pw12345")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged(client):
    login(client)
    return client


def user_id(email="demo@sawings.com"):
    conn = get_db()
    try:
        return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    finally:
        conn.close()


def run_count(uid=None):
    conn = get_db()
    try:
        if uid is None:
            return conn.execute("SELECT COUNT(*) c FROM advisor_runs").fetchone()["c"]
        return conn.execute(
            "SELECT COUNT(*) c FROM advisor_runs WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    finally:
        conn.close()


def make_run(
    uid,
    question="Q?",
    status="done",
    verdict="yes",
    summary="Summary.",
    changes=(),
    stopped_reason="final_answer",
    applied=False,
    age=None,
):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO advisor_runs (user_id, question, status, verdict, summary,"
            " proposed_changes, stopped_reason, applied_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                uid,
                question,
                status,
                verdict,
                summary,
                json.dumps(list(changes)),
                stopped_reason,
                "2026-01-01 00:00:00" if applied else None,
            ),
        )
        rid = cur.lastrowid
        if age:
            conn.execute(
                "UPDATE advisor_runs SET created_at = datetime('now', ?) WHERE id = ?",
                (age, rid),
            )
        conn.commit()
        return rid
    finally:
        conn.close()


def get_run(rid):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM advisor_runs WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()


def goal_names(uid):
    conn = get_db()
    try:
        return [r["name"] for r in conn.execute("SELECT name FROM goals WHERE user_id = ?", (uid,))]
    finally:
        conn.close()


def add_goal_row(uid, name="Laptop", target=1000, deadline=DEADLINE, importance="Should-have"):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO goals (user_id, name, target_amount, saved_amount, deadline, importance)"
            " VALUES (?,?,?,?,?,?)",
            (uid, name, target, 0, deadline, importance),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def text(response):
    """Response body with HTML entities decoded and whitespace collapsed."""
    return " ".join(htmllib.unescape(response.get_data(as_text=True)).split())


def set_income_row(uid, amount):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO incomes (user_id, monthly_amount) VALUES (?, ?)", (uid, amount)
        )
        conn.commit()
    finally:
        conn.close()


def ask(client, question=QUESTION, follow=False):
    return client.post("/advisor", data={"question": question}, follow_redirects=follow)


# ----------------------------- auth guards ----------------------------- #


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/advisor"),
        ("post", "/advisor"),
        ("get", "/advisor/runs/1"),
        ("post", "/advisor/runs/1/confirm"),
    ],
)
def test_advisor_routes_redirect_to_login_when_logged_out(client, method, url):
    r = getattr(client, method)(url)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_logged_out_post_creates_no_run(client, monkeypatch):
    use_model(monkeypatch, answer())
    client.post("/advisor", data={"question": QUESTION})
    assert run_count() == 0


# ----------------------------- navigation ----------------------------- #


def test_navbar_shows_advisor_link_for_logged_in_user(logged):
    html = logged.get("/plan").get_data(as_text=True)
    assert ">Advisor</a>" in html


def test_navbar_has_no_advisor_link_when_logged_out(client):
    html = client.get("/").get_data(as_text=True)
    assert ">Advisor</a>" not in html


def test_advisor_nav_link_is_highlighted_on_advisor_page(logged):
    html = logged.get("/advisor").get_data(as_text=True)
    idx = html.index(">Advisor</a>")
    link_start = html.rfind("<a ", 0, idx)
    assert "active" in html[link_start:idx]


def test_advisor_nav_link_is_not_highlighted_on_plan_page(logged):
    html = logged.get("/plan").get_data(as_text=True)
    idx = html.index(">Advisor</a>")
    link_start = html.rfind("<a ", 0, idx)
    assert "active" not in html[link_start:idx]


def test_plan_page_links_to_advisor(logged):
    html = logged.get("/plan").get_data(as_text=True)
    assert "Ask the Advisor a what-if question" in html
    assert 'href="/advisor"' in html


# ------------------------- GET /advisor page ------------------------- #


def test_advisor_page_shows_ai_notice_and_form_when_available(logged, monkeypatch):
    use_model(monkeypatch, answer())
    r = logged.get("/advisor")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "<textarea" in html
    assert "unavailable" not in html.lower()
    assert "AI" in html


def test_advisor_unavailable_without_env(logged):
    html = logged.get("/advisor").get_data(as_text=True)
    assert "The Advisor is unavailable right now." in html


def test_unavailable_form_is_disabled(logged):
    html = logged.get("/advisor").get_data(as_text=True)
    assert "disabled" in html


def test_post_when_unavailable_creates_no_run(logged):
    r = ask(logged)
    assert r.status_code == 200
    assert "The Advisor is unavailable right now." in r.get_data(as_text=True)
    assert run_count() == 0


def test_caller_is_none_without_api_key_or_model(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ADVISOR_MODEL", raising=False)
    assert app_module._advisor_caller() is None
    monkeypatch.setenv("ADVISOR_MODEL", "some-model")
    assert app_module._advisor_caller() is None
    monkeypatch.delenv("ADVISOR_MODEL")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert app_module._advisor_caller() is None


# ----------------------------- validation ----------------------------- #


@pytest.mark.parametrize("question", ["", "   ", "\n\t "])
def test_empty_question_shows_error_and_creates_no_run(logged, monkeypatch, question):
    use_model(monkeypatch, answer())
    r = ask(logged, question)
    assert r.status_code == 200
    assert "error" in r.get_data(as_text=True).lower() or "question" in r.get_data(as_text=True).lower()
    assert run_count() == 0


def test_question_over_500_characters_is_rejected(logged, monkeypatch):
    use_model(monkeypatch, answer())
    r = ask(logged, "x" * 501)
    assert r.status_code == 200
    assert run_count() == 0


def test_question_of_exactly_500_characters_is_accepted(logged, monkeypatch):
    use_model(monkeypatch, answer())
    r = ask(logged, "x" * 500)
    assert r.status_code == 302
    assert run_count() == 1


def test_question_is_trimmed_before_length_check_and_storage(logged, monkeypatch):
    use_model(monkeypatch, answer())
    r = ask(logged, "  " + "x" * 500 + "  ")
    assert r.status_code == 302
    conn = get_db()
    try:
        stored = conn.execute("SELECT question FROM advisor_runs").fetchone()["question"]
    finally:
        conn.close()
    assert stored == "x" * 500


# ----------------------------- happy path ----------------------------- #


def test_asking_redirects_to_run_page(logged, monkeypatch):
    use_model(monkeypatch, answer())
    r = ask(logged)
    assert r.status_code == 302
    rid = get_run_id_latest()
    assert r.headers["Location"].endswith(f"/advisor/runs/{rid}")


def get_run_id_latest():
    conn = get_db()
    try:
        return conn.execute("SELECT MAX(id) m FROM advisor_runs").fetchone()["m"]
    finally:
        conn.close()


def test_run_page_shows_verdict_summary_and_change_sentence(logged, monkeypatch):
    use_model(monkeypatch, answer("Plenty of room for it.", [CAMERA], "yes"))
    html = text(ask(logged, follow=True))
    assert "Yes, it fits" in html
    assert "Plenty of room for it." in html
    assert 'Add Goal "Camera"' in html
    assert "900.00" in html
    assert DEADLINE in html
    assert "Nice-to-have" in html


@pytest.mark.parametrize(
    "verdict,label",
    [("yes", "Yes, it fits"), ("trade_off", "Fits with a trade-off"), ("no", "Does not fit")],
)
def test_verdict_labels(logged, monkeypatch, verdict, label):
    use_model(monkeypatch, answer("S.", [], verdict))
    html = ask(logged, follow=True).get_data(as_text=True)
    assert label in html


def test_run_row_is_stored_as_done_with_changes_and_not_applied(logged, monkeypatch):
    use_model(monkeypatch, answer("S.", [CAMERA]))
    ask(logged)
    row = get_run(get_run_id_latest())
    assert row["status"] == "done"
    assert row["verdict"] == "yes"
    assert row["stopped_reason"] == "final_answer"
    assert row["question"] == QUESTION
    assert row["applied_at"] is None
    assert row["finished_at"] is not None
    assert json.loads(row["proposed_changes"]) == [CAMERA]


def test_asking_saves_no_goal_income_or_obligation(logged, monkeypatch):
    uid = user_id()
    conn = get_db()
    before = (
        conn.execute("SELECT COUNT(*) c FROM goals").fetchone()["c"],
        conn.execute("SELECT COUNT(*) c FROM obligations").fetchone()["c"],
        conn.execute("SELECT COUNT(*) c FROM incomes").fetchone()["c"],
    )
    conn.close()
    use_model(monkeypatch, answer("S.", [CAMERA]))
    ask(logged)
    conn = get_db()
    after = (
        conn.execute("SELECT COUNT(*) c FROM goals").fetchone()["c"],
        conn.execute("SELECT COUNT(*) c FROM obligations").fetchone()["c"],
        conn.execute("SELECT COUNT(*) c FROM incomes").fetchone()["c"],
    )
    conn.close()
    assert before == after
    assert "Camera" not in goal_names(uid)


def test_set_income_change_sentence(logged, monkeypatch):
    change = {"op": "set_income", "amount": 3200}
    use_model(monkeypatch, answer("S.", [change]))
    html = ask(logged, "What if my income were 3200?", follow=True).get_data(as_text=True)
    assert "Set Income to" in html
    assert "3,200.00" in html


def test_update_and_remove_goal_sentences_use_current_names(logged, monkeypatch):
    uid = user_id()
    gid = add_goal_row(uid, "Sofa")
    changes = [{"op": "update_goal", "goal_id": gid, "importance": "Nice-to-have"}]
    use_model(monkeypatch, answer("S.", changes))
    html = ask(logged, "Lower the Sofa importance", follow=True).get_data(as_text=True)
    assert "Change Goal" in html
    assert "Sofa" in html
    assert "Nice-to-have" in html


def test_remove_goal_sentence(logged):
    gid = add_goal_row(user_id(), "Sofa")
    rid = make_run(user_id(), changes=[{"op": "remove_goal", "goal_id": gid}])
    html = text(logged.get(f"/advisor/runs/{rid}"))
    assert 'Remove Goal "Sofa"' in html


def test_change_for_missing_goal_says_no_longer_exists(logged):
    rid = make_run(user_id(), changes=[{"op": "remove_goal", "goal_id": 99999}])
    r = logged.get(f"/advisor/runs/{rid}")
    assert r.status_code == 200
    assert "(no longer exists)" in r.get_data(as_text=True)


def test_obligation_sentences(logged):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO obligations (user_id, name, amount) VALUES (?,?,?)",
        (user_id(), "Gym", 40),
    )
    oid = cur.lastrowid
    conn.commit()
    conn.close()
    rid = make_run(
        user_id(),
        changes=[
            {"op": "add_obligation", "name": "Phone", "amount": 25},
            {"op": "remove_obligation", "obligation_id": oid},
        ],
    )
    html = text(logged.get(f"/advisor/runs/{rid}"))
    assert 'Add Obligation "Phone"' in html
    assert 'Remove Obligation "Gym"' in html


# ------------------------- running / refresh ------------------------- #


def test_running_run_shows_thinking_and_refreshes(logged):
    rid = make_run(user_id(), status="running", verdict=None, summary=None, stopped_reason=None)
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert "Thinking" in html
    assert 'http-equiv="refresh"' in html
    assert 'content="3"' in html


def test_done_run_has_no_refresh(logged):
    rid = make_run(user_id())
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert 'http-equiv="refresh"' not in html
    assert "Thinking" not in html


def test_viewing_a_run_never_calls_the_model(logged, monkeypatch):
    def boom(messages, tools):
        raise AssertionError("model called")

    use_model(monkeypatch, boom)
    rid = make_run(user_id(), status="running", verdict=None, summary=None, stopped_reason=None)
    done = make_run(user_id(), changes=[CAMERA])
    assert logged.get(f"/advisor/runs/{rid}").status_code == 200
    assert logged.get(f"/advisor/runs/{done}").status_code == 200
    assert logged.get("/advisor").status_code == 200


def test_asking_while_another_is_running_is_blocked(logged, monkeypatch):
    use_model(monkeypatch, answer())
    make_run(user_id(), status="running", verdict=None, summary=None, stopped_reason=None)
    r = ask(logged)
    assert r.status_code == 200
    assert "Your last question is still running" in r.get_data(as_text=True)
    assert run_count() == 1


def test_running_run_of_another_user_does_not_block(client, monkeypatch):
    other = None
    register_and_login(client, "other@example.com")
    other = user_id("other@example.com")
    client.get("/logout")
    make_run(other, status="running", verdict=None, summary=None, stopped_reason=None)
    login(client)
    use_model(monkeypatch, answer())
    assert ask(client).status_code == 302


def test_stale_running_run_shows_as_failed(logged):
    rid = make_run(
        user_id(),
        status="running",
        verdict=None,
        summary=None,
        stopped_reason=None,
        age="-16 minutes",
    )
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert "The Advisor could not answer. Try asking again." in html
    assert "Thinking" not in html


def test_stale_run_does_not_block_new_question(logged, monkeypatch):
    use_model(monkeypatch, answer())
    make_run(
        user_id(),
        status="running",
        verdict=None,
        summary=None,
        stopped_reason=None,
        age="-16 minutes",
    )
    r = ask(logged)
    assert r.status_code == 302
    assert run_count() == 2


def test_recent_running_run_at_14_minutes_still_blocks(logged, monkeypatch):
    use_model(monkeypatch, answer())
    make_run(
        user_id(),
        status="running",
        verdict=None,
        summary=None,
        stopped_reason=None,
        age="-14 minutes",
    )
    r = ask(logged)
    assert r.status_code == 200
    assert run_count() == 1


# ----------------------------- failures ----------------------------- #


def test_model_exception_marks_run_failed_without_500(logged, monkeypatch):
    def boom(messages, tools):
        raise RuntimeError("sk-secret-key exploded")

    use_model(monkeypatch, boom)
    r = ask(logged, follow=True)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "The Advisor could not answer. Try asking again." in html
    assert "sk-secret-key" not in html
    row = get_run(get_run_id_latest())
    assert row["status"] == "failed"
    assert row["stopped_reason"] == "error"
    assert json.loads(row["proposed_changes"]) == []


def test_failed_run_has_no_confirm_button_and_failed_run_does_not_block(logged, monkeypatch):
    def boom(messages, tools):
        raise TimeoutError("timeout")

    use_model(monkeypatch, boom)
    html = ask(logged, follow=True).get_data(as_text=True)
    assert "Confirm" not in html
    use_model(monkeypatch, answer())
    assert ask(logged).status_code == 302


def test_max_steps_run_is_done_without_verdict_or_changes(logged, monkeypatch):
    use_model(monkeypatch, lambda m, t: {"content": "still thinking", "tool_calls": []})
    r = ask(logged, follow=True)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    row = get_run(get_run_id_latest())
    assert row["status"] == "done"
    assert row["stopped_reason"] == "max_steps"
    assert row["verdict"] is None
    assert json.loads(row["proposed_changes"]) == []
    assert "Confirm" not in html


# ------------------------------ confirm ------------------------------ #


def test_confirm_saves_goal_flashes_and_redirects_to_plan(logged, monkeypatch):
    set_income_row(user_id(), 3000)
    use_model(monkeypatch, answer("S.", [CAMERA]))
    ask(logged)
    rid = get_run_id_latest()
    r = logged.post(f"/advisor/runs/{rid}/confirm")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/plan")
    assert "Camera" in goal_names(user_id())
    follow = logged.get("/plan")
    assert "Changes saved." in follow.get_data(as_text=True)
    assert "Camera" in follow.get_data(as_text=True)
    assert "Camera" in logged.get("/goals").get_data(as_text=True)
    assert get_run(rid)["applied_at"] is not None


def test_done_run_with_changes_shows_confirm_button(logged):
    rid = make_run(user_id(), changes=[CAMERA])
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert f"/advisor/runs/{rid}/confirm" in html
    assert "Confirm" in html


def test_confirm_button_hidden_after_applied(logged):
    rid = make_run(user_id(), changes=[CAMERA], applied=True)
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert f"/advisor/runs/{rid}/confirm" not in html


def test_confirm_twice_saves_once(logged):
    rid = make_run(user_id(), changes=[CAMERA])
    logged.post(f"/advisor/runs/{rid}/confirm")
    r = logged.post(f"/advisor/runs/{rid}/confirm", follow_redirects=True)
    assert "These changes were already saved." in r.get_data(as_text=True)
    assert goal_names(user_id()).count("Camera") == 1


def test_second_confirm_redirects_to_the_run(logged):
    rid = make_run(user_id(), changes=[CAMERA])
    logged.post(f"/advisor/runs/{rid}/confirm")
    r = logged.post(f"/advisor/runs/{rid}/confirm")
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/advisor/runs/{rid}")


def test_confirm_get_is_405(logged):
    rid = make_run(user_id(), changes=[CAMERA])
    assert logged.get(f"/advisor/runs/{rid}/confirm").status_code == 405


def test_confirm_with_deleted_goal_saves_nothing_and_run_still_viewable(logged):
    uid = user_id()
    gid = add_goal_row(uid, "Sofa")
    rid = make_run(uid, changes=[{"op": "remove_goal", "goal_id": gid}, CAMERA])
    conn = get_db()
    conn.execute("DELETE FROM goals WHERE id = ?", (gid,))
    conn.commit()
    conn.close()
    r = logged.post(f"/advisor/runs/{rid}/confirm", follow_redirects=True)
    html = text(r)
    assert "changed since this answer" in html
    assert "nothing was saved" in html
    assert "Camera" not in goal_names(uid)
    assert r.status_code == 200
    assert logged.get(f"/advisor/runs/{rid}").status_code == 200


def test_failed_confirm_clears_applied_at_so_run_is_not_marked_saved(logged):
    uid = user_id()
    rid = make_run(uid, changes=[{"op": "remove_goal", "goal_id": 99999}])
    logged.post(f"/advisor/runs/{rid}/confirm")
    assert get_run(rid)["applied_at"] is None


def test_confirm_failure_does_not_show_raw_tool_error(logged):
    rid = make_run(user_id(), changes=[{"op": "remove_goal", "goal_id": 99999}])
    html = logged.post(f"/advisor/runs/{rid}/confirm", follow_redirects=True).get_data(
        as_text=True
    )
    assert "ToolError" not in html
    assert "Traceback" not in html


def test_confirm_on_run_without_changes_saves_nothing(logged):
    uid = user_id()
    rid = make_run(uid, changes=[])
    before = goal_names(uid)
    r = logged.post(f"/advisor/runs/{rid}/confirm")
    assert r.status_code in (302, 200)
    assert goal_names(uid) == before
    assert get_run(rid)["applied_at"] is None
    assert "Confirm" not in logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)


def test_confirm_on_max_steps_run_saves_nothing(logged):
    uid = user_id()
    rid = make_run(
        uid,
        verdict=None,
        summary="I could not finish within the step limit.",
        stopped_reason="max_steps",
        changes=[],
    )
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert "I could not finish within the step limit." in html
    assert "Confirm" not in html
    before = goal_names(uid)
    logged.post(f"/advisor/runs/{rid}/confirm")
    assert goal_names(uid) == before


def test_confirm_on_running_or_failed_run_saves_nothing(logged):
    uid = user_id()
    before = goal_names(uid)
    for status in ("running", "failed"):
        rid = make_run(
            uid, status=status, verdict=None, summary=None, stopped_reason=None, changes=[CAMERA]
        )
        logged.post(f"/advisor/runs/{rid}/confirm")
        assert get_run(rid)["applied_at"] is None
    assert goal_names(uid) == before


def test_changes_in_confirm_form_body_are_ignored(logged):
    uid = user_id()
    rid = make_run(uid, changes=[CAMERA])
    evil = dict(CAMERA, name="Evil")
    logged.post(
        f"/advisor/runs/{rid}/confirm",
        data={
            "proposed_changes": json.dumps([evil]),
            "changes": json.dumps([evil]),
            "name": "Evil",
            "op": "add_goal",
        },
    )
    names = goal_names(uid)
    assert "Camera" in names
    assert "Evil" not in names


def test_changes_in_form_body_ignored_when_run_has_no_changes(logged):
    uid = user_id()
    rid = make_run(uid, changes=[])
    logged.post(
        f"/advisor/runs/{rid}/confirm",
        data={"proposed_changes": json.dumps([dict(CAMERA, name="Evil")])},
    )
    assert "Evil" not in goal_names(uid)


def test_confirm_update_goal_keeps_dependencies(logged):
    uid = user_id()
    a = add_goal_row(uid, "Base")
    b = add_goal_row(uid, "Laptop")
    conn = get_db()
    conn.execute("INSERT INTO goal_dependencies (goal_id, depends_on_goal_id) VALUES (?,?)", (b, a))
    conn.commit()
    conn.close()
    new_deadline = (date.today() + timedelta(days=300)).isoformat()
    rid = make_run(
        uid, changes=[{"op": "update_goal", "goal_id": b, "deadline": new_deadline}]
    )
    logged.post(f"/advisor/runs/{rid}/confirm")
    conn = get_db()
    try:
        deps = conn.execute(
            "SELECT depends_on_goal_id FROM goal_dependencies WHERE goal_id = ?", (b,)
        ).fetchall()
        goal = conn.execute("SELECT deadline FROM goals WHERE id = ?", (b,)).fetchone()
    finally:
        conn.close()
    assert [d["depends_on_goal_id"] for d in deps] == [a]
    assert goal["deadline"] == new_deadline


def test_confirm_set_income_and_obligation(logged):
    uid = user_id()
    rid = make_run(
        uid,
        changes=[
            {"op": "set_income", "amount": 4321},
            {"op": "add_obligation", "name": "Phone", "amount": 25},
        ],
    )
    logged.post(f"/advisor/runs/{rid}/confirm")
    conn = get_db()
    try:
        income = conn.execute("SELECT monthly_amount FROM incomes WHERE user_id = ?", (uid,)).fetchone()
        ob = conn.execute(
            "SELECT amount FROM obligations WHERE user_id = ? AND name = 'Phone'", (uid,)
        ).fetchone()
    finally:
        conn.close()
    assert income["monthly_amount"] == 4321
    assert ob["amount"] == 25


# ------------------------------ ownership ------------------------------ #


def test_other_users_run_is_404_on_view_and_confirm(client):
    owner = user_id()
    rid = make_run(owner, changes=[CAMERA], summary="Private summary")
    register_and_login(client)
    r = client.get(f"/advisor/runs/{rid}")
    assert r.status_code == 404
    assert "Private summary" not in r.get_data(as_text=True)
    assert client.post(f"/advisor/runs/{rid}/confirm").status_code == 404
    assert get_run(rid)["applied_at"] is None
    assert "Camera" not in goal_names(owner)
    assert "Camera" not in goal_names(user_id("other@example.com"))


def test_missing_run_is_404_on_view_and_confirm(logged):
    assert logged.get("/advisor/runs/9999").status_code == 404
    assert logged.post("/advisor/runs/9999/confirm").status_code == 404


# -------------------------------- CSRF -------------------------------- #


def test_post_advisor_without_csrf_token_is_400_and_starts_no_run(app, monkeypatch):
    client = app.test_client()
    login(client)
    use_model(monkeypatch, answer())
    app.config["CSRF_ENABLED"] = True
    r = client.post("/advisor", data={"question": QUESTION})
    assert r.status_code == 400
    assert run_count() == 0


def test_confirm_without_csrf_token_is_400_and_saves_nothing(app):
    client = app.test_client()
    login(client)
    uid = user_id()
    rid = make_run(uid, changes=[CAMERA])
    app.config["CSRF_ENABLED"] = True
    r = client.post(f"/advisor/runs/{rid}/confirm")
    assert r.status_code == 400
    assert "Camera" not in goal_names(uid)
    assert get_run(rid)["applied_at"] is None


def test_advisor_form_includes_csrf_token_field(app, monkeypatch):
    client = app.test_client()
    login(client)
    use_model(monkeypatch, answer())
    app.config["CSRF_ENABLED"] = True
    html = client.get("/advisor").get_data(as_text=True)
    assert "csrf_token" in html


# ------------------------------ escaping ------------------------------ #


def test_summary_html_is_escaped(logged):
    rid = make_run(user_id(), summary="<script>alert(1)</script><b>bold</b>")
    html = logged.get(f"/advisor/runs/{rid}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_question_html_is_escaped_on_list(logged):
    make_run(user_id(), question="<img src=x onerror=alert(1)>")
    html = logged.get("/advisor").get_data(as_text=True)
    assert "<img src=x" not in html
    assert "&lt;img" in html


# --------------------------- recent questions --------------------------- #


def test_advisor_lists_five_most_recent_newest_first(logged):
    uid = user_id()
    for i in range(1, 8):
        make_run(uid, question=f"Question number {i:02d}", verdict="no")
    html = logged.get("/advisor").get_data(as_text=True)
    for i in range(3, 8):
        assert f"Question number {i:02d}" in html
    assert "Question number 01" not in html
    assert "Question number 02" not in html
    assert html.index("Question number 07") < html.index("Question number 03")


def test_advisor_list_shows_verdict_and_link_to_run(logged):
    rid = make_run(user_id(), question="Listed question", verdict="trade_off")
    html = logged.get("/advisor").get_data(as_text=True)
    assert "Listed question" in html
    assert "Fits with a trade-off" in html
    assert f"/advisor/runs/{rid}" in html


def test_advisor_list_never_shows_other_users_questions(client):
    make_run(user_id(), question="Demo secret question")
    register_and_login(client)
    html = client.get("/advisor").get_data(as_text=True)
    assert "Demo secret question" not in html


# ------------------------------ privacy ------------------------------ #


def test_privacy_page_names_openai_and_what_is_sent(client):
    html = text(client.get("/privacy"))
    assert "OpenAI" in html
    for word in ("Income", "Obligations", "Goals", "Top-ups", "Savings Pot"):
        assert word in html


# ------------------------- startup / schema ------------------------- #


def test_advisor_runs_table_has_spec_columns(db_path):
    conn = get_db()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(advisor_runs)")}
    finally:
        conn.close()
    assert cols >= {
        "id", "user_id", "question", "status", "verdict", "summary",
        "proposed_changes", "stopped_reason", "created_at", "finished_at", "applied_at",
    }


def test_init_db_twice_keeps_existing_data(db_path):
    from database.db import init_db

    rid = make_run(user_id(), question="Keep me")
    init_db()
    assert get_run(rid)["question"] == "Keep me"


def test_run_defaults_status_running_and_empty_changes(db_path):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO advisor_runs (user_id, question) VALUES (?, ?)", (user_id(), "Q")
        )
        conn.commit()
        row = conn.execute("SELECT * FROM advisor_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "running"
    assert row["proposed_changes"] == "[]"


def test_other_pages_work_without_openai_key(logged):
    for url in ("/", "/profile", "/goals", "/obligations", "/plan", "/advisor"):
        assert logged.get(url).status_code == 200


# ------------------- security review fixes (S1–S4) ------------------- #

SNAPSHOT = {
    "income": 2000.0,
    "obligations": [{"id": 1, "name": "Rent", "amount": 800.0}],
    "goals": [],
    "top_ups": [],
    "pot_amounts": [],
}
FAR_DEADLINE = (date.today() + timedelta(days=365 * 60)).isoformat()


def _goal(**fields):
    return {**CAMERA, **fields}


# S2: at most ADVISOR_DAILY_LIMIT questions per account in any 24 hours.


def test_daily_limit_blocks_the_next_question(logged, monkeypatch):
    use_model(monkeypatch, answer())
    uid = user_id()
    for _ in range(10):
        make_run(uid)
    r = ask(logged)
    assert r.status_code == 200
    assert "You have asked 10 questions in the last 24 hours" in text(r)
    assert run_count(uid) == 10


def test_questions_older_than_24_hours_do_not_count(logged, monkeypatch):
    use_model(monkeypatch, answer())
    uid = user_id()
    for _ in range(10):
        make_run(uid, age="-25 hours")
    assert ask(logged).status_code == 302
    assert run_count(uid) == 11


def test_daily_limit_comes_from_config(logged, monkeypatch, app):
    use_model(monkeypatch, answer())
    monkeypatch.setitem(app.config, "ADVISOR_DAILY_LIMIT", 1)
    make_run(user_id())
    assert "You have asked 1 questions" in text(ask(logged))


def test_daily_limit_is_per_account(client, monkeypatch):
    use_model(monkeypatch, answer())
    demo = user_id()
    for _ in range(10):
        make_run(demo)
    register_and_login(client)
    assert ask(client).status_code == 302


# S1: confirm saves every change or none, and never shows a 500.


def test_confirm_saves_nothing_when_a_write_fails(logged, monkeypatch):
    uid = user_id()
    rid = make_run(uid, changes=[CAMERA, _goal(name="Lens")])
    real_create_goal = queries.create_goal
    calls = []

    def create_goal_then_fail(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real_create_goal(*args, **kwargs)

    monkeypatch.setattr(queries, "create_goal", create_goal_then_fail)
    r = logged.post(f"/advisor/runs/{rid}/confirm", follow_redirects=True)
    assert r.status_code == 200
    assert "could not be saved, so nothing was saved" in text(r)
    assert goal_names(uid) == []  # the first Goal was rolled back too
    assert get_run(rid)["applied_at"] is None


def test_confirm_can_be_retried_after_a_failed_write(logged, monkeypatch):
    uid = user_id()
    rid = make_run(uid, changes=[CAMERA])
    real_create_goal = queries.create_goal

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(queries, "create_goal", fail)
    logged.post(f"/advisor/runs/{rid}/confirm")
    # Not monkeypatch.undo(): that would also undo conftest's test database.
    monkeypatch.setattr(queries, "create_goal", real_create_goal)
    r = logged.post(f"/advisor/runs/{rid}/confirm")
    assert r.status_code == 302 and r.headers["Location"].endswith("/plan")
    assert goal_names(uid) == ["Camera"]
    assert get_run(rid)["applied_at"] is not None


def test_confirm_with_a_bad_stored_value_saves_nothing(logged):
    uid = user_id()
    rid = make_run(uid, changes=[_goal(name=["Camera"])])
    r = logged.post(f"/advisor/runs/{rid}/confirm", follow_redirects=True)
    assert r.status_code == 200
    assert "nothing was saved" in text(r)
    assert goal_names(uid) == []
    assert get_run(rid)["applied_at"] is None


# S1: the Advisor's checks accept only what the forms accept.


@pytest.mark.parametrize(
    "change",
    [
        _goal(name=["Camera"]),
        _goal(name="x" * 61),
        _goal(name="   "),
        _goal(target_amount=float("inf")),
        _goal(target_amount=float("nan")),
        _goal(target_amount=True),
        _goal(target_amount=10_000_001),
        _goal(deadline=FAR_DEADLINE),
        {"op": "set_income", "amount": float("nan")},
        {"op": "set_income", "amount": 10_000_001},
        {"op": "add_obligation", "name": {"x": 1}, "amount": 50},
        {"op": "update_obligation", "obligation_id": 1, "name": 7},
        "add_goal",
    ],
    ids=[
        "list-name", "long-name", "blank-name", "inf", "nan", "bool", "over-max",
        "deadline-60-years", "nan-income", "income-over-max", "dict-name",
        "int-obligation-name", "not-an-object",
    ],
)
def test_checks_reject_values_the_forms_would_reject(change):
    with pytest.raises(ToolError):
        apply_changes(SNAPSHOT, [change], date.today())


def test_checks_accept_the_form_limits():
    state = apply_changes(
        SNAPSHOT,
        [_goal(name="x" * 60, target_amount=10_000_000), {"op": "set_income", "amount": 10_000_000}],
        date.today(),
    )
    assert state["income"] == 10_000_000


def test_checks_cap_the_number_of_goals():
    snapshot = {**SNAPSHOT, "goals": [
        {"id": i, "name": f"G{i}", "target_amount": 10, "saved_amount": 0,
         "deadline": DEADLINE, "importance": "Should-have"}
        for i in range(1, 101)
    ]}
    with pytest.raises(ToolError):
        apply_changes(snapshot, [CAMERA], date.today())


def _final_answer(**args):
    payload = {"verdict": "yes", "summary": "It fits.", "proposed_changes": [], **args}
    return dispatch("final_answer", json.dumps(payload), SNAPSHOT, date.today(), "camera?")


def test_final_answer_rejects_too_many_changes():
    result, is_final = _final_answer(proposed_changes=[CAMERA] * 11)
    assert not is_final and "at most 10" in result["error"]


def test_final_answer_rejects_a_very_long_summary():
    result, is_final = _final_answer(summary="x" * 4001)
    assert not is_final and "4000" in result["error"]


def test_tool_arguments_that_are_not_an_object_go_back_to_the_model():
    result, is_final = dispatch("final_answer", "[1, 2]", SNAPSHOT, date.today(), "q")
    assert not is_final and "JSON object" in result["error"]


# S3: logs carry the error type, never the error message.


def test_failed_run_logs_only_the_error_type(logged, monkeypatch, caplog):
    def boom(messages, tools):
        raise RuntimeError("Incorrect API key provided: sk-proj-****abcd")

    use_model(monkeypatch, boom)
    with caplog.at_level("ERROR"):
        ask(logged)
    assert "RuntimeError" in caplog.text
    assert "sk-proj" not in caplog.text


# S4: no session key that is published in the repo.


def test_secret_key_is_never_the_old_public_fallback(app):
    assert app.secret_key != "dev-secret-key-change-in-production"
    assert len(app.secret_key) >= 32
