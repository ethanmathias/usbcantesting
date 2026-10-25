#!/usr/bin/env python3
"""Read and write CAN frames on the HIL rig, decoded with the CAN-messages DBCs.

On the Raspberry Pi the kernel's gs_usb driver presents the UCAN as can0, so
bring it up once per boot:

    sudo ip link set can0 up type can bitrate 250000

Listen and decode everything:

    python3 hil_can.py

Send, by DBC message name -- signals you leave out are sent as 0:

    python3 hil_can.py PedalStatus throttle_pedal=128 brake_pedal=0

Send raw bytes to an ID, when there is no DBC entry:

    python3 hil_can.py 0x1F4 05 00 05

Send repeatedly until Ctrl-C (default every 0.1 s, like the real boards):

    python3 hil_can.py -r PedalStatus throttle_pedal=128
    python3 hil_can.py -r 0.5 DashboardCommands hazards=1

A second adapter or a virtual bus for testing without hardware:

    CAN_CHANNEL=can1 python3 hil_can.py
    sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
    CAN_CHANNEL=vcan0 python3 hil_can.py

socketcan is Linux only, so this runs on the Pi, not on macOS.
"""

import glob
import os
import sys
import time

import can
import cantools

CHANNEL = os.environ.get("CAN_CHANNEL", "can0")
INTERFACE = os.environ.get("CAN_INTERFACE", "socketcan")
DBC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "resources", "CAN-messages")

# --- load every DBC, indexed by frame ID and by message name ---
by_id, by_name = {}, {}
for path in sorted(glob.glob(os.path.join(DBC_DIR, "*.dbc"))):
    for message in cantools.database.load_file(path).messages:
        by_id.setdefault(message.frame_id, message)
        by_name.setdefault(message.name, message)
print(f"loaded {len(by_id)} message IDs from {DBC_DIR}")

# --- command line: [-r [period]] [MessageName sig=val ... | 0xID BYTES ...] ---
args = sys.argv[1:]
period = None
if args and args[0] == "-r":
    args.pop(0)
    period = 0.1
    if args:
        try:
            period = float(args[0])
            args.pop(0)
        except ValueError:
            pass

try:
    bus = can.Bus(interface=INTERFACE, channel=CHANNEL, bitrate=250000)
except Exception as exc:
    hint = f"Try: sudo ip link set {CHANNEL} up type can bitrate 250000"
    if sys.platform != "linux":
        hint = ("socketcan is Linux only -- run this on the Raspberry Pi. "
                f"(this is {sys.platform})")
    sys.exit(f"cannot open {CHANNEL}: {exc}\n{hint}")

if args:  # ---------------- write ----------------
    target = args.pop(0)
    if target in by_name:
        message = by_name[target]
        values = {signal.name: 0 for signal in message.signals}
        for pair in args:
            name, _, value = pair.partition("=")
            if name not in values:
                sys.exit(f"{message.name} has no signal '{name}'. Signals: "
                         f"{', '.join(values)}")
            values[name] = float(value) if "." in value else int(value, 0)
        data = message.encode(values)
        label = f"{message.name}  {values}"
        frame_id = message.frame_id
    else:
        try:
            frame_id = int(target, 0)
        except ValueError:
            sys.exit(f"'{target}' is neither a frame ID nor a message name. "
                     f"Known names: {', '.join(sorted(by_name))}")
        data = bytes.fromhex("".join(args))
        label = by_id[frame_id].name if frame_id in by_id else "<raw>"

    frame = can.Message(arbitration_id=frame_id, data=data, is_extended_id=False)
    print(f"sending 0x{frame_id:03X} [{len(data)}] {data.hex(' ').upper()}  {label}")
    try:
        while True:
            bus.send(frame)
            if period is None:
                break
            time.sleep(period)
    except can.CanError as exc:
        sys.exit(f"send failed: {exc}\nIs anything else on the bus to acknowledge it?")
    except KeyboardInterrupt:
        pass
    bus.shutdown()
    sys.exit(0)

# ---------------- read ----------------
print(f"listening on {CHANNEL} (Ctrl-C to stop)")
try:
    for msg in bus:
        data = bytes(msg.data)
        message = by_id.get(msg.arbitration_id)
        if message is None:
            print(f"0x{msg.arbitration_id:03X}  <unknown>  {data.hex(' ').upper()}")
            continue
        signals = message.decode(data, allow_truncated=True)
        values = "  ".join(f"{name}={value}" for name, value in signals.items())
        print(f"0x{msg.arbitration_id:03X}  {message.name}  {values}")
except KeyboardInterrupt:
    bus.shutdown()
