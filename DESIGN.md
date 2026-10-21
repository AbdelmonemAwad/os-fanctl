# os-fanctl — design contract

Gives a repurposed appliance back the fan curve that went away with the vendor's firmware, and is
arranged so that it can never be the reason a fan stops.

Claims are marked **V** (verified on the reference machine), **D** (documented in a primary source,
quoted) or **R** (reported). Nothing unmarked is a fact.

A **V** sometimes names a path under `/root/` — `/root/fanwork2/adv_test.py` and its neighbours.
Those are the throwaway harnesses the measurement was taken with, on the machine this was written
on, and they are deliberately **not** in this repository: a test that needs one particular Super
I/O in one particular chassis is not a test anybody else can run, and shipping it would suggest
otherwise. The path is there so the measurement has an address rather than being "I checked" — it
is a citation, not an instruction. What *is* shipped and *can* be run anywhere is
`history.py selftest`, which is 29 checks over the record and the statistics and needs no
hardware at all.

---

## 1. Why this exists

Somebody buys a decommissioned appliance, installs OPNsense, and the fans run at full speed for
ever — because the fan curve lived in the vendor's firmware and the vendor's firmware is gone.
Nothing in the OPNsense plugin repository touches fans; the nearest entries are `os-dec-hw`
(Deciso's own hardware), `os-dmidecode` and `os-hw-probe` (**R**, from the published plugin list).

So this is written to be **general**, not to be one appliance's fix: the reading half works
anywhere, the hardware table is the thing that grows, and the reference machine is one row in it.

## 2. What the hardware allows, and what it does not

The reference machine is a Sophos XG 330 rev 2 on OPNsense 26.7.4_1 / FreeBSD 15.1-RELEASE-p3:
`smbios.planar.maker="Sophos"`, `smbios.planar.product="XG"`, `smbios.planar.version="330r2"` (**V**).

| Question | Answer |
|---|---|
| What chip | `superio0: <Nuvoton NCT6779D> at port 0x2e-0x2f on isa0` (**V**) |
| What reads it today | `ncthwm0`, five tach channels: `SYSFAN` 935–954 rpm over 90 samples, the other four 0 (**V**) |
| Can `ncthwm` set anything | No. Five sysctls, every one `CTLFLAG_RD`, one handler, no `device_ioctl`, no `make_dev` (**D**, `sys/dev/ncthwm/ncthwm.c`); `sysctl -aW \| grep ncthwm` returns nothing (**V**) |
| Any other in-tree path | No. `aibs` is read-only (**D**); `pwmc`/`pwmbus` attach only to a `pwmbus` provider, which on x86 is nobody, and `/dev/pwm*` is absent (**D**+**V**); there is no `it87`/`f71882`/`adt7470` equivalent among the 844 modules in `/boot/kernel` (**V**) |
| `/dev/superio0` | Exists, `crw------- root wheel`, and has a `SUPERIO_CR_WRITE` ioctl (**D**, `superio.c:1058`) — but it addresses per-LDN **configuration registers**, not the HWM bank space where a duty lives. A half-open door, not an open one |
| So the only writable path | Raw port I/O through `/dev/io`, at the EC window the driver publishes at `0xa35`/`0xa36` (**V**: a reader containing no write primitive read `0xAD` there while the sysctl reported 941 = `0x03AD`) |
| Is there a BMC | No. `/dev/ipmi*` does not exist (**V**) |

**And the fact the whole design turns on** (**D**): `ncthwm_query_fan_speed()` takes no lock at all —
there is no mutex in `struct ncthwm_softc`. Two `sysctl` readers already race each other inside the
kernel. A userland sequence through `/dev/io` races the kernel with **nothing to serialise against**:
if the kernel's bank select lands between our index write and our data write, our value goes into
whichever register the kernel had just selected.

That cannot be fixed from userland. It can only be made small, and three decisions in this plugin
exist for that and nothing else:

* duties are quantised and rounded up, so an ordinary cycle writes **nothing at all**;
* one write per change, never a write per cycle;
* **every write is read back**, and a read-back that disagrees hands the fans to the hardware. It
  does not retry — a disagreement means a write went somewhere, and the honest response to that is
  to stop writing. The one exception is the duty byte while the channel is under an automatic
  mode, written on the way into manual and on the way back out of it: some chips show the curve's
  own output in that register then, so it would never read back (issue 3). The mode beside it is
  read back every time, and so is the duty once the channel is manual.

## 3. Three layers, and this document is about the top one

| Layer | What it is | What it may do |
|---|---|---|
| `fanctl-io` | a small C program, built on the firewall by the firewall's own clang | the only thing that touches an I/O port. Holds a non-blocking lock over the window, does a handful of accesses, exits |
| `chip.py` | the only caller of that program | speaks fans, not registers: identify, status, tach, duty, release. Chooses between the helper and the read-only sysctl path, and refuses to use both at once |
| everything else | the contract, the curve, the sensors, the daemon, the page, the packaging | never names a register, never opens a port |

What this layer uses, and nothing else: `chip.read_path()`, and on a `chip.Chip()`
`channel_status`, `tach`, `set_duty`, `release`, `release_all`, plus `chip.ChipError` and its
kinds. Three properties of that surface shaped this layer, and each of them replaced code
written against an assumed one:

* **Channels are numbered**, from 1. `SYSFAN`, `CPUFAN`, `AUXFAN0` are `ncthwm(4)`'s labels for
  the same five registers, carried as aliases and never as facts. This layer keeps the name as
  the key a person, a settings page and `fans.json` use, and translates to a number at the last
  moment, in one place. A chip with no alias table gets `FAN1..FANn`.
* **The floor is not this layer's alone** — see §6.
* **Restore belongs to the helper.** `release(n)` writes back the mode and duty the channel had
  before this plugin first touched it, from a record the helper wrote at that moment; where it
  has no record it falls back to the chip's own PWM-off, which is full output. So the baseline
  this layer keeps is a record for the page and for the contract, not the mechanism. That is the
  right way round: the thing that can undo a write is the thing that made it.

**One rule crosses the boundary and this layer obeys it.** `ncthwm`'s sysctls walk the same two
ports as the helper, with no lock, so `chip.py` refuses to read them while the helper is usable.
This layer therefore has exactly one sysctl-tachometer fallback and it runs **only when `chip.py`
cannot be imported at all**. When `chip.py` is present, whatever it returned is the answer —
including when the answer is that the chip could not be read. A second, cheerful fallback here
would undo that decision from above, which is why the function that does it says so in its own
docstring rather than in a comment somewhere else.

The split is not tidiness. It means the whole of the safety logic can be exercised against a
faked helper on a machine with no fan controller in it, which is how the failure paths in §5 were
tested rather than reasoned about — and after the real `chip.py` landed, the same harness was
re-pointed at it with only the helper process faked, which is what caught the field names this
layer had guessed wrong.

With no `chip.py` at all, this layer reads tachometers through `dev.ncthwm.*` and temperatures
through the ordinary sysctl tree, says in as many words that it cannot write, and the page is
still full of real readings (**V**, 2026-09-22). **That half works on any appliance**, including
one nobody has ever opened.

## 4. The safety contract

Six conditions. Nothing is written unless all six hold, and they are re-evaluated **before every
decision**, because four of them can stop being true while the daemon runs. They are named
identically in `fans.json`, in `fanctl.py` and on the page, so the three cannot drift.

| | Condition | Why it can fail mid-run |
|---|---|---|
| 1 | **fingerprint** — the chip is the one the matched table entry describes, and it can be written to | a chip that starts answering differently is not the chip in the table |
| 2 | **permission** — the entry permits control, or the owner has accepted an unmeasured entry in writing | — |
| 3 | **baseline** — what each channel was under before this plugin touched it has been captured | the state file can be removed under a running daemon |
| 4 | **floor** — every driven channel has a floor that was *measured*, or the mode is a fixed 100% | a measurement can be superseded by a later one that stalled |
| 5 | **tach** — every driven channel reports a fan that is turning | a fan can stop |
| 6 | **sensors** — every temperature the decision needs is readable right now | a sensor can vanish |

The baseline is captured **before** the contract is judged, not after, because otherwise condition 3
could never become true. Capturing is a read; it writes nothing.

A baseline is about the **mode**, not the duty. If the chip was under SmartFan IV, its duty was a
moving number the chip recomputes for itself the instant that law is back in charge — which is why
an hour-old baseline is still exact where it matters, and why `restore` means "write back what was
found" and never "set a duty that looks safe".

## 5. Every way out ends in a restore

Exercised against a fake chip, 2026-09-22 (**V** for the behaviour of this code; the fake chip is
not hardware):

| Path | What it is for | Observed |
|---|---|---|
| clean stop / `SIGTERM` | the service being stopped, and the shutdown sequence | restore, exit 0 |
| an unhandled exception | anything unforeseen | restore, exit 1 |
| a sensor stops answering | no honest duty can be inferred | never took control; `sensors` line failed |
| a read-back disagrees | something else is writing to this chip | `75% was written and 12% read back` → restore, exit 1 |
| the helper refuses | its own floor, its own read-back check, its own busy lock | the refusal is shown in the helper's words; nothing is retried behind it |
| a driven fan stalls | the floor is wrong | `stopped turning at 75%, which is above its floor of 40%` → restore, exit 1 |
| above the panic point | this plugin is not what is keeping the machine cool | `reached 90.0 C, above the panic point of 85 C` → restore, exit 1 |
| the watchdog | the loop stopped going round at all | restore inside the `SIGALRM` handler, then `os._exit` |
| the rc `stop` | a daemon that was killed never ran its own path | `stop` always ends with a `restore`, running or not |
| package removal | the rc script and the Python are packaged files | `pre-deinstall` stops and restores **before** the files go |

None of these retries into a loop. A daemon that took the fans back every ten seconds to fail the
same way would be a fan oscillating between two control laws all night.

`SIGALRM` restores inside the handler and then calls `os._exit`, skipping every `finally` on the
way out. Deliberate: a process that has just proven it cannot finish a loop must not be trusted to
unwind an exception through code it is already stuck in. The fans are back before `_exit` is
reached, which is the whole of what has to be true.

**When a restore itself fails** there is nothing left this plugin can do, so it says so instead of
hiding it: an incident file is written, the log gets a `LOG_CRIT` line, the rc `status` line says
it, the page shows the only red alert it has, and all four say the same thing — *power the
appliance off and on; a warm reboot does not necessarily clear a fan register*.

**And where this layer's own record of what it was driving has gone** — a state file removed under
a running controller — the release still goes ahead, because the record that matters is the
helper's. The answer then says whose record decided, which is the honest version of a sentence an
earlier draft got wrong: it refused, wrote an incident, and left the fan held.

## 6. The floor is measured or it does not exist

The floor is the lowest duty at which a fan that is **already turning keeps turning**, measured on
that machine, plus one step of margin.

Three sources, in this order, and the order is the design:

1. a floor measured on **this** machine with `fanctl.py test`;
2. a floor the table carries **with a confirmed line behind it**;
3. nothing — and then that channel is not driven, whatever is on the settings page.

**The settings page is not a third source.** `floor_duty` can only *raise* the floor above a
measured one. A number typed into a web page is not a measurement, and this is the one place in the
plugin where that distinction decides whether a fan turns.

**And there is one duty that needs no floor at all.** 100% cannot stop a fan: it is the chip's own
full output, the value it writes with PWM switched off, and it is above every floor that could ever
be measured, including the helper's hard 30%. So a configuration whose every decision is full output
carries line 4 of the contract without a measurement, and the line says so in those words rather
than claiming one. The other five still have to hold on their own.

This is deliberately narrow. `manual` at 100 and nothing else — **not** a curve whose points all
happen to be 100, because a curve is a thing people edit, and a rule that stopped applying the
moment somebody moved a point from 100 to 95, without saying so, is a rule nobody can see. It is
also not a way in: a channel driven this way is driven at 100% and at no other number, so the first
time anybody wants the machine quieter, the floor test is still the only road there.

What it buys is worth stating, because it is the case that forced it (**V**, 2026-09-22, the
reference machine). A chassis fan whose floor nobody has measured, on an appliance the table does
not permit control of, **held at full by hand through the raw port helper** — which is what this
machine was doing for six hours — is a fan outside this plugin's restore, its watchdog, its incident
file and its page. The same fan held at full by the daemon is handed back on a clean stop, on an
unhandled exception, on a signal, on the watchdog firing, on a package removal, and the moment a
sensor goes quiet. Refusing the safer of those two, and leaving the owner the unsafer one, was the
wrong refusal.

**And underneath all three there is a floor this layer cannot move.** `fanctl-io` has a
compiled-in hard floor of 30% (**D**, `FANCTL_HARD_FLOOR_PCT` in `fanctl-io.c:144`) and reads
`/usr/local/etc/fanctl/floor.conf`, which can raise it per channel and cannot lower it; a
malformed line there rejects the whole file and the hard floor applies. The helper refuses
anything below that and does **not** clamp — it names the floor and where it came from, and this
layer passes that refusal through as it arrives.

Two consequences, and both are stated on the page rather than discovered:

* A duty this layer considers acceptable can still be refused. Good: the floor survives a bug in
  every layer above it, which is the whole point of putting it down there.
* **The floor test cannot measure below the helper's floor.** If a fan is still turning at that
  duty, the honest reading is "this is as low as this machine will let anyone go". Going further
  down takes an edit to `floor.conf` on the firewall, by the person who owns the fan, with the lid
  off — which is exactly the right gate for it, and is not something a web page should be able to
  do.

Two things are worth being exact about, because both invite the wrong reading:

* This is not the duty at which a **stopped** fan starts. That is higher on every fan ever made,
  and measuring it means stopping a fan on a firewall that is in service. The plugin never goes
  below the keep-turning floor, and on restore the hardware decides.
* A test that **stops** the fan is a measurement too — the most valuable one, because it is taken
  at the boundary. It is recorded as `not_below`, and the effective floor is the higher of the two
  readings. (The first version of that bookkeeping threw a stall away unless some other test had
  succeeded, which left a fan with a floor below a duty that had demonstrably stopped it. Fixed,
  and named here because it is the kind of bug that is invisible until it is not.)

On the reference machine **the floor is unmeasured, so `fans.json` ships `control: false` and
`floor: null` for `SYSFAN`** (**V** for the readings, and an explicit absence for the floor). The
plugin monitors that machine and drives nothing until somebody stands in front of it.

## 7. Four channels reading zero are not four stopped fans

`CPUFAN`, `AUXFAN0`, `AUXFAN1` and `AUXFAN2` all read 0 rpm on the reference appliance (**V**), and
a 330-class chassis plainly has more than one fan. A tach of zero means this chip is not being told
about a fan there: no fan, a fan with no tach wire, or a fan on a header this chip does not sample.
**Nothing in software can tell those apart**, so they are reported as *no tachometer signal*, they
are never offered as fans to control, and the word "stopped" is only ever used about a channel the
table says should have a fan turning on it.

## 8. What is deliberately not settled

* **Which control law `SYSFAN` is under.** 0.6% of variation across a 12 C swing fits a fixed duty
  and fits a SmartFan IV curve that is flat below 40 C equally well (**V**, and the ambiguity is the
  finding). Settling it needs three writes — bank select `0x4E`, then reads of `0x102` and `0x109` —
  none of which is a fan setting and all of which are writes to this chip, so they belong to the
  owner with his eyes on the machine, not to a plugin and not to an agent.
* **One curve for all driven fans.** The sensor group is per channel and the floor is per channel,
  but the shape is shared. A machine with a CPU fan and a chassis fan that want different shapes is
  a real case this version does not cover.
* **Nothing is known about start-up duty**, deliberately (§6).
* **A cross-version baseline format.** An upgrade leaves the old daemon running by design
  (`pre-deinstall` skips on `PKG_UPGRADE`), so new code must keep reading an old `baseline.json`.
  Today there is one version and nothing to be compatible with.
* **The packaging seam for the C helper.** `plugins.mk` copies everything under `src/` verbatim,
  which would ship `fanctl-io.c` into `/usr/local/fanctl-io/` and install no binary. Both honest
  finishes are written down in the `Makefile`; today `install/install.sh` builds it from the
  repository and `pkg/post-install.sh` builds it from `/usr/local/fanctl-io` if the source
  arrived there. Until it is closed one way, a package built from this tree installs a plugin
  that reads correctly and cannot write — the safe half to get wrong.
* **`/var/db/fanctl` is shared** with the helper, which keeps its own `ch<n>.state` files there
  (**D**, `STATE_DIR` in `fanctl-io.c:163`). Nothing collides today — the names are distinct —
  but it is one directory with two writers, and both packaging paths create it with the same
  owner and mode so that neither surprises the other.

## 9. Settings

| Setting | Default | What it does |
|---|---|---|
| `enabled` | off | nothing runs until it is on |
| `mode` | **monitor** | monitor writes nothing, ever; `curve` and `manual` write |
| `channels` | empty | which channels may be driven; empty means none |
| `sensors` | `cpu,pch` | which groups to follow — the **hottest** among them, never an average |
| `interval` | 10 s | how often a decision is made |
| `floor_duty` | 40% | the owner's own floor, on top of the measured one; can only raise it |
| `manual_duty` | 70% | what `manual` holds, raised to the floor if below it |
| `points` | `40:60,50:75,60:90,70:100` | the curve |
| `limit_c` | 70 C | at or above: 100%, at once, curve not consulted |
| `panic_c` | 85 C | above: hand the fans back and exit |
| `hysteresis_c` | 4 C | how far it must cool before slowing — **downwards only** |
| `dwell_seconds` | 30 s | and how long before it may slow at all |
| `step` | 5% | duties round **up** to this, so most cycles write nothing |
| `accept_unmeasured` | off | satisfies contract line 2 and nothing else |

The watchdog length is **not** a setting: six intervals, floor 45 s. A watchdog whose length can be
typed into a page is a watchdog somebody will one day set to an hour.

Every bound is enforced twice — in `FanCtl.xml` and again in `settings()` in `fanctl.py` — because
the daemon reads `config.xml` with no model in front of it, and a file restored from a backup
arrives unchecked. Where the two disagree the narrower wins, which is the Python one, because that
is the copy with a fan behind it.

## 10. Layout

```
Makefile, pkg-descr, README.md, DESIGN.md
install/{install.sh,uninstall.sh,enable.sh}
pkg/{post-install.sh,pre-deinstall.sh,post-deinstall.sh}
docs/contributing-fans.md
src/etc/rc.d/fanctl                          the service
src/etc/newsyslog.conf.d/fanctl
src/opnsense/service/conf/actions.d/actions_fanctl.conf
src/opnsense/scripts/fanctl/{fanctl.py,curve.py,sensors.py,fans.json}
src/opnsense/scripts/fanctl/chip.py          the hardware layer (not this document)
src/opnsense/scripts/fanctl/history.py       the record: rings, slots, duty-change events
src/opnsense/scripts/fanctl/stats.py         arithmetic over the record; opens no file
src/opnsense/scripts/fanctl/probe.py         what is in a machine nobody here has opened
src/opnsense/scripts/fanctl/response.py      the one question that needs a write
src/opnsense/scripts/fanctl/policy.py        the curve, read from the machine own limits
src/etc/cron.d/fanctl                        the sampler, for when the daemon is not running
src/opnsense/www/js/widgets/FanCtl.js        the dashboard widget
src/opnsense/www/js/widgets/Metadata/FanCtl.xml
src/fanctl-io/{fanctl-io.c,Makefile}         the port helper, built on the firewall
src/etc/fanctl/floor.conf.shadow             the helper own floor file, all lines commented out
src/opnsense/mvc/app/models/OPNsense/FanCtl/{FanCtl.xml,FanCtl.php,Menu/Menu.xml,ACL/ACL.xml}
src/opnsense/mvc/app/controllers/OPNsense/FanCtl/{IndexController.php,forms/general.xml,Api/*.php}
src/opnsense/mvc/app/views/OPNsense/FanCtl/index.volt
```

Menu: **System → Fan Control** (it is about the appliance, not about the network). State in
`/var/db/fanctl`: `baseline.json`, `measured.json`, `held.json`, `status.json`, `incident.json`,
and the helper's own `ch<n>.state`.

The page has three tabs under the strip of vital readings. **Readings** is the record: the
temperatures as a grid of cards - each one the name over its sensor, the reading large and in the
theme's warning colour at the limit and its danger colour past the panic point, a short band, and
the window's lowest, mean and highest - with the processor cores as one card that opens a grid of
its own; the fans and the load as rows. **Settings**
is OPNsense's own form with the form's own **Apply**, which saves and restarts the controller
(~~a "Save and apply" button that followed the reader down the form~~ until 2026-10-17: the form
is short with full help off, and OPNsense's forms keep their button at the end). **Hardware** is
laid out like a form too - the chip, this appliance, the floor workbench one channel a row - with
the long explanations under its **full help** switch, as on every OPNsense form.

Two bugs found in `os-frontpanel`'s rc script are not repeated here, and both are commented at the
line that avoids them: `daemon(8)` writes its pidfile with **no trailing newline**, so `read(1)`
reports failure having already assigned the value — the value is read first and judged afterwards —
and the wait for that pidfile is **polled**, a tenth of a second at a time, because a flat
`sleep 1` is too long when it worked and too short on a machine that is busy booting.

## 11. The history, and the one statistic it exists for

### 11.1 Why there is a record at all

The owner of the reference appliance measured this by hand, with the lid off and a stopwatch
(**R**, reported by the owner and consistent with this machine's own readings):

| | fan | CPU | chipset |
|---|---|---|---|
| duty 20% | 1332 rpm | 81 C | 54.0 C |
| duty 100% | 5844 rpm | 75–80 C | 56.0 C |

**A 4.4× increase in fan speed for about six degrees, and the chipset went up two.** That is the
most useful thing anybody could learn about this chassis, and the plugin had every number the
measurement needed and threw all of them away every ten seconds. §12 is what stops that.

It is not a graph feature. The measurement above is an *argument*: it says that this box is a
restricted duct, that the fan is not the limiting part, and that a louder fan would buy noise
and not cooling. A plugin that can make that argument from its own record is a plugin that can
tell somebody to stop shopping.

### 11.2 What is sampled, and what one sample costs

Once a minute: every temperature in the sysctl tree, every fan's tachometer, the duty each
recorded channel is at, and the CPU busy percentage over the interval since the last sample.

The busy figure is not about fans, and §12.5 is why it is not optional.

Measured on the reference appliance 2026-09-22, FreeBSD 15.1-RELEASE-p3, Python 3.13.15,
i5-6500, 40 rounds each (**V**):

| what | median | p90 | max |
|---|---|---|---|
| `sysctl -e` over the four **prefixes**, as `sensors.read()` does it | 40.79 ms | 94.63 | 126.99 |
| `sysctl -e` over the seven named **leaves** | 1.54 ms | 2.01 | 4.33 |
| the same seven leaves **+ `kern.cp_times`, one call** | 1.28 ms | 1.34 | 1.41 |
| `fanctl-io status`, all five channels, read-only | 2.11 ms | 2.39 | 2.60 |
| `sysctl -N` over all four prefixes (the discovery walk) | 1.72 ms | 4.77 | 4.91 |
| bare `sysctl -n kern.ostype` — the process-launch floor | 1.16 ms | 1.24 | 1.45 |

**One sample costs 3.4 ms of work** (1.28 + 2.11), and `history.py cost 40` measured the reading
half of it at **1.43 ms median on the machine itself** (**V**, min 1.37, max 3.47, 7 leaves).

**The 40 ms is the finding, and it is not where anybody would look.** `sysctl -e dev.cpu.`
evaluates all 81 nodes under that prefix (**V**, `sysctl -N dev.cpu. | wc -l` → 81), and
`dev.cpu.0.freq` alone costs a median 3.39 ms and a maximum of 46.32 ms on this machine because
reading it goes out through ACPI (**V**). Naming the four `dev.cpu.N.temperature` leaves instead
costs 1.83 ms for all four. So the sampler discovers the leaf list with `sysctl -N`, which prints
names without running a single handler, once an hour, and reads named leaves in between. The
whole price of that arrangement is that a sensor plugged in mid-hour is picked up at the next
refresh.

**This applies to `sensors.read()` too**, which the daemon calls every ten seconds and which walks
the prefixes. It is not this chapter's file to change, and the measurement is recorded here
because it is a 40 ms cost in the control loop that nobody had priced. `history.py` does not
depend on it being fixed: it takes its own cheap reading when it has to.

**What was deliberately not optimised.** Reading sysctls through `ctypes` and libc would take the
1.28 ms to near zero, and it is not worth doing. The cron path pays **46 ms to start Python**
before it reads anything (**V**: bare interpreter 18 ms, `+json` 30, `+subprocess` 41,
`+json,os,time,subprocess,tempfile` 46, `+fcntl,math` 51), so removing 1.28 ms from a 49 ms sample
is noise bought with a new class of bug — buffer sizes and integer widths — in a plugin whose
whole argument is that it is careful. For the same reason turned around, `math` and `statistics`
are not imported: they cost 5 ms of interpreter start between them, and two small functions in
`stats.py` replace them.

End to end on the real appliance, cron path, reading the real chip and the real sensors (**V**,
8 samples 61 s apart, 2026-09-22): **54.4 ms for the first sample** — leaf discovery, a chip read
and three files created — and **18.6 to 50.8 ms afterwards**, median about 40 ms. Roughly 0.07% of
one minute on one of four cores, and the great majority of it is the 46 ms of interpreter start
that the daemon path does not pay at all. The run recorded 10 series a sample, closed a
five-minute slot at the boundary, and rewrote `trend.json` on its own five-minute cadence:

```
{"recorded":true,"series":10,"seconds":61,"closed":["fine"],"whole_sample_ms":40.75}
```

The layout it settled on is the shape §12.4 describes — the four silent channels are absent, and
`SYSFAN` is there twice, once for its tachometer and once for its duty (**V**):

```
['B|cpu', 'T|dev.cpu.0.temperature' ... 'T|hw.acpi.thermal.tz1.temperature', 'R|SYSFAN', 'D|SYSFAN']
[1790095890, 0, 38, 570, 530, 520, 520, 470, 279, 299, 5113, null]
```

That slot is 57.0 / 53.0 / 52.0 / 52.0 C on the cores, 47.0 C at the chipset, 27.9 and 29.9 C at
the board zones, 38% busy and 5113 rpm — **the same machine that read 81 C during the owner's test,
with the fan still at 100%.** The load moved and the fan did not, which is condition 2 of §12.5
arguing for itself out of a single sample.

### 11.3 Where it lives, and exactly how big it gets

Four rings and a step record, each in its own file under `/var/db/fanctl`. One file per ring is
not tidiness — it is the whole write budget. A single document would be rewritten every minute
because the newest ring changed, carrying the coarse rings through that write 1440 times a day
for nothing.

| file | cadence | slots | covers | rewritten |
|---|---|---|---|---|
| `history-live.json` | 60 s | 120 | 2 hours | every minute |
| `history-fine.json` | 5 min | 288 | 24 hours | every 5 min |
| `history-hour.json` | 1 h | 720 | 30 days | hourly |
| `history-day.json` | 1 d | 365 | 1 year | daily |
| `steps.json` | on a duty change | 100 | the last 100 changes | on a change |
| `trend.json` | 5 min | — | the document the page reads | every 5 min |

Measured with every ring filled to its limit, on the reference appliance's own series — 7
temperatures, 1 fan with a tachometer, 1 duty, 1 busy figure — by
`history.py selftest --sizes` (**V**, 2026-09-22):

```
history-live.json    10,660 B   120 slots    88.8 B/slot
history-fine.json    77,785 B   288 slots   270.1 B/slot
history-hour.json   221,943 B   720 slots   308.3 B/slot
history-day.json    124,776 B   365 slots   341.9 B/slot
steps.json           72,434 B   100 steps   724.3 B/step
trend.json            7,573 B
                    ---------
TOTAL               515,171 B  =  503 KiB
```

**The record is bounded at 503 KiB and reaches that size after a year.** Every ring has a fixed
slot count and every slot has a fixed width; there is no path in `history.py` that appends
without discarding. 503 KiB is the number the plugin promises, because a promise that depends on
somebody else's compression setting is not a promise.

**On the filesystem it is less, and the obvious way to measure that is wrong.** The pool is ZFS
with compression on (**V**, `zfs get compression,compressratio zroot/ROOT/default` →
`on`, `2.59x`), and `du -k` over the filled rings reported **48 KiB for 515,171 bytes** — a ratio
of 10.7x, which would be a lovely number to quote and is meaningless. `selftest --sizes` fills the
rings by **repeating one slot**, so what was measured is how well ZFS compresses the same 300
bytes seven hundred times. `gzip -9` on the same files makes the artefact plain: the padded hourly
ring goes 221,943 → 1,638 bytes, a ratio of 135x, which no real record will ever see (**V**).

Measured on the rings from the **live run**, which contain genuinely varying readings taken a
minute apart from the real machine (**V**, 2026-09-22):

```
history-live.json   3,567 B  ->    857 B  gzip -9   4.2x
trend.json          5,389 B  ->  1,035 B  gzip -9   5.2x
```

So the realistic on-disk figure is **somewhere near 100–130 KiB**, and it is stated as a range
with its method attached rather than as the single clean number the padded test was happy to
provide.

**Bytes written per day**, from the same measurement:

```
live     1440 x  10,660  =  15,350,400
fine      288 x  77,785  =  22,402,080
hour       24 x 221,943  =   5,326,632
day         1 x 124,776  =     124,776
trend     288 x   7,573  =   2,181,024
steps       only on a duty change
                            ----------
                            45,384,912 B/day  =  43.3 MiB/day
```

That is the honest figure and it is not a problem: 43 MiB a day is 15.4 GiB a year, before the
2.59× the filesystem takes off it, against an SSD endurance measured in hundreds of terabytes.
It is recorded here rather than waved away because `os-linkhealth`'s `state.py` says the disk in
this machine has 31% of its write endurance consumed, and a plugin that adds to that should say
by how much.

Two things were trimmed once the figure was measured rather than guessed at, and both were free:
`trend.json` is written every five minutes instead of every minute (it summarises windows of an
hour and upwards; recomputing "the last month" sixty times an hour changes nothing in it), which
saved 8.7 MiB/day; and `steps.json` keeps 100 changes instead of 200, which saved 72 KiB of the
total, because each step carries every sensor's name twice and the estimator stops learning much
after a dozen.

### 11.4 The shape of a slot, and why it merges exactly

A live slot is `[timestamp, layout, values...]`. An aggregate slot is
`[timestamp, layout, samples, observed, unobserved, per-series blocks...]`, and each block is
**minimum, maximum, the weighted sum, and the seconds that sum was taken over** — plus, for
temperatures only, the seconds spent above each rung of a fixed ladder.

Three decisions there are load-bearing:

* **A weighted sum and its seconds, never a mean.** Means do not merge: the average of twelve
  five-minute averages is only the hour's average when all twelve covered the same number of
  seconds, and the one case where they do not is the case that matters — the hour containing a
  reboot. Sums and seconds merge by addition whatever the gaps, so a day built from hours built
  from five-minute slots carries the number it would have had from the raw samples.

* **Seconds per series, not only per slot.** A sensor that answered for two minutes of a
  five-minute slot has a sum covering two minutes, and dividing it by the slot's five would report
  a sensor that read 70 C as having averaged 28 C.

* **`unobserved` is derived at close, as the period minus what was observed**, and never carried
  upward. That is exact for a missed minute, a missed hour, and a bucket that was created and
  never sampled into; accumulating it as well would count the same silence twice.

The time-above ladder is **60, 70 and 80 C, fixed and absolute, and deliberately not taken from
the settings**. A threshold that followed a setting would silently rewrite history every time
somebody edited the page: yesterday's "four hours above the limit" would become "no time above the
limit" because the limit moved. A question about 75 C is answered with the rungs that bracket it,
and the per-slot maximum answers "did it ever" exactly, for any temperature.

**Channels with nothing to say are left out.** Four of the five channels on the reference appliance
read 0 rpm for ever and carry a duty of 50% that nothing has ever changed (**V**, `fanctl-io
status`, 2026-09-22); recording them would be eight of every thirteen numbers in a slot describing
four headers that no fan is on. A channel enters the record the first time it reports a turning fan
or the first time this plugin drives it, and once in it never leaves — a fan is at its most
interesting at the moment it stops, and a rule that dropped it for reading 0 would delete the
evidence. On this machine the record holds 10 series; the ncthwm view offers 12.

### 11.5 The statistic that matters, and the four ways it goes wrong

**The naive version is worse than nothing.** Regress temperature on duty across a whole history
and the answer comes out saying that *more fan makes the machine hotter*. It is not an arithmetic
error; it is the arithmetic answering a different question. A fan curve raises the duty **because**
the machine got hot, so in any history taken while a curve was running, high duty and high
temperature arrive together. The cause runs backwards through the data.

So nothing in `stats.py` correlates duty against temperature. Everything is a **paired comparison
across one duty change** — a window before it against a window after it — with four conditions, each
of which exists because without it the number is wrong in a specific way. A pair that fails one is
recorded as rejected with the rule that rejected it, and the page shows those counts.

1. **The duty must really have moved** — at least 10 points. Below that the temperature change is
   smaller than the sensors' resolution (coretemp reports whole degrees on this machine: **V**,
   `dev.cpu.0.coretemp.resolution` = 1), and dividing sensor noise by a small number manufactures a
   large slope out of nothing.

2. **The load must not have moved** — the CPU busy percentage may differ by at most 10 points
   between the two sides. This is the one that matters, and it was not a theoretical worry: the
   reference machine's CPU was at 75–81 C during the owner's test and at **65–66 C an hour later
   with the fan still at 100%** (**V**, `dev.cpu.0.temperature` = 66.0C at duty 100,
   2026-09-22) — a bigger swing than the entire effect being measured, and none of it was the fan.

3. **The reading must have settled** — 240 seconds, four sample intervals. Two time constants sit
   on top of each other and only the slower matters: the fan reaches its new speed inside one
   sample, and the chassis does not. The owner's own measurement ran two minutes and the CPU was
   still moving at the end of it. The estimator does **not** assume 240 s is enough; it compares
   the two halves of the settling window's tail and, where the reading was still moving by more
   than one whole degree, marks the pair and the page says the figure is a **lower bound** — what
   the fan had bought by then, not what it will buy.

4. **There must be more than one** — three pairs minimum, and the total duty span across them must
   be at least 20 points. Three pairs that all stepped 10 to 20 per cent describe what happens
   between 10 and 20 per cent; a rate quoted without that span invites exactly the extrapolation it
   cannot support. "Too few pairs" and "too narrow a span" are different answers and stay different
   all the way to the page — neither is ever reported as a slope of zero.

What comes out is a **median across pairs with the spread beside it**, never a lone number, so a
reader can see for themselves whether the chassis answers consistently. The sign convention is
fixed and stated once — **positive means more fan is cooler** — so the chipset coming out negative
reads as the finding it is rather than as a mistake.

**And the window filter that is easy to miss.** A comparison window is filtered by the duty the
sample was taken at, not only by when it was taken: a sample at the new duty is not part of the
before, whatever its timestamp says. This was measured, not reasoned about. Before the filter, a
change the sampler inferred a second after the daemon had announced the same one took a "before"
mean of 79.0 C across a change from 81.0 to 75.0 and reported 0.5 C per ten points for a chassis
that gives 0.75 (**V**, `history.py selftest`, 2026-09-22).

**And the duplicate that was worse than the error.** The daemon calls `note_duty()` at the second
of the write; the sampler sees the same change in the next minute's duty column. Without a guard,
every commanded change is recorded twice — measured: **fourteen closed steps where seven changes
had happened**, and later twelve where seven had (**V**, same selftest). The double entries are not
independent observations of anything, so they narrow the reported spread while adding no
information, which is precisely the shape of a statistic that looks more certain the more it
repeats itself. The two records are matched on **the channel and the duty arrived at**, within one
sampling gap — not on the timestamp, which cannot be made to line up.

### 11.6 The sentence the plugin should be able to write by itself

`stats.verdict()` returns a **code and the numbers its sentence needs, and never the sentence**.
The words are built in `_trend_text.js.part` through `lang._()`, because `lang._()` resolves at
template-compile time against a literal and cannot reach a string assembled in Python. Arabic
lives in the catalogue and nowhere else; a finished English sentence arriving from the collector
is a sentence no catalogue can translate.

| code | when |
|---|---|
| `trend.waiting` | nothing has changed the duty enough to measure from — the commonest state under a gentle curve, and not a failure. The page offers to run the experiment instead of waiting for it to happen by accident. |
| `trend.narrow` | there are pairs, over too short a stretch of the range to quote a rate from |
| `trend.good` | the fan buys real degrees; more of it is a remedy |
| `trend.poor` | less than 1.0 C per ten points — **this is the reference appliance's answer** |
| `trend.inverse` | this sensor got **hotter** with more fan — the chipset's answer, and not an error |
| `trend.lower_bound` | a qualifier on any of the above: the readings had not finished moving |

The sentence this appliance should be able to write, once it has been given four minutes of
experiment, is the English behind `trend.poor` with its own numbers in it:

> More fan buys very little here: core 0 falls only about 0.75 C for every ten per cent of duty,
> roughly 7.5 C across the whole range, and the fan turns 4.4 times faster to do it. This is a
> restricted chassis, not a fan that is too slow — a louder fan would not fix it. The hottest
> reading in the last day left 19 C before this CPU protects itself. It has never once slowed
> itself down for heat since it was switched on.

The last two clauses are the half that decides what to do about the first half, and they come from
`dev.cpu.0.coretemp.tjmax` (100.0C) and `dev.cpu.0.coretemp.throttle_log` (**0**, **V**,
2026-09-22). *The fan buys you six degrees* on its own invites somebody to buy a louder fan. *The
fan buys you six degrees and you have nineteen to spare and have never needed one of them* is a
complete answer.

**The experiment is four minutes and needs no new write path.** `configctl fanctl set SYSFAN 100
300` holds a duty and hands it back, with the floor enforced and the contract in front of it —
the verb already exists. Three of those, a few minutes apart, is all `trend.waiting` is asking
for. The history records what they bought without being told.

### 11.6b What the page shows, concretely

Three panels under the existing live table, all of them fed by `trend.json` — the page never walks
a ring. `configctl fanctl history <ring>` exists for the graph and is asked for one ring by name.

**Panel 1 — every sensor and every fan, four windows.** One row per series, one column group per
window (hour, 24 hours, 7 days, 30 days), each cell `now / lowest / highest / average`. On the
reference appliance that is 10 rows. Two rules on the cells:

* where the window's coverage is below 99%, the cell carries *"{percent}% of this window was
  watched"* — a 24-hour average from a plugin that ran for two hours of it is a true statement
  about two hours, and the only fix that does not throw the number away is to print the coverage
  beside it;
* the temperature rows carry the time-above ladder — *"3 h 12 m above 70 C"* — from the fixed
  rungs of §12.4, and a row with nothing above 60 C shows nothing rather than three zeroes.

**Panel 2 — what more fan buys.** The verdict sentence of §12.6 in full, and beneath it the
working, because the sentence is an argument and an argument that hides its evidence is a slogan:

* `{counted} of {looked_at} duty changes could be counted`;
* the rejection counts, one line each — *"11 discarded because the machine got busier or quieter
  at the same moment"* is the most likely line on a real firewall and it teaches the reader the
  same lesson the slope does, earlier;
* the per-pair spread, `lowest` to `highest`, beside the median, never the median alone;
* the range the figure describes, `from_pct` to `to_pct`, so nobody reads an answer about 20–30%
  as an answer about the fan;
* the standing note that a change only counts when the fan moved at least 10 points **and the
  machine was doing about the same work before and after**, with the one-sentence reason: a fan
  speeds up because the machine got hot, so comparing fan speed against temperature without that
  rule answers the opposite question and gets the opposite answer.

**Panel 3 — the comparison that started all this**, when there is one: the plugin's own version of
the owner's stopwatch, drawn straight from the two ends of the measured span.

| | fan | hottest sensor | chipset |
|---|---|---|---|
| at `{from_pct}`% | `{rpm}` rpm | `{c}` C | `{c}` C |
| at `{to_pct}`% | `{rpm}` rpm | `{c}` C | `{c}` C |
| | **x{rpm_ratio}** | **{delta} C** | **{delta} C** |

and where `trend.waiting` is the verdict, this panel is replaced by the offer to measure it:
*"Hold this fan at full speed for five minutes, then hand it back. That is the whole experiment."*
The button behind it is the existing `set` action — see §12.6 — so the page gains an experiment
and the plugin gains no new way to write to a fan.

**What the page must not do:** show a slope with fewer than three pairs, show a rate outside the
duty range it was measured in, show a mean without its coverage, or print *0.0 C per ten per cent*
where the answer is that nothing has changed enough to measure. Zero and unknown are different all
the way down this chapter and they must stay different on the glass.

### 11.7 Who samples, and the rule that is not restated

Two callers, and the difference is the chip:

* **The daemon**, every cycle, passing the reading and the fan view it has *already taken*.
  Nothing is read again; the call costs a comparison and, once a minute, a write. This is the
  normal path where fan control is on.
* **Cron**, every minute, with nothing — but only after checking that the daemon is not running,
  which it does by the age of `status.json` rather than by a pidfile, because what it needs to
  know is not "is a process there" but "is something else reading this chip", and a wedged daemon
  answers the pidfile question wrongly in the dangerous direction.

`history.py` has no code that could write to the chip. Its only hardware call is
`chip.read_path()`, read-only on both of its paths, and it is not called at all while the daemon
is running. That is `chip.py`'s rule and this layer keeps it rather than restating it.

**A failing history must never take the fan controller down.** The hook in `write_status()` is
wrapped in a `try` that logs and continues. A record of what the fans did is worth less than the
fans.

### 11.8 What is deliberately not settled here

* **The NCT6779D's own temperature inputs.** `ncthwm` exposes no temperatures at all, though the
  chip has SYSTIN, CPUTIN and AUXTIN0-3 (**D**, `nct6775-core.c`). Nobody has read them on this
  machine, and reading them means register reads through a new `fanctl-io` verb — reads, not
  writes, but still a verb that does not exist. `history.py` needs no change to pick them up: a
  temperature that appears in the reading appears in the record, under a new layout, and the
  rings carry on.
* **The power supply has no sensor anywhere** (**V**, exhaustive sysctl walk), and its fan was
  also replaced. Nothing in this plugin can say anything about it, and it does not try.
* **Sub-minute events.** A fan that stalls and recovers inside one minute leaves no trace in the
  rings. The daemon's own stall check catches that in its own cycle and writes an incident; the
  history is for trends, not for incidents, and the two are different files on purpose.
* **Cross-version ring format.** `version` is 1 and there is nothing to be compatible with. A
  reader that meets a version it does not know should show nothing rather than guess; no such
  reader exists yet.

## 12. What this plugin does not do

- It does not start on installation, and an upgrade does not turn control on either.
- It does not touch a fan to find out what is there: the probe reads and writes nothing.
- It does not drive a channel whose floor was not measured on the machine it is running on.
- It does not drive a channel that reports no turning fan.
- It does not claim a fan is stopped when what it means is that no tachometer answered.
- It does not write to a Super I/O configuration register, a bank select or any I/O port itself —
  every one of those lives below `chip.py`, in `fanctl-io`.
- It does not read the `ncthwm` sysctls while the helper is usable, and it has no way to ask for
  both at once. That is `chip.py`'s rule and this layer keeps it.
- It cannot lower the helper's floor, and there is no setting, no API call and no page control
  that tries to.
- It does not keep control when anything is unclear. It hands the fans back, which is the one
  behaviour worth more than every feature above it.
