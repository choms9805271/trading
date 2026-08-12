import datetime
import os
import sqlite3
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "samo_sugup.db")

st.set_page_config(
    page_title="사모펀드 수급 역대 순위 분석 대시보드",
    page_icon="📈",
    layout="wide",
)

st.title("📈 사모펀드 수급 역대 순위 & 시가총액 비중 대시보드")
st.caption(
    "2026년 1월 1일부터 누적된 일별 데이터를 바탕으로 사모펀드 매수 금액의 종목"
    " 내 역대 순위와 시가총액 대비 비중(%)을 분석합니다."
)


def get_db_connection():
  return sqlite3.connect(DB_PATH)


def get_available_dates():
  try:
    if not os.path.exists(DB_PATH):
      return []
    conn = get_db_connection()
    df = pd.read_sql(
        "SELECT DISTINCT date FROM samo_daily ORDER BY date DESC", conn
    )
    conn.close()
    return df["date"].tolist()
  except Exception:
    return []


dates = get_available_dates()
st.sidebar.header("⚙️ 분석 조건 설정")

if not dates:
  st.warning(
      "저장된 데이터가 없습니다. 먼저 `collector.py`를 실행하여 2026년 1월 1일부터"
      " 데이터를 수집해 주세요."
  )
  st.info(f"💡 DB 파일 경로: `{DB_PATH}`")
else:
  min_date = datetime.datetime.strptime(dates[-1], "%Y-%m-%d").date()
  max_date = datetime.datetime.strptime(dates[0], "%Y-%m-%d").date()

  sel_date = st.sidebar.date_input(
      "조회 날짜 선택", value=max_date, min_value=min_date, max_value=max_date
  )
  date_filter = sel_date.strftime("%Y-%m-%d")

  unit = st.sidebar.radio("금액 표시 단위", ["원", "백만원", "억원"], index=2)
  unit_div = 100000000 if unit == "억원" else (1000000 if unit == "백만원" else 1)

  conn = get_db_connection()

  # 1. 선택한 날짜의 당일 수급 데이터 조회
  query_today = f"""
        SELECT date, market, ticker AS 종목코드, name AS 종목명, 
               net_buy_val AS 사모_순매수, market_cap AS 시가총액
        FROM samo_daily
        WHERE date = '{date_filter}'
    """
  df_today = pd.read_sql(query_today, conn)

  # 2. 해당 날짜까지의 과거 전체 수급 데이터 조회 (종목별 역대 순위 산출용)
  query_hist = f"""
        SELECT ticker AS 종목코드, date, net_buy_val AS 사모_순매수
        FROM samo_daily
        WHERE date <= '{date_filter}'
    """
  df_hist = pd.read_sql(query_hist, conn)
  conn.close()

  if df_today.empty:
    st.warning(
        f"{date_filter} 날짜의 데이터가 존재하지 않습니다. `collector.py`를"
        " 실행해 주세요."
    )
  else:
    df_today["사모_순매수"] = (
        pd.to_numeric(df_today["사모_순매수"], errors="coerce").fillna(0)
    )
    df_today["시가총액"] = (
        pd.to_numeric(df_today["시가총액"], errors="coerce").fillna(0)
    )

    # 3. 회사의 시가총액 대비 매수금액 비중 (%) 계산
    df_today["시가총액_대비_비중_pct"] = np.where(
        df_today["시가총액"] > 0,
        (df_today["사모_순매수"] / df_today["시가총액"]) * 100,
        0,
    )

    # 4. 각 종목별 과거 수집일 중 당일 매수금액의 순위(등수) 계산
    hist_ranks = []
    hist_total_days = []

    for _, row in df_today.iterrows():
      tk = row["종목코드"]
      val = row["사모_순매수"]

      sub = df_hist[df_hist["종목코드"] == tk]["사모_순매수"]
      total_cnt = len(sub)
      if total_cnt > 0:
        # 당일 금액보다 큰 날의 수 + 1 = 당일의 역대 순위 (1등 = 가장 많이 매수한 날)
        rank = (sub > val).sum() + 1
      else:
        rank = 1
        total_cnt = 1

      hist_ranks.append(f"{rank}등 / {total_cnt}일 중")
      hist_total_days.append(rank)

    df_today["종목_내_과거_수급_순위"] = hist_ranks
    df_today["역대_순위_숫자"] = hist_total_days

    # 단위 변환
    df_today[f"당일 사모 순매수 ({unit})"] = df_today["사모_순매수"] / unit_div
    df_today[f"시가총액 ({unit})"] = df_today["시가총액"] / unit_div

    # 시장 필터링
    df_today["market"] = df_today["market"].fillna("").astype(str)
    is_kospi = df_today["market"].str.contains(
        "KOSPI|코스피|STK", case=False, na=False
    )
    is_kosdaq = df_today["market"].str.contains(
        "KOSDAQ|코스닥|KSQ", case=False, na=False
    )

    # KOSPI / KOSDAQ 당일 사모 순매수 금액 기준 상위 30개 종목 추출
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

    # 상단 요약
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
          "250일 중 역대 수급 순위",
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