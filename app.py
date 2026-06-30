import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime, date

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

@st.cache_resource
def get_client():
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=SCOPES)
    return gspread.authorize(creds)

def excel_date(val):
    """엑셀 날짜 시리얼 → 문자열 변환"""
    try:
        v = float(val)
        if v > 40000:
            d = datetime(1899, 12, 30) + pd.Timedelta(days=int(v))
            return d.strftime("%Y-%m-%d")
    except:
        pass
    return str(val).strip()

def clean_number(val):
    try:
        return float(str(val).replace(",","").replace(" ","") or 0)
    except:
        return 0

@st.cache_data(ttl=60)
def load_data():
    client = get_client()
    sh = client.open_by_key(st.secrets["spreadsheet_id"])

    def read_ws(ws):
        vals = ws.get_all_values()
        if not vals: return pd.DataFrame()
        # 헤더 중복/빈 처리
        headers = []
        seen = {}
        for h in vals[0]:
            h = h.strip() or "unnamed"
            if h in seen:
                seen[h] += 1
                h = f"{h}_{seen[h]}"
            else:
                seen[h] = 0
            headers.append(h)
        df = pd.DataFrame(vals[1:], columns=headers)
        # 완전히 빈 행 제거
        df = df[df.apply(lambda r: any(str(v).strip() for v in r), axis=1)]
        return df

    # 현장마스터
    mdf = read_ws(sh.worksheet("현장마스터"))

    # 현장별 시트
    sheets = [
        ("스프링카운티자이", "SC001"),
        ("다산유승한내들",   "DS001"),
    ]
    frames = []
    for sname, sid in sheets:
        try:
            df = read_ws(sh.worksheet(sname))
            df["현장ID"]  = sid
            df["현장명"]  = sname
            frames.append(df)
        except Exception as e:
            st.warning(f"{sname} 시트 로드 실패: {e}")

    rdf = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # 날짜 변환
    date_col = next((c for c in rdf.columns if "날짜" in c), None)
    if date_col:
        rdf["날짜"] = rdf[date_col].apply(excel_date)

    # 컬럼 표준화
    col_map = {}
    for c in rdf.columns:
        if "작업" in c and "항목" in c: col_map[c] = "작업항목"
        elif "카테고리" in c: col_map[c] = "카테고리"
        elif "주차" in c and c != "현장ID": col_map[c] = "주차"
        elif "규격" in c or "단위" in c: col_map[c] = "규격단위"
        elif "수량" in c: col_map[c] = "수량"
        elif "단가" in c and "금액" not in c: col_map[c] = "단가"
        elif "금액" in c: col_map[c] = "금액"
        elif "상태" in c: col_map[c] = "상태"
        elif "비고" in c: col_map[c] = "비고"
    rdf = rdf.rename(columns=col_map)

    # 숫자 변환
    for col in ["수량","단가","금액"]:
        if col in rdf.columns:
            rdf[col] = rdf[col].apply(clean_number)

    # 금액 재계산
    if "수량" in rdf.columns and "단가" in rdf.columns:
        rdf["금액"] = rdf["수량"] * rdf["단가"]

    # 현장마스터 컬럼 표준화
    mcol_map = {}
    for c in mdf.columns:
        if "현장ID" in c: mcol_map[c] = "현장ID"
        elif "현장명" in c: mcol_map[c] = "현장명"
        elif "센터장" in c or "담당" in c: mcol_map[c] = "담당센터장"
        elif "면적" in c: mcol_map[c] = "면적"
        elif "예산" in c: mcol_map[c] = "월예산"
        elif "시작" in c: mcol_map[c] = "계약시작"
        elif "종료" in c: mcol_map[c] = "계약종료"
        elif "유형" in c or "비고" in c: mcol_map[c] = "유형"
    mdf = mdf.rename(columns=mcol_map)
    if "월예산" in mdf.columns:
        mdf["월예산"] = mdf["월예산"].apply(clean_number)
    if "면적" in mdf.columns:
        mdf["면적"] = mdf["면적"].apply(clean_number)

    return mdf, rdf

def fmt_won(n):
    return f"{int(n):,}원"

def pct(a, b):
    return round(a/b*100) if b else 0

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
    rdf = rdf[rdf.get("작업항목", pd.Series(dtype=str)).astype(str).str.strip() != ""]
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.info("secrets.toml 설정 및 구글시트 공유 권한을 확인해주세요.")
    st.stop()

SITES = mdf.to_dict("records")

def get_budget(sid):
    row = mdf[mdf["현장ID"]==sid]
    if row.empty: return 0
    return float(row["월예산"].values[0]) if "월예산" in row.columns else 0

def get_total_budget():
    return mdf["월예산"].sum() if "월예산" in mdf.columns else 0

# ── 대시보드 ──────────────────────────────────────────────────────────────
if menu == "📊 대시보드":
    st.title("📊 대시보드")

    month = st.text_input("기준월", value="2026-06", label_visibility="collapsed")
    act = rdf[(rdf.get("상태","")=="실적") & (rdf["날짜"].astype(str).str.startswith(month))] if "상태" in rdf.columns else rdf
    total_act = act["금액"].sum() if "금액" in act.columns else 0
    total_budget = get_total_budget()
    upcoming = rdf[rdf.get("상태","")=="예정"].sort_values("날짜") if "상태" in rdf.columns else pd.DataFrame()

    c1,c2,c3 = st.columns(3)
    c1.metric("이번달 실투입 원가", fmt_won(total_act), f"예산 {fmt_won(total_budget)}")
    c2.metric("전체 집행률", f"{pct(total_act,total_budget)}%", f"잔여 {fmt_won(total_budget-total_act)}")
    c3.metric("예정 작업", f"{len(upcoming)}건", "향후 30일")

    st.divider()
    st.subheader("현장별 집행 현황")
    cols = st.columns(len(SITES)) if SITES else st.columns(1)
    for i, site in enumerate(SITES):
        sid = site.get("현장ID","")
        sname = site.get("현장명","")
        budget = get_budget(sid)
        site_act = act[act["현장ID"]==sid]["금액"].sum() if "현장ID" in act.columns else 0
        rate = pct(site_act, budget)
        with cols[i]:
            st.markdown(f"**{sname}**")
            st.progress(min(rate/100,1.0))
            a,b = st.columns(2)
            a.metric("실적", fmt_won(site_act))
            b.metric("집행률", f"{rate}%")

    st.divider()
    cl, cr = st.columns([1.7,1])
    with cl:
        st.subheader("주별 원가 추이")
        if "주차" in act.columns:
            weekly = act.groupby(["주차","현장명"])["금액"].sum().reset_index()
            if not weekly.empty:
                st.bar_chart(weekly.pivot(index="주차",columns="현장명",values="금액").fillna(0))
    with cr:
        st.subheader("항목별 원가 구성")
        if "카테고리" in act.columns:
            cat_sum = act.groupby("카테고리")["금액"].sum()
            if not cat_sum.empty:
                st.bar_chart(cat_sum)

    st.divider()
    st.subheader("예정 작업 (다음 30일)")
    if len(upcoming) > 0:
        cols3 = st.columns(3)
        for i, (_, row) in enumerate(upcoming.head(6).iterrows()):
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
    site_filter  = c1.selectbox("현장", ["전체"]+list(rdf["현장명"].unique())) if "현장명" in rdf.columns else c1.selectbox("현장",["전체"])
    status_filter= c2.selectbox("상태", ["전체","실적","예정"])
    cat_filter   = c3.selectbox("카테고리", ["전체","작업비","자재비","인건비","경비"])
    search       = c4.text_input("작업명 검색")

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

    show = [c for c in ["날짜","주차","현장명","카테고리","작업항목","수량","단가","금액","상태","비고"] if c in filtered.columns]
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
        budget= get_budget(sid)
        srecs = rdf[(rdf["현장ID"]==sid)&(rdf.get("상태","")=="실적")] if "현장ID" in rdf.columns else pd.DataFrame()
        actual= srecs["금액"].sum() if "금액" in srecs.columns else 0
        rate  = pct(actual,budget)

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
    rep_site  = c1.selectbox("현장", ["전체"]+list(rdf["현장명"].unique())) if "현장명" in rdf.columns else c1.selectbox("현장",["전체"])
    rep_month = c2.text_input("기준월 (YYYY-MM)", value="2026-06")

    recs = rdf[rdf["날짜"].astype(str).str.startswith(rep_month)] if "날짜" in rdf.columns else rdf
    if "상태" in recs.columns: recs = recs[recs["상태"]=="실적"]
    if rep_site!="전체" and "현장명" in recs.columns: recs = recs[recs["현장명"]==rep_site]

    total  = recs["금액"].sum() if "금액" in recs.columns else 0
    budget = mdf[mdf["현장명"]==rep_site]["월예산"].sum() if rep_site!="전체" else get_total_budget()

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
