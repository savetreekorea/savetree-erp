
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime
import json

# ── 페이지 설정 ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SaveTree ERP",
    page_icon="🌳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── 스타일 ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
[data-testid="stSidebar"] { background-color: #1a3a2a; }
[data-testid="stSidebar"] * { color: #95d5b2 !important; }
[data-testid="stSidebar"] .st-emotion-cache-1rtdyuf { color: #fff !important; font-weight: 900; font-size: 20px; }
.metric-card { background: #fff; border-radius: 14px; padding: 20px; box-shadow: 0 1px 6px rgba(0,0,0,0.07); }
.stMetric { background: #fff; border-radius: 14px; padding: 16px; box-shadow: 0 1px 6px rgba(0,0,0,0.07); }
div[data-testid="metric-container"] { background: #fff; border-radius: 14px; padding: 16px; box-shadow: 0 1px 6px rgba(0,0,0,0.07); }
</style>
""", unsafe_allow_html=True)

# ── 구글시트 연결 ────────────────────────────────────────────────────────
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SPREADSHEET_NAME = "SaveTree ERP"  # 구글시트 파일명

@st.cache_resource
def get_client():
    creds_dict = st.secrets["gcp_service_account"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

@st.cache_data(ttl=60)  # 60초 캐시
def load_data():
    client = get_client()
    # 스프레드시트 ID로 열기
    sh = client.open_by_key(st.secrets["spreadsheet_id"])
    
    # 현장마스터
    master_ws = sh.worksheet("현장마스터")
    master_df = pd.DataFrame(master_ws.get_all_records())
    
    # 스프링카운티자이
    spring_ws = sh.worksheet("스프링카운티자이")
    spring_df = pd.DataFrame(spring_ws.get_all_records())
    spring_df["현장ID"] = "SC001"
    spring_df["현장명"] = "스프링카운티자이"
    
    # 다산유승한내들
    dasan_ws = sh.worksheet("다산유승한내들")
    dasan_df = pd.DataFrame(dasan_ws.get_all_records())
    dasan_df["현장ID"] = "DS001"
    dasan_df["현장명"] = "다산유승한내들"
    
    # 합치기
    records_df = pd.concat([spring_df, dasan_df], ignore_index=True)
    
    # 컬럼명 정리
    records_df.columns = [c.strip() for c in records_df.columns]
    
    # 금액 계산
    if "수량" in records_df.columns and "단가(원)" in records_df.columns:
        records_df["수량"] = pd.to_numeric(records_df["수량"], errors="coerce").fillna(0)
        records_df["단가"] = pd.to_numeric(records_df["단가(원)"], errors="coerce").fillna(0)
        records_df["금액"] = records_df["수량"] * records_df["단가"]
    
    return master_df, records_df

def fmt_won(n):
    return f"{int(n):,}원"

def pct(a, b):
    return round(a/b*100) if b else 0

# ── 사이드바 ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🌳 SaveTree")
    st.markdown("##### 현장 원가 관리 시스템")
    st.divider()
    
    menu = st.radio("메뉴", ["📊 대시보드", "📋 작업 내역", "📍 현장 현황", "📄 보고서"], label_visibility="hidden")
    
    st.divider()
    if st.button("🔄 데이터 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.caption(f"마지막 업데이트: {datetime.now().strftime('%H:%M:%S')}")

# ── 데이터 로드 ───────────────────────────────────────────────────────────
try:
    master_df, records_df = load_data()
    
    # 빈 행 제거
    records_df = records_df[records_df["작업항목"].notna() & (records_df["작업항목"] != "")]
    
    SITES = master_df.to_dict("records")
    
except Exception as e:
    st.error(f"⚠️ 데이터 로드 실패: {e}")
    st.info("secrets.toml 설정을 확인해주세요.")
    st.stop()

# ── 대시보드 ──────────────────────────────────────────────────────────────
if menu == "📊 대시보드":
    st.title("📊 대시보드")
    
    month = "2026-06"
    act = records_df[(records_df["상태"] == "실적") & (records_df["날짜"].astype(str).str.startswith(month))]
    total_act = act["금액"].sum()
    
    # 예산 합계
    budget_col = "월예산(원)" if "월예산(원)" in master_df.columns else "월 예산(원)"
    total_budget = pd.to_numeric(master_df[budget_col].astype(str).str.replace(",",""), errors="coerce").sum()
    
    upcoming = records_df[records_df["상태"] == "예정"].sort_values("날짜")
    
    # KPI
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("이번달 실투입 원가", fmt_won(total_act), f"예산 {fmt_won(total_budget)}")
    with col2:
        rate = pct(total_act, total_budget)
        st.metric("전체 집행률", f"{rate}%", f"잔여 {fmt_won(total_budget - total_act)}")
    with col3:
        st.metric("예정 작업", f"{len(upcoming)}건", "향후 30일 이내")
    
    st.divider()
    
    # 현장별 집행률
    st.subheader("현장별 집행 현황")
    cols = st.columns(len(SITES))
    for i, site in enumerate(SITES):
        sid = site.get("현장ID", "")
        sname = site.get("현장명", "")
        budget_val = pd.to_numeric(str(site.get(budget_col, 0)).replace(",",""), errors="coerce") or 0
        site_act = act[act["현장ID"] == sid]["금액"].sum()
        site_rate = pct(site_act, budget_val)
        
        with cols[i]:
            st.markdown(f"**{sname}**")
            st.progress(min(site_rate/100, 1.0))
            c1, c2 = st.columns(2)
            c1.metric("실적", fmt_won(site_act))
            c2.metric("집행률", f"{site_rate}%")
    
    st.divider()
    
    # 주별 원가 추이
    col_l, col_r = st.columns([1.7, 1])
    
    with col_l:
        st.subheader("주별 원가 추이 (실적)")
        if "주차" in records_df.columns:
            weekly = act.groupby(["주차","현장명"])["금액"].sum().reset_index()
            if not weekly.empty:
                weekly_pivot = weekly.pivot(index="주차", columns="현장명", values="금액").fillna(0)
                st.bar_chart(weekly_pivot)
    
    with col_r:
        st.subheader("항목별 원가 구성")
        if "카테고리" in act.columns:
            cat_sum = act.groupby("카테고리")["금액"].sum()
            if not cat_sum.empty:
                st.bar_chart(cat_sum)
    
    st.divider()
    
    # 예정 작업
    st.subheader("예정 작업 (다음 30일)")
    if len(upcoming) > 0:
        cols = st.columns(min(3, len(upcoming)))
        for i, (_, row) in enumerate(upcoming.head(6).iterrows()):
            with cols[i % 3]:
                with st.container(border=True):
                    st.caption(f"📅 {row.get('날짜','')} · {row.get('주차','')}주차")
                    st.markdown(f"**{row.get('작업항목','')}**")
                    c1, c2 = st.columns(2)
                    c1.caption(row.get("카테고리",""))
                    c2.markdown(f"**{fmt_won(row.get('금액',0))}**")
    else:
        st.info("예정 작업이 없습니다.")

# ── 작업 내역 ─────────────────────────────────────────────────────────────
elif menu == "📋 작업 내역":
    st.title("📋 작업 내역")
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        site_filter = st.selectbox("현장", ["전체"] + list(records_df["현장명"].unique()))
    with col2:
        status_filter = st.selectbox("상태", ["전체", "실적", "예정"])
    with col3:
        cat_filter = st.selectbox("카테고리", ["전체", "작업비", "자재비", "인건비", "경비"])
    with col4:
        search = st.text_input("작업명 검색", placeholder="검색어 입력...")
    
    filtered = records_df.copy()
    if site_filter != "전체":
        filtered = filtered[filtered["현장명"] == site_filter]
    if status_filter != "전체":
        filtered = filtered[filtered["상태"] == status_filter]
    if cat_filter != "전체":
        filtered = filtered[filtered["카테고리"] == cat_filter]
    if search:
        filtered = filtered[filtered["작업항목"].str.contains(search, na=False)]
    
    total = filtered["금액"].sum()
    st.caption(f"총 {len(filtered)}건 | 합계: {fmt_won(total)}")
    
    # 표시할 컬럼
    show_cols = ["날짜", "주차", "현장명", "카테고리", "작업항목", "수량", "단가", "금액", "상태", "비고"]
    show_cols = [c for c in show_cols if c in filtered.columns]
    
    st.dataframe(
        filtered[show_cols].reset_index(drop=True),
        use_container_width=True,
        height=500,
        column_config={
            "금액": st.column_config.NumberColumn("금액", format="%d원"),
            "단가": st.column_config.NumberColumn("단가", format="%d원"),
        }
    )

# ── 현장 현황 ─────────────────────────────────────────────────────────────
elif menu == "📍 현장 현황":
    st.title("📍 현장 현황")
    
    budget_col = "월예산(원)" if "월예산(원)" in master_df.columns else "월 예산(원)"
    
    for site in SITES:
        sid = site.get("현장ID","")
        sname = site.get("현장명","")
        budget_val = pd.to_numeric(str(site.get(budget_col,0)).replace(",",""), errors="coerce") or 0
        
        site_recs = records_df[(records_df["현장ID"]==sid) & (records_df["상태"]=="실적")]
        actual = site_recs["금액"].sum()
        rate = pct(actual, budget_val)
        
        with st.expander(f"🌳 {sname} — 집행률 {rate}%", expanded=True):
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("담당 센터장", site.get("담당센터장", site.get("담당 센터장","")))
            col2.metric("현장 면적", f"{site.get('면적(㎡)', site.get('현장 면적(㎡)',''))}㎡")
            col3.metric("실적 합계", fmt_won(actual))
            col4.metric("월 예산", fmt_won(budget_val))
            
            st.progress(min(rate/100, 1.0), text=f"집행률 {rate}%")
            
            # 카테고리별
            cat_sum = site_recs.groupby("카테고리")["금액"].sum()
            if not cat_sum.empty:
                st.bar_chart(cat_sum)

# ── 보고서 ────────────────────────────────────────────────────────────────
elif menu == "📄 보고서":
    st.title("📄 보고서")
    
    col1, col2 = st.columns(2)
    with col1:
        rep_site = st.selectbox("현장", ["전체"] + list(records_df["현장명"].unique()))
    with col2:
        rep_month = st.text_input("기준월 (YYYY-MM)", value="2026-06")
    
    recs = records_df[
        (records_df["날짜"].astype(str).str.startswith(rep_month)) &
        (records_df["상태"] == "실적")
    ]
    if rep_site != "전체":
        recs = recs[recs["현장명"] == rep_site]
    
    total = recs["금액"].sum()
    budget_col = "월예산(원)" if "월예산(원)" in master_df.columns else "월 예산(원)"
    budget_sites = master_df if rep_site == "전체" else master_df[master_df["현장명"] == rep_site]
    budget = pd.to_numeric(budget_sites[budget_col].astype(str).str.replace(",",""), errors="coerce").sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("총 집행 원가", fmt_won(total), f"예산 {fmt_won(budget)}")
    col2.metric("집행률", f"{pct(total, budget)}%", f"잔여 {fmt_won(budget-total)}")
    col3.metric("작업 건수", f"{len(recs)}건")
    
    st.divider()
    
    col_l, col_r = st.columns(2)
    
    with col_l:
        st.subheader("현장별 집행 현황")
        site_sum = recs.groupby("현장명")["금액"].sum().reset_index()
        if not site_sum.empty:
            st.bar_chart(site_sum.set_index("현장명"))
    
    with col_r:
        st.subheader("카테고리별 원가")
        cat_sum = recs.groupby("카테고리")["금액"].sum()
        if not cat_sum.empty:
            st.bar_chart(cat_sum)
    
    st.divider()
    st.subheader("작업 상세 내역")
    
    show_cols = ["날짜", "현장명", "카테고리", "작업항목", "수량", "단가", "금액"]
    show_cols = [c for c in show_cols if c in recs.columns]
    
    st.dataframe(
        recs[show_cols].reset_index(drop=True),
        use_container_width=True,
        column_config={
            "금액": st.column_config.NumberColumn("금액", format="%d원"),
            "단가": st.column_config.NumberColumn("단가", format="%d원"),
        }
    )
    
    st.caption(f"합계: {fmt_won(total)}")
