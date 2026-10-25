# usbcantesting

CAN interface for the Rivanna3 HIL rig. Reads and writes frames on the UCAN
USB-CAN adapter, decoded with the DBC files from the
[CAN-messages](https://github.com/solarcaratuva/CAN-messages) repo, which is a git
submodule at `resources/CAN-messages`.

Everything is in one file: [`hil_can.py`](hil_can.py).

## Setup (Raspberry Pi)

```bash
git submodule update --init --recursive
pip3 install -r requirements.txt
```

The adapter runs candleLight firmware, which the Linux kernel's `gs_usb` driver
handles, so it appears as an ordinary socketcan interface. Bring it up once per
boot -- the car's bus runs at **250 kbit/s**:

```bash
sudo ip link set can0 up type can bitrate 250000
```

Check it came up with `ip -details link show can0`.

## Use

```bash
# listen and decode everything
python3 hil_can.py

# send, by DBC message name -- signals left out are sent as 0
python3 hil_can.py PedalStatus throttle_pedal=128 brake_pedal=0

# send raw bytes to an ID
python3 hil_can.py 0x1F4 05 00 05

# send repeatedly until Ctrl-C (default 0.1 s, like the real boards)
python3 hil_can.py -r PedalStatus throttle_pedal=128
python3 hil_can.py -r 0.5 DashboardCommands hazards=1
```

Output is one line per frame; IDs in no DBC print as raw hex:

```
0x1F4  PedalStatus  throttle_pedal=5  brake_pedal=5
0x7FE  <unknown>  DE AD
```

Filter and log with the shell: `python3 hil_can.py | grep PedalStatus`,
`python3 hil_can.py | tee run.log`.

`CAN_CHANNEL` selects another interface (`can1`, or `vcan0` for a virtual bus with
no hardware).

## Notes

- **Bitrate lives in `ip link`, not in this script.** socketcan takes it from the
  interface, so a wrong rate shows up as silence or error frames and is fixed with
  `ip link`, not a flag.
- **Transmitting needs another node.** A CAN frame must be acknowledged by at least
  one other node, so sending with the boards powered off fails. The firmware also
  disables auto-retransmission, so unacknowledged frames are dropped, not retried.
- **Termination:** the UCAN carries its own termination (two 59 Ω resistors, ~118 Ω,
  with headers to disable), so it is one end of the bus and the transceiver breakout's
  120 Ω is the other. Powered off, CANH-CANL should measure about 60 Ω.
- socketcan is Linux only. This does not run on macOS.
