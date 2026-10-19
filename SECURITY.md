# Security

## Reporting a vulnerability

**Not in a public issue, please.** Report it privately through GitHub -
[**Report a vulnerability**](https://github.com/AbdelmonemAwad/os-fanctl/security/advisories/new),
on the Security tab - where only you and the owner see it, and the fix can be prepared before anything is
public. Without a GitHub account, write to **security@npuforge.org**. Either way, include:

- what is affected - the fan control service, its page and its configd actions, the readers of the sensors;
- the appliance, the version you installed, and the OPNsense version;
- how to reproduce it, and what you saw.

This is one person's project, worked on in the evenings: expect an answer within days, not hours.
A fix is made in a pull request and the reporter credited in it, unless they ask not to be.

## What is supported

The newest version on `main`, and the newest release where there are releases. Fixes go there,
with no backports.

## Worth knowing before you report

- **A fan held too slow for the heat is a safety problem**, not only a bug: the plugin writes to the hardware that keeps the appliance cool. Report one here, privately.
- Anything that lets the page or an action write to the hardware without the GUI's login is a vulnerability.
- **Never send a configuration backup.** It holds keys and passwords.
