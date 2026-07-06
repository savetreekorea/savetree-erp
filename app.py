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

# 실제 구글시트 탭 이름 (현장별 개별 시트가 아니라 통합 시트 2개 구조)
MASTER_SHEET = "현장마스터"
LOG_SHEET = "작업내역"


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
        elif "예산" in cl:
            mcol_map[c] = "월예산"
        elif "시작" in cl:
            mcol_map[c] = "계약시작"
        elif "종료" in cl:
            mcol_map[c] = "계약종료"
        elif "유형" in cl:
            mcol_map[c] = "유형"
    mdf = mdf.rename(columns=mcol_map)

    # 현장명이 비어있는 행 제거 (키 컬럼)
    if "현장명" in mdf.columns:
        mdf = mdf[mdf["현장명"].astype(str).str.strip() != ""]
    else:
        warnings.append(f"'{MASTER_SHEET}' 시트에 현장명 컬럼을 찾지 못했습니다.")
        mdf = pd.DataFrame(columns=["현장명"])

    for numcol in ["월예산", "면적"]:
        if numcol in mdf.columns:
            parsed = mdf[numcol].apply(clean_number)
            bad = mdf.loc[parsed.isna() & mdf[numcol].astype(str).str.strip().ne(""), numcol]
            if len(bad) > 0:
                warnings.append(f"현장마스터의 '{numcol}' 값 중 숫자로 변환 못한 값 {len(bad)}건 (0으로 처리됨): {list(bad.unique())[:3]}")
            mdf[numcol] = parsed.fillna(0)

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

    # 작업항목 없는 빈 행 제거
    if "작업항목" in rdf.columns:
        rdf = rdf[rdf["작업항목"].astype(str).str.strip() != ""]
    else:
        warnings.append(f"'{LOG_SHEET}' 시트에 작업항목 컬럼을 찾지 못했습니다.")

    # 금액: 원본 그대로 사용 (수량×단가 계산 로직 없음 — 실제 시트는 금액 직접 입력 방식)
    if "금액" in rdf.columns:
        parsed = rdf["금액"].apply(clean_number)
        bad = rdf.loc[parsed.isna() & rdf["금액"].astype(str).str.strip().ne(""), "금액"]
        if len(bad) > 0:
            warnings.append(f"작업내역의 '금액' 값 중 숫자로 변환 못한 값 {len(bad)}건 (0으로 처리됨): {list(bad.unique())[:3]}")
        rdf["금액"] = parsed.fillna(0)
    else:
        rdf["금액"] = 0

    # 날짜 파싱 (필터/정렬/주차 계산에 사용)
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

    # 현장마스터에 없는 현장명이 작업내역에 등장하면 알림 (오타 등 데이터 정합성 문제 조기 발견용)
    if "현장명" in rdf.columns and "현장명" in mdf.columns:
        unknown_sites = set(rdf["현장명"].dropna().unique()) - set(mdf["현장명"].dropna().unique())
        unknown_sites.discard("")
        if unknown_sites:
            warnings.append(f"현장마스터에 없는 현장명이 작업내역에 있습니다: {sorted(unknown_sites)}")

    return mdf, rdf, warnings


def fmt_won(n):
    return f"{float(n):,.2f}원"


def pct(a, b):
    return round(a / b * 100) if b else 0


def get_budget(mdf, site_name):
    row = mdf[mdf["현장명"] == site_name] if "현장명" in mdf.columns else pd.DataFrame()
    if row.empty or "월예산" not in row.columns:
        return 0
    return float(row["월예산"].values[0])


def get_total_budget(mdf):
    return mdf["월예산"].sum() if "월예산" in mdf.columns else 0


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

    c1, _ = st.columns([1, 5])
    month = c1.text_input("기준월", value=datetime.now().strftime("%Y-%m"))

    act = rdf.copy()
    if "상태" in act.columns:
        act = act[act["상태"] == "완료"]
    if "날짜" in act.columns:
        act = act[act["날짜"].astype(str).str.startswith(month)]

    total_act = act["금액"].sum() if "금액" in act.columns else 0
    total_budget = get_total_budget(mdf)

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
    c1.metric("이번달 실투입 원가", fmt_won(total_act), f"예산 {fmt_won(total_budget)}")
    c2.metric("전체 집행률", f"{pct(total_act, total_budget)}%", f"잔여 {fmt_won(total_budget - total_act)}")
    c3.metric("예정 작업", f"{len(upcoming)}건", "향후 30일")

    st.divider()
    st.subheader("현장별 집행 현황")
    cols = st.columns(max(len(SITES), 1))
    for i, site in enumerate(SITES):
        sname = site.get("현장명", "")
        budget = get_budget(mdf, sname)
        site_act = act[act["현장명"] == sname]["금액"].sum() if "현장명" in act.columns else 0
        rate = pct(site_act, budget)
        with cols[i]:
            st.markdown(f"**{sname}**")
            st.progress(min(rate / 100, 1.0))
            a, b = st.columns(2)
            a.metric("실적", fmt_won(site_act))
            b.metric("집행률", f"{rate}%")

    st.divider()
    cl, cr = st.columns([1.7, 1])
    with cl:
        st.subheader("주별 원가 추이")
        if "주차" in act.columns and "현장명" in act.columns and not act.empty:
            pivot = act.groupby(["주차", "현장명"])["금액"].sum().reset_index().pivot(
                index="주차", columns="현장명", values="금액"
            ).fillna(0)
            st.bar_chart(pivot)
        else:
            st.caption("표시할 실적 데이터가 없습니다.")
    with cr:
        st.subheader("항목별 원가 구성")
        if "카테고리" in act.columns and not act.empty:
            st.bar_chart(act.groupby("카테고리")["금액"].sum())
        else:
            st.caption("표시할 실적 데이터가 없습니다.")

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
    cat_filter = c3.selectbox("카테고리", ["전체", "작업비", "자재비", "인건비", "경비"])
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
        budget = get_budget(mdf, sname)
        srecs = rdf[(rdf["현장명"] == sname) & (rdf["상태"] == "완료")] if "현장명" in rdf.columns and "상태" in rdf.columns else pd.DataFrame()
        actual = srecs["금액"].sum() if "금액" in srecs.columns else 0
        rate = pct(actual, budget)

        with st.expander(f"🌳 {sname} — 집행률 {rate}%", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("담당 센터장", site.get("담당센터장", ""))
            c2.metric("현장 면적", f"{int(site.get('면적', 0)):,}㎡")
            c3.metric("실적 합계", fmt_won(actual))
            c4.metric("월 예산", fmt_won(budget))
            st.progress(min(rate / 100, 1.0), text=f"집행률 {rate}%")
            if "카테고리" in srecs.columns and not srecs.empty:
                st.bar_chart(srecs.groupby("카테고리")["금액"].sum())
            else:
                st.caption("해당 현장의 실적 데이터가 없습니다.")

# ── 보고서 ────────────────────────────────────────────────────────────────
elif menu == "📄 보고서":
    st.title("📄 보고서")

    c1, c2 = st.columns(2)
    rep_site = c1.selectbox("현장", ["전체"] + site_names)
    rep_month = c2.text_input("기준월 (YYYY-MM)", value=datetime.now().strftime("%Y-%m"))

    recs = rdf.copy()
    if "날짜" in recs.columns:
        recs = recs[recs["날짜"].astype(str).str.startswith(rep_month)]
    if "상태" in recs.columns:
        recs = recs[recs["상태"] == "완료"]
    if rep_site != "전체" and "현장명" in recs.columns:
        recs = recs[recs["현장명"] == rep_site]

    total = recs["금액"].sum() if "금액" in recs.columns else 0
    budget = get_total_budget(mdf) if rep_site == "전체" else get_budget(mdf, rep_site)

    c1, c2, c3 = st.columns(3)
    c1.metric("총 집행 원가", fmt_won(total), f"예산 {fmt_won(budget)}")
    c2.metric("집행률", f"{pct(total, budget)}%", f"잔여 {fmt_won(budget - total)}")
    c3.metric("작업 건수", f"{len(recs)}건")

    st.divider()
    cl, cr = st.columns(2)
    with cl:
        st.subheader("현장별 집행")
        if "현장명" in recs.columns and not recs.empty:
            st.bar_chart(recs.groupby("현장명")["금액"].sum())
    with cr:
        st.subheader("카테고리별 집행")
        if "카테고리" in recs.columns and not recs.empty:
            st.bar_chart(recs.groupby("카테고리")["금액"].sum())

    st.divider()
    st.subheader("작업 상세 내역")
    show = [c for c in ["날짜", "현장명", "카테고리", "작업항목", "금액"] if c in recs.columns]
    rep_display = recs[show].reset_index(drop=True)
    if "금액" in rep_display.columns:
        rep_display["금액"] = rep_display["금액"].apply(fmt_won)
    st.dataframe(rep_display, use_container_width=True)
    st.caption(f"합계: **{fmt_won(total)}**")
