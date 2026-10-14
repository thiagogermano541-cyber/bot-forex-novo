from datetime import date

from .base import get_json


async def fred_observations(client, api_key):
    if not api_key:
        return []
    result = []
    for series in ("DFF", "DGS10", "DTWEXBGS"):
        data = await get_json(client, "https://api.stlouisfed.org/fred/series/observations",
                              params={"api_key": api_key, "series_id": series, "file_type": "json",
                                      "sort_order": "desc", "limit": 5, "realtime_start": date.today().isoformat(),
                                      "realtime_end": date.today().isoformat()})
        for row in data["observations"]:
            if row["value"] != ".":
                result.append(dict(id=f"fred:{series}:{row['date']}", series=series, observation_date=row["date"],
                                   value=float(row["value"]), provider="FRED", vintage=date.today().isoformat(),
                                   caveat="Observação diária, sujeita a atraso e revisão; não é calendário de eventos."))
                break
    return result
