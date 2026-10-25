#!/usr/bin/env python3
"""macOS mirror of hil_can.py: same commands, but talks to the UCAN directly over
libusb instead of socketcan, which does not exist on macOS.

Setup:

    brew install libusb
    pip3 install -r requirements-macos.txt

Same usage as hil_can.py -- the bitrate is set here, not by `ip link`:

    python3 hil_can_macos.py                                       # listen
    python3 hil_can_macos.py PedalStatus throttle_pedal=128        # send once
    python3 hil_can_macos.py 0x1F4 05 00 05                        # send raw bytes
    python3 hil_can_macos.py -r PedalStatus throttle_pedal=128     # repeat every 0.1 s

If a run dies partway, the adapter stops answering and every later run fails with
"Pipe error" or "Input/Output Error" until you unplug and replug it. That is the
firmware, not this script. macOS also blocks unapproved USB accessories, so answer
the "Allow accessory to connect?" prompt, or set Privacy & Security ->
Allow accessories to connect -> Automatically when unlocked.
"""

import glob
import os
import sys
import time

import can
import cantools
import usb.core
import usb.util

BITRATE = 250000
DBC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "resources", "CAN-messages")

# --- macOS fixes, all in the gs_usb/pyusb libraries rather than here ---------
# 1. gs_usb calls detach_kernel_driver() unconditionally; macOS has no gs_usb
#    kernel driver and libusb cannot detach one, so the call errors out.
usb.core.Device.detach_kernel_driver = lambda self, interface: None
usb.core.Device.is_kernel_driver_active = lambda self, interface: False
# 2. gs_usb sends control requests with no timeout, so they get pyusb's 1 s
#    default; some are slower than that.
_ctrl = usb.core.Device.ctrl_transfer
usb.core.Device.ctrl_transfer = (
    lambda self, *a, timeout=None, **kw: _ctrl(self, *a, timeout=timeout or 5000, **kw)
)

# --- load every DBC, indexed by frame ID and by message name ----------------
by_id, by_name = {}, {}
for path in sorted(glob.glob(os.path.join(DBC_DIR, "*.dbc"))):
    for message in cantools.database.load_file(path).messages:
        by_id.setdefault(message.frame_id, message)
        by_name.setdefault(message.name, message)
print(f"loaded {len(by_id)} message IDs from {DBC_DIR}")

# --- command line: [-r [period]] [MessageName sig=val ... | 0xID BYTES ...] --
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

if usb.core.find(idVendor=0x1D50, idProduct=0x606F) is None:
    sys.exit("no UCAN on the USB bus (looking for 1D50:606F).\n"
             "Plug it in, and approve the macOS 'Allow accessory to connect?' prompt.")

try:
    bus = can.Bus(interface="gs_usb", channel="can0", index=0, bitrate=BITRATE)
except Exception as exc:
    sys.exit(f"cannot open the UCAN: {exc}\n"
             "Unplug and replug it, then try again.")

# gs_usb swallows every USB read error and python-can turns that into "no
# message", so an unclaimable interface looks exactly like an idle bus. Claim it
# up front to turn that silence into a real error.
try:
    usb.util.claim_interface(bus.gs_usb.gs_usb, 0)
except Exception as exc:
    bus.shutdown()
    sys.exit(f"cannot claim the adapter's data interface: {exc}\n"
             "Reads would silently return nothing. Unplug and replug it.")

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
print(f"listening at {BITRATE} bit/s (Ctrl-C to stop)")
errors = 0
try:
    for msg in bus:
        if msg.is_error_frame:
            # python-can's gs_usb reader reports one of these per real frame on
            # this firmware; the data frames themselves decode correctly.
            errors += 1
            continue
        data = bytes(msg.data)
        message = by_id.get(msg.arbitration_id)
        if message is None:
            print(f"0x{msg.arbitration_id:03X}  <unknown>  {data.hex(' ').upper()}")
            continue
        signals = message.decode(data, allow_truncated=True)
        values = "  ".join(f"{name}={value}" for name, value in signals.items())
        print(f"0x{msg.arbitration_id:03X}  {message.name}  {values}")
except KeyboardInterrupt:
    pass
finally:
    bus.shutdown()
    if errors:
        print(f"\n({errors} error frames ignored)")
