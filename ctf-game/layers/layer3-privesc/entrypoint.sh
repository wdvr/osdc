#!/bin/bash

# Start cron daemon for the vulnerable cron job
service cron start

# Start SSH daemon
/usr/sbin/sshd -D
