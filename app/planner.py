"""Legacy step-list preview (POST /api/plan), kept for API compatibility.

* DemoPlanner: deterministic keyword rules, labelled "Demo planner". Not a model.

Cosmos 3 integration lives in decisions.py and is used by the autonomous supervisor.
"""
import re
import time
import uuid
from abc import ABC, abstractmethod

from .schemas import Observation, TaskProposal, TaskStep


class ProviderUnavailable(RuntimeError):
    pass


class ReasoningProvider(ABC):
    id: str
    name: str
    kind: str

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def propose(self, obs: Observation) -> TaskProposal: ...


_RELATIONS = [(r"\b(beside|next to|near|by|alongside|left of|right of)\b", "adjacent to"),
              (r"\b(behind)\b", "behind"), (r"\b(in front of)\b", "in front of"),
              (r"\b(at|on|under)\b", "at")]


class DemoPlanner(ReasoningProvider):
    id, name, kind = "demo", "Demo planner (rule-based, not Cosmos or an LLM)", "demo_planner"

    def status(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "available": True,
                "message": "Deterministic keyword rules for demonstrating the interface"}

    def propose(self, obs: Observation) -> TaskProposal:
        text = obs.instruction.text.strip()
        low = text.lower()
        warnings, assumptions = [], []
        relation = next((rel for pat, rel in _RELATIONS if re.search(pat, low)), "at")

        t = obs.target
        if t is None:
            warnings.append("No target selected. Select one in the camera view or object list.")
            target_summary, xyz, target_label = None, None, "unspecified target"
        else:
            d = t.detection
            target_label = d.label
            target_summary = (f"{d.label} ({d.source}, id {d.id} at frame {t.last_seen_frame_number}"
                              + (f", conf {d.confidence:.2f}" if d.confidence is not None else "") + ")")
            xyz = list(d.depth.xyz_camera_m) if d.depth.xyz_camera_m else None
            if t.stale:
                warnings.append(f"Target is stale: {t.stale_reason}.")
            if d.depth.status != "ok":
                warnings.append(f"Target depth is {d.depth.status.replace('_', ' ')}"
                                + (f": {d.depth.reason}" if d.depth.reason else "") + ".")
            mentioned = set(re.findall(r"[a-z]+", low)) & {"red", "blue", "green", "yellow", "orange", "marker",
                                                            "cone", "rock", "pile", "bucket", "box"}
            if mentioned and not any(w in d.label.lower() for w in mentioned):
                warnings.append(f"Instruction mentions {', '.join(sorted(mentioned))}, but the selected target "
                                f"is '{d.label}'. Check that the right object is selected.")

        dig = not re.search(r"\b(dump|unload|empty)\b", low) or re.search(r"\b(scoop|dig|pick|load)\b", low)
        assumptions += [
            "Positions are in the camera frame. There's no camera-to-excavator calibration, so reach can't be checked.",
            "The dump location isn't defined in this milestone; the operator must specify it.",
        ]
        steps = [
            ("select_target", f"Confirm target: {target_label}", {"camera_xyz_m": xyz}),
            ("approach", f"Move bucket to a position {relation} the target (pose not computed)",
             {"relation": relation, "reference": target_label}),
        ]
        if dig:
            steps += [("scoop", f"Scoop material {relation} the target", {"relation": relation}),
                      ("lift", "Lift the bucket clear of the ground", {})]
        steps += [("rotate", "Rotate toward the dump zone (not yet defined)", {"dump_zone": None}),
                  ("dump", "Dump bucket contents", {}),
                  ("wait_for_operator", "Wait for operator review; nothing is executed automatically", {})]
        return TaskProposal(
            id=f"plan_{uuid.uuid4().hex[:8]}", created_at=time.time(), provider=self.name, provider_kind=self.kind,
            observation_id=obs.id, instruction=text, target_summary=target_summary,
            steps=[TaskStep(index=i + 1, action=a, description=desc, parameters=p) for i, (a, desc, p) in enumerate(steps)],
            warnings=warnings, assumptions=assumptions)


PROVIDERS: dict[str, ReasoningProvider] = {p.id: p for p in (DemoPlanner(),)}
