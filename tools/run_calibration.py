# -*- coding: utf-8 -*-
"""
واجهة تشغيل سريعة من سطر الأوامر.
أمثلة:
  python tools/run_calibration.py --csv data/XAUUSD_H1.csv --mode both --random-trials 150
  python tools/run_calibration.py --csv data/XAUUSD_H1.csv --mode grid
"""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.services.calibrator import main

if __name__=="__main__":
    main()






