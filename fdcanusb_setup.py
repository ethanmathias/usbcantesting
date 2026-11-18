#!/usr/bin/env python3
"""Point an mjbots fdcanusb at the Rivanna3 bus, and put it back afterwards.

The adapter ships configured for 1 Mbit/s CAN FD with bitrate switching. The
Rivanna3 boards run classic CAN at 250 kbit/s, and a classic node treats an FD
frame as a form error, so all three keys below have to change or the boards
reject everything.

    python3 fdcanusb_setup.py --list              # serial ports, and which one is it
    python3 fdcanusb_setup.py --show              # current values of the keys we touch
    python3 fdcanusb_setup.py --apply --write     # 250 kbit/s classic, then replug
    python3 fdcanusb_setup.py --restore --write   # back to how the adapter was found
    python3 fdcanusb_setup.py --dump              # full `conf enumerate`

Runs on macOS, Windows and Linux. Everything goes over pyserial, so there is no
socketcan here and no driver to install: the fdcanusb is a USB CDC-ACM device
with a line-based text protocol, unlike the UCAN adapter that hil_can.py drives.

`conf set` only stages a value. It does NOT reconfigure a running CAN core, and
`conf get` reports the new value either way, which is an easy way to spend an
afternoon testing at a bitrate you are not actually using. Pass --write and
then physically unplug and replug the adapter.

The configuration as found is recorded in fdcanusb-original-config.txt.
"""

import argparse
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial is not installed. Run: pip install -r requirements-fdcanusb.txt")

FDCANUSB_VID = 0x0483  # STMicroelectronics
FDCANUSB_PID = 0x5740  # STM32 CDC ACM, as the fdcanusb presents itself

# What the Rivanna3 bus needs.
RIVANNA3 = {
    "can.bitrate": "250000",
    "can.fdcan_frame": "0",
    "can.bitrate_switch": "0",
}

# What this adapter was set to when first found, from
# fdcanusb-original-config.txt. Keep the two in step.
AS_FOUND = {
    "can.bitrate": "1000000",
    "can.fdcan_frame": "1",
    "can.bitrate_switch": "1",
}

# Read-only keys worth seeing alongside, since they change how the bench behaves.
CONTEXT_KEYS = ("can.termination", "can.automatic_retransmission",
                "can.autostart", "can.bus_monitor")


def candidates():
    return [p for p in serial.tools.list_ports.comports()
            if p.vid == FDCANUSB_VID and p.pid == FDCANUSB_PID]


def list_ports():
    """Print every serial port, marking the ones that look like an fdcanusb."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("no serial ports at all. Is anything plugged in?")
        return
    for port in sorted(ports, key=lambda p: p.device):
        vid = f"{port.vid:04X}" if port.vid is not None else "----"
        pid = f"{port.pid:04X}" if port.pid is not None else "----"
        is_fdcanusb = port.vid == FDCANUSB_VID and port.pid == FDCANUSB_PID
        mark = "fdcanusb" if is_fdcanusb else ""
        print(f"  {port.device:<24} {vid}:{pid}  {port.description}  {mark}")


def find_port():
    """Return the fdcanusb's port name, or exit with a usable message."""
    found = candidates()
    if not found:
        # An ST-Link is also 0483, but a different product id, so a bare vendor
        # match would happily hand back the wrong device.
        sys.exit("no fdcanusb found (looking for USB 0483:5740).\n"
                 "Plug it in, check it is not the ST-Link you are seeing, and\n"
                 "run --list to see what the machine does have.")
    if len(found) > 1:
        names = ", ".join(p.device for p in found)
        sys.exit(f"more than one fdcanusb found ({names}); pass --port to pick one.")
    return found[0].device


class Fdcanusb:
    def __init__(self, port):
        try:
            self.serial = serial.Serial(port, 115200, timeout=0.5,
                                        dsrdtr=False, rtscts=False)
        except (serial.SerialException, OSError) as exc:
            # On Windows a port held by another program fails here rather than
            # on the first read, and PuTTY or an open Arduino monitor is the
            # usual culprit.
            sys.exit(f"cannot open {port}: {exc}\n"
                     "Close anything else talking to the adapter, then try again.")
        # Some USB CDC stacks hold the device in reset until DTR is asserted.
        # pyserial raises on a port that does not support the line, so this is
        # best effort rather than a requirement.
        try:
            self.serial.dtr = True
        except (serial.SerialException, OSError):
            pass
        time.sleep(0.2)
        # Drain anything already queued, by reading rather than flushing.
        while self.serial.in_waiting:
            self.serial.read(self.serial.in_waiting)
        self.port = port

    def command(self, text, lines=1, settle=0.05):
        """Send one command and return its reply lines.

        Deliberately line-synchronous, and deliberately never calls
        reset_input_buffer(): flushing this firmware's CDC input buffer on
        macOS wedges it until it is physically replugged, and on every platform
        it loses sync, so a reply can be read as the answer to the next command.
        """
        try:
            self.serial.write((text + "\n").encode())
            self.serial.flush()
            time.sleep(settle)
            out = []
            for _ in range(lines + 24):
                reply = self.serial.readline().decode("utf-8", "replace").strip()
                if not reply or reply.startswith("rcv "):
                    # Received frames arrive unsolicited and interleave with
                    # replies, so on a busy bus a `conf get` otherwise returns
                    # somebody's CAN frame instead of the setting.
                    continue
                out.append(reply)
                if len(out) >= lines:
                    break
            return "\n".join(out)
        except (serial.SerialException, OSError) as exc:
            sys.exit(f"the adapter stopped answering on {self.port}: {exc}\n"
                     "Unplug and replug it, then try again.")

    def get(self, key):
        return self.command(f"conf get {key}", lines=1)

    def set(self, key, value):
        reply = self.command(f"conf set {key} {value}")
        if "ERR" in reply:
            sys.exit(f"setting {key}={value} failed: {reply}")
        return self.get(key)

    def close(self):
        self.serial.close()


def show(dev, keys):
    for key in keys:
        print(f"  {key} = {dev.get(key)}")


def main():
    parser = argparse.ArgumentParser(description="Configure an mjbots fdcanusb for the Rivanna3 bus.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="print serial ports and which is the adapter")
    group.add_argument("--show", action="store_true", help="print the keys this script touches")
    group.add_argument("--apply", action="store_true", help="set 250 kbit/s classic CAN")
    group.add_argument("--restore", action="store_true", help="put the keys back as found")
    group.add_argument("--dump", action="store_true", help="print the full configuration")
    parser.add_argument("--port", help="serial device, if auto-detection picks wrong")
    parser.add_argument("--write", action="store_true",
                        help="persist with `conf write`. Needed for a change to take "
                             "effect at all: conf set only stages a value, it does not "
                             "reconfigure a running CAN core. Power cycle afterwards.")
    args = parser.parse_args()

    if args.list:
        list_ports()
        return

    port = args.port or find_port()
    dev = Fdcanusb(port)
    print(f"fdcanusb on {port}")

    try:
        if args.dump:
            print(dev.command("conf enumerate", lines=200, settle=1.5))
        elif args.show:
            show(dev, list(RIVANNA3) + list(CONTEXT_KEYS))
        else:
            wanted = RIVANNA3 if args.apply else AS_FOUND
            label = "Rivanna3 (250 kbit/s, classic)" if args.apply else "as found"
            print(f"setting {label}:")
            for key, value in wanted.items():
                now = dev.set(key, value)
                flag = "ok" if now == value else f"MISMATCH, reads {now}"
                print(f"  {key} = {now}   [{flag}]")
            if args.write:
                print(f"\nconf write -> {dev.command('conf write', settle=2.0)}")
                print("Now UNPLUG AND REPLUG the adapter. The CAN core only reads\n"
                      "this configuration when it initialises, so until it is power\n"
                      "cycled it keeps running with whatever it started with -- and\n"
                      "`conf get` will report the new value regardless.")
            else:
                print("\nStaged only: the running CAN core is still using its previous\n"
                      "configuration, and unplugging the adapter discards this. Pass\n"
                      "--write (then replug) to make it take effect.")
            print(f"status: {dev.command('can status')}")
    finally:
        dev.close()


if __name__ == "__main__":
    main()
