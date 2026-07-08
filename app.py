import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

st.set_page_config(page_title="SaveTree ERP", page_icon="🌳", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebar"]{background-color:#1a3a2a}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] span,[data-testid="stSidebar"] label{color:#95d5b2!important}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#fff!important}
div[data-testid="metric-container"]{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 6px rgba(0,0,0,0.08)}
</style>
""", unsafe_allow_html=True)

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

MASTER_SHEET = "현장마스터"
LOG_SHEET = "작업내역"

COST_CATEGORIES = ["재료비", "노무비", "경비"]
KST = ZoneInfo("Asia/Seoul")


def now_kst():
    return datetime.now(KST)


@st.cache_resource
def get_client():
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    return gspread.authorize(creds)


def clean_number(val):
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    s = s.replace(",", "").replace(" ", "")
    for unit in ["원", "㎡", "m2", "m²"]:
        s = s.replace(unit, "")
    try:
        return float(s)
    except ValueError:
        return None


@st.cache_data(ttl=60)
def load_data():
    client = get_client()
    sh = client.open_by_key(st.secrets["spreadsheet_id"])

    def read_ws(name):
        try:
            ws = sh.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            actual = [w.title for w in sh.worksheets()]
            raise RuntimeError(f"'{name}' 탭을 찾을 수 없습니다. 실제 존재하는 탭: {actual}")
        vals = ws.get_all_values()
        if not vals or len(vals) < 2:
            return pd.DataFrame()
        headers = [h.strip() or f"col{i}" for i, h in enumerate(vals[0])]
        return pd.DataFrame(vals[1:], columns=headers)

    warnings = []

    # ── 현장마스터 (한 행 = 공사 하나. 현장명 컬럼 없음 — 공사명 문자열 안에 현장명이 포함됨) ──
    mdf = read_ws(MASTER_SHEET)
    mcol_map = {}
    for c in mdf.columns:
        cl = c.lower().replace(" ", "")
        if "공사명" in cl or "공사" in cl:
            mcol_map[c] = "공사명"
        elif "완료여부" in cl or "공사완료" in cl:
            mcol_map[c] = "완료여부"
        elif "담당자" in cl or "센터장" in cl or "담당" in cl:
            mcol_map[c] = "담당자"
        elif "면적" in cl:
            mcol_map[c] = "면적"
        elif "계약금액" in cl or "총계약" in cl:
            mcol_map[c] = "총계약금액"
        elif "예산" in cl:
            mcol_map[c] = "월예산"
        elif "시작" in cl:
            mcol_map[c] = "계약시작"
        elif "종료" in cl:
            mcol_map[c] = "계약종료"
        elif "유형" in cl:
            mcol_map[c] = "유형"
    mdf = mdf.rename(columns=mcol_map)

    if "공사명" in mdf.columns:
        mdf = mdf[mdf["공사명"].astype(str).str.strip() != ""]
    else:
        warnings.append(f"'{MASTER_SHEET}' 시트에 공사명 컬럼을 찾지 못했습니다.")
        mdf = pd.DataFrame(columns=["공사명"])

    if "완료여부" in mdf.columns:
        mdf["완료여부"] = mdf["완료여부"].astype(str).str.strip().str.upper().isin(["O", "ㅇ", "V", "TRUE", "완료"])
    else:
        mdf["완료여부"] = False

    dup_mask = mdf.duplicated(subset=["공사명"], keep=False)
    if dup_mask.sum() > 0:
        dup_vals = sorted(mdf.loc[dup_mask, "공사명"].unique())
        warnings.append(
            f"현장마스터에 '공사명'이 중복 등록됐습니다: {dup_vals} "
            f"— 계약금액/계약시작일이 합산/혼동될 수 있으니 정리하세요."
        )

    for numcol in ["월예산", "면적", "총계약금액"]:
        if numcol in mdf.columns:
            parsed = mdf[numcol].apply(clean_number)
            bad = mdf.loc[parsed.isna() & mdf[numcol].astype(str).str.strip().ne(""), numcol]
            if len(bad) > 0:
                warnings.append(f"현장마스터의 '{numcol}' 값 중 숫자로 변환 못한 값 {len(bad)}건 (0으로 처리됨): {list(bad.unique())[:3]}")
            mdf[numcol] = parsed.fillna(0)
    if "총계약금액" not in mdf.columns:
        mdf["총계약금액"] = 0

    for datecol in ["계약시작", "계약종료"]:
        if datecol in mdf.columns:
            mdf[datecol + "_dt"] = pd.to_datetime(mdf[datecol], errors="coerce", format="mixed")

    # ── 작업내역 (공사명으로만 구분) ──────────────────────────
    rdf = read_ws(LOG_SHEET)
    rcol_map = {}
    for c in rdf.columns:
        cl = c.lower().replace(" ", "")
        if "공사명" in cl or "공사" in cl:
            rcol_map[c] = "공사명"
        elif "날짜" in cl:
            rcol_map[c] = "날짜"
        elif "카테고리" in cl:
            rcol_map[c] = "카테고리"
        elif "작업" in cl and "항목" in cl:
            rcol_map[c] = "작업항목"
        elif "금액" in cl:
            rcol_map[c] = "금액"
        elif "상태" in cl:
            rcol_map[c] = "상태"
        elif "비고" in cl:
            rcol_map[c] = "비고"
    rdf = rdf.rename(columns=rcol_map)

    if "작업항목" in rdf.columns:
        rdf = rdf[rdf["작업항목"].astype(str).str.strip() != ""]
    else:
        warnings.append(f"'{LOG_SHEET}' 시트에 작업항목 컬럼을 찾지 못했습니다.")

    if "공사명" not in rdf.columns:
        warnings.append(f"'{LOG_SHEET}' 시트에 공사명 컬럼을 찾지 못했습니다.")
        rdf["공사명"] = ""

    if "금액" in rdf.columns:
        parsed = rdf["금액"].apply(clean_number)
        bad = rdf.loc[parsed.isna() & rdf["금액"].astype(str).str.strip().ne(""), "금액"]
        if len(bad) > 0:
            warnings.append(f"작업내역의 '금액' 값 중 숫자로 변환 못한 값 {len(bad)}건 (0으로 처리됨): {list(bad.unique())[:3]}")
        rdf["금액"] = parsed.fillna(0)
    else:
        rdf["금액"] = 0

    if "날짜" in rdf.columns:
        rdf["날짜_dt"] = pd.to_datetime(rdf["날짜"], errors="coerce", format="mixed")
        bad_dates = rdf["날짜"].astype(str).str.strip().ne("") & rdf["날짜_dt"].isna()
        if bad_dates.sum() > 0:
            bad_rows = rdf.loc[bad_dates]
            by_proj = bad_rows.groupby("공사명")["날짜"].apply(lambda s: sorted(set(s))[:3])
            detail = "; ".join(f"{proj} {vals}" for proj, vals in by_proj.items())
            warnings.append(
                f"작업내역의 '날짜' 값 중 인식 못한 값 {int(bad_dates.sum())}건 — {detail} "
                f"(해당 행은 완료여도 원가 집계·연도 필터에서 빠집니다)"
            )
        rdf["날짜"] = rdf["날짜_dt"].dt.strftime("%Y-%m-%d").fillna(rdf["날짜"])
    else:
        rdf["날짜_dt"] = pd.NaT

    # 공사명이 현장마스터에 없는 행 — 원가 집계엔 포함되지만 계약금액을 못 찾음 (문자열이 한 글자만 달라도 여기 걸림)
    if "공사명" in rdf.columns and "공사명" in mdf.columns:
        unknown = set(rdf["공사명"].dropna().unique()) - set(mdf["공사명"].dropna().unique())
        unknown.discard("")
        if unknown:
            unknown_amt = rdf[rdf["공사명"].isin(unknown)]["금액"].sum()
            warnings.append(
                f"현장마스터에 없는 공사명이 작업내역에 있습니다: {sorted(unknown)} "
                f"— 원가 집계에는 포함되지만 계약금액이 없어 이윤율은 계산되지 않습니다(총 {unknown_amt:,.0f}원). "
                f"오타로 인한 불일치일 수 있으니 문자열을 정확히 확인하세요."
            )

    if "상태" in rdf.columns:
        bad_status_mask = ~rdf["상태"].isin(["완료", "예정"])
        if bad_status_mask.sum() > 0:
            by_proj = (
                rdf.loc[bad_status_mask]
                .groupby("공사명")
                .agg(건수=("금액", "size"), 금액합계=("금액", "sum"))
            )
            detail = "; ".join(f"{proj} {int(row['건수'])}건({row['금액합계']:,.0f}원)" for proj, row in by_proj.iterrows())
            warnings.append(
                f"'상태'가 완료/예정 중 하나도 아닌 행이 있습니다 — {detail}. "
                f"완료/예정 어느 집계에도 안 잡힙니다."
            )

    if "카테고리" in rdf.columns and "상태" in rdf.columns:
        done = rdf[rdf["상태"] == "완료"]
        bad_cat_mask = ~done["카테고리"].isin(COST_CATEGORIES)
        if bad_cat_mask.sum() > 0:
            by_proj = (
                done.loc[bad_cat_mask]
                .assign(카테고리표시=done.loc[bad_cat_mask, "카테고리"].replace("", "(비어있음)"))
                .groupby("공사명")
                .agg(건수=("금액", "size"), 금액합계=("금액", "sum"))
            )
            detail = "; ".join(f"{proj} {int(row['건수'])}건({row['금액합계']:,.0f}원)" for proj, row in by_proj.iterrows())
            warnings.append(
                f"완료 상태인데 '카테고리'가 재료비/노무비/경비가 아닌 행이 있습니다 — {detail}. "
                f"원가 집계에서 빠져 이윤이 실제보다 높게 나옵니다."
            )

    return mdf, rdf, warnings


def fmt_won(n):
    return f"{float(n) / 1000:,.1f}천원"


def fmt_pct(n):
    return f"{n:.1f}%" if n is not None else "N/A"


def get_contract_total(mdf, project_name):
    df = mdf[mdf["공사명"] == project_name] if "공사명" in mdf.columns else pd.DataFrame()
    if df.empty or "총계약금액" not in df.columns:
        return 0
    return float(df["총계약금액"].sum())


def get_contract_start(mdf, project_name):
    df = mdf[mdf["공사명"] == project_name] if "공사명" in mdf.columns else pd.DataFrame()
    if df.empty or "계약시작_dt" not in df.columns:
        return None
    vals = df["계약시작_dt"].dropna()
    return pd.Timestamp(vals.min()) if not vals.empty else None


def get_contract_end(mdf, project_name):
    df = mdf[mdf["공사명"] == project_name] if "공사명" in mdf.columns else pd.DataFrame()
    if df.empty or "계약종료_dt" not in df.columns:
        return None
    vals = df["계약종료_dt"].dropna()
    return pd.Timestamp(vals.max()) if not vals.empty else None


def project_active_in_year(mdf, project_name, year):
    """계약 기간에 해당 연도가 포함되는지. 시작/종료일이 없으면 판단 불가로 보고 True(표시 유지)."""
    if year is None:
        return True
    start = get_contract_start(mdf, project_name)
    end = get_contract_end(mdf, project_name)
    if start is not None and start.year > year:
        return False
    if end is not None and end.year < year:
        return False
    return True


def cost_breakdown(rdf, project_name=None, since=None, year=None):
    df = rdf.copy()
    if "상태" in df.columns:
        df = df[df["상태"] == "완료"]
    if project_name is not None and "공사명" in df.columns:
        df = df[df["공사명"] == project_name]
    if since is not None and "날짜_dt" in df.columns:
        df = df[df["날짜_dt"] >= since]
    if year is not None and "날짜_dt" in df.columns:
        df = df[df["날짜_dt"].dt.year == year]
    return cost_breakdown_from_df(df)


def cost_breakdown_from_df(df):
    result = {}
    for cat in COST_CATEGORIES:
        result[cat] = df[df["카테고리"] == cat]["금액"].sum() if "카테고리" in df.columns else 0
    result["미분류"] = df[~df["카테고리"].isin(COST_CATEGORIES)]["금액"].sum() if "카테고리" in df.columns else df["금액"].sum()
    result["합계"] = result["재료비"] + result["노무비"] + result["경비"] + result["미분류"]
    return result


def project_progress_pct(mdf, project_name, today):
    """전체 계약기간 대비 현재까지 경과 비율(시간 기준 공정률). 실제 작업 진척과는 다른 개념."""
    start = get_contract_start(mdf, project_name)
    end = get_contract_end(mdf, project_name)
    if start is None or end is None or end <= start:
        return None
    total_days = (end - start).days
    elapsed_days = (today - start).days
    pct = elapsed_days / total_days * 100
    return max(0.0, min(100.0, pct))


def profit(revenue, cost):
    margin = revenue - cost
    rate = round(margin / revenue * 100, 1) if revenue else None
    return margin, rate


# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🌳 SaveTree")
    st.markdown("##### 공사 원가 관리 시스템")
    st.divider()
    menu = st.radio("메뉴", ["📊 대시보드", "📋 작업 내역", "📄 보고서"], label_visibility="hidden")
    st.divider()
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"업데이트: {now_kst().strftime('%Y-%m-%d %H:%M:%S')}")

# ── 데이터 로드 ───────────────────────────────────────────────────────────
try:
    mdf, rdf, load_warnings = load_data()
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

for w in load_warnings:
    st.sidebar.warning(w)

PROJECTS = mdf.to_dict("records") if not mdf.empty else []
known_projects = set(mdf["공사명"].dropna().unique()) if "공사명" in mdf.columns else set()
logged_projects = set(rdf["공사명"].dropna().unique()) if "공사명" in rdf.columns else set()
logged_projects.discard("")

unregistered_projects = sorted(logged_projects - known_projects)
for pname in unregistered_projects:
    PROJECTS.append({"공사명": pname, "담당자": "(미등록)", "면적": 0, "총계약금액": 0})

project_names = sorted(known_projects | logged_projects)

_done_for_years = rdf[rdf["상태"] == "완료"] if "상태" in rdf.columns else pd.DataFrame()
_years_from_logs = set(_done_for_years["날짜_dt"].dropna().dt.year.tolist()) if "날짜_dt" in _done_for_years.columns and not _done_for_years.empty else set()

_years_from_contracts = set()
for p in PROJECTS:
    s = get_contract_start(mdf, p["공사명"])
    e = get_contract_end(mdf, p["공사명"])
    if s is not None and e is not None:
        _years_from_contracts.update(range(s.year, e.year + 1))
    elif s is not None:
        _years_from_contracts.add(s.year)

ALL_YEARS = sorted(_years_from_logs | _years_from_contracts)

# ── 대시보드 ──────────────────────────────────────────────────────────────
if menu == "📊 대시보드":
    st.title("📊 대시보드")

    year_options = ["전체"] + [str(y) for y in ALL_YEARS]
    current_year_str = str(now_kst().year)
    default_year_idx = year_options.index(current_year_str) if current_year_str in year_options else 0

    dc1, _ = st.columns([1, 4])
    top_year_sel = dc1.selectbox(
        "연도 (계약기간이 겹치는 공사만 집계)", year_options, index=default_year_idx, key="top_year"
    )
    top_year = int(top_year_sel) if top_year_sel != "전체" else None
    st.caption("계약 시작일부터 현재까지 누적 기준 (재료비 + 노무비 + 경비 vs 총계약금액)" + (f" · {top_year_sel}년과 계약기간이 겹치는 공사만 집계됨" if top_year else ""))

    active_projects = [p for p in PROJECTS if project_active_in_year(mdf, p["공사명"], top_year)]
    total_revenue = sum(get_contract_total(mdf, p["공사명"]) for p in active_projects)
    total_cost = 0
    for p in active_projects:
        since = get_contract_start(mdf, p["공사명"])
        total_cost += cost_breakdown(rdf, p["공사명"], since, year=top_year)["합계"]
    total_margin, total_rate = profit(total_revenue, total_cost)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총계약금액", fmt_won(total_revenue))
    c2.metric("누적 원가", fmt_won(total_cost))
    c3.metric("이윤", fmt_won(total_margin))
    c4.metric("이윤율", fmt_pct(total_rate))

    if total_revenue == 0:
        st.warning("현장마스터에 '총계약금액'이 입력되지 않아 이윤율을 계산할 수 없습니다.")

    st.divider()
    st.subheader("공사별 이윤 현황")
    st.caption(f"연도: {top_year_sel} (상단 드롭다운과 연동) · 회색 배경 = 현장마스터에 완료여부 O로 표시된 공사")
    table_year = top_year
    year_filtered_names = sorted(p["공사명"] for p in PROJECTS if project_active_in_year(mdf, p["공사명"], table_year))
    search_proj = st.selectbox(
        "공사명 검색 (입력하면 목록이 좁혀짐)", ["전체"] + year_filtered_names, key=f"table_search_{top_year_sel}"
    )

    proj_rows = []
    done_flags = []
    any_unclassified = False
    excluded_count = 0
    today = pd.Timestamp(now_kst().date())
    for p in PROJECTS:
        pname = p["공사명"]
        if search_proj != "전체" and pname != search_proj:
            continue
        if not project_active_in_year(mdf, pname, table_year):
            excluded_count += 1
            continue
        revenue = get_contract_total(mdf, pname)
        since = get_contract_start(mdf, pname)
        cb = cost_breakdown(rdf, pname, since, year=table_year)
        margin, rate = profit(revenue, cb["합계"])
        progress = project_progress_pct(mdf, pname, today)
        if cb["미분류"] > 0:
            any_unclassified = True
        proj_rows.append({
            "공사명": pname,
            "총계약금액": fmt_won(revenue),
            "재료비": fmt_won(cb["재료비"]),
            "노무비": fmt_won(cb["노무비"]),
            "경비": fmt_won(cb["경비"]),
            "미분류": fmt_won(cb["미분류"]) + (" ⚠️" if cb["미분류"] > 0 else ""),
            "누적원가": fmt_won(cb["합계"]),
            "이윤": fmt_won(margin),
            "이윤율": fmt_pct(rate),
            "공정률": fmt_pct(round(progress, 1)) if progress is not None else "N/A",
        })
        done_flags.append(bool(p.get("완료여부", False)))
    if proj_rows:
        df_proj = pd.DataFrame(proj_rows)
        if not any_unclassified:
            df_proj = df_proj.drop(columns=["미분류"])
        df_proj.index = range(1, len(df_proj) + 1)  # 연번 1부터 시작

        def _highlight_done(row):
            color = "background-color: #e5e5e5" if done_flags[row.name - 1] else ""
            return [color] * len(row)

        styled = df_proj.style.apply(_highlight_done, axis=1)
        _col_profit = df_proj.columns.get_loc("이윤")
        _col_rate = df_proj.columns.get_loc("이윤율")
        _table_styles = [
            {"selector": "th", "props": [("font-weight", "bold")]},
            {"selector": f"th.col_heading.col{_col_profit}", "props": [("background-color", "skyblue"), ("font-weight", "bold")]},
            {"selector": f"th.col_heading.col{_col_rate}", "props": [("background-color", "skyblue"), ("font-weight", "bold")]},
        ]
        # 총계약금액/누적원가 폭 통일
        for colname in ["총계약금액", "누적원가"]:
            if colname in df_proj.columns:
                idx = df_proj.columns.get_loc(colname)
                _table_styles.append({"selector": f".col{idx}", "props": [("width", "110px")]})
        # 재료비/노무비/경비/이윤/이윤율 폭 통일
        for colname in ["재료비", "노무비", "경비", "이윤", "이윤율"]:
            if colname in df_proj.columns:
                idx = df_proj.columns.get_loc(colname)
                _table_styles.append({"selector": f".col{idx}", "props": [("width", "85px")]})
        styled = styled.set_table_styles(_table_styles, overwrite=False)
        styled = styled.set_properties(
            subset=["공사명"], **{"text-align": "left", "width": "480px", "white-space": "nowrap"}
        )
        st.table(styled)
        if excluded_count > 0:
            st.caption(f"계약기간이 {top_year_sel}년과 겹치지 않는 공사 {excluded_count}건은 표에서 제외됐습니다.")
    else:
        if excluded_count > 0:
            st.info(f"{top_year_sel}년에 계약기간이 겹치는 공사가 없습니다 (전체 {excluded_count}건 모두 제외됨).")
        else:
            st.info("표시할 공사가 없습니다.")

    st.divider()
    st.subheader("연도-월-공사별 원가 조회")
    st.caption("이윤율은 계약금액 전체 기준 누적으로만 의미가 있어 여기서는 원가(재료비/노무비/경비/미분류)만 조회합니다.")
    done_all = rdf[rdf["상태"] == "완료"] if "상태" in rdf.columns else pd.DataFrame()

    fc1, fc2 = st.columns(2)
    year_sel = fc1.selectbox("연도", ["전체"] + [str(y) for y in ALL_YEARS], key="q_year")
    month_sel = fc2.selectbox("월", ["전체"] + [f"{m}월" for m in range(1, 13)], key="q_month")
    proj_sel_list = st.multiselect(
        "공사명 (입력해서 검색, 비교하고 싶은 것만 선택)",
        project_names,
        default=[],
        key="q_project_multi",
    )

    q = done_all.copy()
    if year_sel != "전체" and "날짜_dt" in q.columns:
        q = q[q["날짜_dt"].dt.year == int(year_sel)]
    if month_sel != "전체" and "날짜_dt" in q.columns:
        q = q[q["날짜_dt"].dt.month == int(month_sel.replace("월", ""))]
    if "공사명" in q.columns:
        q = q[q["공사명"].isin(proj_sel_list)]

    qcb = cost_breakdown_from_df(q)
    if qcb["미분류"] > 0:
        qc1, qc2, qc3, qc4, qc5 = st.columns(5)
        qc4.metric("미분류", fmt_won(qcb["미분류"]))
        qc5.metric("합계", fmt_won(qcb["합계"]))
    else:
        qc1, qc2, qc3, qc5 = st.columns(4)
        qc5.metric("합계", fmt_won(qcb["합계"]))
    qc1.metric("재료비", fmt_won(qcb["재료비"]))
    qc2.metric("노무비", fmt_won(qcb["노무비"]))
    qc3.metric("경비", fmt_won(qcb["경비"]))

    if "공사명" in q.columns and not q.empty:
        st.bar_chart(q.groupby("공사명")["금액"].sum(), horizontal=True)
    elif not proj_sel_list:
        st.info("비교할 공사를 하나 이상 선택하세요.")

# ── 작업 내역 ─────────────────────────────────────────────────────────────
elif menu == "📋 작업 내역":
    st.title("📋 작업 내역")

    c1, c2, c3, c4 = st.columns(4)
    proj_filter = c1.selectbox("공사명", ["전체"] + project_names)
    status_filter = c2.selectbox("상태", ["전체", "완료", "예정"])
    cat_filter = c3.selectbox("카테고리", ["전체"] + COST_CATEGORIES)
    search = c4.text_input("작업명 검색")

    filtered = rdf.copy()
    if proj_filter != "전체" and "공사명" in filtered.columns:
        filtered = filtered[filtered["공사명"] == proj_filter]
    if status_filter != "전체" and "상태" in filtered.columns:
        filtered = filtered[filtered["상태"] == status_filter]
    if cat_filter != "전체" and "카테고리" in filtered.columns:
        filtered = filtered[filtered["카테고리"] == cat_filter]
    if search and "작업항목" in filtered.columns:
        filtered = filtered[filtered["작업항목"].str.contains(search, na=False)]

    total = filtered["금액"].sum() if "금액" in filtered.columns else 0
    st.caption(f"총 {len(filtered)}건 · 합계: **{fmt_won(total)}**")

    show = [c for c in ["날짜", "공사명", "카테고리", "작업항목", "금액", "상태", "비고"] if c in filtered.columns]
    display_df = filtered[show].sort_values("날짜", ascending=False).reset_index(drop=True) if "날짜" in show else filtered[show].reset_index(drop=True)
    if "금액" in display_df.columns:
        display_df["금액"] = display_df["금액"].apply(fmt_won)
    st.dataframe(display_df, use_container_width=True, height=500)

# ── 보고서 ────────────────────────────────────────────────────────────────
elif menu == "📄 보고서":
    st.title("📄 보고서")

    c1, c2 = st.columns(2)
    rep_proj = c1.selectbox("공사명", ["전체"] + project_names)
    use_range = c2.checkbox("기간 직접 지정 (미체크 시 계약 시작일부터 누적)")

    date_from, date_to = None, None
    if use_range:
        dc1, dc2 = st.columns(2)
        date_from = pd.Timestamp(dc1.date_input("시작일"))
        date_to = pd.Timestamp(dc2.date_input("종료일"))

    target_projects = project_names if rep_proj == "전체" else [rep_proj]

    revenue = sum(get_contract_total(mdf, p) for p in target_projects)
    cost_total = {"재료비": 0, "노무비": 0, "경비": 0, "미분류": 0, "합계": 0}
    for p in target_projects:
        since = get_contract_start(mdf, p) if not use_range else date_from
        if use_range and date_to is not None:
            df_tmp = rdf[(rdf["공사명"] == p) & (rdf["상태"] == "완료")]
            if "날짜_dt" in df_tmp.columns:
                df_tmp = df_tmp[(df_tmp["날짜_dt"] >= date_from) & (df_tmp["날짜_dt"] <= date_to)]
            cb = cost_breakdown_from_df(df_tmp)
        else:
            cb = cost_breakdown(rdf, p, since)
        for k in cost_total:
            cost_total[k] += cb[k]
    cb = cost_total

    margin, rate = profit(revenue, cb["합계"])

    breakdown_text = f"재료비 {fmt_won(cb['재료비'])} · 노무비 {fmt_won(cb['노무비'])} · 경비 {fmt_won(cb['경비'])}" + (f" · 미분류 {fmt_won(cb['미분류'])}⚠️" if cb["미분류"] > 0 else "")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총계약금액", fmt_won(revenue))
    c2.metric("누적 원가", fmt_won(cb["합계"]), help=breakdown_text)
    c3.metric("이윤", fmt_won(margin))
    c4.metric("이윤율", fmt_pct(rate))

    st.divider()
    st.subheader("작업 상세 내역")
    recs = rdf.copy()
    if "상태" in recs.columns:
        recs = recs[recs["상태"] == "완료"]
    if rep_proj != "전체" and "공사명" in recs.columns:
        recs = recs[recs["공사명"] == rep_proj]
    if use_range and date_from is not None and date_to is not None and "날짜_dt" in recs.columns:
        recs = recs[(recs["날짜_dt"] >= date_from) & (recs["날짜_dt"] <= date_to)]

    show = [c for c in ["날짜", "공사명", "카테고리", "작업항목", "금액"] if c in recs.columns]
    rep_display = recs[show].reset_index(drop=True)
    if "금액" in rep_display.columns:
        rep_display["금액"] = rep_display["금액"].apply(fmt_won)
    st.dataframe(rep_display, use_container_width=True)
