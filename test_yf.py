import yfinance as yf

sym = "GC=F"
df = yf.download(sym, period="5d", interval="5m")
print(len(df)); print(df.tail())

print("عدد الصفوف:", len(df))
print(df.tail())  # آخر 5 شمعات