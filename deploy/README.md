# Running the poller on a timer

The archive is only worth what has been put in it, and feeds expose a window of
days: an hour nobody polls is an hour no endpoint will serve again. So the
poller belongs on a timer, independently of whether an MCP client is running.

Copy both files into `~/.config/systemd/user/`, adjust the path in the service,
and:

    systemctl --user daemon-reload
    systemctl --user enable --now cablegram-poll.timer
    systemctl --user list-timers cablegram-poll.timer

`cablegram poll` exits non-zero when every source failed, so a failure is
visible to `systemctl --user status` rather than silent.

Any scheduler works — this is just the one with no daemon to install. Hourly is
comfortable for the RSS sources.
