#!/usr/bin/env python3
"""Forecast Roblox's Up-and-Coming top 10 from the chart history alone.

Reads /input/history.ndjson and /input/task.json. Writes one forecast for
every hour of the scored window. Standard library only, no network, and the
same bytes on every run.

A game's chance of a place is how often a game at its current rank held that
place 1 to 72 hours later, counted over every such pair in the history and
applied to the last chart. Each place keeps back its own rate of being taken
by a game that was not on the earlier chart, grown with how deep the place
is, and that mass is left unnamed so a game the history has never shown can
still collect it. The top 10 is the ten games with the most probability of
sitting somewhere in the top 10, in that order.
"""

import json
import os
from collections import defaultdict

INPUT_DIR = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
OUTPUT_DIR = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")

MAX_ROWS = 8
MIN_HOURS = 1.0
MAX_HOURS = 72.0
# Half of each place stays unnamed, shared by the chart games not named there.
UNNAMED = 0.5


def parse_time(value):
    date, time = value.replace("Z", "").split("T")
    year, month, day = (int(part) for part in date.split("-"))
    hour, minute, second = (int(part) for part in time.split(".")[0].split(":"))
    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return ((days * 24 + hour) * 60 + minute) * 60 + second


def read_task(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_history(path):
    snaps = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            chart = []
            for item in row.get("up_and_coming") or []:
                uid = item.get("universe_id")
                rank = item.get("rank")
                if isinstance(uid, int) and uid >= 1 and isinstance(rank, int) and rank >= 1:
                    chart.append((rank, uid))
            chart.sort()
            at = row.get("at")
            if chart and isinstance(at, str):
                snaps.append((parse_time(at), chart))
    snaps.sort()
    return snaps


def transitions(snaps, places):
    """counts[rank][place] over pairs 1..72h apart.

    place 0 counts a game that left the top `places`. Only games that were
    already on the earlier chart are counted: a game the history has never
    shown cannot be named, and its share is left unnamed instead.
    """
    counts = defaultdict(lambda: defaultdict(int))
    for i in range(len(snaps)):
        earlier = {uid: rank for rank, uid in snaps[i][1]}
        start = snaps[i][0]
        for j in range(i + 1, len(snaps)):
            hours = (snaps[j][0] - start) / 3600.0
            if hours < MIN_HOURS:
                continue
            if hours > MAX_HOURS:
                break
            later_top = set()
            for rank, uid in snaps[j][1]:
                if rank > places:
                    break
                later_top.add(uid)
                prev = earlier.get(uid)
                if prev is not None:
                    counts[prev][rank] += 1
            for uid, rank in earlier.items():
                if uid not in later_top:
                    counts[rank][0] += 1
    return counts


def forecast(snaps, places):
    counts = transitions(snaps, places)
    last = snaps[-1][1]
    rows = []
    strength = defaultdict(float)
    for place in range(1, places + 1):
        raw = []
        for rank, uid in last:
            bucket = counts.get(rank)
            if not bucket:
                continue
            total = sum(bucket.values())
            if total <= 0:
                continue
            p = bucket.get(place, 0) / total
            if p > 0.0:
                raw.append((uid, p))
        raw.sort(key=lambda item: (-item[1], item[0]))
        raw = raw[:MAX_ROWS]
        total = sum(p for _uid, p in raw)
        # Half of every place is left unnamed. The archive's reads are 4 to 11
        # hours apart, so a count of games that were absent from the earlier
        # chart misses the ones published between two reads, and a later chart
        # is largely those. The mass kept back is shared at scoring time by
        # every chart game this forecast did not name.
        reserve = UNNAMED
        mass = 1.0 - reserve
        if total <= 0 or mass <= 0:
            continue
        for uid, p in raw:
            share = mass * p / total
            rows.append((uid, place, share))
            strength[uid] += share
    ordered = sorted(strength, key=lambda uid: (-strength[uid], uid))
    top = ordered[:places]
    for _rank, uid in last:
        if len(top) >= places:
            break
        if uid not in top:
            top.append(uid)
    by_place = defaultdict(list)
    for uid, place, share in rows:
        rounded = round(share, 6)
        if rounded > 0.0:
            by_place[place].append({"universe_id": uid, "place": place, "p": rounded})
    out = []
    for place in range(1, places + 1):
        group = sorted(by_place[place], key=lambda row: (-row["p"], row["universe_id"]))[:MAX_ROWS]
        total = sum(row["p"] for row in group)
        if total > 1.0:
            scale = 0.999999 / total
            for row in group:
                row["p"] = round(row["p"] * scale, 6)
            group = [row for row in group if row["p"] > 0.0]
        out.extend(group)
    return out, top


def main():
    task = read_task(os.path.join(INPUT_DIR, "task.json"))
    places = int(task.get("places") or 10)
    snaps = read_history(os.path.join(INPUT_DIR, "history.ndjson"))
    if len(snaps) < 2:
        raise SystemExit("history needs at least two reads")
    rows, top = forecast(snaps, places)
    if len(top) != places or len(set(top)) != places:
        raise SystemExit("top10 is not %d distinct ids" % places)
    text = json.dumps({"probabilities": rows, "top10": top}, separators=(",", ":")) + "\n"
    if len(text.encode("utf-8")) > 1_048_576:
        raise SystemExit("forecast exceeds 1 MiB")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    name = task.get("output") or "forecast.json"
    with open(os.path.join(OUTPUT_DIR, name), "w", encoding="utf-8") as handle:
        handle.write(text)


if __name__ == "__main__":
    main()
