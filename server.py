"""账本服务 — Web 前端 + MCP（Xander）共用同一份云端数据。

部署要点（沿用 mood/diet）：
- DNS rebinding 保护关闭
- token 走 URL path：/{TOKEN} 看板，/{TOKEN}/mcp 给 Xander
"""
import os, json
from datetime import date
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import HTMLResponse, JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from contextlib import asynccontextmanager
import ledger_core as L

TOKEN = os.environ.get("LEDGER_TOKEN", "changeme")
L.init_db()

mcp = FastMCP("ledger")
mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)


# ── MCP 工具（Xander 用）──
@mcp.tool()
def record(category: str, amount: float, note: str = "", date_str: str = "") -> str:
    """记一笔。category 可用中文名或 key：日常支出/享受支出/梦想账户/成长支出/突然支出/养老金/投资金/保险金。
    amount 金额。note 备注。date_str 日期 YYYY-MM-DD，默认今天。消费类超预算会提醒。"""
    try:
        r = L.add_entry(category, amount, date_str or None, note or None)
    except ValueError as e:
        return str(e)
    msg = f"已记：{r['cat_name']} {amount} 元（{r['date']}）"
    if r["warning"]:
        msg += "\n" + r["warning"]
    return msg


@mcp.tool()
def overview(year: int = 0, month: int = 0) -> str:
    """查看某月各分类的预算执行情况。默认本月。"""
    t = date.today()
    return json.dumps(L.overview(year or t.year, month or t.month), ensure_ascii=False, indent=2)


@mcp.tool()
def recent(limit: int = 20, category: str = "") -> str:
    """查看最近的记录，可按分类过滤。"""
    return json.dumps(L.list_entries(limit=limit, cat=category or None), ensure_ascii=False, indent=2)


@mcp.tool()
def edit(entry_id: str, amount: float = -1, category: str = "", note: str = "", date_str: str = "") -> str:
    """修改某条记录，只传要改的。"""
    f = {}
    if amount >= 0: f["amount"] = amount
    if category: f["cat"] = category
    if note: f["note"] = note
    if date_str: f["date"] = date_str
    r = L.update_entry(entry_id, **f)
    return f"已更新 {entry_id}（改 {r['changed']} 项）"


@mcp.tool()
def remove(entry_id: str) -> str:
    """删除某条记录。"""
    L.delete_entry(entry_id)
    return f"已删除 {entry_id}"


@mcp.tool()
def categories() -> str:
    """查看全部分类与当前预算。"""
    return json.dumps(L.overview(date.today().year, date.today().month), ensure_ascii=False, indent=2)


# ── Web 路由 ──
def _auth(r):
    return r.path_params.get("token") == TOKEN

async def web_home(request):
    if not _auth(request):
        return HTMLResponse("<h1>401</h1>", status_code=401)
    with open(os.path.join(os.path.dirname(__file__), "frontend.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read().replace("__TOKEN__", TOKEN))

async def api_state(request):
    if not _auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(L.dump_state())

async def api_sync(request):
    """前端整体回写（entries/budgets/notes）。"""
    if not _auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    L.replace_state(entries=body.get("entries"), budgets=body.get("budgets"),
                    notes=body.get("categoryNotes"))
    return JSONResponse({"ok": True})

async def api_add(request):
    if not _auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    b = await request.json()
    try:
        r = L.add_entry(b["cat"], b["amount"], b.get("date") or None, b.get("note") or None)
        return JSONResponse({"ok": True, "result": r})
    except (ValueError, KeyError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

async def api_del(request):
    if not _auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    L.delete_entry(request.path_params["eid"])
    return JSONResponse({"ok": True})


mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = Starlette(routes=[
    Route("/{token}", web_home),
    Route("/{token}/api/state", api_state),
    Route("/{token}/api/sync", api_sync, methods=["POST"]),
    Route("/{token}/api/add", api_add, methods=["POST"]),
    Route("/{token}/api/del/{eid}", api_del, methods=["DELETE"]),
    Mount("/{token}", app=mcp_app),
], lifespan=lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
