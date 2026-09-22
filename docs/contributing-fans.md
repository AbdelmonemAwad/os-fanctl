# Adding your appliance to fans.json

What is wanted here is a **measurement**, not a datasheet. A block in `fans.json` says "on this
model, this header turns this fan, and this is the duty at which that fan stops" — and the only way
to know the second half is to have watched it.

## What to send

One block, and the name of whoever watched it.

```json
"vendor-model": {
  "display": "Vendor Model 1234",
  "match": { "maker": "^Vendor$", "product": "^Model$", "version": "^1234" },
  "verify": {
    "chip": "^NCT6776F$",
    "hwm_base": "0x290",
    "channels": ["SYSFAN", "CPUFAN"]
  },
  "control": true,
  "fans": [
    {
      "channel": "SYSFAN",
      "label": "chassis fan, left",
      "sensors": ["cpu", "pch"],
      "floor": 35,
      "floor_source": "measured 2026-10-04: turning at 30%, stopped at 25%",
      "tach": "turning",
      "confirmed": "Vendor Model 1234, 2026-10-04, A. N. Other: held at 30% for 60 s, 620 rpm steady; stopped at 25%; restored to SmartFan IV and the chip took it back within a cycle."
    }
  ],
  "curve": { "points": "40:50,50:70,60:90,70:100", "limit_c": 70, "panic_c": 85 }
}
```

`match` is regular expressions against `kenv smbios.planar.*` (falling back to `smbios.system.*`).
`verify` is what must actually be underneath: a block that matched by SMBIOS but fails `verify`
drives nothing, which is what stops an entry written for one revision of a board from being applied
to another wearing the same strings.

**`control: true` only with a `confirmed` line.** An entry without one is a guess, and a guess here
has a fan behind it. If you are not sure, send it with `control: false` and the readings — that is
still worth having: it names the hardware, and the next person starts from something.

## How to get the numbers

1. **System → Fan Control → Hardware → Refresh.** Send the chip panel as it stands: chip, device id,
   configuration port, monitoring window, and whether it can be written to.
2. **Note which channels report a turning fan.** A channel reading zero is not a stopped fan and must
   not be described as one — it may have no fan, or a fan with no tach wire, or a fan on a header
   this chip does not sample.
3. **Be in front of the machine.** Not in another room, not over ssh from a train.
4. **Measure the floor, one channel at a time**, with the *Measure the floor* button: start at 60%,
   then 50, 45, 40, 35, 30, and stop when the fan stops. The test writes the duty, watches the
   tachometer, ends the moment the fan stops, and hands the fan back in every case. The floor it
   records is one step above the lowest duty that kept the fan turning.
5. **Watch what the fan does when it is handed back.** If the channel was under a firmware curve,
   the chip should be visibly back in charge within a cycle. If it is not, say so — that is the most
   important sentence you could send, and it belongs in `confirmed`.
6. **Then, if you want a curve**, run the machine under real load with it and say what the
   temperatures did. A curve nobody has loaded is a proposal; say which yours is.

## What not to do

- Do not copy a floor from another model, however similar the chassis looks.
- Do not measure a floor on a machine carrying traffic somebody depends on.
- Do not send a `confirmed` line that describes what you expected. It is the field that separates
  this table from a wiki page, and the only thing that keeps it worth trusting is that every line in
  it was written by somebody who looked.
