"""Écriture GRIB2 minimale en Python pur (grille lat/lon 3.0, produit 4.0, simple packing 5.0 + masque).
Utilisé par le robot des courants ; lisible par XyGrib et par grib2.py du programme."""
import datetime as dt
import math
import struct


def _s32(v: int) -> bytes:                      # entier signé GRIB2 (bit de signe)
    return ((1 << 31) | -v).to_bytes(4, "big") if v < 0 else v.to_bytes(4, "big")


def _sec(num: int, corps: bytes) -> bytes:
    return struct.pack(">IB", len(corps) + 5, num) + corps


def _bits(valeurs, nbits: int) -> bytes:
    if nbits == 0 or not valeurs:
        return b""
    total = len(valeurs) * nbits
    grand = 0
    for v in valeurs:
        grand = (grand << nbits) | v
    pad = (-total) % 8
    return (grand << pad).to_bytes((total + pad) // 8, "big")


def message(discipline: int, categorie: int, numero: int, date_ref: dt.datetime, minutes: int,
            lat1: float, lon1: float, dlat: float, dlon: float, ni: int, nj: int, valeurs: list,
            decimales: int = 2, centre: int = 85, niveau: int = 1) -> bytes:
    """valeurs : liste ni*nj, du sud au nord, d'ouest en est ; None = terre."""
    s1 = struct.pack(">HHBBBHBBBBBBB", centre, 0, 2, 1, 1, date_ref.year, date_ref.month, date_ref.day,
                     date_ref.hour, date_ref.minute, 0, 0, 1)
    lat2, lon2 = lat1 + (nj - 1) * dlat, lon1 + (ni - 1) * dlon
    s3 = (struct.pack(">BIBBH", 0, ni * nj, 0, 0, 0)                     # source, npts, listes, modèle 3.0
          + struct.pack(">BBIBIBI", 6, 0, 0, 0, 0, 0, 0)                           # Terre sphérique
          + struct.pack(">IIII", ni, nj, 0, 0xFFFFFFFF)
          + _s32(round(lat1 * 1e6)) + _s32(round(lon1 * 1e6)) + bytes([48])
          + _s32(round(lat2 * 1e6)) + _s32(round(lon2 * 1e6))
          + struct.pack(">II", round(dlon * 1e6), round(dlat * 1e6)) + bytes([0x40]))  # balayage sud -> nord
    s4 = (struct.pack(">HH", 0, 0) + bytes([categorie, numero, 2, 0, 0]) + struct.pack(">HB", 0, 0)
          + bytes([0]) + struct.pack(">i", minutes)                                 # unité : minute
          + bytes([niveau, 0]) + struct.pack(">I", 0) + bytes([255, 0]) + struct.pack(">I", 0))
    # niveau (table 4.5) : 1 = surface (défaut) ; 10 = atmosphère entière (nuages, comme GFS : XyGrib l'attend)
    presents = [v for v in valeurs if v is not None]
    f = 10 ** decimales
    entiers = [round(v * f) for v in presents]
    R = min(entiers) if entiers else 0
    plage = (max(entiers) - R) if entiers else 0
    nbits = max(1, math.ceil(math.log2(plage + 1))) if plage else 0
    s5 = struct.pack(">IH", len(presents), 0) + struct.pack(">f", float(R)) + struct.pack(">hh", 0, decimales) + bytes([nbits, 0])
    masque = _bits([0 if v is None else 1 for v in valeurs], 1)
    s6 = bytes([0]) + masque
    s7 = _bits([e - R for e in entiers], nbits)
    corps = _sec(1, s1) + _sec(3, s3) + _sec(4, s4) + _sec(5, s5) + _sec(6, s6) + _sec(7, s7) + b"7777"
    return b"GRIB" + struct.pack(">HBB", 0, discipline, 2) + struct.pack(">Q", 16 + len(corps)) + corps
