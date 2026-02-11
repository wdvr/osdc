# CPU-only GPU Dev Server Image (local k3d development)
# Mirrors the production image (terraform-gpu-devservers/docker/Dockerfile)
# but without CUDA/PyTorch - same tools, same /devserver-setup layout.
#
# Build context must be terraform-gpu-devservers/docker/ so we can
# reuse the same shell configs, scripts, and ssh_config.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        openssh-server \
        sudo \
        curl \
        wget \
        vim \
        nano \
        git \
        coreutils \
        util-linux \
        procps \
        zsh \
        bash-completion \
        ca-certificates \
        gnupg \
        lsb-release \
        iproute2 \
        tmux \
        unzip \
        htop \
        tree \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 (required for Claude CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install common Python ML packages (CPU-only, no PyTorch)
RUN pip install --no-cache-dir \
        jupyterlab \
        ipywidgets \
        matplotlib \
        pandas \
        numpy \
        scikit-learn

# Create dev user (UID 1081 matches production)
RUN useradd -u 1081 -m -s /usr/bin/zsh dev && \
    usermod -aG sudo dev && \
    echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev && \
    echo 'Defaults lecture=never' >> /etc/sudoers.d/dev && \
    echo 'Defaults !lecture' >> /etc/sudoers.d/dev

# Configure SSH daemon
RUN mkdir -p /run/sshd /var/run/sshd && \
    ssh-keygen -A
COPY ssh_config /etc/ssh/sshd_config

# Install Claude CLI and oh-my-zsh as dev user
USER dev
WORKDIR /home/dev

RUN mkdir -p ~/.npm-global && \
    npm config set prefix ~/.npm-global && \
    npm install -g @anthropic-ai/claude-code || echo "Claude CLI install failed, will retry at runtime"

RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended

RUN git clone https://github.com/zsh-users/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions && \
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting

RUN mkdir -p ~/.jupyter

# Create /devserver-setup with all configs (same layout as production)
USER root
RUN mkdir -p /devserver-setup && \
    cp -r /home/dev/.npm-global /devserver-setup/npm-global && \
    cp -r /home/dev/.oh-my-zsh /devserver-setup/oh-my-zsh && \
    cp -r /home/dev/.jupyter /devserver-setup/jupyter && \
    cp /home/dev/.npmrc /devserver-setup/.npmrc

COPY --chown=root:root shell_env /devserver-setup/.shell_env
COPY --chown=root:root zshrc /devserver-setup/.zshrc
COPY --chown=root:root zshrc_ext /devserver-setup/.zshrc_ext
COPY --chown=root:root bashrc /devserver-setup/.bashrc
COPY --chown=root:root bashrc_ext /devserver-setup/.bashrc_ext
COPY --chown=root:root bash_profile /devserver-setup/.bash_profile
COPY --chown=root:root profile /devserver-setup/.profile
COPY --chown=root:root zprofile /devserver-setup/.zprofile

# MOTD
COPY motd_script /usr/local/bin/motd_script
COPY motd_script /etc/update-motd.d/00-custom
RUN chmod +x /usr/local/bin/motd_script && \
    chmod +x /etc/update-motd.d/00-custom && \
    rm -f /etc/motd /etc/legal /usr/share/base-files/motd 2>/dev/null || true && \
    mkdir -p /etc/motd.d /etc/update-motd.d /var/lib/sudo/lectured && \
    touch /etc/motd.d/00-header && \
    chmod 644 /etc/motd.d/00-header && \
    touch /var/lib/sudo/lectured/dev && \
    echo "dev" > /var/lib/sudo/lectured/dev

# Dotfiles persistence scripts
COPY setup-dotfiles-persistence /usr/local/bin/setup-dotfiles-persistence
COPY backup-dotfiles /usr/local/bin/backup-dotfiles
COPY restore-dotfiles /usr/local/bin/restore-dotfiles
COPY list-dotfile-versions /usr/local/bin/list-dotfile-versions
COPY restore-dotfiles-version /usr/local/bin/restore-dotfiles-version
COPY dotfiles-shutdown-handler /usr/local/bin/dotfiles-shutdown-handler
RUN chmod +x /usr/local/bin/setup-dotfiles-persistence \
             /usr/local/bin/backup-dotfiles \
             /usr/local/bin/restore-dotfiles \
             /usr/local/bin/list-dotfile-versions \
             /usr/local/bin/restore-dotfiles-version \
             /usr/local/bin/dotfiles-shutdown-handler

# nproc wrapper
COPY nproc_wrapper /usr/local/bin/nproc_wrapper
RUN mv /usr/bin/nproc /usr/bin/nproc.real && \
    cp /usr/local/bin/nproc_wrapper /usr/bin/nproc && \
    chmod +x /usr/bin/nproc /usr/local/bin/nproc_wrapper

# Disable PAM MOTD noise
RUN sed -i 's/session    optional     pam_motd.so/#&/g' /etc/pam.d/sshd 2>/dev/null || true && \
    sed -i 's/session    optional     pam_motd.so  motd=/#&/g' /etc/pam.d/sshd 2>/dev/null || true && \
    rm -rf /etc/update-motd.d/00-header /etc/update-motd.d/10-help-text 2>/dev/null || true

# Clean up
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN chown -R dev:dev /home/dev && \
    chmod 700 /home/dev/.ssh 2>/dev/null || true

EXPOSE 22 8888

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep sshd || exit 1

# Default command - overridden by K8s pod spec startup script
CMD ["/usr/sbin/sshd", "-D", "-e"]
