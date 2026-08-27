"""
人材・企業リサーチ API
公的統計と公開求人の集計値のみを扱う。個人情報は保持しない。
"""
import sqlite3, os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DB = Path(__file__).parent / "talent.db"
app = FastAPI(title="人材・企業リサーチ API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://tsune2034.github.io", "https://kairox.jp",
                   "https://www.kairox.jp", "http://127.0.0.1:8090"],
    allow_methods=["GET"], allow_headers=["*"],
)


def q(sql, args=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql, args).fetchall()]
    con.close()
    return rows


app.mount("/img", StaticFiles(directory=Path(__file__).parent / "static" / "img"), name="img")


@app.get("/api/health")
def health():
    return {"status": "ok", "db": DB.exists(), "note": "集計値のみ・個人情報なし"}


@app.get("/api/cities")
def cities(fab_only: bool = False):
    sql = "SELECT code,name,pref,fab,pop,w2020,w2031,r31,emp_mfg,estab_mfg,mfg_share,wage_mfg,a20,lat,lng FROM city"
    if fab_only:
        sql += " WHERE fab != '―'"
    return q(sql + " ORDER BY name")


@app.get("/api/city/{name}")
def city(name: str):
    rows = q("SELECT * FROM city WHERE name = ?", (name,))
    if not rows:
        raise HTTPException(404, f"{name} は登録されていません")
    c = rows[0]
    j = q("SELECT * FROM jobs WHERE city = ?", (name,))
    g = q("SELECT * FROM github WHERE pref = ?", (c["pref"],))
    out = {"city": c, "jobs": j[0] if j else None, "github": g[0] if g else None}
    if j and c["emp_mfg"]:
        out["derived"] = {
            "pool_per_job": round(c["emp_mfg"] / j[0]["total"]),
            "agency_rate": round(j[0]["agency"] / j[0]["total"] * 100, 1),
            "jobs_per_10k": round(j[0]["total"] / c["w2020"] * 10000, 1),
        }
        if g:
            out["derived"]["github_coverage"] = round(g[0]["users"] / c["emp_mfg"] * 100, 2)
    return out


@app.get("/api/difficulty")
def difficulty():
    """採用の難易度ランキング（候補者の厚みが薄い順）"""
    rows = q("""
        SELECT c.name, c.pref, c.fab, c.emp_mfg, c.estab_mfg, c.wage_mfg, c.a20,
               c.w2031, c.r31, j.total AS jobs, j.agency, j.companies,
               g.users AS gh_users
        FROM city c
        JOIN jobs j ON j.city = c.name
        LEFT JOIN github g ON g.pref = c.pref
    """)
    for r in rows:
        r["pool_per_job"] = round(r["emp_mfg"] / r["jobs"]) if r["jobs"] else None
        r["agency_rate"] = round(r["agency"] / r["jobs"] * 100, 1) if r["jobs"] else None
        r["gh_coverage"] = round(r["gh_users"] / r["emp_mfg"] * 100, 2) if r.get("gh_users") and r["emp_mfg"] else None
    rows.sort(key=lambda x: x["pool_per_job"] or 9e9)
    return rows


@app.get("/api/search")
def search(pref: str = None, min_emp: int = None, fab_only: bool = False):
    sql = "SELECT name,pref,fab,pop,w2031,r31,emp_mfg,mfg_share,wage_mfg,a20 FROM city WHERE 1=1"
    args = []
    if pref:
        sql += " AND pref = ?"; args.append(pref)
    if min_emp:
        sql += " AND emp_mfg >= ?"; args.append(min_emp)
    if fab_only:
        sql += " AND fab != '―'"
    return q(sql + " ORDER BY emp_mfg DESC", tuple(args))


@app.get("/api/sources")
def sources():
    return {
        "公的統計": [
            {"名称": "国勢調査 人口等基本集計", "id": "e-Stat 0003445162"},
            {"名称": "人口動態調査", "id": "e-Stat 0003412064"},
            {"名称": "住民基本台帳人口移動報告", "id": "e-Stat 0004044330 ほか"},
            {"名称": "市区町村データ 基礎データ", "id": "e-Stat 0000020103"},
            {"名称": "将来推計人口", "id": "社人研 令和5(2023)年推計"},
        ],
        "調査ツール": [
            {"名称": "厚生年金 適用事業所検索", "url": "https://www.nenkin.go.jp/do/search_section/",
             "分かること": "事業所ごとの被保険者数（実社員数）"},
            {"名称": "人材サービス総合サイト", "url": "https://jinzai.hellowork.mhlw.go.jp/JinzaiWeb/GICB101010.do",
             "分かること": "派遣のマージン率・行政処分・労使協定"},
        ],
        "求人": {"取得元": "Indeed 公開求人", "取得日": "2026-08-27", "件数": 278},
        "GitHub": {"取得元": "GitHub Search API", "取得日": "2026-08-27", "備考": "都道府県単位の集計のみ"},
    }


@app.get("/api/foreign")
def foreign():
    """在留外国人数の推移（出入国在留管理庁）"""
    rows = q("SELECT * FROM foreign_residents ORDER BY year")
    pref = q("SELECT * FROM foreign_pref ORDER BY total DESC")
    recent = [r for r in rows if r["yoy"] is not None][-3:]
    avg = round(sum(r["yoy"] for r in recent) / len(recent)) if recent else None
    return {
        "推移": rows,
        "都道府県": pref,
        "直近3年の年平均増加": avg,
        "対比": {
            "日本の生産年齢人口": "年 約-56万人（社人研推計 2020→2031）",
            "在留外国人": f"年 約+{avg//10000}万人（直近3年平均）" if avg else None,
        },
        "出典": "出入国在留管理庁 在留外国人統計（各年末）",
    }


@app.get("/api/policy")
def policy(category: str = None):
    """外国人材に関する制度変更（2026年8月時点）"""
    sql = "SELECT * FROM policy"
    args = []
    if category:
        sql += " WHERE category = ?"; args.append(category)
    rows = q(sql + " ORDER BY id", tuple(args))
    return {
        "件数": len(rows),
        "制度": rows,
        "要点": "2027年4月に永住許可の要件が大きく変わる。年収要件の新設、"
                "国益要件の明確化、配偶者特例の期間延長、永住資格の取消し制度。"
                "同時に技能実習が廃止され育成就労へ移行する。",
        "確認日": "2026-08-27",
    }


@app.get("/api/retention")
def retention():
    """定着・離職のデータ（厚生労働省 雇用動向調査）"""
    t = q("SELECT * FROM turnover WHERE period='令和6年上半期' ORDER BY exit_rate DESC")
    w = q("SELECT * FROM wage_change ORDER BY period DESC")
    v = q("SELECT * FROM vacancy ORDER BY rate DESC")
    mfg = next((r for r in t if r["industry"] == "製造業"), None)
    allind = next((r for r in t if r["industry"] == "産業計"), None)
    return {
        "産業別入職離職": t,
        "転職者の賃金変動": w,
        "欠員率": v,
        "製造業の特徴": {
            "入職率": mfg["entry_rate"] if mfg else None,
            "離職率": mfg["exit_rate"] if mfg else None,
            "全産業の離職率": allind["exit_rate"] if allind else None,
            "欠員率": 2.2,
            "読み方": "製造業は入職も離職も全産業平均より低い。人が動きにくい業界だが、"
                     "動き出すと戻らない。転職者の40.5%が賃金増を実現しており、"
                     "条件が見合わなければ移る合理性がある。",
        },
        "出典": "厚生労働省 雇用動向調査（令和6年・令和7年上半期）",
    }


@app.get("/")
def index():
    f = Path(__file__).parent / "static" / "index.html"
    if f.exists():
        return FileResponse(f)
    return {"api": "/api/health", "docs": "/docs"}
