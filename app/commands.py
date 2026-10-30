"""One command pipeline for typed and spoken instructions.

parse_command() is deterministic and fast, so stop/pause never wait for a model. Spoken
transcripts only auto-run the commands that reduce motion (stop, pause, cancel); everything
else becomes a preview the operator confirms with "Send".
"""
import re
import time
from dataclasses import dataclass
from typing import Optional

_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "once": 1, "twice": 2}
_CYCLES = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:cycles?|times|loads?|scoops?|rounds?)\b"
                     r"|\b(once|twice)\b")

_STOP = re.compile(r"\b(stop|halt|freeze|abort|emergency)\b")
_PAUSE = re.compile(r"\b(pause|hold on|hold it)\b")
_RESUME = re.compile(r"\b(resume|continue|carry on)\b")
_CANCEL = re.compile(r"\b(cancel|never mind|nevermind)\b")
_IDENTIFY = re.compile(r"\b(identify|find|locate|where is|select|show me)\b")
_TASK = re.compile(r"\b(scoop|dig|dump|load|excavate)\w*")
_START = re.compile(r"\b(start|begin|run|execute|go ahead|autonomous)\b")
_COLOURS = ("red", "blue", "green", "yellow", "orange")

AUTO_FROM_VOICE = {"stop", "pause", "cancel"}   # motion-reducing commands run without confirmation


@dataclass
class Command:
    kind: str                       # stop | pause | resume | cancel | identify | plan_task | unsupported
    text: str
    cycles: Optional[int] = None
    start_requested: bool = False
    object_phrase: Optional[str] = None


def parse_cycles(text: str) -> Optional[int]:
    m = _CYCLES.search(text.lower())
    if not m:
        return None
    word = m.group(1) or m.group(2)
    return int(word) if word.isdigit() else _NUM[word]


def parse_command(text: str) -> Command:
    t = " ".join(text.lower().replace("-", " ").split()).strip(" .!?")
    if _STOP.search(t):
        return Command("stop", text)
    if _PAUSE.search(t):
        return Command("pause", text)
    if _CANCEL.search(t):
        return Command("cancel", text)
    if _RESUME.search(t) and not _TASK.search(t):
        return Command("resume", text)
    if _TASK.search(t):
        return Command("plan_task", text, cycles=parse_cycles(t), start_requested=bool(_START.search(t)))
    if _IDENTIFY.search(t):
        phrase = _IDENTIFY.split(t, maxsplit=1)[-1].strip()
        phrase = re.sub(r"^(the|a|an)\s+", "", phrase)
        return Command("identify", text, object_phrase=phrase or None)
    return Command("unsupported", text)


def match_detections(phrase: str, detections) -> list:
    """Detections whose label matches the phrase ("red marker", "cup", ...). Deterministic."""
    words = set(re.findall(r"[a-z]+", (phrase or "").lower()))
    colour = next((c for c in _COLOURS if c in words), None)
    out = []
    for d in detections:
        label = d.label.lower()
        if d.source == "color_marker":
            if (colour and label.startswith(colour)) or (not colour and words & {"marker", "cone"}):
                out.append(d)
        elif words & set(label.split()) or label in (phrase or "").lower():
            out.append(d)
    return out


class CommandService:
    """Routes a typed or transcribed command. Unconfirmed voice input only runs AUTO_FROM_VOICE."""

    def __init__(self, supervisor, selector, detector, events_log):
        self.supervisor, self.selector, self.detector, self.events = supervisor, selector, detector, events_log
        self.last = None

    def handle(self, text, source="typed", provider_id="rules", confirmed=True, recording=None, transcribed_at=None):
        from .schemas import Instruction  # local import keeps parse_command import-light
        cmd = parse_command(text)
        if not confirmed and cmd.kind not in AUTO_FROM_VOICE:
            result = {"handled": False, "kind": cmd.kind, "needs_confirmation": True,
                      "message": f"Heard \"{text}\". Check the transcript and press Send to run it."}
            return self._done(result, source)
        try:
            result = self._dispatch(cmd, source, provider_id,
                                    lambda: Instruction(text=text[:500], source=source, recording=recording,
                                                        transcribed_at=transcribed_at))
        except Exception as exc:  # TransitionError, ValueError, RuntimeError from the supervisor
            result = {"handled": False, "kind": cmd.kind, "message": str(exc)}
        return self._done(result, source)

    def _done(self, result, source):
        self.last = {**result, "source": source, "at": time.time()}
        self.events.add("command", f"[{source}] {result['kind']}: {result['message']}",
                        "info" if result.get("handled") else "warn")
        return self.last

    def _dispatch(self, cmd: Command, source, provider_id, instruction):
        sup = self.supervisor
        if cmd.kind == "stop":
            ack = sup.stop(source=f"{source} command", reason="stop command")
            return {"handled": True, "kind": "stop", "stop_ack": ack,
                    "message": "Stopped" + (" (Pi acknowledged)" if ack["acknowledged"] else f" — Pi: {ack['error']}")}
        if cmd.kind == "pause":
            return {"handled": True, "kind": "pause", "message": f"Pause: {sup.pause(source)}"}
        if cmd.kind == "cancel":
            return {"handled": True, "kind": "cancel", "message": f"Cancel: {sup.cancel(source)}"}
        if cmd.kind == "resume":
            sup.resume()
            return {"handled": True, "kind": "resume", "message": "Resumed"}
        if cmd.kind == "identify":
            frame = self.detector.latest()
            if frame is None:
                return {"handled": False, "kind": "identify", "message": "No detection frame (camera or detector not running)"}
            matches = match_detections(cmd.object_phrase or "", frame.detections)
            if not matches:
                hint = (" Turn on colour-marker mode to look for coloured markers."
                        if any(c in (cmd.object_phrase or "") for c in _COLOURS) and
                        not self.detector.settings.marker.enabled else "")
                return {"handled": False, "kind": "identify",
                        "message": f"No detection matches \"{cmd.object_phrase}\" in the current frame.{hint}"}
            if len(matches) > 1:
                return {"handled": False, "kind": "identify", "ambiguous": [m.id for m in matches],
                        "message": f"{len(matches)} candidates ({', '.join(f'{m.label} {m.id}' for m in matches)}): "
                                   "click the intended one"}
            t = self.selector.select(frame, matches[0].id)
            return {"handled": True, "kind": "identify", "target_id": matches[0].id,
                    "message": f"Selected {t.detection.label} ({matches[0].id}). Check it on the camera view."}
        if cmd.kind == "plan_task":
            obs_id = sup.plan(instruction(), provider_id, operator_cycles=cmd.cycles, start_requested=cmd.start_requested)
            return {"handled": True, "kind": "plan_task", "observation_id": obs_id,
                    "message": "Planning" + (f" {cmd.cycles} cycle(s)" if cmd.cycles else "")
                               + (" — approval required before it can start" if cmd.start_requested else "")}
        return {"handled": False, "kind": "unsupported",
                "message": "Not a supported command. Try: identify the red marker · plan a scoop-and-dump task · "
                           "start autonomous scoop-and-dump for two cycles · pause · stop"}
