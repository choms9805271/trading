import datetime
import os
import sqlite3
import time
import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# 절대 경로 설정 (Streamlit Cloud 가상 환경 자동 대응)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "samo_sugup.db")
AUTH_KEY = "61BCAC40E5AC4680A401E0ABA4AD3D7D7611B890"

URL_KOSPI = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
URL_KOSDAQ = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"
URL_INVESTOR_KOSPI = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_invst"
URL_INVESTOR_KOSDAQ = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_invst"

st.set_page_config(
    page_title="사모펀드 수급 역대 순위 대시보드",
    page_icon="📈",
    layout="wide",
)

st.title("📈 사모펀드 수급 역대 순위 & 시가총액 비중 대시보드")
st.caption(
    "2026년 1월 1일부터 누적된 데이터를 바탕으로 사모펀드 매수 금액의 종목"
    " 내 역대 순위와 시가총액 대비 비중(%)을 분석합니다."
)


def init_db():
  os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS samo_daily (
            date TEXT,
            market TEXT,
            ticker TEXT,
            name TEXT,
            net_buy_val REAL,
            market_cap REAL,
            PRIMARY KEY (date, ticker)
        )
    """)
  conn.commit()
  conn.close()


def fetch_api_endpoint(url, date_str):
  headers = {
      "AUTH_KEY": AUTH_KEY,
      "Content-Type": "application/json",
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      ),
  }
  try:
    response = requests.get(
        url, headers=headers, params={"basDd": date_str}, timeout=10
    )
    if response.status_code != 200:
      response = requests.post(
          url, headers=headers, json={"basDd": date_str}, timeout=10
      )
    if response.status_code == 200:
      data = response.json()
      if "OutBlock_1" in data and data["OutBlock_1"]:
        return pd.DataFrame(data["OutBlock_1"])
    return None
  except Exception:
    return None


def collect_market_data(date_str, default_market, url_trade, url_inv):
  df_trade = fetch_api_endpoint(url_trade, date_str)
  time.sleep(0.1)
  df_inv = fetch_api_endpoint(url_inv, date_str)

  if df_trade is None or df_trade.empty:
    return None

  if "MKT_NM" in df_trade.columns:
    df_trade["market"] = df_trade["MKT_NM"].fillna(default_market)
  else:
    df_trade["market"] = default_market

  if df_inv is not None and not df_inv.empty:
    net_col = (
        "PRIV_NETBID_TRDVAL"
        if "PRIV_NETBID_TRDVAL" in df_inv.columns
        else ("NETBID_TRDVAL" if "NETBID_TRDVAL" in df_inv.columns else None)
    )
    if net_col and "ISU_CD" in df_inv.columns:
      df_merged = df_trade.merge(
          df_inv[["ISU_CD", net_col]], on="ISU_CD", how="left"
      )
      df_merged["net_buy_val"] = pd.to_numeric(
          df_merged[net_col], errors="coerce"
      ).fillna(0)
    else:
      df_merged = df_trade.copy()
      df_merged["net_buy_val"] = pd.to_numeric(
          df_merged.get("ACC_TRDVAL", 0), errors="coerce"
      ).fillna(0)
  else:
    df_merged = df_trade.copy()
    df_merged["net_buy_val"] = pd.to_numeric(
        df_merged.get("ACC_TRDVAL", 0), errors="coerce"
    ).fillna(0)

  return pd.DataFrame({
      "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
      "market": df_merged["market"].astype(str),
      "ticker": df_merged["ISU_CD"].astype(str),
      "name": df_merged["ISU_NM"].astype(str),
      "net_buy_val": df_merged["net_buy_val"],
      "market_cap": pd.to_numeric(
          df_merged.get("MKTCAP", 0), errors="coerce"
      ).fillna(0),
  })


def collect_daily_data(date_str):
  init_db()
  df_ksp = collect_market_data(
      date_str, "KOSPI", URL_KOSPI, URL_INVESTOR_KOSPI
  )
  df_ksd = collect_market_data(
      date_str, "KOSDAQ", URL_KOSDAQ, URL_INVESTOR_KOSDAQ
  )
  dfs = [df for df in [df_ksp, df_ksd] if df is not None and not df.empty]
  if not dfs:
    return False

  df_today = pd.concat(dfs, ignore_index=True)
  conn = sqlite3.connect(DB_PATH)
  cursor = conn.cursor()
  rows = [
      (
          str(r["date"]),
          str(r["market"]),
          str(r["ticker"]),
          str(r["name"]),
          float(r["net_buy_val"]),
          float(r["market_cap"]),
      )
      for _, r in df_today.iterrows()
  ]
  cursor.executemany(
      """
        INSERT OR REPLACE INTO samo_daily (date, market, ticker, name, net_buy_val, market_cap)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
      rows,
  )
  conn.commit()
  conn.close()
  return True


def auto_collect_if_empty(start_date="20260101"):
  """클라우드 서버에 데이터가 없을 때 자동 수집 진행"""
  end_date = datetime.datetime.now().strftime("%Y%m%d")
  start_dt = datetime.datetime.strptime(start_date, "%Y%m%d")
  end_dt = datetime.datetime.strptime(end_date, "%Y%m%d")
  curr = start_dt
  count = 0
  progress_bar = st.progress(0)
  status_text = st.empty()

  total_days = (end_dt - start_dt).days + 1
  idx = 0
  while curr <= end_dt:
    d_str = curr.strftime("%Y%m%d")
    idx += 1
    status_text.text(f"⏳ 데이터 자동 수집 중: {d_str} ({idx}/{total_days})")
    if collect_daily_data(d_str):
      count += 1
    progress_bar.progress(idx / total_days)
    curr += datetime.timedelta(days=1)
    time.sleep(0.05)

  status_text.text("✅ 자동 수집이 완료되었습니다!")
  time.sleep(1)
  status_text.empty()
  progress_bar.empty()


def get_available_dates():
  try:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT DISTINCT date FROM samo_daily ORDER BY date DESC", conn
    )
    conn.close()
    return df["date"].tolist()
  except Exception:
    return []


dates = get_available_dates()

# 최초 접속 시 데이터가 없으면 클라우드 상에서 자동 데이터 수집 수행
if not dates:
  st.info(
      "💡 클라우드 서버에 데이터가 없어 2026년 1월 1일부터 현재까지 수급"
      " 데이터를 최초 자동 수집합니다."
  )
  auto_collect_if_empty("20260101")
  dates = get_available_dates()

st.sidebar.header("⚙️ 대시보드 옵션")

if st.sidebar.button("🔄 수급 데이터 즉시 재수집 / 갱신"):
  st.info("데이터를 갱신합니다...")
  auto_collect_if_empty("20260101")
  st.cache_data.clear()
  st.rerun()

if not dates:
  st.error("데이터 수집에 실패했거나 거래일 데이터가 없습니다.")
else:
  min_date = datetime.datetime.strptime(dates[-1], "%Y-%m-%d").date()
  max_date = datetime.datetime.strptime(dates[0], "%Y-%m-%d").date()

  st.sidebar.success(
      f"📅 수집된 데이터: {min_date} ~ {max_date} (총 {len(dates)}거래일)"
  )

  sel_date = st.sidebar.date_input(
      "조회 날짜 선택", value=max_date, min_value=min_date, max_value=max_date
  )
  date_filter = sel_date.strftime("%Y-%m-%d")

  unit = st.sidebar.radio("금액 표시 단위", ["원", "백만원", "억원"], index=2)
  unit_div = 100000000 if unit == "억원" else (1000000 if unit == "백만원" else 1)

  conn = sqlite3.connect(DB_PATH)
  query_today = f"""
        SELECT date, market, ticker AS 종목코드, name AS 종목명, 
               net_buy_val AS 사모_순매수, market_cap AS 시가총액
        FROM samo_daily
        WHERE date = '{date_filter}'
    """
  df_today = pd.read_sql(query_today, conn)

  query_hist = f"""
        SELECT ticker AS 종목코드, date, net_buy_val AS 사모_순매수
        FROM samo_daily
        WHERE date <= '{date_filter}'
    """
  df_hist = pd.read_sql(query_hist, conn)
  conn.close()

  if df_today.empty:
    st.warning(
        f"{date_filter} 날짜의 데이터가 존재하지 않습니다. 사이드바 수집"
        " 버튼을 눌러주세요."
    )
  else:
    df_today["사모_순매수"] = (
        pd.to_numeric(df_today["사모_순매수"], errors="coerce").fillna(0)
    )
    df_today["시가총액"] = (
        pd.to_numeric(df_today["시가총액"], errors="coerce").fillna(0)
    )

    df_today["시가총액_대비_비중_pct"] = np.where(
        df_today["시가총액"] > 0,
        (df_today["사모_순매수"] / df_today["시가총액"]) * 100,
        0,
    )

    hist_ranks = []
    for _, row in df_today.iterrows():
      tk = row["종목코드"]
      val = row["사모_순매수"]
      sub = df_hist[df_hist["종목코드"] == tk]["사모_순매수"]
      total_cnt = len(sub)
      rank = (sub > val).sum() + 1 if total_cnt > 0 else 1
      hist_ranks.append(f"{rank}등 / {total_cnt}일 중")

    df_today["종목_내_과거_수급_순위"] = hist_ranks
    df_today[f"당일 사모 순매수 ({unit})"] = df_today["사모_순매수"] / unit_div
    df_today[f"시가총액 ({unit})"] = df_today["시가총액"] / unit_div

    df_today["market"] = df_today["market"].fillna("").astype(str)
    is_kospi = df_today["market"].str.contains(
        "KOSPI|코스피|STK", case=False, na=False
    )
    is_kosdaq = df_today["market"].str.contains(
        "KOSDAQ|코스닥|KSQ", case=False, na=False
    )

    df_kospi = (
        df_today[is_kospi]
        .sort_values(by="사모_순매수", ascending=False)
        .head(30)
        .reset_index(drop=True)
    )
    df_kosdaq = (
        df_today[is_kosdaq]
        .sort_values(by="사모_순매수", ascending=False)
        .head(30)
        .reset_index(drop=True)
    )
    df_all = (
        df_today.sort_values(by="사모_순매수", ascending=False)
        .head(30)
        .reset_index(drop=True)
    )

    df_kospi["시장내_순위"] = np.arange(1, len(df_kospi) + 1)
    df_kosdaq["시장내_순위"] = np.arange(1, len(df_kosdaq) + 1)
    df_all["시장내_순위"] = np.arange(1, len(df_all) + 1)

    st.markdown(f"### 📌 {date_filter} 사모펀드 매수 요약")
    m1, m2, m3, m4 = st.columns(4)
    top_ksp = df_kospi.iloc[0] if not df_kospi.empty else None
    top_ksd = df_kosdaq.iloc[0] if not df_kosdaq.empty else None

    if top_ksp is not None:
      m1.metric(
          "KOSPI 1위 종목",
          f"{top_ksp['종목명']}",
          f"역대 순위: {top_ksp['종목_내_과거_수급_순위']}",
      )
      m2.metric(
          "KOSPI 1위 매수액",
          f"{top_ksp[f'당일 사모 순매수 ({unit})']:,.1f} {unit}",
          f"시총 대비 {top_ksp['시가총액_대비_비중_pct']:.2f}%",
      )
    else:
      m1.metric("KOSPI 1위 종목", "-")
      m2.metric("KOSPI 1위 매수액", "-")

    if top_ksd is not None:
      m3.metric(
          "KOSDAQ 1위 종목",
          f"{top_ksd['종목명']}",
          f"역대 순위: {top_ksd['종목_내_과거_수급_순위']}",
      )
      m4.metric(
          "KOSDAQ 1위 매수액",
          f"{top_ksd[f'당일 사모 순매수 ({unit})']:,.1f} {unit}",
          f"시총 대비 {top_ksd['시가총액_대비_비중_pct']:.2f}%",
      )
    else:
      m3.metric("KOSDAQ 1위 종목", "-")
      m4.metric("KOSDAQ 1위 매수액", "-")

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs([
        "🏛️ KOSPI 가장 강하게 들어온 30개 기업",
        "🚀 KOSDAQ 가장 강하게 들어온 30개 기업",
        "🌐 전체 시장 통합 상위 30",
    ])

    def render_market_section(df_mkt, market_label):
      if df_mkt.empty:
        st.info(f"{market_label} 조건에 해당하는 데이터가 없습니다.")
        return

      c_chart1, c_chart2 = st.columns(2)

      with c_chart1:
        st.markdown(f"#### 📊 {market_label} 당일 사모 순매수 TOP 15 ({unit})")
        fig_bar = px.bar(
            df_mkt.head(15),
            x="종목명",
            y=f"당일 사모 순매수 ({unit})",
            text=f"당일 사모 순매수 ({unit})",
            color=f"당일 사모 순매수 ({unit})",
            color_continuous_scale="Reds",
            hover_data=[
                "종목코드",
                "종목_내_과거_수급_순위",
                "시가총액_대비_비중_pct",
            ],
        )
        fig_bar.update_traces(
            texttemplate="%{text:,.1f}", textposition="outside"
        )
        st.plotly_chart(fig_bar, use_container_width=True)

      with c_chart2:
        st.markdown(f"#### 🎯 시가총액 대비 순매수 비율 (%)")
        fig_ratio = px.bar(
            df_mkt.head(15),
            x="종목명",
            y="시가총액_대비_비중_pct",
            text="시가총액_대비_비중_pct",
            color="시가총액_대비_비중_pct",
            color_continuous_scale="Oranges",
            hover_data=["종목코드", "종목_내_과거_수급_순위"],
        )
        fig_ratio.update_traces(
            texttemplate="%{text:.2f}%", textposition="outside"
        )
        st.plotly_chart(fig_ratio, use_container_width=True)

      st.markdown(
          f"#### 📋 {market_label} 사모펀드 순매수 상위 30개 기업 상세 표"
      )

      disp_cols = [
          "시장내_순위",
          "종목코드",
          "종목명",
          f"당일 사모 순매수 ({unit})",
          f"시가총액 ({unit})",
          "종목_내_과거_수급_순위",
          "시가총액_대비_비중_pct",
      ]

      show_df = df_mkt[disp_cols].copy()
      show_df.columns = [
          "시장 순위",
          "종목코드",
          "종목명",
          f"당일 사모 순매수 ({unit})",
          f"시가총액 ({unit})",
          "누적 수급 중 역대 순위",
          "시가총액 대비 매수 비율 (%)",
      ]

      st.dataframe(
          show_df.style.format({
              f"당일 사모 순매수 ({unit})": "{:,.2f}",
              f"시가총액 ({unit})": "{:,.0f}",
              "시가총액 대비 매수 비율 (%)": "{:.2f}%",
          }),
          use_container_width=True,
          height=500,
      )

      csv = show_df.to_csv(index=False, encoding="utf-8-sig")
      st.download_button(
          label=f"📥 {market_label} 상위 30개 종목 CSV 다운로드",
          data=csv,
          file_name=f"{market_label}_samo_top30_{date_filter}.csv",
          mime="text/csv",
      )

    with tab1:
      render_market_section(df_kospi, "KOSPI")

    with tab2:
      render_market_section(df_kosdaq, "KOSDAQ")

    with tab3:
      render_market_section(df_all, "전체 시장")