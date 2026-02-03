FROM python:3.11-slim

WORKDIR /app

# Install some basic tools that players can use
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-openbsd \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY shell_service.py /app/
COPY motd.txt /app/

# Create directory structure
RUN mkdir -p /home/ctfuser /var/log /opt/secrets

# Create fake files for exploration
RUN echo "Application logs - nothing interesting here" > /var/log/app.log
RUN echo "Connection logs..." > /var/log/connections.log

# The important file - readable only by root
RUN echo "FLAG{r3str1ct3d_sh3ll_3sc4p3}" > /opt/secrets/flag2.txt
RUN chmod 600 /opt/secrets/flag2.txt

# Create a hint file
RUN echo "Interesting... there's a setuid binary at /usr/local/bin/logviewer" > /home/ctfuser/notes.txt
RUN echo "Also check /opt/secrets/ - but you'll need elevated privileges" >> /home/ctfuser/notes.txt

# Create the "vulnerable" setuid-like simulation
COPY logviewer.py /usr/local/bin/logviewer
RUN chmod +x /usr/local/bin/logviewer

# Layer 3 connection info (hidden)
RUN mkdir -p /etc/corp
RUN echo "layer3-priv:8888:admin:backup-pass-2024" > /etc/corp/services.conf
RUN chmod 640 /etc/corp/services.conf

# Flag for this layer (in a findable but not obvious place)
RUN mkdir -p /flags
RUN echo "FLAG{r3str1ct3d_sh3ll_3sc4p3}" > /flags/flag2.txt

# Token for authentication
ENV ACCESS_TOKEN=layer2-access-token-7f3d9a2b

EXPOSE 9000

CMD ["python", "/app/shell_service.py"]
