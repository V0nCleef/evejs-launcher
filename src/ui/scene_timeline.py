"""Bounded, deterministic orbital traffic and local station-light timing."""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import logging
from pathlib import Path
import random

SCENE_MANIFEST = Path(__file__).resolve().parents[2] / "assets/deep_signal/operations_scene.json"


@lru_cache(maxsize=1)
def scene_definition() -> dict:
    """Read the bundled authored anchors once, outside the paint loop."""
    try:
        with SCENE_MANIFEST.open(encoding="utf-8") as handle:
            definition = json.load(handle)
        if definition.get("schema") != 1 or len(definition["routes"]) != 7:
            raise ValueError("Unsupported station scene manifest")
        if definition["reference_size"] != [1664, 936] or len(definition["hull"]) < 3:
            raise ValueError("Unsupported station artwork geometry")
        for point in definition["hull"] + definition["beacons"]:
            if len(point) != 2 or not all(math.isfinite(value) for value in point):
                raise ValueError("Invalid scene anchor")
        for x, y, width, height in definition["lights"]:
            if not (0 <= x < x+width <= 1664 and 0 <= y < y+height <= 936):
                raise ValueError("Window bank outside station artwork")
        for route in definition["routes"]:
            if not 1 <= route["duration"] <= 180:
                raise ValueError("Invalid traffic duration")
            for key in ("start", "a", "b", "end"):
                if len(route[key]) != 2 or not all(math.isfinite(value) for value in route[key]):
                    raise ValueError("Invalid traffic path")
            if route["kind"] not in {"arrival", "departure", "transit", "freighter"} or route["tone"] not in {"cyan", "cool", "warm"}:
                raise ValueError("Invalid traffic style")
        return definition
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        logging.getLogger(__name__).warning("Station effects unavailable; using static artwork", exc_info=True)
        return {"reference_size": [1664, 936], "routes": [], "hull": [], "lights": [], "beacons": []}


def _cubic(start, a, b, end, t):
    inv = 1. - t
    point = tuple(inv**3 * start[i] + 3*inv*inv*t*a[i] +
                  3*inv*t*t*b[i] + t**3*end[i] for i in (0, 1))
    direction = tuple(3*(inv*inv*(a[i]-start[i]) +
                        2*inv*t*(b[i]-a[i]) + t*t*(end[i]-b[i])) for i in (0, 1))
    return *point, *direction


@dataclass(frozen=True, slots=True)
class TrafficSample:
    kind: str
    x: float
    y: float
    dx: float
    dy: float
    progress: float
    opacity: float
    warp_alpha: float
    scale: float
    tone: str


class SceneTimeline:
    """A small flight schedule with different timings on each three-minute pass."""

    CYCLE_SECONDS = 180.

    def __init__(self, seed: int = 31407):
        self.seed = seed
        self.definition = scene_definition()
        self._schedules: dict[int, tuple] = {}

    def _schedule(self, cycle: int) -> tuple:
        if cycle not in self._schedules:
            rng = random.Random(self.seed + cycle * 1907)
            routes = self.definition["routes"]
            if not routes:
                return ()
            flights = []
            for i in range(12):
                route = routes[i % 6]
                start = i * 15. + rng.uniform(-3., 3.) - 22.
                flights.append((start, route, rng.uniform(.82, 1.15)))
            if cycle % 2 == 0:
                flights.append((48. + rng.uniform(0., 15.), routes[6], 1.))
            self._schedules[cycle] = tuple(flights)
            for old in tuple(self._schedules):
                if abs(old - cycle) > 2:
                    del self._schedules[old]
        return self._schedules[cycle]

    def sample(self, elapsed_ms: int) -> tuple[TrafficSample, ...]:
        seconds = max(0., elapsed_ms / 1000.)
        cycle = int(seconds // self.CYCLE_SECONDS)
        samples = []
        for current in (cycle - 1, cycle, cycle + 1):
            for start, route, size in self._schedule(current):
                age = seconds - (current*self.CYCLE_SECONDS + start)
                duration = route["duration"]
                if age < 0. or age > duration:
                    continue
                progress = age / duration
                kind = route["kind"]
                travel = 1.-(1.-progress)**1.5 if kind == "arrival" else progress
                if kind == "departure":
                    travel = progress**1.25
                x, y, dx, dy = _cubic(route["start"], route["a"], route["b"], route["end"], travel)
                opacity = min(1., age/1.2) * min(1., (duration-age)/2.4)
                warp = max(0., 1.-age/1.25) if kind == "arrival" else 0.
                if kind == "departure":
                    warp = max(0., 1.-(duration-age)/1.1)
                samples.append(TrafficSample(kind, x, y, dx, dy, progress,
                                             opacity, warp, size, route["tone"]))
        return tuple(samples)

    @staticmethod
    def light_level(index: int, elapsed_ms: int) -> float:
        period = 43. + index*7.31
        phase = (elapsed_ms/1000. + index*13.73) % period
        if phase < 2.5:
            t = phase/2.5
            return 1.-.88*t*t*(3.-2.*t)
        if phase < 12.+index*.8:
            return .12
        if phase < 15.+index*.8:
            t = (phase-12.-index*.8)/3.
            return .12+.88*t*t*(3.-2.*t)
        return 1.

    @staticmethod
    def beacon_level(index: int, elapsed_ms: int) -> float:
        phase = elapsed_ms/1000. * (1.2+index*.13) + index*2.1
        return .15 + .85 * ((1.+math.sin(phase))*.5)**5
