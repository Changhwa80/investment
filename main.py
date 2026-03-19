import os
import re
import json
import yfinance as yf
import anthropic

PORTFOLIO_FILE = "portfolio.json"
REPORT_FILE = "artifacts/stock-report/public/daily_report.html"
REPORT_FILE_ROOT = "daily_report.html"

TRACKED_STOCKS_FILE = "tracked_stocks.json"
_DEFAULT_GROUPS: dict = {
    "🇰🇷 한국 반도체": ["005930.KS", "000660.KS"],
    "🇺🇸 미국 AI/반도체·EV": ["NVDA", "AMD", "AVGO", "TSLA"],
    "🚀 우주/SpaceX 관련 ETF": ["UFO", "ARKX", "XOVR"],
}
_DEFAULT_NAMES: dict = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "NVDA": "엔비디아",
    "AMD": "AMD",
    "AVGO": "브로드컴",
    "TSLA": "테슬라",
    "UFO": "UFO (우주ETF)",
    "ARKX": "ARKX (우주ETF)",
    "XOVR": "XOVR (크로스오버)",
}

if os.path.exists(TRACKED_STOCKS_FILE):
    with open(TRACKED_STOCKS_FILE, "r", encoding="utf-8") as _f:
        _tracked = json.load(_f)
    STOCK_GROUPS: dict = _tracked.get("stock_groups", _DEFAULT_GROUPS)
    KOREAN_NAMES: dict = {**_DEFAULT_NAMES, **_tracked.get("korean_names", {})}
else:
    STOCK_GROUPS = _DEFAULT_GROUPS
    KOREAN_NAMES = _DEFAULT_NAMES

ALL_TICKERS = [t for tickers in STOCK_GROUPS.values() for t in tickers]
CURRENCY_SYMBOL = {"USD": "$", "KRW": "₩", "TWD": "NT$"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_price(price, currency: str) -> str:
    if not isinstance(price, (int, float)):
        return "N/A"
    sym = CURRENCY_SYMBOL.get(currency, currency + " ")
    return f"{sym}{price:,.0f}" if currency == "KRW" else f"{sym}{price:,.2f}"


def fmt_cap(market_cap, currency: str) -> str:
    if not isinstance(market_cap, (int, float)):
        return "N/A"
    return f"₩{market_cap / 1e12:.1f}조" if currency == "KRW" else f"${market_cap / 1e9:.1f}B"


def calc_upside(current, target) -> float | None:
    if isinstance(current, (int, float)) and isinstance(target, (int, float)) and current > 0:
        return round((target - current) / current * 100, 1)
    return None


# ── Data fetching ──────────────────────────────────────────────────────────────

def get_stock_data(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info
    hist = stock.history(period="5d")
    week_change_pct = None
    if len(hist) >= 2:
        week_change_pct = ((hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0]) * 100

    currency = info.get("currency", "USD")
    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName", ticker),
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice") or info.get("navPrice"),
        "previous_close": info.get("previousClose"),
        "market_cap": info.get("marketCap") or info.get("totalAssets"),
        "pe_ratio": info.get("trailingPE"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "analyst_target": info.get("targetMeanPrice"),
        "week_change_pct": round(week_change_pct, 2) if week_change_pct is not None else None,
        "currency": currency,
        "asset_type": info.get("quoteType", "EQUITY"),
    }


# ── Terminal display ───────────────────────────────────────────────────────────

def build_stock_table(stocks: list[dict]) -> str:
    ticker_to_stock = {s["ticker"]: s for s in stocks}
    lines = []
    for group_name, tickers in STOCK_GROUPS.items():
        lines.append(f"\n{group_name}")
        lines.append(f"  {'티커':<14} {'종목명':<38} {'현재가':>13} {'5일수익률':>10} {'시가총액':>13}")
        lines.append("  " + "-" * 92)
        for ticker in tickers:
            s = ticker_to_stock.get(ticker)
            if not s:
                continue
            price_str = fmt_price(s["current_price"], s["currency"])
            cap_str = fmt_cap(s["market_cap"], s["currency"])
            week = s["week_change_pct"]
            week_str = f"{week:+.2f}%" if week is not None else "N/A"
            lines.append(f"  {ticker:<14} {s['name'][:37]:<38} {price_str:>13} {week_str:>10} {cap_str:>13}")
    return "\n".join(lines)


def print_stock_table(stocks: list[dict]) -> None:
    print(build_stock_table(stocks))


def print_recommendation_terminal(analysis: dict) -> None:
    REC_LABEL = {"매수": "✅ 매수", "보유": "🟡 보유", "매도": "🔴 매도"}
    RISK_LABEL = {"상": "🔴 상", "중": "🟡 중", "하": "🟢 하"}

    for s in analysis.get("stocks", []):
        rec = REC_LABEL.get(s.get("recommendation", ""), s.get("recommendation", ""))
        risk = RISK_LABEL.get(s.get("risk_level", ""), s.get("risk_level", ""))
        print(f"\n{'─'*56}")
        print(f"  {s.get('name', '')} ({s.get('ticker', '')})")
        print(f"  의견: {rec}   위험도: {risk}")
        print(f"  목표가: {s.get('target_price_text', 'N/A')}   진입가: {s.get('entry_price_text', 'N/A')}")
        print(f"  근거: {s.get('rationale', '')}")
        print(f"  리스크: {s.get('risk_note', '')}")

    print(f"\n{'═'*56}")
    print("  그룹별 총평")
    print(f"{'═'*56}")
    for g in analysis.get("group_summaries", []):
        print(f"\n  [{g.get('group', '')}]")
        print(f"  {g.get('summary', '')}")

    print(f"\n{'═'*56}")
    print("  핵심 요약")
    print(f"{'═'*56}")
    for i, tip in enumerate(analysis.get("key_takeaways", []), 1):
        print(f"  {i}. {tip}")


# ── Claude analysis ────────────────────────────────────────────────────────────

def format_stock_summary(stocks: list[dict]) -> str:
    lines = []
    for s in stocks:
        asset = "ETF" if s["asset_type"] in ("ETF", "MUTUALFUND") else "주식"
        pe = f"{s['pe_ratio']:.1f}" if isinstance(s["pe_ratio"], (int, float)) else "N/A"
        w = s["week_change_pct"]
        week_str = f"{w:+.2f}%" if w is not None else "N/A"
        high_str = fmt_price(s["52w_high"], s["currency"]) if s["52w_high"] else "N/A"
        low_str = fmt_price(s["52w_low"], s["currency"]) if s["52w_low"] else "N/A"
        target_str = fmt_price(s["analyst_target"], s["currency"]) if s["analyst_target"] else "N/A"
        lines.append(
            f"- [{asset}] {s['name']} ({s['ticker']}, {s['currency']}): "
            f"현재가 {fmt_price(s['current_price'], s['currency'])}, "
            f"시가총액 {fmt_cap(s['market_cap'], s['currency'])}, PER {pe}, "
            f"52주 최고 {high_str}, 52주 최저 {low_str}, "
            f"애널리스트 목표가 {target_str}, 5일 수익률 {week_str}"
        )
    return "\n".join(lines)


def parse_json_from_response(text: str) -> dict:
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = match.group(1) if match else text.strip()
    return json.loads(raw)


def get_recommendation(stocks: list[dict]) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")

    client = anthropic.Anthropic(api_key=api_key)
    summary = format_stock_summary(stocks)

    prompt = f"""다음은 한국 반도체주, 미국 AI/반도체/EV주, 우주/SpaceX 관련 ETF의 오늘 시장 데이터입니다.
한국 주식은 KRW(원화), 미국 주식/ETF는 USD(달러) 기준입니다.

{summary}

위 {len(stocks)}개 종목 각각을 분석하여 **반드시 아래 JSON 형식으로만** 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

{{
  "stocks": [
    {{
      "ticker": "NVDA",
      "name": "NVIDIA Corporation",
      "recommendation": "매수",
      "target_price_text": "$250~$270",
      "entry_price_text": "$170~$175 분할매수",
      "risk_level": "중",
      "risk_note": "중국 수출규제 강화 위험",
      "rationale": "AI 인프라 투자 확대 핵심 수혜주. 5일 조정으로 단기 매수 기회 형성. 애널리스트 목표가 대비 47% 업사이드."
    }}
  ],
  "group_summaries": [
    {{"group": "🇰🇷 한국 반도체", "summary": "그룹 전체 전망 2줄"}},
    {{"group": "🇺🇸 미국 AI·EV", "summary": "그룹 전체 전망 2줄"}},
    {{"group": "🚀 우주 ETF", "summary": "그룹 전체 전망 2줄"}}
  ],
  "key_takeaways": [
    "최우선 매수: ...",
    "보유/관망: ...",
    "비중축소/매도: ..."
  ]
}}

규칙:
- recommendation은 반드시 "매수", "보유", "매도" 중 하나
- risk_level은 반드시 "상", "중", "하" 중 하나
- ETF는 target_price_text에 단기 방향성("상승 예상", "횡보", "하락 예상")을 쓰세요
- rationale은 2~3문장으로 데이터 기반 한국어 작성
- 모든 필드는 한국어로 작성"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return parse_json_from_response(message.content[0].text)


# ── HTML report ────────────────────────────────────────────────────────────────

def build_html_report(stocks: list[dict], analysis: dict, generated_at: str, date_str: str) -> str:
    REC_CLASS = {"매수": "badge-buy", "보유": "badge-hold", "매도": "badge-sell"}
    REC_EN = {"매수": "BUY", "보유": "HOLD", "매도": "SELL"}
    RISK_CLASS = {"상": "risk-high", "중": "risk-mid", "하": "risk-low"}

    # Deduplicate Claude analysis: first occurrence of each ticker wins
    _seen: set[str] = set()
    _analysis_deduped = []
    for _a in analysis.get("stocks", []):
        if _a["ticker"] not in _seen:
            _seen.add(_a["ticker"])
            _analysis_deduped.append(_a)
    analysis_by_ticker = {a["ticker"]: a for a in _analysis_deduped}

    # Single source of truth: one merged dict per stock, iterated everywhere
    merged_stocks = []
    for s in stocks:
        a = analysis_by_ticker.get(s["ticker"], {})
        merged_stocks.append({
            **s,
            "korean_name": KOREAN_NAMES.get(s["ticker"], s["name"]),
            "recommendation": a.get("recommendation", "—"),
            "target_price_text": a.get("target_price_text", fmt_price(s.get("analyst_target"), s.get("currency", "USD"))),
            "entry_price_text": a.get("entry_price_text", "—"),
            "risk_level": a.get("risk_level", "—"),
            "risk_note": a.get("risk_note", ""),
            "rationale": a.get("rationale", ""),
        })

    buy_count  = sum(1 for m in merged_stocks if m["recommendation"] == "매수")
    hold_count = sum(1 for m in merged_stocks if m["recommendation"] == "보유")
    sell_count = sum(1 for m in merged_stocks if m["recommendation"] == "매도")

    by_ticker = {m["ticker"]: m for m in merged_stocks}

    upsides = []
    for m in merged_stocks:
        up = calc_upside(m["current_price"], m["analyst_target"])
        if up is None:
            up = calc_upside(m["current_price"], m["52w_high"])
        upsides.append((m["ticker"], up))
    max_abs_upside = max((abs(u) for _, u in upsides if u is not None), default=50) or 50

    def upside_bar(ticker) -> str:
        s = by_ticker.get(ticker, {})
        up = calc_upside(s.get("current_price"), s.get("analyst_target"))
        is_52w_fallback = False
        if up is None:
            up = calc_upside(s.get("current_price"), s.get("52w_high"))
            is_52w_fallback = True
        if up is None:
            return '<div class="bar-wrap"><span class="bar-na">데이터 없음</span></div>'
        pct = min(abs(up) / max_abs_upside * 100, 100)
        cls = "bar-pos" if up >= 0 else "bar-neg"
        suffix = " (52주 최고 기준)" if is_52w_fallback else ""
        label = f"{up:+.1f}%{suffix}"
        return (f'<div class="bar-wrap">'
                f'<div class="bar {cls}" style="width:{pct:.1f}%"></div>'
                f'<span class="bar-label">{label}</span>'
                f'</div>')

    def summary_rows() -> str:
        rows = []
        for m in merged_stocks:
            rec = m["recommendation"]
            badge_cls = REC_CLASS.get(rec, "badge-hold")
            rec_en = REC_EN.get(rec, rec)
            risk = m["risk_level"]
            risk_cls = RISK_CLASS.get(risk, "")
            price_str = fmt_price(m["current_price"], m["currency"])
            week = m["week_change_pct"]
            week_str = f"{week:+.2f}%" if week is not None else "N/A"
            week_cls = "positive" if (week or 0) >= 0 else "negative"
            arrow = "▲" if (week or 0) >= 0 else "▼"
            rows.append(f"""
        <tr>
          <td><span class="ticker-tag">{m['ticker']}</span></td>
          <td class="name-cell">{m['korean_name']}</td>
          <td><span class="badge {badge_cls}">{rec} {rec_en}</span></td>
          <td class="price">{price_str}</td>
          <td class="price">{m['target_price_text']}</td>
          <td class="entry-price">{m['entry_price_text']}</td>
          <td class="{week_cls}">{arrow} {week_str}</td>
          <td><span class="risk-badge {risk_cls}">{risk}</span></td>
        </tr>""")
        return "".join(rows)

    def chart_rows() -> str:
        rows = []
        for m in merged_stocks:
            rows.append(f"""
      <div class="chart-row">
        <div class="chart-label">
          <span class="chart-name">{m['korean_name']}</span>
        </div>
        <div class="chart-bar-area">
          {upside_bar(m['ticker'])}
        </div>
      </div>""")
        return "".join(rows)

    def analysis_cards() -> str:
        cards = []
        for m in merged_stocks:
            rec = m["recommendation"]
            badge_cls = REC_CLASS.get(rec, "badge-hold")
            risk = m["risk_level"]
            risk_cls = RISK_CLASS.get(risk, "")
            price_str = fmt_price(m.get("current_price"), m.get("currency", "USD"))
            cards.append(f"""
      <div class="card analysis-card">
        <div class="card-header">
          <div>
            <span class="ticker-tag">{m['ticker']}</span>
            <span class="card-name">{m['korean_name']}</span>
          </div>
          <div class="card-badges">
            <span class="badge {badge_cls}">{rec} {REC_EN.get(rec, '')}</span>
            <span class="risk-badge {risk_cls}">위험도 {risk}</span>
          </div>
        </div>
        <div class="card-body">
          <div class="price-grid">
            <div class="price-item">
              <span class="price-label">현재가</span>
              <span class="price-value">{price_str}</span>
            </div>
            <div class="price-item">
              <span class="price-label">목표가</span>
              <span class="price-value highlight">{m['target_price_text']}</span>
            </div>
            <div class="price-item">
              <span class="price-label">매수 타이밍</span>
              <span class="price-value entry">{m['entry_price_text']}</span>
            </div>
          </div>
          <div class="rationale">{m['rationale']}</div>
          <div class="risk-note">⚠️ {m['risk_note']}</div>
        </div>
      </div>""")
        return "".join(cards)

    def group_summary_cards() -> str:
        cards = []
        for g in analysis.get("group_summaries", []):
            cards.append(f"""
      <div class="group-card">
        <h3>{g.get('group', '')}</h3>
        <p>{g.get('summary', '')}</p>
      </div>""")
        return "".join(cards)

    def takeaway_items() -> str:
        items = []
        icons = ["🥇", "🥈", "🥉"]
        for i, tip in enumerate(analysis.get("key_takeaways", [])):
            icon = icons[i] if i < len(icons) else "•"
            items.append(f'<li><span class="icon">{icon}</span>{tip}</li>')
        return "".join(items)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>📈 일일 주식 분석 리포트 — {date_str}</title>
  <style>
    :root {{
      --bg: #f0f4f8;
      --surface: #ffffff;
      --surface2: #f8fafc;
      --border: #e2e8f0;
      --text: #1e293b;
      --text-muted: #64748b;
      --buy: #059669;
      --buy-bg: #d1fae5;
      --hold: #d97706;
      --hold-bg: #fef3c7;
      --sell: #dc2626;
      --sell-bg: #fee2e2;
      --accent: #3b82f6;
      --accent-dark: #1d4ed8;
      --radius: 12px;
      --shadow: 0 1px 3px rgba(0,0,0,.08), 0 4px 16px rgba(0,0,0,.06);
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 0 0 60px;
    }}

    /* Header */
    .header {{
      background: linear-gradient(135deg, #1e3a5f 0%, #2d6a9f 100%);
      color: white;
      padding: 36px 24px 28px;
      text-align: center;
    }}
    .header h1 {{ font-size: clamp(1.4rem, 4vw, 2rem); font-weight: 700; letter-spacing: -.5px; }}
    .header .meta {{ margin-top: 8px; opacity: .8; font-size: .9rem; }}

    /* Layout */
    .container {{ max-width: 1100px; margin: 0 auto; padding: 0 16px; }}
    section {{ margin-top: 32px; }}
    section h2 {{
      font-size: 1.1rem; font-weight: 700; color: var(--text);
      margin-bottom: 14px; padding-bottom: 8px;
      border-bottom: 2px solid var(--border);
      display: flex; align-items: center; gap: 8px;
    }}

    /* Stat cards */
    .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 24px; }}
    .stat-card {{
      background: var(--surface); border-radius: var(--radius);
      padding: 18px 16px; text-align: center;
      box-shadow: var(--shadow);
    }}
    .stat-card .stat-num {{ font-size: 2rem; font-weight: 800; line-height: 1.1; }}
    .stat-card .stat-label {{ font-size: .78rem; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .05em; }}
    .stat-buy   .stat-num {{ color: var(--buy); }}
    .stat-hold  .stat-num {{ color: var(--hold); }}
    .stat-sell  .stat-num {{ color: var(--sell); }}
    .stat-total .stat-num {{ color: var(--accent); }}

    /* Badges */
    .badge {{
      display: inline-block; padding: 3px 10px; border-radius: 20px;
      font-size: .75rem; font-weight: 700; letter-spacing: .03em;
    }}
    .badge-buy  {{ background: var(--buy-bg);  color: var(--buy);  }}
    .badge-hold {{ background: var(--hold-bg); color: var(--hold); }}
    .badge-sell {{ background: var(--sell-bg); color: var(--sell); }}
    .risk-badge {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: .72rem; font-weight: 600; }}
    .risk-high {{ background: #fee2e2; color: #dc2626; }}
    .risk-mid  {{ background: #fef3c7; color: #d97706; }}
    .risk-low  {{ background: #d1fae5; color: #059669; }}

    /* Table */
    .table-wrap {{ overflow-x: auto; border-radius: var(--radius); box-shadow: var(--shadow); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--surface); font-size: .87rem; }}
    thead tr {{ background: #1e3a5f; color: white; }}
    thead th {{ padding: 12px 14px; text-align: left; font-weight: 600; white-space: nowrap; }}
    tbody tr {{ border-bottom: 1px solid var(--border); transition: background .15s; }}
    tbody tr:last-child {{ border-bottom: none; }}
    tbody tr:hover {{ background: var(--surface2); }}
    td {{ padding: 11px 14px; vertical-align: middle; }}
    .ticker-tag {{
      display: inline-block; background: #eff6ff; color: var(--accent-dark);
      padding: 2px 8px; border-radius: 6px; font-size: .78rem; font-weight: 700;
      font-family: 'SF Mono', 'Fira Code', monospace;
    }}
    .ticker-tag.small {{ font-size: .72rem; padding: 1px 6px; }}
    .name-cell {{ max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .price {{ font-weight: 600; font-family: 'SF Mono', monospace; font-size: .85rem; }}
    .entry-price {{ font-size: .82rem; color: var(--accent-dark); }}
    .positive {{ color: #dc2626; font-weight: 600; }}
    .negative {{ color: #2563eb; font-weight: 600; }}

    /* Bar chart */
    .chart-container {{
      background: var(--surface); border-radius: var(--radius);
      padding: 20px 24px; box-shadow: var(--shadow);
    }}
    .chart-row {{ display: flex; align-items: center; margin-bottom: 10px; gap: 12px; }}
    .chart-label {{ width: 150px; flex-shrink: 0; }}
    .chart-name {{ font-size: .78rem; font-weight: 600; color: var(--text); line-height: 1.3; }}
    .chart-bar-area {{ flex: 1; }}
    .bar-wrap {{ display: flex; align-items: center; gap: 8px; }}
    .bar {{
      height: 22px; border-radius: 4px; min-width: 4px;
      transition: width .4s ease;
    }}
    .bar-pos {{ background: linear-gradient(90deg, #059669, #34d399); }}
    .bar-neg {{ background: linear-gradient(90deg, #dc2626, #f87171); }}
    .bar-label {{ font-size: .82rem; font-weight: 600; color: var(--text); white-space: nowrap; }}
    .bar-na {{ font-size: .8rem; color: var(--text-muted); }}

    /* Analysis cards */
    .cards-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }}
    .card {{
      background: var(--surface); border-radius: var(--radius);
      box-shadow: var(--shadow); overflow: hidden;
    }}
    .analysis-card .card-header {{
      padding: 14px 18px; background: var(--surface2);
      border-bottom: 1px solid var(--border);
      display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;
    }}
    .card-name {{ font-size: .82rem; color: var(--text-muted); margin-top: 4px; }}
    .card-badges {{ display: flex; gap: 6px; align-items: center; flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end; }}
    .card-body {{ padding: 16px 18px; }}
    .price-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px; }}
    .price-item {{ text-align: center; }}
    .price-label {{ display: block; font-size: .7rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 4px; }}
    .price-value {{ display: block; font-weight: 700; font-size: .9rem; font-family: 'SF Mono', monospace; }}
    .price-value.highlight {{ color: var(--buy); }}
    .price-value.entry {{ color: var(--accent-dark); font-size: .82rem; }}
    .rationale {{ font-size: .85rem; color: var(--text); line-height: 1.65; margin-bottom: 10px; }}
    .risk-note {{
      font-size: .8rem; color: #92400e; background: #fef3c7;
      padding: 8px 12px; border-radius: 8px; line-height: 1.5;
    }}

    /* Group summaries */
    .group-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }}
    .group-card {{
      background: var(--surface); border-radius: var(--radius);
      padding: 20px; box-shadow: var(--shadow);
      border-left: 4px solid var(--accent);
    }}
    .group-card h3 {{ font-size: .95rem; font-weight: 700; margin-bottom: 8px; }}
    .group-card p {{ font-size: .85rem; color: var(--text-muted); line-height: 1.65; }}

    /* Takeaways */
    .takeaways {{
      background: linear-gradient(135deg, #1e3a5f, #2d6a9f);
      border-radius: var(--radius); padding: 24px 28px;
      box-shadow: var(--shadow);
    }}
    .takeaways h2 {{ color: white !important; border-color: rgba(255,255,255,.2) !important; }}
    .takeaways ul {{ list-style: none; }}
    .takeaways li {{
      display: flex; align-items: flex-start; gap: 12px;
      color: rgba(255,255,255,.92); font-size: .9rem; padding: 8px 0;
      border-bottom: 1px solid rgba(255,255,255,.1);
    }}
    .takeaways li:last-child {{ border-bottom: none; }}
    .takeaways .icon {{ font-size: 1.1rem; flex-shrink: 0; }}

    /* Footer */
    .footer {{
      text-align: center; margin-top: 40px;
      font-size: .78rem; color: var(--text-muted);
    }}
    .footer a {{ color: var(--accent); text-decoration: none; }}

    /* Responsive */
    @media (max-width: 640px) {{
      .stats {{ grid-template-columns: repeat(2, 1fr); }}
      .price-grid {{ grid-template-columns: 1fr 1fr; }}
      .price-grid .price-item:last-child {{ grid-column: 1 / -1; }}
      .chart-label {{ width: 110px; }}
      .cards-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>

<div class="header">
  <h1>📈 글로벌 주식 &amp; ETF 일일 분석 리포트</h1>
  <div class="meta">{date_str} &nbsp;·&nbsp; 생성: {generated_at} &nbsp;·&nbsp; Claude AI 분석</div>
</div>

<div class="container">

  <!-- Stats -->
  <div class="stats">
    <div class="stat-card stat-total">
      <div class="stat-num">{len(stocks)}</div>
      <div class="stat-label">추적 종목</div>
    </div>
    <div class="stat-card stat-buy">
      <div class="stat-num">{buy_count}</div>
      <div class="stat-label">매수 추천</div>
    </div>
    <div class="stat-card stat-hold">
      <div class="stat-num">{hold_count}</div>
      <div class="stat-label">보유 / 관망</div>
    </div>
    <div class="stat-card stat-sell">
      <div class="stat-num">{sell_count}</div>
      <div class="stat-label">매도 / 비중축소</div>
    </div>
  </div>

  <!-- Summary table -->
  <section>
    <h2>📊 종목 요약</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>티커</th>
            <th>종목명</th>
            <th>의견</th>
            <th>현재가</th>
            <th>목표가</th>
            <th>매수 타이밍</th>
            <th>5일 수익률</th>
            <th>위험도</th>
          </tr>
        </thead>
        <tbody>
          {summary_rows()}
        </tbody>
      </table>
    </div>
  </section>

  <!-- Upside chart -->
  <section>
    <h2>📐 상승 여력 (애널리스트 목표가 기준)</h2>
    <div class="chart-container">
      {chart_rows()}
    </div>
  </section>

  <!-- Analysis cards -->
  <section>
    <h2>🔍 종목별 상세 분석</h2>
    <div class="cards-grid">
      {analysis_cards()}
    </div>
  </section>

  <!-- Group summaries -->
  <section>
    <h2>🗂️ 그룹별 총평</h2>
    <div class="group-grid">
      {group_summary_cards()}
    </div>
  </section>

  <!-- Key takeaways -->
  <section>
    <div class="takeaways">
      <h2>💡 핵심 요약</h2>
      <ul>
        {takeaway_items()}
      </ul>
    </div>
  </section>

  <div class="footer">
    <p>⚠️ 본 리포트는 정보 제공 목적이며 투자 권유가 아닙니다. 투자 결정은 본인 책임하에 이루어져야 합니다.</p>
    <p style="margin-top:6px">생성: {generated_at} &nbsp;|&nbsp; Powered by Claude AI &amp; yfinance</p>
  </div>

</div>
</body>
</html>"""


def upload_to_github(html_content: str) -> None:
    """Upload daily_report.html to GitHub repo Changhwa80/investment via the GitHub API.
    Requires GITHUB_TOKEN environment variable (Personal Access Token with repo scope).
    """
    import base64
    import urllib.request
    import urllib.error
    from datetime import datetime

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("\n⚠️  GITHUB_TOKEN이 설정되지 않아 GitHub 업로드를 건너뜁니다.")
        print("   Replit Secrets에 GITHUB_TOKEN을 추가하면 자동 업로드가 활성화됩니다.")
        return

    repo = "Changhwa80/investment"
    path = "daily_report.html"
    api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Step 1: get current file SHA (required by GitHub API to update an existing file)
    sha = None
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            sha = json.loads(resp.read().decode()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("  ℹ️  GitHub에 파일이 없으므로 신규 생성합니다.")
        else:
            print(f"\n⚠️  GitHub SHA 조회 실패: {e.code} {e.reason}")
            return

    # Step 2: PUT updated file
    from datetime import timezone as _tz, timedelta as _td
    _KST = _tz(_td(hours=9))
    now_str = datetime.now(_KST).strftime("%Y-%m-%d %H:%M KST")
    payload: dict = {
        "message": f"daily_report.html 업데이트 — {now_str}",
        "content": base64.b64encode(html_content.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha

    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(api_url, data=body, headers=headers, method="PUT")
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            html_url = result.get("content", {}).get("html_url", api_url)
            print(f"\n✅ GitHub 업로드 완료: {html_url}")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"\n⚠️  GitHub 업로드 실패: {e.code} {e.reason}")
        print(f"   상세: {error_body[:200]}")


def save_daily_report_html(stocks: list[dict], analysis: dict) -> None:
    from datetime import datetime, timezone, timedelta
    import os as _os
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    date_str = now.strftime("%Y년 %m월 %d일 (%A)")
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S KST")

    html = build_html_report(stocks, analysis, generated_at, date_str)
    _os.makedirs(_os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    with open(REPORT_FILE_ROOT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ HTML 리포트가 저장되었습니다. ({date_str})")

    upload_to_github(html)


# ── Portfolio management ───────────────────────────────────────────────────────

def load_portfolio() -> dict:
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"stocks": [], "last_updated": None}


def save_portfolio(portfolio: dict) -> None:
    from datetime import datetime
    portfolio["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 포트폴리오가 '{PORTFOLIO_FILE}'에 저장되었습니다.")


def show_portfolio_menu(stocks: list[dict]) -> None:
    ticker_map = {s["ticker"]: s["name"] for s in stocks}
    portfolio = load_portfolio()

    print("\n" + "=" * 60)
    print("💼 내 포트폴리오 관리")
    print("=" * 60)

    if portfolio["stocks"]:
        print(f"\n현재 포트폴리오 ({len(portfolio['stocks'])}개 종목):")
        for entry in portfolio["stocks"]:
            name = ticker_map.get(entry["ticker"], entry.get("name", entry["ticker"]))
            print(f"  ✓ {entry['ticker']} — {name}")
        if portfolio.get("last_updated"):
            print(f"  (마지막 업데이트: {portfolio['last_updated']})")
    else:
        print("\n현재 포트폴리오가 비어 있습니다.")

    print("\n추적 중인 종목 목록:")
    for i, s in enumerate(stocks, 1):
        in_portfolio = any(e["ticker"] == s["ticker"] for e in portfolio["stocks"])
        mark = "✓" if in_portfolio else " "
        price_str = fmt_price(s["current_price"], s["currency"])
        print(f"  {i}. [{mark}] {s['ticker']:<14} {s['name'][:32]:<33} {price_str}")

    print("\n명령어:")
    print("  추가할 티커를 입력하세요 (예: NVDA, 005930.KS)")
    print("  'remove TICKER' — 종목 제거")
    print("  'clear'         — 포트폴리오 초기화")
    print("  'done'          — 저장 후 종료")
    print()

    changed = False
    while True:
        try:
            cmd = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료합니다.")
            break

        cmd_upper = cmd.upper()

        if cmd_upper == "DONE":
            if changed:
                save_portfolio(portfolio)
            else:
                print("변경 사항 없음. 종료합니다.")
            break
        elif cmd_upper == "CLEAR":
            portfolio["stocks"] = []
            changed = True
            print("⚠️  포트폴리오가 초기화되었습니다.")
        elif cmd_upper.startswith("REMOVE "):
            ticker = cmd[7:].strip().upper()
            match = next((t for t in ticker_map if t.upper() == ticker), None)
            if match and any(e["ticker"] == match for e in portfolio["stocks"]):
                portfolio["stocks"] = [e for e in portfolio["stocks"] if e["ticker"] != match]
                changed = True
                print(f"  ➖ {match} 제거됨.")
            else:
                print(f"  '{ticker}'은(는) 포트폴리오에 없습니다.")
        else:
            for raw in [t.strip() for t in cmd.split(",") if t.strip()]:
                match = next((t for t in ticker_map if t.upper() == raw.upper()), None)
                if not match:
                    print(f"  ⚠️  '{raw}'은(는) 추적 목록에 없는 종목입니다.")
                elif any(e["ticker"] == match for e in portfolio["stocks"]):
                    print(f"  '{match}'은(는) 이미 포트폴리오에 있습니다.")
                else:
                    portfolio["stocks"].append({"ticker": match, "name": ticker_map[match]})
                    changed = True
                    print(f"  ➕ {match} ({ticker_map[match]}) 추가됨.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("🔍 글로벌 주식 & ETF 분석기")
    print("=" * 60)
    print(f"\n총 {len(ALL_TICKERS)}개 종목 데이터를 가져오는 중...\n")

    stocks = []
    for ticker in ALL_TICKERS:
        print(f"  ⏳ {ticker} 조회 중...", end="\r")
        stocks.append(get_stock_data(ticker))
    print(" " * 50, end="\r")

    print_stock_table(stocks)

    print("\nClaude에게 분석을 요청하는 중...\n")
    analysis = get_recommendation(stocks)

    print("\n" + "=" * 60)
    print("📊 Claude의 매수/보유/매도 분석 (한국어)")
    print("=" * 60)
    print_recommendation_terminal(analysis)

    save_daily_report_html(stocks, analysis)

    show_portfolio_menu(stocks)


if __name__ == "__main__":
    main()
