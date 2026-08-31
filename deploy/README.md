# Running the poller on a timer

**Only if you want an archive.** The server fetches live by default and keeps
nothing, so none of this is part of installing it. Set `CABLEGRAM_ARCHIVE=1` and
the server reads a file instead, and then something has to fill that file.

It is worth doing for two sources in particular. cls.cn holds 3.34 days and
cannot page backwards, and 36Kr is shallow, so an hour nobody polls really is an
hour neither will serve again. For most of the other seventeen it is not: this
laptop was off for eleven hours, the sources published 325 articles, and the
next pass picked up all 325.

Two units are provided. Use `cablegram-poll-uvx.service` if you installed with
`uvx cablegram-mcp` and never cloned; use `cablegram-poll.service` if you did
clone, and adjust its `WorkingDirectory`.

Copy the one you want plus the timer into `~/.config/systemd/user/`, and:

    systemctl --user daemon-reload
    systemctl --user enable --now cablegram-poll.timer
    systemctl --user list-timers cablegram-poll.timer

`cablegram poll` exits non-zero when every source failed, so a failure is
visible to `systemctl --user status` rather than silent.

Any scheduler works — this is just the one with no daemon to install. Hourly is
comfortable, and if you are only doing this for cls.cn, twice a day is enough.
