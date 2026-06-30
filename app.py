import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="SaveTree ERP", page_icon="🌳", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebar"]{background-color:#1a3a2a}
[data-testid="stSidebar"] p,[data-testid="stSidebar"] span,[data-testid="stSidebar"] label{color:#95d5b2!important}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#fff!important}
div[data-testid="metric-container"]{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 6px rgba(0,0,0,0.08)}
</style>
""", unsafe_allow_html=True)

SCOPES = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]

# 현장 목록 — 새 현장 추가 시 여기에 추가
SITE_SHEETS = [
    {"id": "SC001", "name": "스프링카운티자이", "sheet": "스프링카운티자이"},
    {"id": "DS001", "name": "다산유승한내들",   "sheet": "다산유승한내들"},
]

@st.cache_resource
def get_client():
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    return gspread.authorize(creds)

def clean_number(val):
    try:
        return float(str(val).replace(",","").replace(" ","") or 0)
    except:
        return 0

@st.cache_data(ttl=60)
def load_data():
    client = get_client()
    sh = client.open_by_key(st.secrets["spreadsheet_id"])

    def read_ws(name):
        vals = sh.worksheet(name).get_all_values()
        if not vals or len(vals) < 2: return pd.DataFrame()
        headers = [h.strip() or f"col{i}" for i,h in enumerate(vals[0])]
        df = pd.DataFrame(vals[1:], columns=headers)
        return df

    # 현장마스터
    mdf = read_ws("현장마스터")
    mcol_map = {}
    for c in mdf.columns:
        cl = c.lower().replace(" ","")
        if "현장id" in cl: mcol_map[c] = "현장ID"
        elif "현장명" in cl: mcol_map[c] = "현장명"
        elif "센터장" in cl or "담당" in cl: mcol_map[c] = "담당센터장"
        elif "면적" in cl: mcol_map[c] = "면적"
        elif "예산" in cl: mcol_map[c] = "월예산"
        elif "시작" in cl: mcol_map[c] = "계약시작"
        elif "종료" in cl: mcol_map[c] = "계약종료"
        elif "유형" in cl: mcol_map[c] = "유형"
    mdf = mdf.rename(columns=mcol_map)
    if "월예산" in mdf.columns:
        mdf["월예산"] = mdf["월예산"].apply(clean_number)
    if "면적" in mdf.columns:
        mdf["면적"] = mdf["면적"].apply(clean_number)
    mdf = mdf[mdf.get("현장ID", pd.Series(dtype=str)).astype(str).str.strip() != ""]

    # 현장별 시트 읽기
    frames = []
    for site in SITE_SHEETS:
        try:
            df = read_ws(site["sheet"])
            # 컬럼명 표준화
            col_map = {}
            for c in df.columns:
                cl = c.lower().replace(" ","")
                if "날짜" in cl: col_map[c] = "날짜"
                elif "주차" in cl: col_map[c] = "주차"
                elif "카테고리" in cl: col_map[c] = "카테고리"
                elif "작업" in cl and "항목" in cl: col_map[c] = "작업항목"
                elif "규격" in cl or ("단위" in cl and "단가" not in cl): col_map[c] = "규격단위"
                elif "수량" in cl: col_map[c] = "수량"
                elif "단가" in cl: col_map[c] = "단가"
                elif "금액" in cl: col_map[c] = "금액"
                elif "상태" in cl: col_map[c] = "상태"
                elif "비고" in cl: col_map[c] = "비고"
            df = df.rename(columns=col_map)
            df["현장ID"] = site["id"]
            df["현장명"] = site["name"]
            frames.append(df)
        except Exception as e:
            st.warning(f"{site['name']} 시트 오류: {e}")

    rdf = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # 숫자 변환
    for col in ["수량","단가","금액"]:
        if col in rdf.columns:
            rdf[col] = rdf[col].apply(clean_number)

    # 금액 재계산
    if "수량" in rdf.columns and "단가" in rdf.columns:
        rdf["금액"] = rdf["수량"] * rdf["단가"]

    # 빈 행 제거 (작업항목 없는 행)
    if "작업항목" in rdf.columns:
        rdf = rdf[rdf["작업항목"].astype(str).str.strip().isin(["","0"]) == False]

    return mdf, rdf

def fmt_won(n):
    return f"{int(n):,}원"

def pct(a, b):
    return round(a/b*100) if b else 0

def get_budget(mdf, sid):
    row = mdf[mdf["현장ID"]==sid] if "현장ID" in mdf.columns else pd.DataFrame()
    if row.empty or "월예산" not in row.columns: return 0
    return float(row["월예산"].values[0])

def get_total_budget(mdf):
    return mdf["월예산"].sum() if "월예산" in mdf.columns else 0

# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🌳 SaveTree")
    st.markdown("##### 현장 원가 관리 시스템")
    st.divider()
    menu = st.radio("메뉴", ["📊 대시보드","📋 작업 내역","📍 현장 현황","📄 보고서"], label_visibility="hidden")
    st.divider()
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"업데이트: {datetime.now().strftime('%H:%M:%S')}")

# ── 데이터 로드 ───────────────────────────────────────────────────────────
try:
    mdf, rdf = load_data()
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

SITES = mdf.to_dict("records") if not mdf.empty else []
site_names = list(rdf["현장명"].dropna().unique()) if "현장명" in rdf.columns else []

# ── 대시보드 ──────────────────────────────────────────────────────────────
if menu == "📊 대시보드":
    st.title("📊 대시보드")

    c1, _ = st.columns([1,5])
    month = c1.text_input("기준월", value="2026-06")

    act = rdf.copy()
    if "상태" in act.columns: act = act[act["상태"]=="실적"]
    if "날짜" in act.columns: act = act[act["날짜"].astype(str).str.startswith(month)]

    total_act = act["금액"].sum() if "금액" in act.columns else 0
    total_budget = get_total_budget(mdf)
    upcoming = rdf[rdf["상태"]=="예정"].sort_values("날짜") if "상태" in rdf.columns and "날짜" in rdf.columns else pd.DataFrame()

    c1,c2,c3 = st.columns(3)
    c1.metric("이번달 실투입 원가", fmt_won(total_act), f"예산 {fmt_won(total_budget)}")
    c2.metric("전체 집행률", f"{pct(total_act,total_budget)}%", f"잔여 {fmt_won(total_budget-total_act)}")
    c3.metric("예정 작업", f"{len(upcoming)}건", "향후 30일")

    st.divider()
    st.subheader("현장별 집행 현황")
    cols = st.columns(max(len(SITES),1))
    for i, site in enumerate(SITES):
        sid = site.get("현장ID","")
        sname = site.get("현장명","")
        budget = get_budget(mdf, sid)
        site_act = act[act["현장ID"]==sid]["금액"].sum() if "현장ID" in act.columns else 0
        rate = pct(site_act, budget)
        with cols[i]:
            st.markdown(f"**{sname}**")
            st.progress(min(rate/100,1.0))
            a,b = st.columns(2)
            a.metric("실적", fmt_won(site_act))
            b.metric("집행률", f"{rate}%")

    st.divider()
    cl,cr = st.columns([1.7,1])
    with cl:
        st.subheader("주별 원가 추이")
        if "주차" in act.columns and "현장명" in act.columns and not act.empty:
            pivot = act.groupby(["주차","현장명"])["금액"].sum().reset_index().pivot(index="주차",columns="현장명",values="금액").fillna(0)
            st.bar_chart(pivot)
    with cr:
        st.subheader("항목별 원가 구성")
        if "카테고리" in act.columns and not act.empty:
            st.bar_chart(act.groupby("카테고리")["금액"].sum())

    st.divider()
    st.subheader("예정 작업 (다음 30일)")
    if not upcoming.empty:
        cols3 = st.columns(3)
        for i,(_, row) in enumerate(upcoming.head(6).iterrows()):
            with cols3[i%3]:
                with st.container(border=True):
                    st.caption(f"📅 {row.get('날짜','')} · {row.get('주차','')}주차")
                    st.markdown(f"**{row.get('작업항목','')}**")
                    a,b = st.columns(2)
                    a.caption(row.get("카테고리",""))
                    b.markdown(f"**{fmt_won(row.get('금액',0))}**")
    else:
        st.info("예정 작업이 없습니다.")

# ── 작업 내역 ─────────────────────────────────────────────────────────────
elif menu == "📋 작업 내역":
    st.title("📋 작업 내역")

    c1,c2,c3,c4 = st.columns(4)
    site_filter   = c1.selectbox("현장", ["전체"]+site_names)
    status_filter = c2.selectbox("상태", ["전체","실적","예정"])
    cat_filter    = c3.selectbox("카테고리", ["전체","작업비","자재비","인건비","경비"])
    search        = c4.text_input("작업명 검색")

    filtered = rdf.copy()
    if site_filter!="전체" and "현장명" in filtered.columns:
        filtered = filtered[filtered["현장명"]==site_filter]
    if status_filter!="전체" and "상태" in filtered.columns:
        filtered = filtered[filtered["상태"]==status_filter]
    if cat_filter!="전체" and "카테고리" in filtered.columns:
        filtered = filtered[filtered["카테고리"]==cat_filter]
    if search and "작업항목" in filtered.columns:
        filtered = filtered[filtered["작업항목"].str.contains(search,na=False)]

    total = filtered["금액"].sum() if "금액" in filtered.columns else 0
    st.caption(f"총 {len(filtered)}건 · 합계: **{fmt_won(total)}**")

    show = [c for c in ["날짜","주차","현장명","카테고리","작업항목","규격단위","수량","단가","금액","상태","비고"] if c in filtered.columns]
    st.dataframe(
        filtered[show].reset_index(drop=True),
        use_container_width=True, height=500,
        column_config={
            "금액": st.column_config.NumberColumn("금액",format="%d원"),
            "단가": st.column_config.NumberColumn("단가",format="%d원"),
        }
    )

# ── 현장 현황 ─────────────────────────────────────────────────────────────
elif menu == "📍 현장 현황":
    st.title("📍 현장 현황")

    for site in SITES:
        sid   = site.get("현장ID","")
        sname = site.get("현장명","")
        budget= get_budget(mdf, sid)
        srecs = rdf[(rdf["현장ID"]==sid)&(rdf["상태"]=="실적")] if "현장ID" in rdf.columns and "상태" in rdf.columns else pd.DataFrame()
        actual= srecs["금액"].sum() if "금액" in srecs.columns else 0
        rate  = pct(actual, budget)

        with st.expander(f"🌳 {sname} — 집행률 {rate}%", expanded=True):
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("담당 센터장", site.get("담당센터장",""))
            c2.metric("현장 면적", f"{int(site.get('면적',0)):,}㎡")
            c3.metric("실적 합계", fmt_won(actual))
            c4.metric("월 예산", fmt_won(budget))
            st.progress(min(rate/100,1.0), text=f"집행률 {rate}%")
            if "카테고리" in srecs.columns and not srecs.empty:
                st.bar_chart(srecs.groupby("카테고리")["금액"].sum())

# ── 보고서 ────────────────────────────────────────────────────────────────
elif menu == "📄 보고서":
    st.title("📄 보고서")

    c1,c2 = st.columns(2)
    rep_site  = c1.selectbox("현장", ["전체"]+site_names)
    rep_month = c2.text_input("기준월 (YYYY-MM)", value="2026-06")

    recs = rdf.copy()
    if "날짜" in recs.columns: recs = recs[recs["날짜"].astype(str).str.startswith(rep_month)]
    if "상태" in recs.columns: recs = recs[recs["상태"]=="실적"]
    if rep_site!="전체" and "현장명" in recs.columns: recs = recs[recs["현장명"]==rep_site]

    total  = recs["금액"].sum() if "금액" in recs.columns else 0
    budget = get_total_budget(mdf) if rep_site=="전체" else get_budget(mdf, mdf[mdf.get("현장명",pd.Series())==rep_site]["현장ID"].values[0] if "현장명" in mdf.columns and not mdf[mdf.get("현장명",pd.Series())==rep_site].empty else "")

    c1,c2,c3 = st.columns(3)
    c1.metric("총 집행 원가", fmt_won(total), f"예산 {fmt_won(budget)}")
    c2.metric("집행률", f"{pct(total,budget)}%", f"잔여 {fmt_won(budget-total)}")
    c3.metric("작업 건수", f"{len(recs)}건")

    st.divider()
    cl,cr = st.columns(2)
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
    show = [c for c in ["날짜","현장명","카테고리","작업항목","수량","단가","금액"] if c in recs.columns]
    st.dataframe(
        recs[show].reset_index(drop=True),
        use_container_width=True,
        column_config={
            "금액": st.column_config.NumberColumn("금액",format="%d원"),
            "단가": st.column_config.NumberColumn("단가",format="%d원"),
        }
    )
    st.caption(f"합계: **{fmt_won(total)}**")
