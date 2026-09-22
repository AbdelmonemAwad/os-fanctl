/*-
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Copyright (c) 2026 Abdelmonem Awad <eg2@live.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 *
 * ---------------------------------------------------------------------------
 *
 * fanctl-io - the only code in os-fanctl that touches the hardware.
 *
 * It takes a COMMAND, never a register. "tach 2", "duty 1 40", "release 1".
 * Nothing above it can name a register, because a layer that can name a
 * register is a layer that can name the wrong one, and the wrong one here is
 * a fan that stops on a firewall nobody is watching.
 *
 * One command, one JSON answer, exit. It does not fork, loop, sleep or hold
 * anything open. Every state it needs between runs is in a file.
 *
 *
 * WHY THERE IS A C PROGRAM HERE AT ALL
 *
 * There is no supported way to set a fan duty on FreeBSD 15.1. ncthwm(4)
 * registers five sysctls and every one of them is CTLFLAG_RD
 * (sys/dev/ncthwm/ncthwm.c:218-224); `sysctl -aW | grep ncthwm` on the
 * reference machine returns nothing (V, 2026-09-22: the count of matching
 * writable nodes is 0). aibs, coretemp and pchtherm are read-only too.
 * pwm(8) and pwmc(4) exist but DRIVER_MODULE(pwmc, pwmbus, ...) attaches only
 * to a pwmbus provider, and on x86 there is none - /dev/pwm* is absent on the
 * reference machine (V). The only writable path to a Super I/O fan register is
 * raw port I/O through /dev/io.
 *
 *
 * TWO DEVICES, TWO DIFFERENT JOBS, AND WHY IDENTIFICATION USES THE KERNEL
 *
 *   /dev/superio0  - identification only. superio(4) owns the 0x2e/0x2f
 *                    configuration port and serialises every access with
 *                    sc->conf_lock (superio.c:179-190). SUPERIO_CR_READ goes
 *                    through that lock (superio.c:1066-1071), so reading the
 *                    chip id cannot collide with the kernel's own probing.
 *                    Banging 0x2e from userland would: the unlock sequence is
 *                    two writes of 0x87 and an interleave there can leave the
 *                    chip in configuration mode or select the wrong logical
 *                    device. We do not do it, at any price - and see
 *                    HWM_BASE_MIN below, which is what stops us doing it by
 *                    accident when the chip reports a nonsense window.
 *
 *                    superio_io.h is NOT installed on OPNsense
 *                    (V, 2026-09-22: /usr/include/dev/superio/superio_io.h,
 *                    No such file or directory; /usr/src is empty on this
 *                    machine too), so the three-byte struct and the one ioctl
 *                    we use are declared below. The ioctl NUMBER encodes the
 *                    size of the struct and not its name, so a three-uint8_t
 *                    struct declared here produces the same number as the
 *                    kernel's own; if it ever did not, the ioctl would fail
 *                    with ENOTTY and this program would refuse, which is the
 *                    safe direction. SUPERIO_CR_WRITE is deliberately NOT
 *                    declared in this file: a constant that does not exist
 *                    cannot be passed by mistake.
 *
 *   /dev/io        - the hardware-monitor window, at base+5 (index) and
 *                    base+6 (data), where base comes from logical device 0x0b
 *                    register 0x60/0x61. This is where fans live and the
 *                    kernel gives us nothing to reach it with.
 *
 *
 * THE RACE THAT IS THE REAL DANGER HERE
 *
 * ncthwm_query_fan_speed() takes no lock - struct ncthwm_softc has no mutex
 * (ncthwm.c:52-57). It writes the bank, then the index, then reads the data
 * register, on exactly the two ports we use. If it interleaves between our
 * index write and our data write, our byte lands in whatever register the
 * kernel selected last.
 *
 * Nothing in userland can serialise against that. What this program does
 * instead:
 *
 *   - it takes an exclusive flock so two copies of itself can never overlap;
 *   - it re-selects the bank immediately before every index write, because it
 *     cannot assume the bank it left is the bank it finds;
 *   - it reads every configuration register twice and requires agreement,
 *     which turns a collision into a detected error instead of a wrong number;
 *   - it reads a tachometer twice as well, and says in the answer whether the
 *     two reads agreed, rather than presenting a possibly-collided word as a
 *     measurement;
 *   - it verifies every write by reading it back.
 *
 * Two honest limits on that list, because a mitigation that is described as
 * more than it is would be worse than none:
 *
 *   - A collision during a WRITE is detected but not prevented. ec_write_verify
 *     re-reads the register it aimed at, so it catches "the byte did not land
 *     where we meant it to" and retries - but by then the byte has already gone
 *     somewhere. Where, is bounded by what the other party selects:
 *     ncthwm(4) selects bank 4 and reads tachometer counters, which are counts
 *     the chip writes and we cannot usefully change, so a stray byte there is
 *     inert. That is a property of THAT driver, not a guarantee of the window,
 *     and a future reader of these ports that selected a writable register
 *     would make it untrue.
 *   - The window is quiet unless a third party reads dev.ncthwm.*. The layer
 *     above must not read those sysctls while this program runs, and must say
 *     so on the page. Nothing here can stop an administrator typing
 *     `sysctl -a`, and nothing here pretends it can.
 *
 * A kernel driver is the only complete fix, and a kernel module that stops
 * loading after an OPNsense update is exactly the failure this plugin exists
 * to prevent.
 *
 *
 * THE FLOOR
 *
 * A duty below the floor is refused HERE, by this program, reading the floor
 * file itself. Nothing on the command line can lower it and no argument
 * exists to try, so a bug in the Python above cannot stop a fan.
 *
 * The floor binds `release` as well as `duty`, and that is a correction to an
 * earlier version of this file which exempted it. A restore writes a duty that
 * somebody measured off the chip minutes or months ago; if that number is
 * below the floor and the mode being restored is manual, then honouring it
 * literally is how this program stops a fan while reporting success. So a
 * manual original below the floor is not restored: the channel goes to the
 * failsafe instead, and the answer says which happened and why. What cannot be
 * restored faithfully must at least be left turning.
 *
 * FANCTL_HARD_FLOOR_PCT is not a fan model and this program does not pretend
 * to know where any fan stalls - that is a property of the fan, measured with
 * somebody watching it. It is the number below which this program refuses to
 * act at all, chosen above the 20-30% minimum duty commonly reported for
 * four-pin fans (R), so that a wrong value from above is refused rather than
 * obeyed. Whoever has measured a fan can raise the floor in the floor file.
 * Nobody can lower it below this line.
 *
 * It is a PWM floor. On a channel switched to DC output the same percentage is
 * a fraction of a supply voltage and the stall point is somewhere else
 * entirely, so the answer names the output kind and says the floor does not
 * transfer. This program still refuses below its own number there, because a
 * number chosen for the wrong quantity is still better than none.
 *
 *
 * WHAT IS LEFT DRIVING THE FANS IF THE CALLER DIES
 *
 * A manual duty written here is latched in the chip and stays latched. Nothing
 * in this program expires, and a Super I/O fan register is not necessarily
 * cleared by a warm reboot. If the process above is killed with SIGKILL, the
 * duty this program last wrote is what cools the machine until somebody runs
 * `release` or cuts the power - indefinitely, with no thermal response.
 *
 * The chip has its own last-ditch mechanism for exactly this, REG_CRITICAL_PWM
 * and its enable bit, and this program reads it and reports it but does not arm
 * it: arming it is a configuration write with a temperature threshold in it,
 * and neither the threshold nor the consequence has been measured on any
 * machine here. So `duty` states in its answer whether anything in hardware
 * will act if its caller dies, at the moment of taking the channel over, where
 * whoever is deciding can see it. On the reference appliance that answer is
 * expected to be "no" (the bit has only ever been read, never set, V) and the
 * layer above has to say so on the page.
 */

#if !defined(__amd64__) && !defined(__i386__)
#error "fanctl-io drives x86 Super I/O ports through /dev/io; there is no such thing to drive on this architecture."
#endif

#include <sys/types.h>
#include <sys/file.h>
#include <sys/ioccom.h>
#include <sys/ioctl.h>
#include <sys/stat.h>

#include <machine/cpufunc.h>

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*
 * 1.2 adds `temp`, which reads the hardware monitor's own temperature inputs
 * and writes nothing. Nothing parses this string (V, 2026-09-22: no caller in
 * the plugin compares it) - it is bumped because the command set changed and
 * somebody looking at a machine should be able to tell which binary is on it.
 */
#define FANCTL_IO_VERSION	"1.2"

/* See THE FLOOR, above. This is a refusal threshold, not a measurement. */
#define FANCTL_HARD_FLOOR_PCT	30

/*
 * Paths are overridable at BUILD time only, which is how the refusal paths get
 * exercised without a live chip: a build with DEV_SUPERIO pointing nowhere runs
 * every check above the hardware and then stops at the open, having touched no
 * port. FANCTL_HARD_FLOOR_PCT is deliberately not in this list - a floor that
 * can be compiled away is not a floor.
 */
#ifndef DEV_SUPERIO
#define DEV_SUPERIO	"/dev/superio0"
#endif
#ifndef DEV_IO
#define DEV_IO		"/dev/io"
#endif
#ifndef FLOOR_FILE
#define FLOOR_FILE	"/usr/local/etc/fanctl/floor.conf"
#endif
#ifndef STATE_DIR
#define STATE_DIR	"/var/db/fanctl"
#endif
#ifndef LOCK_FILE
#define LOCK_FILE	"/var/run/fanctl-io.lock"
#endif

/*
 * Exit codes. They are part of the interface: chip.py maps each one to its own
 * message, because "it did not work" is not an answer anybody can act on.
 */
#define EX_OK_		0
#define EX_USAGE_	1	/* the command line is wrong */
#define EX_NODEV_	2	/* /dev/superio0 or /dev/io is not there */
#define EX_NOPERM_	3	/* it is there and we may not open it */
#define EX_CHIP_	4	/* unknown chip, or a known chip we will not drive */
#define EX_CHANNEL_	5	/* no such channel */
#define EX_DUTY_	6	/* duty outside 1..100 */
#define EX_FLOOR_	7	/* duty below the floor - refused here */
#define EX_READBACK_	8	/* the chip did not read back what we wrote */
#define EX_BUSY_	9	/* another fanctl-io holds the window */
#define EX_INTERNAL_	10

/* ------------------------------------------------------------------------ */
/* superio(4), declared here because its header is not installed (V)         */
/* ------------------------------------------------------------------------ */

struct superiocmd {
	uint8_t	ldn;
	uint8_t	cr;
	uint8_t	val;
};

/* Stable since FreeBSD 12. The WRITE ioctl is not declared on purpose. */
#define SUPERIO_CR_READ		_IOWR('s', 0, struct superiocmd)

/* Configuration-space registers, all D from nct6775-platform.c:68-90. */
#define SIO_LD_HWM		0x0b	/* NCT6775_LD_HWM */
#define SIO_REG_DEVID		0x20	/* two bytes, 0x20 high, 0x21 low */
#define SIO_REG_ENABLE		0x30	/* logical device enable, bit 0 */
#define SIO_REG_ADDR		0x60	/* logical device base, two bytes */
#define SIO_REG_IO_LOCK		0x28	/* NCT6791_REG_HM_IO_SPACE_LOCK_ENABLE */
#define SIO_ID_MASK		0xfff8
#define IOREGION_ALIGNMENT	0xfff8	/* (~7) in the Linux source */
#define IOREGION_OFFSET		5	/* index port = base + 5, data = base + 6 */

/*
 * The lowest window base this program will drive.
 *
 * Linux checks only for zero (nct6775-platform.c: "Refusing to enable a
 * Super-I/O device with a base I/O port 0"). Zero is not the only value that
 * must be refused. Everything below 0x100 on a PC is fixed motherboard I/O -
 * the DMA controllers and their page registers, the interrupt controllers, the
 * timer, the keyboard controller, the CMOS index - and 0x2e/0x2f and 0x4e/0x4f,
 * the Super I/O configuration ports themselves, are in there. A chip reporting
 * base 0x2e (masked to 0x28) would put this program's DATA port on 0x2e and it
 * would bang the configuration port from userland, which is the one thing the
 * header above swears never to do. A chip reporting base 8 would put it on the
 * DMA page registers.
 *
 * No BIOS programs a hardware-monitor window down there; the values seen in the
 * wild are 0x290, 0x295, 0xa10, 0xa20 and similar. So a base below 0x100 is a
 * chip that is not telling us where its window is, and the answer to that is to
 * stop. This bound is reasoning about the PC I/O map, not a measurement, and it
 * is written here so that it is refusable rather than assumed.
 */
#define HWM_BASE_MIN		0x100

/* ------------------------------------------------------------------------ */
/* The chip table                                                           */
/* ------------------------------------------------------------------------ */

/*
 * Every chip in this table is a Nuvoton whose fan registers are the same
 * registers. That is not an assumption: in the Linux nct6775 driver the cases
 * nct6779, nct6791, nct6792, nct6793, nct6795, nct6796, nct6797, nct6798 and
 * nct6799 all assign REG_FAN = NCT6779_REG_FAN, REG_PWM[0] = NCT6775_REG_PWM,
 * REG_FAN_MODE = NCT6775_REG_FAN_MODE, REG_PWM_READ = NCT6775_REG_PWM_READ,
 * REG_PWM_MODE = NCT6776_REG_PWM_MODE and fan_from_reg = fan_from_reg_rpm
 * (D, nct6775-core.c:3833-3912, 3913-4047, 4048-4130). Only the channel count
 * differs, which is why the count is the only per-chip field here.
 *
 * Chips outside this family are refused BY NAME rather than driven from a map
 * nobody has checked. NCT6775F and NCT6776D share the PWM registers but not
 * the tachometer conversion; NCT6106D and NCT6116D share neither. ITE and
 * Fintek parts, which superio(4) also attaches to, are a different chip
 * entirely. Adding one is a row here plus a tachometer conversion, and it
 * should arrive with a measurement, not with a datasheet reading.
 */

/* NCT6779_REG_FAN - tachometer, word-sized, RPM directly (no divisor). */
static const uint16_t reg_fan[]	     = { 0x4c0, 0x4c2, 0x4c4, 0x4c6, 0x4c8, 0x4ca, 0x4ce };
/* NCT6775_REG_PWM - the manual duty we write, 0..255. */
static const uint16_t reg_pwm[]	     = { 0x109, 0x209, 0x309, 0x809, 0x909, 0xa09, 0xb09 };
/* NCT6775_REG_PWM_READ - what the chip is actually putting out.
 * Channels 6 and 7 have no separate readback register; the Linux table
 * repeats 0xa09/0xb09 there (D, nct6775-core.c:148-149), so on those two
 * channels a readback under an automatic mode tells you nothing new, and
 * this program reports it as unavailable rather than as a measurement. */
static const uint16_t reg_pwm_read[] = { 0x01,  0x03,  0x11,  0x13,  0x15,  0xa09, 0xb09 };
/* NCT6775_REG_FAN_MODE - bits 6:4 are the control law. */
static const uint16_t reg_fan_mode[] = { 0x102, 0x202, 0x302, 0x802, 0x902, 0xa02, 0xb02 };
/* NCT6779_REG_CRITICAL_PWM_ENABLE / _PWM - the chip's own last-ditch duty. */
static const uint16_t reg_crit_en[]  = { 0x136, 0x236, 0x336, 0x836, 0x936, 0xa36, 0xb36 };
static const uint16_t reg_crit[]     = { 0x137, 0x237, 0x337, 0x837, 0x937, 0xa37, 0xb37 };

#define REG_BANK_SELECT	0x4e	/* NCT6775_REG_BANK */
#define REG_PWM_MODE	0x04	/* NCT6776_REG_PWM_MODE: channel 1 only */
#define PWM_MODE_MASK	0x01	/* set means DC output, clear means PWM */
#define CRIT_PWM_ENABLE_MASK 0x01

/*
 * (REG_FAN_MODE >> 4) & 7. Linux names nibbles 0 to 4 - manual, thermal
 * cruise, speed cruise, SmartFan III, SmartFan IV - by way of
 * pwm_enable_to_reg(), which is the enum minus one (D, nct6775-core.c:907-911).
 * The field is three bits wide, so 5, 6 and 7 exist and are not named
 * anywhere we can check. MODE_MAX is therefore 7 and not 4: this program only
 * ever writes a mode it read from the same register, and "what was there" is
 * the only correct answer to give a channel back, including a value no
 * document names.
 *
 * Both writes of this register keep bits 3:0 and clear bit 7, which is what
 * store_pwm_enable() does (`reg &= 0x0f; reg |= pwm_enable_to_reg(val) << 4`,
 * D, nct6775-core.c). Bit 7 of this register is therefore cleared by this
 * program and cannot be put back by `release`. Nobody here has measured what
 * that bit does on any of these chips; the only defence offered is that the one
 * driver known to work on them treats it the same way.
 */
#define MODE_MANUAL	0
#define MODE_MAX	7

struct chip {
	uint16_t	id;		/* device id masked with SIO_ID_MASK */
	const char     *name;
	uint8_t		channels;
	uint8_t		io_lock;	/* CR 0x28 bit 4 gates the window on these */
};

static const struct chip chips[] = {
	{ 0xc560, "NCT6779D",			5, 0 },
	{ 0xc800, "NCT6791D",			6, 1 },
	{ 0xc910, "NCT6792D",			6, 1 },
	{ 0xd120, "NCT6793D",			6, 1 },
	{ 0xd350, "NCT6795D",			6, 1 },
	{ 0xd420, "NCT6796D",			7, 1 },
	{ 0xd450, "NCT6797D",			7, 1 },
	{ 0xd428, "NCT6798D/NCT5585D",		7, 1 },
	{ 0xd800, "NCT6796D-S/NCT6799D-R",	7, 1 },
};

/*
 * Chips we can name but will not drive, with the reason in the answer. Saying
 * "NCT6116D, and here is what differs" is a different answer from "unknown chip
 * 0xd280", and a user deciding whether to file a bug needs the first one. The
 * reason is a string and not a channel count because nothing in this program
 * may act on these rows - a number here would be a number nobody checked,
 * sitting in a table that looks exactly like the one that is acted on.
 */
struct refusal {
	uint16_t	id;
	const char     *name;
	const char     *why;
};

static const struct refusal refused[] = {
	{ 0xb470, "NCT6775F",
	  "the PWM registers match but the tachometer carries a divisor, so a "
	  "speed read here would be wrong" },
	{ 0xc330, "NCT6776D/F",
	  "the PWM registers match but the tachometer conversion differs" },
	{ 0xc450, "NCT6106D",
	  "a different register map: neither the fan nor the PWM registers are "
	  "at these addresses" },
	{ 0xd280, "NCT6116D",
	  "a different register map: neither the fan nor the PWM registers are "
	  "at these addresses" },
};

#define NITEMS(a)	(int)(sizeof(a) / sizeof((a)[0]))
#define NCHIPS		NITEMS(chips)
#define NREFUSED	NITEMS(refused)
#define MAX_CHANNELS	7

/*
 * Every per-channel register array is indexed by channel - 1, bounded by
 * chips[].channels. The bound and the arrays have to agree, and the table is
 * the part of this file most likely to be edited by somebody adding a chip. An
 * 8 in that column would index one past the end of these arrays and write a
 * duty to whatever uint16_t followed them in .rodata - a register number nobody
 * chose. So the agreement is checked by the compiler, and again at runtime
 * below, because a table is data and data can also arrive wrong.
 */
_Static_assert(NITEMS(reg_fan) == MAX_CHANNELS, "reg_fan does not match MAX_CHANNELS");
_Static_assert(NITEMS(reg_pwm) == MAX_CHANNELS, "reg_pwm does not match MAX_CHANNELS");
_Static_assert(NITEMS(reg_pwm_read) == MAX_CHANNELS, "reg_pwm_read does not match MAX_CHANNELS");
_Static_assert(NITEMS(reg_fan_mode) == MAX_CHANNELS, "reg_fan_mode does not match MAX_CHANNELS");
_Static_assert(NITEMS(reg_crit_en) == MAX_CHANNELS, "reg_crit_en does not match MAX_CHANNELS");
_Static_assert(NITEMS(reg_crit) == MAX_CHANNELS, "reg_crit does not match MAX_CHANNELS");

/* ------------------------------------------------------------------------ */
/* The temperature inputs                                                   */
/* ------------------------------------------------------------------------ */

/*
 * `temp` is the seventh command and the second that only reads. It exists
 * because on this board the Super I/O is the only honest sensor in the
 * machine that nothing in FreeBSD exposes: ncthwm(4) registers five sysctls
 * and every one of them is a fan tachometer, and the two ACPI thermal zones
 * the kernel does publish are literals in the DSDT (see sensors.py's header).
 *
 * It writes no setting. Like `status` it selects a bank and latches an index
 * - which are address writes to 0xa35/0xa36 and not registers that hold
 * anything - and then reads bytes back. It adds no new CLASS of access to
 * this program: `status` already reads 0x102 and 0x109, which are bank 1.
 *
 * WHY THIS IS A SEPARATE TABLE FROM THE FAN ONE, AND WHY ONE CHIP IS IN IT
 *
 * The nine chips in the table above share their FAN registers, which is why
 * one set of arrays serves all of them and the channel count is the only
 * per-chip field. They do not share their temperature LABELS. Each of
 * nct6779, nct6791, nct6796 and nct6799 has its own label array in the Linux
 * driver and its own mask of which source numbers exist on it, so a source
 * byte of 20 does not have to mean the same thing on two of them.
 *
 * A row that names the wrong sensor is worse than a row that is missing:
 * somebody puts a fan curve on it. So this table holds exactly the chip whose
 * map has been read on hardware, and every other chip - including ones this
 * program will happily drive fans on - is refused by name, with the reason.
 * Adding one is a label array, a mask and a row here, and it should arrive
 * with a reading taken on that board rather than with a datasheet.
 *
 * THE CITATIONS. The register numbers, the label array, the source mask and
 * the decode below are from the Linux nct6775 driver (D, nct6775-core.c at
 * commit 54743b5ab981, lines as marked) - carried here through the citation
 * block in sensors.py (D, sensors.py:836-905), which is where they were
 * written down when this command was designed. The kernel tree is not on the
 * appliance and these lines were not re-opened against it here; what WAS done
 * is the check that matters, which is reading the registers on the chip and
 * seeing whether what comes out of this decode is a temperature (V - see
 * REPORT.md).
 */

/*
 * nct6779_temp_label (D, nct6775-core.c:389-422). Index is the low five bits
 * of the source register. An empty string is a number that is not a source on
 * this chip; index 0 means the register is assigned to nothing at all.
 */
static const char *const nct6779_temp_label[] = {
	"",			/*  0 - not assigned */
	"SYSTIN",		/*  1 - a pin */
	"CPUTIN",		/*  2 - a pin */
	"AUXTIN0",		/*  3 - a pin */
	"AUXTIN1",		/*  4 - a pin */
	"AUXTIN2",		/*  5 - a pin */
	"AUXTIN3",		/*  6 - a pin */
	"",			/*  7 - not a source on this chip */
	"SMBUSMASTER 0",	/*  8 */
	"SMBUSMASTER 1",	/*  9 */
	"SMBUSMASTER 2",	/* 10 */
	"SMBUSMASTER 3",	/* 11 */
	"SMBUSMASTER 4",	/* 12 */
	"SMBUSMASTER 5",	/* 13 */
	"SMBUSMASTER 6",	/* 14 */
	"SMBUSMASTER 7",	/* 15 */
	"PECI Agent 0",		/* 16 - the CPU reporting its own die */
	"PECI Agent 1",		/* 17 */
	"PCH_CHIP_CPU_MAX_TEMP",/* 18 */
	"PCH_CHIP_TEMP",	/* 19 */
	"PCH_CPU_TEMP",		/* 20 */
	"PCH_MCH_TEMP",		/* 21 */
	"PCH_DIM0_TEMP",	/* 22 */
	"PCH_DIM1_TEMP",	/* 23 */
	"PCH_DIM2_TEMP",	/* 24 */
	"PCH_DIM3_TEMP",	/* 25 */
	"BYTE_TEMP",		/* 26 */
};

/*
 * NCT6779_TEMP_MASK (D, nct6775-core.c:424). Bit n set means source number n
 * exists on this chip. Bits 1-6, 8-26; bit 7 and everything from 27 up are
 * clear, which is why Virtual_TEMP (31) - a source on later chips - is not in
 * the array above. Linux logs and skips a source whose bit is clear
 * (D, core.c:4192-4197); this program reports it, and says it is not one the
 * chip claims to have.
 */
#define NCT6779_TEMP_MASK	0x07ffff7eu

/*
 * The value registers, in Linux's own order: the two that also carry limits
 * first (NCT6779_REG_TEMP, D core.c:377, with their sources at
 * NCT6775_REG_TEMP_SOURCE, D core.c:169-170 selected at core.c:3890), then
 * the five the fan control laws read (NCT6779_REG_TEMP_MON, D core.c:378,
 * with their sources at NCT6775_REG_TEMP_SEL, D core.c:171-172 selected at
 * core.c:3891).
 *
 * `fan_channel` is the one genuinely new thing this command buys and it is
 * free: REG_TEMP_SEL[0] IS fan channel 1's selector, so the source byte in
 * 0x100 is the temperature channel 1's own control law watches. That answers
 * a question DESIGN.md section 8 leaves open, with a read. It does not answer
 * what that source is touching - see the note this command prints.
 *
 * `cfg_reg` is NCT6779_REG_TEMP_CONFIG (D, core.c:379-380): bit 0 set means
 * the firmware switched the input off. There are only two of them, one for
 * each limit-capable register, and the five fan-control registers have none -
 * so for those five this program says it does not know rather than saying
 * they are on.
 */
struct temp_input {
	uint16_t	reg;		/* where the value is */
	uint16_t	src_reg;	/* what the chip says is in it */
	uint16_t	cfg_reg;	/* enable bit 0, 0 = on; zero: no such register */
	uint8_t		word;		/* the value is two bytes, high first */
	uint8_t		fan_channel;	/* whose control law reads it, or 0 */
	const char     *role;
};

static const struct temp_input nct6779_inputs[] = {
	{ 0x027, 0x621, 0x018, 0, 0, "limit-capable" },
	{ 0x150, 0x622, 0x152, 1, 0, "limit-capable" },
	{ 0x073, 0x100, 0x000, 1, 1, "fan-control" },
	{ 0x075, 0x200, 0x000, 1, 2, "fan-control" },
	{ 0x077, 0x300, 0x000, 1, 3, "fan-control" },
	{ 0x079, 0x800, 0x000, 1, 4, "fan-control" },
	{ 0x07b, 0x900, 0x000, 1, 5, "fan-control" },
};

/*
 * NCT6779_REG_TEMP_OFFSET (D, core.c:386-387): a signed byte of whole degrees
 * that the CHIP applies to an input. Linux reads it, shows it separately and
 * does not add it to a reading (D, core.c:1661-1664 reads, core.c:2240-2248
 * prints, and nothing adds it) - so this program reports it and never applies
 * it, because applying it would apply it twice.
 *
 * The array is indexed by FIXED INPUT, and this program takes fixed input i
 * to be source number i + 1 - SYSTIN, CPUTIN, AUXTIN0..3 - which is the order
 * of the label array. That indexing is this program's reading of the driver
 * and not a line anybody quoted (R). It is reported with the register number
 * beside it so that a reader can check it, and it can only ever mislabel a
 * byte: nothing here acts on it and no temperature moves because of it.
 */
static const uint16_t nct6779_temp_offset[] = {
	0x454, 0x455, 0x456, 0x44a, 0x44b, 0x44c, 0x44d, 0x449 };
#define NCT6779_OFFSET_SOURCES	6	/* SYSTIN .. AUXTIN3 */

/*
 * The sources that are PINS on this chip: 1 to 6, SYSTIN through AUXTIN3.
 * Everything numbered above them is the chip being told a temperature by
 * something else - an SMBus device, a PECI agent, the PCH - rather than
 * measuring a wire. The distinction is the whole of what this program can
 * honestly say about attachment.
 */
#define NCT6779_PINS		6

/*
 * Thermistor or diode. REG_VBAT 0x5d, REG_DIODE 0x5e, DIODE_MASK 0x02
 * (D, core.c:99-101). The mask says which of the fixed inputs CAN be a diode
 * at all; on this chip that is exactly one of them, fixed input 1, which is
 * CPUTIN. Every other pin is a thermistor and that is a fact of the chip
 * rather than a reading.
 *
 * For the one input that can be either, this program prints the DIODE
 * register byte and says `unsettled` rather than decoding it. The decode in
 * Linux is a two-line expression this file's author has not read at the line
 * it lives on, and a wrong answer there would put the word `diode` next to a
 * thermistor on a page for ever. The byte is in the answer; whoever wants the
 * other half can settle it in one reading.
 */
#define REG_VBAT		0x5d
#define REG_DIODE		0x5e
#define NCT6779_DIODE_MASK	0x02

struct temp_map {
	uint16_t		 id;		/* device id masked with SIO_ID_MASK */
	const char		*name;
	const struct temp_input	*inputs;
	int			 ninputs;
	const char *const	*label;
	int			 nlabels;
	uint32_t		 src_mask;
	const uint16_t		*offset;
	int			 noffsets;
	uint8_t			 pins;		/* sources 1..pins are board pins */
	uint8_t			 diode_mask;
};

static const struct temp_map temp_maps[] = {
	{ 0xc560, "NCT6779D", nct6779_inputs, NITEMS(nct6779_inputs),
	  nct6779_temp_label, NITEMS(nct6779_temp_label), NCT6779_TEMP_MASK,
	  nct6779_temp_offset, NCT6779_OFFSET_SOURCES, NCT6779_PINS,
	  NCT6779_DIODE_MASK },
};

#define NTEMP_MAPS	NITEMS(temp_maps)
#define MAX_TEMP_INPUTS	7

_Static_assert(NITEMS(nct6779_inputs) <= MAX_TEMP_INPUTS,
    "nct6779_inputs is longer than the snapshot array that holds it");
_Static_assert(NITEMS(nct6779_temp_offset) >= NCT6779_OFFSET_SOURCES,
    "the offset table is shorter than the number of sources indexed into it");

/*
 * Outside this, a number is not a temperature: it is an input with nothing on
 * it, or a byte read across a collision in the window. Same bounds sensors.py
 * uses for a sysctl (PLAUSIBLE_LOW/HIGH), and for the same reason - a curve
 * fed -273 idles a fan in a hot box, and one fed 313 runs every fan for ever.
 * A value out here is reported AS that, with its raw bytes, and never as a
 * reading.
 */
#define TEMP_PLAUSIBLE_LOW_C	(-40)
#define TEMP_PLAUSIBLE_HIGH_C	150

/* ------------------------------------------------------------------------ */
/* Failing                                                                  */
/* ------------------------------------------------------------------------ */

/*
 * Kept open for the life of the process on purpose: the I/O privilege this
 * program needs is granted by the open, and letting the descriptor go is not
 * something to do while a port write is still to come.
 */
static int	iofd = -1;

/*
 * Every exit goes through here so that a caller parsing stdout always gets
 * one JSON object, whatever happened. A tool that prints JSON on success and
 * bare English on failure makes the caller guess, and the caller here is a
 * program deciding whether a fan is under control.
 *
 * That promise is only kept if no command prints part of an answer before it
 * knows the whole of it. An earlier version of `status` printed each channel as
 * it read it, so a failed read appended this object to a half-finished one and
 * the caller got two objects on one line - reproduced on the reference machine
 * (V, 2026-09-22: json.loads raised "Expecting ',' delimiter"), which chip.py
 * then reports as code 10, "printed something that is not JSON", instead of the
 * code 8 the exit status actually carried. Every command below now reads first
 * and prints once.
 */
static void
die(int code, const char *fmt, ...)
{
	va_list ap;
	char msg[512];

	va_start(ap, fmt);
	vsnprintf(msg, sizeof(msg), fmt, ap);
	va_end(ap);

	printf("{\"ok\":false,\"code\":%d,\"error\":\"", code);
	for (const char *p = msg; *p != '\0'; p++) {
		if (*p == '"' || *p == '\\')
			putchar('\\');
		if ((unsigned char)*p < 0x20)
			continue;
		putchar(*p);
	}
	printf("\"}\n");
	exit(code);
}

/*
 * A write pair - duty then mode, or mode then duty - is the one place in this
 * program where dying half way through means something. SIGKILL cannot be
 * stopped, and the ORDER of the two writes is what covers that case; these are
 * the signals that can be stopped, and there is no reason to accept a channel
 * left between two writes by a SIGTERM that arrived a millisecond early.
 */
static sigset_t
hold_signals(void)
{
	sigset_t block, old;

	sigemptyset(&block);
	sigaddset(&block, SIGHUP);
	sigaddset(&block, SIGINT);
	sigaddset(&block, SIGQUIT);
	sigaddset(&block, SIGTERM);
	sigaddset(&block, SIGPIPE);
	sigaddset(&block, SIGALRM);
	sigemptyset(&old);
	(void)sigprocmask(SIG_BLOCK, &block, &old);
	return (old);
}

static void
release_signals(const sigset_t *old)
{
	(void)sigprocmask(SIG_SETMASK, old, NULL);
}

/* ------------------------------------------------------------------------ */
/* Identification, through the kernel's lock                                */
/* ------------------------------------------------------------------------ */

struct ident {
	const struct chip *chip;
	uint16_t	devid;		/* raw, unmasked, as read */
	uint16_t	base;		/* HWM logical device base */
	uint16_t	idx_port;	/* base + 5 */
	uint16_t	dat_port;	/* base + 6 */
};

static int
sio_cr_read(int fd, uint8_t ldn, uint8_t cr)
{
	struct superiocmd s;

	memset(&s, 0, sizeof(s));
	s.ldn = ldn;
	s.cr = cr;
	if (ioctl(fd, SUPERIO_CR_READ, &s) != 0) {
		/*
		 * ENOTTY means whatever is behind that path answered, and it is
		 * not superio(4). Worth its own sentence: the alternative is a
		 * user reading "inappropriate ioctl" and concluding the chip is
		 * broken.
		 */
		if (errno == ENOTTY)
			die(EX_NODEV_, "%s does not answer SUPERIO_CR_READ, so it is not "
			    "the superio(4) control device this program needs", DEV_SUPERIO);
		die(EX_INTERNAL_, "SUPERIO_CR_READ ldn 0x%02x cr 0x%02x: %s",
		    ldn, cr, strerror(errno));
	}
	return (s.val);
}

/*
 * The gate. Nothing in this program writes a byte to the chip without having
 * come through here first, in the same run - not through a cached answer, not
 * through a file. A stale identification is how fan registers get written on
 * somebody else's board.
 */
static struct ident
identify(void)
{
	struct ident id;
	int fd, i, enable, lock;

	memset(&id, 0, sizeof(id));

	/*
	 * O_RDWR because SUPERIO_CR_READ is _IOWR. The descriptor is never
	 * written to and no write ioctl is defined in this file.
	 */
	fd = open(DEV_SUPERIO, O_RDWR);
	if (fd < 0) {
		if (errno == ENOENT)
			die(EX_NODEV_, "%s is not there: superio(4) did not attach, so "
			    "nothing on this machine owns the Super I/O configuration port "
			    "and there is no identification this program will trust",
			    DEV_SUPERIO);
		if (errno == EACCES || errno == EPERM)
			die(EX_NOPERM_, "%s: %s - this must run as root", DEV_SUPERIO,
			    strerror(errno));
		die(EX_NODEV_, "%s: %s", DEV_SUPERIO, strerror(errno));
	}

	id.devid = (uint16_t)((sio_cr_read(fd, 0, SIO_REG_DEVID) << 8) |
	    sio_cr_read(fd, 0, SIO_REG_DEVID + 1));

	if (id.devid == 0xffff || id.devid == 0x0000) {
		close(fd);
		die(EX_CHIP_, "the configuration port read back 0x%04x, which is an "
		    "absent chip, not a chip id", id.devid);
	}

	for (i = 0; i < NCHIPS; i++) {
		if ((id.devid & SIO_ID_MASK) == chips[i].id) {
			id.chip = &chips[i];
			break;
		}
	}
	if (id.chip == NULL) {
		for (i = 0; i < NREFUSED; i++) {
			if ((id.devid & SIO_ID_MASK) == refused[i].id) {
				close(fd);
				die(EX_CHIP_, "this is a %s (id 0x%04x), and %s. Nobody has "
				    "measured its fan registers on hardware. Refusing rather "
				    "than guessing", refused[i].name, id.devid, refused[i].why);
			}
		}
		close(fd);
		die(EX_CHIP_, "unknown Super I/O device id 0x%04x. This program drives "
		    "NCT6779D through NCT6799D and nothing else", id.devid);
	}

	/* The table is data, and data can be wrong. See the assertions above. */
	if (id.chip->channels == 0 || id.chip->channels > MAX_CHANNELS) {
		close(fd);
		die(EX_INTERNAL_, "the chip table claims %u channels for a %s, and this "
		    "program only has registers for %u. Refusing rather than indexing "
		    "past the end of them", id.chip->channels, id.chip->name, MAX_CHANNELS);
	}

	/*
	 * A disabled hardware-monitor device answers its window with whatever
	 * happens to be on the bus. Linux's driver enables it here
	 * (nct6775-platform.c:1063-1066) and prints "Sensor is probably
	 * unusable"; we do not, because that is a configuration write and it is
	 * the owner's to make.
	 */
	enable = sio_cr_read(fd, SIO_LD_HWM, SIO_REG_ENABLE);
	if ((enable & 0x01) == 0) {
		close(fd);
		die(EX_CHIP_, "logical device 0x0b is disabled (CR 0x30 = 0x%02x). "
		    "Its window would answer with rubbish. Enabling it is a "
		    "configuration write this program does not make", enable);
	}

	/*
	 * On NCT6791 and later the window can be locked shut by CR 0x28 bit 4.
	 * Same rule: we report it, we do not clear it. The NCT6779D has no such
	 * bit, which is why io_lock is 0 on that row (D: Linux checks this
	 * register for nct6791 and later only).
	 */
	if (id.chip->io_lock) {
		lock = sio_cr_read(fd, 0, SIO_REG_IO_LOCK);
		if ((lock & 0x10) != 0) {
			close(fd);
			die(EX_CHIP_, "the hardware-monitor I/O space is locked "
			    "(CR 0x28 = 0x%02x, bit 4 set). Clearing that bit is a "
			    "configuration write this program does not make", lock);
		}
	}

	id.base = (uint16_t)(((sio_cr_read(fd, SIO_LD_HWM, SIO_REG_ADDR) << 8) |
	    sio_cr_read(fd, SIO_LD_HWM, SIO_REG_ADDR + 1)) & IOREGION_ALIGNMENT);
	close(fd);

	if (id.base == 0)
		die(EX_CHIP_, "logical device 0x0b reports base address 0, which is "
		    "not a window");

	/* See HWM_BASE_MIN. A window down here is not a window. */
	if (id.base < HWM_BASE_MIN)
		die(EX_CHIP_, "logical device 0x0b reports base address 0x%03x, which "
		    "would put this program's index and data ports at 0x%03x and 0x%03x "
		    "- inside the fixed motherboard I/O range that holds the DMA and "
		    "interrupt controllers and the Super I/O configuration ports "
		    "themselves. No hardware monitor lives there. Refusing to write "
		    "anything", id.base, (unsigned)(id.base + IOREGION_OFFSET),
		    (unsigned)(id.base + IOREGION_OFFSET + 1));

	id.idx_port = (uint16_t)(id.base + IOREGION_OFFSET);
	id.dat_port = (uint16_t)(id.base + IOREGION_OFFSET + 1);
	return (id);
}

static void
must_channel_exist(const struct ident *id, unsigned channel)
{
	if (channel > id->chip->channels)
		die(EX_CHANNEL_, "channel %u: a %s has %u", channel,
		    id->chip->name, id->chip->channels);
}

/* ------------------------------------------------------------------------ */
/* The hardware-monitor window                                              */
/* ------------------------------------------------------------------------ */

static void
io_open(void)
{
	iofd = open(DEV_IO, O_RDWR);
	if (iofd < 0) {
		if (errno == ENOENT)
			die(EX_NODEV_, "%s is not there, so this process cannot be given "
			    "I/O privilege and no fan register can be reached", DEV_IO);
		if (errno == EACCES || errno == EPERM)
			die(EX_NOPERM_, "%s: %s - this must run as root, and "
			    "kern.securelevel must be below 1", DEV_IO, strerror(errno));
		die(EX_NODEV_, "%s: %s", DEV_IO, strerror(errno));
	}
}

/*
 * The bank is re-selected before every index write. Caching it would be
 * correct in a program that owned the window; this one shares it with a kernel
 * driver that sets the bank to 4 whenever somebody reads a sysctl, so the bank
 * we left is not the bank we find.
 */
static void
ec_select(const struct ident *id, uint16_t reg)
{
	outb(id->idx_port, REG_BANK_SELECT);
	outb(id->dat_port, (uint8_t)(reg >> 8));
	outb(id->idx_port, (uint8_t)(reg & 0xff));
}

static uint8_t
ec_read_raw(const struct ident *id, uint16_t reg)
{
	ec_select(id, reg);
	return (inb(id->dat_port));
}

/*
 * Configuration registers are read twice and must agree. A disagreement is
 * not noise: it means ncthwm(4) moved the index between our two accesses, and
 * a number read across that is a number from the wrong register.
 */
static uint8_t
ec_read(const struct ident *id, uint16_t reg)
{
	uint8_t a = 0, b = 0;
	int try;

	for (try = 0; try < 2; try++) {
		a = ec_read_raw(id, reg);
		b = ec_read_raw(id, reg);
		if (a == b)
			return (a);
	}
	die(EX_READBACK_, "register 0x%03x read back differently twice in a row "
	    "(0x%02x then 0x%02x). Something else is using the window at "
	    "0x%03x/0x%03x - most likely a reader of dev.ncthwm.*",
	    reg, a, b, id->idx_port, id->dat_port);
	return (0);	/* not reached */
}

/*
 * Tachometers are word-sized: high byte at reg, low byte at reg + 1, each with
 * its own index write (D, nct6775-platform.c:333-338; ncthwm.c does the same
 * with 0xc0 then 0xc1).
 */
static uint16_t
ec_read16(const struct ident *id, uint16_t reg)
{
	uint16_t hi, lo;

	hi = ec_read_raw(id, reg);
	lo = ec_read_raw(id, (uint16_t)(reg + 1));
	return ((uint16_t)((hi << 8) | lo));
}

/*
 * A speed, and whether the chip said the same thing twice.
 *
 * This does not die on a disagreement, because a fan speed can legitimately
 * change and a stopped daemon is worse than a doubtful number. But it does not
 * hide one either: a collision in this window fabricates plausible speeds -
 * on the reference machine a simulated collision on register 0x4c0 produced a
 * confident "4096 rpm" from a register file that held nothing of the kind
 * (V, 2026-09-22) - and the layer above uses rpm to decide whether a fan is
 * turning. So the answer carries both reads when they differ, and says which
 * happened. These registers are updated by the chip a few times a second, so
 * two reads microseconds apart agreeing is the normal case.
 */
static uint16_t
ec_tach(const struct ident *id, uint16_t reg, int *stable, uint16_t *first)
{
	uint16_t a, b;

	a = ec_read16(id, reg);
	b = ec_read16(id, reg);
	*first = a;
	*stable = (a == b);
	return (b);
}

/*
 * A temperature value register, byte or word, and whether the chip said the
 * same thing twice. Returns 1 when it did.
 *
 * This is ec_read()'s discipline - read it twice, and twice again, and only
 * believe an agreement - with ec_tach()'s ending rather than ec_read()'s. It
 * does not die on a disagreement, and the difference is deliberate:
 *
 *   - ec_read() is for CONFIGURATION registers, which cannot change while
 *     nobody writes them, so two different answers can only be a collision in
 *     the window and stopping is right.
 *   - a temperature CAN change between two reads. It changes slowly - these
 *     registers step half a degree - so two reads microseconds apart
 *     disagreeing is rare, and it is still not proof of a collision.
 *
 * Killing the whole command over one flickering input would lose the other
 * six rows, which is a worse answer than a table with one row marked
 * unstable. So the row carries both reads and no temperature at all: an
 * unstable value is reported AS unstable and never as a reading, because a
 * collision in this window fabricates plausible numbers - a simulated one on
 * a tachometer register produced a confident 4096 rpm out of nothing
 * (V, 2026-09-22, recorded above ec_tach).
 */
static int
ec_read_value(const struct ident *id, uint16_t reg, int word, uint16_t *out,
    uint16_t *first)
{
	uint16_t a = 0, b = 0;
	int try;

	for (try = 0; try < 2; try++) {
		a = word ? ec_read16(id, reg) :
		    (uint16_t)(ec_read_raw(id, reg) << 8);
		b = word ? ec_read16(id, reg) :
		    (uint16_t)(ec_read_raw(id, reg) << 8);
		if (a == b) {
			*out = a;
			*first = a;
			return (1);
		}
	}
	*out = b;
	*first = a;
	return (0);
}

static void
ec_write(const struct ident *id, uint16_t reg, uint8_t val)
{
	ec_select(id, reg);
	outb(id->dat_port, val);
}

/*
 * Write, then prove it. A chip that accepts a byte and does not hold it is a
 * chip we are not talking to, and carrying on would mean believing a fan is at
 * 40% because we said so.
 */
static void
ec_write_verify(const struct ident *id, uint16_t reg, uint8_t val, uint8_t mask)
{
	uint8_t got = 0;
	int try;

	for (try = 0; try < 2; try++) {
		ec_write(id, reg, val);
		got = ec_read(id, reg);
		if ((got & mask) == (val & mask))
			return;
	}
	die(EX_READBACK_, "wrote 0x%02x to register 0x%03x and read back 0x%02x "
	    "(mask 0x%02x), twice", val, reg, got, mask);
}

/* ------------------------------------------------------------------------ */
/* The lock                                                                 */
/* ------------------------------------------------------------------------ */

/*
 * Non-blocking on purpose. One command, one answer: a second copy is told it
 * is busy and exits, and the caller decides whether to come back. Blocking
 * here would be a sleep, and a program that can sleep in the fan path is a
 * program that can be waiting when something needs it.
 *
 * Taken before /dev/io is opened, so a run that is only going to be told it is
 * busy never acquires I/O privilege at all.
 */
static int
window_lock(void)
{
	int fd;

	fd = open(LOCK_FILE, O_RDWR | O_CREAT, 0600);
	if (fd < 0)
		die(EX_INTERNAL_, "%s: %s", LOCK_FILE, strerror(errno));
	if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
		if (errno == EWOULDBLOCK)
			die(EX_BUSY_, "another fanctl-io holds %s", LOCK_FILE);
		die(EX_INTERNAL_, "flock %s: %s", LOCK_FILE, strerror(errno));
	}
	return (fd);
}

/* ------------------------------------------------------------------------ */
/* Conversions                                                              */
/* ------------------------------------------------------------------------ */

static uint8_t
pct_to_raw(unsigned pct)
{
	return ((uint8_t)((pct * 255u + 50u) / 100u));
}

static unsigned
raw_to_pct(uint8_t raw)
{
	return ((raw * 100u + 127u) / 255u);
}

/*
 * (REG_FAN_MODE >> 4) & 7, decoded exactly as reg_to_pwm_enable() does
 * (D, nct6775-core.c:900-905): nibble 0 with duty 255 is not "manual at full"
 * but the chip's own "PWM control off", which is the same output and a
 * different statement about who is in charge.
 */
static const char *
mode_name(uint8_t nibble, uint8_t duty)
{
	switch (nibble) {
	case 0:	return (duty == 255 ? "off" : "manual");
	case 1:	return ("thermal-cruise");
	case 2:	return ("speed-cruise");
	case 3:	return ("smartfan-iii");
	case 4:	return ("smartfan-iv");
	default: return ("reserved");
	}
}

/*
 * A temperature register, in half-degree steps.
 *
 * LM75_TEMP_FROM_REG (D, drivers/hwmon/lm75.h): a signed 9-bit value sitting
 * in the top of a 16-bit word, half a degree per step, ((s16)reg / 128) * 500
 * millidegrees. The division is the source's own, and its comment says it is
 * there to guarantee an arithmetic shift - which is what it is NOT: C division
 * truncates towards zero and a shift rounds down, so the two differ by one
 * step for a negative value that is not an exact multiple. This follows the
 * division, because following the driver everybody else's numbers come from
 * matters more than which of the two is prettier, and the difference is half a
 * degree below freezing, which is not where this plugin makes decisions.
 *
 * A byte-sized register is left-aligned into the same word by the caller,
 * exactly as the driver does (D, core.c:1211-1223), so it decodes through
 * here as whole degrees - the low bit that would carry the half is simply not
 * there. The answer says which registers are word-sized and prints the
 * resolution beside each row rather than leaving somebody to infer it from a
 * number that always ends in .0.
 */
static int
temp_half_steps(uint16_t word)
{
	return ((int)(int16_t)word / 128);
}

/*
 * Tenths of a degree as a JSON number, sign and all, with no floating point
 * anywhere near it. printf("%.1f") on a value that is exactly representable
 * would be fine; a decode that never leaves integers is one less thing to
 * argue about when a reading looks wrong.
 */
static void
print_tenths(int tenths)
{
	int magnitude = tenths < 0 ? -tenths : tenths;

	printf("%s%d.%d", tenths < 0 ? "-" : "", magnitude / 10, magnitude % 10);
}

/*
 * What a source number is called on this chip, or NULL when the chip has no
 * such source and when the register is assigned to nothing.
 */
static const char *
temp_source_name(const struct temp_map *map, uint8_t src)
{
	if (src == 0 || (int)src >= map->nlabels)
		return (NULL);
	if (map->label[src][0] == '\0')
		return (NULL);
	return (map->label[src]);
}

/*
 * WHAT THE FAR END OF THE WIRE IS TOUCHING, for the two kinds of source where
 * the answer is not a board designer's private decision.
 *
 * A PECI agent is the CPU reporting its own die over its own bus, and a PCH
 * forward is the chipset passing on something it already knows. Nothing on
 * the board decides what those mean, so they are the only sources this
 * program will name.
 *
 * Everything else is a PIN - SYSTIN, CPUTIN and AUXTIN0..3 - and a pin is
 * unknown, including the one called CPUTIN. That name is the board designer's
 * intention, not a measurement, and on a repurposed appliance the intention
 * belonged to a vendor who is gone.
 *
 * sensors.py keeps the same table (NCT_ATTACHED) and the copy up there is the
 * one the page uses; this one is here because this program is also run by
 * hand, and a row that says nothing about attachment invites the reader to
 * assume. The two must say the same thing, and the one that matters if they
 * ever drift is the one in sensors.py.
 */
static const char *
temp_attached(uint8_t src)
{
	switch (src) {
	case 16:			/* PECI Agent 0 */
	case 17:			/* PECI Agent 1 */
	case 20:			/* PCH_CPU_TEMP */
		return ("cpu_die");
	case 19:			/* PCH_CHIP_TEMP */
		return ("chipset_die");
	case 22: case 23: case 24: case 25:	/* PCH_DIM0..3_TEMP */
		return ("memory_module");
	default:
		return ("unknown");
	}
}

/* ------------------------------------------------------------------------ */
/* The floor, read by this program and by nothing else                      */
/* ------------------------------------------------------------------------ */

struct floor {
	unsigned	pct;		/* what applies, after clamping */
	unsigned	file_pct;	/* what the file asked for */
	int		from_file;	/* the file had a rule for this channel */
	const char     *source;		/* "hard", "file", "file-clamped", "file-rejected" */
};

/*
 * Format, one rule per line:
 *
 *	# comment
 *	default 35
 *	2 55
 *
 * A malformed line rejects the WHOLE file and the hard floor applies. Failing
 * closed matters here: a typo in a config file must not be a way to quietly
 * lower the number that keeps a fan turning. Verified on the reference machine
 * (V, 2026-09-22): "default 10" is reported as file-clamped and 30% still
 * applies; "default 35 oops" is rejected as a whole file, proved by 32% being
 * accepted afterwards where a silently-read 35 would have refused it.
 */
static struct floor
floor_for(unsigned channel)
{
	struct floor f;
	FILE *fp;
	char line[256];
	unsigned found = 0;
	int have_default = 0, have_channel = 0;

	f.pct = FANCTL_HARD_FLOOR_PCT;
	f.file_pct = 0;
	f.from_file = 0;
	f.source = "hard";

	fp = fopen(FLOOR_FILE, "r");
	if (fp == NULL)
		return (f);	/* no file is not an error; the hard floor is the answer */

	while (fgets(line, sizeof(line), fp) != NULL) {
		char key[32], trailing[2];
		unsigned val;
		char *p = line;
		int n;

		/*
		 * A line longer than this buffer arrives in pieces, and the
		 * second piece would be parsed as a rule of its own - so the
		 * tail of a long comment could become a floor. Reject instead.
		 */
		if (strchr(line, '\n') == NULL && getc(fp) != EOF) {
			fclose(fp);
			f.source = "file-rejected";
			return (f);
		}

		while (*p == ' ' || *p == '\t')
			p++;
		if (*p == '#' || *p == '\n' || *p == '\r' || *p == '\0')
			continue;

		/* %1s catches anything after the value, so "default 35 oops"
		 * is a rejected file rather than a silently accepted 35. */
		n = sscanf(p, "%31s %u %1s", key, &val, trailing);
		if (n != 2 || val > 100) {
			fclose(fp);
			f.source = "file-rejected";
			return (f);
		}
		if (strcmp(key, "default") == 0) {
			if (!have_channel) {
				found = val;
				have_default = 1;
			}
		} else {
			char *end;
			unsigned long ch = strtoul(key, &end, 10);

			if (*end != '\0' || ch == 0 || ch > MAX_CHANNELS) {
				fclose(fp);
				f.source = "file-rejected";
				return (f);
			}
			if ((unsigned)ch == channel) {
				found = val;
				have_channel = 1;
			}
		}
	}
	fclose(fp);

	if (!have_default && !have_channel) {
		/* A file that exists and says nothing about this channel. The
		 * hard floor applies, and the answer should not read as if
		 * there were no file at all. */
		f.source = "file-silent";
		return (f);
	}

	f.from_file = 1;
	f.file_pct = found;
	if (found < FANCTL_HARD_FLOOR_PCT) {
		f.source = "file-clamped";	/* the file asked for less; it does not get less */
		return (f);
	}
	f.pct = found;
	f.source = "file";
	return (f);
}

/* ------------------------------------------------------------------------ */
/* Saved original state                                                     */
/* ------------------------------------------------------------------------ */

/*
 * What the channel looked like before this plugin took it over, written once
 * and never rewritten, so that release restores what was found and not the last
 * thing we happened to set.
 *
 * Three limits on that sentence, all of them the reason for the fields below:
 *
 *  - It only holds while the file survives. It lives in /var/db, which is a ZFS
 *    dataset on the reference machine (V, 2026-09-22: /var is part of
 *    zroot/ROOT/default, no memory disk) - but OPNsense can be configured to
 *    put /var on a RAM disk, and then this file is gone at every reboot while
 *    the duty we wrote is still latched in the chip, because a Super I/O fan
 *    register is not necessarily cleared by a warm reboot. The next `duty` then
 *    captures OUR value as the baseline. Demonstrated end to end against a
 *    simulated chip (V, 2026-09-22): a genuine SmartFan IV baseline of "4 255"
 *    became "0 102" after one state-file loss, and release then reported
 *    restoring a "saved-original" of manual 40%. The rc script's pre-start
 *    restore is what normally prevents this, and it cannot when the file it
 *    reads is the file that was lost. Nothing in this program can detect it.
 *    What this program can do is refuse to hand a channel back to a duty that
 *    would stall it, which is what the floor check in cmd_release does.
 *
 *  - It is fingerprinted with the device id, because the disk in an appliance
 *    is the part that gets moved to another appliance. A record captured on one
 *    chip is not applied to a different one; it is reported as unusable and the
 *    failsafe takes over. Two boards of the SAME model are indistinguishable
 *    here, and this program does not claim otherwise.
 *
 *  - It holds the three-bit mode nibble, not the whole register. See MODE_MAX:
 *    bit 7 of the fan-mode register is cleared by both of our writes and cannot
 *    be put back.
 */
struct saved {
	int	valid;
	int	fingerprinted;	/* the record carried a device id */
	int	wrong_chip;	/* it carried one, and it was not this chip */
	uint8_t	mode;		/* the three-bit nibble, not the whole register */
	uint8_t	duty;
	uint16_t devid;
};

static void
state_path(unsigned channel, char *buf, size_t len)
{
	snprintf(buf, len, "%s/ch%u.state", STATE_DIR, channel);
}

static struct saved
state_load(unsigned channel, const struct ident *id)
{
	struct saved s;
	char path[256];
	FILE *fp;
	unsigned mode, duty, devid;
	int n;

	memset(&s, 0, sizeof(s));
	s.duty = 255;

	state_path(channel, path, sizeof(path));
	fp = fopen(path, "r");
	if (fp == NULL)
		return (s);
	n = fscanf(fp, "%u %u %x", &mode, &duty, &devid);
	fclose(fp);

	if (n < 2 || mode > MODE_MAX || duty > 255)
		return (s);
	if (n >= 3) {
		s.fingerprinted = 1;
		s.devid = (uint16_t)devid;
		if (s.devid != id->devid) {
			s.wrong_chip = 1;
			return (s);
		}
	}
	s.valid = 1;
	s.mode = (uint8_t)mode;
	s.duty = (uint8_t)duty;
	return (s);
}

/*
 * Returns 1 if this call recorded the original, 0 if a record already existed,
 * -1 if it could not be recorded. A 1 means the bytes are on the disk: the
 * write, the flush and the fsync all succeeded. An earlier version checked only
 * the open, so a full or read-only /var produced "original_recorded_now":true
 * over a file that was never written - the one claim in this program that must
 * not be made on trust, because the whole of `release` is built on it.
 */
static int
state_save_once(unsigned channel, uint8_t mode, uint8_t duty, uint16_t devid)
{
	char path[256];
	FILE *fp;
	int fd;

	(void)mkdir(STATE_DIR, 0700);
	state_path(channel, path, sizeof(path));

	/* O_EXCL: the first save wins, for as long as the file survives. */
	fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
	if (fd < 0)
		return (errno == EEXIST ? 0 : -1);
	fp = fdopen(fd, "w");
	if (fp == NULL) {
		close(fd);
		(void)unlink(path);
		return (-1);
	}
	if (fprintf(fp, "%u %u %04x\n", mode, duty, devid) < 0 ||
	    fflush(fp) != 0 || fsync(fd) != 0 || fclose(fp) != 0) {
		/*
		 * Half a record is worse than none: "0 2" parses perfectly and
		 * asks release to put a fan at 0.8%. Unlink it and fail.
		 */
		(void)unlink(path);
		return (-1);
	}
	return (1);
}

static void
state_forget(unsigned channel)
{
	char path[256];

	state_path(channel, path, sizeof(path));
	(void)unlink(path);
}

/* ------------------------------------------------------------------------ */
/* Commands                                                                 */
/* ------------------------------------------------------------------------ */

static void
print_chip(const struct ident *id)
{
	printf("\"chip\":{\"name\":\"%s\",\"devid\":\"0x%04x\",\"channels\":%u,"
	    "\"hwm_base\":\"0x%03x\",\"index_port\":\"0x%03x\",\"data_port\":\"0x%03x\"}",
	    id->chip->name, id->devid, id->chip->channels,
	    id->base, id->idx_port, id->dat_port);
}

static void
cmd_identify(void)
{
	struct ident id = identify();

	printf("{\"ok\":true,\"code\":0,\"command\":\"identify\",");
	print_chip(&id);
	printf(",\"hard_floor_pct\":%u}\n", FANCTL_HARD_FLOOR_PCT);
}

static void
cmd_tach(unsigned channel)
{
	struct ident id;
	int lock, stable;
	uint16_t rpm, first;

	id = identify();
	must_channel_exist(&id, channel);

	lock = window_lock();
	io_open();
	rpm = ec_tach(&id, reg_fan[channel - 1], &stable, &first);
	close(lock);

	/*
	 * Zero is reported as zero and nothing is concluded from it. Four of
	 * the five channels on the reference appliance read 0 for three minutes
	 * (V) while the chassis certainly has more than one fan: a tachometer
	 * that is not wired to this chip and a fan that has stopped produce the
	 * same number, and this program cannot tell them apart.
	 */
	printf("{\"ok\":true,\"code\":0,\"command\":\"tach\",\"channel\":%u,"
	    "\"rpm\":%u,\"rpm_is_direct\":true,\"rpm_stable\":%s",
	    channel, rpm, stable ? "true" : "false");
	if (!stable)
		printf(",\"rpm_first_read\":%u,\"note\":\"Two reads of this tachometer "
		    "disagreed. Something else may be using the window; this number is "
		    "not a measurement anybody should act on.\"", first);
	printf("}\n");
}

/*
 * Everything `status` knows about one channel, read under the lock before
 * anything is printed. See die(): one command, one JSON object, and that is
 * only true if the reading finishes before the printing starts.
 */
struct snapshot {
	uint16_t	rpm;
	uint16_t	rpm_first;
	int		rpm_stable;
	uint8_t		mode;
	uint8_t		duty;
	uint8_t		out;
	int		has_readback;
	uint8_t		crit_en;
	uint8_t		crit;
};

static void
cmd_status(void)
{
	struct ident id;
	struct snapshot snap[MAX_CHANNELS];
	int lock, i, n;
	uint8_t pwm_mode_reg;

	id = identify();
	n = id.chip->channels;

	lock = window_lock();
	io_open();
	memset(snap, 0, sizeof(snap));
	for (i = 0; i < n; i++) {
		struct snapshot *c = &snap[i];

		c->rpm = ec_tach(&id, reg_fan[i], &c->rpm_stable, &c->rpm_first);
		c->mode = (uint8_t)((ec_read(&id, reg_fan_mode[i]) >> 4) & 7);
		c->duty = ec_read(&id, reg_pwm[i]);
		c->crit_en = ec_read(&id, reg_crit_en[i]);
		c->crit = ec_read(&id, reg_crit[i]);
		c->has_readback = (reg_pwm_read[i] != reg_pwm[i]);
		c->out = c->has_readback ? ec_read(&id, reg_pwm_read[i]) : c->duty;
	}
	/*
	 * Only channel 1 has an output-type bit on this family
	 * (NCT6776_REG_PWM_MODE = { 0x04, 0, 0, 0, 0, 0, 0 }, D
	 * nct6775-core.c:277). A DC channel drives the fan by voltage, and a
	 * duty on it does not mean what a duty on a PWM channel means.
	 */
	pwm_mode_reg = ec_read(&id, REG_PWM_MODE);
	close(lock);

	printf("{\"ok\":true,\"code\":0,\"command\":\"status\",");
	print_chip(&id);
	printf(",\"hard_floor_pct\":%u,\"channels\":[", FANCTL_HARD_FLOOR_PCT);

	for (i = 0; i < n; i++) {
		const struct snapshot *c = &snap[i];
		unsigned ch = (unsigned)i + 1;
		struct floor f = floor_for(ch);		/* files, read outside the lock */
		struct saved s = state_load(ch, &id);

		printf("%s{\"channel\":%u,\"rpm\":%u,\"rpm_stable\":%s,"
		    "\"mode\":%u,\"mode_name\":\"%s\","
		    "\"duty_raw\":%u,\"duty_pct\":%u,",
		    i == 0 ? "" : ",", ch, c->rpm, c->rpm_stable ? "true" : "false",
		    c->mode, mode_name(c->mode, c->duty), c->duty, raw_to_pct(c->duty));
		if (c->has_readback)
			printf("\"output_raw\":%u,\"output_pct\":%u,",
			    c->out, raw_to_pct(c->out));
		else
			printf("\"output_raw\":null,\"output_pct\":null,");
		printf("\"floor_pct\":%u,\"floor_source\":\"%s\",\"floor_file_pct\":%u,",
		    f.pct, f.source, f.file_pct);
		printf("\"critical_duty_armed\":%s,\"critical_duty_raw\":%u,",
		    (c->crit_en & CRIT_PWM_ENABLE_MASK) ? "true" : "false", c->crit);
		printf("\"saved_original\":%s", s.valid ? "true" : "false");
		if (s.valid)
			printf(",\"saved_mode\":%u,\"saved_duty_raw\":%u,"
			    "\"saved_fingerprint\":%s", s.mode, s.duty,
			    s.fingerprinted ? "true" : "false");
		else if (s.wrong_chip)
			printf(",\"saved_wrong_chip\":\"0x%04x\"", s.devid);
		printf("}");
	}

	printf("],\"channel1_output\":\"%s\"",
	    (pwm_mode_reg & PWM_MODE_MASK) ? "dc" : "pwm");
	printf(",\"note\":\"A tachometer reading 0 may be a stopped fan or a fan with no "
	    "tachometer wired to this chip. This program cannot tell those apart. A duty "
	    "written here is latched in the chip: nothing expires it, and if the process "
	    "that wrote it is killed it stays until something runs release.\"}\n");
}

/*
 * Everything one temperature input says about itself, read under the lock
 * before anything is printed - same rule as `status`, and for the same reason:
 * one command, one JSON object, which is only true if the reading finishes
 * before the printing starts.
 */
struct temp_snapshot {
	uint8_t		src_raw;	/* the source register, whole */
	uint8_t		src;		/* its low five bits */
	int		have_cfg;
	uint8_t		cfg;
	uint16_t	value;
	uint16_t	value_first;
	int		stable;
	int		have_offset;
	uint16_t	offset_reg;
	uint8_t		offset;
};

/*
 * `temp` - every temperature source the chip has, or one of them.
 *
 * The whole table is READ either way, and only the printing is narrowed. Two
 * reasons, and the second is the one that matters: the per-row `selected_by`
 * is a cross-reference over every source register, so a single row cannot be
 * answered without reading them all; and one code path that always does the
 * same accesses is one behaviour to check rather than two.
 *
 * Nothing here is refused on the value. A row that cannot be turned into a
 * temperature is printed with its raw bytes and the reason it is not one -
 * unassigned, disabled-in-chip, unstable, implausible - because the register
 * existing and the register meaning something are different facts, and a page
 * that cannot tell them apart shows a hole where it should show an answer.
 */
static void
cmd_temp(unsigned only)
{
	struct ident id;
	const struct temp_map *map = NULL;
	struct temp_snapshot snap[MAX_TEMP_INPUTS];
	int lock, i, j, n, printed = 0;
	uint8_t vbat, diode;

	id = identify();

	for (i = 0; i < NTEMP_MAPS; i++)
		if ((id.devid & SIO_ID_MASK) == temp_maps[i].id)
			map = &temp_maps[i];
	if (map == NULL)
		die(EX_CHIP_, "this is a %s (id 0x%04x). Its fan registers are the "
		    "ones this program drives, but its temperature sources are not the "
		    "ones it has a map for: the label tables and the source masks differ "
		    "across this family, and a row naming the wrong sensor is how a fan "
		    "curve ends up following something nobody checked. Only the NCT6779D "
		    "map has been read on hardware. Refusing rather than guessing",
		    id.chip->name, id.devid);

	n = map->ninputs;
	if (only != 0 && only > (unsigned)n)
		die(EX_USAGE_, "temperature input %u: a %s has %d. They are numbered "
		    "from 1 in the order this command prints them, and they are not fan "
		    "channels", only, map->name, n);

	lock = window_lock();
	io_open();
	memset(snap, 0, sizeof(snap));
	/*
	 * Two chip-wide bytes first, while the window is ours: which inputs are
	 * wired for a diode, and the battery-monitor byte that sits beside it
	 * and is reported only because it is the other half of that pair in
	 * every description of these registers.
	 */
	vbat = ec_read(&id, REG_VBAT);
	diode = ec_read(&id, REG_DIODE);
	for (i = 0; i < n; i++) {
		const struct temp_input *in = &map->inputs[i];
		struct temp_snapshot *s = &snap[i];

		/*
		 * `src &= 0x1f` (D, core.c:4188). The other three bits are not
		 * part of the source number; the whole byte is reported too, so
		 * that a board doing something with them is visible rather than
		 * masked away in silence.
		 */
		s->src_raw = ec_read(&id, in->src_reg);
		s->src = (uint8_t)(s->src_raw & 0x1f);

		if (in->cfg_reg != 0) {
			s->have_cfg = 1;
			s->cfg = ec_read(&id, in->cfg_reg);
		}

		s->stable = ec_read_value(&id, in->reg, in->word, &s->value,
		    &s->value_first);

		if (s->src >= 1 && (int)s->src <= map->noffsets) {
			s->have_offset = 1;
			s->offset_reg = map->offset[s->src - 1];
			s->offset = ec_read(&id, s->offset_reg);
		}
	}
	close(lock);

	printf("{\"ok\":true,\"code\":0,\"command\":\"temp\",");
	print_chip(&id);
	printf(",\"map\":\"%s\",\"vbat_raw\":\"0x%02x\",\"diode_raw\":\"0x%02x\","
	    "\"diode_mask\":\"0x%02x\",\"inputs\":[", map->name, vbat, diode,
	    map->diode_mask);

	for (i = 0; i < n; i++) {
		const struct temp_input *in = &map->inputs[i];
		const struct temp_snapshot *s = &snap[i];
		const char *name, *reason = NULL;
		int tenths = 0, k;

		if (only != 0 && (unsigned)(i + 1) != only)
			continue;

		printf("%s{\"index\":%d,\"reg\":\"0x%03x\",\"word\":%s,\"role\":\"%s\"",
		    printed == 0 ? "" : ",", i + 1, in->reg,
		    in->word ? "true" : "false", in->role);
		if (in->fan_channel != 0)
			printf(",\"fan_channel\":%u", in->fan_channel);
		else
			printf(",\"fan_channel\":null");

		name = temp_source_name(map, s->src);
		printf(",\"src_reg\":\"0x%03x\",\"src_raw\":\"0x%02x\",\"src\":%u,"
		    "\"src_name\":%s%s%s,\"src_known\":%s,\"present\":%s",
		    in->src_reg, s->src_raw, s->src,
		    name == NULL ? "null" : "\"", name == NULL ? "" : name,
		    name == NULL ? "" : "\"",
		    (s->src < 32 && ((map->src_mask >> s->src) & 1u)) ? "true" : "false",
		    s->src != 0 ? "true" : "false");

		if (s->have_cfg)
			printf(",\"config_reg\":\"0x%03x\",\"config_raw\":\"0x%02x\","
			    "\"enabled\":%s", in->cfg_reg, s->cfg,
			    (s->cfg & 0x01) ? "false" : "true");
		else
			/*
			 * No `enabled` key at all, rather than a true nobody
			 * measured. The five fan-control registers have no
			 * configuration register on this chip, so whether the
			 * source behind one is switched off is not a thing this
			 * program can read. A caller that defaults a missing
			 * key to true is making that choice with its eyes open.
			 */
			printf(",\"config_reg\":null,\"config_raw\":null");

		if (in->word)
			printf(",\"raw\":\"0x%04x\"", s->value);
		else
			printf(",\"raw\":\"0x%02x\"", (unsigned)(s->value >> 8));
		/*
		 * In the register's own width, like `raw` above it. The same
		 * left-alignment that made the all-ones test miss a byte
		 * register would print a byte's first read as "0xff00" beside
		 * a `raw` of "0xff", and two hex strings of different widths
		 * in one row is an invitation to read one of them wrong.
		 */
		if (!s->stable) {
			if (in->word)
				printf(",\"raw_first_read\":\"0x%04x\"", s->value_first);
			else
				printf(",\"raw_first_read\":\"0x%02x\"",
				    (unsigned)(s->value_first >> 8));
		}
		printf(",\"stable\":%s,\"resolution_c\":%s",
		    s->stable ? "true" : "false", in->word ? "0.5" : "1.0");

		/*
		 * Decoded first, refused after. The arithmetic is on bytes this
		 * program has already read and it cannot fail; doing it up here
		 * means every refusal below can print what the bytes WOULD have
		 * said, which is the evidence somebody needs to check the
		 * refusal rather than trust it.
		 */
		tenths = temp_half_steps(s->value) * 5;

		if (s->src == 0)
			reason = "unassigned";
		else if (s->have_cfg && (s->cfg & 0x01) != 0)
			reason = "disabled-in-chip";
		else if (!s->stable)
			reason = "unstable";
		else if (s->src > map->pins &&
		    (s->value == 0x0000 ||
		     s->value == (uint16_t)(in->word ? 0xffff : 0xff00)))
			/*
			 * A SOURCE THAT IS NOT A PIN AND HAS NOTHING TO SAY.
			 *
			 * SYSTIN and the AUXTINs are pins: the chip measures a
			 * wire and whatever comes back is what the wire did. A
			 * PECI agent, an SMBus master and a PCH forward are not
			 * - they are the chip repeating a number some other
			 * device gave it, and until that device answers there is
			 * no number. The register then holds its reset value.
			 *
			 * All zeroes decodes to exactly 0.0 C, and that is the
			 * single most dangerous thing this command could print:
			 * a row that says PECI Agent 0, attached cpu_die, 0.0 C
			 * is authoritative, wrong, and cold - and a curve
			 * following it would hold a fan at its minimum for ever
			 * while the die cooked.
			 *
			 * V, 2026-09-22, this appliance: registers 0x027 and
			 * 0x150 are assigned to PECI Agent 0 and 1 and both read
			 * zero - 0x00 and 0x0000 - continuously, while
			 * dev.cpu.0-3.temperature swung between 54 and 80 C in
			 * the same minutes. Whatever those two registers are,
			 * they are not this CPU's temperature.
			 *
			 * THE ALL-ONES PATTERN IS THE REGISTER'S OWN WIDTH, and
			 * getting that wrong is how this rule was first written:
			 * ec_read_value() left-aligns a byte register into the
			 * word, so a byte holding 0xff arrives here as 0xff00
			 * and NEVER as 0xffff. A test against 0xffff alone is
			 * dead code on a byte register - and on this board the
			 * byte register 0x027 is one of the two assigned to a
			 * PECI agent, so the one row the rule most needed to
			 * cover was the row it could not match. 0xff00 decodes
			 * to -1.0 C, which is inside the plausible band, so the
			 * row came out as a reading: "PECI Agent 0, cpu_die,
			 * -1.0 C" (V, 2026-09-22, a test build of this file with
			 * 0xff00 forced into that register after the reads -
			 * refused as source-silent once this line was widened,
			 * while the word register holding 0xffff was refused
			 * either way).
			 *
			 * The all-ones half of the test is reasoning and not a
			 * measurement (R): a bus that is not answering commonly
			 * reads back all ones, and neither pattern is a
			 * temperature from a device that replied. What IS
			 * measured is that this line now catches it in both
			 * widths.
			 *
			 * A PIN reading zero is deliberately NOT caught here. A
			 * pin can honestly be cold, nothing about a pin has a
			 * reset value, and the layer above convicts a reading
			 * that never moves with half an hour of evidence rather
			 * than with a constant in this file.
			 */
			reason = "source-silent";
		else if (tenths < TEMP_PLAUSIBLE_LOW_C * 10 ||
		    tenths > TEMP_PLAUSIBLE_HIGH_C * 10)
			reason = "implausible";

		if (reason == NULL) {
			printf(",\"celsius\":");
			print_tenths(tenths);
		} else {
			printf(",\"celsius\":null,\"unreadable\":\"%s\"", reason);
			/*
			 * `decoded_c` is never a temperature and must never be
			 * read as one - it is what this decode makes of bytes
			 * that were refused, printed so that the refusal can be
			 * checked. It is left out of the one case where there is
			 * no single value to decode: two reads that disagreed.
			 */
			if (strcmp(reason, "unstable") != 0) {
				printf(",\"decoded_c\":");
				print_tenths(tenths);
			}
		}

		if (s->src == 0)
			printf(",\"type\":null,\"pin\":null");
		else if (s->src <= map->pins)
			printf(",\"type\":\"%s\",\"pin\":true",
			    ((map->diode_mask >> (s->src - 1)) & 0x01) == 0 ?
			    "thermistor" : "unsettled");
		else
			printf(",\"type\":\"not-a-pin\",\"pin\":false");

		printf(",\"attached\":\"%s\"",
		    s->src == 0 ? "unknown" : temp_attached(s->src));

		if (s->have_offset)
			printf(",\"offset_reg\":\"0x%03x\",\"offset_raw\":\"0x%02x\","
			    "\"offset_c\":%d", s->offset_reg, s->offset,
			    (int)(int8_t)s->offset);
		else
			printf(",\"offset_reg\":null,\"offset_raw\":null,"
			    "\"offset_c\":null");

		/*
		 * Which fan channels are watching this same source. For a
		 * fan-control row this includes its own channel, because that
		 * row IS that channel's selector.
		 */
		printf(",\"selected_by\":[");
		for (j = 0, k = 0; j < n; j++)
			if (map->inputs[j].fan_channel != 0 && s->src != 0 &&
			    snap[j].src == s->src)
				printf("%s%u", k++ == 0 ? "" : ",",
				    map->inputs[j].fan_channel);
		printf("]}");
		printed++;
	}

	printf("],\"count\":%d,\"inputs_on_chip\":%d", printed, n);
	printf(",\"note\":\"The chip reports which source each register is assigned "
	    "to. It does not report what a pin is touching: SYSTIN, CPUTIN and "
	    "AUXTIN0-3 are pins, what sits at the far end of one was decided by "
	    "whoever laid this board out, and no register records it - those rows "
	    "carry attached unknown and must stay unknown wherever they are shown. A "
	    "source that is unassigned, switched off in the chip, unstable across two "
	    "reads, outside -40 to 150 C, or silent - a PECI agent, SMBus master or PCH "
	    "forward whose register holds nothing but its reset value - is reported as "
	    "that and never as a temperature; decoded_c beside such a row is what the "
	    "bytes would have said and is not a reading. Nothing was written here: this "
	    "command selects a bank and "
	    "latches an index, which are address writes, and reads bytes back.\"}\n");
}

static void
cmd_duty(unsigned channel, unsigned pct)
{
	struct ident id;
	struct floor f;
	struct saved s;
	sigset_t mask;
	uint8_t raw, cur_mode_reg, cur_mode, cur_duty, crit_en, crit;
	const char *out_kind;
	int lock, saved_now;

	/*
	 * Everything that can be refused without touching the hardware is
	 * refused before the hardware is opened. A bad duty must cost a process
	 * start and nothing else - no port, no lock, no chip.
	 */
	if (pct < 1 || pct > 100)
		die(EX_DUTY_, "duty %u%%: this command takes 1..100. Zero is not a duty, "
		    "it is a stopped fan, and the way to hand a channel back is "
		    "'release'", pct);

	f = floor_for(channel);
	raw = pct_to_raw(pct);
	if (raw < pct_to_raw(f.pct))
		die(EX_FLOOR_, "duty %u%% (raw %u) is below the floor of %u%% (raw %u, "
		    "source %s). Refused here, not upstairs", pct, raw, f.pct,
		    pct_to_raw(f.pct), f.source);

	id = identify();
	must_channel_exist(&id, channel);

	lock = window_lock();
	io_open();

	cur_mode_reg = ec_read(&id, reg_fan_mode[channel - 1]);
	cur_mode = (uint8_t)((cur_mode_reg >> 4) & 7);
	cur_duty = ec_read(&id, reg_pwm[channel - 1]);

	/*
	 * Whether anything in hardware will act if the caller of this program
	 * dies. Read here, at the moment of taking the channel over, because
	 * that is when somebody can still decide not to. This program does not
	 * arm it - see WHAT IS LEFT DRIVING THE FANS, above.
	 */
	crit_en = ec_read(&id, reg_crit_en[channel - 1]);
	crit = ec_read(&id, reg_crit[channel - 1]);

	/*
	 * Reported, not refused. A DC channel drives a three-wire fan by
	 * varying its supply voltage, and 40% of a voltage range is not 40% of
	 * a PWM duty cycle - the stall point is in a different place. DC fan
	 * control is a legitimate thing for a board to do, so the answer names
	 * what kind of channel this is and says the floor was not chosen for it.
	 * Only channel 1 has the bit on this family (D, nct6775-core.c:277).
	 */
	out_kind = "pwm";
	if (channel == 1 && (ec_read(&id, REG_PWM_MODE) & PWM_MODE_MASK) != 0)
		out_kind = "dc";

	saved_now = state_save_once(channel, cur_mode, cur_duty, id.devid);
	if (saved_now < 0) {
		close(lock);
		die(EX_INTERNAL_, "cannot record the original state of channel %u under "
		    "%s, and taking a channel over without being able to give it back "
		    "is not something this program does", channel, STATE_DIR);
	}

	/*
	 * Duty first, mode last, and the order is the safety property. Under an
	 * automatic mode the duty register is not the output - the chip computes
	 * that and reports it in REG_PWM_READ - so writing it is inert until the
	 * mode changes (D: nct6775-core.c:2707 writes this same register while
	 * the channel is still under automatic control). If this program dies
	 * between the two writes, the channel is still under the chip's own
	 * control at a duty of its own choosing. If the channel was ALREADY in
	 * manual the first write is live immediately - and it is a duty at or
	 * above the floor, which is the whole point of refusing below it. So
	 * there is no window in which the fan is under our value and not yet
	 * under our intent, and none in which it is under a value the floor
	 * would have refused.
	 */
	mask = hold_signals();
	ec_write_verify(&id, reg_pwm[channel - 1], raw, 0xff);
	ec_write_verify(&id, reg_fan_mode[channel - 1],
	    (uint8_t)((cur_mode_reg & 0x0f) | (MODE_MANUAL << 4)), 0x70);
	release_signals(&mask);

	close(lock);

	printf("{\"ok\":true,\"code\":0,\"command\":\"duty\",\"channel\":%u,"
	    "\"duty_pct\":%u,\"duty_raw\":%u,\"floor_pct\":%u,\"floor_source\":\"%s\","
	    "\"output\":\"%s\",\"floor_applies_to\":\"pwm\","
	    "\"hardware_backstop\":%s,\"critical_duty_raw\":%u,"
	    "\"previous_mode\":%u,\"previous_mode_name\":\"%s\",\"previous_duty_raw\":%u,"
	    "\"original_recorded_now\":%s,", channel, pct, raw, f.pct, f.source,
	    out_kind, (crit_en & CRIT_PWM_ENABLE_MASK) ? "true" : "false", crit,
	    cur_mode, mode_name(cur_mode, cur_duty), cur_duty,
	    saved_now ? "true" : "false");
	s = state_load(channel, &id);
	printf("\"restores_to_mode\":%u,\"restores_to_duty_raw\":%u",
	    s.valid ? s.mode : 0, s.valid ? s.duty : 255);
	if (strcmp(out_kind, "dc") == 0)
		printf(",\"caution\":\"This channel drives its fan by voltage, not by "
		    "pulse width. The floor is a PWM floor and nobody has measured "
		    "where a fan stalls on a DC channel of this board.\"");
	if ((crit_en & CRIT_PWM_ENABLE_MASK) == 0)
		printf(",\"caution_backstop\":\"This duty is now latched in the chip and "
		    "nothing in hardware will change it. If the process that asked for it "
		    "dies without running release, this duty is what cools the machine "
		    "until somebody intervenes.\"");
	printf("}\n");
}

/*
 * The way out.
 *
 * This is the code that runs when everything else has failed, so it is the
 * plainest code in the file: no allocation, no parsing that can throw, two
 * writes, one readback each.
 *
 * What it will NOT do, and this is a correction to an earlier version:
 *
 *  - It will not write a manual duty below the floor. The floor exists to stop
 *    a fan being stopped, and an original of "manual, duty 0" honoured
 *    literally stops one. Measured against a simulated chip (V, 2026-09-22):
 *    with a baseline of mode 0 / PWM 0x00 the old code wrote 0x00 to register
 *    0x109 and reported {"ok":true,"restored_from":"saved-original"}; with a
 *    baseline of mode 0 / PWM 0x1a it wrote 10% while the floor said 30%. A
 *    baseline like that is what a channel looks like on a board whose firmware
 *    never drove it - and this program cannot tell a header with no fan from a
 *    fan whose tachometer is not wired, which is exactly the case where being
 *    wrong costs hardware. So a manual original below the floor is refused and
 *    the failsafe is written instead, with the reason in the answer.
 *
 *  - It will not take a channel away from the hardware in order to "give it
 *    back". With no usable record and the channel under one of the chip's
 *    automatic modes, this program has no evidence it ever touched that channel
 *    and writes nothing. The old code wrote mode 0 and duty 255 there, which on
 *    a board that has a working firmware fan curve - the general case this
 *    plugin is meant to cover, not the ex-appliance case - replaced that curve
 *    with full speed until the next cold boot (V, 2026-09-22: from a simulated
 *    SmartFan IV baseline it wrote 0x102 <- 0x00).
 *
 * If the saved original is missing or unusable AND the channel is in manual,
 * the target is mode 0 with duty 255: the chip's own "PWM control off", full
 * output, no thermal control. It is loud and it is obviously wrong to anyone
 * standing in the room, which is exactly what should follow a failure nobody
 * understood.
 *
 * It still identifies the chip first. Writing a mode register on an
 * unidentified chip is the one disaster worse than a fan left where we put it,
 * so this refuses rather than guesses, and says so loudly enough to be acted
 * on.
 */
static void
cmd_release(unsigned channel)
{
	struct ident id;
	struct saved s;
	struct floor f;
	sigset_t mask;
	uint8_t mode_reg, cur_mode, target_mode, target_duty, mode_byte;
	const char *from, *why;
	int lock;

	f = floor_for(channel);		/* before the lock: this reads a file */

	id = identify();
	must_channel_exist(&id, channel);

	lock = window_lock();
	io_open();

	s = state_load(channel, &id);
	mode_reg = ec_read(&id, reg_fan_mode[channel - 1]);
	cur_mode = (uint8_t)((mode_reg >> 4) & 7);

	if (!s.valid && cur_mode != MODE_MANUAL) {
		/*
		 * Nothing to give back. Saying so is the answer; writing
		 * something would be the mistake. The duty is read before the
		 * lock goes, because every access to this window happens under
		 * it - including one that only decorates a message.
		 */
		uint8_t cur_duty = ec_read(&id, reg_pwm[channel - 1]);

		close(lock);
		printf("{\"ok\":true,\"code\":0,\"command\":\"release\",\"channel\":%u,"
		    "\"written\":false,\"mode\":%u,\"mode_name\":\"%s\",\"duty_raw\":%u,"
		    "\"restored_from\":\"nothing-to-restore\",\"why\":\"there is no usable "
		    "record that this program ever took this channel over%s, and the "
		    "channel is under the chip's own control (%s). Nothing was "
		    "written.\"}\n", channel, cur_mode,
		    mode_name(cur_mode, cur_duty), cur_duty,
		    s.wrong_chip ? " on this chip" : "",
		    mode_name(cur_mode, cur_duty));
		return;
	}

	why = NULL;
	if (s.valid) {
		target_mode = s.mode;
		target_duty = s.duty;
		from = "saved-original";
	} else {
		target_mode = MODE_MANUAL;
		target_duty = 255;
		from = "failsafe-full-speed";
		why = s.wrong_chip ?
		    "the recorded original was captured on a different chip, so it is "
		    "not this channel's original" :
		    "there is no usable record of this channel's original, and it is "
		    "under manual control, which may be this program's doing";
	}

	/*
	 * The floor binds this command too. Only a manual target is checked:
	 * under an automatic mode the duty register is not the output, so
	 * restoring the byte that was there is inert and correct.
	 */
	if (target_mode == MODE_MANUAL && target_duty < pct_to_raw(f.pct)) {
		target_duty = 255;
		from = "failsafe-full-speed";
		why = "the recorded original was manual below the floor, which is a "
		    "stopped or stalling fan, so it was not restored";
	}

	/* See MODE_MAX: bits 3:0 are kept, bit 7 is cleared, as Linux does. */
	mode_byte = (uint8_t)((mode_reg & 0x0f) | ((target_mode & 0x07) << 4));

	/*
	 * The order is the opposite of cmd_duty's, and for the same reason
	 * rather than in spite of it. Here the channel is in MANUAL when we
	 * arrive - we put it there - so the duty register is live, and writing
	 * the original duty first would apply it under OUR manual mode. If the
	 * second write then failed, the channel would be left at that duty
	 * for ever. Putting an automatic mode back FIRST hands the fan to the
	 * chip immediately and makes the duty write that follows inert, so a
	 * failure after the first write leaves the fan under hardware control.
	 *
	 * When the target is itself manual there is no such order to choose, and
	 * the duty being written has already been checked against the floor.
	 */
	mask = hold_signals();
	if (target_mode == MODE_MANUAL) {
		ec_write_verify(&id, reg_pwm[channel - 1], target_duty, 0xff);
		ec_write_verify(&id, reg_fan_mode[channel - 1], mode_byte, 0x70);
	} else {
		ec_write_verify(&id, reg_fan_mode[channel - 1], mode_byte, 0x70);
		ec_write_verify(&id, reg_pwm[channel - 1], target_duty, 0xff);
	}
	release_signals(&mask);

	close(lock);
	state_forget(channel);

	printf("{\"ok\":true,\"code\":0,\"command\":\"release\",\"channel\":%u,"
	    "\"written\":true,\"restored_from\":\"%s\",\"mode\":%u,\"mode_name\":\"%s\","
	    "\"duty_raw\":%u,\"duty_pct\":%u,\"floor_pct\":%u,\"floor_source\":\"%s\"",
	    channel, from, target_mode, mode_name(target_mode, target_duty),
	    target_duty, raw_to_pct(target_duty), f.pct, f.source);
	if (why != NULL)
		printf(",\"why\":\"%s\"", why);
	if (s.valid && !s.fingerprinted)
		printf(",\"caution\":\"The record restored here carries no chip "
		    "fingerprint, so it was written by an older version of this program "
		    "and cannot be checked against this chip.\"");
	printf("}\n");
}

/* ------------------------------------------------------------------------ */

static void
usage(void)
{
	fprintf(stderr,
"fanctl-io " FANCTL_IO_VERSION " - one command, one answer, exit.\n"
"\n"
"  fanctl-io identify            which chip is here, and where its window is\n"
"  fanctl-io status              every channel: speed, duty, mode, floor\n"
"  fanctl-io tach <channel>      the tachometer of one channel, in RPM\n"
"  fanctl-io temp [<n>]          every temperature source the chip has, or one\n"
"  fanctl-io duty <channel> <%%>  hold one channel at a fixed duty, 1..100\n"
"  fanctl-io release <channel>   put back what this program found on one\n"
"                                channel, or full speed if it cannot\n"
"  fanctl-io version\n"
"\n"
"Channels are numbered from 1, as the chip numbers them. The names ncthwm(4)\n"
"prints - SYSFAN, CPUFAN, AUXFAN0 - are that driver's own labels and say\n"
"nothing about which fan in the chassis is on which header.\n"
"\n"
"temp numbers its inputs from 1 in the order it prints them; they are not fan\n"
"channels. It says which SOURCE the chip has put in each register - SYSTIN,\n"
"CPUTIN, AUXTIN0-3, a PECI agent, a PCH forward - and it does not say what a\n"
"pin is touching, because no register on this chip records that.\n"
"\n"
"release writes nothing at all on a channel this program has no record of\n"
"taking over. It is not a way to reset a fan to firmware control: only the\n"
"firmware can do that, at a cold boot.\n"
"\n"
"The minimum duty is read from " FLOOR_FILE " by this program.\n"
"It binds release as well as duty. There is no option to lower it and none\n"
"will be added.\n"
"\n"
"Answers are one JSON object on stdout, including failures.\n");
	die(EX_USAGE_, "no command, or a command this program does not have");
}

/*
 * Strict on purpose: "40%%", "0x28" and "4 " are all rejected rather than
 * interpreted. A number that had to be guessed at is a number that can be
 * guessed wrong, and the thing on the other end of this one is a fan.
 */
static unsigned
must_number(const char *s, const char *what)
{
	char *end;
	unsigned long v;

	if (s == NULL || *s == '\0' || !isdigit((unsigned char)*s))
		die(EX_USAGE_, "%s: expected a plain decimal number, got \"%s\"",
		    what, s == NULL ? "" : s);
	errno = 0;
	v = strtoul(s, &end, 10);
	if (errno != 0 || *end != '\0' || v > 100000)
		die(EX_USAGE_, "%s: expected a plain decimal number, got \"%s\"", what, s);
	return ((unsigned)v);
}

static unsigned
must_channel(const char *s)
{
	unsigned ch = must_number(s, "channel");

	/*
	 * The syntactic bound. The real bound belongs to the chip and is
	 * checked after identification; this one is here so that a nonsense
	 * channel never causes a device to be opened at all.
	 */
	if (ch < 1 || ch > MAX_CHANNELS)
		die(EX_CHANNEL_, "channel %u: channels are 1..%u on every chip this "
		    "program knows", ch, MAX_CHANNELS);
	return (ch);
}

int
main(int argc, char **argv)
{
	const char *cmd;

	setvbuf(stdout, NULL, _IOLBF, 0);

	if (argc < 2)
		usage();
	cmd = argv[1];

	if (strcmp(cmd, "version") == 0) {
		printf("{\"ok\":true,\"code\":0,\"command\":\"version\","
		    "\"version\":\"%s\",\"hard_floor_pct\":%u,\"floor_file\":\"%s\","
		    "\"state_dir\":\"%s\",\"floor_binds_release\":true}\n",
		    FANCTL_IO_VERSION, FANCTL_HARD_FLOOR_PCT, FLOOR_FILE, STATE_DIR);
		return (0);
	}
	if (strcmp(cmd, "identify") == 0) {
		if (argc != 2)
			usage();
		cmd_identify();
		return (0);
	}
	if (strcmp(cmd, "status") == 0) {
		if (argc != 2)
			usage();
		cmd_status();
		return (0);
	}
	if (strcmp(cmd, "tach") == 0) {
		if (argc != 3)
			usage();
		cmd_tach(must_channel(argv[2]));
		return (0);
	}
	if (strcmp(cmd, "temp") == 0) {
		/*
		 * The index is bounded here only by the widest table this
		 * program has, so that a nonsense number never opens a device.
		 * The real bound belongs to the chip and is checked after
		 * identification, exactly as it is for a channel.
		 */
		if (argc == 2)
			cmd_temp(0);
		else if (argc == 3) {
			unsigned n = must_number(argv[2], "temperature input");

			if (n < 1 || n > MAX_TEMP_INPUTS)
				die(EX_USAGE_, "temperature input %u: there are at most "
				    "%d on any chip this program has a map for", n,
				    MAX_TEMP_INPUTS);
			cmd_temp(n);
		} else
			usage();
		return (0);
	}
	if (strcmp(cmd, "duty") == 0) {
		if (argc != 4)
			usage();
		cmd_duty(must_channel(argv[2]), must_number(argv[3], "duty"));
		return (0);
	}
	if (strcmp(cmd, "release") == 0) {
		if (argc != 3)
			usage();
		cmd_release(must_channel(argv[2]));
		return (0);
	}
	usage();
	return (EX_USAGE_);
}
