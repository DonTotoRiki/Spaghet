import os

import maxminddb

# Base au format MaxMind (.mmdb) : DB-IP City Lite (gratuite, sans compte) ou MaxMind GeoLite2-City.
DEFAULT_PATH = "geoip.mmdb"


def load(path: str | None = None):
    """Ouvre la base GeoIP, ou renvoie None si elle est absente : la géolocalisation est optionnelle.

    Lève ValueError si le fichier existe mais n'est pas lisible.
    """
    path = path or os.environ.get("SPAGHET_GEOIP") or DEFAULT_PATH
    if not os.path.exists(path):
        return None
    try:
        return maxminddb.open_database(path)
    except (OSError, maxminddb.InvalidDatabaseError) as e:
        raise ValueError(f"base {path} illisible (fichier .gz pas décompressé ?)") from e


def _name(record: dict, key: str) -> str | None:
    names = (record.get(key) or {}).get("names") or {}
    return names.get("fr") or names.get("en")


def locate(reader, ip: str) -> dict:
    """Champs de Node (lat, lon, city, country) pour une IP publique ; vide si inconnue."""
    record = reader.get(ip) if reader else None
    location = (record or {}).get("location") or {}
    if "latitude" not in location:
        return {}
    return {"lat": location["latitude"], "lon": location["longitude"],
            "city": _name(record, "city"), "country": _name(record, "country")}
