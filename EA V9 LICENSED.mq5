//============================== PART 1 / 4 ==============================
// EA_Strategy_V9_Pro 9.00 (Licensed Edition)
// Build: 2026-03-25
// Changes from V8:
//   [FIX] Cloud_BlockTradingIfInvalidLicense = true by default
//   [FIX] License validation includes MT5 account number (server-side binding)
//   [FIX] Immediate engine halt on license revocation (no 15-min delay)
//   [FIX] Removed legacy InpLicenseKey / InpLicenseEndpoint inputs
//   [FIX] DoAutoConfig() now adapts ATR_SL_MULT based on live spread/ATR ratio
//   [FIX] VolSpikeNow() uses persistent atr_spike_handle (no recreate per call)
//   [FIX] BT_AISignalsCSV default is generic (no internal path exposed)
//   [FIX] atr_spike_handle released in OnDeinit()
#property strict
#property version   "9.00"
#property description "EA v9.00 Licensed: Full license enforcement + adaptive AutoConfig + performance fixes."

#include <Trade/Trade.mqh>
#include <Trade/PositionInfo.mqh>
#include <Trade/DealInfo.mqh>
#include <Trade/OrderInfo.mqh>

CTrade         trade;
CPositionInfo  pos;
CDealInfo      deal;
COrderInfo     ord;

//=================================================
//============== INPUTS & GLOBAL STATE ============
//=================================================

// -------- Logging --------
input bool   EnableLogger      = true;
input string LogDecisionsCSV   = "logs/decisions.csv";
input string LogDealsCSV       = "logs/deals.csv";
input bool   LogOpenRows       = false;

// -------- JSONL SelfCal --------
input bool   EnableJSONLTrades    = false;
input string JSONL_FilePrefix     = "trades_";
input string JSONL_SymbolTag      = "";
input int    JSONL_MinFieldsMode  = 0;

// -------- General --------
input long   InpMagic             = 20260208;
input string InpSymbol            = "";
input bool   UseCurrentSymbol     = true;

// -------- Trading mode --------
enum ENUM_TRADE_MODE
{
   TM_AI_ONLY   = 0,
   TM_HYBRID    = 1,
   TM_TECH_ONLY = 2
};
input ENUM_TRADE_MODE TradeMode = TM_TECH_ONLY;
input bool HYB_RequireAgreementWhenAI     = false;
input bool HYB_TechFallbackWhenNoAI       = false;
input bool HYB_TechFallbackWhenAIFiltered = false; // أضف هذا هنا فقط


// -------- Technical signals --------
input bool               UseMA        = true;
input int                InpMAfast    = 5;
input int                InpMAslow    = 30;
input ENUM_MA_METHOD     InpMA_Method = MODE_EMA;
input ENUM_APPLIED_PRICE InpMA_Price  = PRICE_CLOSE;

input bool   UseRSI         = true;
input int    InpRSI_Period  = 16;
input int    InpRSI_BuyMax  = 60;  // buy allowed only if RSI <= BuyMax
input int    InpRSI_SellMin = 40;  // sell allowed only if RSI >= SellMin

// -------- ATR / SL/TP baseline --------
input int    InpATR_Period      = 19;
input double InpATR_SL_Mult     = 1.5;
input double InpRR              = 2.5;
input int    InpMaxSpreadPts    = 350;

// -------- Broker adaptation --------
input bool   AutoConfig          = false;
input int    AC_LookbackDays     = 90;
input double InpSpreadSLFactor   = 2.5;
input double InpCommissionPerLot = 7.0;

// ---------- Risk management baseline ----------
input double InpRiskPct        = 0.25;
input int    MaxOpenPerSymbol  = 1;
input int    MaxTradesPerDay   = 2;
input bool   UseDailyLossStop  = true;
input double DailyLossPct      = 3.0;

// -------- Trailing & BreakEven --------
input bool   UseTrailingStop   = true;
input int    TS_StartPts       = 300;
input int    TS_StepPts        = 100;

input bool   UseBreakEven      = true;
input int    BE_TriggerPts     = 100;
input int    BE_OffsetPts      = 20;

// -------- Partial Close (generic) --------
input bool   UsePartialClose   = false;
input int    PC_TriggerPts     = 400;
input double PC_CloseFrac      = 0.5;

// -------- Advanced ATR-based TS/BE guards --------
input double TS_Buffer_ATR      = 0.30;
input double MinSL_Gap_ATR      = 0.30;
input double MinSL_Gap_SprdMul  = 2.0;
input int    TS_CooldownBars    = 3;

// -------- Smart BE/TS guards --------
input double BE_MinR               = 0.30;
input int    BE_MinGainPtsExtra    = 10;
input int    BE_ArmedAfterBars     = 2;
input double BE_SpreadMul          = 1.5;
input int    TS_MinDeltaModifyPts  = 20;

// -------- Sessions --------
input bool   UseSessionFilter    = false;
input int    Sess_GMT_Offset_Min = 0;
input int    Sess_Start_H        = 7;
input int    Sess_End_H          = 22;

// -------- Execution --------
input bool   TradeOnClosedBar    = true;
input int    MinTradeGapSec      = 3600;
input int    DirInput            = 0; // manual override (0=auto, 1=buy, -1=sell)

// -------- Calendar --------
input bool   UseCalendarNews       = false;
input int    Cal_NoTradeBeforeMin  = 5;
input int    Cal_NoTradeAfterMin   = 5;
input int    Cal_MinImpact         = 2;
input string Cal_Currencies        = "";

// -------- Emergency Exit --------
input bool   UseEmergencyExit         = false;
input int    EE_NewsMinImpact         = 3;
input int    EE_NewsBeforeMin         = 2;
input int    EE_NewsAfterMin          = 5;
input bool   EE_CloseOnOppositeAI     = false;
input double EE_AI_MinConfidence      = 0.80;
input bool   EE_ProtectInsteadOfClose = true;
input int    EE_BE_OffsetPts          = 25;
input int    EE_TightTS_Start         = 150;
input int    EE_TightTS_Step          = 50;
input bool   EE_PartialInsteadOfClose = false;
input double EE_PartialFrac           = 0.50;
input double EE_MaxAdverseATR         = 0.80;
input bool   EE_WaitFirstPulse        = false;
input int    EE_PulseSeconds          = 60;

// -------- AI Signals (Local + Cloud) --------
input bool   UseAISignals          = false;
input double AI_MinConfidence      = 0.70;
input bool   ForceInputConfidence  = false;
input double ForcedConfidenceValue = 0.80;
input double AI_RiskCapPct         = 1.00;
input int    AI_MaxHoldMinutes     = 60;
input string AISignalFile          = "ai_signals/xauusd_signal.ini";
input bool   AI_ShadowMode         = false;
input int    AI_FreshSeconds       = 90;
input int    AI_CooldownMinutes    = 5;

// ===== Professional AI Confidence Filtering =====
input double AI_Conf_Low        = 0.60;
input double AI_Conf_Medium     = 0.75;
input double AI_Conf_High       = 0.85;
input double AI_Conf_VeryHigh   = 0.92;

input bool   AI_BlockLowConfidence     = false;
input bool   AI_RequireTechAgreement   = false;
input bool   AI_ScaleRiskByConfidence  = false;
input bool   AI_ScaleRRByConfidence    = false;

input double AI_RiskScale_Low      = 0.50;
input double AI_RiskScale_Medium   = 0.75;
input double AI_RiskScale_High     = 1.00;
input double AI_RiskScale_VeryHigh = 1.30;

input double AI_RRScale_Low        = 0.80;
input double AI_RRScale_Medium     = 1.00;
input double AI_RRScale_High       = 1.20;
input double AI_RRScale_VeryHigh   = 1.40;

// ===== AI BACKTEST REPLAY =====
input bool   BT_EnableAIReplay      = false;
input string BT_AISignalsCSV        = "ai_signals/backtest_signals.csv";
input int    BT_AI_TimeShiftSec     = 0;
input bool   BT_AI_HoldLast         = false;
input bool   BT_AI_AllowOnBarOpen   = false;
input int    BT_AI_MaxHoldGapSec     = 3600;

// -------- Fibonacci Filter (v8: clean MTF swing TF) --------
input bool   UseFibonacciFilter      = false;

// Swing TF for Fibonacci swings (MTF). PERIOD_CURRENT means use chart timeframe.
input ENUM_TIMEFRAMES Fib_SwingTF    = PERIOD_CURRENT;

input int    Fib_LookbackBars        = 500;   // fractals scan window (on swing TF)
input double Fib_Zone_ATR_Mult       = 0.25;  // zone width = ATR_pts * mult
input int    Fib_MinZonePts          = 20;    // hard minimum zone width
input bool   Fib_Use50               = true;  // allow 50% level
input bool   Fib_Use618              = true;  // allow 61.8% level (core)
input bool   Fib_RequireTrendWithMA  = false;  // require MA trend to match dir (uses your MA fast/slow)
input bool   Fib_BlockIfNoSwings     = false; // if true: no swings => block trade





// --- Institutional guards ---
input bool   UseRegimeExtraConf   = false;
input double RangeExtraConf       = 0.05;
input double TrendExtraConf       = 0.00;
input double HighVolExtraConf     = 0.00;

// -------- Regime Detector --------
enum ENUM_REGIME { REG_NEUTRAL=0, REG_TREND=1, REG_RANGE=2, REG_HIGHVOL=3 };
input bool   UseRegimeDetector   = false;
input int    RD_ATR_Period       = 14;
input double RD_HighVolMult      = 2.2;
input int    RD_SlopeLookback    = 1;
input double RD_SlopeThreshPts   = 50;
input int    RD_MedianWindow     = 200;
input double RD_RangeDistMult    = 0.60;
input double RD_TrendSlopeMult   = 0.80;


// --- B2: Enhanced Regime Inputs (ADX + Bollinger Width) ---
input bool   RD_UseADX            = false;
input int    RD_ADX_Period        = 14;
input double RD_ADX_Trend         = 20.0;   // ADX >= => trend bias
input double RD_ADX_RangeMax      = 18.0;   // ADX <= => range bias

input bool   RD_UseBollinger      = false;
input int    RD_BB_Period         = 20;
input double RD_BB_Dev            = 2.0;
input double RD_BB_WidthRangeMult = 0.60;   // BB width <= mult * ATR_median_pts => RANGE
input double RD_BB_WidthTrendMult = 1.10;   // BB width >= mult * ATR_median_pts => TREND assist

// Per-regime parameters
input double R_TR_RR             = 2.40;
input int    R_TR_TS_Start       = 250;
input int    R_TR_TS_Step        = 80;
input int    R_TR_BE_Trig        = 80;
input int    R_TR_BE_Offs        = 15;
input double R_TR_RiskMult       = 1.20;

input double R_RG_RR             = 1.60;
input int    R_RG_TS_Start       = 180;
input int    R_RG_TS_Step        = 60;
input int    R_RG_BE_Trig        = 60;
input int    R_RG_BE_Offs        = 10;
input double R_RG_RiskMult       = 0.90;

input double R_HV_RR             = 2.80;
input int    R_HV_TS_Start       = 350;
input int    R_HV_TS_Step        = 120;
input int    R_HV_BE_Trig        = 120;
input int    R_HV_BE_Offs        = 30;
input double R_HV_RiskMult       = 0.70;

input int    RegimePersistBars    = 10;

// -------- Risk Governor --------
input bool   UseRiskGovernor       = false;
input double RG_DailyLossHardPct   = 5.0;
input double RG_EquityDDHaltPct    = 12.0;
input double RG_MaxExposureLots    = 5.0;
input bool   RG_ScaleRiskByATR     = false;
input double RG_RiskMinPct         = 0.2;
input double RG_RiskMaxPct         = 1.2;
input double RG_ATRNormPts         = 800;

// -------- Safety Nets --------
input int    MaxConsecLoss        = 4;
input int    CooldownMin          = 30;
input int    ATR_Period_Spike     = 14;
input double ATR_SpikeMult        = 2.5;
input string KillFile             = "KILL.TXT";
input int    MaxSlippagePts       = 200;

// -------- LiveConfig --------
input bool   UseLiveConfig         = true;
input string LiveConfigFile        = "live_config.json";
input int    LiveConfigRefreshMin  = 2;
input bool   AutoCreateFolders     = true;

// -------- Shadow-Guard --------
input bool   ForceShadowOff        = true;

// -------- Watchdog --------
input int  HeartbeatSec     = 5;
input int  StallReinitSec   = 90;

// -------- Debug --------
input bool   InpDebug              = true;
input bool   InpDebugVerbose       = false;
input bool   UseTimerEngine        = false;
input int    TimerPeriodSec        = 1;

// -------- Microstructure Filters --------
input bool   UseMicrostructureFilters = false;
input int    SpreadPercWindowSec      = 600;   // time-based window
input int    SpreadPercCut            = 90;
input bool   UseTickImbalance         = false;
input int    TickImbWindowSec         = 30;    // time-based window
input double TickImbalanceMin         = 0.55;  // allow trade only if dominance >= min

// Shadow SL + TimeStop
input bool   UseShadowSL          = false;
input double ShadowSL_Gap_ATR     = 0.6;
input bool   UseTimeStop          = false;
input int    TimeStop_Bars        = 20;
input double MFE_Trigger_Mult     = 1.2;
input bool   UseMFE_MAE_Trailing  = false;

// Ladder TP + Pyramiding
input bool   UseLadderTP          = false;
input double L1_R                 = 1.2;
input double L1_Frac              = 0.33;
input double L2_R                 = 2.0;
input double L2_Frac              = 0.33;

input bool   UsePyramiding        = false;
input int    Pyramid_MaxAdds      = 2;
input double Pyramid_RiskMult     = 0.5;
input double Pyramid_AddAtR       = 1.0;

// -------- Cloud License & Config --------
input bool   Cloud_Enable       = true;
input string Cloud_BaseURL      = "http://185.126.137.240:8000";
input string Cloud_ApiKey       = "";
input string Cloud_AccountId    = "";
input string Cloud_LicenseToken = "";
input int    Cloud_HeartbeatMin = 2;
input int    Cloud_ReloadMin    = 5;
input bool   Cloud_FetchAI      = false;

// v9: block trading if license is invalid (recommended: true for production)
input bool   Cloud_BlockTradingIfInvalidLicense = true;

// Duplicate logs (common folder)
input bool   DuplicateLogsToCommon = false;
input string CommonDealsName       = "deals.csv";
input string CommonDecisionsName   = "decisions.csv";

// --- Backtest behavior ---
input bool   BT_DisableCalendar   = true;
input bool   BT_DisableCloud      = true;
input bool   BT_AllowLocalAIFile  = false;
input bool   BT_ForceTechIfNoAI   = false;

// -------- Robust journaling --------
input bool   EnableCloseJournaling = false;
input int    CloseScanLookbackMin  = 240;
input int    CloseScanEverySec     = 5;

//====================================================
//================ INTERNAL GLOBAL STATE =============
//====================================================

// ===== Backtest Replay Layer =====
struct AIReplayRow
{
   datetime time;
   int      dir;
   double   confidence;
};

AIReplayRow g_replay[];
int         g_replay_count = 0;
bool        g_replay_loaded = false;

int ma_fast_handle = INVALID_HANDLE;
int ma_slow_handle = INVALID_HANDLE;
int rsi_handle     = INVALID_HANDLE;
int atr_handle     = INVALID_HANDLE;
int atr_spike_handle = INVALID_HANDLE; // v9: persistent handle (avoid recreating every call)


// B2: Regime helpers
int adx_handle     = INVALID_HANDLE;
int bb_handle      = INVALID_HANDLE;

// v8: fib fractals handle is bound to swing TF (MTF)
int fractal_handle = INVALID_HANDLE;
ENUM_TIMEFRAMES g_fib_tf = PERIOD_CURRENT;

string  G_Symbol="";
int     G_Digits=0;
double  G_Point=0, G_TickSize=0, G_TickValue=0;
double  G_VolMin=0, G_VolStep=0, G_VolMax=0;

datetime last_bar_time=0, last_trade_time=0;
datetime g_lastTickTS = 0;

// Calendar & AI cache
datetime last_news_check=0, last_ai_read=0, last_ai_exec=0, ai_hold_until=0;
int      ai_dir_hint=0;
double   ai_conf_hint=0;
string   ai_reason_hint="";

struct BT_AI_ROW
{
   datetime t;
   int      dir;      // -1 SELL, 0 NONE, +1 BUY
   double   conf;     // 0..1
   double   rr;       // optional
   double   risk;     // optional
};

BT_AI_ROW g_bt_ai[];
int g_bt_ai_n=0;
int g_bt_ai_idx=0;
bool g_bt_ai_loaded=false;

int _SigToDir(const string s)
{
   string x=UpperCopy(s); TrimStr(x);
   if(x=="BUY") return 1;
   if(x=="SELL") return -1;
   return 0;
}

// Last calendar event
datetime last_evt_time=0;
string   last_evt_currency="";
int      last_evt_impact=0;

// Adaptive derived params
double G_ATR_SL_MULT=0;
double G_RR=0;
int    G_MaxSpreadPts=0;
double G_SpreadSL_Factor=0;
double G_CommissionPerLot=0;

// Effective params (after regime selection)
double E_RR=0;
int    E_TS_Start=0, E_TS_Step=0;
int    E_BE_Trig=0, E_BE_Offs=0;
double E_RiskPct=0;

// Regime state
ENUM_REGIME G_Regime = REG_NEUTRAL;
int         Regime_ConfirmBars=0;

// Equity peak
double G_EquityPeak = 0.0;

// Loss cooldown
datetime G_CooldownUntil = 0;

// Heartbeat
datetime G_LastDbg = 0;

// Slippage tracker
double g_avg_slip_pts=0.0;
int    g_slip_n=0;

// Live Config overrides
bool   LC_Has=false;
bool   LC_Shadow=false;
double LC_AI_MinConf = -1;
double LC_RR         = -1;
double LC_RiskPct    = -1;
int    LC_MaxSpread  = -1;
int    LC_TS_Start   = -1, LC_TS_Step=-1;
int    LC_BE_Trig    = -1, LC_BE_Offs=-1;
int    LC_MaxOpen    = -1, LC_MaxPerDay=-1;
int    LC_NewsBefore = -1, LC_NewsAfter=-1;
int    LC_MinImpact  = -1;
bool   LC_UseCal     = true;

// Advanced LiveConfig Guards
double LC_BE_MinR              = -1;
int    LC_BE_MinGainPtsExtra   = -1;
double LC_BE_SpreadMul         = -1;
int    LC_TS_MinDeltaModifyPts = -1;
int    LC_TS_CooldownBars      = -1;
double LC_MinSL_Gap_ATR        = -1;
double LC_MinSL_Gap_SprdMul    = -1;

// Watchdog internal
datetime g_last_reset_time = 0;
bool     g_trade_lock      = false;
bool     g_traded_this_bar = false;

// Logger state
bool csv_header_written_deals=false;
bool csv_header_written_dec  =false;

// cloud state
bool g_cloud_ready=false;
bool g_license_ok=false;
datetime g_last_hb=0, g_last_cfg=0, g_last_lic=0, g_last_ai_cloud=0;

// Close journaling state
datetime g_last_close_scan=0;
long     g_last_deal_time_msc_seen=0;
ulong    g_last_deal_ticket_seen=0;

//================ DEBUG PRINT HELPERS =================
void DBG(const string msg)
{
   if(InpDebug)
      Print("[EA8] ", msg);
}

void DBGV(const string msg)
{
   if(InpDebugVerbose)
      Print("[EA8V] ", msg);
}

void DBG_TRADE(const string tag,int dir,double lots,double sl_pts,double tp_pts)
{
   if(!InpDebug) return;

   PrintFormat("[TRADE] %s dir=%d lots=%.2f SL_pts=%.1f TP_pts=%.1f",
               tag,dir,lots,sl_pts,tp_pts);
}

void DBG_AI(int dir,double conf,string reason)
{
   if(!InpDebug) return;

   PrintFormat("[AI] dir=%d conf=%.2f reason=%s",
               dir,conf,reason);
}

//====================================================
//==================== HELPERS =======================
//====================================================

string UpperCopy(const string &s){ string t=s; StringToUpper(t); return t; }
string LowerCopy(const string &s){ string t=s; StringToLower(t); return t; }
void TrimStr(string &s){ StringTrimLeft(s); StringTrimRight(s); }

// Convert ushort (Unicode code unit) to string safely without implicit ushort->uchar warning.
// For ASCII (<=255) use CharToString((uchar)ch). For non-ASCII use StringFormat("%c", int(ch)).
string U16CharToString(const ushort ch)
{
   if(ch <= 255)
      return CharToString((uchar)ch);

   // keep Unicode char without truncation
   return StringFormat("%c", (int)ch);
}


string JSON_Escape(const string s)
{
   string out="";
   for(int i=0;i<StringLen(s);i++)
   {
      ushort c=(ushort)StringGetCharacter(s,i);
      if(c=='\\'){ out+="\\\\"; continue; }
      if(c=='\"'){ out+="\\\""; continue; }
      if(c=='\n'){ out+="\\n";  continue; }
      if(c=='\r'){ out+="\\r";  continue; }
      if(c>=32 && c<128){ out+=U16CharToString(c); continue; }
      out+=StringFormat("\\u%04X",(uint)c);
   }
   return out;
}

string StripUTF8BOM(string s)
{
   if(StringLen(s)>=3)
   {
      ushort a=(ushort)StringGetCharacter(s,0);
      ushort b=(ushort)StringGetCharacter(s,1);
      ushort c=(ushort)StringGetCharacter(s,2);
      if(a==0x00EF && b==0x00BB && c==0x00BF)
         return StringSubstr(s,3);
   }
   if(StringLen(s)>=1 && (ushort)StringGetCharacter(s,0)==0xFEFF)
      return StringSubstr(s,1);
   return s;
}

string MonthKey(const datetime t){ MqlDateTime dt; TimeToStruct(t,dt); return StringFormat("%04d%02d",dt.year,dt.mon); }

bool IsTester(){ return (bool)MQLInfoInteger(MQL_TESTER); }
bool IsOptimization(){ return (bool)MQLInfoInteger(MQL_OPTIMIZATION); }
bool IsVisual(){ return (bool)MQLInfoInteger(MQL_VISUAL_MODE); }

bool EnsureDirExists(const string rel_path)
{
   if(rel_path=="") return true;

   ResetLastError();
   if(FolderCreate(rel_path))
      return true;

   int e=GetLastError();
   ResetLastError();

   // Folder may already exist -> still OK in most cases
   if(e==5004 /*ERR_FILE_ALREADY_EXIST*/ || e==0)
      return true;

   if(InpDebugVerbose)
      PrintFormat("[DIR] FolderCreate failed for %s err=%d", rel_path, e);

   return false;
}


void AutoCreateLogFolders()
{
   if(!AutoCreateFolders) return;
   EnsureDirExists("logs");
   EnsureDirExists("ai_signals");
   EnsureDirExists("ai_replay");
}

//====================================================
//==================== PRICE SANITY LAYER (v8) =======
//====================================================
double _ClampDouble(double v,double lo,double hi){ return MathMax(lo,MathMin(hi,v)); }

double NormalizePriceToTick(const string sym,double price)
{
   double tick=0.0;
   if(!SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE,tick) || tick<=0.0)
      tick=SymbolInfoDouble(sym,SYMBOL_POINT);
   if(tick<=0.0) tick=_Point;

   // round to nearest tick
   double q = price/tick;
   double rq = MathRound(q);
   double p = rq*tick;

   int digits=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   return NormalizeDouble(p,digits);
}

double GetStopsLevelPts(const string sym)
{
   long v=0;
   if(!SymbolInfoInteger(sym,SYMBOL_TRADE_STOPS_LEVEL,v)) return 0.0;
   return (double)MathMax((long)0,v);
}

double GetFreezeLevelPts(const string sym)
{
   long v=0;
   if(!SymbolInfoInteger(sym,SYMBOL_TRADE_FREEZE_LEVEL,v)) return 0.0;
   return (double)MathMax((long)0,v);
}

// Unified Sanity Layer for SL/TP before SEND/MODIFY.
// Ensures: correct side, stops+freeze min distance, normalization.
bool SanitizeSLTP(const string sym,const int dir,const double entry_price,double &sl_price,double &tp_price,string &reason_out)
{
   reason_out="ok";

   if(dir==0){ reason_out="dir0"; return false; }
   if(entry_price<=0.0 || !MathIsValidNumber(entry_price)){ reason_out="bad_entry"; return false; }

   double point=0.0; SymbolInfoDouble(sym,SYMBOL_POINT,point);
   if(point<=0.0) point=_Point;

   // Broker constraints in points
   double stopsPts = GetStopsLevelPts(sym);
   double freezePts= GetFreezeLevelPts(sym);

   // Conservative min distance: stops OR freeze (whichever larger) + small buffer
   double minDistPts = MathMax(stopsPts,freezePts);
   minDistPts = MathMax(minDistPts, 2.0);     // never < 2 points
   minDistPts = minDistPts + 2.0;            // safety buffer

   // Validate & correct side first
   if(dir>0)
   {
      if(sl_price>=entry_price || sl_price<=0.0)
         sl_price = entry_price - minDistPts*point;
      if(tp_price<=entry_price || tp_price<=0.0)
         tp_price = entry_price + minDistPts*point;
   }
   else
   {
      if(sl_price<=entry_price || sl_price<=0.0)
         sl_price = entry_price + minDistPts*point;
      if(tp_price>=entry_price || tp_price<=0.0)
         tp_price = entry_price - minDistPts*point;
   }

   // Enforce min distance
   double dSL = MathAbs(entry_price - sl_price)/point;
   double dTP = MathAbs(tp_price - entry_price)/point;

   if(dSL < minDistPts)
   {
      if(dir>0) sl_price = entry_price - minDistPts*point;
      else      sl_price = entry_price + minDistPts*point;
   }
   if(dTP < minDistPts)
   {
      if(dir>0) tp_price = entry_price + minDistPts*point;
      else      tp_price = entry_price - minDistPts*point;
   }

   // Normalize to tick
   sl_price = NormalizePriceToTick(sym,sl_price);
   tp_price = NormalizePriceToTick(sym,tp_price);

   // Final validation (must still be on correct side)
   if(dir>0)
   {
      if(!(sl_price < entry_price && tp_price > entry_price))
      {
         reason_out="side_fail_buy";
         return false;
      }
   }
   else
   {
      if(!(sl_price > entry_price && tp_price < entry_price))
      {
         reason_out="side_fail_sell";
         return false;
      }
   }

   reason_out="ok";
   return true;
}

//==================== PATCH v8: per-position helpers ====================
bool PosSelectByTicketSafe(ulong pticket)
{
   ResetLastError();
   bool ok=PositionSelectByTicket(pticket);
   if(!ok && InpDebugVerbose) PrintFormat("[POS] PositionSelectByTicket fail ticket=%I64u err=%d",pticket,GetLastError());
   ResetLastError();
   return ok;
}

ulong PosIdByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return 0;
   return (ulong)PositionGetInteger(POSITION_IDENTIFIER);
}

string PosSymbolByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return "";
   return (string)PositionGetString(POSITION_SYMBOL);
}

int PosDirByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return 0;
   long t=PositionGetInteger(POSITION_TYPE);
   return (t==POSITION_TYPE_BUY?+1:(t==POSITION_TYPE_SELL?-1:0));
}

double PosVolumeByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return 0.0;
   return PositionGetDouble(POSITION_VOLUME);
}

double PosPriceOpenByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return 0.0;
   return PositionGetDouble(POSITION_PRICE_OPEN);
}

double PosSLByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return 0.0;
   return PositionGetDouble(POSITION_SL);
}

double PosTPByTicket(ulong pticket)
{
   if(!PosSelectByTicketSafe(pticket)) return 0.0;
   return PositionGetDouble(POSITION_TP);
}

ulong FindPositionTicketByPosId(ulong pos_id)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong pt=PositionGetTicket(i);
      if(pt==0) continue;
      if(!PositionSelectByTicket(pt)) continue;

      if((string)PositionGetString(POSITION_SYMBOL)!=G_Symbol) continue;
      if((int)PositionGetInteger(POSITION_MAGIC)!=(int)InpMagic) continue;

      ulong pid=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      if(pid==pos_id) return pt;
   }
   return 0;
}
//================== END PATCH v8 =======================================

//====================================================
//==================== FIBONACCI FILTER (v8) =========
//====================================================
ENUM_TIMEFRAMES Fib_EffectiveTF()
{
   if(Fib_SwingTF==PERIOD_CURRENT) return (ENUM_TIMEFRAMES)_Period;
   return Fib_SwingTF;
}

bool Fib_Init()
{
   ENUM_TIMEFRAMES tf=Fib_EffectiveTF();

   // if handle exists but TF changed -> recreate
   if(fractal_handle!=INVALID_HANDLE && g_fib_tf==tf) return true;

   if(fractal_handle!=INVALID_HANDLE)
   {
      IndicatorRelease(fractal_handle);
      fractal_handle=INVALID_HANDLE;
   }

   fractal_handle = iFractals(G_Symbol, tf);
   g_fib_tf=tf;

   if(fractal_handle==INVALID_HANDLE)
   {
      int e=GetLastError(); ResetLastError();
      if(InpDebug) PrintFormat("[FIB] iFractals create failed tf=%d err=%d", (int)tf, e);
      return false;
   }
   return true;
}

void Fib_Deinit()
{
   if(fractal_handle!=INVALID_HANDLE)
   {
      IndicatorRelease(fractal_handle);
      fractal_handle=INVALID_HANDLE;
   }
}

struct FibLevels
{
   double f382;
   double f500;
   double f618;
   double f786;
};

// Up impulse retracement: levels below the swing HIGH
FibLevels Fib_RetraceFromHigh(double low,double high)
{
   FibLevels f; ZeroMemory(f);
   double range = high - low;
   f.f382 = high - range * 0.382;
   f.f500 = high - range * 0.500;
   f.f618 = high - range * 0.618;
   f.f786 = high - range * 0.786;
   return f;
}

// Down impulse retracement: levels above the swing LOW (bounce up)
FibLevels Fib_RetraceUpFromLow(double low,double high)
{
   FibLevels f; ZeroMemory(f);
   double range = high - low;
   f.f382 = low + range * 0.382;
   f.f500 = low + range * 0.500;
   f.f618 = low + range * 0.618;
   f.f786 = low + range * 0.786;
   return f;
}

double Fib_DistPts(double price, double level)
{
   return MathAbs(price - level) / G_Point;
}

bool Fib_IsNear(double price, double level, double zonePts)
{
   return (Fib_DistPts(price, level) <= zonePts);
}

// v8: Get last confirmed fractal high/low from swing TF
bool Fib_GetLastSwingFractals(int lookbackBars,
                              double &swingHigh, datetime &timeHigh,
                              double &swingLow,  datetime &timeLow)
{
   swingHigh = 0.0;
   swingLow  = 0.0;
   timeHigh  = 0;
   timeLow   = 0;

   if(fractal_handle == INVALID_HANDLE)
      return false;

   int need = MathMax(lookbackBars, 50);

   double upBuf[], dnBuf[];
   ArraySetAsSeries(upBuf, true);
   ArraySetAsSeries(dnBuf, true);

   if(CopyBuffer(fractal_handle, 0, 0, need, upBuf) <= 0)
      return false;

   if(CopyBuffer(fractal_handle, 1, 0, need, dnBuf) <= 0)
      return false;

   bool foundHigh = false;
   bool foundLow  = false;

   ENUM_TIMEFRAMES tf = g_fib_tf;

   for(int i = 2; i < need; i++)
   {
      if(!foundHigh && upBuf[i] != 0.0)
      {
         swingHigh = upBuf[i];
         timeHigh  = iTime(G_Symbol, tf, i);
         foundHigh = true;
      }

      if(!foundLow && dnBuf[i] != 0.0)
      {
         swingLow = dnBuf[i];
         timeLow  = iTime(G_Symbol, tf, i);
         foundLow = true;
      }

      if(foundHigh && foundLow)
         break;
   }

   if(!foundHigh || !foundLow) return false;

   if(swingHigh <= 0.0 || swingLow <= 0.0) return false;
   if(!MathIsValidNumber(swingHigh) || !MathIsValidNumber(swingLow)) return false;

   // sanity: high must be > low
   if(swingHigh <= swingLow) return false;

   // times must be valid
   if(timeHigh<=0 || timeLow<=0) return false;

   return true;
}

// Trend filter using MA fast/slow (chart TF MAs)
bool Fib_TrendAllowsDir(int dir, double ma_fast, double ma_slow)
{
   if(!Fib_RequireTrendWithMA) return true;
   if(!UseMA) return true;
   if(dir>0) return (ma_fast > ma_slow);
   if(dir<0) return (ma_fast < ma_slow);
   return true;
}

// v8: Fixed logic, no duplicated dir blocks, consistent retracement math, clean reasons.
bool Fib_FilterAllows(int dir, double atr_pts, double ma_fast, double ma_slow, string &reason_out)
{
   reason_out = "fib_ok";

   if(!UseFibonacciFilter) return true;
   if(dir==0){ reason_out="fib_dir0"; return false; }

   if(!Fib_Init())
   {
      reason_out="fib_handle_fail";
      return !Fib_BlockIfNoSwings;
   }

   // Zone width (points)
   double zonePts = MathMax((double)Fib_MinZonePts, atr_pts * Fib_Zone_ATR_Mult);
   if(zonePts < (double)Fib_MinZonePts) zonePts=(double)Fib_MinZonePts;

   double sh=0.0, sl=0.0; datetime th=0, tl=0;
   bool ok = Fib_GetLastSwingFractals(Fib_LookbackBars, sh, th, sl, tl);

   if(!ok || sh<=sl)
   {
      if(InpDebug)
         PrintFormat("[FIB] NO/INVALID swings ok=%d sh=%.5f sl=%.5f tf=%d", (int)ok, sh, sl, (int)g_fib_tf);
      reason_out="fib_no_swings";
      return !Fib_BlockIfNoSwings;
   }

   // Impulse definition by time ordering
   bool impulseUp = (tl < th); // low first then high
   bool impulseDn = (th < tl); // high first then low

   MqlTick tk;
   if(!SymbolInfoTick(G_Symbol, tk))
   {
      reason_out="fib_no_tick";
      return false;
   }

   // Use mid price for zone test (reduces bid/ask bias)
   double px = 0.5*(tk.bid + tk.ask);

   if(!Fib_TrendAllowsDir(dir, ma_fast, ma_slow))
   {
      reason_out="fib_trend_ma_mismatch";
      return false;
   }

   if(InpDebugVerbose)
      PrintFormat("[FIB] dir=%d tf=%d zonePts=%.1f swingH=%.5f(%s) swingL=%.5f(%s) px=%.5f",
                  dir,(int)g_fib_tf,zonePts,sh,TimeToString(th,TIME_DATE|TIME_MINUTES),sl,TimeToString(tl,TIME_DATE|TIME_MINUTES),px);

   // BUY: require up impulse then retracement down from high
   if(dir>0)
   {
      if(!impulseUp)
      {
         reason_out="fib_impulse_not_up";
         return false;
      }

      FibLevels fib = Fib_RetraceFromHigh(sl, sh);

      bool inZone=false;
      if(Fib_Use618 && Fib_IsNear(px, fib.f618, zonePts)) inZone=true;
      if(!inZone && Fib_Use50 && Fib_IsNear(px, fib.f500, zonePts)) inZone=true;

      if(!inZone)
      {
         reason_out=StringFormat("fib_not_in_zone(B) zone=%.1f", zonePts);
         return false;
      }

      reason_out="fib_ok_buy";
      return true;
   }

   // SELL: require down impulse then retracement up from low
   if(dir<0)
   {
      if(!impulseDn)
      {
         reason_out="fib_impulse_not_down";
         return false;
      }

      FibLevels fib = Fib_RetraceUpFromLow(sl, sh);

      bool inZone=false;
      if(Fib_Use618 && Fib_IsNear(px, fib.f618, zonePts)) inZone=true;
      if(!inZone && Fib_Use50 && Fib_IsNear(px, fib.f500, zonePts)) inZone=true;

      if(!inZone)
      {
         reason_out=StringFormat("fib_not_in_zone(S) zone=%.1f", zonePts);
         return false;
      }

      reason_out="fib_ok_sell";
      return true;
   }

   reason_out="fib_unknown";
   return false;
}

//====================================================
//==================== SAFE CLOSE / MODIFY ===========
bool ClosePositionByTicket(ulong pticket)
{
   ResetLastError();
   bool ok=trade.PositionClose(pticket);
   if(!ok && InpDebugVerbose)
      PrintFormat("[CLOSE] fail ticket=%I64u ret=%d desc=%s err=%d",
                  pticket,trade.ResultRetcode(),trade.ResultRetcodeDescription(),GetLastError());
   ResetLastError();
   return ok;
}

bool ClosePartialByTicket(ulong pticket,double vol)
{
   vol=NormalizeVolume(vol);
   if(vol<=0.0) return false;

   if(!PositionSelectByTicket(pticket)) return false;
   string sym = PositionGetString(POSITION_SYMBOL);

   ResetLastError();
   bool ok=trade.PositionClosePartial(sym,vol);

   if(!ok && InpDebugVerbose)
      PrintFormat("[PCLOSE] fail ticket=%I64u sym=%s vol=%.2f ret=%d desc=%s err=%d",
                  pticket,sym,vol,trade.ResultRetcode(),trade.ResultRetcodeDescription(),GetLastError());

   ResetLastError();
   return ok;
}

bool ModifyPositionByTicketSafe(ulong pticket,double sl,double tp)
{
   if(pticket==0) return false;
   if(!PositionSelectByTicket(pticket)) return false;

   string sym = PositionGetString(POSITION_SYMBOL);

   int dir = PosDirByTicket(pticket);

   // current SL/TP (avoid useless modify loops)
   double curSL = PositionGetDouble(POSITION_SL);
   double curTP = PositionGetDouble(POSITION_TP);

   // If nothing changes (within 2 points), skip
   double pt = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(pt<=0) pt=_Point;

   if(MathAbs(sl-curSL) <= 2.0*pt && MathAbs(tp-curTP) <= 2.0*pt)
      return true;

   // IMPORTANT: for MODIFY we must sanitize vs CURRENT price, not entry
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double ref = (dir>0 ? bid : ask);   // buy -> bid, sell -> ask

   string rsn="";
   double sl2=sl, tp2=tp;

   if(!SanitizeSLTP(sym,dir,ref,sl2,tp2,rsn))
   {
      if(InpDebugVerbose)
         PrintFormat("[SANITY][MOD] reject ticket=%I64u sym=%s dir=%d reason=%s ref=%.5f",
                     pticket,sym,dir,rsn,ref);
      return false;
   }

   // last safety: if sanitize resulted in same values, don't retry
   if(MathAbs(sl2-curSL) <= 2.0*pt && MathAbs(tp2-curTP) <= 2.0*pt)
      return true;

   bool ok=false;
   for(int i=0;i<3 && !ok;i++)
   {
      ResetLastError();
      ok = trade.PositionModify(sym, sl2, tp2);

      if(!ok)
      {
         int err=_LastError;
         if(InpDebugVerbose)
            PrintFormat("[V8][MODIFY_RETRY] ticket=%I64u sym=%s err=%d sl=%.5f tp=%.5f",
                        pticket,sym,err,sl2,tp2);
         Sleep(100);
      }
   }

   if(!ok)
      PrintFormat("[V8][MODIFY_FAIL] ticket=%I64u sym=%s",pticket,sym);

   return ok;
}

//====================================================
//================ SYMBOL SELECTION ==================
string Sym(){ if(UseCurrentSymbol || InpSymbol=="") return _Symbol; return InpSymbol; }

bool FillSymbolInfo()
{
   G_Symbol=Sym(); if(G_Symbol=="") G_Symbol=_Symbol;
   if(!SymbolSelect(G_Symbol,true))
   {
      int e=GetLastError(); ResetLastError();
      if(InpDebug) PrintFormat("[INIT] SymbolSelect failed sym=%s err=%d",G_Symbol,e);
      return false;
   }
   long digits=0;
   if(!SymbolInfoInteger(G_Symbol,SYMBOL_DIGITS,digits)) return false;
   G_Digits=(int)digits;

   if(!SymbolInfoDouble(G_Symbol,SYMBOL_POINT,G_Point)) return false;
   if(!SymbolInfoDouble(G_Symbol,SYMBOL_TRADE_TICK_SIZE,G_TickSize)) return false;
   if(!SymbolInfoDouble(G_Symbol,SYMBOL_TRADE_TICK_VALUE,G_TickValue)) return false;
   if(!SymbolInfoDouble(G_Symbol,SYMBOL_VOLUME_MIN,G_VolMin)) return false;
   if(!SymbolInfoDouble(G_Symbol,SYMBOL_VOLUME_STEP,G_VolStep)) return false;
   if(!SymbolInfoDouble(G_Symbol,SYMBOL_VOLUME_MAX,G_VolMax)) return false;
   return true;
}

bool EnsureSeriesReady(const string symbol,const ENUM_TIMEFRAMES tf,const int min_bars,const int tries=150)
{
   if(symbol=="") return false;
   if(!SymbolSelect(symbol,true)) return false;

   long synced=0;
   for(int i=0;i<tries;i++)
   {
      ResetLastError();
      if(SeriesInfoInteger(symbol,tf,SERIES_SYNCHRONIZED,synced) && synced==1)
      {
         int bars=Bars(symbol,tf);
         if(bars>=min_bars) return true;
      }
      Sleep(200);
   }
   return false;
}

bool WaitIndicatorReady(const int handle,const int min_bars)
{
   if(handle==INVALID_HANDLE) return false;
   if(!EnsureSeriesReady(G_Symbol,(ENUM_TIMEFRAMES)_Period,min_bars)) return false;

   int tries=0;
   while(tries<150)
   {
      int bc=BarsCalculated(handle);
      if(bc>=min_bars)
      {
         double buf[1];
         ResetLastError();
         if(CopyBuffer(handle,0,1,1,buf)==1 && MathIsValidNumber(buf[0]) && buf[0]!=EMPTY_VALUE)
            return true;
         ResetLastError();
         if(CopyBuffer(handle,0,0,1,buf)==1 && MathIsValidNumber(buf[0]) && buf[0]!=EMPTY_VALUE)
            return true;
      }
      Sleep(200);
      tries++;
   }
   return false;
}

//====================================================
//================ MICROSTRUCTURE (RING BUFFERS) =====
int   g_sp_cap = 4000;
int   g_sp_head=0, g_sp_count=0;
datetime g_sp_t[];
double   g_sp_v[];

int   g_ti_cap = 6000;
int   g_ti_head=0, g_ti_count=0;
datetime g_ti_t[];
int      g_ti_dir[];

double g_last_mid=0.0;

void RB_Init()
{
   ArrayResize(g_sp_t,g_sp_cap);
   ArrayResize(g_sp_v,g_sp_cap);
   g_sp_head=0; g_sp_count=0;

   ArrayResize(g_ti_t,g_ti_cap);
   ArrayResize(g_ti_dir,g_ti_cap);
   g_ti_head=0; g_ti_count=0;
}

void SP_Push(const double sp,const datetime t)
{
   if(g_sp_cap<=0) return;
   int idx=(g_sp_head+g_sp_count)%g_sp_cap;
   g_sp_t[idx]=t;
   g_sp_v[idx]=sp;
   if(g_sp_count<g_sp_cap) g_sp_count++;
   else g_sp_head=(g_sp_head+1)%g_sp_cap;
}

void SP_Prune(const datetime now)
{
   if(SpreadPercWindowSec<=0 || g_sp_count<=0) return;
   datetime cutoff=now-(datetime)SpreadPercWindowSec;
   while(g_sp_count>0)
   {
      datetime tt=g_sp_t[g_sp_head];
      if(tt>=cutoff) break;
      g_sp_head=(g_sp_head+1)%g_sp_cap;
      g_sp_count--;
   }
}

double SP_Percentile(const int pct,const datetime now)
{
   SP_Prune(now);
   int n=g_sp_count;
   if(n<50) return 1e9;

   double tmp[];
   ArrayResize(tmp,n);
   for(int i=0;i<n;i++)
   {
      int idx=(g_sp_head+i)%g_sp_cap;
      tmp[i]=g_sp_v[idx];
   }
   ArraySort(tmp);

   int p=MathMax(0,MathMin(100,pct));
   int idx=(int)MathFloor((p/100.0)*(n-1));
   idx=MathMax(0,MathMin(n-1,idx));
   return tmp[idx];
}

void TI_Push(const int dir,const datetime t)
{
   if(g_ti_cap<=0) return;
   int idx=(g_ti_head+g_ti_count)%g_ti_cap;
   g_ti_t[idx]=t;
   g_ti_dir[idx]=dir;
   if(g_ti_count<g_ti_cap) g_ti_count++;
   else g_ti_head=(g_ti_head+1)%g_ti_cap;
}

void TI_Prune(const datetime now)
{
   if(TickImbWindowSec<=0 || g_ti_count<=0) return;
   datetime cutoff=now-(datetime)TickImbWindowSec;
   while(g_ti_count>0)
   {
      datetime tt=g_ti_t[g_ti_head];
      if(tt>=cutoff) break;
      g_ti_head=(g_ti_head+1)%g_ti_cap;
      g_ti_count--;
   }
}

double TI_Imbalance(const datetime now)
{
   TI_Prune(now);
   int n=g_ti_count;
   if(n<20) return 0.0;

   int up=0,dn=0;
   for(int i=0;i<n;i++)
   {
      int idx=(g_ti_head+i)%g_ti_cap;
      int d=g_ti_dir[idx];
      if(d>0) up++;
      else if(d<0) dn++;
   }
   int tot=up+dn;
   if(tot<=0) return 0.0;
   return (double)MathMax(up,dn)/(double)tot;
}

int TI_DominantDir(const datetime now)
{
   TI_Prune(now);
   int n=g_ti_count;
   if(n<20) return 0;
   int up=0,dn=0;
   for(int i=0;i<n;i++)
   {
      int idx=(g_ti_head+i)%g_ti_cap;
      int d=g_ti_dir[idx];
      if(d>0) up++;
      else if(d<0) dn++;
   }
   if(up==dn) return 0;
   return (up>dn?+1:-1);
}

void Tick_Update()
{
   MqlTick t;
   if(!SymbolInfoTick(G_Symbol,t)) return;

   datetime now=TimeCurrent();
   double sp=(t.ask-t.bid)/G_Point;
   SP_Push(sp,now);

   double mid=0.5*(t.ask+t.bid);
   int dir=0;
   if(g_last_mid>0.0)
   {
      if(mid>g_last_mid) dir=+1;
      else if(mid<g_last_mid) dir=-1;
   }
   g_last_mid=mid;
   TI_Push(dir,now);

   g_lastTickTS=(datetime)(t.time_msc/1000);
}

//====================================================
//================ INDICATORS / ATR ==================
bool _ATR_EnsureHandle()
{
   if(atr_handle!=INVALID_HANDLE) return true;
   atr_handle=iATR(G_Symbol,_Period,InpATR_Period);


   // B2: Regime handles (ADX + Bollinger Bands width)
   if(RD_UseADX)
      adx_handle=iADX(G_Symbol,_Period,RD_ADX_Period);
   if(RD_UseBollinger)
      bb_handle=iBands(G_Symbol,_Period,RD_BB_Period,0,RD_BB_Dev,PRICE_CLOSE);

   if(atr_handle==INVALID_HANDLE)
   {
      int e=GetLastError(); ResetLastError();
      if(InpDebug) PrintFormat("[ATR_DEBUG] iATR create failed err=%d",e);
      return false;
   }
   return true;
}

bool _ATR_BufferOnce(const int handle,double &atr_value)
{
   atr_value=0.0;
   int bars=Bars(G_Symbol,_Period);
   if(bars<InpATR_Period+5) return false;

   int bc=BarsCalculated(handle);
   if(bc<InpATR_Period+2) return false;   // fix: was (bc>0 && bc<...) — missed bc==0 case

   double v[1];
   int copied=CopyBuffer(handle,0,1,1,v); // shift=1: read last CLOSED bar (stable in live)
   if(copied==1 && v[0]>0.0){ atr_value=v[0]; return true; }
   return false;
}

bool GetATR_Points_PerBar(double &out)
{
   out=0.0;
   if(!_ATR_EnsureHandle()) return false;

   double atr_val=0.0;
   if(_ATR_BufferOnce(atr_handle,atr_val)){ out=atr_val/G_Point; return true; }

   if(atr_handle!=INVALID_HANDLE){ IndicatorRelease(atr_handle); atr_handle=INVALID_HANDLE; }
   if(adx_handle!=INVALID_HANDLE){ IndicatorRelease(adx_handle); adx_handle=INVALID_HANDLE; }
   if(bb_handle!=INVALID_HANDLE){ IndicatorRelease(bb_handle); bb_handle=INVALID_HANDLE; }

   if(!_ATR_EnsureHandle()) return false;

   if(_ATR_BufferOnce(atr_handle,atr_val)){ out=atr_val/G_Point; return true; }

   int h_tmp=iATR(G_Symbol,_Period,InpATR_Period);
   if(h_tmp==INVALID_HANDLE) return false;

   double buf[1];
   int got=CopyBuffer(h_tmp,0,0,1,buf);
   IndicatorRelease(h_tmp);

   if(got==1 && buf[0]>0.0){ out=buf[0]/G_Point; return true; }
   return false;
}

bool ReadIndicatorValue(const int handle,const int min_bars,double &out_value,const int preferred_shift=1)
{
   out_value=0.0;
   if(handle==INVALID_HANDLE) return false;
   if(!EnsureSeriesReady(G_Symbol,(ENUM_TIMEFRAMES)_Period,min_bars)) return false;

   int bc=BarsCalculated(handle);
   if(bc<min_bars) return false;

   double buf[1];
   int shifts[2]={preferred_shift,0};
   for(int i=0;i<2;i++)
   {
      int shift=shifts[i];
      if(bc<=shift) continue;

      ResetLastError();
      if(CopyBuffer(handle,0,shift,1,buf)==1 && MathIsValidNumber(buf[0]) && buf[0]!=EMPTY_VALUE)
      {
         out_value=buf[0];
         return true;
      }
   }

   return false;
}

bool ReadTechNow(double &ma_fast,double &ma_slow,double &rsi,double &atr_pts)
{
   ma_fast=0; ma_slow=0; rsi=0; atr_pts=0;
   if(UseMA)
   {
      if(ma_fast_handle==INVALID_HANDLE || ma_slow_handle==INVALID_HANDLE) return false;
      if(!ReadIndicatorValue(ma_fast_handle,InpMAfast+5,ma_fast,1)) return false;
      if(!ReadIndicatorValue(ma_slow_handle,InpMAslow+5,ma_slow,1)) return false;
   }

   if(UseRSI)
   {
      if(rsi_handle==INVALID_HANDLE) return false;
      if(!ReadIndicatorValue(rsi_handle,InpRSI_Period+5,rsi,1)) return false;
   }
   else
   {
      rsi=50.0;
   }

   if(!GetATR_Points_PerBar(atr_pts)) return false;
   return true;
}

int TechDirection(double ma_fast,double ma_slow,double rsi,string &reason)
{
   reason="";
   int dir=0;

   if(UseMA)
   {
      if(ma_fast>ma_slow)      dir=+1;
      else if(ma_fast<ma_slow) dir=-1;
      else { reason=StringFormat("MA_equal fast=%.5f slow=%.5f",ma_fast,ma_slow); dir=0; }
   }

   if(UseRSI)
   {
      if(dir>0 && rsi>InpRSI_BuyMax)
      { reason=StringFormat("RSI_overbought rsi=%.1f BuyMax=%.1f",rsi,InpRSI_BuyMax); dir=0; }
      if(dir<0 && rsi<InpRSI_SellMin)
      { reason=StringFormat("RSI_oversold rsi=%.1f SellMin=%.1f",rsi,InpRSI_SellMin); dir=0; }

      if(!UseMA)
      {
         int d=0;
         if(rsi<=InpRSI_BuyMax)           d=+1;
         if(rsi>=InpRSI_SellMin)          d=-1;
         if(InpRSI_BuyMax>=InpRSI_SellMin) d=0;
         if(d==0 && reason=="")
            reason=StringFormat("RSI_no_dir rsi=%.1f BuyMax=%.1f SellMin=%.1f",rsi,InpRSI_BuyMax,InpRSI_SellMin);
         dir=d;
      }
   }
   return dir;
}

//============================== END PART 1/4 =============================

//============================== PART 2 / 4 ==============================

//====================================================
//================== NEWS CALENDAR ===================
bool StringListHas(const string &csv,const string &needle)
{
   if(csv=="") return true;
   string upNeedle=UpperCopy(needle);
   string parts[]; int n=StringSplit(csv,',',parts);
   for(int i=0;i<n;i++)
   {
      string x=UpperCopy(parts[i]);
      TrimStr(x);
      if(x==upNeedle) return true;
   }
   return false;
}

bool EventCurrencyAndImpact(const MqlCalendarValue &val,string &cur_out,int &imp_out)
{
   cur_out=""; imp_out=0;
   MqlCalendarEvent ev;
   if(!CalendarEventById(val.event_id,ev)) return false;

   int imp=ev.importance;
   if(imp<=0 || imp>3) imp=MathMax(1,MathMin(3,imp+1));
   imp_out=imp;

   MqlCalendarCountry c;
   string cur="";
   if(CalendarCountryById(ev.country_id,c)) cur=c.currency;
   TrimStr(cur);
   cur_out=UpperCopy(cur);
   return (cur_out!="");
}

bool IsSymbolAffectedByCurrency(const string &sym,const string &cur)
{
   if(cur=="") return false;
   if(Cal_Currencies!="") return StringListHas(Cal_Currencies,cur);
   return (StringFind(UpperCopy(sym),cur)>=0);
}

bool UseCalendarEffective()
{
   if(IsTester() && BT_DisableCalendar) return false;
   bool useCal=UseCalendarNews;
   if(LC_Has) useCal=LC_UseCal;
   return useCal;
}

bool IsWithinNewsWindow_Calendar()
{
   if(!UseCalendarEffective()) return false;

   int pre=Cal_NoTradeBeforeMin;
   int post=Cal_NoTradeAfterMin;
   if(LC_Has)
   {
      if(LC_NewsBefore>0) pre=LC_NewsBefore;
      if(LC_NewsAfter>0) post=LC_NewsAfter;
   }

   datetime now=TimeCurrent();
   datetime from=now-(pre+1)*60;
   datetime to=now+(post+1)*60;

   MqlCalendarValue vals[];
   int n=CalendarValueHistory(vals,from,to,"");
   if(n<=0) return false;

   int minImp=MathMax(1,MathMin(3,(LC_Has && LC_MinImpact>0 ? LC_MinImpact : Cal_MinImpact)));

   for(int i=0;i<ArraySize(vals);i++)
   {
      string cur; int imp;
      if(!EventCurrencyAndImpact(vals[i],cur,imp)) continue;
      if(imp<minImp) continue;
      if(!IsSymbolAffectedByCurrency(G_Symbol,cur)) continue;

      datetime evt=vals[i].time;
      datetime start=evt-pre*60;
      datetime end=evt+post*60;

      if(now>=start && now<=end)
      {
         last_evt_time=evt;
         last_evt_currency=cur;
         last_evt_impact=imp;
         if(InpDebugVerbose)
            PrintFormat("[CAL] BLOCK %s cur=%s imp=%d evt=%s",G_Symbol,cur,imp,TimeToString(evt,TIME_MINUTES));
         return true;
      }
   }
   return false;
}

void EE_GetWindows(datetime now,bool &in_pre,bool &in_post,datetime &evt_out,string &cur_out,int &imp_out)
{
   in_pre=false; in_post=false;
   evt_out=0; cur_out=""; imp_out=0;
   if(!UseEmergencyExit) return;
   if(IsTester() && BT_DisableCalendar) return;

   int minImp=MathMax(1,MathMin(3,EE_NewsMinImpact));
   datetime from=now-60;
   datetime to=now+(MathMax(EE_NewsBeforeMin,EE_NewsAfterMin)+1)*60;

   MqlCalendarValue vals[];
   int n=CalendarValueHistory(vals,from,to,"");
   if(n<=0) return;

   for(int i=0;i<n;i++)
   {
      string cur; int imp;
      if(!EventCurrencyAndImpact(vals[i],cur,imp)) continue;
      if(imp<minImp) continue;
      if(!IsSymbolAffectedByCurrency(G_Symbol,cur)) continue;

      datetime evt=vals[i].time;
      datetime pre=evt-EE_NewsBeforeMin*60;
      datetime post=evt+EE_NewsAfterMin*60;

      if(now>=pre && now<evt){ in_pre=true; evt_out=evt; cur_out=cur; imp_out=imp; break; }
      if(now>=evt && now<=post){ in_post=true; evt_out=evt; cur_out=cur; imp_out=imp; break; }
   }
}

string NewsLevelString()
{
   if(!UseCalendarEffective()) return "OFF";
   if(last_evt_time==0) return "NONE";
   return StringFormat("%s_%d",last_evt_currency,last_evt_impact);
}

//====================================================
//================ AI INI / LIVE CONFIG ==============
void San2(string &s)
{
   StringTrimLeft(s); StringTrimRight(s);
   if(StringLen(s)>0 && (ushort)StringGetCharacter(s,0)==0xFEFF) s=StringSubstr(s,1);
}

string CleanSym(string s)
{
   string u=UpperCopy(s), out="";
   for(int i=0;i<StringLen(u);i++)
   {
      ushort ch=(ushort)StringGetCharacter(u,i);
      if(ch>='A' && ch<='Z') out+=U16CharToString(ch);
   }
   return out;
}

bool SymbolsEquivalent(string a,string b)
{
   a=CleanSym(a); b=CleanSym(b);
   if(a==""||b=="") return false;
   return (StringFind(a,b)>=0 || StringFind(b,a)>=0);
}

datetime ParseFlexibleTime(const string raw)
{
   string s=raw,t="";
   StringTrimLeft(s); StringTrimRight(s);
   if(s=="") return 0;

   bool allDigits=true;
   for(int i=0;i<StringLen(s);i++)
   {
      ushort ch=(ushort)StringGetCharacter(s,i);
      if(!(ch>='0'&&ch<='9')){ allDigits=false; break; }
   }
   if(allDigits && StringLen(s)>=10)
   {
      long ep=(long)StringToInteger(s);
      if(ep>0) return (datetime)ep;
   }

   datetime dt=(datetime)StringToTime(s);
   if(dt>0) return dt;

   t=s;
   StringReplace(t,"T"," ");
   StringReplace(t,"Z","");
   StringReplace(t,"/","-");
   dt=(datetime)StringToTime(t);
   if(dt>0) return dt;

   return 0;
}

bool ParseAIFromHandle2(const int fh,
                        string &s_ts,string &s_symbol,string &s_direction,
                        string &s_confidence,string &s_reason,string &s_hold,
                        string &s_rr,string &s_risk)
{
   s_ts=s_symbol=s_direction=s_confidence=s_reason=s_hold=s_rr=s_risk="";
   FileSeek(fh,0,SEEK_SET);
   while(!FileIsEnding(fh))
   {
      string ln=FileReadString(fh);
      if(ln=="") continue;
      int eq=StringFind(ln,"=");
      if(eq<=0) continue;
      string k=StringSubstr(ln,0,eq);
      string v=StringSubstr(ln,eq+1);
      San2(k); San2(v);
      k=LowerCopy(k);

      if(k=="ts"||k=="time"||k=="timestamp") s_ts=v;
      else if(k=="symbol"||k=="sym") s_symbol=v;
      else if(k=="direction"||k=="dir") s_direction=v;
      else if(k=="confidence"||k=="conf") s_confidence=v;
      else if(k=="rationale"||k=="reason") s_reason=v;
      else if(k=="hold_minutes"||k=="hold"||k=="hold_min") s_hold=v;
      else if(k=="rr"||k=="risk_reward") s_rr=v;
      else if(k=="risk_pct"||k=="risk"||k=="risk_percent") s_risk=v;
   }
   return !(s_ts==""&&s_symbol==""&&s_direction==""&&s_confidence==""&&s_hold==""&&s_rr==""&&s_risk=="");
}

void Debug_AI_Path(const string rel)
{
   string common=TerminalInfoString(TERMINAL_COMMONDATA_PATH);
   PrintFormat("[AI] COMMON=%s  REL=%s",common,rel);
   bool exists=FileIsExist(rel,FILE_COMMON);
   PrintFormat("[AI] exists=%s at Common\\Files\\%s",exists?"true":"false",rel);
}

double _num(const string s){ string t=s; San2(t); return StringToDouble(t); }
int _int(const string s){ string t=s; San2(t); return (int)StringToInteger(t); }
bool _bools(const string s)
{
   string t=UpperCopy(s); TrimStr(t);
   return (t=="1"||t=="TRUE"||t=="YES"||t=="Y"||t=="ON");
}

string _json_val(const string &src,const string key)
{
   string kq="\""+key+"\"";
   int p=StringFind(src,kq);
   if(p<0) return "";
   p=StringFind(src,":",p);
   if(p<0) return "";

   int i=p+1;
   while(i<StringLen(src) && (ushort)StringGetCharacter(src,i)<=32) i++;

   string out="";
   bool inq=false;
   for(; i<StringLen(src); i++)
   {
      ushort ch=(ushort)StringGetCharacter(src,i);
      if(ch=='\"'){ inq=!inq; continue; }
      if(!inq && (ch==','||ch=='}'||ch=='\r'||ch=='\n')) break;
      out+=U16CharToString(ch);
   }

   TrimStr(out);
   if(StringLen(out)>=2 && StringGetCharacter(out,0)=='\"' && StringGetCharacter(out,StringLen(out)-1)=='\"')
      out=StringSubstr(out,1,StringLen(out)-2);

   return out;
}

void LC_Reset()
{
   LC_Has=false; LC_Shadow=false;
   LC_AI_MinConf=LC_RR=LC_RiskPct=-1;
   LC_MaxSpread=-1;
   LC_TS_Start=LC_TS_Step=-1;
   LC_BE_Trig=LC_BE_Offs=-1;
   LC_MaxOpen=LC_MaxPerDay=-1;
   LC_NewsBefore=-1; LC_NewsAfter=-1; LC_MinImpact=-1;
   LC_UseCal=true;

   LC_BE_MinR=-1; LC_BE_MinGainPtsExtra=-1; LC_BE_SpreadMul=-1;
   LC_TS_MinDeltaModifyPts=-1; LC_TS_CooldownBars=-1;
   LC_MinSL_Gap_ATR=-1; LC_MinSL_Gap_SprdMul=-1;
}

void LC_Apply()
{
   LC_Has=true;
   if(ForceShadowOff) LC_Shadow=false;

   if(InpDebug)
   {
      PrintFormat("[EA 8.00] LiveConfig OK (shadow=%s, ai_min_conf=%.3f, rr=%.3f, risk_pct=%.3f, maxSpr=%d)",
                  (LC_Shadow?"true":"false"),
                  (LC_AI_MinConf>0?LC_AI_MinConf:AI_MinConfidence),
                  (LC_RR>0?LC_RR:InpRR),
                  (LC_RiskPct>0?LC_RiskPct:InpRiskPct),
                  (LC_MaxSpread>0?LC_MaxSpread:InpMaxSpreadPts));
   }
}

bool LC_Load_JSON(const string file)
{
   int h=FileOpen(file,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_COMMON);
   if(h==INVALID_HANDLE) h=FileOpen(file,FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(h==INVALID_HANDLE) return false;

   string s="";
   while(!FileIsEnding(h)) s+=FileReadString(h)+"\n";
   FileClose(h);

   LC_Reset();

   string v;
   v=_json_val(s,"shadow");                  if(v!="") LC_Shadow=_bools(v);
   v=_json_val(s,"ai_min_confidence");       if(v!="") LC_AI_MinConf=_num(v);
   v=_json_val(s,"rr");                      if(v!="") LC_RR=_num(v);
   v=_json_val(s,"risk_pct");                if(v!="") LC_RiskPct=_num(v);
   v=_json_val(s,"max_spread_pts");          if(v!="") LC_MaxSpread=_int(v);
   v=_json_val(s,"ts_start");                if(v!="") LC_TS_Start=_int(v);
   v=_json_val(s,"ts_step");                 if(v!="") LC_TS_Step=_int(v);
   v=_json_val(s,"be_trig");                 if(v!="") LC_BE_Trig=_int(v);
   v=_json_val(s,"be_offs");                 if(v!="") LC_BE_Offs=_int(v);
   v=_json_val(s,"max_open_per_symbol");     if(v!="") LC_MaxOpen=_int(v);
   v=_json_val(s,"max_trades_per_day");      if(v!="") LC_MaxPerDay=_int(v);
   v=_json_val(s,"use_calendar");            if(v!="") LC_UseCal=_bools(v);
   v=_json_val(s,"cal_no_trade_before_min"); if(v!="") LC_NewsBefore=_int(v);
   v=_json_val(s,"cal_no_trade_after_min");  if(v!="") LC_NewsAfter=_int(v);
   v=_json_val(s,"cal_min_impact");          if(v!="") LC_MinImpact=_int(v);

   v=_json_val(s,"BE_MinR");               if(v!="") LC_BE_MinR=_num(v);
   v=_json_val(s,"BE_MinGainPtsExtra");    if(v!="") LC_BE_MinGainPtsExtra=_int(v);
   v=_json_val(s,"BE_SpreadMul");          if(v!="") LC_BE_SpreadMul=_num(v);
   v=_json_val(s,"TS_MinDeltaModifyPts");  if(v!="") LC_TS_MinDeltaModifyPts=_int(v);
   v=_json_val(s,"TS_CooldownBars");       if(v!="") LC_TS_CooldownBars=_int(v);
   v=_json_val(s,"MinSL_Gap_ATR");         if(v!="") LC_MinSL_Gap_ATR=_num(v);
   v=_json_val(s,"MinSL_Gap_SprdMul");     if(v!="") LC_MinSL_Gap_SprdMul=_num(v);

   LC_Apply();
   return true;
}

bool LC_Load_INI(const string file)
{
   int h=FileOpen(file,FILE_READ|FILE_TXT|FILE_SHARE_READ|FILE_COMMON);
   if(h==INVALID_HANDLE) h=FileOpen(file,FILE_READ|FILE_TXT|FILE_SHARE_READ);
   if(h==INVALID_HANDLE) return false;

   LC_Reset();
   while(!FileIsEnding(h))
   {
      string ln=FileReadString(h);
      if(StringLen(ln)==0) continue;
      int p=StringFind(ln,"=");
      if(p<=0) continue;

      string k=StringSubstr(ln,0,p);
      string v=StringSubstr(ln,p+1);
      San2(k); San2(v);
      k=LowerCopy(k);

      if(k=="shadow") LC_Shadow=_bools(v);
      else if(k=="ai_min_confidence") LC_AI_MinConf=_num(v);
      else if(k=="rr"||k=="risk_reward") LC_RR=_num(v);
      else if(k=="risk_pct") LC_RiskPct=_num(v);
      else if(k=="max_spread_pts") LC_MaxSpread=_int(v);
      else if(k=="ts_start") LC_TS_Start=_int(v);
      else if(k=="ts_step") LC_TS_Step=_int(v);
      else if(k=="be_trig") LC_BE_Trig=_int(v);
      else if(k=="be_offs") LC_BE_Offs=_int(v);
      else if(k=="max_open_per_symbol") LC_MaxOpen=_int(v);
      else if(k=="max_trades_per_day") LC_MaxPerDay=_int(v);
      else if(k=="use_calendar") LC_UseCal=_bools(v);
      else if(k=="cal_no_trade_before_min") LC_NewsBefore=_int(v);
      else if(k=="cal_no_trade_after_min") LC_NewsAfter=_int(v);
      else if(k=="cal_min_impact") LC_MinImpact=_int(v);

      else if(k=="be_minr") LC_BE_MinR=_num(v);
      else if(k=="be_mingainptsextra") LC_BE_MinGainPtsExtra=_int(v);
      else if(k=="be_spreadmul") LC_BE_SpreadMul=_num(v);
      else if(k=="ts_mindeltamodifypts") LC_TS_MinDeltaModifyPts=_int(v);
      else if(k=="ts_cooldownbars") LC_TS_CooldownBars=_int(v);
      else if(k=="minsl_gap_atr") LC_MinSL_Gap_ATR=_num(v);
      else if(k=="minsl_gap_sprdmul") LC_MinSL_Gap_SprdMul=_num(v);
   }
   FileClose(h);
   LC_Apply();
   return true;
}

void LC_TryReload(bool force=false)
{
   if(!UseLiveConfig) return;
   static datetime last=0;
   datetime now=TimeCurrent();
   if(!force && now-last<LiveConfigRefreshMin*60) return;
   last=now;

   bool ok=false;
   string low=LowerCopy(LiveConfigFile);
   if(StringFind(low,".json")>=0) ok=LC_Load_JSON(LiveConfigFile);
   else ok=LC_Load_INI(LiveConfigFile);

   if(!ok && InpDebug) Print("[LC] no config loaded");
}

//====================================================
//==================== AI LOCAL ======================
double EffectiveBaseMinConf()
{
   double base=(LC_Has && LC_AI_MinConf>0 ? LC_AI_MinConf : AI_MinConfidence);
   if(ForceInputConfidence) base=ForcedConfidenceValue;
   return base;
}

double RegimeExtraConf()
{
   if(!UseRegimeExtraConf) return 0.0;
   if(G_Regime==REG_RANGE) return RangeExtraConf;
   if(G_Regime==REG_TREND) return TrendExtraConf;
   if(G_Regime==REG_HIGHVOL) return HighVolExtraConf;
   return 0.0;
}

bool ReadAISignal_Local(int &dir_out,double &conf_out,string &reason_out,int &hold_min,double &rr_out,double &risk_pct_out)
{
   dir_out=0; conf_out=0; reason_out=""; hold_min=0; rr_out=0; risk_pct_out=0;

   if(!UseAISignals){ reason_out="ai_off"; return false; }
   if(IsTester() && !BT_AllowLocalAIFile){ reason_out="bt_ai_off"; return false; }

   if(InpDebug) Debug_AI_Path(AISignalFile);

   bool exists_common=FileIsExist(AISignalFile,FILE_COMMON);
   bool exists_local=FileIsExist(AISignalFile);

   if(!exists_common && !exists_local)
   {
      reason_out="ai_file_missing";
      if(InpDebug) Print("[AI] not found: ",AISignalFile);
      return false;
   }

   int h=INVALID_HANDLE;

   if(exists_common)
      h=FileOpen(AISignalFile,FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ|FILE_UNICODE);
   if(h==INVALID_HANDLE && exists_common)
      h=FileOpen(AISignalFile,FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);

   if(h==INVALID_HANDLE && exists_local)
      h=FileOpen(AISignalFile,FILE_READ|FILE_TXT|FILE_SHARE_READ|FILE_UNICODE);
   if(h==INVALID_HANDLE && exists_local)
      h=FileOpen(AISignalFile,FILE_READ|FILE_TXT|FILE_SHARE_READ);

   if(h==INVALID_HANDLE){ reason_out="ai_open_fail"; return false; }

   string s_ts,s_symbol,s_direction,s_confidence,s_reason,s_hold,s_rr,s_risk;
   bool any=ParseAIFromHandle2(h,s_ts,s_symbol,s_direction,s_confidence,s_reason,s_hold,s_rr,s_risk);
   FileClose(h);

   if(!any){ reason_out="ai_empty"; if(InpDebug) Print("[AI] empty ini"); return false; }

   string chart_sym=Sym();
   if(s_symbol!="" && !SymbolsEquivalent(s_symbol,chart_sym))
   {
      reason_out="ai_symbol_mismatch";
      if(InpDebug) Print("[AI] symbol mismatch (file=",s_symbol," chart=",chart_sym,")");
      return false;
   }

   datetime now=TimeCurrent();
   datetime ts_val=ParseFlexibleTime(s_ts);
   if(ts_val>0 && (int)(now-ts_val)>AI_FreshSeconds)
   {
      reason_out="ai_stale";
      if(InpDebug) Print("[AI] stale");
      return false;
   }

   s_direction=UpperCopy(s_direction);
   if(s_direction=="FLAT"){ reason_out="ai_flat"; return false; }

   int dir=(s_direction=="BUY")?+1:(s_direction=="SELL"?-1:0);
   if(dir==0){ reason_out="ai_dir_invalid"; return false; }

   double baseMin=EffectiveBaseMinConf();
   double minEff=baseMin + RegimeExtraConf();

   double rawConf=(s_confidence=="" ? baseMin : StringToDouble(s_confidence));
   double effConf=rawConf;

   if(effConf<minEff)
   {
      reason_out="ai_low_conf";
      if(InpDebug)
         PrintFormat("[AI] low conf: conf=%.2f minEff=%.2f (base=%.2f extra=%.2f)",effConf,minEff,baseMin,RegimeExtraConf());
      return false;
   }

   double rr=(s_rr=="" ? (LC_Has && LC_RR>0 ? LC_RR : InpRR) : StringToDouble(s_rr));
   double rsk=(s_risk=="" ? (LC_Has && LC_RiskPct>0 ? LC_RiskPct : InpRiskPct) : StringToDouble(s_risk));
   int hold=(s_hold=="" ? AI_MaxHoldMinutes : (int)StringToInteger(s_hold));

   dir_out=dir;
   conf_out=effConf;
   reason_out=(s_reason=="" ? "local" : s_reason);
   hold_min=hold;
   rr_out=rr;
   risk_pct_out=rsk;

   ai_dir_hint=dir;
   ai_conf_hint=effConf;
   ai_reason_hint=reason_out;
   ai_hold_until=now + hold*60;

   return true;
}

//====================================================
//==================== CLOUD =========================
string _hdr_auth(){ if(Cloud_ApiKey=="") return ""; return "Authorization: Bearer "+Cloud_ApiKey; }

bool Cloud_Allowed()
{
   if(!Cloud_Enable) return false;
   if(IsTester() && BT_DisableCloud) return false;
   return true;
}

bool Cloud_Web(const string method,const string path,const string body,string &resp,int timeout_ms=4000)
{
   resp="";
   if(!Cloud_Allowed()) return false;
   if(Cloud_BaseURL==""){ if(InpDebug) Print("[CLOUD] BaseURL empty"); return false; }

   string url=Cloud_BaseURL+path;

   string headers=_hdr_auth();
   if(headers!="") headers+="\r\nContent-Type: application/json";
   else headers="Content-Type: application/json";

   uchar data[];
   uchar result[];
   string result_headers="";
   int code=-1;

   ResetLastError();

   if(method=="GET")
   {
      uchar empty[]; ArrayResize(empty,0);
      code=WebRequest("GET",url,headers,timeout_ms,empty,result,result_headers);
   }
   else
   {
      StringToCharArray(body,data,0,WHOLE_ARRAY);
      code=WebRequest(method,url,headers,timeout_ms,data,result,result_headers);
   }

   if(code==-1)
   {
      int e=GetLastError();
      if(InpDebug) PrintFormat("[CLOUD] WebRequest error=%d url=%s",e,url);
      ResetLastError();
      return false;
   }

   resp=CharArrayToString(result,0,-1);

   // PATCH: enforce HTTP 2xx only
   if(code<200 || code>=300)
   {
      if(InpDebug)
         PrintFormat("[CLOUD] HTTP=%d url=%s resp_len=%d",code,url,StringLen(resp));
      return false;
   }

   return true;
}

bool Cloud_ValidateLicense()
{
   if(!Cloud_Allowed()) return false;
   if(Cloud_LicenseToken=="" || Cloud_AccountId==""){ if(InpDebug) Print("[CLOUD] license/account empty"); return false; }

   // v9: include MT5 account number for server-side binding
   long mt5_account = AccountInfoInteger(ACCOUNT_LOGIN);

   string payload=StringFormat("{\"account_id\":\"%s\",\"license\":\"%s\",\"symbol\":\"%s\",\"magic\":%I64d,\"mt5_account\":%I64d}",
                               JSON_Escape(Cloud_AccountId),JSON_Escape(Cloud_LicenseToken),
                               JSON_Escape(Sym()),InpMagic,mt5_account);

   string resp;
   bool ok=Cloud_Web("POST","/v1/ea/license/validate",payload,resp,5000);
   if(!ok){ if(InpDebug) Print("[CLOUD] license request failed"); return false; }

   bool allow=(StringFind(resp,"\"valid\":true")>=0 || StringFind(UpperCopy(resp),"\"STATUS\":\"OK\"")>=0);
   g_license_ok=allow;

   if(!allow)
   {
      Print("[CLOUD] license invalid/expired — trading blocked");
      // v9: immediate block on invalid license (no waiting for next cycle)
      if(Cloud_BlockTradingIfInvalidLicense) g_license_ok=false;
   }
   return allow;
}

bool Cloud_FetchLiveConfig()
{
   if(!Cloud_Allowed()) return false;
   string path=StringFormat("/v1/ea/config?symbol=%s&magic=%I64d",Sym(),InpMagic);
   string resp;
   if(!Cloud_Web("GET",path,"",resp,5000)) return false;

   LC_Reset();

   string v;
   v=_json_val(resp,"shadow");                  if(v!="") LC_Shadow=_bools(v);
   v=_json_val(resp,"ai_min_confidence");       if(v!="") LC_AI_MinConf=_num(v);
   v=_json_val(resp,"rr");                      if(v!="") LC_RR=_num(v);
   v=_json_val(resp,"risk_pct");                if(v!="") LC_RiskPct=_num(v);
   v=_json_val(resp,"max_spread_pts");          if(v!="") LC_MaxSpread=_int(v);
   v=_json_val(resp,"ts_start");                if(v!="") LC_TS_Start=_int(v);
   v=_json_val(resp,"ts_step");                 if(v!="") LC_TS_Step=_int(v);
   v=_json_val(resp,"be_trig");                 if(v!="") LC_BE_Trig=_int(v);
   v=_json_val(resp,"be_offs");                 if(v!="") LC_BE_Offs=_int(v);
   v=_json_val(resp,"max_open_per_symbol");     if(v!="") LC_MaxOpen=_int(v);
   v=_json_val(resp,"max_trades_per_day");      if(v!="") LC_MaxPerDay=_int(v);
   v=_json_val(resp,"use_calendar");            if(v!="") LC_UseCal=_bools(v);
   v=_json_val(resp,"cal_no_trade_before_min"); if(v!="") LC_NewsBefore=_int(v);
   v=_json_val(resp,"cal_no_trade_after_min");  if(v!="") LC_NewsAfter=_int(v);
   v=_json_val(resp,"cal_min_impact");          if(v!="") LC_MinImpact=_int(v);

   v=_json_val(resp,"BE_MinR");               if(v!="") LC_BE_MinR=_num(v);
   v=_json_val(resp,"BE_MinGainPtsExtra");    if(v!="") LC_BE_MinGainPtsExtra=_int(v);
   v=_json_val(resp,"BE_SpreadMul");          if(v!="") LC_BE_SpreadMul=_num(v);
   v=_json_val(resp,"TS_MinDeltaModifyPts");  if(v!="") LC_TS_MinDeltaModifyPts=_int(v);
   v=_json_val(resp,"TS_CooldownBars");       if(v!="") LC_TS_CooldownBars=_int(v);
   v=_json_val(resp,"MinSL_Gap_ATR");         if(v!="") LC_MinSL_Gap_ATR=_num(v);
   v=_json_val(resp,"MinSL_Gap_SprdMul");     if(v!="") LC_MinSL_Gap_SprdMul=_num(v);

   LC_Apply();
   return true;
}

void Cloud_Heartbeat()
{
   if(!Cloud_Allowed()) return;

   MqlTick tk;
   if(!SymbolInfoTick(G_Symbol,tk)) return;

   string payload=StringFormat("{\"account_id\":\"%s\",\"symbol\":\"%s\",\"equity\":%.2f,\"balance\":%.2f,\"bid\":%.5f,\"ask\":%.5f,\"magic\":%I64d}",
                               JSON_Escape(Cloud_AccountId),JSON_Escape(Sym()),
                               AccountInfoDouble(ACCOUNT_EQUITY),AccountInfoDouble(ACCOUNT_BALANCE),
                               tk.bid,tk.ask,InpMagic);

   string resp;
   Cloud_Web("POST","/v1/ea/heartbeat",payload,resp,3000);
}

bool Cloud_TryFetchAI(int &dir_out,double &conf_out,string &reason_out,int &hold_min,double &rr_out,double &risk_pct_out)
{
   dir_out=0; conf_out=0; reason_out=""; hold_min=0; rr_out=0; risk_pct_out=0;

   if(!Cloud_Allowed() || !Cloud_FetchAI || (!g_license_ok && Cloud_BlockTradingIfInvalidLicense)) return false;

   string path=StringFormat("/v1/ea/ai-signal?symbol=%s&magic=%I64d",Sym(),InpMagic);
   string resp;
   if(!Cloud_Web("GET",path,"",resp,4000)) return false;

   string sdir=_json_val(resp,"direction");
   sdir=UpperCopy(sdir);
   if(sdir!="BUY" && sdir!="SELL") return false;

   int dir=(sdir=="BUY")?+1:-1;

   string sconf=_json_val(resp,"confidence");
   double conf=(sconf==""? EffectiveBaseMinConf() : StringToDouble(sconf));

   double minEff=EffectiveBaseMinConf()+RegimeExtraConf();
   if(conf<minEff) return false;

   string srr=_json_val(resp,"rr");
   double rr=(srr==""? (LC_Has && LC_RR>0 ? LC_RR : InpRR) : StringToDouble(srr));

   string srisk=_json_val(resp,"risk_pct");
   double risk=(srisk==""? (LC_Has && LC_RiskPct>0 ? LC_RiskPct : InpRiskPct) : StringToDouble(srisk));

   string shold=_json_val(resp,"hold_minutes");
   int hold=(shold==""? AI_MaxHoldMinutes : (int)StringToInteger(shold));

   string rsn=_json_val(resp,"reason");
   if(rsn=="") rsn="cloud";

   dir_out=dir; conf_out=conf; reason_out=rsn;
   hold_min=hold; rr_out=rr; risk_pct_out=risk;

   ai_dir_hint=dir;
   ai_conf_hint=conf;
   ai_reason_hint=rsn;
   ai_hold_until=TimeCurrent()+hold*60;

   return true;
}

//====================================================
//==================== AUTO CONFIG ===================
void DoAutoConfig()
{
   // v9: Real adaptive config — not just copying inputs

   // Start from input defaults
   G_RR               = InpRR;
   G_MaxSpreadPts     = InpMaxSpreadPts;
   G_SpreadSL_Factor  = InpSpreadSLFactor;
   G_CommissionPerLot = InpCommissionPerLot;

   // --- Adapt ATR multiplier based on recent spread ---
   double atr_pts=0;
   bool hasATR=GetATR_Points_PerBar(atr_pts);

   MqlTick tk;
   double spread_pts=0;
   if(SymbolInfoTick(G_Symbol,tk))
      spread_pts=(tk.ask-tk.bid)/G_Point;

   if(hasATR && atr_pts>0 && spread_pts>0)
   {
      double spread_ratio=spread_pts/atr_pts;

      // High spread relative to ATR → widen SL multiplier to avoid premature stops
      if(spread_ratio > 0.30)
         G_ATR_SL_MULT = MathMin(InpATR_SL_Mult * 1.30, 4.0);
      else if(spread_ratio > 0.20)
         G_ATR_SL_MULT = MathMin(InpATR_SL_Mult * 1.15, 3.5);
      else
         G_ATR_SL_MULT = InpATR_SL_Mult;

      // High spread → reduce max allowed spread threshold proportionally
      if(spread_pts > InpMaxSpreadPts * 0.80)
         G_MaxSpreadPts = (int)(InpMaxSpreadPts * 1.20);
      else
         G_MaxSpreadPts = InpMaxSpreadPts;

      if(InpDebug)
         PrintFormat("[AutoConfig] spread_pts=%.1f atr_pts=%.1f ratio=%.3f => ATR_SL_MULT=%.2f MaxSpread=%d",
                     spread_pts,atr_pts,spread_ratio,G_ATR_SL_MULT,G_MaxSpreadPts);
   }
   else
   {
      // fallback to inputs when indicators not ready
      G_ATR_SL_MULT = InpATR_SL_Mult;
   }
}

//====================================================
//==================== RISK & SAFETY =================
datetime DateOfDay(datetime t){ MqlDateTime dt; TimeToStruct(t,dt); dt.hour=0; dt.min=0; dt.sec=0; return StructToTime(dt); }

int TodayTradesCount()
{
   datetime from=DateOfDay(TimeCurrent());
   if(!HistorySelect(from,TimeCurrent())) return 0;

   int count=0;
   int total=(int)HistoryDealsTotal();
   for(int i=0;i<total;i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      string sym=(string)HistoryDealGetString(ticket,DEAL_SYMBOL);
      long magic=HistoryDealGetInteger(ticket,DEAL_MAGIC);
      long type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);

      if(sym!=G_Symbol) continue;
      if(magic!=InpMagic) continue;

      bool is_in=((type==DEAL_TYPE_BUY || type==DEAL_TYPE_SELL) && (entry==DEAL_ENTRY_IN));
      if(is_in) count++;
   }
   return count;
}

double TodayProfitUSD()
{
   datetime from=DateOfDay(TimeCurrent());
   if(!HistorySelect(from,TimeCurrent())) return 0.0;

   double pnl=0.0;
   int total=(int)HistoryDealsTotal();
   for(int i=0;i<total;i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      string sym=(string)HistoryDealGetString(ticket,DEAL_SYMBOL);
      long magic=HistoryDealGetInteger(ticket,DEAL_MAGIC);
      if(sym!=G_Symbol) continue;
      if(magic!=InpMagic) continue;

      pnl+=HistoryDealGetDouble(ticket,DEAL_PROFIT);
      pnl+=HistoryDealGetDouble(ticket,DEAL_SWAP);
      pnl+=HistoryDealGetDouble(ticket,DEAL_COMMISSION);
   }
   return pnl;
}

void Slip_Update(double s)
{
   if(s<=0.0) return;
   if(g_slip_n==0) g_avg_slip_pts=s;
   else g_avg_slip_pts=(g_avg_slip_pts*g_slip_n+s)/(g_slip_n+1);
   g_slip_n++;
}

bool CheckKillSwitch()
{
   int h=FileOpen(KillFile,FILE_READ|FILE_TXT);
   if(h!=INVALID_HANDLE){ FileClose(h); if(InpDebug) Print("[KILL] file present"); return true; }
   return false;
}

bool InLossCooldown(){ return (TimeCurrent()<G_CooldownUntil); }

bool VolSpikeNow()
{
   double atr_pts=0;
   if(!GetATR_Points_PerBar(atr_pts)) return false;

   // v9: use persistent handle — avoid creating/releasing every call
   if(atr_spike_handle==INVALID_HANDLE)
      atr_spike_handle=iATR(G_Symbol,_Period,ATR_Period_Spike);
   if(atr_spike_handle==INVALID_HANDLE) return false;

   double buf[];
   int got=CopyBuffer(atr_spike_handle,0,0,200,buf);
   if(got<=0) return false;

   int n=MathMin(200,got);
   if(n<5) return false;

   double tmp[];
   ArrayResize(tmp,n);
   for(int i=0;i<n;i++) tmp[i]=buf[i]/G_Point;
   ArraySort(tmp);

   double med=((n%2)==1)? tmp[n/2] : 0.5*(tmp[n/2-1]+tmp[n/2]);
   return (med>0 && atr_pts>ATR_SpikeMult*med);
}

double ExpectedCostPerLot(double spread_pts)
{
   double ticks=(spread_pts*G_Point)/G_TickSize;
   return ticks*G_TickValue + 2.0*G_CommissionPerLot;
}

double NormalizeVolume(double v)
{
   if(G_VolStep<=0.0) return v;
   v=MathMax(G_VolMin,MathMin(G_VolMax,v));
   double steps=MathFloor((v-G_VolMin)/G_VolStep+0.5);
   v=G_VolMin+steps*G_VolStep;
   v=MathMax(G_VolMin,MathMin(G_VolMax,v));
   return v;
}

double LotsByRisk_TC(double riskPct,double sl_points,double spread_pts)
{
   if(sl_points<=0) return G_VolMin;

   double money_risk=AccountInfoDouble(ACCOUNT_BALANCE)*riskPct/100.0;
   double ticks_sl=(sl_points*G_Point)/G_TickSize;
   double risk_per_lot=ticks_sl*G_TickValue;
   double eff_per_lot=MathMax(1e-6,risk_per_lot+ExpectedCostPerLot(spread_pts));
   double lots=money_risk/eff_per_lot;
   return NormalizeVolume(lots);
}

//====================================================
//==================== LOGGER / CSV ==================
void _write_csv_row(const string filename,const string row,bool to_common)
{
   int h=FileOpen(filename,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE);
   if(h==INVALID_HANDLE) h=FileOpen(filename,FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE);
   if(h!=INVALID_HANDLE)
   {
      FileSeek(h,0,SEEK_END);
      FileWriteString(h,row); FileWriteString(h,"\n");
      FileFlush(h); FileClose(h);
   }

   if(to_common)
   {
      int hc=FileOpen(filename,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE|FILE_COMMON);
      if(hc==INVALID_HANDLE) hc=FileOpen(filename,FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE|FILE_COMMON);
      if(hc!=INVALID_HANDLE)
      {
         FileSeek(hc,0,SEEK_END);
         FileWriteString(hc,row); FileWriteString(hc,"\n");
         FileFlush(hc); FileClose(hc);
      }
   }
}

void LogDecisionCSV(const string tag,const string reason,int dir,
                    double ema_fast,double ema_slow,double rsi,double atr,
                    double spread,double slippage,
                    bool useMA,bool useRSI,
                    bool hasAI,int ai_dir,double ai_conf,
                    const string why_src)
{
   if(!EnableLogger) return;

   string row=StringFormat("%s;%s;%d;%.5f;%.5f;%.5f;%.5f;%.5f;%.5f;%d;%d;%d;%d;%.5f;%s;%s",
                           TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS),
                           tag,dir,
                           ema_fast,ema_slow,rsi,atr,
                           spread,slippage,
                           (int)useMA,(int)useRSI,
                           (int)hasAI,ai_dir,ai_conf,
                           reason,why_src);

   _write_csv_row(LogDecisionsCSV,row,(DuplicateLogsToCommon?true:false));
}

void _write_deals_csv(const string row)
{
   const string header="ts,symbol,type,lots,entry_price,exit_price,sl_pts,tp_pts,rr_eff,"
                       "risk_pct,pnl_usd,R_eff,mfe_pts,mae_pts,slippage_pts,spread_pts,reason,pos_id,deal_in,deal_out,open_time,close_time";

   bool need_header_local=!FileIsExist(LogDealsCSV);
   int h=FileOpen(LogDealsCSV,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE);
   if(h==INVALID_HANDLE){ h=FileOpen(LogDealsCSV,FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE); need_header_local=true; }
   if(h!=INVALID_HANDLE)
   {
      FileSeek(h,0,SEEK_END);
      if(need_header_local && FileSize(h)==0){ FileWriteString(h,header); FileWriteString(h,"\n"); }
      FileWriteString(h,row); FileWriteString(h,"\n");
      FileFlush(h); FileClose(h);
   }
   else
   {
      Print("[DEALS_CSV] Local FileOpen failed. path=",LogDealsCSV," err=",GetLastError());
   }

   if(DuplicateLogsToCommon)
   {
      bool need_header_common=!FileIsExist(CommonDealsName,FILE_COMMON);
      int hc=FileOpen(CommonDealsName,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE|FILE_COMMON);
      if(hc==INVALID_HANDLE){ hc=FileOpen(CommonDealsName,FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE|FILE_COMMON); need_header_common=true; }
      if(hc!=INVALID_HANDLE)
      {
         FileSeek(hc,0,SEEK_END);
         if(need_header_common && FileSize(hc)==0){ FileWriteString(hc,header); FileWriteString(hc,"\n"); }
         FileWriteString(hc,row); FileWriteString(hc,"\n");
         FileFlush(hc); FileClose(hc);
      }

      // Also mirror to the SAME relative path as LogDealsCSV inside Common\\Files.
      // This ensures that if LogDealsCSV is "logs/deals_B2.csv" then the Common copy
      // will be placed under Common\\Files\\logs\\deals_B2.csv (same as decisions).
      bool need_header_common2=!FileIsExist(LogDealsCSV,FILE_COMMON);
      int hc2=FileOpen(LogDealsCSV,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE|FILE_COMMON);
      if(hc2==INVALID_HANDLE){ hc2=FileOpen(LogDealsCSV,FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE|FILE_COMMON); need_header_common2=true; }
      if(hc2!=INVALID_HANDLE)
      {
         FileSeek(hc2,0,SEEK_END);
         if(need_header_common2 && FileSize(hc2)==0){ FileWriteString(hc2,header); FileWriteString(hc2,"\n"); }
         FileWriteString(hc2,row); FileWriteString(hc2,"\n");
         FileFlush(hc2); FileClose(hc2);
      }
      else
      {
         Print("[DEALS_CSV] Common mirror FileOpen failed. path=",LogDealsCSV," err=",GetLastError());
      }
   }
}

void LogDealCSV2(const string side,double lots,double entry_price,double exit_price,double sl_pts,double tp_pts,
                 double rr_eff,double risk_pct,double pnl_usd,double R_eff,double mfe_pts,double mae_pts,
                 double slippage_pts,double spread_pts,const string reason,
                 datetime open_time,datetime close_time,ulong pos_id,ulong deal_in,ulong deal_out)
{
   if(!EnableLogger) return;
   string ts=TimeToString(TimeCurrent(),TIME_DATE|TIME_SECONDS);
   string ot=(open_time>0?TimeToString(open_time,TIME_DATE|TIME_SECONDS):"");
   string ct=(close_time>0?TimeToString(close_time,TIME_DATE|TIME_SECONDS):"");

   string row=StringFormat("%s,%s,%s,%.2f,%.5f,%.5f,%.1f,%.1f,%.3f,%.3f,%.2f,%.3f,%.1f,%.1f,%.2f,%.2f,%s,%I64u,%I64u,%I64u,%s,%s",
                           ts,Sym(),side,lots,entry_price,exit_price,sl_pts,tp_pts,rr_eff,risk_pct,pnl_usd,R_eff,
                           mfe_pts,mae_pts,slippage_pts,spread_pts,reason,
                           pos_id,deal_in,deal_out,ot,ct);
   _write_deals_csv(row);
}

//====================================================
//==================== JSONL =========================
int _open_jsonl_append(const string fname)
{
   int h=FileOpen(fname,FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_SHARE_WRITE);
   if(h==INVALID_HANDLE) h=FileOpen(fname,FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_WRITE);
   return h;
}

void WriteJSONL_Open(const datetime t,const string sym,const double ai_conf_bkt,const double atr_mult,
                     const double rr,const double risk_pct,const int ts_start,const int ts_step,
                     const int be_trig,const int be_offs,const int max_spread_pts,const int max_trades_per_day,
                     const string news_level,const double spread_open,const double slippage_pts,
                     ulong pos_id)
{
   if(!EnableJSONLTrades) return;

   string tag=(JSONL_SymbolTag!=""?JSONL_SymbolTag:sym);
   string fname=JSONL_FilePrefix+MonthKey(t)+".jsonl";

   int h=_open_jsonl_append(fname);
   if(h==INVALID_HANDLE){ if(InpDebug) Print("[JSONL] open(open) fail ",GetLastError()); ResetLastError(); return; }

   FileSeek(h,0,SEEK_END);
   string ts=TimeToString(t,TIME_DATE|TIME_SECONDS);

   string obj=StringFormat("{\"type\":\"open\",\"time\":\"%s\",\"symbol\":\"%s\",\"pos_id\":%I64u,"
                           "\"ai_conf_bucket\":%.4f,\"atr_mult\":%.6f,\"rr\":%.6f,\"risk_pct\":%.6f,"
                           "\"ts_start\":%d,\"ts_step\":%d,\"be_trig\":%d,\"be_offs\":%d,"
                           "\"max_spread_pts\":%d,\"max_trades_per_day\":%d,"
                           "\"news_level\":\"%s\",\"spread_open\":%.2f,\"slippage_pts\":%.2f}",
                           JSON_Escape(ts),JSON_Escape(tag),pos_id,
                           ai_conf_bkt,atr_mult,rr,risk_pct,
                           ts_start,ts_step,be_trig,be_offs,
                           max_spread_pts,max_trades_per_day,
                           JSON_Escape(news_level),spread_open,slippage_pts);

   FileWriteString(h,obj); FileWriteString(h,"\n");
   FileFlush(h); FileClose(h);
}

void WriteJSONL_Close(const datetime t,const string sym,const double R,const double ai_conf_bkt,
                      const double atr_mult,const double rr,const string news_level,
                      double mfe_pts,double mae_pts,double slip_pts,double spread_open,
                      ulong pos_id,ulong deal_in,ulong deal_out,double pnl_usd)
{
   if(!EnableJSONLTrades) return;

   string tag=(JSONL_SymbolTag!=""?JSONL_SymbolTag:sym);
   string fname=JSONL_FilePrefix+MonthKey(t)+".jsonl";

   int h=_open_jsonl_append(fname);
   if(h==INVALID_HANDLE){ if(InpDebug) Print("[JSONL] open(close) fail ",GetLastError()); ResetLastError(); return; }

   FileSeek(h,0,SEEK_END);
   string ts=TimeToString(t,TIME_DATE|TIME_SECONDS);

   string obj=StringFormat("{\"type\":\"close\",\"time\":\"%s\",\"symbol\":\"%s\",\"pos_id\":%I64u,"
                           "\"deal_in\":%I64u,\"deal_out\":%I64u,\"pnl_usd\":%.2f,"
                           "\"R\":%.6f,\"ai_conf_bucket\":%.4f,\"atr_mult\":%.6f,\"rr\":%.6f,"
                           "\"news_level\":\"%s\",\"mfe_pts\":%.2f,\"mae_pts\":%.2f,"
                           "\"slippage_pts\":%.2f,\"spread_open\":%.2f}",
                           JSON_Escape(ts),JSON_Escape(tag),pos_id,
                           deal_in,deal_out,pnl_usd,
                           R,ai_conf_bkt,atr_mult,rr,
                           JSON_Escape(news_level),
                           mfe_pts,mae_pts,slip_pts,spread_open);

   FileWriteString(h,obj); FileWriteString(h,"\n");
   FileFlush(h); FileClose(h);
}

//============================== END PART 2/4 =============================

//============================== PART 3 / 4 ==============================
// ---- Forward declarations (needed because implementations are later) ----
bool LoadReplayCSV();
bool ReplayFetch(datetime bar_time, int &dir, double &conf);

//====================================================
//==================== META TRACKING =================
struct OpenMeta
{
   ulong    pos_id;
   int      dir;
   double   lots;
   double   sl_pts;
   double   rr_eff;
   double   ai_conf;
   string   news_level;
   datetime open_time;

   double   shadow_sl;
   double   mfe_pts;
   double   mae_pts;
   int      bars_open;
   int      adds_done;

   double   entry_price;
   double   spread_on_open;
   double   slippage_pts;
   double   risk_pct;
   datetime last_bar_time;

   bool     l1_done;
   bool     l2_done;
   double   tp_total_pts;

   ulong    deal_in_ticket;
   bool     close_logged;
};

OpenMeta g_meta[];

bool Meta_Find(const ulong id,OpenMeta &out)
{
   int n=ArraySize(g_meta);
   for(int i=0;i<n;i++){ if(g_meta[i].pos_id==id){ out=g_meta[i]; return true; } }
   return false;
}

int Meta_FindIndex(const ulong id)
{
   int n=ArraySize(g_meta);
   for(int i=0;i<n;i++) if(g_meta[i].pos_id==id) return i;
   return -1;
}

void Meta_Add(const OpenMeta &m)
{
   int n=ArraySize(g_meta);
   for(int i=0;i<n;i++){ if(g_meta[i].pos_id==m.pos_id){ g_meta[i]=m; return; } }
   ArrayResize(g_meta,n+1);
   g_meta[n]=m;
}

void Meta_Update(const OpenMeta &m){ Meta_Add(m); }

void Meta_Remove(const ulong id)
{
   int n=ArraySize(g_meta);
   for(int i=0;i<n;i++)
   {
      if(g_meta[i].pos_id==id)
      {
         for(int j=i+1;j<n;j++) g_meta[j-1]=g_meta[j];
         ArrayResize(g_meta,n-1);
         return;
      }
   }
}

//====================================================
//==================== REGIME ========================
double Eff_BE_MinR(){ return (LC_Has && LC_BE_MinR>0 ? LC_BE_MinR : BE_MinR); }
int    Eff_BE_MinGainExtra(){ return (LC_Has && LC_BE_MinGainPtsExtra>0 ? LC_BE_MinGainPtsExtra : BE_MinGainPtsExtra); }
double Eff_BE_SpreadMul(){ return (LC_Has && LC_BE_SpreadMul>0 ? LC_BE_SpreadMul : BE_SpreadMul); }
int    Eff_TS_MinDeltaModify(){ return (LC_Has && LC_TS_MinDeltaModifyPts>0 ? LC_TS_MinDeltaModifyPts : TS_MinDeltaModifyPts); }
int    Eff_TS_CooldownBars(){ return (LC_Has && LC_TS_CooldownBars>=0 ? LC_TS_CooldownBars : TS_CooldownBars); }
double Eff_MinSL_Gap_ATR(){ return (LC_Has && LC_MinSL_Gap_ATR>0 ? LC_MinSL_Gap_ATR : MinSL_Gap_ATR); }
double Eff_MinSL_Gap_SprdMul(){ return (LC_Has && LC_MinSL_Gap_SprdMul>0 ? LC_MinSL_Gap_SprdMul : MinSL_Gap_SprdMul); }

void SelectEffectiveParams()
{
   E_RR       = (LC_Has && LC_RR>0 ? LC_RR : InpRR);
   E_RiskPct  = (LC_Has && LC_RiskPct>0 ? LC_RiskPct : InpRiskPct);
   E_TS_Start = (LC_Has && LC_TS_Start>0 ? LC_TS_Start : TS_StartPts);
   E_TS_Step  = (LC_Has && LC_TS_Step>0 ? LC_TS_Step : TS_StepPts);
   E_BE_Trig  = (LC_Has && LC_BE_Trig>0 ? LC_BE_Trig : BE_TriggerPts);
   E_BE_Offs  = (LC_Has && LC_BE_Offs>0 ? LC_BE_Offs : BE_OffsetPts);

   if(!UseRegimeDetector) return;

   switch(G_Regime)
   {
      case REG_TREND:
         E_RR=R_TR_RR; E_TS_Start=R_TR_TS_Start; E_TS_Step=R_TR_TS_Step; E_BE_Trig=R_TR_BE_Trig; E_BE_Offs=R_TR_BE_Offs;
         E_RiskPct=E_RiskPct*R_TR_RiskMult;
         break;
      case REG_RANGE:
         E_RR=R_RG_RR; E_TS_Start=R_RG_TS_Start; E_TS_Step=R_RG_TS_Step; E_BE_Trig=R_RG_BE_Trig; E_BE_Offs=R_RG_BE_Offs;
         E_RiskPct=E_RiskPct*R_RG_RiskMult;
         break;
      case REG_HIGHVOL:
         E_RR=R_HV_RR; E_TS_Start=R_HV_TS_Start; E_TS_Step=R_HV_TS_Step; E_BE_Trig=R_HV_BE_Trig; E_BE_Offs=R_HV_BE_Offs;
         E_RiskPct=E_RiskPct*R_HV_RiskMult;
         break;
      default: break;
   }

   if(RG_ScaleRiskByATR)
   {
      double atr_pts=0.0;
      if(GetATR_Points_PerBar(atr_pts) && atr_pts>1.0)
      {
         double s=MathMax(0.25,MathMin(4.0,atr_pts/MathMax(1.0,RG_ATRNormPts)));
         double adj=1.0/s;
         double r=E_RiskPct*adj;
         E_RiskPct=MathMax(RG_RiskMinPct,MathMin(RG_RiskMaxPct,r));
      }
   }
}

ENUM_REGIME DetectRegime()
{
   if(!UseRegimeDetector) return REG_NEUTRAL;

   // --- Baseline volatility (ATR) ---
   double atr_pts=0.0;
   if(!GetATR_Points_PerBar(atr_pts) || atr_pts<=0.0) return REG_NEUTRAL;

   int h=iATR(G_Symbol,_Period,RD_ATR_Period);
   if(h==INVALID_HANDLE) return REG_NEUTRAL;

   double buf[];
   int got=CopyBuffer(h,0,0,RD_MedianWindow,buf);
   IndicatorRelease(h);
   if(got<MathMax(20,RD_ATR_Period+5)) return REG_NEUTRAL;

   int n=got;
   double tmp[];
   ArrayResize(tmp,n);
   for(int i=0;i<n;i++) tmp[i]=buf[i]/G_Point;
   ArraySort(tmp);

   double med=((n%2)==1)? tmp[n/2] : 0.5*(tmp[n/2-1]+tmp[n/2]);
   if(med<=0.0) med=atr_pts;

   bool highVol=(atr_pts>=RD_HighVolMult*med);

   // --- Trend slope from MA (existing) ---
   double f0=0,f1=0,s0=0,s1=0;
   if(ma_fast_handle!=INVALID_HANDLE && ma_slow_handle!=INVALID_HANDLE)
   {
      double fb[2],sb[2];
      if(CopyBuffer(ma_fast_handle,0,0,2,fb)==2 && CopyBuffer(ma_slow_handle,0,0,2,sb)==2)
      { f0=fb[0]; f1=fb[1]; s0=sb[0]; s1=sb[1]; }
   }

   double slope_fast=(f0-f1)/G_Point;
   double slope_slow=(s0-s1)/G_Point;
   double slope_pts=slope_fast - slope_slow;

   double slope_thr=MathMax(10.0,RD_SlopeThreshPts);
   slope_thr=MathMax(slope_thr,RD_TrendSlopeMult*0.10*med);

   bool slopeTrending=(MathAbs(slope_pts)>=slope_thr);

   // --- Range distance from MA separation (existing) ---
   double dist=MathAbs(f0-s0)/G_Point;
   double range_thr=MathMax(10.0,RD_RangeDistMult*med);

   // --- B2: ADX + Bollinger width (optional) ---
   double adx_now=-1.0;
   if(RD_UseADX && adx_handle!=INVALID_HANDLE)
   {
      double ab[1];
      if(CopyBuffer(adx_handle,0,0,1,ab)==1) adx_now=ab[0];
   }

   double bb_width_pts=-1.0;
   if(RD_UseBollinger && bb_handle!=INVALID_HANDLE)
   {
      double up[1], dn[1];
      if(CopyBuffer(bb_handle,0,0,1,up)==1 && CopyBuffer(bb_handle,2,0,1,dn)==1)
         bb_width_pts = (up[0]-dn[0]) / G_Point;
   }

   bool adxTrend = (RD_UseADX && adx_now>0.0 && adx_now>=RD_ADX_Trend);
   bool adxRange = (RD_UseADX && adx_now>0.0 && adx_now<=RD_ADX_RangeMax);

   bool bbTight  = (RD_UseBollinger && bb_width_pts>0.0 && bb_width_pts <= RD_BB_WidthRangeMult*med);
   bool bbWide   = (RD_UseBollinger && bb_width_pts>0.0 && bb_width_pts >= RD_BB_WidthTrendMult*med);

   // --- Final classification ---
   if(highVol) return REG_HIGHVOL;

   bool trending = (adxTrend || (slopeTrending && bbWide));
   bool ranging  = (bbTight && adxRange) || (dist<=range_thr && adxRange);

   if(trending) return REG_TREND;
   if(ranging)  return REG_RANGE;

   // If ADX is not available, fall back to prior logic
   if(!RD_UseADX || adx_now<0.0)
   {
      if(slopeTrending) return REG_TREND;
      if(dist<=range_thr) return REG_RANGE;
   }

   return REG_NEUTRAL;
}

//====================================================
//==================== RM FILTER =====================
bool RM_AllowsNewTrade(string &reason)
{
   if(CheckKillSwitch()){
      reason="kill";
      if(InpDebug) Print("[EA] no trade: KILL file present");
      return false; }
   if(InLossCooldown()){
      reason="cooldown";
      if(InpDebug) Print("[EA] no trade: loss cooldown active");
      return false; }

   MqlTick t;
   if(!SymbolInfoTick(G_Symbol,t)){
      reason="no_tick";
      if(InpDebug) Print("[EA] no trade: SymbolInfoTick failed");
      return false; }

   datetime now=TimeCurrent();
   double spread_pts=(t.ask-t.bid)/G_Point;

   int maxSpr=(LC_Has && LC_MaxSpread>0 ? LC_MaxSpread : G_MaxSpreadPts);
   if(spread_pts>maxSpr){
      reason=StringFormat("spread=%g>%d",spread_pts,maxSpr);
      if(InpDebug) PrintFormat("[EA] no trade: spread=%.1f pts > max=%d",spread_pts,maxSpr);
      return false; }

   if(UseMicrostructureFilters)
   {
      double thr=SP_Percentile(SpreadPercCut,now);
      if(spread_pts>thr){
         reason="spread_percentile";
         if(InpDebug) PrintFormat("[EA] no trade: spread=%.1f above %d%% percentile=%.1f",spread_pts,(int)SpreadPercCut,thr);
         return false; }

      if(UseTickImbalance)
      {
         double imb=TI_Imbalance(now);
         if(imb>0.0 && imb<TickImbalanceMin)
         {
            reason=StringFormat("tick_imb=%.2f<%.2f",imb,TickImbalanceMin);
            if(InpDebug) PrintFormat("[EA] no trade: tick imbalance=%.2f < min=%.2f",imb,TickImbalanceMin);
            return false;
         }
      }
   }

   if(UseSessionFilter)
   {
      datetime tn=now+(datetime)(Sess_GMT_Offset_Min*60);
      MqlDateTime dt; TimeToStruct(tn,dt);
      if(dt.hour<Sess_Start_H || dt.hour>=Sess_End_H){
         reason="session";
         if(InpDebug) PrintFormat("[EA] no trade: outside session hour=%d (allowed %d-%d)",dt.hour,Sess_Start_H,Sess_End_H);
         return false; }
   }

   if(VolSpikeNow()){
      reason="atr_spike";
      if(InpDebug) Print("[EA] no trade: ATR spike detected");
      return false; }

   if(MinTradeGapSec>0 && (now-last_trade_time)<MinTradeGapSec){
      reason="gap";
      if(InpDebug) PrintFormat("[EA] no trade: MinTradeGap — elapsed=%ds required=%ds",
                                (int)(now-last_trade_time),MinTradeGapSec);
      return false; }

   int openOnSymbol=0;
   double sumLots=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(!pos.SelectByIndex(i)) continue;
      if(pos.Symbol()==G_Symbol && (int)pos.Magic()==InpMagic){ openOnSymbol++; sumLots+=pos.Volume(); }
   }

   int maxOpen=(LC_Has && LC_MaxOpen>0 ? LC_MaxOpen : MaxOpenPerSymbol);
   if(openOnSymbol>=maxOpen){
      reason="max_positions";
      if(InpDebug) PrintFormat("[EA] no trade: open positions=%d >= max=%d",openOnSymbol,maxOpen);
      return false; }

   int maxDay=(LC_Has && LC_MaxPerDay>0 ? LC_MaxPerDay : MaxTradesPerDay);
   if(TodayTradesCount()>=maxDay){
      reason="max_per_day";
      if(InpDebug) PrintFormat("[EA] no trade: today trades=%d >= max=%d",TodayTradesCount(),maxDay);
      return false; }

   if(UseDailyLossStop)
   {
      double bal=AccountInfoDouble(ACCOUNT_BALANCE);
      double limit=-DailyLossPct/100.0*bal;
      double pnl=TodayProfitUSD();
      if(pnl<=limit){
         reason="daily_loss_soft";
         if(InpDebug) PrintFormat("[EA] no trade: daily loss pnl=%.2f <= limit=%.2f",pnl,limit);
         return false; }
   }

   if(UseRiskGovernor)
   {
      double bal=AccountInfoDouble(ACCOUNT_BALANCE);
      double hard=-RG_DailyLossHardPct/100.0*bal;
      double pnl=TodayProfitUSD();
      if(pnl<=hard){
         reason="daily_loss_hard";
         if(InpDebug) PrintFormat("[EA] no trade: hard daily loss pnl=%.2f <= hard=%.2f",pnl,hard);
         return false; }

      double eq=AccountInfoDouble(ACCOUNT_EQUITY);
      if(G_EquityPeak<=0.0) G_EquityPeak=eq;
      if(eq>G_EquityPeak) G_EquityPeak=eq;

      double dd=(G_EquityPeak-eq)/MathMax(1e-8,G_EquityPeak)*100.0;
      if(dd>=RG_EquityDDHaltPct){
         reason=StringFormat("equity_dd=%.2f",dd);
         if(InpDebug) PrintFormat("[EA] no trade: equity DD=%.2f%% >= halt=%.2f%%",dd,RG_EquityDDHaltPct);
         return false; }

      if(sumLots>RG_MaxExposureLots){
         reason="exposure";
         if(InpDebug) PrintFormat("[EA] no trade: exposure lots=%.2f > max=%.2f",sumLots,RG_MaxExposureLots);
         return false; }
   }

   reason="OK";
   return true;
}

//====================================================
//==================== LADDER TP =====================
bool DoLadderTP_ByTicket(ulong pticket,OpenMeta &m)
{
   if(!UseLadderTP) return false;
   if(!PosSelectByTicketSafe(pticket)) return false;

   string sym=(string)PositionGetString(POSITION_SYMBOL);

   MqlTick tk; if(!SymbolInfoTick(sym,tk)) return false;
   double cur=(m.dir>0 ? tk.bid : tk.ask);

   double gain_pts=(m.dir>0 ? (cur-m.entry_price)/G_Point : (m.entry_price-cur)/G_Point);
   if(m.sl_pts<=0.0) return false;

   double Rnow=gain_pts/m.sl_pts;

   double posVol=PositionGetDouble(POSITION_VOLUME);
   m.lots=posVol;

   if(!m.l1_done && Rnow>=L1_R && m.lots>G_VolMin)
   {
      double close_lots=NormalizeVolume(m.lots*L1_Frac);
      close_lots=MathMin(close_lots,m.lots-G_VolMin);

      if(close_lots>=G_VolMin && close_lots<m.lots)
      {
         if(ClosePartialByTicket(pticket,close_lots))
         {
            m.l1_done=true;
            m.lots=PosVolumeByTicket(pticket);
            Meta_Update(m);
            return true;
         }
      }
      else m.l1_done=true;
   }

   if(!m.l2_done && Rnow>=L2_R && m.lots>G_VolMin)
   {
      double close_lots=NormalizeVolume(m.lots*L2_Frac);
      close_lots=MathMin(close_lots,m.lots-G_VolMin);

      if(close_lots>=G_VolMin && close_lots<m.lots)
      {
         if(ClosePartialByTicket(pticket,close_lots))
         {
            m.l2_done=true;
            m.lots=PosVolumeByTicket(pticket);
            Meta_Update(m);
            return true;
         }
      }
      else m.l2_done=true;
   }

   return false;
}

//====================================================
//==================== AI AGGREGATOR =================
bool ReadAISignal_All(int &dir_out,double &conf_out,string &reason_out,int &hold_min,double &rr_out,double &risk_pct_out)
{
   dir_out=0; conf_out=0.0; reason_out=""; hold_min=0; rr_out=0.0; risk_pct_out=0.0;

   if(!UseAISignals){ reason_out="ai_off"; return false; }

   datetime now=TimeCurrent();

   bool shadow=AI_ShadowMode;
   if(LC_Has) shadow=(shadow || LC_Shadow);
   if(ForceShadowOff) shadow=false;
   if(shadow){ reason_out="shadow"; return false; }

   // ===== BACKTEST AI REPLAY (Professional Layer) =====
   if(IsTester() && BT_EnableAIReplay)
   {
      if(!g_replay_loaded)
         LoadReplayCSV();

      if(g_replay_loaded)
      {
         datetime bar_time = iTime(G_Symbol, _Period, 0);

         int    rdir  = 0;
         double rconf = 0.0;

         if(ReplayFetch(bar_time, rdir, rconf))
         {
            dir_out      = rdir;
            conf_out     = rconf;
            hold_min     = AI_MaxHoldMinutes;
            rr_out       = (LC_Has && LC_RR>0 ? LC_RR : InpRR);
            risk_pct_out = (LC_Has && LC_RiskPct>0 ? LC_RiskPct : InpRiskPct);

            reason_out = "bt_replay";
            return true;
         }
      }
   }

   // ===== HOLD last AI (live/local/cloud) =====
   if(ai_dir_hint!=0 && ai_hold_until>0 && now<ai_hold_until)
   {
      dir_out=ai_dir_hint;
      conf_out=ai_conf_hint;
      reason_out=(ai_reason_hint==""?"ai_hold":ai_reason_hint);
      hold_min=(int)MathMax(0,(ai_hold_until-now)/60);
      rr_out=(LC_Has && LC_RR>0 ? LC_RR : InpRR);
      risk_pct_out=(LC_Has && LC_RiskPct>0 ? LC_RiskPct : InpRiskPct);
      return true;
   }

   // ===== Cooldown =====
   if(AI_CooldownMinutes>0 && last_ai_exec>0)
   {
      int cd=AI_CooldownMinutes*60;
      if((int)(now-last_ai_exec)<cd){ reason_out="ai_cooldown"; return false; }
   }

   // ===== Cloud =====
   if(Cloud_Allowed() && Cloud_FetchAI && (g_license_ok || !Cloud_BlockTradingIfInvalidLicense))
   {
      int d=0; double c=0; string rsn=""; int hold=0; double rr=0; double risk=0;
      if(Cloud_TryFetchAI(d,c,rsn,hold,rr,risk))
      {
         dir_out=d; conf_out=c; reason_out=(rsn==""?"cloud":rsn);
         hold_min=(hold>0?hold:AI_MaxHoldMinutes);
         rr_out=(rr>0?rr:(LC_Has && LC_RR>0?LC_RR:InpRR));
         risk_pct_out=(risk>0?risk:(LC_Has && LC_RiskPct>0?LC_RiskPct:InpRiskPct));
         last_ai_read=now; last_ai_exec=now;
         return true;
      }
   }

   // ===== Local file =====
   int dl=0; double cl=0; string rl=""; int holdl=0; double rrl=0; double riskl=0;
   bool ok=ReadAISignal_Local(dl,cl,rl,holdl,rrl,riskl);
   if(ok)
   {
      dir_out=dl; conf_out=cl; reason_out=(rl==""?"local":rl);
      hold_min=(holdl>0?holdl:AI_MaxHoldMinutes);
      rr_out=(rrl>0?rrl:(LC_Has && LC_RR>0?LC_RR:InpRR));
      risk_pct_out=(riskl>0?riskl:(LC_Has && LC_RiskPct>0?LC_RiskPct:InpRiskPct));
      last_ai_read=now; last_ai_exec=now;
      return true;
   }

   reason_out="no_ai";
   return false;
}

//====================================================
//==================== AI CONF FILTER =================
bool ApplyAIConfidenceFilter(
   int ai_dir,
   double ai_conf,
   int tech_dir,
   double &risk_out,
   double &rr_out,
   string &reason_out)
{
   reason_out="";

   if(ai_dir==0)
   {
      reason_out="ai_none";
      return false;
   }

   if(AI_BlockLowConfidence && ai_conf < AI_Conf_Low)
   {
      reason_out="ai_conf_too_low";
      return false;
   }

   if(AI_RequireTechAgreement && ai_conf < AI_Conf_Medium)
   {
      if(tech_dir!=0 && tech_dir!=ai_dir)
      {
         reason_out="ai_low_conf_no_tech_agreement";
         return false;
      }
   }

   if(AI_ScaleRiskByConfidence)
   {
      if(ai_conf >= AI_Conf_VeryHigh)
         risk_out *= AI_RiskScale_VeryHigh;
      else if(ai_conf >= AI_Conf_High)
         risk_out *= AI_RiskScale_High;
      else if(ai_conf >= AI_Conf_Medium)
         risk_out *= AI_RiskScale_Medium;
      else
         risk_out *= AI_RiskScale_Low;
   }

   if(AI_ScaleRRByConfidence)
   {
      if(ai_conf >= AI_Conf_VeryHigh)
         rr_out *= AI_RRScale_VeryHigh;
      else if(ai_conf >= AI_Conf_High)
         rr_out *= AI_RRScale_High;
      else if(ai_conf >= AI_Conf_Medium)
         rr_out *= AI_RRScale_Medium;
      else
         rr_out *= AI_RRScale_Low;
   }

   reason_out="ai_conf_ok";
   return true;
}

//====================================================
//=========== v8 CLEAN SPLIT: SIGNAL vs EXEC =========
struct SignalPack
{
   bool   ok;
   int    dir;
   double rr;
   double risk;
   double ai_conf;
   string why;

   // context for logs
   double ema_fast;
   double ema_slow;
   double rsi_now;
   double atr_pts;

   string fib_reason;
   bool   fib_ok;
};

void Signal_Reset(SignalPack &s)
{
   s.ok=false; s.dir=0; s.rr=0; s.risk=0; s.ai_conf=0; s.why="none";
   s.ema_fast=0; s.ema_slow=0; s.rsi_now=0; s.atr_pts=0;
   s.fib_reason=""; s.fib_ok=true;
}

// Builds a final trading signal decision (includes Fib + AI conf filter).
// ===================== FIXED v8: BuildSignal (respects TradeMode + Hybrid fallback) =====================
// استبدل دالتك الحالية بهذه بالكامل.
bool BuildSignal(SignalPack &s)
{
   Signal_Reset(s);

   if(!ReadTechNow(s.ema_fast,s.ema_slow,s.rsi_now,s.atr_pts))
   {
      s.why="tech_not_ready";
      if(InpDebug) Print("[EA] no trade: indicators not ready (handle invalid or no data)");
      return false;
   }

   string tech_reason="";
   int tech_dir=TechDirection(s.ema_fast,s.ema_slow,s.rsi_now,tech_reason);

   // Defaults من الإعدادات الفعّالة (Regime/LiveConfig)
   double rr   = E_RR;
   double risk = E_RiskPct;

   int    final_dir     = 0;
   double final_ai_conf = 0.0;
   string src_why       = "";

   //========================
   // 1) TECH ONLY
   //========================
   if(TradeMode==TM_TECH_ONLY)
   {
      if(tech_dir==0)
      {
         s.why="tech_dir0";
         if(InpDebug) PrintFormat("[EA] no trade: MA=%.5f/%.5f RSI=%.1f — %s",
                                   s.ema_fast,s.ema_slow,s.rsi_now,
                                   tech_reason=="" ? "no_direction" : tech_reason);
         return false;
      }

      final_dir     = tech_dir;
      final_ai_conf = 0.0;
      rr            = E_RR;
      risk          = E_RiskPct;
      src_why       = "tech_only";
   }
   else
   {
      //========================
      // 2) AI / HYBRID
      //========================
      int ai_dir=0;
      double ai_conf=0.0;
      string ai_reason="";
      int ai_hold=0;
      double ai_rr=0.0;
      double ai_risk=0.0;

      bool hasAI=ReadAISignal_All(ai_dir,ai_conf,ai_reason,ai_hold,ai_rr,ai_risk);

      // --- لا يوجد AI ---
      if(!hasAI)
      {
         if(TradeMode==TM_HYBRID && HYB_TechFallbackWhenNoAI && tech_dir!=0)
         {
         final_dir     = tech_dir;
         final_ai_conf = 0.0;
         rr            = E_RR;
         risk          = E_RiskPct;
         src_why       = "hybrid_fallback_tech_no_ai";
         }
            else
         {
             s.why="no_ai";
             if(InpDebug) Print("[EA] no trade: no AI signal available");
             return false;
         }
      }

      else
      {
         // HYBRID: شرط الاتفاق إذا مفعل
         if(TradeMode==TM_HYBRID && HYB_RequireAgreementWhenAI)
         {
            if(tech_dir!=0 && tech_dir!=ai_dir)
            {
               s.why="hyb_ai_disagree_tech";
               if(InpDebug) PrintFormat("[EA] no trade: AI dir=%d disagrees with tech dir=%d",ai_dir,tech_dir);
               return false;
            }
         }

         // قيم RR/RISK من AI إذا موجودة، وإلا من E_*
         rr   = (ai_rr>0.0 ? ai_rr   : E_RR);
         risk = (ai_risk>0.0 ? ai_risk : E_RiskPct);

         // فلتر الثقة (مهم: يقدر يغير risk/rr بالـ reference)
         string filter_reason="";
        if(!ApplyAIConfidenceFilter(ai_dir,ai_conf,tech_dir,risk,rr,filter_reason))
        {
           if(TradeMode==TM_HYBRID && HYB_TechFallbackWhenAIFiltered && tech_dir!=0)
          {
             final_dir     = tech_dir;
            final_ai_conf = 0.0;
            rr            = E_RR;
            risk          = E_RiskPct;
            src_why       = "hybrid_fallback_tech_ai_filtered";
          }
            else
          {
             s.why=filter_reason;
             if(InpDebug) PrintFormat("[EA] no trade: AI conf filtered — %s conf=%.2f",filter_reason,ai_conf);
             return false;
          }
       }

         else
         {
            final_dir     = ai_dir;
            final_ai_conf = ai_conf;
            src_why       = (ai_reason=="" ? "ai" : ai_reason);
         }
      }
   }

   //========================
   // 3) Fibonacci filter (على الاتجاه النهائي)
   //========================
   string fib_reason="";
   bool fib_ok = Fib_FilterAllows(final_dir, s.atr_pts, s.ema_fast, s.ema_slow, fib_reason);

   s.fib_ok      = fib_ok;
   s.fib_reason  = fib_reason;

   if(!fib_ok)
   {
      s.why=fib_reason;
      if(InpDebug) PrintFormat("[EA] no trade: Fib filter — %s",fib_reason);
      return false;
   }

   //========================
   // 4) Final pack
   //========================
   s.ok      = true;
   s.dir     = final_dir;
   s.rr      = rr;
   s.risk    = risk;
   s.ai_conf = final_ai_conf;
   s.why     = src_why;

   return true;
}

//====================================================
//==================== EXECUTION =====================
// v8: OpenTrade is "execution only": no Fib re-check.
// It uses SanitizeSLTP before send and before post-fill modify.
ulong GetLastInDealTicketForEA(int lookback_sec=10)
{
   datetime now=TimeCurrent();
   datetime from=now-(datetime)lookback_sec;
   if(!HistorySelect(from,now)) return 0;

   ulong best=0;
   long  best_tmsc=-1;

   int total=(int)HistoryDealsTotal();
   for(int i=0;i<total;i++)
   {
      ulong dt=HistoryDealGetTicket(i);
      if(dt==0) continue;

      string sym=(string)HistoryDealGetString(dt,DEAL_SYMBOL);
      long   mg =(long)HistoryDealGetInteger(dt,DEAL_MAGIC);
      long   entry=(long)HistoryDealGetInteger(dt,DEAL_ENTRY);
      long   type =(long)HistoryDealGetInteger(dt,DEAL_TYPE);
      long   tmsc =(long)HistoryDealGetInteger(dt,DEAL_TIME_MSC);

      if(sym!=G_Symbol) continue;
      if(mg!=(long)InpMagic) continue;
      if(entry!=DEAL_ENTRY_IN) continue;
      if(type!=DEAL_TYPE_BUY && type!=DEAL_TYPE_SELL) continue;

      if(tmsc>best_tmsc){ best_tmsc=tmsc; best=dt; }
   }
   return best;
}

ulong GetPositionIdFromDeal(ulong deal_ticket)
{
   if(deal_ticket==0) return 0;
   return (ulong)HistoryDealGetInteger(deal_ticket,DEAL_POSITION_ID);
}

bool OpenTrade_Exec(const SignalPack &s)
{
   if(!s.ok || s.dir==0) return false;

   if(TradeOnClosedBar && g_traded_this_bar) return false;
   if(g_trade_lock) return false;

   string reason;

   if(!RM_AllowsNewTrade(reason))
   {
      if(InpDebug) PrintFormat("[EA] no trade: RM blocked — %s",reason);
      LogDecisionCSV("open_block",reason,s.dir,s.ema_fast,s.ema_slow,s.rsi_now,s.atr_pts,0,g_avg_slip_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,s.why);
      return false;
   }

   if(IsWithinNewsWindow_Calendar())
   {
      if(InpDebug) Print("[EA] no trade: news window (calendar filter)");
      LogDecisionCSV("open_block","calendar",s.dir,s.ema_fast,s.ema_slow,s.rsi_now,s.atr_pts,0,g_avg_slip_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,s.why);
      return false;
   }

   // Use latest ATR points (execution safety)
   double atr_pts=0.0;
   if(!GetATR_Points_PerBar(atr_pts)) atr_pts=s.atr_pts;

   MqlTick tk;
   if(!SymbolInfoTick(G_Symbol,tk)) return false;

   double spread_pts=(tk.ask-tk.bid)/G_Point;
   double px_open=(s.dir>0 ? tk.ask : tk.bid);

   // SL/TP points
   // Broker constraints (points)
  double stopsPts  = GetStopsLevelPts(G_Symbol);
  double freezePts = GetFreezeLevelPts(G_Symbol);

  // Conservative min distance: max(stops, freeze) + buffer
  double minDistPts = MathMax(stopsPts, freezePts);
  minDistPts = MathMax(minDistPts, 2.0);
  minDistPts = minDistPts + 4.0; // extra safety

  // Min SL in points (use minDistPts as a floor)
  double min_sl_pts = MathMax(50.0, minDistPts);

  // Final SL/TP points
  double sl_pts = MathMax(min_sl_pts, atr_pts * G_ATR_SL_MULT);
  double tp_pts = sl_pts * s.rr;


   double lots=LotsByRisk_TC(s.risk,sl_pts,spread_pts);
   DBG_TRADE("OPEN_CALC",s.dir,lots,sl_pts,tp_pts);

   if(lots<G_VolMin)
   {
      LogDecisionCSV("open_block","lots_too_small",s.dir,s.ema_fast,s.ema_slow,s.rsi_now,atr_pts,spread_pts,g_avg_slip_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,s.why);
      return false;
   }

   // Raw SL/TP prices
   double stop=(s.dir>0 ? (px_open-sl_pts*G_Point) : (px_open+sl_pts*G_Point));
   double take=(s.dir>0 ? (px_open+tp_pts*G_Point) : (px_open-tp_pts*G_Point));

   // v8 Sanity Layer before sending
   string sanity_reason="";
   if(!SanitizeSLTP(G_Symbol,s.dir,px_open,stop,take,sanity_reason))
   {
      LogDecisionCSV("open_block","sanity_fail_send_"+sanity_reason,s.dir,s.ema_fast,s.ema_slow,s.rsi_now,atr_pts,spread_pts,g_avg_slip_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,s.why);
      return false;
   }

   trade.SetExpertMagicNumber(InpMagic);

   bool sent=false;
   const int max_attempts=3;
   g_trade_lock=true;

   for(int attempt=0;attempt<max_attempts;attempt++)
   {
      if(s.dir>0) sent=trade.Buy(lots,G_Symbol,0.0,stop,take);
      else       sent=trade.Sell(lots,G_Symbol,0.0,stop,take);

      if(sent) break;

      if(InpDebug)
         PrintFormat("[OPEN_FAIL] attempt=%d ret=%d desc=%s lastErr=%d",
                     attempt+1,trade.ResultRetcode(),trade.ResultRetcodeDescription(),GetLastError());
      ResetLastError();
      Sleep(200);
   }

   g_trade_lock=false;

   if(!sent)
   {
      LogDecisionCSV("open_try_fail","send_final",s.dir,s.ema_fast,s.ema_slow,s.rsi_now,atr_pts,spread_pts,g_avg_slip_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,s.why);
      return false;
   }

   DBG("ORDER SENT OK");

   double fill=trade.ResultPrice();
   double slippage_pts=MathMax(0.0,MathAbs(fill-px_open)/G_Point);
   Slip_Update(slippage_pts);

   // Post-fill SL/TP (re-anchor around fill; points remain the same)
   double stop2=(s.dir>0 ? (fill-sl_pts*G_Point) : (fill+sl_pts*G_Point));
   double take2=(s.dir>0 ? (fill+tp_pts*G_Point) : (fill-tp_pts*G_Point));

   // v8 Sanity layer for modify
   string sanity_reason2="";
   if(!SanitizeSLTP(G_Symbol,s.dir,fill,stop2,take2,sanity_reason2))
   {
      if(InpDebugVerbose)
         PrintFormat("[SANITY][POST] failed reason=%s (keeping initial SL/TP)", sanity_reason2);
   }

   // --- Get deal & pos_id ---
   ulong deal_in=trade.ResultDeal();
   if(deal_in==0) deal_in=GetLastInDealTicketForEA(10);

   ulong pid=GetPositionIdFromDeal(deal_in);
   ulong pticket = FindPositionTicketByPosId(pid);

   bool mok=false;

   if(pid>0)
   {
      for(int k=0;k<20 && pticket==0;k++)
      {
         pticket = FindPositionTicketByPosId(pid);
         if(pticket>0) break;
         Sleep(50);
      }
   }

   if(pticket>0)
   {
      // Modify with sanitized post-fill stops
      mok = ModifyPositionByTicketSafe(pticket, stop2, take2);
   }
   else
   {
      if(InpDebugVerbose)
         PrintFormat("[MOD_OPEN] skip (pticket not found) pid=%I64u deal_in=%I64u", pid, deal_in);
   }

   if(InpDebugVerbose)
      PrintFormat("[MOD_OPEN] mok=%s ret=%d %s",
                  mok?"true":"false", trade.ResultRetcode(), trade.ResultRetcodeDescription());

   if(pid>0)
   {
      OpenMeta m; ZeroMemory(m);
      m.pos_id=pid;
      m.dir=s.dir;
      m.lots=lots;
      m.sl_pts=sl_pts;
      m.rr_eff=s.rr;
      m.ai_conf=s.ai_conf;
      m.news_level=NewsLevelString();
      m.open_time=TimeCurrent();
      m.shadow_sl=0.0;
      m.mfe_pts=0.0;
      m.mae_pts=0.0;
      m.bars_open=0;
      m.adds_done=0;
      m.entry_price=fill;
      m.spread_on_open=spread_pts;
      m.slippage_pts=slippage_pts;
      m.risk_pct=s.risk;
      m.last_bar_time=iTime(G_Symbol,_Period,0);
      m.l1_done=false;
      m.l2_done=false;
      m.tp_total_pts=tp_pts;
      m.deal_in_ticket=deal_in;
      m.close_logged=false;

      Meta_Add(m);

      WriteJSONL_Open(TimeCurrent(),
                      (JSONL_SymbolTag!=""?JSONL_SymbolTag:Sym()),
                      s.ai_conf,G_ATR_SL_MULT,
                      s.rr,s.risk,
                      E_TS_Start,E_TS_Step,
                      E_BE_Trig,E_BE_Offs,
                      G_MaxSpreadPts,MaxTradesPerDay,
                      m.news_level,
                      spread_pts,slippage_pts,
                      pid);

      last_trade_time=TimeCurrent();
      g_traded_this_bar=true;

      LogDecisionCSV("open_sent","ok",s.dir,s.ema_fast,s.ema_slow,s.rsi_now,atr_pts,spread_pts,slippage_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,s.why);

      if(LogOpenRows)
         LogDealCSV2((s.dir>0?"BUY":"SELL"),lots,fill,0.0,sl_pts,tp_pts,s.rr,s.risk,0.0,0.0,0.0,0.0,slippage_pts,spread_pts,"open",TimeCurrent(),0,pid,deal_in,0);

      return true;
   }

   return false;
}

//====================================================
//==================== MANAGEMENT ====================
void UpdateMFE_MAE(OpenMeta &m)
{
   MqlTick tk;
   if(!SymbolInfoTick(G_Symbol,tk)) return;

   double px=(m.dir>0 ? tk.bid : tk.ask);
   double gain_pts=(m.dir>0 ? (px-m.entry_price)/G_Point : (m.entry_price-px)/G_Point);

   if(gain_pts>m.mfe_pts) m.mfe_pts=gain_pts;
   if(gain_pts<-m.mae_pts) m.mae_pts=-gain_pts;
}

void ApplyTrailingAndBE_ByTicket(ulong pticket, OpenMeta &m)
{
   if(pticket == 0) return;
   if(!PositionSelectByTicket(pticket)) return;

   string sym  = PositionGetString(POSITION_SYMBOL);
   long   type = PositionGetInteger(POSITION_TYPE);

   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl   = PositionGetDouble(POSITION_SL);
   double tp   = PositionGetDouble(POSITION_TP);

   double bid=0.0, ask=0.0;
   SymbolInfoDouble(sym, SYMBOL_BID, bid);
   SymbolInfoDouble(sym, SYMBOL_ASK, ask);

   double price = (type == POSITION_TYPE_BUY ? bid : ask);

   double gain_pts = (type == POSITION_TYPE_BUY ? (price - open) : (open - price)) / G_Point;

   // ===== BreakEven =====
   if(UseBreakEven && gain_pts >= E_BE_Trig)
   {
      double be_price = (type == POSITION_TYPE_BUY
                         ? open + E_BE_Offs * G_Point
                         : open - E_BE_Offs * G_Point);

      bool improve = (type == POSITION_TYPE_BUY
                      ? (sl < be_price)
                      : (sl > be_price || sl == 0.0));

      if(improve)
         ModifyPositionByTicketSafe(pticket, be_price, tp);
   }

   // ===== Trailing Stop =====
   if(UseTrailingStop && gain_pts >= E_TS_Start)
   {
      double tgt = (type == POSITION_TYPE_BUY
                    ? price - E_TS_Step * G_Point
                    : price + E_TS_Step * G_Point);

      bool improve = (type == POSITION_TYPE_BUY
                      ? (tgt > sl)
                      : (tgt < sl || sl == 0.0));

      if(improve)
         ModifyPositionByTicketSafe(pticket, tgt, tp);
   }
}

bool HasOpenOrPendingForMagic(int magic)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i);
      if(PositionSelectByTicket(ticket))
      {
         if((int)PositionGetInteger(POSITION_MAGIC)==magic) return true;
      }
   }
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong ticket=OrderGetTicket(i);
      if(OrderSelect(ticket))
      {
         if((int)OrderGetInteger(ORDER_MAGIC)==magic) return true;
      }
   }
   return false;
}

void ResetSessionState()
{
   g_trade_lock=false;
   g_traded_this_bar=false;
   Print("[Watchdog] Session state reset (no open positions/orders).");
}

void MaybeApplyShadowSL_ByTicket(ulong pticket, OpenMeta &m)
{
   if(!UseShadowSL) return;
   if(pticket==0) return;
   if(!PositionSelectByTicket(pticket)) return;

   string sym = PositionGetString(POSITION_SYMBOL);
   long type  = PositionGetInteger(POSITION_TYPE);

   double open = PositionGetDouble(POSITION_PRICE_OPEN);
   double tp   = PositionGetDouble(POSITION_TP);
   double sl   = PositionGetDouble(POSITION_SL);

   double atr_pts=0;
   if(!GetATR_Points_PerBar(atr_pts)) return;

  double shadow_gap = atr_pts * ShadowSL_Gap_ATR * G_Point;


   double newSL = (type==POSITION_TYPE_BUY) ? open - shadow_gap : open + shadow_gap;

   bool improve = (type==POSITION_TYPE_BUY ? (sl<newSL):(sl>newSL || sl==0));
   if(!improve) return;

   ModifyPositionByTicketSafe(pticket,newSL,tp);
}

void MaybePyramidAdd(OpenMeta &m)
{
   if(!UsePyramiding) return;
   if(m.adds_done >= Pyramid_MaxAdds) return;
   if(m.sl_pts <= 0) return;

   double bid=0.0, ask=0.0;
   if(!SymbolInfoDouble(G_Symbol, SYMBOL_BID, bid)) return;
   if(!SymbolInfoDouble(G_Symbol, SYMBOL_ASK, ask)) return;

   double cur = (m.dir > 0 ? bid : ask);

   double gain_pts = (m.dir > 0 ? (cur - m.entry_price) / G_Point
                                : (m.entry_price - cur) / G_Point);

   double Rnow = gain_pts / m.sl_pts;

   if(Rnow >= Pyramid_AddAtR)
   {
      string reason;
      if(!RM_AllowsNewTrade(reason)) return;
      if(IsWithinNewsWindow_Calendar()) return;

      MqlTick tk;
      if(!SymbolInfoTick(G_Symbol, tk)) return;

      double spread_pts = (tk.ask - tk.bid) / G_Point;

      double atr_pts = 0.0;
      if(!GetATR_Points_PerBar(atr_pts)) return;

      double sl_pts = MathMax(50.0, atr_pts * G_ATR_SL_MULT);

      double risk_use = MathMax(RG_RiskMinPct,
                                MathMin(RG_RiskMaxPct,
                                        E_RiskPct * Pyramid_RiskMult));

      double lots = LotsByRisk_TC(risk_use, sl_pts, spread_pts);
      lots = MathMin(lots, G_VolMax);

      double stop = (m.dir > 0 ? (cur - sl_pts * G_Point) : (cur + sl_pts * G_Point));
      double tp_pts = sl_pts * E_RR;
      double take = (m.dir > 0 ? (cur + tp_pts * G_Point) : (cur - tp_pts * G_Point));

      // v8 sanity before pyramiding send
      string srsn="";
      if(!SanitizeSLTP(G_Symbol,m.dir,cur,stop,take,srsn)) return;

      trade.SetExpertMagicNumber(InpMagic);

      bool ok = (m.dir > 0 ? trade.Buy(lots, G_Symbol, 0.0, stop, take) : trade.Sell(lots, G_Symbol, 0.0, stop, take));

      if(ok)
      {
         m.adds_done++;
         Meta_Update(m);
      }
   }
}

void ManageOpenPositions()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong pticket=PositionGetTicket(i);
      if(pticket==0) continue;
      if(!PosSelectByTicketSafe(pticket)) continue;

      string sym=(string)PositionGetString(POSITION_SYMBOL);
      long magic=PositionGetInteger(POSITION_MAGIC);
      if(sym!=G_Symbol || (int)magic!=InpMagic) continue;

      ulong pid_now=(ulong)PositionGetInteger(POSITION_IDENTIFIER);

      OpenMeta m;
      if(!Meta_Find(pid_now,m))
      {
         ZeroMemory(m);
         m.pos_id=pid_now;
         m.dir=(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?+1:-1);
         m.lots=PositionGetDouble(POSITION_VOLUME);
         m.entry_price=PositionGetDouble(POSITION_PRICE_OPEN);
         m.open_time=(datetime)PositionGetInteger(POSITION_TIME);

         double sl_raw=PositionGetDouble(POSITION_SL);
         double atr_pts_now=0.0;
         if(!GetATR_Points_PerBar(atr_pts_now)) atr_pts_now=MathMax(100.0,(double)E_TS_Step);

         double mult=(G_ATR_SL_MULT>0.0?G_ATR_SL_MULT:InpATR_SL_Mult);
         double sl_pts_eff=(sl_raw<=0.0 ? MathMax(50.0,atr_pts_now*mult) : MathMax(1.0,MathAbs(m.entry_price-sl_raw)/G_Point));

         m.sl_pts=sl_pts_eff;
         m.rr_eff=(E_RR>0.0?E_RR:InpRR);
         m.risk_pct=(E_RiskPct>0.0?E_RiskPct:InpRiskPct);
         m.news_level=NewsLevelString();

         MqlTick tk; SymbolInfoTick(sym,tk);
         m.spread_on_open=(tk.ask-tk.bid)/G_Point;

         m.mfe_pts=0.0; m.mae_pts=0.0; m.bars_open=0; m.adds_done=0;
         m.slippage_pts=0.0;
         m.last_bar_time=iTime(sym,_Period,0);
         m.l1_done=false; m.l2_done=false;
         m.tp_total_pts=sl_pts_eff*m.rr_eff;
         m.deal_in_ticket=0;
         m.close_logged=false;

         Meta_Add(m);
      }

      datetime cur_bar=iTime(sym,_Period,0);
      if(m.last_bar_time!=cur_bar){ m.bars_open++; m.last_bar_time=cur_bar; }

      UpdateMFE_MAE(m);

      if(UseTimeStop)
      {
         if(m.bars_open>=TimeStop_Bars && m.mfe_pts<E_BE_Trig)
         {
            ClosePositionByTicket(pticket);
            Meta_Update(m);
            continue;
         }
      }

      DoLadderTP_ByTicket(pticket,m);
      ApplyTrailingAndBE_ByTicket(pticket,m);
      MaybeApplyShadowSL_ByTicket(pticket,m);

      if(UseMFE_MAE_Trailing && m.mfe_pts>=MFE_Trigger_Mult*E_BE_Trig)
      {
         MqlTick tk; if(SymbolInfoTick(sym,tk))
         {
            double curp=(m.dir>0?tk.bid:tk.ask);
            double extra=MathMax(10.0,0.25*(m.mfe_pts-MFE_Trigger_Mult*E_BE_Trig));
            double new_sl=(m.dir>0 ? (curp-extra*G_Point) : (curp+extra*G_Point));
            double pos_tp=PositionGetDouble(POSITION_TP);
            ModifyPositionByTicketSafe(pticket,new_sl,pos_tp);
         }
      }

      if(UseEmergencyExit)
      {
         datetime now=TimeCurrent();
         bool pre=false,post=false;
         datetime evt; string cur; int imp;
         EE_GetWindows(now,pre,post,evt,cur,imp);

         if(pre||post)
         {
            if(EE_ProtectInsteadOfClose)
            {
               double pos_tp=PositionGetDouble(POSITION_TP);
               double be_offs=MathMax((double)EE_BE_OffsetPts,(double)E_BE_Offs);
               double px_be=(m.dir>0 ? m.entry_price+be_offs*G_Point
                                     : m.entry_price-be_offs*G_Point);

               ModifyPositionByTicketSafe(pticket,px_be,pos_tp);

               MqlTick tk; if(SymbolInfoTick(sym,tk))
               {
                  double curp=(m.dir>0?tk.bid:tk.ask);
                  double ts_start=MathMax((double)EE_TightTS_Start,(double)E_TS_Start/2.0);
                  double ts_step=MathMax((double)EE_TightTS_Step,(double)E_TS_Step/2.0);
                  double gain_pts=(m.dir>0 ? (curp-m.entry_price)/G_Point : (m.entry_price-curp)/G_Point);

                  if(gain_pts>=ts_start)
                  {
                     if(m.dir>0)
                     {
                        double tgt=curp-ts_step*G_Point;
                        if(PositionGetDouble(POSITION_SL)<tgt) ModifyPositionByTicketSafe(pticket,tgt,pos_tp);
                     }
                     else
                     {
                        double tgt=curp+ts_step*G_Point;
                        if(PositionGetDouble(POSITION_SL)>tgt) ModifyPositionByTicketSafe(pticket,tgt,pos_tp);
                     }
                  }
               }

               if(EE_PartialInsteadOfClose && m.lots>G_VolMin)
               {
                  double close_lots=NormalizeVolume(m.lots*EE_PartialFrac);
                  if(close_lots>=G_VolMin && close_lots<m.lots) ClosePartialByTicket(pticket,close_lots);
               }
            }
            else
            {
               ClosePositionByTicket(pticket);
               Meta_Update(m);
               continue;
            }
         }
      }

      Meta_Update(m);
   }
}

//====================================================
//=========== ROBUST CLOSE JOURNALING (v8) ===========
double _SafeDiv(double a,double b){ return (MathAbs(b)<1e-9 ? 0.0 : a/b); }

bool _HistorySelectWindow()
{
   datetime now=TimeCurrent();
   datetime from=now-(datetime)(CloseScanLookbackMin*60);
   return HistorySelect(from,now);
}

bool _DealMatchesEA(ulong deal_ticket)
{
   string sym=(string)HistoryDealGetString(deal_ticket,DEAL_SYMBOL);
   long magic=HistoryDealGetInteger(deal_ticket,DEAL_MAGIC);
   if(sym!=G_Symbol) return false;
   if(magic!=InpMagic) return false;
   return true;
}

bool PositionStillOpenByPosId(ulong pos_id)
{
   ulong pt = FindPositionTicketByPosId(pos_id);
   return (pt>0);
}

void ScanAndLogClosedDeals()
{
   // Deals CSV logging is required for research/verification.
   // In Strategy Tester, trade events can be inconsistent.
   // Allow history-scan logging whenever the general logger is enabled.
   if(!EnableCloseJournaling && !EnableLogger) return;

   datetime now=TimeCurrent();
   if(CloseScanEverySec>0 && g_last_close_scan>0 && (now-g_last_close_scan)<CloseScanEverySec) return;
   g_last_close_scan=now;

   if(!_HistorySelectWindow()) return;

   int total=(int)HistoryDealsTotal();
   if(total<=0) return;

   for(int i=0;i<total;i++)
   {
      ulong dticket=HistoryDealGetTicket(i);
      if(dticket==0) continue;

      long tmsc=(long)HistoryDealGetInteger(dticket,DEAL_TIME_MSC);
      if(tmsc<g_last_deal_time_msc_seen) continue;
      if(tmsc==g_last_deal_time_msc_seen && dticket<=g_last_deal_ticket_seen) continue;

      if(!_DealMatchesEA(dticket)) continue;

      long entry=HistoryDealGetInteger(dticket,DEAL_ENTRY);
      long type =HistoryDealGetInteger(dticket,DEAL_TYPE);

      if(entry==DEAL_ENTRY_IN && (type==DEAL_TYPE_BUY || type==DEAL_TYPE_SELL))
      {
         ulong pos_id=(ulong)HistoryDealGetInteger(dticket,DEAL_POSITION_ID);
         int idx=Meta_FindIndex(pos_id);
         if(idx>=0 && g_meta[idx].deal_in_ticket==0)
         {
            g_meta[idx].deal_in_ticket=dticket;
            Meta_Update(g_meta[idx]);
         }
      }

      if(entry==DEAL_ENTRY_OUT && (type==DEAL_TYPE_BUY || type==DEAL_TYPE_SELL))
      {
         ulong pos_id=(ulong)HistoryDealGetInteger(dticket,DEAL_POSITION_ID);
         int idx=Meta_FindIndex(pos_id);

         double pnl=HistoryDealGetDouble(dticket,DEAL_PROFIT)
                   +HistoryDealGetDouble(dticket,DEAL_SWAP)
                   +HistoryDealGetDouble(dticket,DEAL_COMMISSION);

         double exit_price=HistoryDealGetDouble(dticket,DEAL_PRICE);
         double vol=HistoryDealGetDouble(dticket,DEAL_VOLUME);

         bool is_final_close = !PositionStillOpenByPosId(pos_id);

         if(idx>=0)
         {
            OpenMeta m=g_meta[idx];
            string side=(m.dir>0?"BUY":"SELL");

            if(is_final_close)
            {
               if(!m.close_logged)
               {
                  double entry_price=m.entry_price;
                  double sl_pts=m.sl_pts;

                  double R=0.0;
                  if(sl_pts>0.0)
                  {
                     double gain_pts = (m.dir>0 ? (exit_price-entry_price)/G_Point
                                               : (entry_price-exit_price)/G_Point);
                     R = gain_pts / sl_pts;
                  }

                  LogDealCSV2(side,m.lots,entry_price,exit_price,m.sl_pts,m.tp_total_pts,m.rr_eff,m.risk_pct,
                              pnl,R,m.mfe_pts,m.mae_pts,m.slippage_pts,m.spread_on_open,
                              "close",m.open_time,(datetime)HistoryDealGetInteger(dticket,DEAL_TIME),m.pos_id,m.deal_in_ticket,dticket);

                  WriteJSONL_Close(TimeCurrent(),
                                  (JSONL_SymbolTag!=""?JSONL_SymbolTag:Sym()),
                                  R,m.ai_conf,G_ATR_SL_MULT,m.rr_eff,m.news_level,
                                  m.mfe_pts,m.mae_pts,m.slippage_pts,m.spread_on_open,
                                  m.pos_id,m.deal_in_ticket,dticket,pnl);

                  m.close_logged=true;
                  Meta_Update(m);
               }
            }
            else
            {
               LogDealCSV2(side,vol,m.entry_price,exit_price,m.sl_pts,m.tp_total_pts,m.rr_eff,m.risk_pct,
                           pnl,0.0,m.mfe_pts,m.mae_pts,m.slippage_pts,m.spread_on_open,
                           "partial",m.open_time,(datetime)HistoryDealGetInteger(dticket,DEAL_TIME),m.pos_id,m.deal_in_ticket,dticket);
            }
         }
         else
         {
            string side=(type==DEAL_TYPE_BUY?"BUY":"SELL");

            if(is_final_close)
            {
               LogDealCSV2(side,vol,0.0,exit_price,0.0,0.0,0.0,0.0,pnl,0.0,0.0,0.0,0.0,0.0,"close_nometa",0,(datetime)HistoryDealGetInteger(dticket,DEAL_TIME),pos_id,0,dticket);

               WriteJSONL_Close(TimeCurrent(),
                               (JSONL_SymbolTag!=""?JSONL_SymbolTag:Sym()),
                               0.0,0.0,G_ATR_SL_MULT,0.0,NewsLevelString(),
                               0.0,0.0,0.0,0.0,
                               pos_id,0,dticket,pnl);
            }
            else
            {
               LogDealCSV2(side,vol,0.0,exit_price,0.0,0.0,0.0,0.0,pnl,0.0,0.0,0.0,0.0,0.0,"partial_nometa",0,(datetime)HistoryDealGetInteger(dticket,DEAL_TIME),pos_id,0,dticket);
            }
         }
      }

      long tmsc2=(long)HistoryDealGetInteger(dticket,DEAL_TIME_MSC);
      if(tmsc2>g_last_deal_time_msc_seen || (tmsc2==g_last_deal_time_msc_seen && dticket>g_last_deal_ticket_seen))
      {
         g_last_deal_time_msc_seen=tmsc2;
         g_last_deal_ticket_seen=dticket;
      }
   }

   for(int k=ArraySize(g_meta)-1;k>=0;k--)
   {
      if(g_meta[k].close_logged)
      {
         bool stillOpen=false;
         for(int j=PositionsTotal()-1;j>=0;j--)
         {
            if(!pos.SelectByIndex(j)) continue;
            if(pos.Symbol()!=G_Symbol || (int)pos.Magic()!=InpMagic) continue;
            ulong pid=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
            if(pid==g_meta[k].pos_id){ stillOpen=true; break; }
         }
         if(!stillOpen) Meta_Remove(g_meta[k].pos_id);
      }
   }
}

//====================================================
//==================== ENGINE STEP ===================
void EngineStep(bool on_closed_bar)
{
   datetime now=TimeCurrent();

   if(!HasOpenOrPendingForMagic((int)InpMagic))
   {
      if(now-g_last_reset_time>5*60){ ResetSessionState(); g_last_reset_time=now; }
   }

   if(Cloud_Allowed())
   {
      if(now-g_last_lic>=15*60)
      {
         bool ok=Cloud_ValidateLicense();
         g_last_lic=now;
         if(ok)
            g_license_ok=true;
         else
         {
            g_license_ok=false;
            if(Cloud_BlockTradingIfInvalidLicense)
            {
               // v9: immediate block — do not continue EngineStep
               if(InpDebug) Print("[V9] License invalid — engine halted");
               return;
            }
         }
      }

      // v9: guard — don't continue if license is required but invalid
      if(Cloud_BlockTradingIfInvalidLicense && !g_license_ok) return;

      if(now-g_last_cfg>=Cloud_ReloadMin*60)
      {
         if(Cloud_FetchLiveConfig()) g_last_cfg=now;
      }

      if(now-g_last_hb>=Cloud_HeartbeatMin*60)
      {
         Cloud_Heartbeat();
         g_last_hb=now;
      }
   }

   LC_TryReload(false);
   Tick_Update();

   ENUM_REGIME new_regime=DetectRegime();
   if(new_regime!=G_Regime)
   {
      if(Regime_ConfirmBars>=RegimePersistBars){ G_Regime=new_regime; Regime_ConfirmBars=0; }
      else Regime_ConfirmBars++;
   }
   else Regime_ConfirmBars=0;

   SelectEffectiveParams();

   if(TradeOnClosedBar)
   {
      datetime bt=iTime(G_Symbol,_Period,0);
      if(last_bar_time==bt && !on_closed_bar)
      {
         ManageOpenPositions();
         ScanAndLogClosedDeals();
         return;
      }
      if(bt!=last_bar_time)
      {
         last_bar_time=bt;
         g_traded_this_bar=false;
      }
   }

   ManageOpenPositions();
   ScanAndLogClosedDeals();

   if(InLossCooldown()) return;

   SignalPack sig;
   bool hasSignal=BuildSignal(sig);

   if(!hasSignal || !sig.ok || sig.dir==0)
   {
      if(InpDebug) PrintFormat("[EA] no trade: %s | MA=%.5f/%.5f RSI=%.1f ATR=%.1f",
                                sig.why,sig.ema_fast,sig.ema_slow,sig.rsi_now,sig.atr_pts);
      LogDecisionCSV("no_signal",sig.why,sig.dir,
               sig.ema_fast,sig.ema_slow,sig.rsi_now,sig.atr_pts,
               0,g_avg_slip_pts,UseMA,UseRSI,(ai_dir_hint!=0),ai_dir_hint,ai_conf_hint,sig.why);

      return;
   }

   OpenTrade_Exec(sig);
}

//============================== END PART 3/4 =============================

//============================== PART 4 / 4 ==============================

//====================================================
//==================== DEBUG PANEL ===================
input bool ShowDebugPanel = false;
long   g_dbg_chart=0;
int    g_dbg_subwin=0;
string DBG_OBJ_NAME="EA_V8_DEBUG_PANEL";

string RegimeToString(ENUM_REGIME r)
{
   if(r==REG_TREND) return "TREND";
   if(r==REG_RANGE) return "RANGE";
   if(r==REG_HIGHVOL) return "HIGHVOL";
   return "NEUTRAL";
}

string ModeToString(ENUM_TRADE_MODE m)
{
   if(m==TM_AI_ONLY) return "AI_ONLY";
   if(m==TM_HYBRID) return "HYBRID";
   return "TECH_ONLY";
}

void DebugPanel_Init()
{
   if(!ShowDebugPanel) return;

   g_dbg_chart=ChartID();
   g_dbg_subwin=0;

   if(ObjectFind(g_dbg_chart,DBG_OBJ_NAME)==-1)
   {
      ObjectCreate(g_dbg_chart,DBG_OBJ_NAME,OBJ_LABEL,g_dbg_subwin,0,0);
      ObjectSetInteger(g_dbg_chart,DBG_OBJ_NAME,OBJPROP_CORNER,CORNER_LEFT_UPPER);
      ObjectSetInteger(g_dbg_chart,DBG_OBJ_NAME,OBJPROP_XDISTANCE,8);
      ObjectSetInteger(g_dbg_chart,DBG_OBJ_NAME,OBJPROP_YDISTANCE,18);
      ObjectSetInteger(g_dbg_chart,DBG_OBJ_NAME,OBJPROP_FONTSIZE,9);
      ObjectSetString(g_dbg_chart,DBG_OBJ_NAME,OBJPROP_FONT,"Consolas");
   }
}

void DebugPanel_Deinit()
{
   if(g_dbg_chart!=0 && ObjectFind(g_dbg_chart,DBG_OBJ_NAME)!=-1)
      ObjectDelete(g_dbg_chart,DBG_OBJ_NAME);
}

void DebugPanel_Draw()
{
   if(!ShowDebugPanel) return;
   if(g_dbg_chart==0) g_dbg_chart=ChartID();
   if(ObjectFind(g_dbg_chart,DBG_OBJ_NAME)==-1) DebugPanel_Init();

   double bal=AccountInfoDouble(ACCOUNT_BALANCE);
   double eq=AccountInfoDouble(ACCOUNT_EQUITY);

   datetime now=TimeCurrent();
   double imb=TI_Imbalance(now);
   int dom=TI_DominantDir(now);


   // B2: Regime diagnostics
   double adx_now=-1.0;
   if(RD_UseADX && adx_handle!=INVALID_HANDLE)
   {
      double ab[1];
      if(CopyBuffer(adx_handle,0,0,1,ab)==1) adx_now=ab[0];
   }
   double bb_width_pts=-1.0;
   if(RD_UseBollinger && bb_handle!=INVALID_HANDLE)
   {
      double up[1], dn[1];
      if(CopyBuffer(bb_handle,0,0,1,up)==1 && CopyBuffer(bb_handle,2,0,1,dn)==1)
         bb_width_pts=(up[0]-dn[0])/G_Point;
   }

   string txt="";
   txt+="EA_Strategy_V8_Pro 8.00\n";
   txt+="Sym: "+Sym()+"   TF: "+IntegerToString(_Period)+"\n";
   txt+="Mode: "+ModeToString(TradeMode)+"   Regime: "+RegimeToString(G_Regime)+"\n";
   txt+="RegimeDiag: ADX="+(adx_now>=0?DoubleToString(adx_now,1):"NA")+
        "  BBwPts="+(bb_width_pts>=0?DoubleToString(bb_width_pts,1):"NA")+"\n";
   txt+="Magic: "+IntegerToString((int)InpMagic)+"\n";
   txt+="Bal/Eq: "+DoubleToString(bal,2)+" / "+DoubleToString(eq,2)+"  PeakEq: "+DoubleToString(G_EquityPeak,2)+"\n";
   txt+="Eff RR: "+DoubleToString(E_RR,2)+"  Risk%: "+DoubleToString(E_RiskPct,2)+"  ATRmult: "+DoubleToString(G_ATR_SL_MULT,2)+"\n";
   txt+="MaxSpr: "+IntegerToString(G_MaxSpreadPts)+"  LiveCfg: "+(UseLiveConfig?"ON":"OFF")+
        "  Cloud: "+(Cloud_Enable?"ON":"OFF")+"  LicOK:"+((g_license_ok)?"Y":"N")+"\n";
   txt+="AI: "+(UseAISignals?"ON":"OFF")+"  minConf: "+DoubleToString(EffectiveBaseMinConf()+RegimeExtraConf(),2)+
        "  last_dir: "+IntegerToString(ai_dir_hint)+"  last_conf: "+DoubleToString(ai_conf_hint,2)+"\n";
   txt+="NewsFilter: "+(UseCalendarEffective()?"ON":"OFF")+"  MinImpact: "+IntegerToString(Cal_MinImpact)+"\n";
   txt+="Micro: "+(UseMicrostructureFilters?"ON":"OFF")+"  SpreadWinSec: "+IntegerToString(SpreadPercWindowSec)+
        "  Imb: "+DoubleToString(imb,2)+" dom:"+IntegerToString(dom)+"\n";
   txt+="Fib: "+(UseFibonacciFilter?"ON":"OFF")+"  SwingTF: "+IntegerToString((int)Fib_EffectiveTF())+"\n";
   txt+="TimerEngine: "+(UseTimerEngine?"ON":"OFF")+"  BE: "+(UseBreakEven?"ON":"OFF")+
        "  TS: "+(UseTrailingStop?"ON":"OFF")+"  LadderTP: "+(UseLadderTP?"ON":"OFF")+"\n";
   txt+="CloseScan: "+(EnableCloseJournaling?"ON":"OFF")+" lastDeal: "+IntegerToString((int)g_last_deal_ticket_seen)+"\n";

   ObjectSetString(g_dbg_chart,DBG_OBJ_NAME,OBJPROP_TEXT,txt);
}

//====================================================
//==================== AI BACKTEST REPLAY (PRO) ======

// اتجاه من نص/رقم
int _ReplayDirFromStr(const string raw)
{
   string s=raw;
   TrimStr(s);
   if(s=="") return 0;

   bool allDigits=true;
   int start=0;
   if(StringLen(s)>0 && (StringGetCharacter(s,0)=='-' || StringGetCharacter(s,0)=='+')) start=1;
   for(int i=start;i<StringLen(s);i++)
   {
      ushort ch=(ushort)StringGetCharacter(s,i);
      if(!(ch>='0' && ch<='9')){ allDigits=false; break; }
   }
   if(allDigits)
   {
      int v=(int)StringToInteger(s);
      if(v>0) return +1;
      if(v<0) return -1;
      return 0;
   }

   return _SigToDir(s);
}

int _SplitReplayLine(const string line, string &out[])
{
   ArrayResize(out,0);

   string s=line;
   TrimStr(s);
   if(s=="") return 0;
   if(StringLen(s)>=1 && (StringGetCharacter(s,0)=='#')) return 0;
   if(StringLen(s)>=2 && StringSubstr(s,0,2)=="//") return 0;

   int hasSemi = (StringFind(s,";")>=0);
   int hasComma= (StringFind(s,",")>=0);
   ushort sep = (hasSemi && !hasComma ? ';' : ',');

   int n=StringSplit(s,sep,out);

   if(n<=1)
   {
      sep = (sep==','?';':',');
      n=StringSplit(s,sep,out);
   }

   for(int i=0;i<n;i++) TrimStr(out[i]);
   return n;
}

bool _IsReplayHeader(const string &a,const string &b,const string &c)
{
   string A=LowerCopy(a), B=LowerCopy(b), C=LowerCopy(c);
   if(StringFind(A,"time")>=0 || StringFind(A,"ts")>=0 || StringFind(A,"date")>=0) return true;
   if(StringFind(B,"dir")>=0  || StringFind(B,"side")>=0 || StringFind(B,"signal")>=0) return true;
   if(StringFind(C,"conf")>=0 || StringFind(C,"prob")>=0 || StringFind(C,"score")>=0) return true;
   return false;
}

int  g_replay_ptr=0;
int  g_replay_last_dir=0;
double g_replay_last_conf=0.0;
bool g_replay_has_last=false;
bool LoadReplayCSV()
{
   g_replay_loaded=false;
   g_replay_count=0;
   ArrayResize(g_replay,0);

   g_replay_ptr=0;
   g_replay_last_dir=0;
   g_replay_last_conf=0.0;
   g_replay_has_last=false;

   // ---- Diagnostics: where is MT5 data/common folders?
   PrintFormat("[REPLAY] TERMINAL_DATA_PATH        = %s", TerminalInfoString(TERMINAL_DATA_PATH));
   PrintFormat("[REPLAY] TERMINAL_COMMONDATA_PATH  = %s", TerminalInfoString(TERMINAL_COMMONDATA_PATH));
   Print("[REPLAY] NOTE: COMMON files are in: ", TerminalInfoString(TERMINAL_COMMONDATA_PATH), "\\Files\\");
   Print("[REPLAY] NOTE: LOCAL  files are in: ", TerminalInfoString(TERMINAL_DATA_PATH),       "\\MQL5\\Files\\");

   if(!IsTester())
   {
      Print("[REPLAY] Not in tester -> skip");
      return false;
   }
   if(!BT_EnableAIReplay)
   {
      Print("[REPLAY] BT_EnableAIReplay=false -> skip");
      return false;
   }

   string file = BT_AISignalsCSV;
   TrimStr(file);

   if(file=="")
   {
      Print("[REPLAY] BT_AISignalsCSV empty");
      return false;
   }

   // ---- Diagnostics: show requested file name
   PrintFormat("[REPLAY] BT_AISignalsCSV='%s'", file);

   // Check existence in COMMON/LOCAL explicitly
   bool exCommon = FileIsExist(file, FILE_COMMON);
   bool exLocal  = FileIsExist(file);
   PrintFormat("[REPLAY] FileIsExist COMMON=%s | LOCAL=%s",
               (exCommon?"YES":"NO"), (exLocal?"YES":"NO"));

   int h=INVALID_HANDLE;
   bool from_common=true;

   // Try COMMON first
   h=FileOpen(file, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(h==INVALID_HANDLE)
   {
      // Try LOCAL
      from_common=false;
      h=FileOpen(file, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   }

   if(h==INVALID_HANDLE)
   {
      int err=GetLastError();
      PrintFormat("[REPLAY] FileOpen FAILED for '%s' (common/local). GetLastError=%d", file, err);
      return false;
   }

   int loaded=0;

   while(!FileIsEnding(h))
   {
      string ln=FileReadString(h);

      string parts[];
      int n=_SplitReplayLine(ln,parts);
      if(n<=0) continue;

      if(n>=3 && _IsReplayHeader(parts[0],parts[1],parts[2]))
         continue;

      string ts="", sym="", dirS="", confS="";

      if(n>=4)
      {
         int d_test=_ReplayDirFromStr(parts[2]);
         if(d_test!=0 || UpperCopy(parts[2])=="FLAT")
         {
            ts   = parts[0];
            sym  = parts[1];
            dirS = parts[2];
            confS= parts[3];
         }
         else
         {
            int d_test2=_ReplayDirFromStr(parts[3]);
            if(d_test2!=0 || UpperCopy(parts[3])=="FLAT")
            {
               ts   = parts[0];
               sym  = parts[1];
               confS= parts[2];
               dirS = parts[3];
            }
            else
            {
               ts   = parts[0];
               dirS = parts[1];
               confS= parts[2];
            }
         }
      }
      else if(n>=3)
      {
         ts   = parts[0];
         dirS = parts[1];
         confS= parts[2];
      }
      else
         continue;

      datetime t=ParseFlexibleTime(ts);
      if(t<=0) continue;

      if(BT_AI_TimeShiftSec!=0)
         t = t + (datetime)BT_AI_TimeShiftSec;

      int d=_ReplayDirFromStr(dirS);
      if(d==0) continue;

      double c=StringToDouble(confS);
      if(c<0.0) c=0.0;
      if(c>1.0) c=1.0;

      if(sym!="")
      {
         if(!SymbolsEquivalent(sym, Sym()) && !SymbolsEquivalent(sym, G_Symbol))
            continue;
      }

      AIReplayRow row;
      row.time=t;
      row.dir=d;
      row.confidence=c;

      int k=ArraySize(g_replay);
      ArrayResize(g_replay,k+1);
      g_replay[k]=row;
      loaded++;
   }

   FileClose(h);

   g_replay_count=ArraySize(g_replay);
   g_replay_loaded=(g_replay_count>0);

   PrintFormat("[REPLAY] Loaded rows=%d (kept=%d) from %s (%s)",
               g_replay_count, loaded, file, (from_common?"COMMON":"LOCAL"));

   return g_replay_loaded;
}


bool ReplayFetch(datetime bar_time, int &dir, double &conf)
{
   dir=0;
   conf=0.0;

   if(!g_replay_loaded) return false;
   if(g_replay_count<=0) return false;

   datetime t = bar_time;
   if(!BT_AI_AllowOnBarOpen)
      t = iTime(G_Symbol,_Period,1);

   if(t<=0) return false;

   while(g_replay_ptr < g_replay_count && g_replay[g_replay_ptr].time < t)
   {
      g_replay_last_dir  = g_replay[g_replay_ptr].dir;
      g_replay_last_conf = g_replay[g_replay_ptr].confidence;
      g_replay_has_last  = true;
      g_replay_ptr++;
   }

   if(g_replay_ptr < g_replay_count && g_replay[g_replay_ptr].time == t)
   {
      dir  = g_replay[g_replay_ptr].dir;
      conf = g_replay[g_replay_ptr].confidence;

      g_replay_last_dir  = dir;
      g_replay_last_conf = conf;
      g_replay_has_last  = true;

      if(g_replay_ptr < g_replay_count-1) g_replay_ptr++;
      return true;
   }

   if(BT_AI_HoldLast && g_replay_has_last)
   {
      datetime last_t=0;
      if(g_replay_ptr>0) last_t=g_replay[g_replay_ptr-1].time;

      int gap = (last_t>0 ? (int)(t - last_t) : 0);

      int maxGap = BT_AI_MaxHoldGapSec;
      if(maxGap<=0) maxGap = 3600;

      if(gap >= 0 && gap <= maxGap)
      {
         dir  = g_replay_last_dir;
         conf = g_replay_last_conf;
         return true;
      }

      return false;
   }

   return false;
}

//====================================================
//==================== INIT / TICK ===================
int OnInit()
{
   AutoCreateLogFolders();

   EnsureDirExists("ai_signals");
   EnsureDirExists("ai_replay");
   EnsureDirExists("logs");

   if(IsTester() && BT_EnableAIReplay)
      LoadReplayCSV();

   RB_Init();

   if(!FillSymbolInfo())
   {
      Print("Symbol info fail");
      return INIT_FAILED;
   }

   // v8: init fib with swing TF
   if(!Fib_Init())
   {
      if(InpDebug) Print("[FIB] init failed (continuing)");
   }


// --- Indicator handles (robust init) ---


ma_fast_handle=INVALID_HANDLE;


ma_slow_handle=INVALID_HANDLE;


rsi_handle    =INVALID_HANDLE;


atr_handle    =INVALID_HANDLE;


// optional regime handles


if(!RD_UseADX)       adx_handle=INVALID_HANDLE;


if(!RD_UseBollinger) bb_handle =INVALID_HANDLE;



// Force data load for suffix symbols (XAUUSDr, XAUUSDm, etc.) before creating handles
SymbolSelect(G_Symbol,true);
EnsureSeriesReady(G_Symbol,(ENUM_TIMEFRAMES)_Period,200);

ResetLastError();


if(UseMA)


{


   ma_fast_handle=iMA(G_Symbol,_Period,InpMAfast,0,InpMA_Method,InpMA_Price);


   if(ma_fast_handle==INVALID_HANDLE)


   {


      int e=GetLastError(); ResetLastError();


      PrintFormat("[INIT] iMA fast failed sym=%s tf=%d period=%d err=%d",G_Symbol,(int)_Period,InpMAfast,e);


      return INIT_FAILED;


   }


   ma_slow_handle=iMA(G_Symbol,_Period,InpMAslow,0,InpMA_Method,InpMA_Price);


   if(ma_slow_handle==INVALID_HANDLE)


   {


      int e=GetLastError(); ResetLastError();


      PrintFormat("[INIT] iMA slow failed sym=%s tf=%d period=%d err=%d",G_Symbol,(int)_Period,InpMAslow,e);


      return INIT_FAILED;


   }


}



ResetLastError();


if(UseRSI)


{


   rsi_handle=iRSI(G_Symbol,_Period,InpRSI_Period,PRICE_CLOSE);


   if(rsi_handle==INVALID_HANDLE)


   {


      int e=GetLastError(); ResetLastError();


      PrintFormat("[INIT] iRSI failed sym=%s tf=%d period=%d err=%d",G_Symbol,(int)_Period,InpRSI_Period,e);


      return INIT_FAILED;


   }


}



ResetLastError();


atr_handle=iATR(G_Symbol,_Period,InpATR_Period);


if(atr_handle==INVALID_HANDLE)


{


   int e=GetLastError(); ResetLastError();


   PrintFormat("[INIT] iATR failed sym=%s tf=%d period=%d err=%d",G_Symbol,(int)_Period,InpATR_Period,e);


   return INIT_FAILED;


}



ResetLastError();


if(RD_UseADX)


{


   adx_handle=iADX(G_Symbol,_Period,RD_ADX_Period);


   if(adx_handle==INVALID_HANDLE)


   {


      int e=GetLastError(); ResetLastError();


      PrintFormat("[INIT] iADX failed sym=%s tf=%d period=%d err=%d",G_Symbol,(int)_Period,RD_ADX_Period,e);


      return INIT_FAILED;


   }


}


if(RD_UseBollinger)


{


   bb_handle=iBands(G_Symbol,_Period,RD_BB_Period,0,RD_BB_Dev,PRICE_CLOSE);


   if(bb_handle==INVALID_HANDLE)


   {


      int e=GetLastError(); ResetLastError();


      PrintFormat("[INIT] iBands failed sym=%s tf=%d period=%d dev=%.2f err=%d",G_Symbol,(int)_Period,RD_BB_Period,RD_BB_Dev,e);


      return INIT_FAILED;


   }


}



// Wait for indicators to become readable (avoid BarsCalculated=0 on first ticks)


if(UseMA)


{


   if(!WaitIndicatorReady(ma_fast_handle,InpMAfast+5))
   {
      PrintFormat("[INIT] MA fast not ready sym=%s tf=%d period=%d — retrying",G_Symbol,(int)_Period,InpMAfast);
      return INIT_FAILED;
   }


   if(!WaitIndicatorReady(ma_slow_handle,InpMAslow+5))
   {
      PrintFormat("[INIT] MA slow not ready sym=%s tf=%d period=%d",G_Symbol,(int)_Period,InpMAslow);
      return INIT_FAILED;
   }


}


if(UseRSI && !WaitIndicatorReady(rsi_handle,InpRSI_Period+5))
{
   PrintFormat("[INIT] RSI not ready sym=%s tf=%d period=%d",G_Symbol,(int)_Period,InpRSI_Period);
   return INIT_FAILED;
}


if(!WaitIndicatorReady(atr_handle,InpATR_Period+5))
{
   PrintFormat("[INIT] ATR not ready sym=%s tf=%d period=%d",G_Symbol,(int)_Period,InpATR_Period);
   return INIT_FAILED;
}


   if(RD_UseADX && adx_handle!=INVALID_HANDLE && !WaitIndicatorReady(adx_handle,RD_ADX_Period+5))
   {
      PrintFormat("[INIT] ADX not ready sym=%s tf=%d period=%d",G_Symbol,(int)_Period,RD_ADX_Period);
      return INIT_FAILED;
   }
   if(RD_UseBollinger && bb_handle!=INVALID_HANDLE && !WaitIndicatorReady(bb_handle,RD_BB_Period+5))
   {
      PrintFormat("[INIT] Bollinger not ready sym=%s tf=%d period=%d",G_Symbol,(int)_Period,RD_BB_Period);
      return INIT_FAILED;
   }

   if(AutoConfig) DoAutoConfig();
   else
   {
      G_ATR_SL_MULT=InpATR_SL_Mult;
      G_RR=InpRR;
      G_MaxSpreadPts=InpMaxSpreadPts;
      G_SpreadSL_Factor=InpSpreadSLFactor;
      G_CommissionPerLot=InpCommissionPerLot;
   }

   LC_TryReload(true);

   if(Cloud_Allowed())
   {
      Print("[CLOUD] Add '",Cloud_BaseURL,"' to: Tools -> Options -> Expert Advisors -> Allow WebRequest");
      g_cloud_ready=true;
      g_license_ok=Cloud_ValidateLicense();
      g_last_lic=TimeCurrent();

      if(!g_license_ok && Cloud_BlockTradingIfInvalidLicense)
         Print("[CLOUD] Trading blocked until license is valid");

      if(Cloud_FetchLiveConfig()) g_last_cfg=TimeCurrent();
   }

   if(UseTimerEngine) EventSetTimer(TimerPeriodSec);

   last_bar_time=iTime(G_Symbol,_Period,0);
   G_EquityPeak=AccountInfoDouble(ACCOUNT_EQUITY);

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(MaxSlippagePts>0?MaxSlippagePts:200);

   DebugPanel_Init();

   // Initialize close-scan cursor (EA-only)
   g_last_deal_time_msc_seen = 0;
   g_last_deal_ticket_seen   = 0;

   if(_HistorySelectWindow())
   {
      int total=(int)HistoryDealsTotal();
      for(int i=0;i<total;i++)
      {
         ulong dt=HistoryDealGetTicket(i);
         if(dt==0) continue;
         if(!_DealMatchesEA(dt)) continue;

         long tmsc=(long)HistoryDealGetInteger(dt,DEAL_TIME_MSC);
         if(tmsc>g_last_deal_time_msc_seen || (tmsc==g_last_deal_time_msc_seen && dt>g_last_deal_ticket_seen))
         {
            g_last_deal_time_msc_seen=tmsc;
            g_last_deal_ticket_seen=dt;
         }
      }
   }

   if(InpDebug)
      Print("[EA 8.00] Init completed for symbol=",G_Symbol," magic=",InpMagic," fibTF=",IntegerToString((int)Fib_EffectiveTF()));

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(UseTimerEngine) EventKillTimer();

   if(atr_handle!=INVALID_HANDLE){ IndicatorRelease(atr_handle); atr_handle=INVALID_HANDLE; }
   if(atr_spike_handle!=INVALID_HANDLE){ IndicatorRelease(atr_spike_handle); atr_spike_handle=INVALID_HANDLE; }
   if(rsi_handle!=INVALID_HANDLE){ IndicatorRelease(rsi_handle); rsi_handle=INVALID_HANDLE; }
   if(ma_fast_handle!=INVALID_HANDLE){ IndicatorRelease(ma_fast_handle); ma_fast_handle=INVALID_HANDLE; }
   if(ma_slow_handle!=INVALID_HANDLE){ IndicatorRelease(ma_slow_handle); ma_slow_handle=INVALID_HANDLE; }

   Fib_Deinit();
   DebugPanel_Deinit();
}

void OnTick()
{
   if(UseTimerEngine){ DebugPanel_Draw(); return; }
   EngineStep(false);
   DebugPanel_Draw();
}

void OnTimer()
{
   if(!UseTimerEngine) return;

   static datetime last_bt=0;
   datetime bt=iTime(G_Symbol,_Period,0);
   bool new_bar=(bt!=last_bt);
   if(new_bar) last_bt=bt;

   EngineStep(new_bar);
   DebugPanel_Draw();
}

void OnTrade()
{
   // journaling handled by ScanAndLogClosedDeals()
}
double OnTester()
{
   double profit   = TesterStatistics(STAT_PROFIT);
   double sharpe   = TesterStatistics(STAT_SHARPE_RATIO);
   double dd       = TesterStatistics(STAT_EQUITY_DDREL_PERCENT);

   if(dd <= 0.0)
      dd = 0.01;

   // موازنة بين الربح والجودة وتقليل المخاطرة
   return ((profit * 0.6) + (sharpe * 100.0)) / dd;
}
//============================== END PART 4/4 =============================
