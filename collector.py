import datetime
import os
import sqlite3
import time
from apscheduler.schedulers.background import BackgroundScheduler
import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "samo_sugup.db")
AUTH_KEY = "61BCAC40E5AC4680A401E0ABA4AD3D7D7611B890"

# 명세서 API 서버 엔드포인트 URL
URL_KOSPI = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd"
URL_KOSDAQ = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd"
URL_INVESTOR_KOSPI = "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_invst"
URL_INVESTOR_KOSDAQ = "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_invst"


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
    print(f"[{date_str}] -> 수집 가능한 데이터가 없습니다 (휴장일/데이터 미제공).")
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
        INSERT OR REPLACE INTO samo_daily (
            date, market, ticker, name, net_buy_val, market_cap
        ) VALUES (?, ?, ?, ?, ?, ?)
    """,
      rows,
  )

  conn.commit()
  conn.close()
  print(f" ✅ [{date_str}] 총 {len(rows)}개 종목 수집 완료!")
  return True


def run_backfill(start_date="20260101", end_date=None):
  if end_date is None:
    end_date = datetime.datetime.now().strftime("%Y%m%d")

  print(
      f"\n=== 사모펀드 수급 일별 데이터 소급 수집 ({start_date} ~ {end_date})"
      " ==="
  )
  print(f"-> DB 저장 위치: {DB_PATH}\n")
  start_dt = datetime.datetime.strptime(start_date, "%Y%m%d")
  end_dt = datetime.datetime.strptime(end_date, "%Y%m%d")

  curr = start_dt
  while curr <= end_dt:
    d_str = curr.strftime("%Y%m%d")
    collect_daily_data(d_str)
    curr += datetime.timedelta(days=1)
    time.sleep(0.2)
  print("=== 과거 일별 데이터 수집 완료 ===\n")


def start_scheduler():
  scheduler = BackgroundScheduler(timezone="Asia/Seoul")

  def daily_job():
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    print(f"[{datetime.datetime.now()}] 매일 17:00 사모 수급 일별 수집 실행")
    collect_daily_data(today_str)

  scheduler.add_job(daily_job, "cron", hour=17, minute=0)
  scheduler.start()


if __name__ == "__main__":
  try:
    init_db()
    today_s = datetime.datetime.now().strftime("%Y%m%d")
    # 2026년 1월 1일부터 오늘까지 모든 데이터 수집
    run_backfill("20260101", today_s)
    start_scheduler()
  except Exception as main_e:
    print(f"\n❌ 실행 중 오류 발생: {main_e}")
  finally:
    print("\n" + "=" * 60)
    input(
        "프로그램 실행이 완료되었습니다. 엔터(Enter) 키를 누르면 창이 닫힙니다..."
    )