import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime, timedelta

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

# 원가 3요소. 작업내역 시트의 '카테고리' 값은 이 세 가지 중 하나여야 함 (분류는 시트 작성자 책임).
COST_CATEGORIES = ["재료비", "노무비", "경비"]


@st.cache_resource
def get_client():
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    return gspread.authorize(creds)


def clean_number(val):
    """숫자 문자열(콤마, 공백, 원, ㎡ 등 단위 포함)을 float으로 변환. 실패 시 None을 반환해 호출부에서 감지 가능하게 함."""
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

    # ── 현장마스터 ──────────────────────────────────────────
    mdf = read_ws(MASTER_SHEET)
    mcol_map = {}
    for c in mdf.columns:
        cl = c.lower().replace(" ", "")
        if "현장명" in cl:
            mcol_map[c] = "현장명"
        elif "센터장" in cl or "담당" in cl:
            mcol_map[c] = "담당센터장"
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

    if "현장명" in mdf.columns:
        mdf = mdf[mdf["현장명"].astype(str).str.strip() != ""]
    else:
        warnings.append(f"'{MASTER_SHEET}' 시트에 현장명 컬럼을 찾지 못했습니다.")
        mdf = pd.DataFrame(columns=["현장명"])

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
            mdf[datecol + "_dt"] = pd.to_datetime(mdf[datecol], errors="coerce")

    # ── 작업내역 (통합 시트, 현장명으로 구분) ──────────────────
    rdf = read_ws(LOG_SHEET)
    rcol_map = {}
    for c in rdf.columns:
        cl = c.lower().replace(" ", "")
        if "현장명" in cl:
            rcol_map[c] = "현장명"
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

    if "금액" in rdf.columns:
        parsed = rdf["금액"].apply(clean_number)
        bad = rdf.loc[parsed.isna() & rdf["금액"].astype(str).str.strip().ne(""), "금액"]
        if len(bad) > 0:
            warnings.append(f"작업내역의 '금액' 값 중 숫자로 변환 못한 값 {len(bad)}건 (0으로 처리됨): {list(bad.unique())[:3]}")
        rdf["금액"] = parsed.fillna(0)
    else:
        rdf["금액"] = 0

    if "날짜" in rdf.columns:
        rdf["날짜_dt"] = pd.to_datetime(rdf["날짜"], errors="coerce")
        bad_dates = rdf["날짜"].astype(str).str.strip().ne("") & rdf["날짜_dt"].isna()
        if bad_dates.sum() > 0:
            warnings.append(f"작업내역의 '날짜' 값 중 인식 못한 값 {int(bad_dates.sum())}건이 있습니다.")
        rdf["날짜"] = rdf["날짜_dt"].dt.strftime("%Y-%m-%d").fillna(rdf["날짜"])

        def week_label(d):
            if pd.isna(d):
                return ""
            return f"{d.strftime('%Y-%m')} {((d.day - 1) // 7) + 1}주"

        rdf["주차"] = rdf["날짜_dt"].apply(week_label)
    else:
        rdf["날짜_dt"] = pd.NaT
        rdf["주차"] = ""

    if "현장명" in rdf.columns and "현장명" in mdf.columns:
        unknown_sites = set(rdf["현장명"].dropna().unique()) - set(mdf["현장명"].dropna().unique())
        unknown_sites.discard("")
        if unknown_sites:
            unknown_amt = rdf[rdf["현장명"].isin(unknown_sites)]["금액"].sum()
            warnings.append(
                f"현장마스터에 없는 현장명이 작업내역에 있습니다: {sorted(unknown_sites)} "
                f"— 총 {unknown_amt:,.0f}원이 모든 집계(대시보드/현장현황/보고서)에서 제외됩니다."
            )

    # 상태가 완료/예정이 아닌(빈 값 포함) 행 — 어디 집계에도 안 잡히고 유실됨
    if "상태" in rdf.columns:
        bad_status_mask = ~rdf["상태"].isin(["완료", "예정"])
        if bad_status_mask.sum() > 0:
            bad_status_amt = rdf.loc[bad_status_mask, "금액"].sum()
            warnings.append(
                f"'상태'가 완료/예정 중 하나도 아닌 행 {int(bad_status_mask.sum())}건 "
                f"(금액 합계 {bad_status_amt:,.0f}원) — 완료/예정 어느 집계에도 안 잡힙니다."
            )

    # 카테고리가 재료비/노무비/경비 중 하나가 아닌(빈 값 포함) '완료' 행 — 원가 집계에서 누락되어 이윤이 과대 계산됨
    if "카테고리" in rdf.columns and "상태" in rdf.columns:
        done = rdf[rdf["상태"] == "완료"]
        bad_cat_mask = ~done["카테고리"].isin(COST_CATEGORIES)
        if bad_cat_mask.sum() > 0:
            bad_cat_amt = done.loc[bad_cat_mask, "금액"].sum()
            bad_cat_vals = sorted(set(done.loc[bad_cat_mask, "카테고리"].replace("", "(비어있음)").unique()))
            warnings.append(
                f"완료 상태인데 '카테고리'가 재료비/노무비/경비가 아닌 행 {int(bad_cat_mask.sum())}건 "
                f"(금액 합계 {bad_cat_amt:,.0f}원, 값: {bad_cat_vals}) — 원가 집계에서 빠져 이윤이 실제보다 높게 나옵니다."
            )

    return mdf, rdf, warnings


def fmt_won(n):
    return f"{float(n) / 1000:,.1f}천원"


def fmt_pct(n):
    return f"{n:.1f}%" if n is not None else "N/A"


def get_contract_total(mdf, site_name):
    row = mdf[mdf["현장명"] == site_name] if "현장명" in mdf.columns else pd.DataFrame()
    if row.empty or "총계약금액" not in row.columns:
        return 0
    return float(row["총계약금액"].values[0])


def get_contract_start(mdf, site_name):
    row = mdf[mdf["현장명"] == site_name] if "현장명" in mdf.columns else pd.DataFrame()
    if row.empty or "계약시작_dt" not in row.columns:
        return None
    val = row["계약시작_dt"].values[0]
    return pd.Timestamp(val) if pd.notna(val) else None


def cost_breakdown(rdf, site_name=None, since=None):
    """상태=완료 기준, since 이후(계약시작일 이후) 누적 원가를 재료비/노무비/경비(+미분류)로 분해.
    카테고리가 셋 중 하나가 아닌 완료 건도 '미분류'로 합계에 포함시켜, 분류 누락 때문에
    원가가 축소되고 이윤이 부풀려지는 일이 없도록 한다."""
    df = rdf.copy()
    if "상태" in df.columns:
        df = df[df["상태"] == "완료"]
    if site_name is not None and "현장명" in df.columns:
        df = df[df["현장명"] == site_name]
    if since is not None and "날짜_dt" in df.columns:
        df = df[df["날짜_dt"] >= since]
    result = {}
    for cat in COST_CATEGORIES:
        result[cat] = df[df["카테고리"] == cat]["금액"].sum() if "카테고리" in df.columns else 0
    if "카테고리" in df.columns:
        result["미분류"] = df[~df["카테고리"].isin(COST_CATEGORIES)]["금액"].sum()
    else:
        result["미분류"] = df["금액"].sum()
    result["합계"] = result["재료비"] + result["노무비"] + result["경비"] + result["미분류"]
    return result


def cost_breakdown_from_df(df):
    """이미 필터링된 df(완료 상태, 현장/기간 필터 적용됨)에서 재료비/노무비/경비/미분류/합계 계산."""
    result = {}
    for cat in COST_CATEGORIES:
        result[cat] = df[df["카테고리"] == cat]["금액"].sum() if "카테고리" in df.columns else 0
    result["미분류"] = df[~df["카테고리"].isin(COST_CATEGORIES)]["금액"].sum() if "카테고리" in df.columns else df["금액"].sum()
    result["합계"] = result["재료비"] + result["노무비"] + result["경비"] + result["미분류"]
    return result


def profit(revenue, cost):
    margin = revenue - cost
    rate = round(margin / revenue * 100, 1) if revenue else None
    return margin, rate


# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🌳 SaveTree")
    st.markdown("##### 현장 원가 관리 시스템")
    st.divider()
    menu = st.radio("메뉴", ["📊 대시보드", "📋 작업 내역", "📍 현장 현황", "📄 보고서"], label_visibility="hidden")
    st.divider()
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"업데이트: {datetime.now().strftime('%H:%M:%S')}")

# ── 데이터 로드 ───────────────────────────────────────────────────────────
try:
    mdf, rdf, load_warnings = load_data()
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

for w in load_warnings:
    st.sidebar.warning(w)

SITES = mdf.to_dict("records") if not mdf.empty else []
site_names = list(mdf["현장명"].dropna().unique()) if "현장명" in mdf.columns else []

# ── 대시보드 ──────────────────────────────────────────────────────────────
if menu == "📊 대시보드":
    st.title("📊 대시보드")
    st.caption("계약 시작일부터 현재까지 누적 기준 (재료비 + 노무비 + 경비 vs 총계약금액)")

    total_revenue = mdf["총계약금액"].sum() if "총계약금액" in mdf.columns else 0
    total_cost = 0
    for sname in site_names:
        since = get_contract_start(mdf, sname)
        total_cost += cost_breakdown(rdf, sname, since)["합계"]
    total_margin, total_rate = profit(total_revenue, total_cost)

    today = pd.Timestamp(datetime.now().date())
    if "상태" in rdf.columns and "날짜_dt" in rdf.columns:
        upcoming = rdf[
            (rdf["상태"] == "예정")
            & (rdf["날짜_dt"] >= today)
            & (rdf["날짜_dt"] <= today + timedelta(days=30))
        ].sort_values("날짜_dt")
    else:
        upcoming = pd.DataFrame()

    c1, c2, c3 = st.columns(3)
    c1.metric("총계약금액", fmt_won(total_revenue))
    c2.metric("누적 원가", fmt_won(total_cost), f"이윤 {fmt_won(total_margin)} ({fmt_pct(total_rate)})")
    c3.metric("예정 작업", f"{len(upcoming)}건", "향후 30일")

    if total_revenue == 0:
        st.warning("현장마스터에 '총계약금액'이 입력되지 않아 이윤율을 계산할 수 없습니다.")

    st.divider()
    st.subheader("현장별 이윤 현황")
    cols = st.columns(max(len(SITES), 1))
    for i, site in enumerate(SITES):
        sname = site.get("현장명", "")
        revenue = get_contract_total(mdf, sname)
        since = get_contract_start(mdf, sname)
        cb = cost_breakdown(rdf, sname, since)
        margin, rate = profit(revenue, cb["합계"])
        with cols[i]:
            st.markdown(f"**{sname}**")
            if revenue > 0:
                st.progress(min(cb["합계"] / revenue, 1.0))
            a, b = st.columns(2)
            a.metric("누적원가", fmt_won(cb["합계"]))
            b.metric("이윤율", fmt_pct(rate))
            st.caption(f"이윤 {fmt_won(margin)} · 재료비 {fmt_won(cb['재료비'])} · 노무비 {fmt_won(cb['노무비'])} · 경비 {fmt_won(cb['경비'])}" + (f" · 미분류 {fmt_won(cb['미분류'])}⚠️" if cb["미분류"] > 0 else ""))

    st.divider()
    cl, cr = st.columns([1.7, 1])
    with cl:
        st.subheader("주별 원가 추이")
        act = rdf[rdf["상태"] == "완료"] if "상태" in rdf.columns else rdf
        if "주차" in act.columns and "현장명" in act.columns and not act.empty:
            pivot = act.groupby(["주차", "현장명"])["금액"].sum().reset_index().pivot(
                index="주차", columns="현장명", values="금액"
            ).fillna(0)
            st.bar_chart(pivot)
        else:
            st.caption("표시할 완료 데이터가 없습니다.")
    with cr:
        st.subheader("원가 3요소 구성 (+ 미분류)")
        act = rdf[rdf["상태"] == "완료"] if "상태" in rdf.columns else rdf
        if "카테고리" in act.columns and not act.empty:
            cat_sum = act.assign(
                카테고리=act["카테고리"].where(act["카테고리"].isin(COST_CATEGORIES), "미분류")
            ).groupby("카테고리")["금액"].sum()
            if not cat_sum.empty:
                st.bar_chart(cat_sum)
            else:
                st.caption("완료 데이터가 없습니다.")

    st.divider()
    st.subheader("예정 작업 (다음 30일)")
    if not upcoming.empty:
        cols3 = st.columns(3)
        for i, (_, row) in enumerate(upcoming.head(6).iterrows()):
            with cols3[i % 3]:
                with st.container(border=True):
                    st.caption(f"📅 {row.get('날짜','')} · {row.get('현장명','')}")
                    st.markdown(f"**{row.get('작업항목','')}**")
                    a, b = st.columns(2)
                    a.caption(row.get("카테고리", ""))
                    b.markdown(f"**{fmt_won(row.get('금액', 0))}**")
    else:
        st.info("예정 작업이 없습니다.")

# ── 작업 내역 ─────────────────────────────────────────────────────────────
elif menu == "📋 작업 내역":
    st.title("📋 작업 내역")

    c1, c2, c3, c4 = st.columns(4)
    site_filter = c1.selectbox("현장", ["전체"] + site_names)
    status_filter = c2.selectbox("상태", ["전체", "완료", "예정"])
    cat_filter = c3.selectbox("카테고리", ["전체"] + COST_CATEGORIES)
    search = c4.text_input("작업명 검색")

    filtered = rdf.copy()
    if site_filter != "전체" and "현장명" in filtered.columns:
        filtered = filtered[filtered["현장명"] == site_filter]
    if status_filter != "전체" and "상태" in filtered.columns:
        filtered = filtered[filtered["상태"] == status_filter]
    if cat_filter != "전체" and "카테고리" in filtered.columns:
        filtered = filtered[filtered["카테고리"] == cat_filter]
    if search and "작업항목" in filtered.columns:
        filtered = filtered[filtered["작업항목"].str.contains(search, na=False)]

    total = filtered["금액"].sum() if "금액" in filtered.columns else 0
    st.caption(f"총 {len(filtered)}건 · 합계: **{fmt_won(total)}**")

    show = [c for c in ["날짜", "현장명", "카테고리", "작업항목", "금액", "상태", "비고"] if c in filtered.columns]
    display_df = filtered[show].sort_values("날짜", ascending=False).reset_index(drop=True) if "날짜" in show else filtered[show].reset_index(drop=True)
    if "금액" in display_df.columns:
        display_df["금액"] = display_df["금액"].apply(fmt_won)
    st.dataframe(display_df, use_container_width=True, height=500)

# ── 현장 현황 ─────────────────────────────────────────────────────────────
elif menu == "📍 현장 현황":
    st.title("📍 현장 현황")

    if not SITES:
        st.info("현장마스터 시트에 등록된 현장이 없습니다.")

    for site in SITES:
        sname = site.get("현장명", "")
        revenue = get_contract_total(mdf, sname)
        since = get_contract_start(mdf, sname)
        cb = cost_breakdown(rdf, sname, since)
        margin, rate = profit(revenue, cb["합계"])

        with st.expander(f"🌳 {sname} — 이윤율 {fmt_pct(rate)}", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("담당 센터장", site.get("담당센터장", ""))
            c2.metric("현장 면적", f"{int(site.get('면적', 0)):,}㎡")
            c3.metric("총계약금액", fmt_won(revenue))
            c4.metric("누적원가", fmt_won(cb["합계"]))

            c5, c6, c7, c8, c9 = st.columns(5)
            c5.metric("재료비", fmt_won(cb["재료비"]))
            c6.metric("노무비", fmt_won(cb["노무비"]))
            c7.metric("경비", fmt_won(cb["경비"]))
            c8.metric("미분류", fmt_won(cb["미분류"]), "재분류 필요" if cb["미분류"] > 0 else None)
            c9.metric("이윤", fmt_won(margin), fmt_pct(rate))

            if revenue > 0:
                st.progress(min(cb["합계"] / revenue, 1.0), text=f"원가율 {fmt_pct(round(cb['합계']/revenue*100,1))}")
            else:
                st.caption("총계약금액이 입력되지 않아 원가율을 표시할 수 없습니다.")

            srecs = rdf[(rdf["현장명"] == sname) & (rdf["상태"] == "완료")] if "현장명" in rdf.columns and "상태" in rdf.columns else pd.DataFrame()
            if "카테고리" in srecs.columns and not srecs.empty:
                cat_sum = srecs.assign(
                    카테고리=srecs["카테고리"].where(srecs["카테고리"].isin(COST_CATEGORIES), "미분류")
                ).groupby("카테고리")["금액"].sum()
                if not cat_sum.empty:
                    st.bar_chart(cat_sum)
            else:
                st.caption("해당 현장의 완료 데이터가 없습니다.")

# ── 보고서 ────────────────────────────────────────────────────────────────
elif menu == "📄 보고서":
    st.title("📄 보고서")

    c1, c2 = st.columns(2)
    rep_site = c1.selectbox("현장", ["전체"] + site_names)
    use_range = c2.checkbox("기간 직접 지정 (미체크 시 계약 시작일부터 누적)")

    date_from, date_to = None, None
    if use_range:
        dc1, dc2 = st.columns(2)
        date_from = pd.Timestamp(dc1.date_input("시작일"))
        date_to = pd.Timestamp(dc2.date_input("종료일"))

    if rep_site == "전체":
        revenue = mdf["총계약금액"].sum() if "총계약금액" in mdf.columns else 0
        cost_total = {"재료비": 0, "노무비": 0, "경비": 0, "미분류": 0, "합계": 0}
        for sname in site_names:
            since = get_contract_start(mdf, sname) if not use_range else date_from
            cb = cost_breakdown(rdf, sname, since)
            if use_range and date_to is not None:
                # 종료일 상한도 적용 (전체 기간 재계산: since~date_to)
                df_tmp = rdf[(rdf["현장명"] == sname) & (rdf["상태"] == "완료")]
                if "날짜_dt" in df_tmp.columns:
                    df_tmp = df_tmp[(df_tmp["날짜_dt"] >= date_from) & (df_tmp["날짜_dt"] <= date_to)]
                cb = cost_breakdown_from_df(df_tmp)
            for k in cost_total:
                cost_total[k] += cb[k]
        cb = cost_total
    else:
        revenue = get_contract_total(mdf, rep_site)
        since = get_contract_start(mdf, rep_site) if not use_range else date_from
        if use_range and date_to is not None:
            df_tmp = rdf[(rdf["현장명"] == rep_site) & (rdf["상태"] == "완료")]
            if "날짜_dt" in df_tmp.columns:
                df_tmp = df_tmp[(df_tmp["날짜_dt"] >= date_from) & (df_tmp["날짜_dt"] <= date_to)]
            cb = cost_breakdown_from_df(df_tmp)
        else:
            cb = cost_breakdown(rdf, rep_site, since)

    margin, rate = profit(revenue, cb["합계"])

    c1, c2, c3 = st.columns(3)
    c1.metric("총계약금액", fmt_won(revenue))
    c2.metric("누적 원가", fmt_won(cb["합계"]), f"재료비 {fmt_won(cb['재료비'])} · 노무비 {fmt_won(cb['노무비'])} · 경비 {fmt_won(cb['경비'])}" + (f" · 미분류 {fmt_won(cb['미분류'])}⚠️" if cb["미분류"] > 0 else ""))
    c3.metric("이윤", fmt_won(margin), fmt_pct(rate))

    st.divider()
    st.subheader("작업 상세 내역")
    recs = rdf.copy()
    if "상태" in recs.columns:
        recs = recs[recs["상태"] == "완료"]
    if rep_site != "전체" and "현장명" in recs.columns:
        recs = recs[recs["현장명"] == rep_site]
    if use_range and date_from is not None and date_to is not None and "날짜_dt" in recs.columns:
        recs = recs[(recs["날짜_dt"] >= date_from) & (recs["날짜_dt"] <= date_to)]

    show = [c for c in ["날짜", "현장명", "카테고리", "작업항목", "금액"] if c in recs.columns]
    rep_display = recs[show].reset_index(drop=True)
    if "금액" in rep_display.columns:
        rep_display["금액"] = rep_display["금액"].apply(fmt_won)
    st.dataframe(rep_display, use_container_width=True)
