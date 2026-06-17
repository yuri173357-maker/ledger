"""账本后台核心 — 与上传的前端数据结构完全对齐。

分类（8 个，消费 expense / 储蓄 saving）：
  daily 日常 / enjoy 享受 / dream 梦想账户 / growth 成长 / sudden 突然支出
  pension 养老金 / invest 投资金 / insure 保险金

entry: {id, date(YYYY-MM-DD), cat(key), amount, note}
budget: 每分类一个历史数组 [{from:"YYYY-MM", amount, period:"month"|"year"}]
"""
import sqlite3, os, json, time, random, string
from datetime import date
from contextlib import contextmanager

DB_PATH = os.environ.get("LEDGER_DB", os.path.join(os.path.dirname(__file__), "ledger.db"))

CATEGORIES = [
    {"key": "daily",   "name": "日常支出", "type": "expense", "icon": "🍚",
     "note": "房租/房贷 · 水电燃气 · 物业 · 话费网费 · 日常饮食 · 交通 · 日用品"},
    {"key": "enjoy",   "name": "享受支出", "type": "expense", "icon": "🍰",
     "note": "衣服饰品 · 娱乐 · 美食 · 美容"},
    {"key": "dream",   "name": "梦想账户", "type": "expense", "icon": "🌈",
     "note": "大件购物 · 旅行 · 医美 · 黄金"},
    {"key": "growth",  "name": "成长支出", "type": "expense", "icon": "📚",
     "note": "教练 · AI · 书籍 · 按摩"},
    {"key": "sudden",  "name": "突然支出", "type": "expense", "icon": "⚡",
     "note": "医疗 · 人情 · 家庭"},
    {"key": "pension", "name": "养老金",   "type": "saving",  "icon": "🏡",
     "note": "为未来的自己存一点"},
    {"key": "invest",  "name": "投资金",   "type": "saving",  "icon": "🌱",
     "note": "长期持有 · 复利增长"},
    {"key": "insure",  "name": "保险金",   "type": "saving",  "icon": "🛡️",
     "note": "给生活一份保障"},
]
CAT_KEYS = {c["key"] for c in CATEGORIES}
CAT_BY_KEY = {c["key"]: c for c in CATEGORIES}
CAT_BY_NAME = {c["name"]: c["key"] for c in CATEGORIES}

DEFAULT_BUDGETS = {
    "daily":   [{"from": "2000-01", "amount": 2000, "period": "month"}],
    "enjoy":   [{"from": "2000-01", "amount": 500,  "period": "month"}],
    "dream":   [{"from": "2000-01", "amount": 5000, "period": "year"}],
    "growth":  [{"from": "2000-01", "amount": 300,  "period": "month"}],
    "sudden":  [{"from": "2000-01", "amount": 2000, "period": "year"}],
    "pension": [{"from": "2000-01", "amount": 500,  "period": "month"}],
    "invest":  [{"from": "2000-01", "amount": 1000, "period": "month"}],
    "insure":  [{"from": "2000-01", "amount": 3000, "period": "year"}],
}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS entries(
            id TEXT PRIMARY KEY, date TEXT NOT NULL, cat TEXT NOT NULL,
            amount REAL NOT NULL, note TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS kv(
            k TEXT PRIMARY KEY, v TEXT NOT NULL)""")
        if not conn.execute("SELECT v FROM kv WHERE k='budgets'").fetchone():
            conn.execute("INSERT INTO kv(k,v) VALUES('budgets',?)", (json.dumps(DEFAULT_BUDGETS),))
        if not conn.execute("SELECT v FROM kv WHERE k='notes'").fetchone():
            conn.execute("INSERT INTO kv(k,v) VALUES('notes','{}')")


def _gen_id():
    return f"{int(time.time()*1000)}-{''.join(random.choices(string.ascii_lowercase+string.digits,k=5))}"

def _budgets():
    with get_db() as conn:
        return json.loads(conn.execute("SELECT v FROM kv WHERE k='budgets'").fetchone()["v"])

def _notes():
    with get_db() as conn:
        return json.loads(conn.execute("SELECT v FROM kv WHERE k='notes'").fetchone()["v"])

def _budget_at(cat, ym):
    hist = sorted(_budgets().get(cat, []), key=lambda b: b["from"])
    chosen = None
    for b in hist:
        if b["from"] <= ym:
            chosen = b
    return chosen or (hist[0] if hist else None)


def resolve_cat(cat):
    if cat in CAT_KEYS: return cat
    if cat in CAT_BY_NAME: return CAT_BY_NAME[cat]
    for c in CATEGORIES:
        if c["name"].startswith(cat) or cat in c["name"]:
            return c["key"]
    raise ValueError("未知分类：" + str(cat) + "。可用：" + "、".join(c["name"] for c in CATEGORIES))


def add_entry(cat, amount, date_str=None, note=None):
    key = resolve_cat(cat)
    d = date_str or date.today().isoformat()
    eid = _gen_id()
    with get_db() as conn:
        conn.execute("INSERT INTO entries(id,date,cat,amount,note) VALUES(?,?,?,?,?)",
                     (eid, d, key, float(amount), note or ""))
    return {"id": eid, "cat": key, "cat_name": CAT_BY_KEY[key]["name"],
            "amount": amount, "date": d, "warning": _check(key, d)}


def delete_entry(eid):
    with get_db() as conn:
        conn.execute("DELETE FROM entries WHERE id=?", (eid,))
    return {"deleted": eid}


def update_entry(eid, **f):
    allowed = {"cat", "amount", "date", "note"}
    if f.get("cat"): f["cat"] = resolve_cat(f["cat"])
    sets = {k: v for k, v in f.items() if k in allowed and v is not None}
    if not sets: return {"updated": eid, "changed": 0}
    cols = ", ".join(f"{k}=?" for k in sets)
    with get_db() as conn:
        conn.execute(f"UPDATE entries SET {cols} WHERE id=?", (*sets.values(), eid))
    return {"updated": eid, "changed": len(sets)}


def _sum(cat, year, month=None):
    pat = f"{year:04d}-{month:02d}%" if month else f"{year:04d}%"
    with get_db() as conn:
        return conn.execute("SELECT COALESCE(SUM(amount),0) s FROM entries WHERE cat=? AND date LIKE ?",
                            (cat, pat)).fetchone()["s"]


def _check(cat, d):
    y, m = int(d[:4]), int(d[5:7])
    b = _budget_at(cat, f"{y:04d}-{m:02d}")
    if not b or CAT_BY_KEY[cat]["type"] == "saving": return None
    name = CAT_BY_KEY[cat]["name"]
    if b["period"] == "month":
        s = _sum(cat, y, m)
        if s > b["amount"]:
            return f"⚠️ {name}本月已 {s:.0f} 元，超出月预算 {b['amount']:.0f}（超 {s-b['amount']:.0f}）"
    else:
        s = _sum(cat, y)
        if s > b["amount"]:
            return f"⚠️ {name}本年已 {s:.0f} 元，超出年预算 {b['amount']:.0f}（超 {s-b['amount']:.0f}）"
    return None


def overview(year, month):
    ym = f"{year:04d}-{month:02d}"
    cats = []
    for c in CATEGORIES:
        b = _budget_at(c["key"], ym)
        sm, sy = _sum(c["key"], year, month), _sum(c["key"], year)
        period = b["period"] if b else "month"
        budget = b["amount"] if b else 0
        used = sm if period == "month" else sy
        cats.append({**{k: c[k] for k in ("key","name","type","icon","note")},
            "period": period, "budget": budget,
            "spent_month": round(sm,2), "spent_year": round(sy,2),
            "used": round(used,2), "remaining": round(budget-used,2),
            "over": used > budget and c["type"] == "expense"})
    return {"year": year, "month": month, "categories": cats}


def list_entries(limit=100, year=None, month=None, cat=None):
    q, a = "SELECT * FROM entries WHERE 1=1", []
    if cat: q += " AND cat=?"; a.append(resolve_cat(cat))
    if year and month: q += " AND date LIKE ?"; a.append(f"{year:04d}-{month:02d}%")
    elif year: q += " AND date LIKE ?"; a.append(f"{year:04d}%")
    q += " ORDER BY date DESC, id DESC LIMIT ?"; a.append(limit)
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(q, a).fetchall()]
    for r in rows:
        r["cat_name"] = CAT_BY_KEY.get(r["cat"], {}).get("name", r["cat"])
    return rows


def dump_state():
    with get_db() as conn:
        entries = [dict(r) for r in conn.execute("SELECT * FROM entries").fetchall()]
    return {"entries": entries, "budgets": _budgets(), "categoryNotes": _notes(), "categories": CATEGORIES}


def replace_state(entries=None, budgets=None, notes=None):
    with get_db() as conn:
        if entries is not None:
            conn.execute("DELETE FROM entries")
            for e in entries:
                conn.execute("INSERT OR REPLACE INTO entries(id,date,cat,amount,note) VALUES(?,?,?,?,?)",
                             (e.get("id") or _gen_id(), e["date"], e["cat"], float(e["amount"]), e.get("note","")))
        if budgets is not None:
            conn.execute("UPDATE kv SET v=? WHERE k='budgets'", (json.dumps(budgets),))
        if notes is not None:
            conn.execute("UPDATE kv SET v=? WHERE k='notes'", (json.dumps(notes),))
    return {"ok": True}


def set_budget(cat, amount, period, from_ym=None):
    key = resolve_cat(cat)
    from_ym = from_ym or date.today().strftime("%Y-%m")
    b = _budgets(); b.setdefault(key, [])
    b[key].append({"from": from_ym, "amount": float(amount), "period": period})
    with get_db() as conn:
        conn.execute("UPDATE kv SET v=? WHERE k='budgets'", (json.dumps(b),))
    return {"cat": key, "amount": amount, "period": period, "from": from_ym}


if __name__ == "__main__":
    init_db()
    print("categories:", [c["key"] for c in CATEGORIES])
