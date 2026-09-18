#!/usr/bin/env python3
"""Potenza media del package CPU letta da RAPL, un campione al secondo."""
import sys
import time
from datetime import datetime

BASE = "/sys/class/powercap/intel-rapl/intel-rapl:0/"


def read(name):
    with open(BASE + name) as f:
        return int(f.read())


try:
    MAX = read("max_energy_range_uj")
except PermissionError:
    sys.exit("energy_uj è leggibile solo da root (CVE-2020-8694): rilancia con sudo")
except FileNotFoundError:
    sys.exit(f"RAPL non disponibile in {BASE} (VM o container?)")

v0 = read("energy_uj")
while True:
    time.sleep(1)  # ~1s fisso: J consumati in 1s = W
    v1 = read("energy_uj")
    # il contatore riparte da zero a MAX: il modulo rende il delta sempre positivo
    watt = (v1 - v0) % MAX / 1e6
    print(f"{datetime.now():%H:%M:%S} Potenza media: {watt:.1f} W")
    v0 = v1
