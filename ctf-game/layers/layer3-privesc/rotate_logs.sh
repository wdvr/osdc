#!/bin/bash
# Log rotation script - runs as root via cron
# Note: This script is world-writable (oops!)

cd /var/log
for log in *.log; do
    if [ -f "$log" ]; then
        cp "$log" "/var/backups/${log}.$(date +%Y%m%d)"
    fi
done

# Clean old backups
find /var/backups -mtime +7 -delete

echo "Log rotation completed at $(date)" >> /var/log/rotation.log
