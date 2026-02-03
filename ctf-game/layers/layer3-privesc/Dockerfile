FROM ubuntu:22.04

# Install SSH and basic tools
RUN apt-get update && apt-get install -y \
    openssh-server \
    sudo \
    vim \
    nano \
    curl \
    netcat-openbsd \
    python3 \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Configure SSH
RUN mkdir /var/run/sshd
RUN echo 'PermitRootLogin no' >> /etc/ssh/sshd_config
RUN echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config

# Create users
RUN useradd -m -s /bin/bash admin
RUN useradd -m -s /bin/bash backup
RUN echo 'admin:backup-pass-2024' | chpasswd
RUN echo 'backup:backup-user-2024' | chpasswd

# Create the PRIZE.txt file (only readable by root)
RUN mkdir -p /opt
RUN echo "FLAG{pr1v1l3g3_3sc4l4t10n_m4st3r}" > /opt/PRIZE.txt
RUN echo "" >> /opt/PRIZE.txt
RUN echo "Congratulations! You've escalated privileges." >> /opt/PRIZE.txt
RUN echo "" >> /opt/PRIZE.txt
RUN echo "=== NEXT LAYER INSTRUCTIONS ===" >> /opt/PRIZE.txt
RUN echo "Connect to: layer4-pivot:7777" >> /opt/PRIZE.txt
RUN echo "Protocol: HTTP" >> /opt/PRIZE.txt
RUN echo "Auth Header: X-Pivot-Key: ESCALATION-COMPLETE-2024" >> /opt/PRIZE.txt
RUN chmod 600 /opt/PRIZE.txt

# === VULNERABILITY 1: Writable cron script ===
# A script run by root that backup user can modify
RUN mkdir -p /var/backups
COPY rotate_logs.sh /usr/local/bin/rotate_logs.sh
RUN chmod 777 /usr/local/bin/rotate_logs.sh
RUN echo "* * * * * root /usr/local/bin/rotate_logs.sh" > /etc/cron.d/logrotate
RUN chmod 644 /etc/cron.d/logrotate

# === VULNERABILITY 2: sudo misconfiguration ===
# backup user can run /usr/bin/find as root (GTFOBins pattern)
RUN echo "backup ALL=(root) NOPASSWD: /usr/bin/find" >> /etc/sudoers

# === VULNERABILITY 3: SUID binary ===
# A custom binary that reads files (like less but exploitable)
COPY reader.py /usr/local/bin/reader
RUN chmod 4755 /usr/local/bin/reader

# Create hint files
RUN echo "Check what the backup user can do with sudo -l" > /home/admin/hint.txt
RUN echo "Also look at /etc/cron.d/ for scheduled tasks" >> /home/admin/hint.txt
RUN chown admin:admin /home/admin/hint.txt

RUN echo "I have some special sudo permissions..." > /home/backup/notes.txt
RUN echo "Also there's a cron job that runs as root" >> /home/backup/notes.txt
RUN chown backup:backup /home/backup/notes.txt

# Create flag file for this layer
RUN mkdir -p /flags
RUN echo "FLAG{pr1v1l3g3_3sc4l4t10n_m4st3r}" > /flags/flag3.txt

# Startup script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22 8888

CMD ["/entrypoint.sh"]
