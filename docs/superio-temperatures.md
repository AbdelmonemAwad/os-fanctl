# The Super I/O temperature inputs on this appliance

What was built, what it returned, and which of the readings it produces this
plugin is allowed to vouch for.

Every claim below is marked **V** (run on this machine and quoted), **D**
(quoted from a primary source, with a file and a line) or **R** (reported, or
reasoned, and not measured here). An unmarked sentence is not a fact.

Machine: ex-Sophos XG 330 rev 2, OPNsense 26.7.4_1 / FreeBSD 15.1-RELEASE-p3,
Intel i5-6500, Nuvoton NCT6779D (id 0xc562) at window 0xa30 - index port 0xa35,
data port 0xa36 (**V**, `fanctl-io identify`, 2026-09-22).

---

## 1. What was built

| File | What changed |
|---|---|
| `fanctl-io.c` | a seventh command, `temp [<n>]`, which only reads |
| `chip.py` | `Chip.temps()`, classifying exit codes through the same `_run()` as `tach()` and `status()` |
| `sensors.py` | `read_superio()` wired to it; one sensor per SOURCE rather than per register; `suggested_groups()` now wants a board input to have moved |

**One correction was made to this work after it was first delivered, and it was
in the one rule everything else here rests on.** The silent-source test
compared the value register against `0xffff` for the all-ones pattern.
`ec_read_value()` left-aligns a BYTE register into that word, so a byte holding
`0xff` arrives as `0xff00` and can never equal `0xffff`: the test was dead code
on byte-sized registers, and on this board the byte register 0x027 is one of
the two assigned to a PECI agent. `0xff00` decodes to **-1.0 C**, which is
inside the plausible band, so that row came out of the helper as a READING -
`PECI Agent 0, attached cpu_die, -1.0 C`, with no `unreadable` on it at all -
and `read_superio()` published it as a sensor, `decide('board')` returned it,
and `suggested_groups()` offered `board` for it as `identified_board_input`
(**V**, 2026-09-22, a test build of this file with `0xff00` forced into that
register's snapshot after the reads and before the decode; the word register
holding `0xffff` was refused correctly the whole time). The test is now written
in the register's own width, `in->word ? 0xffff : 0xff00`, and the same test
build is refused as `source-silent` with `decoded_c -1.0` beside it as evidence
(**V**). The real table is unchanged by the correction (**V**, re-read on the
chip afterwards: rows 1-3 `source-silent`, rows 4-7 SYSTIN 47.0 C).

**Two smaller corrections went in with it, both found by looking for the same
mistake somewhere else.** The left-alignment that hid the all-ones pattern
leaks into the answer in one other place: `raw_first_read`, printed only on an
unstable row, was formatted `0x%04x` for every register, so a byte register
would have shown `raw 0xff` beside `raw_first_read 0xff00` - two hex strings of
different widths in one row, describing the same byte. It is now printed in the
register's own width (**V**, 2026-09-22, a test build that alternates a
register's value between two bytes on successive reads: byte register 0x027
gives `raw 0x30, raw_first_read 0x2f`, word register 0x150 gives `raw 0x3000,
raw_first_read 0x2f00`, both `unstable`, both with no `celsius` and no
`decoded_c`).

And `read_superio()` was throwing away the helper's `src_known`. That flag is
the CHIP's own mask - whether this chip has the source number at all -
while `NCT_TEMP_LABEL` in `sensors.py` holds the whole family's names,
including `31: Virtual_TEMP`, which is **not** in `nct6779_temp_label` and has
its mask bit clear on this chip. So a source register holding 31 produced a row
the helper correctly refused to name (`src_known false`, `src_name null`) and
`sensors.py` then named anyway: `superio Virtual_TEMP`, `named_by_driver true`,
eligible for `decide('board')` - a sensor with a driver's name on a chip that
has no such source, which is the row the helper's chip-by-chip refusal exists
to prevent (**V**, 2026-09-22, source byte forced to 31 through a test build).
`read_superio()` now drops the name when - and only when - the helper says
`src_known` is explicitly false, and that row arrives as `superio source 31`,
`named_by_driver false`, keyed `superio:src31` (**V**). An older helper that
does not send the key is unaffected, and on this appliance all seven rows are
`src_known true`, so the branch does not run here and the real end-to-end
answer is byte-for-byte what it was (**V**).

`temp` needed no new port code. `ec_select()`, `ec_read_raw()`, `ec_read()` and
`ec_read16()` were already in the helper and are unchanged. What was added is a
register table, a decode, and one reader - `ec_read_value()` - that applies
`ec_read`'s read-it-twice discipline to a value register without `ec_read`'s
death on a disagreement, because a temperature may legitimately change between
two reads and losing six good rows to one flickering one is the worse answer.

**Nothing was installed and nothing was written.** The binary was built and run
from `/root/fanctl-temps` on the firewall; `/usr/local` was not touched, no
service was restarted, and `duty`, `release`, `set` and `test` were not run at
any point, on hardware or through configd. `temp` takes the same non-blocking
lock (`/var/run/fanctl-io.lock`) as every other command, so it serialises
against the running daemon exactly as `status` does (**V**).

The port accesses `temp` makes are the four `status` already makes, against
different register numbers: two writes that are an address latch - the bank
selector 0x4e, then the index - and a read of the data port (**D**,
nct6775-platform.c:316-341 for the sequence; **V** for the port numbers). None
of them is a setting.

## 2. The machine's fan state, before and after

Unchanged, and checked rather than assumed (**V**, 2026-09-22, read-only):

```
channel 1 (SYSFAN)  mode 0, mode_name "off", duty_raw 255 = 100%, 5133 rpm
                    saved_original true, saved_mode 4, saved_duty_raw 50
/var/db/fanctl/ch1.state   "4 50 c562"   SmartFan IV, raw 50 (20%), this chip
daemon                     running, manual 100% chosen on the settings page
```

The fans are at a manual 100% and that is where this work found them and where
it left them. Writing 100% again would have been a write for no reason: the
daemon owns the channel and is already holding it there, and the one number
that would have changed if anything had gone wrong - `saved_duty_raw 50` - is
still 50.

## 3. What the chip has, and what it said

The NCT6779D has seven temperature VALUE registers - two that also carry limits
and five that the fan control laws read - and for each one a separate register
saying which SOURCE the chip has put in it (**D**, nct6775-core.c:377-380 and
169-172, via the citation block in `sensors.py`). Seven registers is not seven
sensors: several of them can be pointed at the same source, and on this board
they are.

Raw bytes, read under the lock while the daemon ran, identical across 52
samples over 766 s unless noted (**V**, 2026-09-22):

| # | value reg | source reg | source byte | source | config reg | raw value | decodes to | verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | 0x027 byte | 0x621 | `0x10` | 16 PECI Agent 0 | 0x018 = `0x40`, enabled | `0x00` | 0.0 C | **source-silent** |
| 2 | 0x150 word | 0x622 | `0x11` | 17 PECI Agent 1 | 0x152 = `0x00`, enabled | `0x0000` | 0.0 C | **source-silent** |
| 3 | 0x073 word | 0x100 | `0x90` | 16 PECI Agent 0 | none | `0x0000` | 0.0 C | **source-silent** |
| 4 | 0x075 word | 0x200 | `0x81` | 1 SYSTIN | none | `0x2f00` / `0x2f80` | 47.0 / 47.5 C | **a reading** |
| 5 | 0x077 word | 0x300 | `0x81` | 1 SYSTIN | none | `0x2f00` / `0x2f80` | 47.0 / 47.5 C | **a reading** |
| 6 | 0x079 word | 0x800 | `0x81` | 1 SYSTIN | none | `0x2f00` / `0x2f80` | 47.0 / 47.5 C | **a reading** |
| 7 | 0x07b word | 0x900 | `0x01` | 1 SYSTIN | none | `0x2f00` / `0x2f80` | 47.0 / 47.5 C | **a reading** |

Chip-wide bytes: `vbat 0x41`, `diode 0x00`, diode mask `0x02`. The temperature
offset register for SYSTIN, 0x454, reads `0x00`: the firmware calibrated
nothing (**V**).

The decode is LM75's - a signed 9-bit value in the top of the word, half a
degree per step, `((s16)reg / 128) * 500` millidegrees (**D**,
drivers/hwmon/lm75.h). A byte register is left-aligned into the same word
(**D**, nct6775-core.c:1211-1223), which is why row 1 has whole-degree
resolution and the rest have half. Worked through: `0x2f80` = 12160, / 128 =
95, x 0.5 = 47.5 C.

Two bytes are reported and deliberately not interpreted. The source bytes carry
a bit above the source number - `0x90` and `0x81` have bit 7 set, `0x10` and
`0x01` do not - and nothing here knows what it means. The whole byte is in the
answer as `src_raw`, so that a board doing something with it stays visible
rather than being masked away (**R**).

**Seven registers, three sources, one live sensor.**

## 4. Which inputs are connected to something real

This is the part that matters, and the honest answer is short.

### PECI Agent 0 and PECI Agent 1 - NOT REPORTING

Both are enabled in the chip and both hold zero. What settles it is not the
value; it is the value beside something that moved (**V**, 52 samples over
766 s, 2026-09-22, the daemon running and the fan at 100% throughout):

```
dev.cpu.0-3.temperature   54.0 C .. 80.0 C   swing 26.0 C, 19 distinct values
register 0x027 (PECI 0)    0x00 .. 0x00      swing  0.0 C,   1 distinct value
register 0x073 (PECI 0)  0x0000 .. 0x0000    swing  0.0 C,   1 distinct value
register 0x150 (PECI 1)  0x0000 .. 0x0000    swing  0.0 C,   1 distinct value
```

A PECI agent is the CPU reporting its own die over its own bus. This CPU's die
moved twenty-six degrees in under thirteen minutes and these registers did
not move at all. Whatever they are, they are not this CPU's temperature.

Which of "PECI is not wired on this board", "the firmware never enabled the
chip's PECI master" and "the master asks and nothing answers" is true cannot be
told apart from software, and operationally it does not matter: there is no
reading here (**R** for the causes, **V** for the absence).

This is the most dangerous row this command could have produced. All zeroes
decodes to a perfectly plausible **0.0 C**, and the chip itself says the source
is the CPU die - so "PECI Agent 0, cpu_die, 0.0 C" would have been
authoritative, cold and wrong, and a curve following it would hold a fan at its
minimum for ever. The helper therefore refuses a forwarded source sitting at
its reset value: `celsius` is null, `unreadable` is `source-silent`, and what
the bytes would have said is printed beside it as `decoded_c`, which is
evidence for checking the refusal and is never a reading.

The reset value is `0x0000` or all ones **in the register's own width** - and
that second half was wrong in the first version of this file, which is written
up in section 1 and again in section 6. A byte register holding `0xff` decodes
to -1.0 C, not to 0.0, and -1.0 C is a number a curve will happily follow. On
this board the all-zeroes half is what is measured; the all-ones half is still
reasoning about a bus that does not answer (**R**), but it now applies to both
widths (**V**).

A PIN reading zero is deliberately not caught by that rule. A pin can honestly
be cold and nothing about a pin has a reset value; the layer above convicts a
reading that never moves with half an hour of evidence instead.

### SYSTIN - A READING, AND NOBODY KNOWS WHAT IT IS TOUCHING

It is a real measurement in the sense that matters to a fan curve: it answers,
it is plausible, and it is not frozen. It moved between 47.0 and 47.5 during
the window (**V**) - one step, which is this input's whole resolution, so the
liveness evidence is the weakest kind that still counts.

What it is not: it is not the CPU. Across the same window the cores swung 26 C
while SYSTIN moved 0.5 C, and the two were up to 32.5 C apart (**V**). Anything
that fed SYSTIN to a curve expecting to protect the CPU would be following the
wrong thing by twenty degrees and several minutes.

What it might be: it tracks `dev.pchtherm.0.temperature` closely - equal in 45
of 52 samples and never more than one step apart (**V**). That is *suggestive*
and it is not proof, and the reason is in the data: SYSTIN took two distinct
values in the whole window and the PCH took three, so "they agreed" is a much
weaker statement than it looks. Two slow sensors in the same lump of metal
agree with each other, and with a thermometer left on a shelf.

**`attached` stays `unknown`, and must stay `unknown` on the page.** SYSTIN is
a pin. What sits near that pin was decided by whoever laid this board out, and
no register on this chip records it. The chip reports what a pin is CONFIGURED
as; it does not report what the far end of the wire touches, and no software on
this machine or any other can find that out.

The two things that would settle it are not software: warm one part of the
chassis at a time and watch which input moves, or read the board vendor's
documentation - and the vendor is Sophos, the appliance is repurposed, and that
document is not coming.

### CPUTIN and AUXTIN0-3 - NOT READABLE HERE AT ALL

The chip has six temperature pins. Only one of them, SYSTIN, is routed into a
value register on this board: the seven source registers hold sources 16, 17
and 1 and nothing else (**V**).

Pointing a value register at AUXTIN1 to see what is on it means WRITING a
source-selection register on the chip that is at this moment the only thing
controlling the fan cooling this machine. This plugin does not make that write
and `temp` has no argument that could ask for it. So whether the input near the
power supply - whose fan the owner also replaced, and for which no sensor
exists anywhere else in this machine - has a thermistor on it stays an open
question, honestly, rather than being answered with a number.

### Summary

| Source | Reading | Connected to something real? | How I can tell |
|---|---|---|---|
| PECI Agent 0 | none | **No** | zero across a 26 C core swing, 52 samples (V) |
| PECI Agent 1 | none | **No** | same (V) |
| SYSTIN | 47.0-47.5 C | **Yes - and to WHAT is unknown** | plausible, moved one step, tracks the PCH within its resolution, up to 32.5 C away from the CPU (V) |
| CPUTIN, AUXTIN0-3 | none | **Unknown, and unreadable** | no value register is assigned to them, and assigning one is a write (V) |

## 5. The thing this read answered that nobody was looking for

`DESIGN.md` section 8 leaves open which temperature the board's own fan law
follows. The source registers answer it with a read (**V**):

```
0x100  fan channel 1  0x90 -> source 16, PECI Agent 0   (silent)
0x200  fan channel 2  0x81 -> source  1, SYSTIN
0x300  fan channel 3  0x81 -> source  1, SYSTIN
0x800  fan channel 4  0x81 -> source  1, SYSTIN
0x900  fan channel 5  0x01 -> source  1, SYSTIN
```

Channel 1 is the only channel with a fan on it. Its saved original is SmartFan
IV at raw 50, which is 20% (**V**, `/var/db/fanctl/ch1.state` = `4 50 c562`).
The temperature that law watches is a PECI agent that has never reported
anything.

So the vendor's curve was not merely tuned for the louder fan the appliance
shipped with. **It was a curve with a dead sensor on its input**, which is why
it asked for 20% with the cores at 80 C - and why it would have asked for 20%
at any temperature whatsoever. That is a stronger justification for this plugin
than the one written in the design document, and it has a consequence the page
should say out loud: `release` on channel 1 hands this fan back to a control
law that cannot respond to heat.

It also completes the other half of the same story. This board's DSDT declares
five ACPI fans and five trip points on a thermal zone that is a hardcoded
constant (recorded in `sensors.py`'s header), so nothing in ACPI could ever
have cooled this machine either. Both of the firmware's cooling paths on this
appliance are wired to sensors that do not report. Neither was broken by
anything; they were delivered like that.

## 6. What was verified, and what was not

Verified on the machine (**V**, 2026-09-22):

* the seven registers, the three sources and the decode above: 52 samples over
  766 s beside the kernel's own sensors, with the daemon running throughout;
* `temp` and `temp <n>`, and that `temp <n>` reads the whole table and prints
  one row - the cross-reference in `selected_by` needs every source register;
* **busy**, exit 9, with a private lock file so that nothing near the daemon
  was disturbed;
* **no-device**, exit 2, with a build pointed at a device path that does not
  exist: it refuses before any port is touched;
* **no temperature map**, exit 4, with a test build whose map carries a
  different device id, run against the real chip;
* **usage**, exit 1, for `temp 9`, `temp 1 2` and `temp x`;
* 52 reads at 15-second intervals alongside the daemon's own 10-second
  `status` calls produced **zero** busy refusals, and 364 value-register reads
  - each one a pair that had to agree - produced **zero** unstable rows;
* `chip.py` against an older helper: `fanctl-io temp` on the installed 1.1
  binary exits 1, and `sensors.read_superio()` reports `no-helper-command`
  rather than raising;
* `read_superio()` against a synthesised helper answer this board cannot
  produce - an unassigned register, a disabled input, an implausible value, an
  unstable read, and two registers holding one source where only the second is
  readable - all classified correctly, plus every refusal slug (`busy`,
  `chip`, `no-device`, `no-permission`, `no-helper-command`, `error`,
  `no-passthrough`);
* end to end: `sensors.read(include_superio=True)` returns 10 sensors of which
  8 are readings; `decide('board')` returns 47.5 C from SYSTIN and never 0.0 C
  from a PECI agent; `suggested_groups()` does not offer `board` at all,
  because SYSTIN's attachment is unknown.

Re-verified independently after the corrections, on a build made from the
delivered source in a second scratch directory (`/root/fanctl-verify`, **V**,
2026-09-22, daemon running throughout):

* the whole table, identical to the one above, register for register and byte
  for byte;
* the decode across its range, by substituting a register's byte and reading
  the row back: `0x2f80 -> 47.5 C`, `0x2f00 -> 47.0 C`, `0x0080 -> 0.5 C`,
  `0xff80 -> -0.5 C`, `0xff00 -> -1.0 C`, `0x7f (byte) -> 127.0 C`. The
  sign is taken as `int16` and the division is Linux's own `/128`, not a right
  shift: `0xffc0` reads **0.0 C** here, where a shift would give -0.5 C
  (**V**) - which is the behaviour the code documents and, being a truncation
  towards zero, the one `LM75_TEMP_FROM_REG` produces;
* the plausibility window, at both edges of the byte register: `0xd8` is
  -40.0 C and is a reading, `0xd7` is -41.0 C and is refused as `implausible`
  with `decoded_c -41.0` beside it;
* every refusal the value can earn, on the real chip: `0x00` and the
  width-correct all-ones both `source-silent`, a config register with bit 0
  set `disabled-in-chip`, a source register of 0 `unassigned`, a value that
  disagrees with itself `unstable`;
* that a PIN is still allowed to be cold, which is the deliberate hole in that
  rule: with the source byte forced to 3 (AUXTIN0) the same `0x00` and `0xff`
  come out as 0.0 C and -1.0 C and are left to the half-hour `constant`
  machinery upstairs;
* the PECI-versus-kernel comparison, taken again and on a hotter machine:
  `dev.cpu.0-3.temperature` read 51-53 C, then 59-64 C, then 72-74 C in
  successive runs while all three PECI-assigned registers held 0x00/0x0000 and
  `dev.pchtherm.0.temperature` and SYSTIN both held 47.0 C. The two PECI
  agents do not move with the die they claim to be;
* the refusal paths, again, each with a private lock file or a private device
  path so nothing near the daemon was disturbed: busy 9, no-device 2, usage 1
  for `temp 9`, `temp 0`, `temp x` and `temp 1 2` (which prints its JSON on
  stdout and its usage text on stderr, and `chip.py` classifies as `usage`);
* the bank is deliberately NOT restored, and that is safe here rather than
  merely intended: `ec_select()` re-selects the bank before every index write
  because ncthwm(4) moves it, and `dev.ncthwm.0.SYSFAN` read 5113 rpm -
  agreeing with the helper's own tachometer for channel 1 - immediately after
  a `temp` run, five times running (**V**);
* the build, twice: `WARNS=6` through `bsd.prog.mk` as the plugin builds it,
  and again with `-Wall -Wextra -Werror -Wconversion -Wsign-conversion
  -Wshadow -Wcast-qual -Wwrite-strings`. Clean both ways.
* fan state afterwards, read-only and unchanged: channel 1 `duty_raw 255` =
  100%, 5113 rpm, `/var/db/fanctl/ch1.state` still `4 50 c562`, daemon still
  pid 76967 and never restarted.

Not verified, and marked as such:

* **the Linux line numbers.** The register numbers, the label array, the source
  mask and the decode are carried from the citation block already in
  `sensors.py` (**D** there: nct6775-core.c at commit 54743b5ab981). The kernel
  tree is not on this appliance and those lines were not re-opened against it
  here. What was done instead is the check that matters: the decode was run on
  the chip, and it produces 47.5 C from SYSTIN and refuses zeros from PECI.
* **the offset register mapping** (**R**). The offset array is quoted; that its
  index *i* is source *i+1* is this program's reading of the driver. It is
  reported with its register number beside it and nothing acts on it, so a
  wrong mapping there mislabels a byte and cannot move a temperature.
* **the diode bit** (**R**). The mask says only CPUTIN can be a diode on this
  chip, so every other pin is a thermistor and that is a property of the chip
  rather than a reading. For the one input that could be either, the helper
  prints the DIODE register byte and says `unsettled` rather than guessing at
  the expression that decodes it. On this board CPUTIN is not selected anyway.
* **the all-ones half of the silent-source rule** (**R**). All-zeroes was
  measured. All-ones is reasoning about a bus that does not answer - and the
  first version of this file got the reasoning right and the ARITHMETIC wrong,
  testing a left-aligned byte register against `0xffff`, which it can never
  hold. That is corrected and both widths are now exercised through a forced
  value on the real code path (**V**, section 1). It is worth saying why it
  survived the first round: every refusal in this command was checked against
  what this board actually produces, and this board produces all-zeroes. A rule
  written for a case the machine cannot show you is a rule nothing tests, and
  the only defence is to force the value through the real code path - which is
  what was done second and should have been done first.
* **two registers holding one source that disagree** (**R**). `read_superio()`
  merges them into one sensor and keeps the first register that carried a
  reading; a hotter one behind it stays in `registers` and is not the number
  the sensor publishes. Nothing was seen to disagree here (364 reads, all four
  SYSTIN registers identical), and on this chip the mirrors are one
  measurement, so this is a shape to watch rather than a fault to point at -
  but it is the one place in this path that does not take the hottest, which is
  the rule `decide()` follows everywhere else.
* **`unstable`** (**R** on real hardware). No read of these registers ever
  disagreed with itself here - 364 pairs, then more. The path itself is not
  untested: it has since been driven through the real code path by a build that
  alternates a register's value between two bytes on successive reads, in both
  widths, and it refuses the row and carries both reads (**V**, section 1).
  What is unverified is that a real collision in this window ever produces it.
* **which registers are byte-sized** (**D**-by-carriage, and untestable here).
  That 0x027 is a byte and the other six are words comes from
  `nct6775_reg_is_word_sized()` through the citation block, not from a line
  re-opened against the kernel tree. It cannot be settled on this board by
  reading, because 0x027 is assigned to a PECI agent and holds zero: zero
  decodes to 0.0 C whether it is read as one byte or two, so the board offers
  no evidence either way. The six word-sized registers ARE settled by reading -
  `0x2f00`/`0x2f80` decode to 47.0/47.5 C and track `dev.pchtherm.0` step for
  step, which a byte read of the same address could not produce (**V**). What
  is left is bounded rather than open: with the all-ones test now written in
  both widths, both reset patterns are refused whichever width 0x027 turns out
  to be, and a genuine reading taken at the wrong width would be out by the
  fractional bit - half a degree, this register's own resolution - and not by a
  wrong number.
* **every chip except this one.** `temp` refuses the other eight chips in the
  family by name. They share fan registers; they do not share temperature label
  tables or source masks, and a row that names the wrong sensor is how a fan
  curve ends up following something nobody checked.

## 7. What a page must say, if it shows any of this

1. **`attached: unknown` is a sentence, not a blank.** Beside SYSTIN it means
   "this is a real reading and nobody knows what it is measuring", which is
   exactly what it is.
2. **A silent source is not 0 C.** A row with `unreadable: source-silent` must
   never render a number. `decoded_c` is for a maintainer checking a refusal.
3. **One sensor, not four.** `read_superio()` merges the four registers holding
   SYSTIN into one sensor and lists the registers underneath; a page that
   iterated the helper's rows directly would show four sensors where the board
   has one, and count one thermistor four times.
4. **Channel 1's fan law follows a dead sensor.** Whatever else the page says
   about handing the fan back to the firmware, it should say that.
5. **No board input is a curve default on this machine.** Nothing offers one
   today: `suggested_groups()` will not suggest a `board` input whose
   attachment is unknown, and now will not suggest one that has never moved
   either.

## 8. The one thing still not wired

`fanctl.py` calls `sensors.read()` without `include_superio`, so the daemon and
the status file do not carry these inputs yet (**V**: the only caller that asks
for them today is `sensors.py`'s own command line). That is one argument, and
it is deliberately not changed here: turning it on puts a helper run inside the
ten-second fan loop, which is a decision about the loop rather than about this
command, and on this appliance what it would add to the page is one thermistor
whose attachment is unknown and two dead PECI agents. A machine whose only
honest sensor is on the Super I/O - which is a real machine, just not this one
- should turn it on deliberately, where somebody can see the decision was made.
