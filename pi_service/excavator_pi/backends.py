"""Output/feedback backends.

* SimulationBackend: no GPIO is touched. Joint positions are integrated from the commanded
  duty so closed-loop moves can be exercised end to end. Everything it reports is simulated.
* LgpioBackend: Raspberry Pi 5 GPIO through lgpio (software-timed PWM on the enable pins).
  It drives outputs only; there is no real position-feedback driver yet, so closed-loop moves
  are refused on hardware until one is added for the sensors that are actually wired.

Every backend starts with all outputs off and returns to that state on all_off()/close().
"""
import threading
import time

from .config import HardwareConfig

SIM_START = {"boom": 30.0, "cabin": 0.0, "bucket": 40.0, "stick": 45.0}


class SimulationBackend:
    kind = "simulation"

    def __init__(self, hw: HardwareConfig, speed_units_per_s=30.0):
        self._hw = hw
        self._speed = speed_units_per_s
        self._lock = threading.Lock()
        self._out = {j.name: (0, 0.0) for j in hw.joints}            # direction, duty
        self._pos = {j.name: SIM_START.get(j.name, 0.0) for j in hw.joints}
        self._t = time.monotonic()
        self.energised = False

    def _integrate(self):
        now = time.monotonic()
        dt, self._t = now - self._t, now
        for j in self._hw.joints:
            direction, duty = self._out[j.name]
            if direction and duty:
                sign = -direction if j.invert else direction
                self._pos[j.name] += sign * (duty / j.max_duty) * self._speed * dt
                if j.limits:  # simulated mechanical end stops
                    lo, hi = j.limits
                    self._pos[j.name] = min(max(self._pos[j.name], lo - 2.0), hi + 2.0)

    def set_output(self, joint, direction, duty):
        with self._lock:
            self._integrate()
            self._out[joint] = (direction, duty if direction else 0.0)
            self.energised = any(d and u for d, u in self._out.values())

    def all_off(self):
        with self._lock:
            self._integrate()
            self._out = {k: (0, 0.0) for k in self._out}
            self.energised = False

    def read_feedback(self, joint):
        """(position, monotonic timestamp) or None when the joint has no feedback."""
        if self._hw.joint(joint).feedback is None:
            return None
        with self._lock:
            self._integrate()
            return round(self._pos[joint], 3), time.monotonic()

    def outputs(self):
        with self._lock:
            return {k: {"direction": d, "duty": round(u, 3)} for k, (d, u) in self._out.items()}

    def close(self):
        self.all_off()


class LgpioBackend:
    kind = "hardware"

    def __init__(self, hw: HardwareConfig, gpiochip=None):
        if not hw.pins_verified:
            raise RuntimeError("Refusing hardware backend: pins_verified is false in the hardware config")
        bad = [j.name for j in hw.joints if j.feedback is not None and j.feedback.kind == "simulated"]
        if bad:
            raise RuntimeError(f"Refusing hardware backend: simulated feedback configured for {bad}")
        import lgpio  # only available on the Pi
        self._lg = lgpio
        self._hw = hw
        self._lock = threading.Lock()
        self._h = self._open_chip(gpiochip)
        self._pins = [p for j in hw.joints for p in (j.channel.enable, j.channel.in_a, j.channel.in_b)]
        for p in self._pins:
            lgpio.gpio_claim_output(self._h, p, 0)   # claimed low = driver disabled
        self._out = {j.name: (0, 0.0) for j in hw.joints}
        self.energised = False

    def _open_chip(self, chip):
        lg = self._lg
        candidates = [chip] if chip is not None else range(0, 6)
        for n in candidates:
            try:
                h = lg.gpiochip_open(n)
            except Exception:
                continue
            info = lg.gpio_get_chip_info(h)
            label = str(info[3]) if isinstance(info, (list, tuple)) and len(info) > 3 else ""
            if chip is not None or "rp1" in label.lower():
                return h
            lg.gpiochip_close(h)
        raise RuntimeError("Raspberry Pi 5 RP1 gpiochip not found; set PI_GPIOCHIP explicitly")

    def set_output(self, joint, direction, duty):
        j = self._hw.joint(joint)
        c = j.channel
        sign = -direction if j.invert else direction
        with self._lock:
            lg = self._lg
            lg.tx_pwm(self._h, c.enable, 0, 0)               # disable before changing direction
            lg.gpio_write(self._h, c.in_a, 1 if sign > 0 else 0)
            lg.gpio_write(self._h, c.in_b, 1 if sign < 0 else 0)
            if sign and duty > 0:
                lg.tx_pwm(self._h, c.enable, self._hw.pwm_frequency_hz, round(min(duty, j.max_duty) * 100, 1))
            else:
                lg.gpio_write(self._h, c.enable, 0)
            self._out[joint] = (direction, duty if direction else 0.0)
            self.energised = any(d and u for d, u in self._out.values())

    def all_off(self):
        with self._lock:
            for j in self._hw.joints:
                try:
                    self._lg.tx_pwm(self._h, j.channel.enable, 0, 0)
                except Exception:
                    pass
            for p in self._pins:
                self._lg.gpio_write(self._h, p, 0)
            self._out = {k: (0, 0.0) for k in self._out}
            self.energised = False

    def read_feedback(self, joint):
        return None  # no real feedback driver yet: closed-loop moves are refused on hardware

    def outputs(self):
        with self._lock:
            return {k: {"direction": d, "duty": round(u, 3)} for k, (d, u) in self._out.items()}

    def close(self):
        try:
            self.all_off()
            for p in self._pins:
                self._lg.gpio_free(self._h, p)
        finally:
            self._lg.gpiochip_close(self._h)
