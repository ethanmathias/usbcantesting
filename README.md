# usbcantesting

CAN tooling for the Rivanna3 HIL rig. Two adapters, one script each:

| Script | Adapter | Runs on |
| --- | --- | --- |
| [`hil_can.py`](hil_can.py) | UCAN (candleLight, socketcan) | Linux / Raspberry Pi |
| [`fdcanusb_setup.py`](fdcanusb_setup.py) | mjbots fdcanusb | macOS, Windows, Linux |

`hil_can.py` reads and writes frames, decoded with the DBC files from the
[CAN-messages](https://github.com/solarcaratuva/CAN-messages) repo, which is a git
submodule at `resources/CAN-messages`. `fdcanusb_setup.py` configures the other
adapter so it can talk to the car's bus at all.

## hil_can.py: UCAN adapter

### Setup (Raspberry Pi)

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

### Use

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

### Notes

- **Bitrate lives in `ip link`, not in this script.** socketcan takes it from the
  interface, so a wrong rate shows up as silence or error frames and is fixed with
  `ip link`, not a flag.
- **Transmitting needs another node.** A CAN frame must be acknowledged by at least
  one other node, so sending with the boards powered off fails. The firmware also
  disables auto-retransmission, so unacknowledged frames are dropped, not retried.
- **Termination:** the UCAN carries its own termination (two 59 Ω resistors, ~118 Ω,
  with headers to disable), so it is one end of the bus and the transceiver breakout's
  120 Ω is the other. Powered off, CANH-CANL should measure about 60 Ω.
- socketcan is Linux only, so `hil_can.py` does not run on macOS or Windows. The
  fdcanusb below does, because it is a plain serial device.

## fdcanusb_setup.py: mjbots fdcanusb

The [fdcanusb](https://mjbots.com/products/fdcanusb) is a USB CDC-ACM device with
a line-based text protocol, not a socketcan interface, so it needs no kernel
driver and works the same on macOS, Windows and Linux.

It ships set to **1 Mbit/s CAN FD with bitrate switching**, and the car runs
**250 kbit/s classic CAN**. A classic node treats an FD frame as a form error, so
until this is changed the boards reject everything the adapter sends and the bus
looks dead. That is what this script is for.

### Setup

```bash
pip install -r requirements-fdcanusb.txt
```

That is the whole dependency list: pyserial. No submodule, no DBC files, nothing
to install as root.

On Linux, add yourself to the `dialout` group (`sudo usermod -aG dialout $USER`,
then log out and back in) or every command fails with a permission error. macOS
and Windows need nothing; Windows picks up the CDC driver on its own.

### Use

Run the same commands on every platform. On Windows use `py -3` in place of
`python3` if `python3` is not on your PATH.

```bash
# which serial port is the adapter
python3 fdcanusb_setup.py --list

# what the three keys we care about are set to right now
python3 fdcanusb_setup.py --show

# switch it to the car's bus, then UNPLUG AND REPLUG
python3 fdcanusb_setup.py --apply --write

# put it back the way it came, then UNPLUG AND REPLUG
python3 fdcanusb_setup.py --restore --write

# everything the adapter will tell you
python3 fdcanusb_setup.py --dump
```

`--list` prints every serial port with its USB IDs and marks the fdcanusb, which
is how you tell it apart from an ST-Link. Both are vendor `0483`, so matching on
the vendor alone picks the wrong device; the fdcanusb is `0483:5740`. If two are
plugged in, pass `--port` (`/dev/cu.usbmodem...`, `/dev/ttyACM0`, `COM5`).

### The one thing that will waste your afternoon

**`conf set` does not reconfigure a running CAN core.** It stages a value, and
`conf get` then reports the new value whether or not the core is using it. So the
adapter will cheerfully tell you it is at 250 kbit/s while it is still
transmitting at 1 Mbit/s, and all you see is a silent bus.

Always pass `--write`, and always physically unplug and replug afterwards. The
core only reads its configuration when it initialises.

### Notes

- **Transmitting needs another node.** A CAN frame has to be acknowledged by at
  least one other node, so sending with the boards powered off fails. On the
  fdcanusb that shows up as the error counters climbing into error-passive, and
  eventually bus-off, which needs a replug to clear.
- **Put it back when you are done.** The adapter is shared, and the next person
  to use it with a moteus controller will expect 1 Mbit/s FD. `--restore --write`
  and a replug.
- The configuration as found, before any of this, is recorded in
  [`fdcanusb-original-config.txt`](fdcanusb-original-config.txt). The `AS_FOUND`
  values in the script come from that file, so if you ever change the adapter's
  baseline, change both.
