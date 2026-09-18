"""Short-lived spatial identities independent of detector output order."""

from itertools import permutations
from math import dist


class HandIdentity:
    def __init__(self):
        self.tracks = {}
        self.serial = 0

    def update(self, points, labels, now, scale):
        self.tracks = {key:value for key,value in self.tracks.items() if now-value[0] <= .45}
        keys = list(self.tracks)
        # At most two hands. Exhaustive assignment avoids greedy identity swaps.
        choices = keys + [None] * len(points)
        best = None
        best_cost = float('inf')
        for assignment in permutations(choices,len(points)):
            real = [key for key in assignment if key is not None]
            if len(set(real)) != len(real):
                continue
            cost = 0
            for i,key in enumerate(assignment):
                if key is None:
                    cost += 1.5
                else:
                    _,point,label = self.tracks[key]
                    travel = dist(points[i],point)/scale
                    cost += travel + (0 if labels[i] == label else .08) if travel <= 1.4 and (labels[i] == label or travel <= .45) else 100
            if cost < best_cost:
                best,best_cost = assignment,cost
        identities = []
        uncertain = set()
        for i,point in enumerate(points):
            key = best[i] if best is not None else None
            if key is None:
                key = labels[i]
                if key in self.tracks or key in identities:
                    self.serial += 1
                    key = f"{key}-{self.serial}"
            identities.append(key)
            # If both palms nearly overlap, do not treat a guessed identity as
            # a confirmed swing owner or a valid handoff candidate.
            if len(points) == 2 and dist(points[0],points[1]) < scale*.18:
                uncertain.add(key)
        for i,key in enumerate(identities):
            if key not in uncertain:
                self.tracks[key] = (now,points[i],labels[i])
        return identities,uncertain
