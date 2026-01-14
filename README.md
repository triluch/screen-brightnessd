# screen-brightnessd

## Description

**screen-brightnessd** is a small Linux daemon that controls screen backlight brightness by emulating physical brightness buttons via GPIO.

This was made because BTT HDMI screens don't turn off backlight when turned off by software, so it glows all the time.

It is intended to be used together with KlipperScreen and reacts to the display DPMS state (`xset`):
- dims the screen when the monitor enters `Suspend/Off/Standby`
- brightens the screen when the monitor returns to `On`

The project was tested **only** on what I have:
- BTT CB2 in BTT Manta M8P
- BTT HDMI5 display

It **may** also work on Raspberry Pi and other HDMI displays with hardware brightness buttons, but this is **not guaranteed**.

Everything here you do **at your own risk**.

## Requirements

- Klipper with KlipperScreen
- Xorg-based session (required for `xset` / DPMS)
- Debian with systemd
- libgpiod support
- Basic soldering skills (need to solder 2 wires)

Optional but recommended:
- Moonraker (for update integration)

The daemon may work on other Linux distributions, but installer was made only for Debian and `printer_data` directly under your home directory.

## Wiring

This section is based **only** on the **BTT HDMI5 v1.2** display.  
If you are using a different screen, you will need to adapt these steps to your hardware.

This is simple setup and assumes you are connecting screen to the same board as you will drive it from,
as it **requires common ground**. If you require more safety/different setup, you should use transistors,
but then you're on your own on how to connect that.

### 1. Identify the brightness buttons

On the **BTT HDMI5 v1.2**, the brightness buttons are labeled on the PCB as:

- Ks1 – brighten
- Ks3 – dim

These are standard tactile buttons connected to the display controller.

### 2. Identify the GND side of each button

Each button shorts its signal line to GND when pressed.

Use a multimeter in continuity mode:
- probe a known GND pad on the PCB
- probe both pads of the button
- the pad that shows continuity to GND is the ground side

You will be soldering to **the other pad** (the signal pad), not the GND pad.

### 3. Solder wires to the signal pads

Solder one wire each identified signal pad for Ks1 and Ks3 buttons:

Tips:
- jumper wires with a female Dupont connector on one side work well
- the other side can be cut and soldered directly
- make the wires long enough to comfortably reach the GPIO header

If you do not have suitable jumper wires, you can crimp your own Dupont connector on one end or do whatever, it's
your hardware ;).

### 4. Prepare a temporary GND for testing

If you want to test your magnificent solder job:
- take an additional jumper wire (male–male)
- plug one end into a GND pin/pad of your screen
- tape it down so it does not fall out

Your result should be looking somewhat like the image below.

![BTT HDMI5 PCB – soldered wires and test GND](/images/btt-img-1.jpg)

### 5. Test the wiring manually

Before connecting to GPIO:
- connect the display to HDMI and USB
- power on your printer
- briefly short the temporary GND wire with each soldered signal wire

It should behave like a button - brighten/dim screen accordingly

If this does not work, re-check your soldering.

### 6. Connect to GPIO

Once manual testing works, connect the wires to GPIO pins.

#### Important notes for **BTT CB2**: 
Do not use GPIO pins with `bias pull down`.

You can inspect pin bias with:
```bash
grep -R . /sys/kernel/debug/pinctrl/*/pinconf-pins | grep "bias pull up"
```

You only want to use those on CB2. Not all of them will be available on the GPIO header.
On my CB2+M8P I used GPIO pins `gpiochip0/11` and `gpiochip0/12`.
These pins are easy to spot, because they are marked in blue both [in the CB2 docs](https://global.bttwiki.com/CB2.html#40-pin-gpio) and on the board.

If, after connecting both pins, the screen behaves as if a button is constantly pressed
(brightness continuously increasing or decreasing), this is almost always caused by using
a GPIO with `bias-pull-down`.

On Raspberry Pi this usually does not matter, and any unused GPIO pin should work.

## Installation and configuration

### Installation

Run the following steps as the same user that runs Klipper / KlipperScreen
(the user must have sudo access):

```bash
git clone https://github.com/triluch/screen-brightnessd.git
cd screen-brightnessd
./install/install.sh
```

The installer installs required packages, udev rules for GPIO access, and the systemd service.

After the installer finishes, edit the configuration file:

```bash
~/printer_data/config/screen-brightnessd.ini
```

### Configuration

Only a few settings are essential.

In the `[gpio]` section, set:
- `chip` – GPIO chip name (for example `gpiochip0`)
- `line_dim` – GPIO line connected to the dim button
- `line_brighten` – GPIO line connected to the brighten button

These must match the wiring you did earlier.
You don't really have to edit rest of the config, but parameters are described in the comments there.


### Testing

You can test the wiring and configuration before rebooting using test mode.

Because GPIO permissions are not active yet, the test probably needs to be run with `sudo`:

```
sudo python3 main.py ~/printer_data/config/screen-brightnessd.ini test
```

Test mode just does `dim -> brighten -> dim -> brighten`.


After the test finishes, the screen should end up **brightened**.

If the screen ends up dimmed instead, swap `line_dim` and `line_brighten`
in the configuration file and test again.

### Final step

You should have everything set, now reboot the system (this is required for udev rules). After reboot:
- the service should start automatically
- screen brightness should react to DPMS state changes
- the `screen-brightnessd` service should be visible and manageable
  from the web UI (for example Mainsail)

## Contributing and maintenance

- If you want to change or improve the code, please open a pull request.
- If you tested this project on different hardware (other displays, boards, or platforms),
  please share the details so this information can be added to the documentation.
- Project is provided "as is", so if something breaks it's on you. Issues might or might not be dealt with.
