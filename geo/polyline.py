"""Encoded polyline codec.

Valhalla and OSRM both return matched geometry as a Google-encoded polyline
rather than raw coordinate arrays, so decoding one is unavoidable. This is the
standard algorithm and pure stdlib - adding a dependency for ~30 lines is not
worth it when `requirements.txt` is deliberately lean.

The precision matters and is the classic source of "my line is in the Gulf of
Guinea" bugs: Google and OSRM's default `polyline` are 1e5, while Valhalla and
OSRM's `polyline6` are 1e6. Callers must pass what their provider actually sent.
"""


def decode_polyline(encoded, precision=6):
    """Decode an encoded polyline to `[[lng, lat], ...]`.

    Coordinates come back **lng first** to match GeoJSON and `RouteResult`,
    not the lat-first order the wire format stores them in.

    Never raises: a truncated or non-ASCII payload yields whatever full pairs
    were readable, because a partial trace still draws usefully and a matcher
    outage must not surface as a 500 on the dispatch map.
    """
    if not encoded:
        return []

    factor = float(10 ** precision)
    coordinates = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)

    while index < length:
        # Two varints per point, latitude then longitude, each zig-zag encoded
        # as a delta from the previous point.
        for axis in range(2):
            shift = 0
            result = 0
            while index < length:
                byte = ord(encoded[index]) - 63
                index += 1
                if byte < 0:
                    return coordinates
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            else:
                # Ran out of input mid-varint; drop the incomplete pair.
                return coordinates

            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lng += delta

        coordinates.append([lng / factor, lat / factor])

    return coordinates


def encode_polyline(coordinates, precision=6):
    """Encode `[[lng, lat], ...]` back to a polyline string.

    Only used to build test fixtures and to log a compact trace, but it lives
    next to the decoder so the two stay in step on precision.
    """
    factor = 10 ** precision
    parts = []
    previous_lat = 0
    previous_lng = 0

    for point in coordinates or []:
        lat = int(round(float(point[1]) * factor))
        lng = int(round(float(point[0]) * factor))
        for delta in (lat - previous_lat, lng - previous_lng):
            value = ~(delta << 1) if delta < 0 else (delta << 1)
            while value >= 0x20:
                parts.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            parts.append(chr(value + 63))
        previous_lat = lat
        previous_lng = lng

    return ''.join(parts)
