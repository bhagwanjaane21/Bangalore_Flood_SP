"""Read only the explicitly supported GeoJSON files; reject malformed geometry."""

import json
import math
from pathlib import Path

from fastapi import HTTPException


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON value: {value}")


def _position(position):
    if not isinstance(position, list) or len(position) not in (2, 3):
        raise ValueError("Invalid coordinate dimensions")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in position):
        raise ValueError("Coordinates must be finite numbers")
    if not -180 <= position[0] <= 180 or not -90 <= position[1] <= 90:
        raise ValueError("Coordinates outside longitude/latitude bounds")


def _length_m(coordinates):
    distance = 0.0
    for start, end in zip(coordinates, coordinates[1:]):
        lon1, lat1, lon2, lat2 = map(math.radians, (*start[:2], *end[:2]))
        haversine = (math.sin((lat2 - lat1) / 2) ** 2
                     + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
        distance += 6371000 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))
    return distance


def load_spatial(path: Path, geometry_type: str) -> dict:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise HTTPException(status_code=404, detail=f"{path.name}: spatial data not provided")
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise ValueError("Expected a FeatureCollection")
        features = data.get("features")
        if not isinstance(features, list):
            raise ValueError("Expected a feature list")
        if not features:
            raise HTTPException(status_code=404, detail=f"{path.name}: no mapped features")
        length_m = 0.0
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError("Invalid feature")
            if not isinstance(feature.get("properties"), dict):
                raise ValueError("Expected feature properties")
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or geometry.get("type") != geometry_type:
                raise ValueError("Unexpected geometry type")
            coordinates = geometry.get("coordinates")
            if geometry_type == "Point":
                _position(coordinates)
            else:
                if not isinstance(coordinates, list) or len(coordinates) < 2:
                    raise ValueError("A LineString needs at least two positions")
                for position in coordinates:
                    _position(position)
                length_m += _length_m(coordinates)
        data["metadata"] = {"verification_status": "unverified"}
        if geometry_type == "LineString":
            data["metadata"]["geometry_length_m"] = round(length_m, 1)
        return data
    except HTTPException:
        raise
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(status_code=503, detail=f"{path.name}: invalid or unreadable spatial data") from exc
