# tests/test_store.py
"""T2 会话存储：tmp 独立 db 文件，覆盖 CRUD/级联/正序/summarized 计数。"""
import pytest

import aigis_web.store as store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "chat.db")
    return store


def test_create_and_get_session(db):
    s = db.create_session("测试会话")
    assert s["id"] and s["title"] == "测试会话" and s["summary"] == ""
    got = db.get_session(s["id"])
    assert got == s
    assert db.get_session("nope") is None


def test_list_sessions_order_by_updated_at_desc(db):
    a = db.create_session("a")
    b = db.create_session("b")
    assert [s["id"] for s in db.list_sessions()] == [b["id"], a["id"]]
    db.append_message(a["id"], "user", "你好")  # touch a → a 回到最前
    assert [s["id"] for s in db.list_sessions()] == [a["id"], b["id"]]


def test_rename_and_delete_session(db):
    s = db.create_session("old")
    assert db.rename_session(s["id"], "new") is True
    assert db.get_session(s["id"])["title"] == "new"
    assert db.rename_session("nope", "x") is False
    assert db.delete_session(s["id"]) is True
    assert db.get_session(s["id"]) is None
    assert db.delete_session(s["id"]) is False


def test_append_message_first_sets_title_from_content(db):
    s = db.create_session()  # 空标题
    long_q = "三环内有多少个公园分布情况怎么样以及各区的数量统计"
    db.append_message(s["id"], "user", long_q)
    assert db.get_session(s["id"])["title"] == long_q[:20]  # 前 20 字
    assert len(db.get_session(s["id"])["title"]) == 20
    # 显式标题不被覆盖
    s2 = db.create_session("显式标题")
    db.append_message(s2["id"], "user", "第二条会话的首条消息")
    assert db.get_session(s2["id"])["title"] == "显式标题"


def test_append_message_meta_json_and_touch(db):
    s = db.create_session("m")
    before = db.get_session(s["id"])["updated_at"]
    m = db.append_message(s["id"], "assistant", "共 42 个",
                          {"sql": "SELECT ...", "chat_mode": False, "row_count": 42})
    assert m["id"] == 1 and m["meta"]["row_count"] == 42
    assert db.get_session(s["id"])["updated_at"] >= before
    msgs = db.get_messages(s["id"])
    assert msgs[0]["meta"] == {"sql": "SELECT ...", "chat_mode": False, "row_count": 42}


def test_get_messages_returns_recent_limit_in_ascending_order(db):
    s = db.create_session("q")
    for i in range(1, 6):  # 插入 5 条
        db.append_message(s["id"], "user" if i % 2 else "assistant", f"msg{i}")
    msgs = db.get_messages(s["id"], limit=3)  # 最近 3 条且正序
    assert [m["content"] for m in msgs] == ["msg3", "msg4", "msg5"]
    assert [m["id"] for m in msgs] == [3, 4, 5]
    assert len(db.get_messages(s["id"])) == 5  # 不传 limit 全量
    assert db.get_messages("nope") == []


def test_delete_session_cascades_messages(db):
    s = db.create_session("c")
    db.append_message(s["id"], "user", "q1")
    db.append_message(s["id"], "assistant", "a1")
    db.delete_session(s["id"])
    assert db.get_messages(s["id"]) == []
    assert db.summarized_count(s["id"]) == 0


def test_set_summary(db):
    s = db.create_session("s")
    assert db.set_summary(s["id"], "用户在查询三环内公园") is True
    assert db.get_session(s["id"])["summary"] == "用户在查询三环内公园"
    assert db.set_summary("nope", "x") is False


def test_summarized_count_and_mark(db):
    s = db.create_session("z")
    ids = [db.append_message(s["id"], "user" if i % 2 else "assistant", f"m{i}")["id"]
           for i in range(1, 5)]
    assert db.summarized_count(s["id"]) == 0
    assert db.mark_summarized(s["id"], ids[:2]) == 2
    assert db.summarized_count(s["id"]) == 2
    # 重复标记不重复计数；跨会话 id 不生效
    assert db.mark_summarized(s["id"], ids[:2]) == 0
    other = db.create_session("o")
    assert db.mark_summarized(other["id"], ids[2:]) == 0
    assert db.summarized_count(s["id"]) == 2
    # 标记信息随消息行读回
    msgs = db.get_messages(s["id"])
    assert [m["summarized"] for m in msgs] == [1, 1, 0, 0]
