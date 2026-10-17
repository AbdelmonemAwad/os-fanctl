{#
 # Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without modification,
 # are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright notice,
 #    this list of conditions and the following disclaimer in the documentation
 #    and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}

<script>
    $(document).ready(function () {

        /* The fan readings are live and age like it. Five seconds is fast enough that a fan
           answering a change in duty is visible while somebody watches, and slow enough that
           reading this page is not itself a load on the machine. It only runs while the Fans tab
           is on screen: every one of these costs a configd round trip, and neither reading is
           worth taking for somebody who is looking at a different tab. */
        const STATE_MS = 5000;
        /* The probe changes when somebody changes the hardware, which is not often. */
        const PROBE_MS = 30000;
        /* The record is rewritten once a minute at most, so asking more often than that buys
           the reader nothing and costs a configd round trip each time. */
        const TREND_MS = 60000;

        /* Which window the graphs are drawn over. Kept here rather than read off the buttons
           every time, so that a redraw after a refresh lands on the window somebody chose
           rather than resetting to the first one. */
        let trend_window = 'hour';
        let trend_doc = null;

        /* A window whose data covers less than this much of it is still drawn - it is real
           data and hiding it would be worse - but faded, and its coverage is printed. The
           record starts empty, and an hour window a minute after an installation has a
           perfectly good minimum and maximum that describe one minute. */
        const THIN_COVERAGE = 0.2;

        /* The names of the windows the record keeps. history.py emits the keys; these are what
           they are called on the page. */
        const window_title = {
            'hour': "{{ lang._('Last hour') }}",
            'day': "{{ lang._('Last day') }}",
            'week': "{{ lang._('Last week') }}",
            'month': "{{ lang._('Last month') }}"
        };

        /* What each contract condition is called on the page. The keys are the ones fanctl.py
           emits and the ones fans.json names in its own contract block, so the three cannot
           drift apart without a line visibly losing its title. */
        const contract_title = {
            'fingerprint': "{{ lang._('The chip is the one the table describes') }}",
            'permission': "{{ lang._('This model may be controlled') }}",
            'baseline': "{{ lang._('The original settings have been captured') }}",
            'floor': "{{ lang._('Every fan to be driven has a measured floor') }}",
            'tach': "{{ lang._('Every fan to be driven is turning') }}",
            'sensors': "{{ lang._('Every temperature needed is being read') }}"
        };

        /* A channel's tachometer, in words. "Stopped" is only ever said about a channel the
           hardware table says should have a fan turning on it: four of the five channels on the
           reference appliance read zero and none of them is a stopped fan. A tachometer of zero
           means this chip is not being told about a fan there - no fan, a fan with no tach wire,
           or a fan on a header this chip does not sample - and nothing in software can tell those
           apart. Saying "4 fans stopped" on a machine whose fans are all turning is how a plugin
           gets switched off and never switched on again. */
        const fan_state = {
            'turning': ["{{ lang._('turning') }}", 'label-success'],
            'silent': ["{{ lang._('no tachometer signal') }}", 'label-default'],
            'stopped': ["{{ lang._('STOPPED') }}", 'label-danger'],
            'unreadable': ["{{ lang._('not readable') }}", 'label-warning']
        };

        const fan_state_note = {
            'silent': "{{ lang._('This channel reports no fan. It may have no fan on it, a fan with no tachometer wire, or a fan on a header this chip does not sample - nothing here can tell those apart, so it is never driven and is not reported as a fault.') }}",
            'stopped': "{{ lang._('The table says a fan should be turning here and it is not. Look at the machine.') }}",
            'unreadable': "{{ lang._('The tachometer could not be read at all this time.') }}"
        };

        const service_text = {
            'running': "{{ lang._('running') }}",
            'stopped': "{{ lang._('stopped') }}",
            'disabled': "{{ lang._('turned off') }}",
            'unknown': "{{ lang._('unknown') }}"
        };

        /* Turned off is grey and not red: fan control that was deliberately switched off is not a
           fault, it is the state every machine is in until somebody chooses otherwise, and it is
           the state in which the fans are under the law the firmware left them under. */
        const service_class = {
            'running': 'label-success',
            'stopped': 'label-danger',
            'disabled': 'label-default',
            'unknown': 'label-default'
        };

        let state_doc = null;
        let probe_doc = null;

        /* ------------------------------------------------------------------ small helpers */

        function is_object(value) {
            return value !== null && typeof value === 'object' && !$.isArray(value);
        }

        function show_message(type, title, message) {
            BootstrapDialog.show({
                type: type,
                title: title,
                message: message,
                buttons: [{
                    label: "{{ lang._('Close') }}",
                    action: function (dialog) { dialog.close(); }
                }]
            });
        }

        /* The API answers a refusal with `detail` and the script behind it with `message`. Take
           whichever arrived, because which one does depends on how far the request got. */
        function failure_text(data) {
            const detail = data ? (data.detail || data.message) : null;
            return detail ? '<br/><br/>' + $('<div/>').text(detail).html() : '';
        }

        function label(text, classname) {
            return $('<span/>').addClass('label ' + classname).text(text);
        }

        function note(text, classname) {
            return $('<div/>').addClass('fc-note ' + (classname || '')).text(text);
        }

        /* The Hardware tab's long explanations sit under "full help", as the settings tab's do,
           and one drawn after the switch was turned is drawn in the state it was turned to. */
        let help_on = false;

        function help_note(text) {
            return $('<div class="help-block fc-help"/>').text(text).toggle(help_on);
        }

        $('.fc-help-toggle').click(function (event) {
            event.preventDefault();
            help_on = !help_on;
            $('.fc-help-toggle > i')
                .toggleClass('fa-toggle-on text-success', help_on)
                .toggleClass('fa-toggle-off text-danger', !help_on);
            $('.fc-help').toggle(help_on);
        });

        /* one row of a Hardware section: what is read on the left, the reading on the right */
        function hw_row(title, $value) {
            return $('<tr/>').append($('<td/>').text(title)).append($('<td/>').append($value));
        }

        function hw_wide($content) {
            return $('<tr/>').append($('<td colspan="2"/>').append($content));
        }

        function tech(text) {
            return $('<span class="fc-tech"/>').text(text === null || text === undefined
                ? '' : String(text));
        }

        function number_or_dash(value, suffix) {
            if (value === null || value === undefined || value === '') {
                return "—";
            }
            return String(value) + (suffix || '');
        }

        /* ------------------------------------------------------------------ small pieces

           Five shapes, reused on all three tabs, so that one kind of fact looks the same
           wherever it is drawn: a number that exists, a number that does not, a word the
           machine chose, a heading with a count beside it, and a disclosure. */

        /* A number, or the shape of the number that is not there. A dash on its own reads as a
           small value; a dashed box the size of the value it stands in for reads as a hole, and
           carries the collector own sentence about why it is empty. */
        function value(number, suffix, reason) {
            if (number === null || number === undefined || number === '') {
                const $box = $('<span class="fc-val fc-val-absent"/>').text('—');
                if (reason) {
                    $box.attr('title', String(reason));
                }
                return $box;
            }
            return $('<span class="fc-val"/>').attr('dir', 'ltr')
                .text(String(number) + (suffix || ''));
        }

        /* The tone of a chip is always a Bootstrap contextual class and never a colour of its
           own, so the theme owns the hue. The maps above are written in label- classes; this is
           the same short list in the other spelling. */
        const chip_tone = {
            'label-success': 'text-success',
            'label-default': 'text-muted',
            'label-danger': 'text-danger',
            'label-warning': 'text-warning',
            'label-info': 'text-info'
        };

        /* Outlined at full strength with only the inside faded, for the reason the dashboard
           widget records: a block faded as a whole disappears into one of the two themes. The
           title carries the document own long sentence about that word - which is how four
           copies of a 223 character note become four chips and one line under the table. */
        function chip(text, tone, title) {
            const $chip = $('<span class="fc-chip"/>').addClass(tone || '');
            $chip.append($('<span class="fc-chip-text"/>').text(String(text)));
            if (title) {
                $chip.attr('title', String(title));
            }
            return $chip;
        }

        /* A heading, and beside it a COUNT. Never a verdict: a count is the one label this page
           is allowed to compute, because it is arithmetic over what the document already said. */
        function section(title, aside) {
            const $section = $('<div class="fc-section"/>');
            const $head = $('<div class="fc-section-head"/>').appendTo($section);
            $('<span/>').text(String(title)).appendTo($head);
            if (aside) {
                $('<span class="fc-section-aside"/>').text(String(aside)).appendTo($head);
            }
            const $body = $('<div class="fc-section-body"/>').appendTo($section);
            return {
                section: $section,
                body: $body,
                legend: function (text) {
                    if (text) {
                        $section.append($('<div class="fc-note fc-muted"/>').text(String(text)));
                    }
                }
            };
        }

        /* One sentence, printed once. The collector writes the same sentence onto every channel
           it is true of - four identical no-tachometer notes and five identical helper-floor
           lines on this appliance - and four copies of a sentence is not four facts. Folded only
           when every row says exactly the same thing: where they disagree, folding is refused
           and each row keeps its own words. */
        function fold_note(items, getter) {
            const notes = items.map(getter);
            if (notes.length < 2) {
                return null;
            }
            for (let i = 0; i < notes.length; i++) {
                if (!notes[i] || notes[i] !== notes[0]) {
                    return null;
                }
            }
            return notes[0];
        }

        /* Which disclosures are open, held here and not in the DOM, because the rows are rebuilt
           from the state document every five seconds and a paragraph that shut itself under the
           reader every five seconds would be unreadable. */
        const open_strips = {};

        /* fa-angle-down and fa-angle-up, never the left/right pair: a horizontal glyph is not
           flipped by the mirrored stylesheet, so a caret would point the wrong way in the Arabic
           layout while a vertical one means the same thing in both. */
        function set_strip($button, $body, open) {
            $body.toggle(!!open);
            $button.attr('aria-expanded', open ? 'true' : 'false');
            $button.find('i.fa').first()
                .toggleClass('fa-angle-down', !open)
                .toggleClass('fa-angle-up', !!open);
        }

        function bind_strip($button, $body, key, fallback) {
            if (!(key in open_strips)) {
                open_strips[key] = !!fallback;
            }
            set_strip($button, $body, open_strips[key]);
            $button.off('click.fc').on('click.fc', function () {
                open_strips[key] = !open_strips[key];
                set_strip($button, $body, open_strips[key]);
            });
        }

        /* The content is always built, only hidden, so that the browser own find still reaches
           it and a reader who never clicks has lost nothing that a search would have found. */
        function strip($into, key, title, lines) {
            const $button = $('<button type="button" class="fc-strip"/>')
                .append($('<i class="fa fa-fw fa-angle-down"/>'))
                .append(' ')
                .append($('<span/>').text(String(title)));
            const $body = $('<div class="fc-strip-body"/>');
            (lines || []).forEach(function (line) {
                $body.append($('<div class="fc-note"/>').text(String(line)));
            });
            $into.append($button).append($body);
            bind_strip($button, $body, key, false);
        }

        /* How old a document is, measured against the firewall own clock and never against the
           browser. A laptop ten minutes out must not be able to call a fresh record ten minutes
           old, so the state document timestamp is the only "now" this page has - and before that
           document arrives nothing is said at all rather than something guessed. A clock that
           went backwards reads as nothing having passed, never as a negative age. */
        function doc_age_seconds(at) {
            const now = Number((state_doc || {}).at);
            const then = Number(at);
            if (!isFinite(now) || !isFinite(then)) {
                return null;
            }
            return Math.max(0, now - then);
        }

        function age_text(seconds) {
            if (seconds === null) {
                return '';
            }
            if (seconds < 90) {
                return fc_fill("{{ lang._('{n} s') }}", {n: Math.round(seconds)});
            }
            if (seconds < 5400) {
                return fc_fill("{{ lang._('{n} min') }}", {n: Math.round(seconds / 60)});
            }
            return fc_fill("{{ lang._('{n} h') }}", {n: Math.round(seconds / 3600)});
        }

        /* The header colours on the panic point and on nothing else. The limit is a SETTING:
           colouring on it would paint an alarm across the top of a machine that has never once
           throttled, because somebody typed 70 into a box. The rows still mark the limit, on the
           ladder, where it is a mark and not a mood. */
        function tone_for(celsius) {
            const panic = Number((state_doc && state_doc.curve || {}).panic_c);
            const reading = Number(celsius);
            return (isFinite(reading) && isFinite(panic) && reading > panic) ? 'fc-hot' : '';
        }

        /* The name a person reads for a recorded load series. The record calls it cpu. */
        const load_title = {
            'cpu': "{{ lang._('processor busy') }}"
        };

        /* Whether the channels that report no fan are expanded. Four of the five on this
           appliance report nothing, and four rows that each say there is nothing here is three
           rows too many - but somebody who has just wired a fan to one of them wants it at once,
           so they are one click away and the summary names them. */
        let show_silent = false;
        /* The processor's cores, one row until asked for: on most of these machines every core
           reads the same number, and eight identical bars said nothing the hottest one did not. */
        let show_cores = false;

        /* The incident that has been read. Keyed on the incident itself, so a NEW one appears
           even after an old one was dismissed. */
        let incident_dismissed = '';

        /* ------------------------------------------------------------------ the header

           The four things somebody standing in front of a hot appliance needs, above the tab
           strip rather than inside one. Until today every one of them lived on the Fans tab -
           and the incident panel lived inside that same pane - so opening Graphs, Settings or
           Hardware cost the reader the heat, the speed, the custody and the failed-restore
           banner all at once. */
        function draw_vitals() {
            const cells = ['#fc-vital-heat', '#fc-vital-air', '#fc-vital-custody'];
            if (!is_object(state_doc)) {
                cells.forEach(function (id) {
                    $(id).removeClass('fc-hot').removeAttr('title');
                    $(id).find('.fc-vital-value').empty().text('—');
                    $(id).find('.fc-vital-sub').empty()
                        .text("{{ lang._('Asking the firewall...') }}");
                });
                $('#fc-record').empty();
                $('#fc-age').empty();
                return;
            }

            /* Heat. The sensor is the one the record calls hottest, matched by name against the
               live list; where the record has not arrived, the hottest live reading. The number
               is this second. The room it has left is the collector own PER-SENSOR figure and is
               never a subtraction done here: a tjmax is meaningless against a chipset zone, and
               the collector already knows which limit belongs to which sensor. */
            const sensors = ((state_doc.temperatures || {}).sensors) || [];
            const wanted = ((trend_doc || {}).hottest || {}).sensor;
            let hot = null;
            sensors.forEach(function (sensor) {
                if (sensor.celsius === null || sensor.celsius === undefined) {
                    return;
                }
                if (wanted && sensor.name === wanted) {
                    hot = sensor;
                }
            });
            if (hot === null) {
                sensors.forEach(function (sensor) {
                    if (sensor.celsius === null || sensor.celsius === undefined) {
                        return;
                    }
                    if (hot === null || Number(sensor.celsius) > Number(hot.celsius)) {
                        hot = sensor;
                    }
                });
            }

            const $heat = $('#fc-vital-heat').removeClass('fc-hot');
            const $heat_value = $heat.find('.fc-vital-value').empty();
            const $heat_sub = $heat.find('.fc-vital-sub').empty();
            if (hot === null) {
                $heat_value.append(value(null, '', ''));
                $heat_sub.text("{{ lang._('not read') }}");
            } else {
                $heat_value.text(Number(hot.celsius).toFixed(1) + unit_suffix('c'));
                $heat.addClass(tone_for(hot.celsius));
                const said = [String(hot.label || fc_sensor(hot.name))];
                if (hot.headroom_c !== null && hot.headroom_c !== undefined && hot.limit_name) {
                    said.push(fc_fill("{{ lang._('{headroom} C below {limit}') }}", {
                        headroom: fc_num(hot.headroom_c), limit: String(hot.limit_name)
                    }));
                }
                $heat_sub.text(said.join(', '));
            }

            /* Air. The fastest channel that reports a speed, and a count of the ones that report
               none - which on this appliance is four of five, and is not four stopped fans. */
            const fans = $.isArray(state_doc.fans) ? state_doc.fans : [];
            let loudest = null;
            let reporting = 0;
            const quiet = [];
            fans.forEach(function (fan) {
                const rpm = Number(fan.rpm);
                if (!isFinite(rpm) || rpm <= 0) {
                    quiet.push(String(fan.channel || ''));
                    return;
                }
                reporting += 1;
                if (loudest === null || rpm > Number(loudest.rpm)) {
                    loudest = fan;
                }
            });

            const $air_value = $('#fc-vital-air').find('.fc-vital-value').empty();
            const $air_sub = $('#fc-vital-air').find('.fc-vital-sub').empty();
            if (loudest === null) {
                $air_value.append(value(null, '', ''));
                $air_sub.text(quiet.length
                    ? fc_fill("{{ lang._('{count} channels report no fan:') }}",
                        {count: quiet.length}) + ' ' + quiet.join(', ')
                    : '');
            } else {
                $air_value.text(fc_num(loudest.rpm) + unit_suffix('rpm'));
                /* Every field on this line is printed only if the document carried it.
                   String(undefined) is the word undefined, and a reader who is told that
                   undefined is turning at 5113 rpm has been told nothing twice. */
                const said = [];
                if (loudest.channel !== null && loudest.channel !== undefined
                        && loudest.channel !== '') {
                    said.push(String(loudest.channel));
                }
                if (loudest.duty !== null && loudest.duty !== undefined) {
                    said.push(String(loudest.duty) + '%');
                }
                if (loudest.label && loudest.label !== 'unknown') {
                    said.push(String(loudest.label));
                }
                if (reporting > 1) {
                    said.push(fc_fill("{{ lang._('and {count} more') }}",
                        {count: reporting - 1}));
                }
                $air_sub.text(said.join(', '));
            }

            /* Custody. Four fields on one line and no fifth word invented out of them: whether
               anything is driven, the service, the plugin mode, and the chip own register word
               beside them as a chip whose title says whose word it is. "off" next to a fan
               turning at 5113 rpm is the chip talking about a register and not about a fan, and
               a page that reconciled the two would be printing a verdict nobody measured. */
            const driven = fans.filter(function (fan) { return !!fan.driven; });
            const $custody = $('#fc-vital-custody');
            const $custody_value = $custody.find('.fc-vital-value').empty();
            const $custody_sub = $custody.find('.fc-vital-sub').empty();
            $custody_value.append(driven.length
                ? label("{{ lang._('this plugin') }}", 'label-info')
                : label("{{ lang._('the hardware') }}", 'label-default'));

            const service = (state_doc.service && state_doc.service.running)
                ? 'running'
                : (state_doc.enabled === false ? 'disabled' : 'stopped');
            $custody_sub.append($('<span/>').text(fc_fill("{{ lang._('service: {state}') }}",
                {state: service_text[service]})));
            if (state_doc.mode) {
                $custody_sub.append(' ').append($('<span/>').text(
                    fc_fill("{{ lang._('plugin mode: {mode}') }}",
                        {mode: String(state_doc.mode)})));
            }
            const word = driven.length ? driven[0].mode : (fans.length ? fans[0].mode : null);
            if (word !== null && word !== undefined && word !== '') {
                $custody_sub.append(' ').append(chip(String(word), 'text-muted',
                    fc_fill("{{ lang._('the chip calls this mode: {mode}') }}",
                        {mode: String(word)})));
            }
            if (state_doc.state) {
                $custody.attr('title', String(state_doc.state));
            }

            /* The two figures the record can answer and the page has never once printed: how
               much room the hottest sensor had over the last day, and whether this processor has
               ever slowed itself down for heat. They are in trend.hottest, and the verdict path
               returns before reaching them whenever there is not yet enough to measure from -
               which is the ordinary state of a fresh installation. Read here instead, where no
               verdict can swallow them, in the same two sentences the verdict would have used. */
            const hottest = (trend_doc || {}).hottest;
            const $record = $('#fc-record').empty();
            if (is_object(hottest)) {
                const said = fc_headroom({headroom_c: hottest.headroom_c,
                                          throttled: hottest.throttle_log});
                if (said) {
                    $record.append($('<span/>').text(said.replace(/^\s+/, '')));

                    /* And beside it, the coverage of the window that sentence is about. This is
                       the rule every row on the Readings tab already keeps, and the header was
                       the one place a record figure reached the glass without it: on this
                       appliance the day window holds 3827 seconds of a day, so the sentence
                       above describes an hour and calls it a day.

                       It matters more here than in a row, and in the one direction that is not
                       safe to be wrong in. The figure is a MAXIMUM and the headroom is a
                       subtraction from it, so a day the record mostly slept through can only
                       ever be hiding a HOTTER reading than the one it saw - which makes the
                       unqualified sentence claim more room before the CPU protects itself than
                       anybody has watched for. The number is still worth printing; what is not
                       allowed is printing it as though the record had been awake all day.

                       The window is the day window of the sensor the record itself calls
                       hottest, never another sensor and never another window, because that is
                       the exact set of samples hottest.max_c was taken over. Where that window
                       is not in the document the sentence says so rather than falling through
                       to a silent nothing: a coverage nobody recorded is not a coverage of
                       zero, and the two do not become one on the glass. */
                    const day = ((((trend_doc || {}).series || {})['T|' + hottest.sensor]
                                  || {}).windows || {}).day;
                    const cover = is_object(day) ? day.coverage : null;
                    const watched = (cover === null || cover === undefined
                                     || !isFinite(Number(cover)))
                        ? "{{ lang._('how much of that day was watched is not recorded') }}"
                        : trend_coverage(cover);
                    if (watched) {
                        $record.append(' ')
                            .append($('<span class="fc-row-thin"/>').text(watched));
                    }
                }
            }

            /* One age, and it is the record against the readings. Both timestamps are the
               firewall own, so their difference is sound without the browser and the firewall
               agreeing about anything. */
            const age = doc_age_seconds((trend_doc || {}).at);
            $('#fc-age').text(age === null ? '' : fc_fill(
                "{{ lang._('the record is {age} behind these readings') }}",
                {age: age_text(age)}));
        }

        /* ------------------------------------------------------------------ the contract line

           (The marker above is deliberately not the one the patch script anchors on, so that a
           second run of that script cannot find its own output and edit it again.) */

        /* Six lines, and the plugin drives nothing unless every one of them says yes. Written as
           a list rather than as a single verdict on purpose: "not driving" on its own is the
           answer that makes somebody uninstall a plugin, and the line that is failing is almost
           always one they can do something about. It is one line with a count now, because six
           paragraphs of prose were the first thing on the page even on a machine where all six
           held - and it opens on the failing ones first. */
        function draw_contract_strip() {
            const $head = $('#fc-contract-head').empty();
            const $body = $('#fc-contract-body').empty();
            const lines = (state_doc && $.isArray(state_doc.contract)) ? state_doc.contract : [];

            if (lines.length === 0) {
                $head.append($('<span/>').text(
                    "{{ lang._('The firewall did not report the safety contract.') }}"));
                return;
            }

            const met = lines.filter(function (line) { return line.ok; }).length;
            if (met === lines.length) {
                $head.append(label("{{ lang._('all conditions met') }}", 'label-success'));
            } else {
                $head.append(label(fc_fill("{{ lang._('{met} of {all} conditions met') }}",
                    {met: met, all: lines.length}), 'label-warning'));
                $head.append(' ').append($('<span class="fc-sub"/>').text(
                    "{{ lang._('While any of these is unmet, no duty is written and the fans stay under hardware control.') }}"));
            }

            /* What the service is doing and where these readings came from, in the firewall own
               words. It was an alert at the top of the Fans tab; it is the first thing in the
               drawer behind the contract, which is the same place and less of the screen. */
            draw_service_state($('<div class="fc-note"/>').appendTo($body));

            $body.append($('<div class="fc-note"/>').text(
                "{{ lang._('Six conditions. This plugin writes nothing to a fan unless every one of them holds, and it checks them again every time it makes a decision - so a sensor that stops answering, or a fan that stops turning, hands the fans straight back to the hardware rather than waiting for somebody to notice.') }}"));

            /* Failing lines first: where five hold and one does not, the one that does not is
               the whole message. */
            const ordered = lines.slice().sort(function (a, b) {
                return (a.ok ? 1 : 0) - (b.ok ? 1 : 0);
            });
            ordered.forEach(function (line) {
                const $row = $('<div class="fc-row-why"/>');
                $row.append($('<i class="fa fa-fw"/>').addClass(
                    line.ok ? 'fa-check text-success' : 'fa-times text-warning'));
                $row.append(' ');
                $row.append($('<span class="fc-strong"/>').text(
                    contract_title[line.name] || String(line.name || '')));
                $row.append($('<div class="fc-note"/>').text(String(line.detail || '')));
                $body.append($row);
            });

            /* Closed until opened: the count above says how many hold, and on hardware nobody has
               measured none of them can - the six explanations every visit was most of the page. */
            bind_strip($('#fc-contract-strip'), $body, 'contract', false);
        }

        /* ------------------------------------------------------------------ one row

           A sensor and a fan are the same shape: a name, what it reads now, the band the record
           has for it, and whatever the documents say underneath. It draws with series === null
           as well, so a fresh installation and a channel the record has never seen still produce
           a row with a live number in it and an empty track behind it. */
        function row(opts) {
            /* opts.card draws the same reading as a card in a grid - the temperatures are drawn
               this way: the name over its sensor, the reading large, a short band, and the three
               figures of the window under it - rather than a line as wide as the page. */
            const $row = $('<div class="fc-row"/>').toggleClass('fc-card', !!opts.card);
            const suffix = unit_suffix(opts.unit);
            const decimals = opts.decimals || 0;
            /* A window the record kept a slot for and put nothing into carries min, mean and
               max as null - stats.present() returns exactly that for a window of zero seconds -
               and Number(null) is 0. Read through Number() alone, a window nobody watched
               printed 0 C three times and drew a band at the bottom of the scale, which is a
               reading this machine never took. An absent number is NaN here, and written()
               says it was not read. */
            const figure = function (v) {
                return (v === null || v === undefined || v === '') ? NaN : Number(v);
            };
            const written = function (v) {
                const n = figure(v);
                return isFinite(n) ? n.toFixed(decimals) : "{{ lang._('not read') }}";
            };
            const window_data = (opts.series && opts.series.windows)
                ? opts.series.windows[trend_window] : null;
            const coverage = window_data ? Number(window_data.coverage) : NaN;
            const thin = isFinite(coverage) && coverage < THIN_COVERAGE;
            const now = (opts.now === null || opts.now === undefined || opts.now === '')
                ? NaN : Number(opts.now);

            const $head = $('<div class="fc-row-head"/>').appendTo($row);
            const $title = opts.card ? $('<div class="fc-card-title"/>').appendTo($head) : $head;
            $('<span class="fc-row-name"/>').text(String(opts.name))
                .attr('title', opts.card ? String(opts.name) : null).appendTo($title);
            if (opts.id) {
                $('<span class="fc-row-id"/>').attr('dir', 'ltr').text(String(opts.id))
                    .attr('title', opts.card ? String(opts.id) : null).appendTo($title);
            }
            (opts.chips || []).forEach(function ($chip) { $head.append($chip); });

            /* The dir attribute goes on a span INSIDE this cell and never on the cell itself.
               .fc-row-now is what carries margin-inline-start: auto, and a logical margin is
               resolved against the direction of the element that carries it - not against the
               direction of the flex line it sits in. So dir="ltr" here becomes margin-left:
               auto in the Arabic layout, where the free space is already on the left, and the
               auto margin absorbs it instead of pushing anything: measured at 709 px of empty
               line with the reading parked against the sysctl. The number reads left to right
               without the attribute anyway, because unicode-bidi: plaintext at the top of the
               stylesheet reaches every fc- element and takes the direction from the first
               strong character - which in 63.0 C is the C. */
            const $now = $('<span class="fc-row-now"/>').appendTo($head);
            /* A card's reading takes the theme's warning colour at the limit and its danger
               colour past the panic point - judged on the reading now, which is what the large
               number is, and never on the window's highest. */
            if (opts.now_tone === 'fc-hot') {
                $now.addClass('text-danger');
            } else if (opts.now_tone === 'fc-warm') {
                $now.addClass('text-warning');
            }
            if (isFinite(now)) {
                $now.append($('<span/>').attr('dir', 'ltr').text(written(now) + suffix));
            } else {
                $now.append(value(null, '', opts.absent || ''));
            }

            const low = window_data ? figure(window_data.min) : NaN;
            const high = window_data ? figure(window_data.max) : NaN;
            const mean = window_data ? figure(window_data.mean) : NaN;
            /* The fade for a thinly watched window goes on the BAND and not on the row: dimming
               the row dims the sensor name and the reading now, neither of which is any less
               certain for the record having been asleep. */
            bar(low, high, mean, isFinite(now) ? now : null, opts.top, opts.rungs || [],
                opts.tone || '').toggleClass('fc-thin', thin).appendTo($row);

            if (window_data && opts.card) {
                /* the three figures each under its own word, in three even columns */
                const $stats = $('<div class="fc-card-stats"/>').appendTo($row);
                [
                    ["{{ lang._('lowest') }}", low],
                    ["{{ lang._('mean') }}", mean],
                    ["{{ lang._('highest') }}", high]
                ].forEach(function (pair) {
                    $('<div class="fc-card-stat"/>')
                        .append($('<span class="fc-card-stat-key"/>').text(pair[0]))
                        .append($('<span class="fc-card-stat-value"/>').attr('dir', 'ltr')
                            .text(written(pair[1]) + suffix))
                        .appendTo($stats);
                });
                const spent = trend_above(window_data.above, opts.rungs || []);
                const watched = trend_coverage(coverage);
                if (spent || watched) {
                    const $foot = $('<div class="fc-row-foot"/>').appendTo($row);
                    if (spent) {
                        $('<span class="fc-row-above"/>').text(spent).appendTo($foot);
                    }
                    if (watched) {
                        $('<span class="fc-row-thin"/>').text(watched).appendTo($foot);
                    }
                }
            } else if (window_data) {
                const $foot = $('<div class="fc-row-foot"/>').appendTo($row);
                $('<span/>').attr('dir', 'ltr').text(
                    written(low) + suffix + ' / ' + written(mean) + suffix + ' / '
                    + written(high) + suffix).appendTo($foot);
                $('<span class="fc-row-legend"/>').text(
                    "{{ lang._('lowest / mean / highest') }}").appendTo($foot);

                /* The speed beside the duty, in the footer and never behind a disclosure. The
                   bar it used to have was drawn against the fastest reading this record happened
                   to hold, which is a scale whose meaning changes as the record grows - and the
                   dashboard widget already refuses to bar an rpm for the stated reason that
                   nothing here knows what this fan can do. One page, one rule. */
                const extra = opts.extra ? (opts.extra.windows || {})[trend_window] : null;
                /* Through figure() and checked, like every other number in this row. Read
                   straight out of the record it printed NaN / NaN / NaN rpm where a key was
                   missing and 0 / 0.0 / 0 rpm where the window was empty, and a speed is the
                   one number on this page a reader would act on. Nothing is said instead:
                   the duty line above already says the window was thin. */
                const rpm_low = extra ? figure(extra.min) : NaN;
                const rpm_mean = extra ? figure(extra.mean) : NaN;
                const rpm_high = extra ? figure(extra.max) : NaN;
                if (isFinite(rpm_low) && isFinite(rpm_mean) && isFinite(rpm_high)) {
                    $('<span/>').attr('dir', 'ltr').text(
                        Math.round(rpm_low) + ' / ' + rpm_mean.toFixed(1) + ' / '
                        + Math.round(rpm_high) + unit_suffix('rpm')).appendTo($foot);
                }

                const spent = trend_above(window_data.above, opts.rungs || []);
                if (spent) {
                    $('<span class="fc-row-above"/>').text(spent).appendTo($foot);
                }

                /* Coverage stays on the row. It is NOT the same across the series in one window
                   - the load series is sampled by a different writer and reads lower than the
                   rest - so a single number on the window button would be wrong for it. */
                const watched = trend_coverage(coverage);
                if (watched) {
                    $('<span class="fc-row-thin"/>').text(watched).appendTo($foot);
                }
            }

            (opts.why || []).forEach(function (part) {
                const $why = $('<div class="fc-row-why"/>');
                if (typeof part === 'string') {
                    $why.text(part);
                } else {
                    $why.append(part);
                }
                $row.append($why);
            });

            (opts.more || []).forEach(function (item) {
                strip($row, item.key, item.title, item.lines);
            });

            return $row;
        }

        /* ------------------------------------------------------------------ the curve */

        /* Drawn from the sample the firewall sent, which curve.py produced with the same code
           that decides a duty - the rounding up to a step, the floor and the cliff at the limit
           are all in that code, and a picture redrawn here from the four numbers in the box
           would agree with the box and not with the daemon. */
        function draw_curve(target) {
            const $body = $(target).empty();
            const curve = (state_doc && is_object(state_doc.curve)) ? state_doc.curve : null;
            if (curve === null) {
                return;
            }
            if (curve.error) {
                $body.append($('<div class="alert alert-warning fc-alert" role="alert"/>').text(
                    "{{ lang._('This curve cannot be used:') }}" + ' ' + String(curve.error)));
                return;
            }
            const sample = $.isArray(curve.sample) ? curve.sample : [];
            if (sample.length < 2) {
                return;
            }

            const width = 520;
            const height = 150;
            const pad = 24;
            const low = sample[0].celsius;
            const high = sample[sample.length - 1].celsius;
            const x = function (celsius) {
                return pad + (celsius - low) / (high - low) * (width - pad * 2);
            };
            const y = function (duty) {
                return height - pad - (duty / 100) * (height - pad * 2);
            };

            const points = sample.map(function (entry) {
                return x(entry.celsius).toFixed(1) + ',' + y(entry.duty).toFixed(1);
            }).join(' ');

            /* Built as a string because it is one shape and a namespace-correct jQuery SVG build
               is four times the code. Every colour is currentColor or a theme class, so this
               drawing follows a theme into the dark instead of carrying values of its own. */
            const svg = [
                '<svg class="fc-curve" viewBox="0 0 ' + width + ' ' + height + '" ',
                'preserveAspectRatio="xMidYMid meet" role="img">',
                '<line x1="' + pad + '" y1="' + y(0) + '" x2="' + (width - pad) + '" y2="' + y(0) +
                    '" class="fc-axis"/>',
                '<line x1="' + pad + '" y1="' + y(0) + '" x2="' + pad + '" y2="' + y(100) +
                    '" class="fc-axis"/>',
                '<line x1="' + pad + '" y1="' + y(curve.floor) + '" x2="' + (width - pad) +
                    '" y2="' + y(curve.floor) + '" class="fc-floor"/>',
                '<line x1="' + x(curve.limit_c) + '" y1="' + y(0) + '" x2="' + x(curve.limit_c) +
                    '" y2="' + y(100) + '" class="fc-limit"/>',
                '<polyline points="' + points + '" class="fc-line"/>',
                '<text x="' + pad + '" y="' + (height - 6) + '" class="fc-tick">' + low + ' C</text>',
                '<text x="' + (width - pad) + '" y="' + (height - 6) +
                    '" class="fc-tick" text-anchor="end">' + high + ' C</text>',
                '<text x="' + (pad + 4) + '" y="' + (y(curve.floor) - 4) + '" class="fc-tick">' +
                    curve.floor + '%</text>',
                '</svg>'
            ].join('');

            $body.append($(svg));
            $body.append(note(
                "{{ lang._('The flat line is the floor, the upright one the limit temperature. The steps are the rounding: a duty is rounded up to the step, so the fan is never slower than the curve asked for.') }}"
            ));
        }

        /* ------------------------------------------------------------------ the hardware tab */

        function draw_chip() {
            const $body = $('#chip-body').empty();
            const found = (probe_doc && is_object(probe_doc.chip)) ? probe_doc.chip : null;
            const layer = (probe_doc && is_object(probe_doc.hardware_layer))
                ? probe_doc.hardware_layer : null;

            $body.append(hw_wide(help_note(
                "{{ lang._('What was found in this machine, what the shipped table says about it, and the one instrument this plugin has: the floor test. Probing reads and writes nothing.') }}"
            )));
            if (found === null) {
                $body.append(hw_wide(note("{{ lang._('Asking the firewall...') }}")));
                return;
            }

            /* Where the chip is, on one line. The monitoring window, the index port and the data
               port are three numbers describing one fact, and three rows of a table said that
               fact three times. */
            const ports = [found.hwm_base, found.index_port, found.data_port]
                .filter(function (part) {
                    return part !== null && part !== undefined && part !== '';
                }).join(' ');
            const rows = [
                ["{{ lang._('Chip') }}", found.chip],
                ["{{ lang._('Device id') }}", found.id],
                ["{{ lang._('Where the chip is') }}", ports],
                ["{{ lang._('Read through') }}", found.source],
                ["{{ lang._('Can be written to') }}", found.writable
                    ? "{{ lang._('yes') }}" : "{{ lang._('no') }}"],
                ["{{ lang._('Lowest duty the helper will ever allow') }}",
                    found.hard_floor === null || found.hard_floor === undefined
                        ? '' : found.hard_floor + '%']
            ];
            rows.forEach(function (line) {
                if (line[1] === undefined || line[1] === null || line[1] === '') {
                    return;
                }
                $body.append(hw_row(line[0], tech(line[1])));
            });

            /* When this probe was taken, against the firewall own clock. A reading twenty
               minutes old was presented exactly like one taken this second until today. */
            const age = doc_age_seconds((probe_doc || {}).at);
            if (age !== null) {
                $body.append(hw_wide(note(fc_fill("{{ lang._('this probe was taken {age} ago') }}",
                    {age: age_text(age)}), 'fc-muted')));
            }

            /* What each channel was found under before this plugin touched it, per channel, from
               the helper own record. The contract line reports one date for the whole daemon
               baseline; this is a different book kept by a different writer, and both are shown
               without either being reconciled to the other here. */
            const channels = $.isArray(found.channels) ? found.channels : [];
            if (channels.length) {
                const $saved = $('<div/>');
                channels.forEach(function (channel) {
                    $saved.append(chip(
                        String(channel.name || '') + ' ' + (channel.saved
                            ? "{{ lang._('baseline captured') }}"
                            : "{{ lang._('not recorded') }}"),
                        channel.saved ? 'text-success' : 'text-muted', '')).append(' ');
                });
                $body.append(hw_row("{{ lang._('Baseline per channel') }}", $saved));
            }

            if (found.detail) {
                $body.append(hw_wide(help_note(String(found.detail))));
            }
            /* Channel 1 is the only channel on this family with an output-type bit, and a DC
               channel drives its fan by voltage: a duty on it does not mean what a duty on a
               PWM channel means. Said where somebody is about to measure a floor on it. */
            if (String(found.channel1_output || '') === 'dc') {
                $body.append(hw_wide(note(
                    "{{ lang._('Channel 1 is set to DC output, which drives its fan by voltage rather than by a pulse width. A duty on that channel does not mean the same thing as a duty on a PWM channel, and a floor measured on it is a voltage floor.') }}",
                    'text-warning')));
            }
            /* Why the read-only path is in use, in the layer own words. This is the ordinary
               case on a machine where the helper was never built or may not open /dev/io, and
               it is a sentence rather than a blank space. */
            if (is_object(found.unavailable)) {
                $body.append(hw_wide(note(
                    "{{ lang._('The helper could not be used, so these readings came from the kernel driver instead:') }}"
                    + ' ' + String(found.unavailable.error || '')
                    + (found.unavailable.slug ? ' (' + String(found.unavailable.slug) + ')' : ''),
                    'text-warning')));
            }
            /* The honest, common answer on hardware nobody has written a layer for: the readings
               are real and the writing is not available. Said plainly, in the script's own words,
               rather than shown as an empty panel. */
            if (found.problem) {
                $body.append(hw_wide(note(String(found.problem), 'text-warning')));
            }
            if (layer !== null && layer.present === false) {
                $body.append(hw_wide(note(
                    "{{ lang._('Without the hardware layer this plugin reads and reports and can write nothing at all. Everything on the Fans tab is still a real reading.') }}"
                )));
            }
        }

        function draw_model() {
            const $body = $('#model-body').empty();
            if (probe_doc === null) {
                $body.append(hw_wide(note("{{ lang._('Asking the firewall...') }}")));
                return;
            }
            const machine = is_object(probe_doc.machine) ? probe_doc.machine : {};
            const table = is_object(probe_doc.table) ? probe_doc.table : {};
            const print = is_object(probe_doc.fingerprint) ? probe_doc.fingerprint : {};

            $body.append(hw_row("{{ lang._('Model') }}", $('<div/>')
                .append($('<span class="fc-strong"/>').text(
                    String(machine.display || "{{ lang._('unrecognised appliance') }}")))
                .append(' ')
                .append(tech([machine.maker, machine.product, machine.version].filter(function (part) {
                    return part;
                }).join(' / ') || "{{ lang._('this machine reports no SMBIOS names') }}").addClass('fc-muted'))));

            /* the table's verdict in plain sight; its reasons, which run to a paragraph, under
               full help */
            const $control = $('<div/>').append(table.control
                ? label("{{ lang._('the table permits control on this model') }}", 'label-info')
                : label("{{ lang._('the table does not permit control on this model') }}",
                        'label-default'));
            if (table.reason) {
                $control.append(help_note(String(table.reason)));
            }
            if (table.hint) {
                $control.append(help_note(String(table.hint)));
            }
            $body.append(hw_row("{{ lang._('Fan control') }}", $control));

            if (print.ok === false) {
                const complaints = $.isArray(print.complaints) ? print.complaints : [];
                $body.append(hw_wide(note(
                    "{{ lang._('The chip in this machine does not match the table entry it was matched to, so nothing will be driven:') }}" +
                    ' ' + complaints.join(' '), 'text-warning')));
            }

            /* What the table admits to, printed as it was written - nothing truncated, nothing
               summarised, no label this page invented. Behind a count, because it is 3400
               characters of reference on this model and a reference is not a reading. */
            const notes = $.isArray(table.notes) ? table.notes : [];
            if (notes.length > 0) {
                const $notes = $('<div/>');
                strip($notes, 'model-notes',
                    fc_fill("{{ lang._('{n} notes about this model') }}", {n: notes.length}),
                    notes.map(String));
                $body.append(hw_wide($notes));
            }
        }

        /* The paragraphs that justify the shipped curve, under the box where somebody is about
           to change it. They are in the probe document and the page has never drawn them; they
           are the answer to the only question anybody editing that field has. */
        function draw_curve_why() {
            const $into = $('#fc-curve-why');
            if ($into.length === 0) {
                return;
            }
            $into.empty();
            const table = (probe_doc && is_object(probe_doc.table)) ? probe_doc.table : {};
            const curve = is_object(table.curve) ? table.curve : {};
            const comment = $.isArray(curve.comment)
                ? curve.comment : (curve.comment ? [curve.comment] : []);
            const lines = comment.filter(function (line) {
                return String(line).replace(/\s+/g, '') !== '';
            });
            if (lines.length === 0) {
                return;
            }
            strip($into, 'curve-why', "{{ lang._('Why these numbers') }}", lines.map(String));
        }

        /* The per-channel workbench: what the floor is, how to measure one, and how to hold a
           duty for a moment. Every control here is drawn from the probe, so a channel that does
           not exist cannot be typed into. */
        function draw_workbench() {
            const $body = $('#workbench-body').empty();
            const running = state_doc && is_object(state_doc.service) && state_doc.service.running;
            const chip = (probe_doc && is_object(probe_doc.chip)) ? probe_doc.chip : null;
            const channels = (chip && $.isArray(chip.channels)) ? chip.channels : [];
            const floors = (probe_doc && is_object(probe_doc.floors)) ? probe_doc.floors : {};

            $body.append(hw_wide(help_note(
                "{{ lang._('A floor is the lowest duty at which a fan that is already turning keeps turning, measured on this machine, plus one step. It is not the duty at which a stopped fan starts - that is higher on every fan ever made, and measuring it would mean stopping a fan on a firewall that is in service. Nothing is driven below a floor, and nothing is driven at all on a channel whose floor has never been measured.') }}"
            )));
            if (running) {
                $body.append(hw_wide($('<div/>')
                    .append(note("{{ lang._('The service is running and owns the chip, so nothing here can be used - stop it first.') }}", 'text-info'))
                    .append(help_note(
                        "{{ lang._('Two writers on the same two ports, with nothing to serialise either of them against the kernel driver that reads the same chip without a lock, is exactly the race this plugin is arranged to avoid.') }}"
                    ))));
                return;
            }
            if (channels.length === 0 || !chip.writable) {
                $body.append(hw_wide(note(
                    "{{ lang._('There is no writable path to a fan on this machine, so there is nothing to measure. The panel above says what was found.') }}"
                )));
                return;
            }

            channels.forEach(function (channel) {
                const name = String(channel.name || '');
                const known = is_object(floors[name]) ? floors[name] : {};
                const floor = known.floor;
                const silent = channel.tach === null || channel.tach === undefined
                    || Number(channel.tach) < 200;

                /* one row per channel: the channel and what it reads on the left, its floor and
                   the two instruments on the right */
                const $head = $('<div class="fc-bench-head"/>')
                    .append(tech(name).addClass('fc-strong'))
                    .append(' ')
                    .append(tech(number_or_dash(channel.tach, ' rpm')))
                    .append(' ')
                    .append(tech(number_or_dash(channel.duty, '%')));
                const $panel = $('<div/>');
                const add_row = function () {
                    $body.append($('<tr/>').append($('<td/>').append($head)).append($('<td/>').append($panel)));
                };

                $panel.append($('<div class="fc-note"/>').text(
                    floor === null || floor === undefined
                        ? String(known.source || '')
                        : "{{ lang._('Floor:') }}" + ' ' + floor + '% - ' + String(known.source || '')
                ));
                /* The number the helper itself will not go below, which is also the lowest
                   duty this test can reach. Shown beside the input rather than discovered by
                   being refused. */
                const helper_floor = channel.helper_floor;
                if (helper_floor !== null && helper_floor !== undefined) {
                    $panel.append($('<div class="fc-note"/>')
                        .append($('<span/>').text(
                            "{{ lang._('fanctl-io refuses anything below') }}" + ' '))
                        .append(tech(helper_floor + '%'))
                        .append($('<span/>').text(channel.helper_floor_source
                            ? ' (' + String(channel.helper_floor_source) + ')' : '')));
                }

                if (silent) {
                    /* No measuring a fan that is not reporting one. The test judges its result by
                       watching the tachometer stop, and a channel that reads zero to begin with
                       cannot produce a measurement at all - only a few seconds of writing to a
                       header nobody knows anything about. */
                    $panel.append(note(
                        "{{ lang._('This channel reports no turning fan, so there is nothing here to measure and nothing to drive.') }}"
                    ));
                    add_row();
                    return;
                }

                const $controls = $('<div class="fc-bench-controls"/>');
                const lowest = (channel.helper_floor === null
                    || channel.helper_floor === undefined) ? 1 : Number(channel.helper_floor);
                const $duty = $('<input type="number" class="form-control fc-duty"/>')
                    .attr('min', lowest).attr('max', 100).attr('step', 5)
                    .attr('data-channel', name)
                    .val(floor === null || floor === undefined ? 60 : Math.max(floor, 60));
                $controls.append($duty);
                $controls.append($('<button type="button" class="btn btn-default fc-hold"/>')
                    .attr('data-channel', name)
                    .append($('<i class="fa fa-fw fa-clock-o"/>'))
                    .append(' ')
                    .append($('<span/>').text("{{ lang._('Hold for 15 s') }}")));
                $controls.append($('<button type="button" class="btn btn-warning fc-test"/>')
                    .attr('data-channel', name)
                    .append($('<i class="fa fa-fw fa-flask"/>'))
                    .append(' ')
                    .append($('<span/>').text("{{ lang._('Measure the floor at this duty') }}")));
                $panel.append($controls);

                $panel.append(help_note(
                    "{{ lang._('Hold will not go below the floor. The measurement will, because that is what it is for: it writes the duty, watches the tachometer, and stops the moment the fan does. It hands the fan back afterwards in every case - at the end, on the fan stopping, on a signal, and on its own watchdog.') }}"
                ));
                $panel.append(help_note(
                    "{{ lang._('Neither can go below the number fanctl-io itself enforces, and nothing on this page can lower that. If a fan is still turning at that duty, the honest reading is that this is as low as this machine will let anyone go: to measure further down, write the measurement into /usr/local/etc/fanctl/floor.conf on the firewall, which is a deliberate act by somebody in front of it.') }}"
                ));
                add_row();
            });
        }

        function draw_incident() {
            const $body = $('#incident-body').empty();
            const incident = state_doc ? state_doc.incident : null;
            /* Dismissible once it has been read, and keyed on the incident itself so that a
               NEW failure appears even after an old one was waved away. */
            const mark = is_object(incident) ? JSON.stringify(incident) : '';
            $('#incident-panel').toggle(is_object(incident) && mark !== incident_dismissed);
            if (!is_object(incident)) {
                return;
            }
            /* The one state in this plugin that a human has to act on, and the only alert on the
               page that is red. */
            $body.append($('<div class="alert alert-danger fc-alert" role="alert"/>')
                .append($('<i class="fa fa-fw fa-exclamation-triangle"/>'))
                .append(' ')
                .append($('<span/>').text(
                    "{{ lang._('The fans could not be handed back to the hardware. They may still be held at the last duty this plugin wrote. Power the appliance off and on to return them to firmware control - a warm reboot does not necessarily clear a fan register.') }}"
                )));
            const failed = $.isArray(incident.failed) ? incident.failed : [];
            failed.forEach(function (item) {
                $body.append($('<div class="fc-note"/>')
                    .append(tech(String(item.channel || '')))
                    .append(' ')
                    .append($('<span/>').text(String(item.detail || ''))));
            });
            $body.append($('<button type="button" id="restoreAct2" class="btn btn-danger fc-action"/>')
                .text("{{ lang._('Try handing them back again') }}"));
            $body.append($('<button type="button" class="btn btn-default fc-action fc-dismiss"/>')
                .text("{{ lang._('Dismiss') }}")
                .on('click', function () {
                    incident_dismissed = mark;
                    $('#incident-panel').hide();
                }));
        }

        /* ------------------------------------------------------------------ loading */

        function draw_service_state($line) {
            const state = (state_doc && state_doc.service && state_doc.service.running)
                ? 'running'
                : (state_doc && state_doc.enabled === false ? 'disabled' : 'stopped');
            $line.empty();
            $line.append(label(service_text[state], service_class[state]));
            const sentence = {
                'running': "{{ lang._('The controller is up. What it is doing to each fan is in the table below.') }}",
                'stopped': "{{ lang._('Fan control is enabled but the service is not running. Its log under System will say why.') }}",
                'disabled': "{{ lang._('Fan control is turned off, so every fan is under the law the firmware left it under. The readings below are still live.') }}"
            }[state] || '';
            $line.append(' ').append($('<span class="fc-sub"/>').text(sentence));
            if (state_doc && state_doc.state) {
                $line.append($('<div class="fc-note"/>').text(String(state_doc.state)));
            }
            if (state_doc && state_doc.source === 'direct') {
                $line.append($('<div class="fc-note"/>').text(
                    {# No apostrophe in this sentence, and none anywhere else in this file
                       either. Volt compiles a lang._() call into a PHP single-quoted string,
                       so an apostrophe has to arrive here already escaped for PHP - and an
                       escape that is one backslash out takes the whole page down with a parse
                       error rather than showing a stray character. Rewording costs nothing. #}
                    "{{ lang._('These readings were taken by this page. While the service runs, what is shown here is the record the controller itself kept of what it decided, because two processes reading one chip through the same two ports is the thing this plugin avoids.') }}"
                ));
            }
        }

        /* ------------------------------------------------------------------
           The sentence the plugin writes about this appliance.

           stats.verdict() returns a CODE and the numbers the sentence needs,
           and never the sentence. The words are built here, through lang._(),
           because lang._() resolves at template-compile time against a literal
           and cannot reach a string that was assembled in Python. A finished
           English sentence arriving from the collector is a sentence no
           catalogue can translate, and half an Arabic page with English under
           every verdict is worse than either language alone. os-linkhealth
           learned that one the expensive way and this is the same fix.

           A code this page does not know prints nothing rather than guessing,
           and the numbers are still in the table above it. A verdict added
           later is a string added here and in i18n/ui/source.json, and
           tools/check-strings.py reports the one that was forgotten.
           ------------------------------------------------------------------ */
        function fc_num(value) {
            const number = Number(value);
            return isNaN(number) ? String(value != null ? value : '') : number.toLocaleString();
        }

        function fc_fill(template, values) {
            let out = template;
            $.each(values, function (key, value) {
                out = out.split('{' + key + '}').join(String(value));
            });
            return out;
        }

        /* The name a person reads for a sysctl. The same mapping sensors.py
           makes for the live table, repeated here rather than sent, because a
           label is the one field in this plugin that is pure presentation. */
        function fc_sensor(name) {
            /* The name is not always there, and this function took the whole page down when
               it was not. history.py writes hottest.sensor and verdict.params.sensor from
               stats.hottest(), which answers None when the day window holds no temperature
               series at all - a machine with no readable sensor, or one whose record has not
               reached a day yet - and the verdict is still written, carrying that null
               (history.py, hot_name from stats.hottest(temperature_day); stats.py,
               params['sensor'] = hottest_sensor). trend_text() then calls this before it
               looks at the code, so even the waiting verdict, which never prints a sensor
               name, went through here. null.indexOf is a TypeError, it escaped the reload
               callback on every tick, and it took the rest of reload_state() - the floor note
               and the out-of-date mark on the curve - with it. */
            const text = String(name === null || name === undefined ? '' : name);
            const parts = text.split('.');
            if (text.indexOf('dev.cpu.') === 0 && parts.length >= 3) {
                return fc_fill("{{ lang._('core {n}') }}", {n: parts[2]});
            }
            if (text.indexOf('dev.pchtherm.') === 0) { return "{{ lang._('chipset') }}"; }
            if (text.indexOf('hw.acpi.thermal.') === 0 && parts.length >= 4) {
                return fc_fill("{{ lang._('board zone {n}') }}",
                    {n: parts[3].replace('tz', '')});
            }
            if (text.indexOf('dev.nvme.') === 0 && parts.length >= 3) {
                return fc_fill("{{ lang._('drive {n}') }}", {n: parts[2]});
            }
            return text;
        }

        /* The qualifier, when the readings had not finished moving. It is
           appended to whichever verdict was chosen rather than replacing it:
           the figure is still the best one there is, it is just a floor under
           the real one. */
        function fc_qualifier(verdict) {
            const seconds = {seconds: fc_num((verdict.params || {}).settle_seconds)};

            /* Two of these, and the difference between them is the difference between
               knowing something and not. lower_bound: the readings WERE still moving,
               so the figure is a floor under the real one. settling_unknown: there
               were too few samples after the change to tell either way, which is a
               weaker thing to say and is said more weakly. */
            if (verdict.qualifier === 'trend.lower_bound') {
                return ' ' + fc_fill(
                    "{{ lang._('The readings had not finished settling {seconds} seconds after the change, so this is what the fan had bought by then and not what it will buy.') }}",
                    seconds);
            }
            if (verdict.qualifier === 'trend.settling_unknown') {
                return ' ' + fc_fill(
                    "{{ lang._('There were too few readings in the {seconds} seconds after each change to tell whether they had finished settling, so the figure may be a floor under the real one.') }}",
                    seconds);
            }
            return '';
        }

        /* The other half of the answer, and the half that decides what anybody
           should DO about the first half. A chassis whose fan buys little is
           only a problem if the machine is running out of room. */
        function fc_headroom(p) {
            if (p.headroom_c == null) { return ''; }
            let text = ' ' + fc_fill(
                "{{ lang._('The hottest reading in the last day left {headroom} C before this CPU protects itself.') }}",
                {headroom: fc_num(p.headroom_c)});
            if (p.throttled === 0) {
                text += ' ' + "{{ lang._('It has never once slowed itself down for heat since it was switched on.') }}";
            } else if (p.throttled > 0) {
                text += ' ' + "{{ lang._('It HAS slowed itself down for heat since it was switched on, so this machine is not merely warm.') }}";
            }
            return text;
        }

        function trend_text(verdict) {
            if (!verdict || !verdict.code) { return ''; }
            const p = verdict.params || {};
            const sensor = fc_sensor(p.sensor);
            let text = '';

            switch (verdict.code) {
                case 'trend.waiting':
                    return fc_fill(
                        "{{ lang._('Nothing has changed the fan by {step}% or more often enough to measure from: {pairs} of {looked_at} changes could be counted, and {needed} are needed. Hold a fan at full for five minutes and let it go back, and the answer will be here afterwards.') }}",
                        {step: fc_num(p.step_pct), pairs: fc_num(p.pairs),
                         looked_at: fc_num(p.looked_at), needed: fc_num(p.needed)});

                case 'trend.narrow':
                    return fc_fill(
                        "{{ lang._('The fan has only ever moved between {from}% and {to}%, which is too short a stretch to quote a rate from. What happens between {from}% and {to}% is known; what happens at the ends of the range is not.') }}",
                        {from: fc_num(p.from_pct), to: fc_num(p.to_pct)});

                case 'trend.good':
                    text = fc_fill(
                        "{{ lang._('More fan cools this chassis: {sensor} falls about {degrees} C for every ten per cent of duty, and about {span} C across the {span_pct} points that were actually watched. Measured from {pairs} changes between {from}% and {to}%, each one taken with the machine under the same load.') }}",
                        {sensor: sensor, degrees: fc_num(p.degrees),
                         span: fc_num(p.span_c), span_pct: fc_num(p.span_pct),
                         pairs: fc_num(p.pairs),
                         from: fc_num(p.from_pct), to: fc_num(p.to_pct)});
                    break;

                case 'trend.poor':
                    text = fc_fill(
                        "{{ lang._('More fan buys very little here: {sensor} falls only about {degrees} C for every ten per cent of duty, about {span} C across the {span_pct} points that were watched, and the fan turns {ratio} times faster to do it. This is a restricted chassis, not a fan that is too slow - a louder fan would not fix it.') }}",
                        {sensor: sensor, degrees: fc_num(p.degrees),
                         span: fc_num(p.span_c), span_pct: fc_num(p.span_pct),
                         ratio: fc_num(p.rpm_ratio)});
                    break;

                /* There are enough changes over a wide enough stretch, and what they
                   show is smaller than these sensors can resolve. That is a real
                   answer and a different one from "not enough to measure from": it
                   says the effect, if there is one, is below the step size of the
                   thermometer being used to look for it. */
                case 'trend.within_noise':
                    text = fc_fill(
                        "{{ lang._('Whatever more fan does for {sensor} here, it is smaller than these sensors can see: the effect would have to be at least {floor} C per ten per cent of duty to be told apart from the rounding, and it is not. Measured from {pairs} changes between {from}% and {to}%.') }}",
                        {sensor: sensor, floor: fc_num(p.noise_floor),
                         pairs: fc_num(p.pairs),
                         from: fc_num(p.from_pct), to: fc_num(p.to_pct)});
                    break;

                case 'trend.inverse':
                    text = fc_fill(
                        "{{ lang._('{sensor} gets WARMER when this fan speeds up, by about {degrees} C for every ten per cent of duty. That is what a chassis does when more air is pulled across the hot parts and over this one; it is a real reading and not a fault.') }}",
                        {sensor: sensor, degrees: fc_num(Math.abs(p.degrees))});
                    break;

                default:
                    return '';
            }

            return text + fc_qualifier(verdict) + fc_headroom(p);
        }

        /* Why a duty change was not counted. Worth showing: a reader who sees
           that eleven of fourteen changes were discarded because the load
           moved has learned something true about their machine - that its
           temperature is set by what it is doing and not by its fan - which is
           the same lesson the figure teaches, arriving earlier. */
        function trend_rejection(code, count) {
            const n = {count: fc_num(count)};
            switch (code) {
                case 'open':
                    return fc_fill("{{ lang._('{count} still settling') }}", n);
                case 'step_too_small':
                    return fc_fill("{{ lang._('{count} too small a change to measure') }}", n);
                case 'load_moved':
                    return fc_fill("{{ lang._('{count} discarded because the machine got busier or quieter at the same moment') }}", n);
                case 'load_unknown':
                    return fc_fill("{{ lang._('{count} discarded because the load could not be read') }}", n);
                case 'no_temperature':
                    return fc_fill("{{ lang._('{count} discarded because a temperature was missing on one side') }}", n);
                default:
                    return fc_fill("{{ lang._('{count} not counted') }}", n);
            }
        }

        /* Coverage. A mean over a day taken from a plugin that ran for two
           hours of it is a true statement about two hours, and the only fix
           that does not throw the number away is to print how much of the
           window it covers. */
        function trend_coverage(fraction) {
            const percent = Math.round((Number(fraction) || 0) * 100);
            if (percent >= 99) { return ''; }
            return fc_fill("{{ lang._('{percent}% of this window was watched') }}",
                {percent: fc_num(percent)});
        }

        function trend_above(above, ladder) {
            /* "3 h 12 m above 70 C". The ladder is fixed and absolute so that
               editing a setting can never rewrite what was already counted;
               a question about a temperature between two rungs is answered
               with the rungs, not with a number nobody counted. */
            const out = [];
            $.each(ladder || [], function (_, rung) {
                const seconds = Number((above || {})[rung] || 0);
                if (!seconds) { return; }
                const hours = Math.floor(seconds / 3600);
                const minutes = Math.round((seconds % 3600) / 60);
                out.push(fc_fill("{{ lang._('{time} above {rung} C') }}", {
                    rung: fc_num(rung),
                    time: hours ? fc_fill("{{ lang._('{h} h {m} m') }}", {h: hours, m: minutes})
                                : fc_fill("{{ lang._('{m} m') }}", {m: minutes})
                }));
            });
            return out.join(', ');
        }

        /* ------------------------------------------------------------------ the graphs */

        /* A bar. The track runs from zero to `top`; `low` to `high` is the band; `mean` and
           `now` are marks on it, and `rungs` are the ladder.

           Everything is a percentage of the track and nothing is placed with a left or a
           right: inset-inline-start turns with the page, so the same markup reads correctly in
           both layouts without a second stylesheet knowing about it. */
        function bar(low, high, mean, now, top, rungs, tone) {
            const scale = (value) => {
                if (value === null || value === undefined || !isFinite(value) || top <= 0) {
                    return null;
                }
                return Math.max(0, Math.min(100, (Number(value) / top) * 100));
            };

            const start = scale(low);
            const end = scale(high);
            const $track = $('<div class="fc-bar"/>');

            if (start !== null && end !== null) {
                /* A band narrower than the mark inside it is not a band. Drawn as a tick
                   instead of as a minimum-width sliver, because a sliver claims a range this
                   sensor did not have - and the three numbers underneath still say so exactly. */
                if (end - start < 0.6) {
                    $('<span class="fc-bar-flat"/>')
                        .addClass(tone || '')
                        .css('inset-inline-start', start + '%')
                        .appendTo($track);
                } else {
                    $('<span class="fc-bar-band"/>')
                        .addClass(tone || '')
                        .css({'inset-inline-start': start + '%', 'width': (end - start) + '%'})
                        .appendTo($track);
                }
            }

            (rungs || []).forEach(function (rung) {
                const at = scale(rung);
                if (at === null || at >= 100) {
                    return;
                }
                $('<span class="fc-bar-rung"/>')
                    .css('inset-inline-start', at + '%')
                    .attr('title', String(rung))
                    .appendTo($track);
            });

            const at_mean = scale(mean);
            if (at_mean !== null) {
                $('<span class="fc-bar-mean"/>').css('inset-inline-start', at_mean + '%').appendTo($track);
            }

            const at_now = scale(now);
            if (at_now !== null) {
                $('<span class="fc-bar-now"/>')
                    .addClass(tone || '')
                    .css('inset-inline-start', at_now + '%')
                    .appendTo($track);
            }

            return $track;
        }

        /* The top of a temperature sensor scale, in this order and for this reason:
           where the record knows this CPU protects itself, that is the honest end of the
           scale; otherwise the panic point, which is where this plugin hands the fans back;
           and where neither is known, the highest reading ever recorded plus a margin, so the
           bar still means something on a machine nobody has told us anything about. */
        function temperature_top(highest) {
            const tjmax = Number((trend_doc && trend_doc.hottest || {}).tjmax_c);
            if (isFinite(tjmax) && tjmax > 0) {
                return tjmax;
            }
            const panic = Number((state_doc && state_doc.curve || {}).panic_c);
            if (isFinite(panic) && panic > 0) {
                return panic;
            }
            return isFinite(highest) && highest > 0 ? highest * 1.25 : 100;
        }

        /* The reading now, and it does not come from the summary. history.py rewrites that
           document every five minutes, not every sample, because recomputing "the last month"
           sixty times an hour changes nothing in it - and the comment in history.py that
           settles the cadence says in as many words that the live figure a page shows beside
           it comes from the status document instead.

           So it does. The band, the mean and the time above each rung are the record talking
           about a window; the bright mark is the machine talking about this second, refreshed
           on the same tick as the Fans tab. The summary is the fallback, for the moment before
           the state arrives. */
        function current_value(key) {
            const kind = key.charAt(0);
            const name = key.slice(2);

            if (kind === 'T') {
                const sensors = ((state_doc || {}).temperatures || {}).sensors || [];
                for (let i = 0; i < sensors.length; i++) {
                    if (sensors[i].name === name && sensors[i].celsius !== null) {
                        return Number(sensors[i].celsius);
                    }
                }
            } else {
                const fans = (state_doc || {}).fans || [];
                for (let i = 0; i < fans.length; i++) {
                    if (fans[i].channel !== name) {
                        continue;
                    }
                    if (kind === 'R' && fans[i].rpm !== null && fans[i].rpm !== undefined) {
                        return Number(fans[i].rpm);
                    }
                    if (kind === 'D') {
                        const duty = fans[i].output !== null && fans[i].output !== undefined
                            ? fans[i].output : fans[i].duty;
                        if (duty !== null && duty !== undefined) {
                            return Number(duty);
                        }
                    }
                }
            }

            return Number(((trend_doc || {}).current || {})[key]);
        }


        function unit_suffix(unit) {
            if (unit === 'c') {
                return ' \u00b0C';
            }
            if (unit === 'pct') {
                return '%';
            }
            return unit === 'rpm' ? ' ' + "{{ lang._('rpm') }}" : '';
        }

        /* The name a person reads for a series. A temperature carries the label the discovery
           gave it, which is the same label the Fans tab prints; a fan carries its channel name.
           Where the state document has not arrived yet the raw name stands in rather than a
           blank, because a blank row is worse than an ugly one. */
        function sensor_label(name) {
            const sensors = ((state_doc || {}).temperatures || {}).sensors || [];
            for (let i = 0; i < sensors.length; i++) {
                if (sensors[i].name === name) {
                    return sensors[i].label || name;
                }
            }
            const fans = (state_doc || {}).fans || [];
            for (let i = 0; i < fans.length; i++) {
                if (fans[i].channel === name && fans[i].label && fans[i].label !== 'unknown') {
                    return fans[i].label;
                }
            }

            /* The state document has not arrived, or this sensor is in the record and no
               longer on the machine. fc_sensor() turns the common sysctl names into the same
               words the live table uses without needing anything fetched, so a graph drawn
               before the state lands says "core 0" rather than dev.cpu.0.temperature. */
            return fc_sensor(name);
        }

        function draw_window_picker() {
            const $picker = $('#graph-windows').empty();
            const windows = (trend_doc && trend_doc.windows) || [];

            if (!windows.length) {
                return;
            }

            /* Only a window some series actually carries. history.py lists the four it keeps
               whether or not a single sample has reached the longer ones, and four buttons of
               which three draw an empty page is not a choice, it is three dead ends. */
            const carried = {};
            Object.keys(trend_doc.series || {}).forEach(function (key) {
                Object.keys((trend_doc.series[key] || {}).windows || {}).forEach(function (name) {
                    carried[name] = true;
                });
            });

            const offered = windows.filter((item) => carried[item.name]);
            if (!offered.length) {
                return;
            }
            if (!carried[trend_window]) {
                trend_window = offered[0].name;
            }

            offered.forEach(function (item) {
                $('<button type="button" class="btn btn-xs"/>')
                    .addClass(item.name === trend_window ? 'btn-primary' : 'btn-default')
                    .text(window_title[item.name] || item.name)
                    .on('click', function () {
                        trend_window = item.name;
                        draw_rows();
                    })
                    .appendTo($picker);
            });
        }

        /* Every reading on one tab, one row each. The live number the Fans tab printed as text
           and the band the Graphs tab drew are the same sensor: they were on two tabs because
           they arrive in two documents, which is not a reason a reader has to care about. The
           bar has always drawn the live mark - current_value() reads the state document and says
           so at length - so merging the two deletes a table rather than adding a drawing. */
        function draw_rows() {
            const $into = $('#fc-rows').empty();

            draw_window_picker();

            const series = (is_object(trend_doc) && is_object(trend_doc.series))
                ? trend_doc.series : {};
            const of = function (key) {
                return is_object(series[key]) ? series[key] : null;
            };
            const rungs = (((trend_doc || {}).ladder_c) || []).map(Number);
            const curve = (state_doc && is_object(state_doc.curve)) ? state_doc.curve : {};
            const panic = Number(curve.panic_c);
            const limit = Number(curve.limit_c);
            const tone_of = function (high) {
                if (!isFinite(high)) { return ''; }
                if (isFinite(panic) && high > panic) { return 'fc-hot'; }
                if (isFinite(limit) && high >= limit) { return 'fc-warm'; }
                return '';
            };

            /* ---------------------------------------------------------------- temperatures */
            const reading = (state_doc && is_object(state_doc.temperatures))
                ? state_doc.temperatures : {};
            const sensors = ($.isArray(reading.sensors) ? reading.sensors : []).slice();
            const constant = $.isArray(reading.constant) ? reading.constant : [];
            const counts = is_object(reading.counts) ? reading.counts : {};
            sensors.sort(function (a, b) { return Number(b.celsius) - Number(a.celsius); });

            let highest = 0;
            const note_high = function (seen) {
                if (isFinite(seen) && seen > highest) { highest = seen; }
            };
            sensors.forEach(function (sensor) { note_high(Number(sensor.celsius)); });
            Object.keys(series).forEach(function (key) {
                if (series[key].kind !== 'T') { return; }
                note_high(Number(((series[key].windows || {})[trend_window] || {}).max));
            });
            const top = temperature_top(highest);

            const temps = section("{{ lang._('Temperatures') }}",
                (counts.found === null || counts.found === undefined) ? ''
                    : fc_fill("{{ lang._('{read} of {found} read') }}",
                        {read: fc_num(counts.read), found: fc_num(counts.found)}));
            $into.append(temps.section);
            temps.body.addClass('fc-cards');

            const seen_names = {};
            let frozen = 0;
            /* The cores as one row - the hottest, the list being sorted hottest first - and the
               rest behind a toggle, where there are enough of them to be worth folding. */
            const cores = sensors.filter(function (sensor) {
                return String(sensor.kind) === 'cpu_core' && constant.indexOf(String(sensor.name || '')) === -1;
            });
            const fold_cores = cores.length >= 3;
            let $cores = null;
            sensors.forEach(function (sensor) {
                const name = String(sensor.name || '');
                seen_names[name] = true;
                /* A sensor the collector has PROVED never moves is counted and not drawn, and it
                   is the collector that proves it: temperatures.constant is the evidence
                   sensors.py keeps. The page does not decide a zone is frozen by looking at a
                   band of zero width - that list is empty on this appliance and both zones get
                   a full row, which is the collector call to make and not this one. */
                if (constant.indexOf(name) !== -1) {
                    frozen += 1;
                    return;
                }
                const record = of('T|' + name);
                const band_high = record
                    ? Number(((record.windows || {})[trend_window] || {}).max)
                    : Number(sensor.celsius);
                const why = [];
                /* The per-sensor limit the collector already ships and the page threw away:
                   45 C below dev.cpu.0.coretemp.tjmax for a core, and the board own critical
                   point for a zone. Never one number subtracted for all of them. */
                if (sensor.headroom_c !== null && sensor.headroom_c !== undefined
                        && sensor.limit_name) {
                    why.push(fc_fill("{{ lang._('{headroom} C below {limit}') }}",
                        {headroom: fc_num(sensor.headroom_c),
                         limit: String(sensor.limit_name)}));
                }
                const one = {
                    name: String(sensor.label || fc_sensor(name)),
                    id: name,
                    now: sensor.celsius,
                    unit: 'c',
                    decimals: 1,
                    series: record,
                    top: top,
                    rungs: rungs,
                    tone: tone_of(band_high),
                    now_tone: tone_of(Number(sensor.celsius)),
                    why: why,
                    card: true
                };
                if (fold_cores && String(sensor.kind) === 'cpu_core') {
                    if ($cores === null) {
                        /* the hottest core stands for them all in one card, and the toggle that
                           opens the rest is in that card; the rest are a grid of their own under
                           the first one */
                        const $summary = row($.extend({}, one, {
                            name: "{{ lang._('processor cores') }}" + ' (' + cores.length + ')'
                        })).appendTo(temps.body);
                        $('<button type="button" class="fc-strip fc-card-toggle"/>')
                            .append($('<i class="fa fa-fw"/>').addClass(show_cores ? 'fa-angle-up' : 'fa-angle-down'))
                            .append(' ')
                            .append($('<span/>').text(show_cores
                                ? "{{ lang._('Hide them') }}" : "{{ lang._('Show them') }}"))
                            .on('click', function () { show_cores = !show_cores; draw_rows(); })
                            .appendTo($summary);
                        $cores = $('<div class="fc-cards fc-cores"/>').toggle(show_cores)
                            .insertAfter(temps.body);
                    }
                    $cores.append(row(one));
                    return;
                }
                temps.body.append(row(one));
            });

            /* In the record and no longer on the machine. The band is real and the reading now
               is a hole, which is exactly what the dashed box is for. */
            Object.keys(series).sort().forEach(function (key) {
                if (series[key].kind !== 'T') { return; }
                const name = String(series[key].name);
                if (seen_names[name]) { return; }
                temps.body.append(row({
                    name: sensor_label(name),
                    id: name,
                    now: null,
                    unit: 'c',
                    decimals: 1,
                    series: series[key],
                    top: top,
                    rungs: rungs,
                    tone: tone_of(Number(((series[key].windows || {})[trend_window] || {}).max)),
                    absent: "{{ lang._('This sensor is in the record and the machine is not reporting it now.') }}",
                    card: true
                }));
            });

            if (frozen) {
                temps.legend(fc_fill("{{ lang._('{count} sensors have never moved') }}",
                    {count: frozen}));
            }
            if (temps.body.children().length === 0) {
                temps.legend("{{ lang._('This machine reports no temperature at all. Nothing can be decided from a sensor that is not there, so no fan would be driven.') }}");
            }
            /* A node that exists and gave no number this time is not the same thing as a sensor
               this machine does not have, and it is the only one of the two worth a warning. */
            const unreadable = $.isArray(reading.unreadable) ? reading.unreadable : [];
            if (unreadable.length > 0) {
                temps.section.append(note(
                    "{{ lang._('These sensors exist and did not answer this time:') }}"
                    + ' ' + unreadable.join(', '), 'text-warning'));
            }

            /* ---------------------------------------------------------------- fans */
            const fans = (state_doc && $.isArray(state_doc.fans)) ? state_doc.fans : [];
            const turning = fans.filter(function (fan) {
                return String(fan.state) === 'turning';
            }).length;
            const air = section("{{ lang._('Fans') }}", fans.length
                ? fc_fill("{{ lang._('{n} channels, {m} turning') }}",
                    {n: fans.length, m: turning})
                : '');
            $into.append(air.section);

            if (fans.length === 0) {
                air.legend("{{ lang._('No fan channel was reported at all. The Hardware tab says what the probe found and why.') }}");
            }

            const silent = fans.filter(function (fan) {
                return String(fan.state) === 'silent';
            });
            const shown = fans.filter(function (fan) {
                return show_silent || String(fan.state) !== 'silent';
            });

            const floor_of = function (fan) { return String(fan.floor_source || ''); };
            const helper_of = function (fan) {
                if (fan.helper_floor === null || fan.helper_floor === undefined) {
                    return '';
                }
                return "{{ lang._('the helper refuses below') }}" + ' ' + fan.helper_floor + '%'
                    + (fan.helper_floor_source ? ' (' + String(fan.helper_floor_source) + ')' : '');
            };
            const shared_floor = fold_note(shown, floor_of);
            const shared_helper = fold_note(shown, helper_of);

            shown.forEach(function (fan) {
                const channel = String(fan.channel || '');
                const state = String(fan.state || 'unreadable');
                const words = fan_state[state] || fan_state.unreadable;
                const chips = [
                    chip(words[0], chip_tone[words[1]] || '', fan_state_note[state] || ''),
                    fan.driven
                        ? chip("{{ lang._('this plugin') }}", 'text-info', '')
                        : chip("{{ lang._('the hardware') }}", 'text-muted', '')
                ];
                /* The register word, with whose word it is in its title. It is the chip talking
                   about a bit, not a statement about a fan: four channels on this appliance
                   carry the same register value and two different words for it. */
                if (fan.mode !== null && fan.mode !== undefined && fan.mode !== '') {
                    chips.push(chip(String(fan.mode), 'text-muted',
                        fc_fill("{{ lang._('the chip calls this mode: {mode}') }}",
                            {mode: String(fan.mode)})));
                }

                const why = [];
                const $floor = $('<span/>');
                $floor.append($('<span/>').text("{{ lang._('Floor:') }}" + ' '));
                $floor.append(value(fan.floor, '%', floor_of(fan)));
                if (!shared_floor && floor_of(fan)) {
                    $floor.append($('<div class="fc-note"/>').text(floor_of(fan)));
                }
                if (!shared_helper && helper_of(fan)) {
                    $floor.append($('<div class="fc-note"/>').text(helper_of(fan)));
                }
                why.push($floor);

                /* The temperature the decision came from, with the sysctl it was read from. Six
                   weeks from now somebody will want to know why a fan sped up at 03:00, and
                   "dev.pchtherm.0.temperature read 61 C" is an answer where "it was hot" is not. */
                const $from = $('<span/>');
                if (fan.celsius !== null && fan.celsius !== undefined) {
                    $from.append(tech(String(fan.celsius) + ' C').addClass('fc-strong'));
                    const from = $.isArray(fan.from) ? fan.from : [];
                    if (from.length > 0) {
                        $from.append(' ').append(tech(from.join(', ')));
                    }
                } else {
                    $from.append($('<span class="fc-muted"/>').text(
                        "{{ lang._('nothing was decided for this fan') }}"));
                }
                if (fan.reason) {
                    $from.append($('<div class="fc-note"/>').text(String(fan.reason)));
                }
                why.push($from);

                /* The paragraph this whole plugin turns on - what was measured on this exact
                   chassis fan - is in both documents and has never been drawn anywhere. */
                const more = [];
                if (fan.confirmed) {
                    more.push({
                        key: 'confirmed:' + channel,
                        title: "{{ lang._('What has been measured on this channel') }}",
                        lines: [String(fan.confirmed)]
                    });
                }

                const duty = (fan.output !== null && fan.output !== undefined)
                    ? fan.output : fan.duty;
                air.body.append(row({
                    name: (fan.label && fan.label !== 'unknown') ? String(fan.label) : channel,
                    id: channel,
                    now: duty,
                    unit: 'pct',
                    decimals: 0,
                    series: of('D|' + channel),
                    extra: of('R|' + channel),
                    top: 100,
                    rungs: [],
                    chips: chips,
                    why: why,
                    more: more
                }));
            });

            if (shared_floor) { air.legend(shared_floor); }
            if (shared_helper) { air.legend(shared_helper); }

            if (silent.length && !show_silent) {
                const $line = $('<div class="fc-note fc-muted"/>').appendTo(air.section);
                $line.append($('<span/>').text(
                    fc_fill("{{ lang._('{count} channels report no fan:') }}",
                        {count: silent.length}) + ' '));
                $line.append(tech(silent.map(function (fan) {
                    return String(fan.channel || '');
                }).join(', ')));
                const shared_silent = fold_note(silent, function (fan) {
                    return fan_state_note[String(fan.state)] || '';
                });
                if (shared_silent) {
                    $line.append($('<div class="fc-note"/>').text(shared_silent));
                }
                $('<button type="button" class="fc-strip"/>')
                    .append($('<i class="fa fa-fw fa-angle-down"/>'))
                    .append(' ')
                    .append($('<span/>').text("{{ lang._('Show them') }}"))
                    .on('click', function () { show_silent = true; draw_rows(); })
                    .appendTo($line);
            } else if (silent.length) {
                $('<button type="button" class="fc-strip"/>')
                    .append($('<i class="fa fa-fw fa-angle-up"/>'))
                    .append(' ')
                    .append($('<span/>').text("{{ lang._('Hide them') }}"))
                    .on('click', function () { show_silent = false; draw_rows(); })
                    .appendTo(air.section);
            }

            /* ---------------------------------------------------------------- the load */
            /* Fetched every minute and dropped on the floor until today: draw_trend matched T, D
               and R and never B. It is also the series that explains the changes the verdict says
               it discarded - a machine whose temperature is set by what it is doing rather than
               by its fan says so here, in a picture, before the sentence says it in words. */
            const loads = Object.keys(series).filter(function (key) {
                return series[key].kind === 'B';
            }).sort();
            if (loads.length) {
                const busy = section("{{ lang._('Load') }}", '');
                $into.append(busy.section);
                loads.forEach(function (key) {
                    busy.body.append(row({
                        name: load_title[String(series[key].name || '')]
                            || String(series[key].name || ''),
                        id: String(series[key].name || ''),
                        now: current_value(key),
                        unit: 'pct',
                        decimals: 0,
                        series: series[key],
                        top: 100,
                        rungs: []
                    }));
                });
            }

            draw_curve_line();
            draw_buys();
        }

        /* The curve, as one line. The drawing belongs where somebody is editing it, which is the
           Settings tab; here the four numbers in force are the whole of what a reader needs. */
        function draw_curve_line() {
            const $line = $('#fc-curve-line').empty();
            const curve = (state_doc && is_object(state_doc.curve)) ? state_doc.curve : null;
            if (curve === null) {
                return;
            }
            if (curve.error) {
                $line.append(note("{{ lang._('This curve cannot be used:') }}"
                    + ' ' + String(curve.error), 'text-warning'));
                return;
            }
            const parts = [];
            if (curve.points) {
                parts.push(fc_fill("{{ lang._('the curve: {points}') }}",
                    {points: String(curve.points)}));
            }
            if (curve.limit_c !== null && curve.limit_c !== undefined) {
                parts.push(fc_fill("{{ lang._('full at {limit} C') }}",
                    {limit: fc_num(curve.limit_c)}));
            }
            if (curve.panic_c !== null && curve.panic_c !== undefined) {
                parts.push(fc_fill("{{ lang._('hands back at {panic} C') }}",
                    {panic: fc_num(curve.panic_c)}));
            }
            if (curve.floor !== null && curve.floor !== undefined) {
                parts.push(fc_fill("{{ lang._('floor {floor}%') }}",
                    {floor: fc_num(curve.floor)}));
            }
            $line.text(parts.join(' · '));
        }

        /* The verdict, behind a disclosure that opens itself the moment there is an answer. It
           is closed while the record is still waiting because its text then ends in an
           instruction to hold a fan at full for five minutes, which is not a two in the morning
           instruction - and deliberately, there is no button beside it. */
        function draw_buys() {
            const $body = $('#fc-buys-body').empty();
            draw_trend_verdict($body);
            const code = ((trend_doc || {}).verdict || {}).code;
            bind_strip($('#fc-buys-strip'), $body, 'buys', !!code && code !== 'trend.waiting');
        }

        /* The one sentence the record exists to be able to say: how much cooler this chassis
           gets for more fan. It arrives as a code and a set of parameters rather than as a
           sentence, so that the page builds it through lang._() like every other string - see
           trend_text. Until enough duty changes have been seen it says which and how many. */
        function draw_trend_verdict($into) {
            const verdict = (trend_doc || {}).verdict;
            if (!is_object(verdict) || !verdict.code) {
                return;
            }

            const said = trend_text(verdict);
            if (!said) {
                return;
            }

            /* trend_text() already carries the qualifier and the headroom sentence: the
               figure, what it is a floor under, and how much room the machine has left are
               one statement and are not split across three paragraphs here. */
            $into.append($('<div class="fc-note"/>').text(said));

            /* Why changes were not counted. A reader who learns that eleven of fourteen were
               discarded because the load moved has been told something true about their
               machine - that its temperature is set by what it is doing rather than by its
               fan - which is the same lesson the figure teaches, arriving earlier. */
            const rejected = ((trend_doc || {}).response || {}).rejected || {};
            const reasons = Object.keys(rejected)
                .map((code) => trend_rejection(code, rejected[code]))
                .filter((part) => !!part);
            if (reasons.length) {
                $into.append($('<div class="fc-note"/>').text(reasons.join(', ')));
            }
        }

        function reload_trend(done) {
            ajaxGet('/api/fanctl/service/trend', {}, function (data) {
                trend_doc = is_object(data) ? data : null;
                /* The header takes the hottest sensor, the day headroom and the record age from
                   this document, so it is redrawn with it. */
                draw_vitals();
                draw_rows();
                if (typeof done === 'function') {
                    done();
                }
            });
        }

        function reload_state(done) {
            ajaxGet('/api/fanctl/service/state', {}, function (data) {
                state_doc = is_object(data) ? data : null;
                draw_vitals();
                draw_incident();
                draw_contract_strip();
                /* Unconditionally, and not only when the record has arrived: a row draws from
                   the live document alone, so a fresh installation gets rows with numbers in
                   them and empty tracks behind them rather than a page that says nothing. */
                draw_rows();
                update_floor_note();
                update_curve_pending();
                if (typeof done === 'function') {
                    done();
                }
            });
        }

        function reload_probe(done) {
            ajaxGet('/api/fanctl/service/probe', {}, function (data) {
                probe_doc = is_object(data) ? data : null;
                draw_chip();
                draw_model();
                draw_curve_why();
                draw_workbench();
                if (typeof done === 'function') {
                    done();
                }
            });
        }

        /* ------------------------------------------------------------------ the workbench acts */

        function hold_request(action, channel, duty, title, message) {
            $('#OPNsenseStdWaitDialog').modal('show');
            ajaxCall('/api/fanctl/service/' + action,
                {'channel': channel, 'duty': duty, 'seconds': 15},
                function (data) {
                    $('#OPNsenseStdWaitDialog').modal('hide');
                    const ok = data && data.status === 'ok';
                    let text = ok ? message : "{{ lang._('It was refused.') }}";
                    if (ok && data.stopped) {
                        text = "{{ lang._('The fan STOPPED at this duty. That is the measurement: the floor has been raised above it, and nothing will be driven below the new floor.') }}";
                    }
                    if (ok && data.floor !== null && data.floor !== undefined) {
                        text += ' ' + "{{ lang._('Floor now:') }}" + ' ' + data.floor + '%.';
                    }
                    if (data && data.restored === false) {
                        text += ' ' + "{{ lang._('WARNING: the fan was NOT handed back.') }}";
                    }
                    show_message(
                        ok ? BootstrapDialog.TYPE_SUCCESS : BootstrapDialog.TYPE_WARNING,
                        title, text + failure_text(data)
                    );
                    reload_state();
                    reload_probe();
                });
        }

        $('#workbench-body').on('click', '.fc-hold', function () {
            const channel = $(this).attr('data-channel');
            const duty = $('.fc-duty[data-channel="' + channel + '"]').val();
            hold_request('set', channel, duty, "{{ lang._('Hold a duty') }}",
                "{{ lang._('The duty was held and the fan has been handed back to the hardware.') }}");
        });

        $('#workbench-body').on('click', '.fc-test', function () {
            const channel = $(this).attr('data-channel');
            const duty = $('.fc-duty[data-channel="' + channel + '"]').val();
            /* Asked first, and asked in full. This is the one button on the page that may stop a
               fan on purpose, and somebody who presses it should know that before it happens
               rather than after. */
            BootstrapDialog.confirm({
                title: "{{ lang._('Measure the floor') }}",
                message: "{{ lang._('This writes {duty}% to {channel} for up to fifteen seconds and watches the tachometer. If the fan stops, that is the measurement and the test ends at once; the fan is handed back to the hardware in every case. Do this with the appliance in front of you.') }}"
                    .replace('{duty}', String(duty)).replace('{channel}', String(channel)),
                type: BootstrapDialog.TYPE_WARNING,
                closable: true,
                btnCancelLabel: "{{ lang._('Cancel') }}",
                btnOKLabel: "{{ lang._('Measure it') }}",
                btnOKClass: 'btn-warning',
                callback: function (yes) {
                    if (yes) {
                        hold_request('test', channel, duty, "{{ lang._('Floor measurement') }}",
                            "{{ lang._('The fan kept turning at this duty. The floor is now one step above it.') }}");
                    }
                }
            });
        });

        function restore_now() {
            ajaxCall('/api/fanctl/service/restore', {}, function (data) {
                const ok = data && data.status === 'ok';
                show_message(ok ? BootstrapDialog.TYPE_SUCCESS : BootstrapDialog.TYPE_DANGER,
                    "{{ lang._('Hand the fans back') }}",
                    (ok ? "{{ lang._('Done. Every fan this plugin had taken is back under the control it was found under.') }}"
                        : "{{ lang._('It did not work. Power the appliance off and on to return the fans to firmware control.') }}")
                    + failure_text(data));
                reload_state();
            });
        }

        /* The guard travels with the button. It is in the header now, on screen on every
           tab, which makes it easier to reach by accident - and on this appliance it would
           release a fan somebody is deliberately holding at full. The paragraph that used to
           sit above it is what the dialog says, so it is reachable on a touch screen as well.
           The button inside the incident panel is NOT guarded: there the fans are already
           stuck, and a confirmation in front of the recovery is friction in an emergency. */
        $('#restoreAct').click(function () {
            BootstrapDialog.confirm({
                title: "{{ lang._('Hand the fans back') }}",
                message: "{{ lang._('Writes back the control mode and the duty each fan was found under before this plugin touched it. It does nothing at all if this plugin was never driving anything, it works with the service stopped, and it is the right button to press if anything on this page is behaving in a way you do not understand.') }}",
                type: BootstrapDialog.TYPE_WARNING,
                closable: true,
                btnCancelLabel: "{{ lang._('Cancel') }}",
                btnOKLabel: "{{ lang._('Hand them back now') }}",
                btnOKClass: 'btn-warning',
                callback: function (yes) {
                    if (yes) {
                        restore_now();
                    }
                }
            });
        });
        $('#incident-body').on('click', '#restoreAct2', restore_now);

        /* ------------------------------------------------------------------ settings */

        /* The preview under the Points box is the curve the DAEMON is running, drawn from the
           sample it sent. It is never redrawn from what somebody has just typed - a picture
           rebuilt here out of four numbers would agree with the box and not with the machine,
           which is the whole reason draw_curve() takes a sample. So when the typed numbers and
           the saved ones disagree, the picture is marked as out of date and left alone. */
        function update_curve_pending() {
            const $preview = $('#curve-preview');
            if ($preview.length === 0) {
                return;
            }
            $('#fc-curve-pending').remove();
            $preview.css('opacity', '');
            const curve = (state_doc && is_object(state_doc.curve)) ? state_doc.curve : null;
            if (curve === null) {
                return;
            }
            const pairs = [
                [$('#fanctl\\.general\\.points').val(), curve.points],
                [$('#fanctl\\.general\\.limit_c').val(), curve.limit_c],
                [$('#fanctl\\.general\\.panic_c').val(), curve.panic_c],
                [$('#fanctl\\.general\\.floor_duty').val(), curve.floor]
            ];
            let differs = false;
            pairs.forEach(function (pair) {
                if (pair[0] === undefined || pair[0] === null) {
                    return;
                }
                const saved = (pair[1] === null || pair[1] === undefined) ? '' : String(pair[1]);
                if (String(pair[0]).trim() !== saved.trim()) {
                    differs = true;
                }
            });
            if (!differs) {
                return;
            }
            $preview.css('opacity', '0.6');
            $preview.before($('<span id="fc-curve-pending" class="label label-warning"/>')
                .text("{{ lang._('not applied yet') }}"));
        }

        /* The floor is the one number on this page that must never be undercut, and this is the
           third of the three places that make sure of it. The model refuses a save; the daemon
           clamps whatever it is given; and this says so while somebody is still typing, because
           being told after a save is being told too late to be useful. */
        function update_floor_note() {
            const $note = $('#floor-note').empty();
            const typed = parseInt($('#fanctl\\.general\\.manual_duty').val(), 10);
            const floor = parseInt($('#fanctl\\.general\\.floor_duty').val(), 10);
            const fans = (state_doc && $.isArray(state_doc.fans)) ? state_doc.fans : [];

            let measured = null;
            fans.forEach(function (fan) {
                if (fan.floor !== null && fan.floor !== undefined
                        && (measured === null || fan.floor > measured)) {
                    measured = fan.floor;
                }
            });

            if (isFinite(typed) && isFinite(floor) && typed < floor) {
                $note.append(note(
                    "{{ lang._('The fixed duty is below the floor, so it could never be applied as written. Raise it, or lower the floor.') }}",
                    'text-warning'));
            }
            if (measured !== null && isFinite(floor) && measured > floor) {
                $note.append(note(
                    "{{ lang._('A floor measured on this hardware is higher than the one set here ({measured}%), and the higher of the two is what is used.') }}"
                        .replace('{measured}', String(measured))));
            }
        }

        /* The sensor groups, named here rather than in the model, for the same reason the sibling
           plugin names its screens here: a Python script on this firewall has no catalogue to
           translate against, and this page is read in Arabic. The keys are the ones sensors.py
           dispatches on and the ones the model's mask allows, so the three cannot drift apart
           without a group visibly losing its name. */
        const sensor_title = {
            'cpu': "{{ lang._('processor cores') }}",
            'pch': "{{ lang._('chipset') }}",
            'acpi': "{{ lang._('ACPI thermal zones') }}",
            'nvme': "{{ lang._('drives') }}"
        };

        /* The form is loaded only after the probe has answered, and that ordering is the whole
           reason this is a function rather than a block at the end of ready().

           A stored list knows nothing of the options nobody chose: what comes back from the API
           is the channels and the groups that were saved, and everything else has to be added as
           a choice before the tokeniser turns the lot into draggable tokens - because once it
           has, an option appended behind its back is an option nobody can see. So both lists are
           filled in here, in this order, once. If the probe failed, the callback still runs and
           the form still loads: the channel field then offers only what was saved and accepts a
           name typed by hand, and the Hardware tab is where the names are. */
        function load_form() {
            mapDataToFormUI({'frm_general': '/api/fanctl/settings/get'}).done(function () {
                offer_channels();
                offer_sensors();
                formatTokenizersUI();
                $('.selectpicker').selectpicker('refresh');
                $('#fanctl\\.general\\.manual_duty').parent().append(
                    $('<div id="floor-note"></div>'));
                $('#fanctl\\.general\\.manual_duty, #fanctl\\.general\\.floor_duty')
                    .on('change keyup', update_floor_note);
                $('#fanctl\\.general\\.points').parent().append(
                    $('<div id="curve-preview"></div>'));
                $('#fanctl\\.general\\.points').parent().append(
                    $('<div id="fc-curve-why"></div>'));
                draw_curve('#curve-preview');
                draw_curve_why();
                $('#fanctl\\.general\\.points, #fanctl\\.general\\.limit_c, '
                  + '#fanctl\\.general\\.panic_c, #fanctl\\.general\\.floor_duty')
                    .on('change keyup', update_curve_pending);
                update_floor_note();
                update_curve_pending();
            });
        }

        function add_option($select, value, text) {
            if ($select.find('option').filter(function () {
                return $(this).val() === value;
            }).length === 0) {
                $select.append($('<option/>').attr('value', value).text(text));
            }
        }

        /* Only channels the probe actually found, and only those that report a turning fan, are
           offered as fans to control. A channel reading zero may have no fan on it at all, and
           offering it would be offering somebody a way to write a duty to a header nobody knows
           anything about. It can still be typed in by hand, which is the right balance: possible,
           but not suggested. */
        function offer_channels() {
            const $select = $('#fanctl\\.general\\.channels');
            if ($select.length === 0 || probe_doc === null) {
                return;
            }
            const chip = is_object(probe_doc.chip) ? probe_doc.chip : {};
            const channels = $.isArray(chip.channels) ? chip.channels : [];
            channels.forEach(function (channel) {
                const name = String(channel.name || '');
                if (name === '' || channel.tach === null || Number(channel.tach) < 200) {
                    return;
                }
                add_option($select, name, name);
            });
        }

        /* All four groups, always, whether or not this machine has them. A group this machine
           does not report is worth being able to choose and then being told about: choosing it
           fails the sensors line of the contract, with the group named, which is a better answer
           than a list that quietly omitted it. */
        function offer_sensors() {
            const $select = $('#fanctl\\.general\\.sensors');
            if ($select.length === 0) {
                return;
            }
            const present = (state_doc && is_object(state_doc.temperatures)
                && $.isArray(state_doc.temperatures.present))
                ? state_doc.temperatures.present : [];
            $select.find('option').each(function () {
                const key = $(this).val();
                if (sensor_title[key] !== undefined) {
                    $(this).text(sensor_title[key]);
                }
            });
            Object.keys(sensor_title).forEach(function (key) {
                add_option($select, key, sensor_title[key] + (
                    present.indexOf(key) === -1
                        ? ' ' + "{{ lang._('(not on this machine)') }}" : ''));
            });
        }

        /* Saving is only half of it. The daemon reads its settings once, at start, so a curve
           written into config.xml and not applied is a curve nobody is running - and applying it
           means a restart, which passes through a restore, which is the right thing to do when
           the rules have changed underneath a fan. */
        $('#saveAct').click(function () {
            const $button = $(this);
            const $progress = $('#frm_general_progress');
            $button.prop('disabled', true);
            $progress.addClass('fa fa-spinner fa-pulse');
            saveFormToEndpoint('/api/fanctl/settings/set', 'frm_general', function () {
                ajaxCall('/api/fanctl/service/reconfigure', {}, function (data) {
                    $progress.removeClass('fa fa-spinner fa-pulse');
                    $button.prop('disabled', false);
                    updateServiceControlUI('fanctl');
                    reload_state();
                    reload_probe();
                    if (data && data.status !== 'ok') {
                        show_message(BootstrapDialog.TYPE_WARNING, "{{ lang._('Fan Control') }}",
                            "{{ lang._('The settings were saved, but the service did not take them.') }}"
                            + failure_text(data));
                    }
                });
            }, false, function () {
                $progress.removeClass('fa fa-spinner fa-pulse');
                $button.prop('disabled', false);
            });
        });

        /* ------------------------------------------------------------------ page plumbing */

        /* One Refresh, and it fetches both documents, because both are on the tab it sits
           above and the Graphs tab that had the second button is gone. */
        $('#stateRefreshAct').click(function () {
            const $icon = $(this).find('i');
            $icon.addClass('fa-spin');
            reload_state(function () { $icon.removeClass('fa-spin'); });
            reload_trend();
        });

        $('#probeRefreshAct').click(function () {
            const $icon = $(this).find('i');
            $icon.addClass('fa-spin');
            reload_probe(function () { $icon.removeClass('fa-spin'); });
        });

        /* How often each pane is worth a configd round trip. Every tab is drawn from the
           same two documents now, so this is a table rather than a chain of conditions: the
           readings tab wants the live document on every tick and the record once a minute, and
           the other two want a slow refresh so that the header above them does not go stale
           while somebody types. */
        const trend_every = Math.round(TREND_MS / STATE_MS);
        const probe_every = Math.round(PROBE_MS / STATE_MS);
        let ticks = 0;
        window.setInterval(function () {
            ticks += 1;
            if ($('#fans').hasClass('active')) {
                reload_state();
                if (ticks % trend_every === 0) {
                    reload_trend();
                }
            } else if ($('#settings').hasClass('active')) {
                if (ticks % probe_every === 0) {
                    reload_state();
                }
            } else if ($('#hardware').hasClass('active')) {
                if (ticks % probe_every === 0) {
                    reload_probe();
                    reload_state();
                }
            }
        }, STATE_MS);

        updateServiceControlUI('fanctl');
        /* The state first, so that the sensor list knows which groups this machine reports, then
           the probe, and the form last inside the probe's callback - see load_form(). */
        reload_state();
        reload_trend();
        reload_probe(load_form);

        /* The Graphs tab is gone and its rows are on Readings, so a bookmark or a link to it
           lands there rather than on a pane that does not exist. */
        const asked_for = window.location.hash !== '' ? window.location.hash : '#fans';
        const selected_tab = asked_for === '#graphs' ? '#fans' : asked_for;
        $('a[href="' + selected_tab + '"]').tab('show');
        $('.nav-tabs a').on('shown.bs.tab', function (e) {
            history.pushState(null, null, e.target.hash);
        });
        $('a[href="#fans"]').on('shown.bs.tab', function () {
            reload_state();
            reload_trend();
        });
        $('a[href="#hardware"]').on('shown.bs.tab', function () {
            reload_probe();
            reload_state();
        });
        $('a[href="#settings"]').on('shown.bs.tab', function () {
            draw_curve('#curve-preview');
            update_floor_note();
            update_curve_pending();
        });

    });
</script>

<style>
    /* Nothing here is placed with a `left` or a `right`. The mirrored stylesheets are built from
       the files under www/css, so an offset written in a page template survives into the Arabic
       layout untouched and lands against the wrong edge. Logical properties turn with the page. */

    /* Channel names, sysctl names, temperatures and duties are written left to right even when
       the sentence around them is not. Letting each one carry its own direction is what keeps a
       dot, a slash or a percent sign from being reordered by the text beside it, and the same
       treatment goes to whole sentences: until these strings are translated they are English
       inside a right-to-left page, and the bidirectional algorithm will move a trailing full stop
       to the opposite end of the line. */
    [class^="fc-"],
    [class*=" fc-"],
    .tab-pane .alert,
    .tab-pane .help-block,
    .tab-pane small,
    .tab-pane td,
    .tab-pane th {
        unicode-bidi: plaintext;
        text-align: start;
    }

    /* The graphs. currentColor throughout and opacity only on the band, for the reason the
       dashboard widget records: this GUI has a light theme and a dark one, and a bar faded as
       a whole disappears into one of them. The track keeps a full-strength outline on both. */
    .fc-windows {
        display: inline-flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-inline-start: auto;
    }

    /* One row, and the same one for a sensor, a fan and the load. min-width: 0 is what lets
       a long sysctl be cut short rather than push the reading off the end of the line. */
    .fc-row {
        padding: 6px 0 10px;
        border-bottom: 1px solid rgba(128, 128, 128, 0.22);
        /* A bar as wide as a 1920-pixel screen is a thread; this keeps a reading and its band
           in one glance on a wide screen and changes nothing on a narrow one. */
        max-width: 980px;
    }

    .fc-row-head {
        display: flex;
        align-items: baseline;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: 4px;
    }

    /* The temperatures as cards: as many to a line as the width takes, all the same width, so
       a reading and its band are one glance on any screen rather than a thread across it. */
    .fc-cards {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
        gap: 10px;
    }

    .fc-cards.fc-cores {
        margin-top: 10px;
    }

    .fc-row.fc-card {
        display: flex;
        flex-direction: column;
        max-width: none;
        padding: 10px 12px;
        border: 1px solid rgba(128, 128, 128, 0.3);
        border-radius: 4px;
        background-color: rgba(128, 128, 128, 0.05);
    }

    /* the name and its sensor on top, the reading on a line of its own under them: side by side,
       a 22-pixel number left a card's name about twelve characters */
    .fc-card .fc-row-head {
        flex-direction: column;
        flex-wrap: nowrap;
        align-items: stretch;
        gap: 4px;
        margin-bottom: 8px;
    }

    /* the name over the sensor it was read from, both cut short rather than wrapped; the whole
       of each is in its title */
    .fc-card-title {
        display: flex;
        flex-direction: column;
        min-width: 0;
    }

    .fc-card .fc-row-now {
        margin-inline-start: 0;
        font-size: 24px;
        line-height: 1.1;
    }

    .fc-card .fc-bar {
        height: 8px;
    }

    .fc-card-stats {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 6px;
        margin-top: 8px;
        font-variant-numeric: tabular-nums;
    }

    .fc-card-stat {
        display: flex;
        flex-direction: column;
        min-width: 0;
    }

    .fc-card-stat-key {
        font-size: 11px;
        opacity: 0.7;
    }

    .fc-card-stat-value {
        font-size: 12px;
        font-weight: bold;
        white-space: nowrap;
    }

    .fc-card .fc-row-foot {
        gap: 6px;
        margin-top: 6px;
    }

    .fc-card .fc-row-id {
        white-space: nowrap;
    }

    .fc-card-toggle {
        align-self: flex-start;
        width: auto;
        margin-top: 6px;
    }

    .fc-row-name {
        font-weight: bold;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .fc-row-id,
    .fc-row-legend,
    .fc-row-thin {
        font-size: 11px;
        opacity: 0.7;
    }

    .fc-row-id {
        font-family: monospace;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .fc-row-now {
        margin-inline-start: auto;
        font-weight: bold;
        white-space: nowrap;
        font-variant-numeric: tabular-nums;
    }

    /* On a phone the reading has no room to sit at the far end of its own line. */
    @media (max-width: 700px) {
        .fc-row-now {
            margin-inline-start: 0;
        }
    }

    .fc-bar {
        position: relative;
        height: 12px;
        border-radius: 3px;
        box-shadow: inset 0 0 0 1px currentColor;
        opacity: 0.85;
    }

    .fc-bar-band {
        position: absolute;
        top: 0;
        bottom: 0;
        background-color: currentColor;
        opacity: 0.35;
        border-radius: 3px;
    }

    .fc-bar-band.fc-warm {
        opacity: 0.5;
    }

    .fc-bar-band.fc-hot {
        opacity: 0.62;
    }

    /* The rungs are drawn under the marks and thinner than either, so that a ladder of three
       never competes with the two numbers a reader came for. */
    .fc-bar-rung {
        position: absolute;
        top: 2px;
        bottom: 2px;
        width: 1px;
        background-color: currentColor;
        opacity: 0.45;
    }

    .fc-bar-mean {
        position: absolute;
        top: 1px;
        bottom: 1px;
        width: 1px;
        background-color: currentColor;
        opacity: 0.9;
    }

    .fc-bar-now {
        position: absolute;
        top: -2px;
        bottom: -2px;
        width: 3px;
        margin-inline-start: -1px;
        border-radius: 2px;
        background-color: currentColor;
    }

    .fc-row-foot {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin-top: 4px;
        font-size: 11px;
        font-variant-numeric: tabular-nums;
    }

    .fc-row-above {
        font-weight: bold;
    }

    .fc-row-why {
        font-size: 85%;
        opacity: 0.85;
        padding-top: 3px;
    }

    /* A band nobody watched for long is faded; the name and the reading beside it are not, and
       they used to be, because the fade was on the whole row. */
    .fc-bar.fc-thin .fc-bar-band {
        opacity: 0.2;
    }

    .fc-bar.fc-thin .fc-bar-band.fc-warm {
        opacity: 0.28;
    }

    .fc-bar.fc-thin .fc-bar-band.fc-hot {
        opacity: 0.34;
    }

    /* A range too narrow to draw as a band. Same colour, same place, no width claimed. */
    .fc-bar-flat {
        position: absolute;
        top: 1px;
        bottom: 1px;
        width: 5px;
        margin-inline-start: -2px;
        border-radius: 3px;
        background-color: currentColor;
        opacity: 0.55;
    }

    .fc-alert { margin: 10px; }
    .fc-panels { padding: 0 10px 10px; }
    /* the heading of a Hardware section, as OPNsense's base_form draws one */
    .fc-head-row { padding-top: 5px; padding-bottom: 5px; font-size: 16px; }
    .fc-head-row small { font-size: 12px; font-weight: normal; }
    /* under "full help" until it is turned on */
    .fc-help { display: none; }
    .fc-note { font-size: 85%; opacity: 0.85; padding-top: 4px; }
    .fc-sub { font-size: 85%; opacity: 0.75; }
    .fc-muted { opacity: 0.7; }
    .fc-strong { font-weight: bold; }
    .fc-action { margin-top: 10px; }
    .fc-section { padding: 10px 0; }
    .fc-section-head {
        display: flex;
        align-items: baseline;
        flex-wrap: wrap;
        gap: 8px;
        font-weight: bold;
        padding-bottom: 6px;
    }
    .fc-section-aside { font-size: 11px; opacity: 0.7; font-weight: normal; }

    /* Numbers and names that came from the machine, in a face where a zero cannot be read as an
       O and a one cannot be read as an l. On this page that matters: the difference between 0 rpm
       and 60 rpm is the difference between a stopped fan and a slow one. */
    .fc-tech {
        font-family: monospace;
        unicode-bidi: plaintext;
    }

    /* The header, which is outside every .tab-pane - which is why every element in it
       carries an fc- class. The bidi rule at the top of this block is global and reaches it;
       the selectors beside that rule are scoped to .tab-pane and do not. */
    .fc-head { padding: 10px 12px; margin-bottom: 8px; }
    /* The vital readings as summary cards, the same as the coprocessor's pages in os-xgs-npu: the
       theme's text and a faint tint of it, and a coloured edge that says how the reading stands. */
    .fc-vitals { display: flex; flex-wrap: wrap; gap: 12px; align-items: stretch; }

    .fc-vital {
        flex: 1 1 200px;
        min-width: 190px;
        display: flex;
        flex-direction: column;
        padding: 10px 14px;
        border: 1px solid rgba(128, 128, 128, 0.25);
        border-inline-start: 4px solid #9e9e9e;
        border-radius: 3px;
        background-color: rgba(128, 128, 128, 0.04);
    }

    .fc-vital.fc-hot { border-inline-start-color: #d9534f; }
    .fc-vital > .fa { opacity: 0.35; font-size: 18px; align-self: flex-end; margin-bottom: -18px; }
    .fc-vital-key {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        opacity: 0.6;
    }
    .fc-vital-value { font-size: 19px; line-height: 1.3; font-weight: 600; font-family: inherit; margin: 2px 0; }
    .fc-vital-value .label { font-size: 12px; font-weight: 600; }
    .fc-vital-sub { font-size: 12px; opacity: 0.7; }
    .fc-vital-acts {
        margin-inline-start: auto;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
    }
    .fc-record { padding-top: 6px; }

    /* A disclosure. The glyph is a vertical angle in both states, because the mirrored
       stylesheet will not turn a horizontal one round and it would then point the wrong way
       in the Arabic layout. */
    .fc-strip {
        display: block;
        width: 100%;
        text-align: start;
        background: none;
        border: 0;
        color: inherit;
        padding: 6px 0 0;
        font-size: 90%;
    }
    .fc-strip-body { padding: 4px 0 2px; }

    /* A number that exists, and the shape of one that does not. */
    .fc-val {
        font-family: monospace;
        font-variant-numeric: tabular-nums;
        unicode-bidi: plaintext;
        white-space: nowrap;
    }

    .fc-val-absent {
        display: inline-block;
        min-width: 3.4em;
        padding: 0 0.35em;
        text-align: center;
        border: 1px dashed currentColor;
        border-radius: 3px;
        opacity: 0.55;
    }

    /* Outlined at full strength with only the inside faded: a chip faded as a whole loses one
       of the two themes, which is the lesson the dashboard widget already carries. */
    .fc-chip {
        position: relative;
        display: inline-block;
        padding-inline-start: 6px;
        padding-inline-end: 6px;
        line-height: 16px;
        border-radius: 2px;
        font-size: 11px;
        box-shadow: inset 0 0 0 1px currentColor;
    }

    .fc-chip::before {
        content: "";
        position: absolute;
        inset: 0;
        border-radius: 2px;
        background-color: currentColor;
        opacity: 0.12;
    }

    .fc-chip-text { position: relative; }

    /* The save bar follows the reader down a thirteen-field form. It has to be opaque or the
       rows scroll through it, and the only colour that is right in both themes is the one the
       content box is already painted in - which reaches it only if the pane in between passes
       it down, because every one of them is transparent by default. */
    #fanctl > .tab-pane { background-color: inherit; }


    .fc-notes { margin: 0; padding-inline-start: 1.4em; }
    .fc-notes > li { font-size: 85%; opacity: 0.85; padding-bottom: 4px; }

    .fc-bench-head > * { margin-inline-end: 8px; }
    .fc-bench-controls { padding-top: 6px; }
    .fc-bench-controls > * { margin-inline-end: 6px; margin-top: 4px; }
    .fc-duty { display: inline-block; width: 7em; }

    /* The curve. Every stroke takes its colour from the text around it, so the drawing follows a
       theme into the dark rather than carrying a value of its own that has to be kept in step. */
    .fc-curve {
        width: 100%;
        max-width: 34em;
        height: auto;
        display: block;
        margin-top: 6px;
    }
    .fc-curve .fc-line {
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
    }
    .fc-curve .fc-axis {
        stroke: currentColor;
        stroke-width: 1;
        opacity: 0.35;
    }
    .fc-curve .fc-floor {
        stroke: currentColor;
        stroke-width: 1;
        stroke-dasharray: 4 3;
        opacity: 0.6;
    }
    .fc-curve .fc-limit {
        stroke: currentColor;
        stroke-width: 1;
        stroke-dasharray: 2 4;
        opacity: 0.6;
    }
    .fc-curve .fc-tick {
        fill: currentColor;
        opacity: 0.7;
        font-size: 11px;
        font-family: monospace;
    }
</style>

<div class="content-box fc-head">
    {# The incident bar is the one state on this page a person has to act on, and until today it
       was inside the Fans pane - so a restore that failed was invisible from three of the four
       tabs. It is above the tab strip now, and it can be dismissed once it has been read rather
       than occupying a Settings viewport indefinitely while somebody types. #}
    <div id="incident-panel" style="display: none;">
        <div id="incident-body"></div>
    </div>
    <div class="fc-vitals">
        <div class="fc-vital" id="fc-vital-heat">
            <i class="fa fa-thermometer-half"></i>
            <span class="fc-vital-key">{{ lang._('Hottest') }}</span>
            <span class="fc-vital-value fc-tech" dir="ltr"></span>
            <span class="fc-vital-sub"></span>
        </div>
        <div class="fc-vital" id="fc-vital-air">
            <i class="fa fa-snowflake-o"></i>
            <span class="fc-vital-key">{{ lang._('Fans') }}</span>
            <span class="fc-vital-value fc-tech" dir="ltr"></span>
            <span class="fc-vital-sub"></span>
        </div>
        <div class="fc-vital" id="fc-vital-custody">
            <i class="fa fa-hand-paper-o"></i>
            <span class="fc-vital-key">{{ lang._('Fans held by') }}</span>
            <span class="fc-vital-value"></span>
            <span class="fc-vital-sub"></span>
        </div>
        <div class="fc-vital-acts">
            <span id="fc-age" class="fc-vital-sub"></span>
            <button id="stateRefreshAct" class="btn btn-xs btn-default" type="button">
                <i class="fa fa-fw fa-refresh"></i> {{ lang._('Refresh') }}
            </button>
            <button id="restoreAct" class="btn btn-xs btn-default" type="button">
                <i class="fa fa-fw fa-undo"></i> {{ lang._('Hand them back now') }}
            </button>
        </div>
    </div>
    <div id="fc-record" class="fc-note fc-record"></div>
    <button type="button" id="fc-contract-strip" class="fc-strip" aria-expanded="false"
            aria-controls="fc-contract-body">
        <i class="fa fa-fw fa-angle-down"></i> <span id="fc-contract-head"></span>
    </button>
    <div id="fc-contract-body" class="fc-strip-body" style="display: none;"></div>
</div>

<ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
    <li><a data-toggle="tab" href="#fans">{{ lang._('Readings') }}</a></li>
    <li><a data-toggle="tab" href="#settings">{{ lang._('Settings') }}</a></li>
    <li><a data-toggle="tab" href="#hardware">{{ lang._('Hardware') }}</a></li>
</ul>

<div class="tab-content content-box" id="fanctl">
    <div id="fans" class="tab-pane fade in">
        <div class="fc-panels">
            <div class="fc-section-head">
                <span>{{ lang._('Over') }}</span>
                <span id="graph-windows" class="fc-windows"></span>
            </div>
            <div id="fc-rows"></div>
            <div class="fc-section">
                <div id="fc-curve-line" class="fc-note"></div>
                <button type="button" id="fc-buys-strip" class="fc-strip" aria-expanded="false"
                        aria-controls="fc-buys-body">
                    <i class="fa fa-fw fa-angle-down"></i> {{ lang._('What more fan buys') }}
                </button>
                <div id="fc-buys-body" class="fc-strip-body" style="display: none;"></div>
            </div>
        </div>
    </div>

    <div id="settings" class="tab-pane fade in">
        {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general', 'apply_btn_id': 'saveAct']) }}
    </div>

    {# Laid out the way OPNsense lays out a form: sections with a heading, what is read on the
       left and the reading on the right, and the long explanations under a "full help" switch
       like the one the settings tab has. #}
    <div id="hardware" class="tab-pane fade in">
        <div class="table-responsive">
            <table class="table table-striped table-condensed fc-form">
                <colgroup>
                    <col style="width: 25%;"/>
                    <col style="width: 75%;"/>
                </colgroup>
                <thead>
                    <tr>
                        <th colspan="2">
                            <div class="fc-head-row">
                                <b>{{ lang._('The chip') }}</b>
                                <span class="pull-right">
                                    <i class="fa fa-info-circle fa-sm fa-fw text-primary"></i> <small>{{ lang._('full help') }}</small>
                                    <a href="#" class="fc-help-toggle"><i class="fa fa-toggle-off fa-fw text-danger"></i></a>
                                    <button id="probeRefreshAct" class="btn btn-xs btn-default" type="button" title="{{ lang._('Refresh') }}">
                                        <i class="fa fa-fw fa-refresh"></i>
                                    </button>
                                </span>
                            </div>
                        </th>
                    </tr>
                </thead>
                <tbody id="chip-body"></tbody>
                <thead>
                    <tr><th colspan="2"><div class="fc-head-row"><b>{{ lang._('This appliance') }}</b></div></th></tr>
                </thead>
                <tbody id="model-body"></tbody>
                <thead>
                    <tr><th colspan="2"><div class="fc-head-row"><b>{{ lang._('Floors, and how to measure one') }}</b></div></th></tr>
                </thead>
                <tbody id="workbench-body"></tbody>
            </table>
        </div>
    </div>
</div>
