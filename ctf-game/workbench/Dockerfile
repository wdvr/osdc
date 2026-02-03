FROM ubuntu:22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install useful hacking/CTF tools
RUN apt-get update && apt-get install -y \
    # Networking
    curl \
    wget \
    netcat-openbsd \
    nmap \
    dnsutils \
    iputils-ping \
    telnet \
    socat \
    # Text processing
    jq \
    vim \
    nano \
    less \
    # Programming
    python3 \
    python3-pip \
    # Utilities
    git \
    tmux \
    htop \
    tree \
    file \
    xxd \
    binutils \
    # SSH client
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python tools
RUN pip3 install \
    requests \
    pwntools \
    httpie

# Create player directory
RUN useradd -m -s /bin/bash player
WORKDIR /home/player

# Copy welcome message and hints
COPY motd.txt /etc/motd
COPY hints.txt /home/player/hints.txt
COPY tools.txt /home/player/tools.txt

# Set up bashrc to show MOTD
RUN echo 'cat /etc/motd' >> /home/player/.bashrc

# Create workspace
RUN mkdir -p /home/player/workspace /home/player/notes
RUN chown -R player:player /home/player

USER player

CMD ["/bin/bash"]
