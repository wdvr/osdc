FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server bash curl git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Configure sshd
RUN mkdir -p /var/run/sshd && \
    sed -i 's/#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    echo "PubkeyAuthentication yes" >> /etc/ssh/sshd_config

# Create dev user (UID 1081 matches production)
RUN useradd -m -u 1081 -s /bin/bash dev && \
    mkdir -p /home/dev/.ssh && \
    chown -R dev:dev /home/dev/.ssh && \
    chmod 700 /home/dev/.ssh

EXPOSE 22

# The processor injects a startup command; default to sleep infinity
CMD ["sleep", "infinity"]
