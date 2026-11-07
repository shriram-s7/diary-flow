"""Road-validation adapter. Call outside database connections/transactions.

Reuses the existing bounded OSRM nearest cache; never falls back to a straight line.
Validation time is not a claim about provider/cache retrieval time or road legality.
"""
import math
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field
from v2.domain.types import Value


class Location(Value):
    lat: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False, strict=True)]
    lon: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False, strict=True)]


class RoadError(ValueError):
    def __init__(self, code, message, status):
        self.code, self.status = code, status
        super().__init__(message)


def validate_location(location: Location, *, snapper=None, provider_url=None, clock=None):
    if snapper is None:
        import routing
        snapper = routing.snap_to_road
    if provider_url is None:
        import config
        provider_url = config.OSRM_BASE_URL
    clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        result = snapper(location.lat, location.lon, budget_sec=3.0)
        if result is None:
            raise RoadError('road_provider_unavailable',
                            'Road validation could not complete. Retry when the routing provider is available.', 503)
        values = [result[k] for k in ('road_lat', 'road_lon', 'snap_distance_m')]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError('Nonfinite provider value')
        lat, lon, distance = values
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and distance >= 0):
            raise ValueError('Invalid provider coordinate/distance')
        # Independently bound raw-to-road distance; a broken provider must not
        # claim zero distance while returning a far-away coordinate.
        a, b = math.radians(location.lat), math.radians(lat)
        dlat, dlon = b - a, math.radians(lon - location.lon)
        hav = math.sin(dlat / 2)**2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2)**2
        measured = 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, hav))))
        conservative_distance = max(distance, measured)
    except RoadError:
        raise
    except Exception as exc:
        raise RoadError('road_provider_invalid',
                        'Road validation failed or returned unusable coordinates. No location was approved.', 503) from exc
    if conservative_distance > 300:
        raise RoadError('road_too_far', 'Choose a location within 300 metres of a routable road.', 422)
    parsed = urlsplit(provider_url)
    # Strip credentials, query and fragment from provenance.
    host = parsed.hostname or 'unknown'
    origin = f'{parsed.scheme}://{host}' + (f':{parsed.port}' if parsed.port else '')
    return {
        'raw': location.model_dump(mode='json'),
        'snapped': {'lat': lat, 'lon': lon},
        'snap_distance_m': conservative_distance,
        'road_name': str(result.get('road_name') or '')[:500] or None,
        'provenance': {'provider': 'osrm-nearest', 'origin': origin,
            'validated_at': clock().isoformat(), 'cache_policy': 'existing bounded OSRM cache may be used',
            'provider_data_version': None, 'max_snap_distance_m': 300},
        'validated': True,
    }
