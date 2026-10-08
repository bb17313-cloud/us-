import os
import json
import html
import time as time_mod
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import time, datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import re

# جلب بيانات الاعتماد من GitHub Secrets أو بيئة العمل
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# هوية الطلبات لموقع SEC (مطلوبة منهم) - يفضل وضع بريدك في Secret باسم SEC_USER_AGENT
SEC_HEADERS = {
    "User-Agent": os.environ.get("SEC_USER_AGENT", "USStockScanner/1.0 (contact@example.com)"),
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json",
}

# اسم ملف التخزين الدائم لسجل التنبيهات
HISTORY_FILE = "alerts_history.json"

US_STOCKS = [
    "NB", "RKLB", "LCUT", "QSI", "WWR", "QUBT", "EVLV", "QS", "CSCO", "GRRR",
    "RZLV", "CMPX", "PANW", "NNE", "S", "AUR", "ARAY", "ASTS", "KOPN", "SATL",
    "AVGO", "BIRK", "RGTI", "OKTA", "APG", "GEV", "MBOT", "KULR", "TPR", "OSRH",
    "CORZ", "TEM", "HEI", "IRIX", "ONDS", "BSM", "MLYS", "AGNT", "EBS", "SLI",
    "USAR", "CRMD", "SMR", "LAMR", "YOU", "WRAP", "AMBA", "ACHR", "NKE", "U",
    "LEN", "DHI", "NVAX", "ZENA", "MLTX", "SPCX", "MVST", "PL", "CVX", "INDO",
    "IBRX", "GDRX", "FSLR", "QBTS", "LMT", "HQ", "DVN", "QNC", "COR", "NVO",
    "CRM", "BBAI", "MSFT", "AA", "MUX", "ANRO", "GMAB", "EONR", "AISP", "TDC",
    "PLSE", "VRME", "DUOL", "NTSK", "TWST", "ITRG", "CF", "PCLA", "PPSI", "ZETA",
    "RPD", "DPRO", "BZAI", "APH", "INFQ", "SLDP", "MP", "RMBS", "TE", "ATEC",
    "INOD", "CMOPF", "YEXT", "LAC", "ALLE", "TYGO", "HIMX", "NVDA", "LAES", "CTMX", "LUNR"
]

# ---------------------------------------------------------------
# إعدادات الإفصاحات (SEC)
# ---------------------------------------------------------------
SEC_FORMS_AR = {
    "8-K": "تقرير حدث",
    "10-Q": "تقرير ربع سنوي",
    "10-K": "تقرير سنوي",
    "SCHEDULE 13G": "ملكية كبار المساهمين",
    "SCHEDULE 13D": "ملكية كبار المساهمين (نشط)",
    "SC 13G": "ملكية كبار المساهمين",
    "SC 13D": "ملكية كبار المساهمين (نشط)",
    "S-1": "تسجيل طرح",
    "S-3": "تسجيل طرح",
    "424B5": "نشرة طرح",
    "424B3": "نشرة طرح",
    "424B4": "نشرة طرح",
    "6-K": "تقرير أجنبي",
    "20-F": "تقرير سنوي أجنبي",
    "4": "معاملة مطّلع",
    "4/A": "معاملة مطّلع (تعديل)",
    "3": "إفصاح مطّلع أولي",
    "5": "تقرير مطّلع سنوي",
    "13F-HR": "حيازات مؤسساتية",
}

ISSUANCE_FORMS = {"S-1", "S-3", "424B5", "424B3", "424B4", "F-1", "F-3"}

SEC_8K_ITEMS_AR = {
    "1.01": "اتفاقية جوهرية",
    "2.02": "نتائج مالية",
    "2.03": "التزام مالي",
    "3.02": "بيع أسهم غير مسجل",
    "3.03": "تعديل حقوق المساهمين",
    "5.02": "تغيير في الإدارة",
    "5.03": "تعديل النظام الأساسي",
    "5.07": "نتائج تصويت",
    "7.01": "إفصاح عادل FD",
    "8.01": "أحداث أخرى",
}

_cik_map = None

def load_alert_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ خطأ في قراءة ملف الذاكرة: {e}")
            return {}
    return {}

def save_alert_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ خطأ في حفظ ملف الذاكرة: {e}")

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_power_trend_age(df, min_candles=20):
    if len(df) < min_candles:
        return 0
    ema20 = df['Close'].ewm(span=20, adjust=False).mean()
    sma50 = df['Close'].rolling(window=50).mean()
    rsi = calculate_rsi(df['Close'], period=14)
    is_power_trend = (df['Close'] > ema20) & (ema20 > sma50) & (rsi > 50)
    age = 0
    for flag in reversed(is_power_trend.values):
        if flag:
            age += 1
        else:
            break
    return age

def check_choch_change(df_15m):
    if len(df_15m) < 20:
        return False
    recent_structure_high = df_15m['High'].iloc[-15:-2].max()
    latest_close = df_15m['Close'].iloc[-1]
    return latest_close > recent_structure_high

def get_current_session(last_timestamp):
    try:
        if last_timestamp.tzinfo is None:
            ny_time = last_timestamp.tz_localize('UTC').tz_convert('America/New_York').time()
        else:
            ny_time = last_timestamp.tz_convert('America/New_York').time()
        if time(4, 0) <= ny_time < time(9, 30):
            return "🌅 ما قبل الافتتاح"
        elif time(9, 30) <= ny_time < time(16, 0):
            return "🔔 الجلسة الرسمية"
        elif time(16, 0) <= ny_time <= time(20, 0):
            return "🌙 ما بعد الإغلاق"
        else:
            return "💤 خارج الجلسة"
    except Exception:
        return "🌐 خارج الجلسة"

def format_volume(vol):
    if vol is None or (isinstance(vol, float) and np.isnan(vol)):
        return "N/A"
    try:
        vol = float(vol)
        if vol >= 1_000_000:
            return f"{vol / 1_000_000:.2f}M"
        if vol >= 1_000:
            return f"{vol / 1_000:.1f}K"
        return str(int(vol))
    except Exception:
        return "N/A"

# ---------------------------------------------------------------
# نقاط القوة والمخاطر (موجز عام لكل الأسهم بدون تمييز)
# ---------------------------------------------------------------
def get_strengths_risks(stock, ticker):
    """
    يبني ملخصًا موجزًا لنقاط القوة والمخاطر لأي سهم.
    يعتمد على البيانات المالية + ملخص النشاط + مؤشرات النمو والمشاريع المستقبلية.
    لا يوجد أي تمييز أو نقاط ثابتة لرمز معين.
    """
    info = stock.info or {}
    strengths = []
    risks = []

    # ---- بيانات أساسية ----
    market_cap = info.get("marketCap")
    total_cash = info.get("totalCash")
    total_debt = info.get("totalDebt")
    revenue = info.get("totalRevenue") or info.get("revenue")
    beta = info.get("beta")
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    profit_margins = info.get("profitMargins")
    free_cashflow = info.get("freeCashflow")
    short_ratio = info.get("shortRatio")
    revenue_growth = info.get("revenueGrowth")
    earnings_growth = info.get("earningsGrowth")
    recommendation = (info.get("recommendationKey") or "").lower()
    target_mean = info.get("targetMeanPrice")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    sector = (info.get("sector") or "").strip()
    industry = (info.get("industry") or "").strip()
    summary = (info.get("longBusinessSummary") or info.get("longName") or "").lower()

    # ---- 1. السيولة والقيمة السوقية ----
    if market_cap:
        if market_cap >= 10_000_000_000:
            strengths.append(f"🏦 قيمة سوقية كبيرة ~{market_cap/1e9:.1f}$ مليار")
        elif market_cap >= 1_000_000_000:
            strengths.append(f"🏦 قيمة سوقية ~{market_cap/1e9:.1f}$ مليار")
        elif market_cap >= 300_000_000:
            strengths.append(f"🏦 قيمة سوقية متوسطة ~{market_cap/1e6:.0f}$ مليون")

    if total_cash and total_cash >= 50_000_000:
        cash_str = f"{total_cash/1e6:.0f}$ مليون" if total_cash < 1e9 else f"{total_cash/1e9:.1f}$ مليار"
        strengths.append(f"💰 نقدية قوية ~{cash_str}")

    # ---- 2. الديون ----
    if total_debt is not None:
        if total_debt < 5_000_000:
            strengths.append("✅ بدون ديون تُذكر")
        elif total_cash and total_cash > total_debt * 1.2:
            strengths.append("✅ نقدية تفوق الديون")
        elif total_debt > 0 and (not total_cash or total_debt > total_cash * 2):
            risks.append(f"📉 ديون مرتفعة نسبيًا")

    # ---- 3. الإيرادات والنمو ----
    if revenue and revenue > 0:
        if revenue_growth is not None:
            if revenue_growth > 0.25:
                strengths.append(f"📈 نمو إيرادات قوي (+{revenue_growth*100:.0f}%)")
            elif revenue_growth > 0.10:
                strengths.append(f"📈 نمو إيرادات إيجابي (+{revenue_growth*100:.0f}%)")
            elif revenue_growth < -0.10:
                risks.append(f"📉 تراجع في الإيرادات ({revenue_growth*100:.0f}%)")
        if profit_margins is not None:
            if profit_margins > 0.15:
                strengths.append(f"✅ هوامش ربح قوية ({profit_margins*100:.0f}%)")
            elif profit_margins < 0:
                risks.append("🔥 خسائر تشغيلية")
    else:
        risks.append("📉 إيرادات محدودة أو غير موجودة بعد")

    if earnings_growth is not None and earnings_growth > 0.20:
        strengths.append(f"📊 نمو أرباح ملحوظ (+{earnings_growth*100:.0f}%)")

    # ---- 4. التقييم ----
    pe = trailing_pe or forward_pe
    if pe:
        if pe > 100:
            risks.append("📈 تقييم مرتفع جدًا مقارنة بالإيرادات")
        elif pe > 50:
            risks.append("📈 تقييم مرتفع")
        elif 0 < pe < 20:
            strengths.append("✅ تقييم جذاب نسبيًا")

    # ---- 5. التذبذب والشورت ----
    if beta is not None:
        if beta > 2.0:
            risks.append("🎢 تذبذب شديد")
        elif beta > 1.5:
            risks.append("🎢 تذبذب مرتفع")
        elif beta < 0.8:
            strengths.append("🛡️ تذبذب منخفض نسبيًا")

    if short_ratio and short_ratio > 10:
        risks.append(f"🩳 نسبة شورت مرتفعة ({short_ratio:.1f})")

    # ---- 6. التدفق النقدي ----
    if free_cashflow is not None:
        if free_cashflow > 50_000_000:
            strengths.append("💵 تدفق نقدي حر إيجابي قوي")
        elif free_cashflow < -20_000_000:
            risks.append("🔥 حرق نقدي (تدفق حر سلبي)")

    # ---- 7. توصيات المحللين والهدف السعري ----
    if recommendation in ("buy", "strong_buy"):
        strengths.append("👍 توصيات محللين إيجابية")
    elif recommendation in ("sell", "strong_sell"):
        risks.append("👎 توصيات محللين سلبية")

    if target_mean and current_price and current_price > 0:
        upside = ((target_mean - current_price) / current_price) * 100
        if upside > 30:
            strengths.append(f"🎯 هدف سعري أعلى بكثير (+{upside:.0f}%)")
        elif upside < -15:
            risks.append(f"🎯 هدف سعري أقل من السعر الحالي")

    # ---- 8. مشاريع مستقبلية / محركات نمو من ملخص النشاط ----
    future_keywords = [
        "project", "pipeline", "development", "expand", "expansion", "contract",
        "partnership", "agreement", "launch", "commercial", "deployment",
        "construction", "build", "facility", "plant", "reactor", "satellite",
        "mission", "trial", "phase", "approval", "fda", "nrc", "backlog",
        "order", "award", "deal", "acquisition", "merge", "growth", "scale"
    ]
    risk_keywords = [
        "risk", "uncertainty", "delay", "lawsuit", "litigation", "investigation",
        "loss", "deficit", "dilution", "offering", "going concern", "bankruptcy",
        "competition", "regulatory", "volatile", "dependence", "single"
    ]

    found_future = []
    for kw in future_keywords:
        if kw in summary:
            found_future.append(kw)

    if found_future:
        # صياغة موجزة حسب الكلمات الموجودة
        if any(k in found_future for k in ("project", "pipeline", "development", "construction", "facility", "plant", "reactor")):
            strengths.append("🚀 مشاريع تطوير/إنشاء قيد التنفيذ")
        if any(k in found_future for k in ("contract", "partnership", "agreement", "deal", "award", "order", "backlog")):
            strengths.append("📝 عقود أو شراكات استراتيجية")
        if any(k in found_future for k in ("launch", "commercial", "deployment", "mission")):
            strengths.append("📡 خطط إطلاق/نشر تجاري")
        if any(k in found_future for k in ("trial", "phase", "approval", "fda", "nrc")):
            strengths.append("🧪 مراحل تجارب أو موافقات تنظيمية")
        if any(k in found_future for k in ("expand", "expansion", "scale", "growth")):
            strengths.append("📈 خطط توسع ونمو مستقبلي")

    found_risks_txt = [kw for kw in risk_keywords if kw in summary]
    if found_risks_txt:
        if any(k in found_risks_txt for k in ("lawsuit", "litigation", "investigation")):
            risks.append("⚖️ مخاطر قانونية أو تحقيقات")
        if any(k in found_risks_txt for k in ("delay", "uncertainty", "regulatory")):
            risks.append("⏳ تأخيرات أو عدم يقين تنظيمي")
        if "dilution" in found_risks_txt or "offering" in found_risks_txt:
            risks.append("📉 مخاطر تخفيف ملكية (إصدارات)")
        if "competition" in found_risks_txt:
            risks.append("⚔️ منافسة شديدة في القطاع")

    # ---- 9. إشارات من القطاع/الصناعة (عامة بدون تمييز رموز) ----
    sector_l = sector.lower()
    industry_l = industry.lower()
    if any(k in industry_l or k in sector_l for k in ("nuclear", "uranium", "energy")):
        strengths.append("⚡ قطاع مدعوم بالطلب المتزايد على الطاقة")
    if any(k in industry_l for k in ("semiconductor", "software", "artificial intelligence", "quantum", "cybersecurity")):
        strengths.append("💻 قطاع تقني ذو إمكانات نمو عالية")
    if any(k in industry_l for k in ("biotechnology", "pharmaceutical", "drug")):
        strengths.append("🧬 قطاع دوائي/حيوي ذو محفزات تنظيمية")
    if any(k in industry_l for k in ("aerospace", "defense", "satellite", "space")):
        strengths.append("🛰️ قطاع فضاء/دفاع استراتيجي")
    if any(k in industry_l for k in ("renewable", "solar", "battery", "electric vehicle")):
        strengths.append("🌱 قطاع طاقة نظيفة مدعوم بالتحول العالمي")

    # ---- ضمان وجود نقاط ----
    if not strengths:
        strengths.append("📊 بيانات أساسية متاحة للتحليل الفني والأساسي")
    if not risks:
        risks.append("⚠️ مخاطر السوق العامة والتذبذب الطبيعي")

    # الحد الأقصى للنقاط (موجز)
    return strengths[:5], risks[:4]


# ---------------------------------------------------------------
# إفصاحات SEC
# ---------------------------------------------------------------
def get_cik(ticker):
    global _cik_map
    if _cik_map is None:
        _cik_map = {}
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers=SEC_HEADERS, timeout=15)
            r.raise_for_status()
            for item in r.json().values():
                _cik_map[item["ticker"].upper()] = str(item["cik_str"]).zfill(10)
        except Exception as e:
            print(f"⚠️ تعذر تحميل خريطة CIK: {e}")
    return _cik_map.get(ticker.upper().replace("-", "."))

def get_sec_filings(ticker, days=90, limit=30):
    cik = get_cik(ticker)
    if not cik:
        print(f"⚠️ لا يوجد CIK لـ {ticker}")
        return []
    try:
        time_mod.sleep(0.3)
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=SEC_HEADERS, timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        if not recent or "form" not in recent:
            print(f"⚠️ لا توجد إفصاحات حديثة لـ {ticker}")
            return []
    except Exception as e:
        print(f"⚠️ تعذر جلب إفصاحات {ticker}: {e}")
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    cik_int = str(int(cik))
    forms_list = recent.get("form", [])
    dates_list = recent.get("filingDate", [])
    acc_list = recent.get("accessionNumber", [])
    doc_list = recent.get("primaryDocument", [])
    items_list = recent.get("items", [""] * len(forms_list))

    filings = []
    for i in range(len(forms_list)):
        form = forms_list[i].strip()
        date = dates_list[i]
        if date < cutoff:
            break
        if form not in SEC_FORMS_AR:
            continue
        acc = acc_list[i].replace("-", "")
        doc = doc_list[i] if i < len(doc_list) else ""
        items_raw = items_list[i] if i < len(items_list) else ""
        filings.append({
            "form": form,
            "date": date,
            "items": [x.strip() for x in str(items_raw).split(",") if x.strip()],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}",
            "accession": acc_list[i],
        })
        if len(filings) >= limit:
            break
    print(f"📄 {ticker}: SEC filings = {len(filings)}")
    return filings

def format_filing_line(f):
    form = f["form"]
    label = SEC_FORMS_AR.get(form, form)
    if form == "8-K":
        names = [SEC_8K_ITEMS_AR[i] for i in f["items"] if i in SEC_8K_ITEMS_AR]
        if names:
            label = "/".join(names[:2])
    elif form in ("4", "4/A"):
        label = "شراء مطّلع"
    elif form in ISSUANCE_FORMS:
        label = "طرح"
    return (
        f'• <a href="{f["url"]}">{html.escape(form)}</a> '
        f'<code>{f["date"]}</code> {html.escape(label)}'
    )

# ---------------------------------------------------------------
# الإصدارات · المحفزات · تجزئة عكسية / توزيعات
# ---------------------------------------------------------------
def get_issuance_lines(filings, limit=3):
    lines = []
    seen = set()
    for f in filings:
        form = f["form"]
        key = (form, f["date"])
        if key in seen:
            continue
        if form in ISSUANCE_FORMS:
            seen.add(key)
            lines.append(
                f'• <a href="{f["url"]}">{html.escape(form)}</a> '
                f'طرح/إصدار <code>{f["date"]}</code>'
            )
        elif form == "8-K" and "3.02" in f["items"]:
            seen.add(key)
            lines.append(
                f'• <a href="{f["url"]}">8-K</a> '
                f'بيع أسهم غير مسجل <code>{f["date"]}</code>'
            )
        if len(lines) >= limit:
            break
    if not lines:
        for f in filings:
            if f["form"] == "6-K":
                lines.append(
                    f'• <a href="{f["url"]}">6-K</a> '
                    f'تقرير أجنبي <code>{f["date"]}</code>'
                )
                break
    return lines


def get_corporate_action_lines(stock, filings, news_items):
    lines = []
    found_split = False
    found_div = False

    for f in filings:
        if found_split:
            break
        if f["form"] == "8-K" and any(i in ("3.03", "5.03") for i in f["items"]):
            lines.append(
                f'📉 تجزئة عكسية: <a href="{f["url"]}">8-K</a> <code>{f["date"]}</code>'
            )
            found_split = True

    if not found_split:
        for n in news_items:
            t = n["title"].lower()
            if "reverse split" in t or "reverse stock split" in t:
                lines.append(
                    f'📉 تجزئة عكسية: '
                    f'<a href="{n["url"]}">{html.escape(n["title"][:55])}</a> '
                    f'<code>{n["date"]}</code>'
                )
                found_split = True
                break

    try:
        info = stock.info or {}
        div_rate = info.get("dividendRate") or info.get("trailingAnnualDividendRate")
        div_yield = info.get("dividendYield")
        ex_div = info.get("exDividendDate")

        if div_rate and float(div_rate) > 0:
            yield_str = ""
            if div_yield is not None:
                y = float(div_yield)
                if y < 0.15:
                    y = y * 100
                if 0 < y <= 20:
                    yield_str = f" | {y:.2f}%"
            date_str = ""
            if ex_div:
                try:
                    if isinstance(ex_div, (int, float)):
                        d = datetime.fromtimestamp(int(ex_div), tz=timezone.utc).strftime("%Y-%m-%d")
                    else:
                        d = str(ex_div)[:10]
                    date_str = f" | <code>{d}</code>"
                except Exception:
                    pass
            lines.append(
                f'💰 توزيعات <code>${float(div_rate):.2f}</code>{yield_str}{date_str}'
            )
            found_div = True
    except Exception:
        pass

    if not found_div:
        for n in news_items:
            t = n["title"].lower()
            if any(k in t for k in ("dividend", "special dividend", "cash dividend")):
                lines.append(
                    f'💰 توزيعات: '
                    f'<a href="{n["url"]}">{html.escape(n["title"][:55])}</a> '
                    f'<code>{n["date"]}</code>'
                )
                break
    return lines


CATALYST_ITEMS_AR = {
    "2.02": "إعلان نتائج مالية/أرباح",
    "1.01": "اتفاقية جوهرية",
    "5.02": "تغيير في الإدارة",
    "3.02": "بيع أسهم غير مسجل",
    "2.03": "التزام مالي جديد",
}

NEWS_DAYS = 90
NEWS_SHOW = 3

def get_catalyst_lines(stock, filings):
    lines = []
    today = datetime.now(timezone.utc).date()
    done = set()
    for f in filings:
        if f["form"] != "8-K":
            continue
        for it in f["items"]:
            if it in CATALYST_ITEMS_AR and it not in done:
                done.add(it)
                lines.append(
                    f'• <a href="{f["url"]}">8-K</a> '
                    f'{CATALYST_ITEMS_AR[it]} <code>{f["date"]}</code>'
                )
    lines = lines[:3]

    try:
        cal = stock.calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        for d in (dates or []):
            d = d.date() if hasattr(d, "date") else d
            if 0 <= (d - today).days <= NEWS_DAYS:
                lines.append(f"📅 موعد النتائج: <code>{d}</code>")
                break
    except Exception:
        pass

    if not any("موعد النتائج" in l for l in lines):
        try:
            ts = (stock.info or {}).get("earningsTimestamp")
            if ts:
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                if 0 <= (d - today).days <= NEWS_DAYS:
                    lines.append(f"📅 موعد النتائج: <code>{d}</code>")
        except Exception:
            pass
    return lines

# ---------------------------------------------------------------
# الأخبار
# ---------------------------------------------------------------
def _news_from_yfinance(stock, days=7, limit=2):
    out = []
    try:
        items = stock.news or []
    except Exception as e:
        print(f"⚠️ yfinance news خطأ: {e}")
        return out
    now = datetime.now(timezone.utc)
    for it in items:
        try:
            c = it.get("content") or it
            title = c.get("title")
            url = ((c.get("canonicalUrl") or {}).get("url")
                   or (c.get("clickThroughUrl") or {}).get("url")
                   or it.get("link"))
            pub = c.get("pubDate") or it.get("providerPublishTime")
            if isinstance(pub, (int, float)):
                pub_dt = datetime.fromtimestamp(pub, tz=timezone.utc)
            else:
                pub_dt = datetime.fromisoformat(str(pub).replace("Z", "+00:00"))
            if not title or not url or (now - pub_dt).days > days:
                continue
            source = (c.get("provider") or {}).get("displayName") or it.get("publisher") or ""
            out.append({"title": title, "url": url, "date": pub_dt.strftime("%Y-%m-%d"), "source": source})
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out

def _news_from_rss(ticker, days=7, limit=2):
    out = []
    try:
        r = requests.get(
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        now = datetime.now(timezone.utc)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = item.findtext("pubDate")
            if not title or not link or not pub:
                continue
            pub_dt = parsedate_to_datetime(pub)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if (now - pub_dt).days > days:
                continue
            out.append({"title": title, "url": link, "date": pub_dt.strftime("%Y-%m-%d"), "source": "Yahoo Finance"})
            if len(out) >= limit:
                break
    except Exception as e:
        print(f"⚠️ RSS news خطأ لـ {ticker}: {e}")
    return out

IMPORTANT_KEYWORDS = [
    "earnings", "revenue", "guidance", "outlook", "forecast", "fda", "approval", "approves",
    "approved", "clearance", "contract", "award", "awarded", "order", "deal", "acquisition",
    "acquire", "acquires", "merger", "buyout", "partnership", "agreement", "offering",
    "raises", "upgrade", "downgrade", "price target", "lawsuit", "investigation", "bankruptcy",
    "split", "dividend", "buyback", "repurchase", "ceo", "launch", "trial", "phase", "patent",
    "beats", "misses", "record", "backlog", "ipo", "delist", "short report", "reverse split",
]

def news_importance(title):
    t = title.lower()
    return sum(1 for k in IMPORTANT_KEYWORDS if k in t)

def get_recent_news(stock, ticker, days=NEWS_DAYS, limit=NEWS_SHOW):
    pool = _news_from_yfinance(stock, days, 40) + _news_from_rss(ticker, days, 40)
    seen, unique = set(), []
    ticker_l = ticker.lower()
    company = ""
    try:
        company = ((stock.info or {}).get("shortName") or "").lower()
        company = company.split(",")[0].split(" ")[0] if company else ""
    except Exception:
        pass

    for n in pool:
        key = n["title"].strip().lower()
        if n["url"] in seen or key in seen:
            continue
        if ticker_l not in key and (not company or company not in key):
            if n.get("source") != "Yahoo Finance":
                continue
        seen.add(n["url"]); seen.add(key)
        n["score"] = news_importance(n["title"])
        unique.append(n)

    important = [n for n in unique if n["score"] > 0]
    important.sort(key=lambda n: (n["score"], n["date"]), reverse=True)
    top = important[:limit]
    top.sort(key=lambda n: n["date"], reverse=True)
    return top

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Secrets غير معرفة!")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload)

def scan_us_market():
    print("🚀 بدء المسح المتقدم عبر جميع الجلسات...")

    alert_history = load_alert_history()
    found_opportunities = 0

    for ticker in US_STOCKS:
        try:
            stock = yf.Ticker(ticker)

            df_4h_raw = stock.history(period="3mo", interval="1h", prepost=True)
            if df_4h_raw.empty or len(df_4h_raw) < 50:
                continue

            df_4h = df_4h_raw.resample('4h').agg({
                'Open': 'first',
                'High': 'max',
                'Low': 'min',
                'Close': 'last',
                'Volume': 'sum'
            }).dropna()

            df_4h['RSI'] = calculate_rsi(df_4h['Close'], period=14)
            rsi_4h = round(df_4h['RSI'].iloc[-1], 2)

            if pd.isna(rsi_4h) or rsi_4h <= 54:
                continue

            pt_age_4h = calculate_power_trend_age(df_4h)
            if pt_age_4h < 1:
                continue

            if pt_age_4h > 6:
                pt_4h_display = f"{pt_age_4h} شمعة ⚠️ متقدم"
            else:
                pt_4h_display = f"{pt_age_4h} شمعة ⚡"

            df_15m = stock.history(period="5d", interval="15m", prepost=True)
            if df_15m.empty or len(df_15m) < 20:
                continue

            current_session = get_current_session(df_15m.index[-1])
            pt_age_15m = calculate_power_trend_age(df_15m)

            latest_price = round(df_15m['Close'].iloc[-1], 2)
            prev_high = df_15m['High'].iloc[-3]
            current_low = df_15m['Low'].iloc[-1]

            has_fvg = current_low >= (prev_high * 0.997)

            if has_fvg:
                # لا تكرار إذا لم يتغير السعر (≥ 0.1%)
                if ticker in alert_history:
                    prev_price = alert_history[ticker].get('last_price', 0)
                    if prev_price and abs(latest_price - prev_price) / prev_price < 0.001:
                        continue

                found_opportunities += 1

                has_choch = check_choch_change(df_15m)
                choch_line = "\n⚡ اختراق هيكلي صاعد (CHOCH)" if has_choch else ""

                info = stock.info or {}
                sector = info.get('sector', 'غير محدد')
                industry = info.get('industry', 'غير محدد')

                volume = info.get('volume') or info.get('regularMarketVolume')
                if not volume:
                    try:
                        volume = int(df_15m['Volume'].iloc[-20:].sum())
                    except Exception:
                        volume = None
                vol_str = format_volume(volume)

                change_pct = info.get('regularMarketChangePercent')
                if change_pct is None:
                    try:
                        prev_close = info.get('regularMarketPreviousClose') or info.get('previousClose')
                        if prev_close and prev_close > 0:
                            change_pct = ((latest_price - prev_close) / prev_close) * 100
                    except Exception:
                        change_pct = None
                if change_pct is not None:
                    sign = "+" if change_pct >= 0 else ""
                    change_str = f"{sign}{change_pct:.2f}%"
                else:
                    change_str = "N/A"

                high_52w = info.get('fiftyTwoWeekHigh', df_4h_raw['High'].max())
                if high_52w and isinstance(high_52w, (int, float)) and high_52w > 0:
                    high_pct = round(((latest_price - high_52w) / high_52w) * 100, 1)
                    high_52w_str = f"${round(high_52w, 2)} ({high_pct}%)"
                else:
                    high_52w_str = "N/A"

                low_52w = info.get('fiftyTwoWeekLow', df_4h_raw['Low'].min())
                if low_52w and isinstance(low_52w, (int, float)) and low_52w > 0:
                    low_pct = round(((latest_price - low_52w) / low_52w) * 100, 1)
                    low_sign = "+" if low_pct > 0 else ""
                    low_52w_str = f"${round(low_52w, 2)} ({low_sign}{low_pct}%)"
                else:
                    low_52w_str = "N/A"

                stop_loss = round(df_15m['Low'].iloc[-5:].min(), 2)
                target1 = round(latest_price * 1.02, 2)
                target_max = round(latest_price * 1.05, 2)

                if ticker in alert_history:
                    prev_data = alert_history[ticker]
                    alert_num = prev_data['count'] + 1
                    prev_price = prev_data['last_price']
                    price_change = ((latest_price - prev_price) / prev_price) * 100

                    if price_change >= 3.0:
                        alert_line = f"🚀 <b>تنبيه ({alert_num}) - زخم ⚡ +{price_change:.1f}%</b>"
                    elif price_change >= 2.0:
                        alert_line = f"⚡ <b>تنبيه ({alert_num}) - تسارع 🔥 +{price_change:.1f}%</b>"
                    elif price_change >= 1.0:
                        alert_line = f"📈 <b>تنبيه ({alert_num}) - ارتفاع 🟢 +{price_change:.1f}%</b>"
                    else:
                        alert_line = f"🔔 <b>تنبيه ({alert_num}) - مكرر</b>"

                    alert_history[ticker] = {'count': alert_num, 'last_price': latest_price}
                else:
                    alert_history[ticker] = {'count': 1, 'last_price': latest_price}
                    alert_line = "⚡ <b>تنبيه (1)</b>"

                save_alert_history(alert_history)

                tv_url = f"https://www.tradingview.com/chart/?symbol={ticker}"
                sec_browse = (
                    f"https://www.sec.gov/cgi-bin/browse-edgar"
                    f"?action=getcompany&CIK={ticker}&type=&dateb=&owner=include&count=40"
                )

                all_filings = get_sec_filings(ticker, limit=40)
                filings_display = all_filings[:4]
                catalyst_lines = get_catalyst_lines(stock, all_filings)
                news_items = get_recent_news(stock, ticker)
                issuance_lines = get_issuance_lines(all_filings)
                corp_action_lines = get_corporate_action_lines(stock, all_filings, news_items)

                # ---- نقاط القوة والمخاطر (عامة لكل سهم) ----
                strengths, risks = get_strengths_risks(stock, ticker)
                strength_lines = "\n".join(f"• {s}" for s in strengths)
                risk_lines = "\n".join(f"• {r}" for r in risks)

                print(
                    f"ℹ️ {ticker}: أخبار={len(news_items)} | محفزات={len(catalyst_lines)} "
                    f"| إصدارات={len(issuance_lines)} | إجراءات={len(corp_action_lines)} | SEC={len(filings_display)}"
                )

                # ---- بناء البطاقة المنظمة ----
                extra_parts = []

                # نقاط القوة والمخاطر
                extra_parts.append(
                    f"💪 <b>نقاط القوة</b>\n{strength_lines}\n\n"
                    f"⚠️ <b>المخاطر</b>\n{risk_lines}"
                )

                if corp_action_lines:
                    extra_parts.append("\n".join(corp_action_lines))

                if issuance_lines:
                    extra_parts.append("📋 <b>الإصدارات</b>\n" + "\n".join(issuance_lines[:2]))

                # المحفزات (مترجمة + روابط)
                cat_lines = []
                if catalyst_lines:
                    cat_lines.extend(catalyst_lines[:3])
                if news_items:
                    for n in news_items[:2]:
                        cat_lines.append(
                            f'• <a href="{n["url"]}">{html.escape(n["title"][:60])}</a> <code>{n["date"]}</code>'
                        )
                if cat_lines:
                    extra_parts.append("⚡ <b>المحفزات</b>\n" + "\n".join(cat_lines))
                else:
                    extra_parts.append("⚡ <b>المحفزات</b>\nلا توجد محفزات حديثة")

                # آخر 4 إفصاحات SEC مهمة (مترجمة + روابط)
                if filings_display:
                    sec_lines = [format_filing_line(f) for f in filings_display[:4]]
                    extra_parts.append("📄 <b>إفصاحات SEC</b>\n" + "\n".join(sec_lines))
                else:
                    extra_parts.append("📄 <b>إفصاحات SEC</b>\nلا توجد إفصاحات حديثة")

                extra_parts.append(
                    f'🔗 <a href="{sec_browse}">SEC</a> · <a href="{tv_url}">TradingView</a>'
                )

                extra_text = ("\n\n" + "\n\n".join(extra_parts)) if extra_parts else ""

                msg = f"""{current_session} | {alert_line}

<b>الرمز:</b> {html.escape(ticker)}
<b>القطاع:</b> {html.escape(str(sector))}
<b>الصناعة:</b> {html.escape(str(industry))}
<b>السعر الحالي:</b> <code>${latest_price}</code>
<b>الحجم Vol:</b> <code>{vol_str}</code>
<b>التغيير:</b> <code>{change_str}</code>
<b>قمة 52 أسبوع:</b> <code>{high_52w_str}</code>
<b>قاع 52 أسبوع:</b> <code>{low_52w_str}</code>
<b>RSI:</b> <code>{rsi_4h}</code>
<b>Power Trend 4H:</b> <code>{pt_4h_display}</code>
<b>Power Trend 15M:</b> <code>{pt_age_15m} شمعة</code>{choch_line}

🎯 <b>الأهداف:</b> <code>${target1}</code> ← <code>${target_max}</code>
⛔ <b>وقف الخسارة:</b> <code>${stop_loss}</code>{extra_text}"""

                send_telegram(msg)
                print(f"✅ تم إرسال تنبيه للسهم {ticker} | الجلسة: {current_session} | سعر: ${latest_price}")

        except Exception as e:
            print(f"❌ خطأ في فحص {ticker}: {e}")

    if found_opportunities == 0:
        print("ℹ️ لا توجد أسهم تطابق الشروط حالياً.")

if __name__ == "__main__":
    scan_us_market()
