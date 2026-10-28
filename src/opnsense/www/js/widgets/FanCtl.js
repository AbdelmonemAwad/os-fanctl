/*
 * Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
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
 */

/*
 * Dashboard widget for Fan Control: every fan that reports a speed, then every temperature the
 * machine will give up, hottest first, each one drawn as a bar.
 *
 * A bar rather than a number because a number is only worth reading against something. The
 * temperature bars all run from zero to the panic point, with a mark where the limit is, so
 * "58 C" stops being a value somebody has to remember the meaning of and becomes a distance from
 * the two thresholds that actually change the plugin's behaviour - both read out of the same
 * document, never assumed here.
 *
 * Hottest first, and that is not only a sort. The curve follows the hottest sensor in the groups
 * it is given and never an average (DESIGN.md section 9), so the top line of this list is the one
 * the plugin is actually deciding from.
 *
 * Three things this widget will not do, all three for reasons the design settles rather than this
 * file:
 *
 *   - It never calls a channel reading zero a stopped fan. That may be a header with no fan on
 *     it, or a fan whose tachometer is not wired to this chip, and nothing in software can tell
 *     those apart (section 7). Those channels are not given a line each either: on the reference
 *     machine four of the five would be a permanent list of nothing, so they are counted in one
 *     muted line at the foot.
 *   - It never draws a bar for a fan speed. A duty is a percentage of something known; an rpm is
 *     not, because nothing here knows what this fan's maximum is. Where the helper reports a duty
 *     there is a bar, and where only a tachometer answered there is a number and no bar.
 *   - It re-derives nothing. Every verdict, threshold and label is printed as the status document
 *     gave it, which is the same document the full page reads, so the two cannot disagree.
 *
 * The only red thing here is an incident: a restore that failed, which is the one state in this
 * plugin a person has to act on, and it says what to do rather than what happened.
 */

export default class FanCtl extends BaseTableWidget {
    constructor(config) {
        super(config);

        /* The daemon writes its document once per decision - ten seconds by default - and where
           it is not running the controller takes the readings itself when asked. Twenty seconds
           keeps the dashboard within two decisions of the truth without asking a machine that is
           thinking about its fans to take a second set of readings every tick. */
        this.tickTimeout = 20;

        /* dataChanged() answers "is this the same document as last time", which is not the same
           question as "is the list still on the screen" - see _update(). */
        this.listed = false;

        /* There is no width at which the bars are dropped, and the first draft was wrong about
           that. It hid them below 360px on the reasoning that a narrow panel has no room - but
           what actually happens below that width is that the base class stacks the two cells, so
           the bar stops sharing a line with the label and gets the whole panel to itself. It is
           at its widest exactly where it was being hidden. Measured on the reference dashboard:
           a one-column panel is about 200px across, and a stacked bar there is 200px of scale
           where the side-by-side one had 90. */
    }

    getGridOptions() {
        return {
            // trigger overflow-y:scroll after 500px height
            sizeToContent: 500
        };
    }

    getMarkup() {
        const $container = $('<div></div>');

        /* One line for what is true of the whole document rather than of any one reading: an
           incident, a service that has stopped, a file nobody is updating any more. */
        $container.append($(`
            <div id="fanctl-notice" style="display: none; padding: 0.5em; text-align: center;"></div>
        `));

        $container.append(this.createTable('fanctl-table', {headerPosition: 'none'}));

        return $container;
    }

    async onMarkupRendered() {
        /* The dashboard serialises getMarkup() to a string before gridstack parses it again, so a
           handler bound while the markup is being built never reaches the page. This runs once the
           markup is in the document, and it is delegated from the table, which stays put -
           updateTable() throws the rows away and builds new ones on every refresh. */
        $('#fanctl-table')
            .off('click.fanctl')
            .on('click.fanctl', '.flextable-row', () => {
                window.location.href = '/ui/fanctl';
            });
    }

    async onWidgetTick() {
        let status = null;

        try {
            status = await this.ajaxCall('/api/fanctl/service/state');
        } catch (error) {
            /* An exception that escapes here replaces the widget with the dashboard's red "Failed
               to load widget" box, which then says nothing at all about the fans. A web service
               being restarted, or a session that has just expired, is not worth that: the last
               readings stay on screen under a line saying they are no longer being refreshed. */
            this._setNotice(this.translations.unreachable, 'text-warning');
            return;
        }

        try {
            this._update(status);
        } catch (error) {
            /* one malformed record must cost this widget one line at most, never the whole list */
            console.error('FanCtl: could not render the readings', error);
            this._setNotice(this.translations.unreachable, 'text-warning');
        }
    }

    /* Rendering */

    _update(status) {
        /* The API hands the plugin's own document back verbatim, and that document has no top
           level "status" key of this shape. An envelope with one is the controller saying it never
           reached the backend - configd stopped, or the script died - which is a different thing
           from a plugin that has simply not been set up yet. */
        if (status && status.status === 'failed') {
            this._setNotice(this.translations.unreachable, 'text-warning');
            return;
        }

        const fans = Array.isArray(status && status.fans) ? status.fans : [];
        const readings = this._readings(status);

        if (fans.length === 0 && readings.length === 0) {
            this._setNotice(this.translations.nodata, 'text-muted');
            this.updateTable('fanctl-table', []);
            this.listed = false;
            return;
        }

        this._setNotice(...this._notice(status));

        /* A document identical to the last one still has to be drawn when the list is not on the
           screen - the first tick after a resize, or after an empty sweep emptied the table. */
        const shape = {fans: fans, readings: readings, curve: status.curve || {}};
        if (!this.dataChanged('fanctl-readings', shape) && this.listed) {
            return;
        }

        const rows = [];

        const heard = fans.filter((fan) => this._rpm(fan) > 0 || fan.driven === true);
        heard.sort((a, b) => this._rpm(b) - this._rpm(a));
        heard.forEach((fan) => rows.push(this._fanRow(fan)));

        readings.forEach((sensor) => rows.push(this._sensorRow(sensor, status.curve || {})));

        /* The channels that answered nothing, counted rather than listed. Four permanent lines
           saying "no tachometer signal" is what this widget looks like on the reference machine if
           this line is not here, and none of the four is news on any day. */
        const silent = fans.length - heard.length;
        if (silent > 0) {
            rows.push(this._silentRow(silent));
        }

        this.updateTable('fanctl-table', rows);

        /* the rows are built by the base class, so the pointer is set once they exist */
        $('#fanctl-table').children('.flextable-row').css('cursor', 'pointer');

        this.listed = true;
    }

    _readings(status) {
        const temperatures = (status && status.temperatures) || {};
        const sensors = Array.isArray(temperatures.sensors) ? temperatures.sensors : [];

        /* Hottest first: the top line is the sensor the curve would be deciding from. A sensor
           that answered with something that is not a number is dropped rather than drawn at zero,
           because a bar at the far left is a claim that the machine is cold. Number(null) is 0,
           so a sensor that answered nothing at all is ruled out before the conversion and not by
           it: an XGS 3300's memory module with no sensor was drawn at 0.0 C. */
        const sorted = sensors
            .filter((sensor) => sensor && sensor.celsius !== null && sensor.celsius !== ''
                && Number.isFinite(Number(sensor.celsius)))
            .slice()
            .sort((a, b) => Number(b.celsius) - Number(a.celsius));

        /* The processor's cores as one line, the hottest of them: eight identical bars made this
           widget the tallest thing on the dashboard and said nothing the hottest one does not. */
        const cores = sorted.filter((sensor) => String(sensor.kind) === 'cpu_core');
        if (cores.length < 3) {
            return sorted;
        }
        const lead = Object.assign({}, cores[0], {
            label: String(this.translations.cores || '%s').replace('%s', this._format(cores.length))
        });
        return sorted
            .filter((sensor) => String(sensor.kind) !== 'cpu_core' || sensor === cores[0])
            .map((sensor) => (sensor === cores[0] ? lead : sensor));
    }

    _fanRow(fan) {
        const driven = fan.driven === true;
        const rpm = this._rpm(fan);

        const name = this._identity(
            'fa-life-ring',
            driven ? 'text-info' : 'text-muted',
            this._label(fan),
            fan.channel,
            [fan.channel, driven ? this.translations.state_driven : this.translations.state_turning]
        );

        /* The duty is the only number here a bar can honestly be drawn from. `output` is what the
           chip says it is putting out and `duty` is what it was asked for; where both are known
           they are the same number, and where they are not it is the output that is true. */
        const duty = this._percent(fan.output !== null && fan.output !== undefined ? fan.output : fan.duty);
        const speed = rpm > 0
            ? `${this._format(rpm)} ${this.translations.unit_rpm}`
            : this.translations.state_silent;

        if (duty === null) {
            /* No duty, so no bar, and the tachometer is the whole of the line. */
            return [name, `
                <div style="display: flex; align-items: center; justify-content: flex-end; min-width: 0;">
                    <span dir="ltr" class="text-muted">${this._escape(speed)}</span>
                </div>`];
        }

        /* Both numbers are printed, because the bar is drawn from the duty and the speed is what
           the fan is actually doing about it - a duty that moved and an rpm that did not is the
           first sign of a fan that has stopped answering. */
        const reading = `
            <span dir="ltr">${duty}%</span>
            <span dir="ltr" class="text-muted" style="font-size: 11px;">${this._escape(speed)}</span>`;

        return [name, this._measure(reading, '', duty, null, driven ? 'text-info' : 'text-muted')];
    }

    _sensorRow(sensor, curve) {
        const celsius = Number(sensor.celsius);
        const limit = Number(curve.limit_c);
        const panic = Number(curve.panic_c);

        /* The scale is the panic point and the mark is the limit, both out of the document. Where
           the document carries neither - an older backend, a curve that failed to parse - the bar
           would be a drawing with no scale behind it, so there is no bar and the reading stands on
           its own. */
        const top = Number.isFinite(panic) && panic > 0 ? panic : null;
        const mark = Number.isFinite(limit) && limit > 0 && top !== null && limit < top ? (limit / top) * 100 : null;

        let colour = 'text-muted';
        let tip = '';
        if (Number.isFinite(panic) && celsius > panic) {
            colour = 'text-danger';
            tip = this.translations.over_panic;
        } else if (Number.isFinite(limit) && celsius >= limit) {
            colour = 'text-warning';
            tip = this.translations.over_limit;
        } else if (top !== null) {
            tip = String(this.translations.scale || '')
                .replace('%s', this._degrees(panic))
                .replace('%s', this._degrees(limit));
        }

        const name = this._identity(
            'fa-thermometer-half', colour, sensor.label, sensor.group,
            [sensor.name, sensor.group]
        );

        const reading = `<span dir="ltr" class="${colour}">${this._escape(this._degrees(celsius))}</span>`;

        if (top === null) {
            return [name, `<div style="display: flex; align-items: center; min-width: 0;">${reading}</div>`];
        }

        return [name, this._measure(reading, tip, (celsius / top) * 100, mark, colour)];
    }

    _silentRow(count) {
        const text = String(this.translations.silent_summary || '').replace('%s', this._format(count));

        return [
            `<div class="text-muted" style="font-size: 11px; min-width: 0;">${this._escape(text)}</div>`,
            ''
        ];
    }

    _identity(icon, colour, label, aside, tooltip) {
        /* min-width on the row and on the label is what lets a long sysctl name be cut with an
           ellipsis: a flex item refuses to shrink below its own content until it is told it may. */
        const tip = (tooltip || []).filter((part) => !!part).join(' - ');

        return `
            <div style="display: flex; align-items: center; gap: 6px; min-width: 0;">
                <i class="fa fa-fw ${icon} ${colour} fanctl-tip" style="font-size: 11px;"
                   data-toggle="tooltip" title="${this._escape(this._isolate(tip))}"></i>
                <span style="font-weight: bold; min-width: 0; overflow: hidden;
                             text-overflow: ellipsis; white-space: nowrap;"
                      >${this._escape(this._isolate(label || ''))}</span>
                <span class="text-muted" dir="ltr" style="font-size: 11px;"
                      >${this._escape(this._isolate(aside || ''))}</span>
            </div>`;
    }

    _measure(reading, tooltip, fillPercent, markPercent, colour) {
        const fill = Math.max(0, Math.min(100, Number(fillPercent) || 0));

        /* currentColor throughout, and the outline at full strength with only the inside faded.
           That split is not a matter of taste: `opacity` blends a block towards whatever is behind
           it, and the widget panel is nearly white on the light dashboard and nearly black on the
           dark one, so a bar faded as a whole loses one theme or the other. The same colour at
           full strength on the outline reads on both, which is the lesson the faceplate strip in
           os-linkhealth learned the hard way.

           inset-inline-start for the mark rather than left: the bar itself mirrors with the page
           on a right-to-left GUI, and a mark pinned to the left edge would then be sitting at the
           wrong end of its own scale. */
        const mark = (markPercent === null || markPercent === undefined)
            ? ''
            : `<span style="position: absolute; top: -1px; bottom: -1px;
                            inset-inline-start: ${Math.max(0, Math.min(100, Number(markPercent)))}%;
                            width: 1px; background-color: currentColor; opacity: 0.9;"></span>`;

        return `
            <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
                <div class="fanctl-bar ${colour} fanctl-tip" data-toggle="tooltip"
                     title="${this._escape(this._isolate(tooltip || ''))}"
                     style="position: relative; flex: 1 1 auto; min-width: 24px; height: 8px;
                            border-radius: 2px; box-shadow: inset 0 0 0 1px currentColor;">
                    <span style="display: block; height: 100%; width: ${fill}%;
                                 background-color: currentColor; opacity: 0.55;
                                 border-radius: 2px;"></span>
                    ${mark}
                </div>
                <div style="flex: 0 0 auto; white-space: nowrap;">${reading}</div>
            </div>`;
    }

    /* State of the whole document */

    _notice(status) {
        /* Ranked, because only one line fits and they are not equally urgent. An incident first
           and in red: it is the one state in this plugin that needs a person, and it outranks the
           fact that the service is also stopped - which it will be, because the restore that
           failed is what stopped it. */
        if (status && status.incident) {
            return [this.translations.incident, 'text-danger'];
        }

        const stale = this._stale(status);
        if (stale !== '') {
            return [stale, 'text-warning'];
        }

        if (status && status.enabled === false) {
            return [this.translations.disabled, 'text-muted'];
        }

        const service = (status && status.service) || {};
        if (service.running !== true) {
            return [this.translations.not_running, 'text-muted'];
        }

        /* Running, and the mode is the whole of what there is to say. monitor is the default and
           it is not a complaint: the plugin reads and writes nothing until somebody has measured a
           floor with the appliance in front of them. */
        const modes = {
            monitor: this.translations.mode_monitor,
            curve: this.translations.mode_curve,
            manual: this.translations.mode_manual
        };

        return [modes[status.mode] || '', 'text-muted'];
    }

    _stale(status) {
        /* Only the daemon's own file can go stale. Where the service is not running the controller
           took these readings when this tick asked for them, so they are fresh by construction and
           there is nothing here to warn about. */
        const service = (status && status.service) || {};
        if (service.running !== true) {
            return '';
        }

        const at = Number(status && status.at);
        if (!Number.isFinite(at) || at <= 0) {
            return '';
        }

        const interval = Number(status.interval) > 0 ? Number(status.interval) : 10;
        const age = (Date.now() / 1000) - at;

        /* Six decisions, and never less than two minutes, so a long interval does not make this
           fire on a healthy machine. Six is the watchdog's own length: past that the daemon has
           already decided it is not going round any more. */
        if (age <= Math.max(120, interval * 6)) {
            return '';
        }

        return String(this.translations.stale || '')
            .replace('%s', this._isolate(new Date(at * 1000).toLocaleString()));
    }

    _setNotice(text, colour) {
        const $notice = $('#fanctl-notice');

        if (!text) {
            $notice.hide().empty();
            return;
        }

        $notice
            .attr('class', colour || 'text-muted')
            .css('font-size', '11px')
            .text(text)
            .show();
    }

    /* Formatting */

    _label(fan) {
        /* fans.json carries a human name for a channel the chassis table has been confirmed
           against - "chassis fan" - and "unknown" where nobody has. Either way the channel's own
           name is printed beside it, so a label nobody has checked is never the only thing on the
           line. */
        return fan.label && fan.label !== 'unknown' ? fan.label : fan.channel;
    }

    _rpm(fan) {
        const rpm = Number(fan && fan.rpm);
        return Number.isFinite(rpm) && rpm > 0 ? rpm : 0;
    }

    _percent(value) {
        const percent = Number(value);
        return Number.isFinite(percent) ? Math.max(0, Math.min(100, Math.round(percent))) : null;
    }

    _degrees(celsius) {
        const value = Number(celsius);
        return Number.isFinite(value) ? `${value.toFixed(1)} °C` : '';
    }

    _format(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number.toLocaleString() : '';
    }

    _escape(value) {
        /* Labels come from fans.json, sensor names straight out of the sysctl tree, and the rest
           from config.xml - all of it text somebody else wrote, all of it going into markup this
           file builds by hand. */
        return $('<div></div>').text(value === null || value === undefined ? '' : String(value)).html();
    }

    _isolate(value) {
        /* A sysctl name, a channel name and an appliance model are all Latin text that may land in
           the middle of a right-to-left sentence, where the bidirectional algorithm will reorder
           the punctuation around it. U+2068/U+2069 fence it off without changing what it says. */
        const text = (value === null || value === undefined) ? '' : String(value);
        return text === '' ? '' : `⁨${text}⁩`;
    }
}
