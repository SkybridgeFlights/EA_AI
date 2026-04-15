# -*- coding: utf-8 -*-
"""
tools/optimize_full.py

Backtest + Multi-Param Optimization (Python) لمحاكاة الإكسبرت:
- Signals: EMA crossover + RSI filter
- SL=ATR*mult, TP=SL*RR
- Trailing / BreakEven / PartialClose
- Regime Detector (مبسّط) لتبديل RR/TS/BE/Risk
- RiskGovernor مبسّط (حد أقصى خسارة يومية/تعرض)
- News window block (اختياري عبر CSV)
- Spread/Commission aware
- GridSearch على عدة متغيرات أساسية
- يخرج: artifacts/bt_results_YYYYMMDD_HHMMSS.csv + artifacts/best_result_*.json
- (اختياري) يكتب runtime/live_config.json للتجربة على MT5

تشغيل مثال:
python tools/optimize_full.py ^
  --price data\\XAUUSD_H1.csv --symbol XAUUSD ^
  --start 2023-01-01 --end 2025-01-01 ^
  --outdir artifacts --grid medium ^
  --write-live-config 1
"""
from __future__ import annotations

import os
import sys
import json
import math
import argparse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd


# --------- تهيئة مسار المشروع لاستيراد app/ml/features ----------
THIS = Path(__file__).resolve()
PROJ = THIS.parents[1]  # ..\ (جذر المشروع المفترض C:\EA_AI)
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

# محاولة استيراد make_features/make_labels من مشروعك
try:
    from app.ml.features import make_features, make_labels  # type: ignore
except Exception:
    # نسخة احتياطية مبسطة إذا تعذر الاستيراد (لا توقف السكربت)
    def make_features(df_price: pd.DataFrame, df_news: pd.DataFrame) -> pd.DataFrame:
        df = df_price.copy()
        if "Time" in df.columns:
            df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce")
            df = df.dropna(subset=["Time"]).sort_values("Time").set_index("Time")
        else:
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, utc=True, errors="coerce")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")

        close = pd.to_numeric(df["Close"], errors="coerce")
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        rsi = _fallback_rsi(close, 14)
        atr = _fallback_atr(
            pd.to_numeric(df["High"], errors="coerce"),
            pd.to_numeric(df["Low"], errors="coerce"),
            close, 14
        )
        out = pd.DataFrame({
            "Close": close,
            "ema20": ema20,
            "ema50": ema50,
            "rsi14": rsi,
            "atr14": atr
        }, index=df.index).ffill().fillna(0.0)
        return out

    def make_labels(df_price: pd.DataFrame, horizon: int = 6) -> pd.Series:
        return pd.Series(0, index=df_price.index, dtype=int)

    def _fallback_rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        up = delta.clip(lower=0.0)
        down = (-delta.clip(upper=0.0))
        avg_gain = up.ewm(alpha=1.0 / period, adjust=False).mean()
        avg_loss = down.ewm(alpha=1.0 / period, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-12)
        return 100.0 - (100.0 / (1.0 + rs))

    def _fallback_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        prev_close = close.shift(1)
        tr = pd.concat([(high - low).abs(),
                        (high - prev_close).abs(),
                        (low - prev_close).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# -------------------------- إعدادات عامة --------------------------
DEFAULT_COMMISSION_PER_LOT = 7.0     # USD (تقريبي)
DEFAULT_SPREAD_PTS         = 200.0   # نقاط (يمكن تغييره من CLI)
POINTS_PER_PIP_GOLD        = 10.0    # للذهب غالباً (اضبطه حسب وسيطك إن لزم)


# -------------------------- بيانات الأخبار (اختياري) --------------
def load_news_csv(path: Optional[str]) -> pd.DataFrame:
    """CSV بأعمدة: time, currency, impact(1..3)"""
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    if "time" not in df.columns:
        return pd.DataFrame()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time")
    return df[["time", "currency", "impact"]].copy()


def in_news_window(ts: pd.Timestamp, df_news: pd.DataFrame,
                   before_min: int, after_min: int, min_impact: int) -> bool:
    if df_news is None or df_news.empty:
        return False
    start = ts - pd.Timedelta(minutes=before_min)
    end   = ts + pd.Timedelta(minutes=after_min)
    win = df_news.loc[(df_news["time"] >= start) & (df_news["time"] <= end)]
    if win.empty:
        return False
    if min_impact is not None and min_impact > 1:
        win = win[pd.to_numeric(win["impact"], errors="coerce") >= min_impact]
    return not win.empty


# -------------------------- بيانات الأسعار ------------------------
def load_price_csv(path: str, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "Time" not in df.columns:
        raise SystemExit("CSV يجب أن يحوي عمود Time + Open/High/Low/Close")
    df["Time"] = pd.to_datetime(df["Time"], utc=True, errors="coerce")
    df = df.dropna(subset=["Time"]).sort_values("Time")
    if start:
        df = df[df["Time"] >= pd.to_datetime(start, utc=True)]
    if end:
        df = df[df["Time"] <= pd.to_datetime(end, utc=True)]
    df = df.reset_index(drop=True)
    return df


# -------------------------- أدوات قياس ----------------------------
@dataclass
class Metrics:
    trades: int
    winrate: float
    pf: float
    net_profit: float
    max_dd: float
    avg_R: float


def compute_metrics(pnl_list: List[float]) -> Metrics:
    if not pnl_list:
        return Metrics(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    pnl = np.array(pnl_list, dtype=float)
    wins = pnl[pnl > 0.0]
    losses = pnl[pnl < 0.0]
    trades = int((pnl != 0.0).sum())
    winrate = float(round(100.0 * len(wins) / trades, 2)) if trades > 0 else 0.0
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = float(round(gross_win / gross_loss, 3)) if gross_loss > 0 else (0.0 if trades == 0 else float("inf"))
    netp = float(pnl.sum())

    # Drawdown
    eq = pnl.cumsum()
    peak = -1e18
    mdd = 0.0
    for v in eq:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd

    avg_R = float(round(netp / (abs(losses).sum() + 1e-9), 3)) if len(losses) > 0 else 0.0
    return Metrics(trades, winrate, (0 if pf == float("inf") else pf), netp, float(round(mdd, 2)), avg_R)


# -------------------------- محرك التداول --------------------------
@dataclass
class EngineParams:
    # Signals
    use_ma: bool = True
    ma_fast: int = 20
    ma_slow: int = 50
    use_rsi: bool = True
    rsi_period: int = 14
    rsi_buy_max: float = 70.0   # مرفوعة لتسهيل الدخول
    rsi_sell_min: float = 30.0  # مخفّضة لتسهيل الدخول

    # Risk / cost
    risk_pct: float = 0.8
    commission_per_lot: float = DEFAULT_COMMISSION_PER_LOT
    spread_pts: float = DEFAULT_SPREAD_PTS
    point_value_usd_per_lot: float = 1.0  # قيمة النقطة لكل لوت (تقدير خام)

    # SL/TP
    atr_period: int = 14
    atr_mult: float = 1.8
    rr: float = 2.0

    # Trade control
    max_open_per_symbol: int = 1
    max_trades_per_day: int = 10
    min_trade_gap_sec: int = 10
    max_spread_pts: int = 600  # مرفوعة لتفادي الرفض المفرط

    # Trailing / BE / Partial
    use_trailing: bool = True
    ts_start: int = 250
    ts_step: int = 80

    use_be: bool = True
    be_trig: int = 80
    be_offs: int = 15

    use_pc: bool = False
    pc_trig: int = 400
    pc_frac: float = 0.5

    # Regime (مبسّط)
    use_regime: bool = True
    rd_highvol_mult: float = 2.2
    rd_slope_thresh_pts: float = 50.0  # ميل EMA سريع (بالنقاط)

    rr_trend: float = 2.4
    ts_start_tr: int = 250
    ts_step_tr: int = 80
    be_trig_tr: int = 80
    be_offs_tr: int = 15
    risk_mult_tr: float = 1.2

    rr_range: float = 1.6
    ts_start_rg: int = 180
    ts_step_rg: int = 60
    be_trig_rg: int = 60
    be_offs_rg: int = 10
    risk_mult_rg: float = 0.9

    rr_highvol: float = 2.8
    ts_start_hv: int = 350
    ts_step_hv: int = 120
    be_trig_hv: int = 120
    be_offs_hv: int = 30
    risk_mult_hv: float = 0.7

    # News filter (افتراضيًا مُعطّل لضمان صفقات في أول اختبار)
    use_news: bool = False
    cal_before_min: int = 5
    cal_after_min: int = 5
    cal_min_impact: int = 2

    # Session
    use_session: bool = False
    sess_start_h: int = 7
    sess_end_h: int = 22
    gmt_offset_min: int = 0

    # Risk governor (مبسّط)
    use_rg: bool = True
    daily_loss_pct_soft: float = 3.0
    daily_loss_pct_hard: float = 5.0
    equity_dd_halt_pct: float = 12.0
    max_exposure_lots: float = 5.0
    rg_risk_min_pct: float = 0.2
    rg_risk_max_pct: float = 1.2
    atr_norm_pts: float = 800.0
    scale_risk_by_atr: bool = True


@dataclass
class Trade:
    direction: int          # +1/-1
    open_px: float
    lots: float
    sl_pts: float
    tp_pts: float
    open_time: pd.Timestamp
    stop_loss: float
    take_profit: float


class Backtester:
    def __init__(self,
                 df_price: pd.DataFrame,
                 df_news: pd.DataFrame,
                 symbol: str,
                 params: EngineParams,
                 balance: float = 10000.0,
                 point: float = 0.01,
                 digits: int = 2,
                 verbose: bool = False):
        self.df_price = df_price.copy()
        self.df_news = df_news.copy() if df_news is not None else pd.DataFrame()
        self.symbol = symbol
        self.p = params
        self.balance = float(balance)
        self.equity_peak = float(balance)
        self.point = float(point)
        self.digits = int(digits)
        self.open_trades: List[Trade] = []
        self.last_trade_ts: Optional[pd.Timestamp] = None
        self.day_trades_count: Dict[str, int] = {}
        self.realized_pnl: List[float] = []
        self.verbose = bool(verbose)

    # ---- helpers ----
    def _log(self, *a):
        if self.verbose:
            print(*a)

    def _risk_to_lots(self, risk_pct: float, sl_pts: float) -> float:
        if sl_pts <= 0:
            return 0.01
        money_risk = self.balance * (risk_pct / 100.0)
        money_risk = max(0.0, money_risk - self.p.commission_per_lot)
        value_per_lot = sl_pts * self.p.point_value_usd_per_lot
        lots = money_risk / max(value_per_lot, 1e-9)
        return max(0.01, round(lots, 2))

    def _regime_params(self, idx: pd.Timestamp, f_now: float, f_prev: float,
                       atr_pts_now: float) -> Tuple[float, int, int, int, int, float]:
        """يرجع (rr, ts_start, ts_step, be_trig, be_offs, risk_mult)"""
        if not self.p.use_regime:
            return (self.p.rr, self.p.ts_start, self.p.ts_step, self.p.be_trig, self.p.be_offs, 1.0)

        slope_pts = (f_now - f_prev) / self.point  # بالنقاط
        is_trend = abs(slope_pts) >= self.p.rd_slope_thresh_pts

        # تقدير HighVol من خلال ATR الحالي مقابل متوسط مرجعي
        ref = self.p.atr_norm_pts if self.p.atr_norm_pts > 0 else 800.0
        highvol = (atr_pts_now >= self.p.rd_highvol_mult * ref)

        if highvol:
            return (self.p.rr_highvol, self.p.ts_start_hv, self.p.ts_step_hv,
                    self.p.be_trig_hv, self.p.be_offs_hv, self.p.risk_mult_hv)
        if is_trend:
            return (self.p.rr_trend, self.p.ts_start_tr, self.p.ts_step_tr,
                    self.p.be_trig_tr, self.p.be_offs_tr, self.p.risk_mult_tr)
        # range
        return (self.p.rr_range, self.p.ts_start_rg, self.p.ts_step_rg,
                self.p.be_trig_rg, self.p.be_offs_rg, self.p.risk_mult_rg)

    def _rg_adjust_risk(self, base_risk_pct: float, atr_pts_now: float) -> float:
        rp = base_risk_pct
        if self.p.use_rg and self.p.scale_risk_by_atr and self.p.atr_norm_pts > 0 and atr_pts_now > 0:
            ratio = atr_pts_now / self.p.atr_norm_pts
            ratio = max(0.25, min(2.5, ratio))
            rp *= (1.0 / ratio)
        if self.p.use_rg:
            rp = max(self.p.rg_risk_min_pct, min(self.p.rg_risk_max_pct, rp))
        return rp

    # ---- إدارة الصفقات على كل شمعة ----
    def _step_close_manage(self, ts: pd.Timestamp, bid: float, ask: float,
                           e_rr: float, e_ts_start: int, e_ts_step: int,
                           e_be_trig: int, e_be_offs: int):
        new_open = []
        for tr in self.open_trades:
            if tr.direction > 0:
                gain_pts = (bid - tr.open_px) / self.point
                if self.p.use_be and gain_pts >= e_be_trig:
                    be = tr.open_px + e_be_offs * self.point
                    if be > tr.stop_loss:
                        tr.stop_loss = be
                if self.p.use_trailing and gain_pts > e_ts_start:
                    new_sl = bid - (gain_pts - e_ts_step) * self.point
                    if new_sl > tr.stop_loss:
                        tr.stop_loss = new_sl
                hit_sl = bid <= tr.stop_loss if tr.stop_loss > 0 else False
                hit_tp = bid >= tr.take_profit if tr.take_profit > 0 else False
            else:
                gain_pts = (tr.open_px - ask) / self.point
                if self.p.use_be and gain_pts >= e_be_trig:
                    be = tr.open_px - e_be_offs * self.point
                    if (tr.stop_loss == 0.0) or (be < tr.stop_loss):
                        tr.stop_loss = be
                if self.p.use_trailing and gain_pts > e_ts_start:
                    new_sl = ask + (gain_pts - e_ts_step) * self.point
                    if (tr.stop_loss == 0.0) or (new_sl < tr.stop_loss):
                        tr.stop_loss = new_sl
                hit_sl = ask >= tr.stop_loss if tr.stop_loss > 0 else False
                hit_tp = ask <= tr.take_profit if tr.take_profit > 0 else False

            close_now = False
            close_px = None
            if hit_sl:
                close_now = True
                close_px = tr.stop_loss
            elif hit_tp:
                close_now = True
                close_px = tr.take_profit

            if close_now:
                pnl = self._deal_profit(tr, close_px)
                self.balance += pnl
                self.realized_pnl.append(pnl)
                self.equity_peak = max(self.equity_peak, self.balance)
                self._log(f"[close] {ts} dir={tr.direction} lots={tr.lots} px={close_px:.2f} pnl={pnl:.2f} bal={self.balance:.2f}")
            else:
                new_open.append(tr)
        self.open_trades = new_open

    def _deal_profit(self, tr: Trade, close_px: float) -> float:
        if tr.direction > 0:
            pts = (close_px - tr.open_px) / self.point
        else:
            pts = (tr.open_px - close_px) / self.point
        gross = pts * self.p.point_value_usd_per_lot * tr.lots
        spread_cost = (self.p.spread_pts * self.p.point_value_usd_per_lot * tr.lots)
        comm = self.p.commission_per_lot * tr.lots
        return gross - spread_cost - comm

    def _can_open(self, ts: pd.Timestamp, spread_pts: float) -> Tuple[bool, str]:
        if spread_pts > self.p.max_spread_pts:
            return False, "spread"
        if self.p.use_session:
            local_ts = ts + pd.Timedelta(minutes=self.p.gmt_offset_min)
            h = local_ts.hour
            if not (self.p.sess_start_h <= h < self.p.sess_end_h):
                return False, "session"
        if self.last_trade_ts is not None:
            if (ts - self.last_trade_ts).total_seconds() < self.p.min_trade_gap_sec:
                return False, "gap"
        if len(self.open_trades) >= self.p.max_open_per_symbol:
            return False, "max_open"
        dkey = ts.date().isoformat()
        cnt = self.day_trades_count.get(dkey, 0)
        if cnt >= self.p.max_trades_per_day:
            return False, "max/day"
        return True, "OK"

    def _risk_governor_blocks(self, ts: pd.Timestamp) -> bool:
        if not self.p.use_rg:
            return False
        dd_pct = (self.equity_peak - self.balance) / max(1e-9, self.equity_peak) * 100.0
        if dd_pct >= self.p.equity_dd_halt_pct:
            return True
        return False

    def run(self) -> Metrics:
        feats = make_features(self.df_price, self.df_news)
        req_cols = ["Close", "ema20", "ema50", "rsi14"]
        for c in req_cols:
            if c not in feats.columns:
                raise SystemExit(f"Feature '{c}' مفقود — تحقق من make_features.")
        if "atr14" in feats.columns:
            atr_pts = feats["atr14"] / self.point
        else:
            atr_pts = feats["Close"].pct_change().rolling(14, min_periods=5).std().fillna(0) * (self.p.atr_norm_pts or 800)

        idx = feats.index
        close = feats["Close"]
        emaf = feats["ema20"]
        emas = feats["ema50"]
        rsi  = feats["rsi14"]

        # تأكد أن الفهرس UTC
        if isinstance(idx, pd.DatetimeIndex) and idx.tz is None:
            idx = idx.tz_localize("UTC")

        for i in range(2, len(idx)):
            ts = idx[i]
            mid = float(close.iloc[i])
            spread = self.p.spread_pts * self.point
            bid = mid - spread / 2.0
            ask = mid + spread / 2.0

            atr_now_pts = float(atr_pts.iloc[i]) if not pd.isna(atr_pts.iloc[i]) else self.p.atr_norm_pts
            rr_e, ts_s, ts_step, be_t, be_o, risk_mult = self._regime_params(
                ts, float(emaf.iloc[i]), float(emaf.iloc[i - 1]), atr_now_pts
            )
            risk_now = self._rg_adjust_risk(self.p.risk_pct, atr_now_pts) * risk_mult

            # إدارة الصفقات المفتوحة
            self._step_close_manage(ts, bid, ask, rr_e, ts_s, ts_step, be_t, be_o)

            # حوكمة المخاطرة
            if self._risk_governor_blocks(ts):
                self._log(f"[skip-rg] {ts}")
                continue

            # فلتر الأخبار
            if self.p.use_news and in_news_window(ts, self.df_news, self.p.cal_before_min, self.p.cal_after_min, self.p.cal_min_impact):
                self._log(f"[skip-news] {ts}")
                continue

            # الإشارات
            buy = sell = False
            if self.p.use_ma:
                f_now, f_prev = float(emaf.iloc[i]), float(emaf.iloc[i - 1])
                s_now, s_prev = float(emas.iloc[i]), float(emas.iloc[i - 1])
                buy  = (f_prev <= s_prev and f_now > s_now)
                sell = (f_prev >= s_prev and f_now < s_now)
            else:
                buy = sell = True

            if self.p.use_rsi:
                rv = float(rsi.iloc[i])
                if buy and not (rv <= self.p.rsi_buy_max):
                    buy = False
                if sell and not (rv >= self.p.rsi_sell_min):
                    sell = False

            direction = +1 if buy else (-1 if sell else 0)
            if direction == 0:
                continue

            ok, reason = self._can_open(ts, self.p.spread_pts)
            if not ok:
                self._log(f"[skip-open:{reason}] {ts}")
                continue

            sl_pts = max(self.p.atr_mult * atr_now_pts, max(10.0, 2.5 * self.p.spread_pts))
            tp_pts = max(10.0, sl_pts * rr_e)

            lots = self._risk_to_lots(risk_now, sl_pts)
            if lots <= 0.0:
                self._log(f"[skip-open:lots<=0] {ts}")
                continue

            open_px = ask if direction > 0 else bid
            sl = (open_px - sl_pts * self.point) if direction > 0 else (open_px + sl_pts * self.point)
            tp = (open_px + tp_pts * self.point) if direction > 0 else (open_px - tp_pts * self.point)

            tr = Trade(direction, open_px, lots, sl_pts, tp_pts, ts, sl, tp)
            self.open_trades.append(tr)
            self.last_trade_ts = ts
            dkey = ts.date().isoformat()
            self.day_trades_count[dkey] = self.day_trades_count.get(dkey, 0) + 1
            self._log(f"[open] {ts} dir={direction} px={open_px:.2f} sl_pts={sl_pts:.1f} tp_pts={tp_pts:.1f} lots={lots}")

        # إغلاق قسري عند نهاية البيانات
        if len(self.open_trades) > 0 and len(idx) > 0:
            ts = idx[-1]
            mid = float(close.iloc[-1])
            spread = self.p.spread_pts * self.point
            bid = mid - spread / 2.0
            ask = mid + spread / 2.0
            for tr in self.open_trades:
                px = bid if tr.direction > 0 else ask
                pnl = self._deal_profit(tr, px)
                self.balance += pnl
                self.realized_pnl.append(pnl)
                self._log(f"[force-close] {ts} dir={tr.direction} px={px:.2f} pnl={pnl:.2f} bal={self.balance:.2f}")
            self.open_trades = []

        return compute_metrics(self.realized_pnl)


# -------------------------- Grid Search ----------------------------
def grid_presets(name: str) -> Dict[str, List]:
    name = (name or "small").lower()
    if name == "tiny":
        return {
            "atr_mult": [1.6, 1.8],
            "rr": [1.6, 2.0],
            "risk_pct": [0.4, 0.8],
        }
    if name == "small":
        return {
            "atr_mult": [1.6, 1.8, 2.0],
            "rr": [1.6, 2.0, 2.4],
            "risk_pct": [0.4, 0.8, 1.0],
            "ts_start": [180, 250],
            "ts_step": [60, 80],
            "be_trig": [60, 80],
            "be_offs": [10, 15],
        }
    
    if name == "medium":
        return {
           "atr_mult": [1.5, 1.8, 2.1],
           "rr": [2.0, 2.4],                 # ارفع RR
           "risk_pct": [0.3, 0.6, 0.9],
           "ts_start": [160, 220, 280],
           "ts_step": [50, 80, 120],
           "be_trig": [50, 80, 120],
           "be_offs": [10, 15, 25],

         # جديد:
          "rsi_buy_max": [60, 65, 70],
         "rsi_sell_min": [30, 35, 40],
          "use_rsi": [True, False],         # جرّب بدون RSI لزيادة عدد الصفقات
        }
        
    # large
    return {
        "atr_mult": [1.2, 1.5, 1.8, 2.1, 2.4],
        "rr": [1.5, 1.8, 2.0, 2.4, 2.8],
        "risk_pct": [0.2, 0.6, 1.0, 1.4],
        "ts_start": [150, 200, 250, 300],
        "ts_step": [50, 80, 120, 150],
        "be_trig": [50, 80, 120, 160],
        "be_offs": [10, 15, 25, 35],
    }


def run_grid(df_price: pd.DataFrame,
             df_news: pd.DataFrame,
             symbol: str,
             base: EngineParams,
             grid: Dict[str, List],
             balance: float,
             point: float,
             digits: int,
             outdir: Path,
             save_every: int) -> Tuple[pd.DataFrame, Dict]:
    rows: List[Dict] = []
    best: Optional[Dict] = None
    best_key: Optional[float] = None

    keys = list(grid.keys())
    import itertools
    combos = list(itertools.product(*[grid[k] for k in keys]))
    total = len(combos)

    def save_partial():
        if not rows:
            return
        df_partial = pd.DataFrame(rows)
        df_partial.to_csv(outdir / "bt_results_partial.csv", index=False)
        if best is not None:
            with open(outdir / "best_result_partial.json", "w", encoding="utf-8") as f:
                json.dump(best, f, ensure_ascii=False, indent=2)

    for i, vals in enumerate(combos, 1):
        p = EngineParams(**asdict(base))
        for k, v in zip(keys, vals):
            setattr(p, k, v)

        bt = Backtester(df_price, df_news, symbol, p,
                        balance=balance, point=point, digits=digits, verbose=False)
        m = bt.run()
        row = {
            "atr_mult": p.atr_mult,
            "rr": p.rr,
            "risk_pct": p.risk_pct,
            "ts_start": p.ts_start,
            "ts_step": p.ts_step,
            "be_trig": p.be_trig,
            "be_offs": p.be_offs,
            "trades": m.trades,
            "winrate": m.winrate,
            "pf": m.pf,
            "net_profit": m.net_profit,
            "max_dd": m.max_dd,
            "avg_R": m.avg_R,
        }
        rows.append(row)

        # درجة مبسطة للمقارنة
        score = (m.pf if m.pf > 0 else 0) * 1.0 + (m.winrate / 100.0) * 0.5 + (m.trades > 20) * 0.2 - (m.max_dd / 10000.0)
        if best is None or score > (best_key if best_key is not None else -1e9):
            best = {"metrics": asdict(m), "params": asdict(p)}
            best_key = score

        if save_every > 0 and (i % save_every == 0 or i == total):
            pct = round(100.0 * i / total, 1)
            print(f"[grid] {i}/{total}  ({pct}%)  — saved partial")
            save_partial()

    df = pd.DataFrame(rows).sort_values(["pf", "winrate", "trades"], ascending=[False, False, False]).reset_index(drop=True)
    return df, best if best is not None else {}


# -------------------------- كتابة Live Config ----------------------
def write_live_config(best: Dict, out_path: Path):
    """يكتب runtime/live_config.json (متوافق مع الإكسبرت/الداشبورد)."""
    p = best["params"]
    obj = {
        "version": 1,
        "updated_at": pd.Timestamp.utcnow().isoformat(),
        "shadow": False,
        "ai_min_confidence": 0.60,
        "rr": float(p["rr"]),
        "risk_pct": float(p["risk_pct"]),
        "max_spread_pts": 350,
        "ts_start": int(p["ts_start"]),
        "ts_step": int(p["ts_step"]),
        "be_trig": int(p["be_trig"]),
        "be_offs": int(p["be_offs"]),
        "use_calendar": True,
        "cal_no_trade_before_min": 5,
        "cal_no_trade_after_min": 5,
        "cal_min_impact": 2,
        "params": {
            "AI_MinConfidence": 0.60,
            "InpATR_SL_Mult": float(p["atr_mult"]),
            "InpRR": float(p["rr"]),
            "NewsFilterLevel": "medium",
        },
        "notes": "auto-generated by optimize_full.py",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"[live_config] wrote -> {out_path}")


# -------------------------- CLI -----------------------------------
def main():
    ap = argparse.ArgumentParser(description="Full Optimization Backtester (Python)")
    ap.add_argument("--price", required=True, help="CSV: Time,Open,High,Low,Close")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--news", default="", help="اختياري CSV للأخبار: time,currency,impact")
    ap.add_argument("--start", default="", help="YYYY-MM-DD")
    ap.add_argument("--end", default="", help="YYYY-MM-DD")
    ap.add_argument("--outdir", default="artifacts")
    ap.add_argument("--grid", default="small", choices=["tiny", "small", "medium", "large"])
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--point", type=float, default=0.01)
    ap.add_argument("--digits", type=int, default=2)
    ap.add_argument("--spread_pts", type=float, default=DEFAULT_SPREAD_PTS)
    ap.add_argument("--commission", type=float, default=DEFAULT_COMMISSION_PER_LOT)
    ap.add_argument("--write-live-config", type=int, default=0)
    ap.add_argument("--save-every", type=int, default=25, help="احفظ نتائج مرحلية كل N تركيبة")
    ap.add_argument("--verbose", type=int, default=0, help="0/1 لطباعة لوج فتح/إغلاق الصفقات")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    dfp = load_price_csv(args.price, args.start or None, args.end or None)
    dfn = load_news_csv(args.news or None)

    # (تحقّق مبكّر من الميزات لتفادي مفاجآت لاحقة)
    _ = make_features(dfp, dfn)

    base = EngineParams()
    base.spread_pts = float(args.spread_pts)
    base.commission_per_lot = float(args.commission)
    base.point_value_usd_per_lot = 1.0
    # هذه القيم تضمن إشارات أكثر في البداية. عدّلها لاحقًا حسب الحاجة.
    base.ma_fast = 10
    base.ma_slow = 30
    base.rsi_buy_max = 70
    base.rsi_sell_min = 30
    base.atr_mult = 1.4
    base.rr = 1.6

    base.use_news = False
    base.min_trade_gap_sec = 5
    base.max_trades_per_day = 20
    base.risk_pct = 0.8
    base.max_spread_pts = 600

    grid = grid_presets(args.grid)

    df, best = run_grid(dfp, dfn, args.symbol, base, grid,
                        balance=args.balance, point=args.point, digits=args.digits,
                        outdir=outdir, save_every=int(args.save_every))

    # --- حفظ النتائج بأسماء فريدة + كتابة ذرّية (تمنع PermissionError) ---
    ts = pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S")
    bt_csv = outdir / f"bt_results_{ts}.csv"
    best_json = outdir / f"best_result_{ts}.json"

    # CSV
    tmp_csv = bt_csv.with_suffix(".tmp.csv")
    df.to_csv(tmp_csv, index=False)
    tmp_csv.replace(bt_csv)
    print(f"[results] wrote -> {bt_csv}")

    # JSON
    tmp_json = best_json.with_suffix(".tmp.json")
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(best, f, ensure_ascii=False, indent=2)
    tmp_json.replace(best_json)
    print(f"[best] wrote -> {best_json}")
    if best:
        print("[best][params]:", json.dumps(best.get("params", {}), ensure_ascii=False))
        print("[best][metrics]:", json.dumps(best.get("metrics", {}), ensure_ascii=False))

    if int(args.write_live_config) == 1 and best:
        live_cfg = Path("runtime") / "live_config.json"
        write_live_config(best, live_cfg)

    # مخرجات إضافية لطباعة نسخة جزئية أيضًا باسم ثابت (اختياري للتكامل الخارجي)
    try:
        df.to_csv(outdir / "bt_results.csv", index=False)
        with open(outdir / "best_result.json", "w", encoding="utf-8") as f:
            json.dump(best, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # ليس خطأً قاتلاً
        print(f"[warn] failed to write fixed-names due to: {e}")


if __name__ == "__main__":
    main()








    