#!/usr/bin/env python3
"""
DPMS-aware brightness button emulator for BTT HDMI LCD
Emulates button presses by temporarily driving a GPIO line LOW (short to GND),
then returning the line to INPUT.

Works on BTT CB2 and probably should work on Raspberry Pi and other BTT boards.
Xorg only.

Usage:
  python3 main.py config.ini test
  python3 main.py config.ini
"""

import configparser
import logging
import subprocess
import time
import sys
import signal
from dataclasses import dataclass

import gpiod


# ----------------------------
# Logging
# ----------------------------

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("screen-brightnessd")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
        h.setFormatter(fmt)
        logger.addHandler(h)
    return logger


LOG = setup_logger()


# ----------------------------
# Config models
# ----------------------------

@dataclass
class GPIOConfig:
    chip: str
    line_brighten: int
    line_dim: int


@dataclass
class PressConfig:
    dim_press_ms: float
    brighten_press_ms: float
    gap_ms: float
    dim_presses: int
    brighten_presses: int


@dataclass
class DPMSConfig:
    display: str
    poll_interval_ms: float
    suspend_grace_ms: float


# ----------------------------
# GPIO button emulation
# ----------------------------

class ButtonEmulator:
    """
    Emulate a button that shorts a signal to GND when pressed.

    - Released: line is configured as INPUT (Hi-Z)
    - Pressed:  line is configured as OUTPUT driving LOW
    - After each press set INPUT again.
    """
    def __init__(self, gpio_cfg: GPIOConfig, consumer: str = "screen-brightnessd"):
        self.gpio_cfg = gpio_cfg
        self.consumer = consumer

        self.chip = gpiod.Chip(gpio_cfg.chip)
        self.line_brighten = self.chip.get_line(gpio_cfg.line_brighten)
        self.line_dim = self.chip.get_line(gpio_cfg.line_dim)

        self.out_flags = gpiod.LINE_REQ_DIR_OUT
        self.in_flags = gpiod.LINE_REQ_DIR_IN

        # Ensure both lines start in INPUT mode (Hi-Z).
        self._set_input(self.line_brighten, "brighten")
        self._set_input(self.line_dim, "dim")

    def _set_input(self, line: gpiod.Line, name: str):
        """Force INPUT (Hi-Z) and release immediately."""
        try:
            line.request(consumer=self.consumer, type=self.in_flags)
            LOG.info("GPIO '%s' set to INPUT (Hi-Z)", name)
        except Exception as e:
            LOG.warning("Failed to set GPIO '%s' to INPUT: %s", name, e)
        finally:
            try:
                line.release()
            except Exception as e:
                LOG.warning("Failed to release GPIO '%s' after INPUT request: %s", name, e)

    def _press_line(self, line: gpiod.Line, name: str, press_ms: float):
        """Drive LOW for press_ms seconds, then return to INPUT."""
        try:
            line.request(consumer=self.consumer, type=self.out_flags, default_vals=[0])
            LOG.info("GPIO '%s' pressed (OUTPUT LOW) for %.0fms", name, press_ms)
        except Exception as e:
            LOG.error("Failed to request GPIO '%s' as OUTPUT LOW: %s", name, e)
            return

        time.sleep(press_ms/1000.0)

        try:
            line.release()
        except Exception as e:
            LOG.warning("Failed to release GPIO '%s' after press: %s", name, e)

        # Always return to INPUT after a press.
        self._set_input(line, name)

    def click_brighten(self, press_ms: float):
        self._press_line(self.line_brighten, "brighten", press_ms)

    def click_dim(self, press_ms: float):
        self._press_line(self.line_dim, "dim", press_ms)

    def close(self):
        """Failsafe: return both lines to INPUT on shutdown."""
        LOG.info("Shutting down: returning GPIO lines to INPUT")
        try:
            self._set_input(self.line_brighten, "brighten")
        except Exception as e:
            LOG.warning("Unexpected error while setting brighten INPUT: %s", e)
        try:
            self._set_input(self.line_dim, "dim")
        except Exception as e:
            LOG.warning("Unexpected error while setting dim INPUT: %s", e)
        try:
            self.chip.close()
        except Exception as e:
            LOG.warning("Failed to close gpiochip: %s", e)


# ----------------------------
# DPMS query
# ----------------------------

def read_dpms_state(display: str) -> str:
    """
    Returns one of: 'On', 'Off', 'Suspend', 'Standby', or 'Unknown'.

    Uses:
      xset -display :0 q
    and parses lines like:
      "Monitor is On"
      "Monitor is in Suspend"
    We take the last token as the state (after stripping).
    """
    try:
        out = subprocess.check_output(
            ["xset", "-display", display, "q"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as e:
        LOG.warning("Failed to query DPMS via xset (DISPLAY=%s): %s", display, e)
        return "Unknown"

    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Monitor is"):
            try:
                return s.rsplit(None, 1)[-1]
            except Exception:
                return "Unknown"
    return "Unknown"


# ----------------------------
# Config loader
# ----------------------------

def load_config(path: str):
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        raise FileNotFoundError(f"Cannot read config: {path}")

    dpms = DPMSConfig(
        display=cfg.get("dpms", "display", fallback=":0"),
        poll_interval_ms=cfg.getfloat("dpms", "poll_interval_ms", fallback=1000.0),
        suspend_grace_ms=cfg.getfloat("dpms", "suspend_grace_ms", fallback=5000.0),
    )

    gpio = GPIOConfig(
        chip=cfg.get("gpio", "chip"),
        line_brighten=cfg.getint("gpio", "line_brighten"),
        line_dim=cfg.getint("gpio", "line_dim"),
    )

    press = PressConfig(
        dim_press_ms=cfg.getint("press", "dim_press_ms", fallback=2000),
        brighten_press_ms=cfg.getint("press", "brighten_press_ms", fallback=2000),
        gap_ms=cfg.getint("press", "gap_ms", fallback=50),
        dim_presses=cfg.getint("press", "dim_presses", fallback=1),
        brighten_presses=cfg.getint("press", "brighten_presses", fallback=1),
    )

    return dpms, gpio, press


# ----------------------------
# Helpers
# ----------------------------

def do_clicks(action_name: str, be: ButtonEmulator, press_cfg: PressConfig):
    if action_name == "dim":
        click_fn = be.click_dim
        presses = max(0, int(press_cfg.dim_presses))
        press_ms = press_cfg.dim_press_ms
    else:
        click_fn = be.click_brighten
        presses = max(0, int(press_cfg.brighten_presses))
        press_ms = press_cfg.brighten_press_ms
    LOG.info("%s: presses=%d, press=%.0fms, gap=%.0fms",
             action_name, presses, press_ms, press_cfg.gap_ms)
    for i in range(presses):
        click_fn(press_ms)
        if i < presses - 1:
            time.sleep(press_cfg.gap_ms/1000.0)


# ----------------------------
# Test mode
# ----------------------------

def run_test(config_path: str):
    dpms_cfg, gpio_cfg, press_cfg = load_config(config_path)
    be = ButtonEmulator(gpio_cfg)

    try:
        print("======= TEST =======\n")
        print(f"Display: {dpms_cfg.display}")
        print(f"GPIO chip: {gpio_cfg.chip}")
        print(f"BRIGHTEN line: {gpio_cfg.line_brighten} (presses={press_cfg.brighten_presses}, ms={press_cfg.brighten_press_ms})")
        print(f"DIM line:      {gpio_cfg.line_dim} (presses={press_cfg.dim_presses}, ms={press_cfg.dim_press_ms})")
        print(f"Presses gap: {press_cfg.gap_ms:.1f}ms")
        print("\nSequence: DIM -> BRIGHTEN -> DIM -> BRIGHTEN (1s pauses)\n")

        print("1) DIM")
        do_clicks("dim", be, press_cfg)
        time.sleep(1.0)

        print("2) BRIGHTEN")
        do_clicks("brighten", be, press_cfg)
        time.sleep(1.0)

        print("3) DIM")
        do_clicks("dim", be, press_cfg)
        time.sleep(1.0)

        print("4) BRIGHTEN")
        do_clicks("brighten", be, press_cfg)

        print("\nTest completed.\n")
    finally:
        be.close()


# ----------------------------
# Daemon mode
# ----------------------------

def run_daemon(config_path: str):
    dpms_cfg, gpio_cfg, press_cfg = load_config(config_path)
    be = ButtonEmulator(gpio_cfg)

    stop = False

    def handle_sig(signum, _frame):
        nonlocal stop
        LOG.info("Signal %s received, shutting down...", signum)
        stop = True

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    last_state = None
    off_since = None
    dimmed = False

    LOG.info("screen-brightnessd started.")
    LOG.info("Monitoring DPMS on DISPLAY=%s (poll=%.0fms, grace=%.0fms)",
             dpms_cfg.display, dpms_cfg.poll_interval_ms, dpms_cfg.suspend_grace_ms)
    LOG.info("GPIO: chip=%s, brighten=%d, dim=%d", gpio_cfg.chip, gpio_cfg.line_brighten, gpio_cfg.line_dim)

    try:
        while not stop:
            state = read_dpms_state(dpms_cfg.display)

            if state != last_state:
                LOG.info("DPMS state changed: %s -> %s", last_state, state)

                # Transition to On: brighten if we previously dimmed, ignore on start
                if state == "On":
                    off_since = None
                    if dimmed and press_cfg.brighten_presses > 0:
                        do_clicks("brighten", be, press_cfg)
                    dimmed = False

                # Transition to Off/Suspend/Standby: start timer
                if state in ("Off", "Suspend", "Standby"):
                    if off_since is None:
                        off_since = time.monotonic()

                last_state = state

            # If still Off/Suspend/Standby and grace elapsed: dim once
            if state in ("Off", "Suspend", "Standby"):
                if off_since is None:
                    off_since = time.monotonic()

                elapsed = time.monotonic() - off_since
                if (not dimmed) and (elapsed >= dpms_cfg.suspend_grace_ms/1000.0):
                    if press_cfg.dim_presses > 0:
                        do_clicks("dim", be, press_cfg)
                    dimmed = True

            time.sleep(dpms_cfg.poll_interval_ms/1000.0)

    finally:
        try:
            if dimmed == True:
                LOG.info("exit signal detected while screen was dim, brightening screen.")
                do_clicks("brighten", be, press_cfg)
        except:
            LOG.warning("tried to brighten screen on exit but failed.")
        be.close()
        LOG.info("screen-brightnessd stopped (GPIO lines returned to INPUT).")


# ----------------------------
# Entry point
# ----------------------------

def main():
    if len(sys.argv) not in (2, 3):
        print(f"Usage: {sys.argv[0]} /path/to/config.ini [test]")
        return 2

    config_path = sys.argv[1]
    mode = sys.argv[2].lower() if len(sys.argv) == 3 else "daemon"

    if mode == "test":
        run_test(config_path)
        return 0

    run_daemon(config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
