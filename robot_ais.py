"""
Robot AIS — A.C.I.S Marine / Météo Marine Tunisie.
Lancé toutes les 15 min par GitHub (.github/workflows/ais.yml) : écoute aisstream.io ~80 s sur la zone
Tunisie, fusionne avec la photo précédente et écrit ais_tunisie.json (navires vus depuis moins d'une heure).
Clé aisstream : secret GitHub AISSTREAM_KEY (jamais dans l'appli).
"""
import asyncio
import json
import os
import sys
import time

import websockets

ZONE = [[30.0, 7.0], [39.0, 14.5]]
DUREE_S = 80
GARDER_S = 3600
SORTIE = sys.argv[1] if len(sys.argv) > 1 else "ais_tunisie.json"
PRECEDENT = sys.argv[2] if len(sys.argv) > 2 else None


def charger_precedent():
    try:
        d = json.load(open(PRECEDENT, encoding="utf-8"))
        return {n["mmsi"]: n for n in d.get("navires", [])}
    except Exception:
        return {}


async def ecouter(navires: dict):
    cle = os.environ["AISSTREAM_KEY"]
    fin = time.time() + DUREE_S
    async with websockets.connect("wss://stream.aisstream.io/v0/stream", open_timeout=20) as ws:
        await ws.send(json.dumps({"APIKey": cle, "BoundingBoxes": [ZONE],
                                  "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport",
                                                         "ExtendedClassBPositionReport", "ShipStaticData",
                                                         "StaticDataReport"]}))
        while time.time() < fin:
            try:
                brut = await asyncio.wait_for(ws.recv(), timeout=max(1, fin - time.time()))
            except asyncio.TimeoutError:
                break
            m = json.loads(brut)
            if "error" in m:
                raise SystemExit("aisstream : " + str(m["error"]))
            meta, typ = m.get("MetaData", {}), m.get("MessageType")
            mmsi = meta.get("MMSI")
            if not mmsi:
                continue
            n = navires.setdefault(mmsi, {"mmsi": mmsi, "nom": "", "type": 0, "lat": None, "lon": None,
                                          "sog": 0.0, "cog": 0.0, "statut": 15, "t": 0})
            if meta.get("ShipName", "").strip():
                n["nom"] = meta["ShipName"].strip()
            corps = m.get("Message", {}).get(typ, {})
            if typ in ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"):
                la, lo = corps.get("Latitude"), corps.get("Longitude")
                if la is None or abs(la) > 90 or abs(lo) > 180:
                    continue
                n.update(lat=round(la, 5), lon=round(lo, 5), sog=round(min(corps.get("Sog", 0) or 0, 102.2), 1),
                         cog=round((corps.get("Cog", 0) or 0) % 360), t=int(time.time()))
                if "NavigationalStatus" in corps:
                    n["statut"] = corps["NavigationalStatus"]
                if typ == "ExtendedClassBPositionReport" and corps.get("Type"):
                    n["type"] = corps["Type"]
            elif typ == "ShipStaticData":
                n["type"] = corps.get("Type") or n["type"]
                n["indicatif"] = (corps.get("CallSign") or "").strip()
                n["dest"] = (corps.get("Destination") or "").strip()
                if corps.get("MaximumStaticDraught"):
                    n["tirant"] = corps["MaximumStaticDraught"]
                if (corps.get("Name") or "").strip():
                    n["nom"] = corps["Name"].strip()
            elif typ == "StaticDataReport":
                a, b = corps.get("ReportA", {}), corps.get("ReportB", {})
                if a.get("Valid") and a.get("Name", "").strip():
                    n["nom"] = a["Name"].strip()
                if b.get("Valid"):
                    n["type"] = b.get("ShipType") or n["type"]
                    n["indicatif"] = (b.get("CallSign") or "").strip()


def main():
    navires = charger_precedent()
    avant = len(navires)
    try:
        asyncio.run(ecouter(navires))
    except (OSError, websockets.exceptions.WebSocketException) as e:
        print("Connexion aisstream interrompue :", e)
    maintenant = time.time()
    garde = [n for n in navires.values() if n.get("lat") is not None and maintenant - n["t"] < GARDER_S]
    for n in garde:
        n["nom"] = n["nom"] or str(n["mmsi"])
    with open(SORTIE + ".part", "w", encoding="utf-8") as f:
        json.dump({"maj": int(maintenant), "source": "aisstream.io", "navires": garde}, f, ensure_ascii=False,
                  separators=(",", ":"))
    os.replace(SORTIE + ".part", SORTIE)
    print(f"{len(garde)} navires (avant : {avant})")


if __name__ == "__main__":
    main()
